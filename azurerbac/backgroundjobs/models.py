"""Background job result models."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from azurerbac.core.types import JsonDict


@dataclass(slots=True)
class ScanResult:
    """Base scan result."""

    created: int
    updated: int
    total: int

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(slots=True)
class RoleScanResult(ScanResult):
    """Role scan result with deleted count."""

    deleted: int = 0

    @property
    def has_changes(self) -> bool:
        """True if any changes were detected."""
        return bool(self.created or self.updated or self.deleted)


@dataclass(slots=True)
class OperationsScanResult(ScanResult):
    """Operations scan result with duplicates and providers."""

    duplicates_skipped: int = 0
    providers: int = 0
