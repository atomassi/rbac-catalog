"""Monitor for Azure built-in roles - detects and stores role changes."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import RoleDefinition
from azurerbac.cache import invalidate_and_rebuild_cache
from azurerbac.core import (
    EventType,
    Role,
    RoleHistory,
    RoleScanStatus,
    RoleStatus,
    utcnow,
)
from azurerbac.core.diffing import diff_roles, diff_summary

logger = logging.getLogger(__name__)


def _create_history_entry(
    role_id: str,
    role_name: str,
    version_number: int,
    event_type: EventType,
    diff: dict,
    role_json: dict | None,
    azure_updated_on: dt.datetime | None,
) -> RoleHistory:
    """Create a new RoleHistory entry (combined version + event).

    Note: scan_id is set after RoleScanStatus is created in apply_role_scan.
    """
    return RoleHistory(
        role_id=role_id,
        version_number=version_number,
        role_json=role_json,
        azure_updated_on=azure_updated_on,
        role_name=role_name,
        event_type=event_type,
        diff_json=diff,
        summary=diff_summary(diff),
    )


async def _handle_new_role(
    session: AsyncSession,
    role: RoleDefinition,
) -> RoleHistory:
    """Handle creation of a new role.

    Distinguishes between truly new roles (CREATED) and pre-existing roles
    discovered on initial scan (INITIAL_SCAN) by comparing Azure timestamps.

    Returns the created history entry so scan_id can be set later.
    """
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
    snap: Role,
    role: RoleDefinition,
) -> RoleHistory | None:
    """Handle update of an existing role. Returns history entry if changes detected."""
    role_name = role.role_name
    azure_updated_on = role.properties.updated_on

    snap_updated_on = snap.updated_on
    if snap_updated_on and snap_updated_on.tzinfo is None:
        snap_updated_on = snap_updated_on.replace(tzinfo=dt.UTC)

    # Skip timestamp check if role was deleted - always process to reactivate
    # For active roles, skip if Azure timestamp hasn't changed
    is_deleted = snap.status == RoleStatus.DELETED
    if not is_deleted and snap_updated_on and azure_updated_on and azure_updated_on <= snap_updated_on:
        return None

    # Get old_json directly from current_version relationship (no query needed)
    old_json = snap.current_version.role_json if snap.current_version else None
    old_role = RoleDefinition.model_validate(old_json) if old_json else None
    diff = diff_roles(old_role, role)

    snap.status = RoleStatus.ACTIVE
    snap.role_name = role_name  # Keep denormalized column in sync

    if diff.get("changed"):
        role_dict = role.to_dict()
        diff["before_json"] = old_json
        diff["after_json"] = role_dict

        # Increment version number
        next_version_num = (snap.history[0].version_number if snap.history else 0) + 1
        logger.info(
            "Role updated: %s (%s) -> version %d, summary: %s",
            role_name,
            snap.role_id,
            next_version_num,
            diff_summary(diff),
        )
        history_entry = _create_history_entry(
            role_id=snap.role_id,
            role_name=role_name,
            version_number=next_version_num,
            event_type=EventType.UPDATED,
            diff=diff,
            role_json=role_dict,
            azure_updated_on=azure_updated_on,
        )
        session.add(history_entry)
        return history_entry
    return None


async def apply_role_scan(session: AsyncSession, roles: list[dict]) -> dict:
    """Compare fetched roles vs DB snapshots, store changes.

    Returns small stats dict.
    """
    now = utcnow()
    logger.info("Starting role scan at %s with %d roles from Azure", now, len(roles))

    fetched_by_id: dict[str, RoleDefinition] = {}
    parse_errors = 0
    for r in roles:
        try:
            role = RoleDefinition.model_validate(r)
            if role.role_id:
                fetched_by_id[role.role_id] = role
        except Exception as e:
            parse_errors += 1
            logger.warning("Failed to parse role JSON: %s - %s", e, r.get("name", "unknown"))

    if parse_errors > 0:
        logger.warning("Role parsing: %d errors out of %d roles", parse_errors, len(roles))

    # Load all roles
    existing = (await session.execute(select(Role))).scalars().all()
    existing_by_id = {s.role_id: s for s in existing}
    logger.debug(
        "Loaded %d existing roles from DB, comparing with %d fetched roles",
        len(existing_by_id),
        len(fetched_by_id),
    )

    created = updated = deleted_count = 0
    history_entries: list[RoleHistory] = []

    # Handle creates/updates
    for role_id, role in fetched_by_id.items():
        snap = existing_by_id.get(role_id)
        if snap is None:
            entry = await _handle_new_role(session, role)
            history_entries.append(entry)
            created += 1
        else:
            entry = await _handle_role_update(session, snap, role)
            if entry:
                history_entries.append(entry)
                updated += 1

    # Handle deletes (roles missing in fetched list)
    for role_id, snap in existing_by_id.items():
        if role_id in fetched_by_id or snap.status == RoleStatus.DELETED:
            continue

        logger.info("Role deleted: %s (%s)", snap.role_name, role_id)

        # Get old_json directly from current_version relationship
        old_json = snap.current_version.role_json if snap.current_version else None
        old_role = RoleDefinition.model_validate(old_json) if old_json else None
        diff = diff_roles(old_role, None)

        # Delete events have no JSON - role_json is NULL
        next_version_num = (snap.history[0].version_number if snap.history else 0) + 1
        history_entry = _create_history_entry(
            role_id=role_id,
            role_name=snap.role_name,
            version_number=next_version_num,
            event_type=EventType.DELETED,
            diff=diff,
            role_json=None,  # No JSON for deleted role
            azure_updated_on=now,  # Use scan timestamp as event date
        )
        session.add(history_entry)
        history_entries.append(history_entry)
        snap.status = RoleStatus.DELETED
        deleted_count += 1

    # Record scan status first to get the ID for history entries
    scan_status = RoleScanStatus(
        scan_timestamp=now,
        roles_scanned=len(fetched_by_id),
        additions=created,
        updates=updated,
        deletions=deleted_count,
    )
    session.add(scan_status)
    await session.flush()  # Get the scan_status.id

    # Update all history entries created in this scan with scan_id
    for entry in history_entries:
        entry.scan_id = scan_status.id

    await session.commit()
    logger.info(
        "Role scan committed: scan_id=%d, created=%d, updated=%d, deleted=%d, total=%d",
        scan_status.id,
        created,
        updated,
        deleted_count,
        len(fetched_by_id),
    )

    changes = created + updated + deleted_count
    if changes > 0:
        logger.info(
            "Roles changed (created=%d, updated=%d, deleted=%d), triggering cache rebuild",
            created,
            updated,
            deleted_count,
        )
        await invalidate_and_rebuild_cache(session)

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted_count,
        "total": len(fetched_by_id),
    }
