"""Monitor for Azure built-in roles."""

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import RoleDefinition
from azurerbac.backgroundjobs.models import RoleScanResult
from azurerbac.core import (
    EventType,
    Role,
    RoleHistory,
    RoleScanStatus,
    RoleStatus,
    ensure_utc,
    utcnow,
)
from azurerbac.core.diffing import RoleDiff, diff_roles, diff_summary

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Partitioning logic
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartitionedRoleIds:
    """Result of partitioning role IDs into create/update/delete sets."""

    new: set[str]
    update: set[str]
    deletion: set[str]


class RoleChangeProcessor:
    """Partition and process role changes (new, updated, deleted)."""

    def __init__(
        self,
        session: AsyncSession,
        roles: list[RoleDefinition],
    ) -> None:
        self._session = session
        self._fetched_by_id = {r.role_id: r for r in roles if r.role_id}
        self._existing_by_id: dict[str, Role] = {}
        self._timestamp = utcnow()

    @property
    def timestamp(self) -> dt.datetime:
        """Timestamp when processing started."""
        return self._timestamp

    @property
    def total_fetched(self) -> int:
        """Number of roles fetched from Azure."""
        return len(self._fetched_by_id)

    async def process(self) -> RoleScanResult:
        """Partition roles, process changes, record scan status, and return result."""
        self._existing_by_id = await self._load_existing_roles()
        partition = self._partition()
        new = self._process_new(partition.new)
        updated = self._process_updates(partition.update)
        deleted = self._process_deletions(partition.deletion)

        # Record scan status and link history entries
        all_entries = new + updated + deleted
        await self._record_scan_status(len(new), len(updated), len(deleted), all_entries)

        return RoleScanResult(
            created=len(new),
            updated=len(updated),
            deleted=len(deleted),
            total=self.total_fetched,
        )

    async def _load_existing_roles(self) -> dict[str, Role]:
        """Load all existing roles from database."""
        existing = (await self._session.execute(select(Role))).scalars().all()
        return {r.role_id: r for r in existing}

    @staticmethod
    def _is_update_candidate(stored_role: Role, fetched_role: RoleDefinition) -> bool:
        """Determine if a role should be processed for update."""
        # Deleted roles always need processing for reactivation
        if stored_role.status == RoleStatus.DELETED:
            return True

        stored_updated_on = ensure_utc(stored_role.updated_on)
        fetched_updated_on = fetched_role.properties.updated_on

        # If either timestamp is missing, treat as candidate to be safe
        if stored_updated_on is None or fetched_updated_on is None:
            return True

        if fetched_updated_on > stored_updated_on:
            return True

        if fetched_updated_on < stored_updated_on:
            logger.info(
                "Rejecting stale update for role %s (%s): "
                "stored updated_on=%s is newer than fetched updated_on=%s",
                fetched_role.role_name,
                stored_role.role_id,
                stored_updated_on.isoformat(),
                fetched_updated_on.isoformat(),
            )
            return False

        # Equal timestamps - unchanged
        logger.debug(
            "Skipping unchanged role %s (%s): updated_on=%s",
            fetched_role.role_name,
            stored_role.role_id,
            fetched_updated_on.isoformat(),
        )
        return False

    def _partition(self) -> PartitionedRoleIds:
        """Return three disjoint sets: new, update candidates, and deletions."""
        fetched_ids = set(self._fetched_by_id.keys())
        new_ids = fetched_ids - self._existing_by_id.keys()
        update_ids: set[str] = set()
        deletion_ids: set[str] = set()

        for role_id, stored_role in self._existing_by_id.items():
            if role_id in fetched_ids:
                if self._is_update_candidate(stored_role, self._fetched_by_id[role_id]):
                    update_ids.add(role_id)
            elif stored_role.status != RoleStatus.DELETED:
                deletion_ids.add(role_id)

        logger.debug(
            "Partitioned roles: new=%d, update_candidates=%d, deletions=%d",
            len(new_ids),
            len(update_ids),
            len(deletion_ids),
        )
        return PartitionedRoleIds(new_ids, update_ids, deletion_ids)

    def _process_new(self, new_ids: set[str]) -> list[RoleHistory]:
        return [self._handle_new_role(self._fetched_by_id[rid]) for rid in new_ids]

    def _process_updates(self, update_ids: set[str]) -> list[RoleHistory]:
        return [
            entry
            for rid in update_ids
            if (entry := self._handle_update(self._existing_by_id[rid], self._fetched_by_id[rid]))
        ]

    def _process_deletions(self, deletion_ids: set[str]) -> list[RoleHistory]:
        entries = [self._handle_deletion(self._existing_by_id[rid]) for rid in deletion_ids]
        for entry in entries:
            self._session.add(entry)
        return entries

    def _create_history_entry(
        self,
        role_id: str,
        role_name: str,
        version_number: int,
        event_type: EventType,
        diff: RoleDiff,
        role_json: dict | None,
        azure_updated_on: dt.datetime | None,
    ) -> RoleHistory:
        """Create a new RoleHistory entry. Note: scan_id is set after commit."""
        return RoleHistory(
            role_id=role_id,
            version_number=version_number,
            role_json=role_json,
            azure_updated_on=azure_updated_on,
            role_name=role_name,
            event_type=event_type,
            diff_json=diff.to_dict(),
            summary=diff_summary(diff),
        )

    def _handle_new_role(self, role: RoleDefinition) -> RoleHistory:
        """Handle creation of a new role. Returns the created history entry."""
        role_id = role.role_id
        role_name = role.role_name
        self._session.add(Role(role_id=role_id, role_name=role_name))

        azure_created_on = role.properties.created_on
        azure_updated_on = role.properties.updated_on

        # Determine if this is a truly new role or a pre-existing one
        # Truly new: azure_created_on == azure_updated_on (just created in Azure)
        # Pre-existing: azure_created_on != azure_updated_on (existed before our scan)
        is_truly_new = (
            azure_created_on is not None
            and azure_updated_on is not None
            and azure_created_on == azure_updated_on
        )
        event_type = EventType.CREATED if is_truly_new else EventType.INITIAL_SCAN

        logger.debug(
            "New role: %s (%s) - event_type=%s, azure_created=%s, azure_updated=%s",
            role_name,
            role_id,
            event_type.value,
            azure_created_on,
            azure_updated_on,
        )

        diff = diff_roles(None, role)
        history_entry = self._create_history_entry(
            role_id=role_id,
            role_name=role_name,
            version_number=1,
            event_type=event_type,
            diff=diff,
            role_json=role.to_dict(),
            azure_updated_on=azure_updated_on,
        )
        self._session.add(history_entry)
        return history_entry

    def _handle_update(
        self,
        stored_role: Role,
        fetched_role: RoleDefinition,
    ) -> RoleHistory | None:
        """Handle update of an existing role. Returns history entry if changes detected."""
        # Get old_json directly from current_version relationship (no query needed)
        old_json = stored_role.current_version.role_json if stored_role.current_version else None
        old_role = RoleDefinition.model_validate(old_json) if old_json else None
        diff = diff_roles(old_role, fetched_role)

        stored_role.status = RoleStatus.ACTIVE
        stored_role.role_name = fetched_role.role_name  # Keep denormalized column in sync

        if diff.changed:
            role_dict = fetched_role.to_dict()
            diff.before_json = old_json
            diff.after_json = role_dict

            # Increment version number
            current_version = stored_role.history[0].version_number if stored_role.history else 0
            next_version_num = current_version + 1
            logger.info(
                "Role updated: %s (%s) -> version %d, summary: %s",
                fetched_role.role_name,
                stored_role.role_id,
                next_version_num,
                diff_summary(diff),
            )
            history_entry = self._create_history_entry(
                role_id=stored_role.role_id,
                role_name=fetched_role.role_name,
                version_number=next_version_num,
                event_type=EventType.UPDATED,
                diff=diff,
                role_json=role_dict,
                azure_updated_on=fetched_role.properties.updated_on,
            )
            self._session.add(history_entry)
            return history_entry
        return None

    def _handle_deletion(self, stored_role: Role) -> RoleHistory:
        """Handle deletion of a role. Returns the created history entry."""
        logger.info("Role deleted: %s (%s)", stored_role.role_name, stored_role.role_id)

        # Get old_json directly from current_version relationship
        old_json = stored_role.current_version.role_json if stored_role.current_version else None
        old_role = RoleDefinition.model_validate(old_json) if old_json else None
        diff = diff_roles(old_role, None)

        # Delete events have no JSON - role_json is NULL
        next_version_num = (stored_role.history[0].version_number if stored_role.history else 0) + 1
        history_entry = self._create_history_entry(
            role_id=stored_role.role_id,
            role_name=stored_role.role_name,
            version_number=next_version_num,
            event_type=EventType.DELETED,
            diff=diff,
            role_json=None,  # No JSON for deleted role
            azure_updated_on=self._timestamp,  # Use scan timestamp as event date
        )
        stored_role.status = RoleStatus.DELETED
        return history_entry

    async def _record_scan_status(
        self,
        created: int,
        updated: int,
        deleted: int,
        entries: list[RoleHistory],
    ) -> RoleScanStatus:
        """Record scan status and link all history entries to it."""
        scan_status = RoleScanStatus(
            scan_timestamp=self._timestamp,
            roles_scanned=self.total_fetched,
            additions=created,
            updates=updated,
            deletions=deleted,
        )
        self._session.add(scan_status)
        await self._session.flush()

        for entry in entries:
            entry.scan_id = scan_status.id

        return scan_status


# -----------------------------------------------------------------------------
# Scan orchestration
# -----------------------------------------------------------------------------


async def apply_role_scan(session: AsyncSession, roles: list[RoleDefinition]) -> RoleScanResult:
    """Compare fetched roles vs DB snapshots and store changes."""
    processor = RoleChangeProcessor(session, roles)

    logger.info(
        "Processing %d roles fetched from Azure at %s",
        processor.total_fetched,
        processor.timestamp,
    )

    result = await processor.process()
    await session.commit()

    logger.info(
        "Role scan committed: created=%d, updated=%d, deleted=%d, total=%d",
        result.created,
        result.updated,
        result.deleted,
        result.total,
    )

    return result
