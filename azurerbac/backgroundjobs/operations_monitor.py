"""Monitor for Azure provider operations - stores operations to database."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Final, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.backgroundjobs.utils import rebuild_cache_if_needed
from azurerbac.core import Operation, OperationScanStatus, utcnow


class FieldMapping(NamedTuple):
    """Maps an Operation attribute to a dict key with optional transform.

    Since attr and dict_key are always identical, we use a single 'name' field.
    """

    name: str  # Used for both Operation attribute and dict key lookup
    transform: Callable[[Any], Any] | None = None


# Fields to compare/update when syncing operations from Azure
# Only includes indexed fields - other data is in operation_json
_UPDATABLE_FIELDS: Final[tuple[FieldMapping, ...]] = (
    FieldMapping("provider_display_name"),
    FieldMapping("resource_type"),
    FieldMapping("is_data_action", lambda v: bool(v or False)),
)


def _deduplicate_operations(operations: list[dict]) -> tuple[dict[str, dict], int]:
    """Deduplicate operations by name, keeping last occurrence."""
    ops_by_name: dict[str, dict] = {}
    duplicates = 0
    for op_data in operations:
        name = op_data.get("name", "")
        if not name:
            continue
        if name in ops_by_name:
            duplicates += 1
        ops_by_name[name] = op_data
    return ops_by_name, duplicates


def _try_update(existing_op: Operation, op_data: dict, now: dt.datetime) -> bool:
    """Update operation fields if they changed. Returns True if changed."""
    changed = False
    for field in _UPDATABLE_FIELDS:
        new_value = op_data.get(field.name)
        if field.transform:
            new_value = field.transform(new_value)
        if getattr(existing_op, field.name) != new_value:
            setattr(existing_op, field.name, new_value)
            changed = True
    existing_op.last_seen_at = now
    return changed


def _create_operation(
    name: str, op_data: dict, now: dt.datetime, operation_json: dict | None = None
) -> Operation:
    """Create a new Operation from data dict."""
    return Operation(
        name=name,
        provider_display_name=op_data.get("provider_display_name"),
        resource_type=op_data.get("resource_type"),
        is_data_action=bool(op_data.get("is_data_action", False)),
        operation_json=operation_json,
        first_seen_at=now,
        last_seen_at=now,
    )


async def apply_operations_scan(session: AsyncSession, operations: list[dict]) -> dict:
    """Store/update operations in the database.

    Uses upsert logic - updates existing operations, inserts new ones.
    Handles duplicates by keeping the last occurrence.

    Returns stats dict.
    """
    now = utcnow()
    ops_by_name, duplicates = _deduplicate_operations(operations)

    # Load existing operations by name
    existing = (await session.execute(select(Operation))).scalars().all()
    existing_by_name = {op.name: op for op in existing}

    created = updated = 0
    for name, op_data in ops_by_name.items():
        existing_op = existing_by_name.get(name)
        if existing_op is None:
            # Get the operation JSON from the data
            raw_json = op_data.get("operation_json")
            session.add(_create_operation(name, op_data, now, raw_json))
            created += 1
        elif _try_update(existing_op, op_data, now):
            updated += 1

    # Count unique providers
    providers = {
        op.get("provider_display_name")
        for op in ops_by_name.values()
        if op.get("provider_display_name")
    }

    # Record scan status
    scan_status = OperationScanStatus(
        scan_timestamp=now,
        operations_scanned=len(ops_by_name),
        providers_scanned=len(providers),
        additions=created,
        updates=updated,
    )
    session.add(scan_status)

    await session.commit()

    # Invalidate and rebuild cache if new operations were added
    if created > 0:
        await rebuild_cache_if_needed(
            session,
            reason="new operations added",
            logger_name="azurerbac.operations_monitor",
            created=created,
        )

    return {
        "created": created,
        "updated": updated,
        "total": len(ops_by_name),
        "duplicates_skipped": duplicates,
        "providers": len(providers),
    }
