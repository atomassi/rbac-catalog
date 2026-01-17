"""Monitor for Azure built-in roles."""

from __future__ import annotations

import datetime as dt
import logging
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import RoleDefinition
from azurerbac.backgroundjobs.models import RoleScanResult
from azurerbac.cache import get_cache_service
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


class _PartitionedRoleIds(NamedTuple):
    """Result of partitioning role IDs into create/update/delete sets."""

    new: set[str]
    update: set[str]
    deletion: set[str]


def _is_update_candidate(
    stored_role: Role,
    fetched_role: RoleDefinition,
) -> bool:
    """Determine if a role should be processed for update.

    Returns True if the role needs processing, False to skip.
    Logs info for stale updates, debug for unchanged roles.
    """
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


def _partition_roles(
    fetched_by_id: dict[str, RoleDefinition],
    existing_by_id: dict[str, Role],
) -> _PartitionedRoleIds:
    """Partition roles into new, update candidates, and deletions.

    Args:
        fetched_by_id: Roles fetched from Azure, keyed by role_id.
        existing_by_id: Roles in the database, keyed by role_id.

    Returns:
        _PartitionedRoleIds with the three disjoint sets.
    """
    fetched_ids = set(fetched_by_id.keys())
    new_ids = fetched_ids - existing_by_id.keys()
    update_ids: set[str] = set()
    deletion_ids: set[str] = set()

    for role_id, stored_role in existing_by_id.items():
        # Role still exists in Azure - check if update needed
        if role_id in fetched_ids:
            if _is_update_candidate(stored_role, fetched_by_id[role_id]):
                update_ids.add(role_id)
        # Role no longer in Azure - mark for deletion (unless already deleted)
        elif stored_role.status != RoleStatus.DELETED:
            deletion_ids.add(role_id)

    logger.debug(
        "Partitioned roles: new=%d, update_candidates=%d, deletions=%d",
        len(new_ids),
        len(update_ids),
        len(deletion_ids),
    )
    return _PartitionedRoleIds(new_ids, update_ids, deletion_ids)


# -----------------------------------------------------------------------------
# History entry creation
# -----------------------------------------------------------------------------


