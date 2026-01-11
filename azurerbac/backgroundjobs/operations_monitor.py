"""Monitor for Azure provider operations - stores operations to database."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import OperationData
from azurerbac.backgroundjobs.models import OperationsScanResult
from azurerbac.cache import get_cache_service
from azurerbac.core import Operation, OperationScanStatus, utcnow

logger = logging.getLogger(__name__)

# Fields to sync between OperationData and Operation
_OPERATION_FIELDS = (
    "display_name",
    "description",
    "origin",
    "provider_display_name",
    "resource_type",
    "resource_type_display_name",
    "is_data_action",
)


def _deduplicate_operations(
    operations: list[OperationData],
) -> tuple[dict[str, OperationData], int]:
    """Deduplicate operations by name, keeping last occurrence."""
    ops_by_name: dict[str, OperationData] = {}
    duplicates = 0
    for op in operations:
        if not op.name:
            continue
        if op.name in ops_by_name:
            duplicates += 1
        ops_by_name[op.name] = op
    return ops_by_name, duplicates


def _sync_operation_fields(existing_op: Operation, op_data: OperationData) -> bool:
    """Sync operation fields from source data. Returns True if any changed."""
    changed = False
    for field in _OPERATION_FIELDS:
        old_val = getattr(existing_op, field)
        new_val = getattr(op_data, field)
        if old_val != new_val:
            setattr(existing_op, field, new_val)
            changed = True
    return changed


def _create_operation(op_data: OperationData, now: dt.datetime) -> Operation:
    """Create a new DB Operation from OperationData model."""
    return Operation(
        name=op_data.name,
        display_name=op_data.display_name,
        description=op_data.description,
        origin=op_data.origin,
        provider_display_name=op_data.provider_display_name,
        resource_type=op_data.resource_type,
        resource_type_display_name=op_data.resource_type_display_name,
        is_data_action=op_data.is_data_action,
        first_seen_at=now,
        last_seen_at=now,
    )


async def apply_operations_scan(
    session: AsyncSession, operations: list[OperationData]
) -> OperationsScanResult:
    """Store/update operations in the database.

    Uses upsert logic - updates existing operations, inserts new ones.
    Handles duplicates by keeping the last occurrence.

    Returns OperationsScanResult with created/updated/total/duplicates/providers counts.
    """
    now = utcnow()
    logger.info(
        "Starting operations scan at %s with %d operations from Azure", now, len(operations)
    )

    ops_by_name, duplicates = _deduplicate_operations(operations)
    if duplicates > 0:
        logger.debug("Deduplicated %d operations (kept last occurrence)", duplicates)

    # Load existing operations by name
    existing = (await session.execute(select(Operation))).scalars().all()
    existing_by_name = {op.name: op for op in existing}
    logger.debug(
        "Loaded %d existing operations from DB, comparing with %d fetched operations",
        len(existing_by_name),
        len(ops_by_name),
    )

    created = updated = 0
    for name, op_data in ops_by_name.items():
        existing_op = existing_by_name.get(name)
        if existing_op is None:
            session.add(_create_operation(op_data, now))
            created += 1
        else:
            if _sync_operation_fields(existing_op, op_data):
                updated += 1
            existing_op.last_seen_at = now

    # Count unique providers
    providers = {
        op.provider_display_name for op in ops_by_name.values() if op.provider_display_name
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
    logger.info(
        "Operations scan committed: scan_id=%d, created=%d, updated=%d, total=%d, providers=%d",
        scan_status.id,
        created,
        updated,
        len(ops_by_name),
        len(providers),
    )

    # Invalidate and rebuild cache if new operations were added
    if created > 0:
        logger.info("New operations added: %d, triggering cache rebuild", created)
        await get_cache_service().invalidate_and_rebuild(session)

    return OperationsScanResult(
        created=created,
        updated=updated,
        total=len(ops_by_name),
        duplicates_skipped=duplicates,
        providers=len(providers),
    )
