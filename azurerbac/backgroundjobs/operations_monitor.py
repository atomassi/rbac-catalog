"""Monitor for Azure provider operations."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import OperationData
from azurerbac.backgroundjobs.models import OperationsScanResult
from azurerbac.core import Operation, OperationScanStatus, utcnow

logger = logging.getLogger(__name__)

_OPERATION_FIELDS = (
    "display_name",
    "description",
    "origin",
    "provider_display_name",
    "resource_type",
    "resource_type_display_name",
    "is_data_action",
)


class OperationChangeProcessor:
    """Process operation changes (new, updated)."""

    def __init__(
        self,
        session: AsyncSession,
        operations: list[OperationData],
    ) -> None:
        self._session = session
        self._ops_by_name, self._duplicates = self._deduplicate(operations)
        self._existing_by_name: dict[str, Operation] = {}
        self._timestamp = utcnow()

    @property
    def timestamp(self) -> dt.datetime:
        """Timestamp when processing started."""
        return self._timestamp

    @property
    def total_fetched(self) -> int:
        """Number of unique operations after deduplication."""
        return len(self._ops_by_name)

    @property
    def providers(self) -> set[str]:
        """Unique provider names from fetched operations."""
        return {
            op.provider_display_name
            for op in self._ops_by_name.values()
            if op.provider_display_name
        }

    @staticmethod
    def _deduplicate(
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

    async def process(self) -> OperationsScanResult:
        """Process all operations and return result."""
        self._existing_by_name = await self._load_existing_operations()

        if self._duplicates > 0:
            logger.debug("Deduplicated %d operations (kept last occurrence)", self._duplicates)

        created, updated = self._process_operations()
        await self._record_scan_status(created, updated)

        return OperationsScanResult(
            created=created,
            updated=updated,
            total=self.total_fetched,
            duplicates_skipped=self._duplicates,
            providers=len(self.providers),
        )

    async def _load_existing_operations(self) -> dict[str, Operation]:
        """Load all existing operations from database."""
        existing = (await self._session.execute(select(Operation))).scalars().all()
        return {op.name: op for op in existing}

    def _process_operations(self) -> tuple[int, int]:
        """Process all operations, returning (created, updated) counts."""
        created = updated = 0

        for name, op_data in self._ops_by_name.items():
            existing_op = self._existing_by_name.get(name)
            if existing_op is None:
                self._session.add(self._create_operation(op_data))
                created += 1
            else:
                if self._sync_fields(existing_op, op_data):
                    updated += 1
                existing_op.last_seen_at = self._timestamp

        return created, updated

    def _create_operation(self, op_data: OperationData) -> Operation:
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
            first_seen_at=self._timestamp,
            last_seen_at=self._timestamp,
        )

    @staticmethod
    def _sync_fields(existing_op: Operation, op_data: OperationData) -> bool:
        """Sync operation fields from source data. Returns True if any changed."""
        changed_fields: list[tuple[str, object, object]] = []
        for field in _OPERATION_FIELDS:
            old_val = getattr(existing_op, field)
            new_val = getattr(op_data, field)
            if old_val != new_val:
                setattr(existing_op, field, new_val)
                changed_fields.append((field, old_val, new_val))

        if changed_fields:
            logger.debug("Operation %s updated:", existing_op.name)
            for field, old_val, new_val in changed_fields:
                logger.debug("  %s: %r -> %r", field, old_val, new_val)
        return bool(changed_fields)

    async def _record_scan_status(self, created: int, updated: int) -> OperationScanStatus:
        """Record scan status in database."""
        scan_status = OperationScanStatus(
            scan_timestamp=self._timestamp,
            operations_scanned=self.total_fetched,
            providers_scanned=len(self.providers),
            additions=created,
            updates=updated,
        )
        self._session.add(scan_status)
        await self._session.flush()
        return scan_status


# -----------------------------------------------------------------------------
# Scan orchestration
# -----------------------------------------------------------------------------


async def apply_operations_scan(
    session: AsyncSession, operations: list[OperationData]
) -> OperationsScanResult:
    """Store/update operations in the database using upsert logic."""
    processor = OperationChangeProcessor(session, operations)

    logger.info(
        "Processing %d operations from Azure at %s",
        len(operations),
        processor.timestamp,
    )

    result = await processor.process()
    await session.commit()

    logger.info(
        "Operations scan committed: created=%d, updated=%d, total=%d, providers=%d",
        result.created,
        result.updated,
        result.total,
        result.providers,
    )

    return result