def _create_history_entry(
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


async def _handle_new_role(
    session: AsyncSession,
    role: RoleDefinition,
) -> RoleHistory:
    """Handle creation of a new role. Returns the created history entry."""
    role_id = role.role_id
    role_name = role.role_name
    session.add(Role(role_id=role_id, role_name=role_name))

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
    history_entry = _create_history_entry(
        role_id=role_id,
        role_name=role_name,
        version_number=1,
        event_type=event_type,
        diff=diff,
        role_json=role.to_dict(),
        azure_updated_on=azure_updated_on,
    )
    session.add(history_entry)
    return history_entry


async def _handle_role_update(
    session: AsyncSession,
    stored_role: Role,
    fetched_role: RoleDefinition,
) -> RoleHistory | None:
    """Handle update of an existing role. Returns history entry if changes detected.

    Note: Timestamp filtering is done during the partitioning phase in apply_role_scan.
    This function assumes the role is already a valid update candidate.
    """
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
        next_version_num = (stored_role.history[0].version_number if stored_role.history else 0) + 1
        logger.info(
            "Role updated: %s (%s) -> version %d, summary: %s",
            fetched_role.role_name,
            stored_role.role_id,
            next_version_num,
            diff_summary(diff),
        )
        history_entry = _create_history_entry(
            role_id=stored_role.role_id,
            role_name=fetched_role.role_name,
            version_number=next_version_num,
            event_type=EventType.UPDATED,
            diff=diff,
            role_json=role_dict,
            azure_updated_on=fetched_role.properties.updated_on,
        )
        session.add(history_entry)
        return history_entry
    return None


def _handle_role_deletion(
    stored_role: Role,
    now: dt.datetime,
) -> RoleHistory:
    """Handle deletion of a role. Returns the created history entry."""
    logger.info("Role deleted: %s (%s)", stored_role.role_name, stored_role.role_id)

    # Get old_json directly from current_version relationship
    old_json = stored_role.current_version.role_json if stored_role.current_version else None
    old_role = RoleDefinition.model_validate(old_json) if old_json else None
    diff = diff_roles(old_role, None)

    # Delete events have no JSON - role_json is NULL
    next_version_num = (stored_role.history[0].version_number if stored_role.history else 0) + 1
    history_entry = _create_history_entry(
        role_id=stored_role.role_id,
        role_name=stored_role.role_name,
        version_number=next_version_num,
        event_type=EventType.DELETED,
        diff=diff,
        role_json=None,  # No JSON for deleted role
        azure_updated_on=now,  # Use scan timestamp as event date
    )
    stored_role.status = RoleStatus.DELETED
    return history_entry


# -----------------------------------------------------------------------------
# Scan orchestration
# -----------------------------------------------------------------------------


async def _load_existing_roles(session: AsyncSession) -> dict[str, Role]:
    """Load all existing roles from database."""
    existing = (await session.execute(select(Role))).scalars().all()
    return {s.role_id: s for s in existing}


async def _process_new_roles(
    session: AsyncSession,
    new_ids: set[str],
    fetched_by_id: dict[str, RoleDefinition],
) -> list[RoleHistory]:
    """Process all new roles and return their history entries."""
    entries = []
    for role_id in new_ids:
        entry = await _handle_new_role(session, fetched_by_id[role_id])
        entries.append(entry)
    return entries


async def _process_updates(
    session: AsyncSession,
    update_ids: set[str],
    fetched_by_id: dict[str, RoleDefinition],
    existing_by_id: dict[str, Role],
) -> list[RoleHistory]:
    """Process all update candidates and return history entries for actual changes."""
    entries = []
    for role_id in update_ids:
        if entry := await _handle_role_update(
            session, existing_by_id[role_id], fetched_by_id[role_id]
        ):
            entries.append(entry)
    return entries


def _process_deletions(
    session: AsyncSession,
    deletion_ids: set[str],
    existing_by_id: dict[str, Role],
    now: dt.datetime,
) -> list[RoleHistory]:
    """Process all deletions and return their history entries."""
    entries = []
    for role_id in deletion_ids:
        entry = _handle_role_deletion(existing_by_id[role_id], now)
        session.add(entry)
        entries.append(entry)
    return entries


async def _record_scan_status(
    session: AsyncSession,
    history_entries: list[RoleHistory],
    now: dt.datetime,
    total_fetched: int,
    created: int,
    updated: int,
    deleted: int,
) -> RoleScanStatus:
    """Record scan status and link all history entries to it."""
    scan_status = RoleScanStatus(
        scan_timestamp=now,
        roles_scanned=total_fetched,
        additions=created,
        updates=updated,
        deletions=deleted,
    )
    session.add(scan_status)
    await session.flush()

    for entry in history_entries:
        entry.scan_id = scan_status.id

    return scan_status


async def apply_role_scan(session: AsyncSession, roles: list[RoleDefinition]) -> RoleScanResult:
    """Compare fetched roles vs DB snapshots and store changes."""
    now = utcnow()
    logger.info("Starting role scan at %s with %d roles from Azure", now, len(roles))

    # Build lookups
    fetched_by_id = {role.role_id: role for role in roles if role.role_id}
    existing_by_id = await _load_existing_roles(session)
    logger.debug(
        "Loaded %d existing roles from DB, comparing with %d fetched roles",
        len(existing_by_id),
        len(fetched_by_id),
    )

    # Partition into create/update/delete sets
    partition = _partition_roles(fetched_by_id, existing_by_id)

    # Process each partition
    new_entries = await _process_new_roles(session, partition.new, fetched_by_id)
    update_entries = await _process_updates(
        session, partition.update, fetched_by_id, existing_by_id
    )
    delete_entries = _process_deletions(session, partition.deletion, existing_by_id, now)

    created = len(new_entries)
    updated = len(update_entries)
    deleted_count = len(delete_entries)
    history_entries = new_entries + update_entries + delete_entries

    # Record scan and commit
    scan_status = await _record_scan_status(
        session, history_entries, now, len(fetched_by_id), created, updated, deleted_count
    )
    await session.commit()

    logger.info(
        "Role scan committed: scan_id=%d, created=%d, updated=%d, deleted=%d, total=%d",
        scan_status.id,
        created,
        updated,
        deleted_count,
        len(fetched_by_id),
    )

    # Invalidate cache if changes detected
    if history_entries:
        logger.info(
            "Roles changed (created=%d, updated=%d, deleted=%d), triggering cache rebuild",
            created,
            updated,
            deleted_count,
        )
        await get_cache_service().invalidate_and_rebuild(session)

    return RoleScanResult(
        created=created,
        updated=updated,
        deleted=deleted_count,
        total=len(fetched_by_id),
    )
