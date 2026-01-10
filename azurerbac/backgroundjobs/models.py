"""Typed models for background job results.

These dataclasses replace untyped dicts returned by scan functions,
providing better type safety and IDE support.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ScanResult:
    """Base class for scan operation results.

    Contains common counts shared by all scan types.
    """

    created: int
    updated: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "created": self.created,
            "updated": self.updated,
            "total": self.total,
        }


@dataclass(slots=True)
class RoleScanResult(ScanResult):
    """Result from a role scan operation.

    Extends ScanResult with deleted count.
    """

    deleted: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            **super().to_dict(),
            "deleted": self.deleted,
        }


@dataclass(slots=True)
class OperationsScanResult(ScanResult):
    """Result from an operations scan operation.

    Extends ScanResult with duplicates_skipped and providers count.
    """

    duplicates_skipped: int = 0
    providers: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            **super().to_dict(),
            "duplicates_skipped": self.duplicates_skipped,
            "providers": self.providers,
        }
