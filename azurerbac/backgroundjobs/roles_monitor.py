"""Monitor for Azure built-in roles - detects and stores role changes."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.backgroundjobs.utils import parse_azure_date
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

# Type alias for parsed role data
type RoleData = tuple[str, str, str | None, dt.datetime | None, dt.datetime | None]


def _parse_role_data(role_json: dict) -> RoleData:
    """Extract common role data from JSON."""
    props = role_json.get("properties") or {}
    role_id = (role_json.get("name") or role_json.get("id") or "").split("/")[-1]
    role_name = props.get("roleName") or role_json.get("name") or role_id
    role_type = props.get("type")
    azure_updated_on = parse_azure_date(role_json, "updatedOn")
    azure_created_on = parse_azure_date(role_json, "createdOn")
    return role_id, str(role_name), role_type, azure_updated_on, azure_created_on


def _create_role(role_id: str, role_name: str) -> Role:
    """Create a new Role with denormalized role_name for queries."""
    return Role(
        role_id=role_id,
        role_name=role_name,
    )


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
    role_id: str,
    role_name: str,
    role_json: dict,
    azure_updated_on: dt.datetime | None,
    azure_created_on: dt.datetime | None,
    now: dt.datetime,
) -> RoleHistory:
    """Handle creation of a new role.

    Distinguishes between truly new roles (CREATED) and pre-existing roles
    discovered on initial scan (INITIAL_SCAN) by comparing Azure timestamps.

    Returns the created history entry so scan_id can be set later.
    """
    snap = _create_role(role_id, role_name)
    session.add(snap)

    # Determine if this is a truly new role or a pre-existing one
    # Truly new: azure_created_on == azure_updated_on (just created in Azure)
    # Pre-existing: azure_created_on != azure_updated_on (existed before our scan)
    is_truly_new = (
        azure_created_on is not None
        and azure_updated_on is not None
        and azure_created_on == azure_updated_on
    )
    event_type = EventType.CREATED if is_truly_new else EventType.INITIAL_SCAN

    diff = diff_roles(None, role_json)
    history_entry = _create_history_entry(
        role_id=role_id,
        role_name=role_name,
        version_number=1,
        event_type=event_type,
        diff=diff,
        role_json=role_json,
        azure_updated_on=azure_updated_on,
    )
    session.add(history_entry)
    return history_entry


async def _handle_role_update(
    session: AsyncSession,
    snap: Role,
    role_json: dict,
    role_name: str,
    azure_updated_on: dt.datetime | None,
    now: dt.datetime,
) -> RoleHistory | None:
    """Handle update of an existing role. Returns history entry if changes detected."""
    snap_updated_on = snap.updated_on
    if snap_updated_on and snap_updated_on.tzinfo is None:
        snap_updated_on = snap_updated_on.replace(tzinfo=dt.UTC)

    if snap_updated_on and azure_updated_on and azure_updated_on <= snap_updated_on:
        return None

    # Get old_json directly from current_version relationship (no query needed)
    old_json = snap.current_version.role_json if snap.current_version else None
    diff = diff_roles(old_json, role_json)

    snap.status = RoleStatus.ACTIVE
    snap.role_name = role_name  # Keep denormalized column in sync

    if diff.get("changed"):
        diff["before_json"] = old_json
        diff["after_json"] = role_json

        # Increment version number
        next_version_num = (snap.history[0].version_number if snap.history else 0) + 1
        history_entry = _create_history_entry(
            role_id=snap.role_id,
            role_name=role_name,
            version_number=next_version_num,
            event_type=EventType.UPDATED,
            diff=diff,
            role_json=role_json,
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

    fetched_by_id: dict[str, dict] = {}
    for r in roles:
        role_id = (r.get("name") or r.get("id") or "").split("/")[-1]
        if role_id:
            fetched_by_id[role_id] = r

    # Load all roles
    existing = (await session.execute(select(Role))).scalars().all()
    existing_by_id = {s.role_id: s for s in existing}

    created = updated = deleted_count = 0
    history_entries: list[RoleHistory] = []

    # Handle creates/updates
    for role_id, role_json in fetched_by_id.items():
        _, role_name, _role_type, azure_updated_on, azure_created_on = _parse_role_data(role_json)

        snap = existing_by_id.get(role_id)
        if snap is None:
            entry = await _handle_new_role(
                session,
                role_id,
                role_name,
                role_json,
                azure_updated_on,
                azure_created_on,
                now,
            )
            history_entries.append(entry)
            created += 1
        else:
            entry = await _handle_role_update(
                session, snap, role_json, role_name, azure_updated_on, now
            )
            if entry:
                history_entries.append(entry)
                updated += 1

    # Handle deletes (roles missing in fetched list)
    for role_id, snap in existing_by_id.items():
        if role_id in fetched_by_id or snap.status == RoleStatus.DELETED:
            continue

        # Get old_json directly from current_version relationship
        old_json = snap.current_version.role_json if snap.current_version else None
        diff = diff_roles(old_json, None)

        # Delete events have no JSON - role_json is NULL
        next_version_num = (snap.history[0].version_number if snap.history else 0) + 1
        history_entry = _create_history_entry(
            role_id=role_id,
            role_name=snap.role_name,
            version_number=next_version_num,
            event_type=EventType.DELETED,
            diff=diff,
            role_json=None,  # No JSON for deleted role
            azure_updated_on=None,
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
