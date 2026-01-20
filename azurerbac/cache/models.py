"""Cache data models."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Final

from azurerbac.core.constants import RoleStatus
from azurerbac.core.types import JsonDict
from azurerbac.core.utils import format_datetime, parse_datetime
from azurerbac.matching.models import (
    CacheOpsCount,
    CoverageResult,
    PartialCoverageCacheKey,
    PatternCacheKey,
    Plane,
    RoleCoverage,
    RoleNetPermissions,
)

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, RoleDefinition

CACHE_VERSION: Final[str] = "v7"


@dataclass(slots=True)
class CachedRole:
    """Cached role with definition and tracking metadata."""

    definition: RoleDefinition
    status: RoleStatus
    last_seen_at: datetime | None = None

    @property
    def role_id(self) -> str:
        return self.definition.role_id

    @property
    def role_name(self) -> str:
        return self.definition.role_name

    @property
    def role_type(self) -> str:
        return self.definition.role_type

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def created_on(self) -> datetime | None:
        return self.definition.properties.created_on

    @property
    def updated_on(self) -> datetime | None:
        return self.definition.properties.updated_on

    def to_dict(self) -> JsonDict:
        return {
            "definition": self.definition.to_dict(),
            "status": self.status.value,
            "last_seen_at": format_datetime(self.last_seen_at),
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> CachedRole:
        from azurerbac.azure.models import RoleDefinition

        return cls(
            definition=RoleDefinition.model_validate(data["definition"]),
            status=RoleStatus(data["status"]),
            last_seen_at=parse_datetime(data.get("last_seen_at")),
        )


@dataclass(slots=True)
class CachedChangeEvent:
    """Cached change event from role history."""

    id: int
    role_id: str
    role_name: str
    event_type: str
    scan_timestamp: datetime | None = None
    azure_updated_on: datetime | None = None
    summary: str | None = None
    diff_json: JsonDict | None = None
    role_json: JsonDict | None = None

    def to_dict(self) -> JsonDict:
        return {
            "id": self.id,
            "role_id": self.role_id,
            "role_name": self.role_name,
            "event_type": self.event_type,
            "scan_timestamp": format_datetime(self.scan_timestamp),
            "azure_updated_on": format_datetime(self.azure_updated_on),
            "summary": self.summary,
            "diff_json": self.diff_json,
            "role_json": self.role_json,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> CachedChangeEvent:
        return cls(
            id=data["id"],
            role_id=data["role_id"],
            role_name=data["role_name"],
            event_type=data["event_type"],
            scan_timestamp=parse_datetime(data.get("scan_timestamp")),
            azure_updated_on=parse_datetime(data.get("azure_updated_on")),
            summary=data.get("summary"),
            diff_json=data.get("diff_json"),
            role_json=data.get("role_json"),
        )


@dataclass(slots=True)
class CacheMetadata:
    """Metadata for cache invalidation."""

    version: str = CACHE_VERSION
    roles_count: int = 0
    operations_count: int = 0
    roles_hash: str = ""
    operations_hash: str = ""
    created_at: float = field(default_factory=time.time)

    def _version_matches(self) -> bool:
        return self.version == CACHE_VERSION

    def _counts_match(self, roles_count: int, operations_count: int) -> bool:
        return self.roles_count == roles_count and self.operations_count == operations_count

    def _hash_matches(self, stored: str, provided: str) -> bool:
        """Check if hashes match. Empty strings on either side are ignored."""
        if not stored or not provided:
            return True
        return stored == provided

    def is_valid_for(
        self,
        roles_count: int,
        operations_count: int,
        roles_hash: str = "",
        operations_hash: str = "",
    ) -> bool:
        """Check if cache is still valid for given parameters."""
        return (
            self._version_matches()
            and self._counts_match(roles_count, operations_count)
            and self._hash_matches(self.roles_hash, roles_hash)
            and self._hash_matches(self.operations_hash, operations_hash)
        )


@dataclass
class CacheData:
    """Unified cache container - all data in one atomically-swappable object."""

    # Metadata (for versioning and invalidation)
    metadata: CacheMetadata = field(default_factory=CacheMetadata)

    # Raw data (from database)
    all_operations: list[OperationData] = field(default_factory=list)
    roles_by_id: dict[str, CachedRole] = field(default_factory=dict)
    all_change_events: list[CachedChangeEvent] = field(default_factory=list)
    unique_providers: list[str] = field(default_factory=list)
    last_scan: datetime | None = None
    first_scan: datetime | None = None

    # Indexes (built from raw data for fast lookup)
    ops_by_name_lower: dict[str, OperationData] = field(default_factory=dict)
    ops_by_prefix: dict[str, list[OperationData]] = field(default_factory=dict)

    # Computed caches (expensive analysis, rebuilt on data change)
    role_coverage: dict[str, RoleCoverage] = field(default_factory=dict)
    role_net_permissions: dict[str, RoleNetPermissions] = field(default_factory=dict)
    operation_role_count: dict[str, int] = field(default_factory=dict)
    pattern_match: dict[PatternCacheKey, set[str]] = field(default_factory=dict)
    partial_coverage: dict[PartialCoverageCacheKey, CoverageResult] = field(default_factory=dict)
    wildcard_count: dict[PatternCacheKey, int] = field(default_factory=dict)
    operations_by_prefix_computed: dict[Plane, dict[str, set[str]]] = field(default_factory=dict)
    cache_ops_count: CacheOpsCount = field(default_factory=lambda: CacheOpsCount(0, 0))

    @cached_property
    def ops_names_set(self) -> set[str]:
        """Set of all operation names (original case)."""
        return {op.name for op in self.all_operations}

    @cached_property
    def ops_folded_to_orig(self) -> dict[str, str]:
        """Mapping from casefolded operation name to original casing."""
        return {op.name.casefold(): op.name for op in self.all_operations}

    def get_role_definitions(self) -> list[RoleDefinition]:
        """Get all active roles as RoleDefinition objects."""
        return [r.definition for r in self.roles_by_id.values() if r.status == RoleStatus.ACTIVE]


def compute_roles_hash(roles: list[RoleDefinition]) -> str:
    """Compute hash of role data for change detection."""
    from azurerbac.core.utils import content_hash

    def role_key(role: RoleDefinition) -> str:
        updated = role.properties.updated_on.isoformat() if role.properties.updated_on else ""
        return f"{role.role_id}:{updated}"

    return content_hash("|".join(sorted(role_key(r) for r in roles)))


def compute_operations_hash(operations: list[OperationData]) -> str:
    """Compute hash of operation data for change detection."""
    from azurerbac.core.utils import content_hash

    return content_hash("|".join(sorted(op.name for op in operations)))


def build_indexes(
    operations: list[OperationData],
) -> tuple[dict[str, OperationData], dict[str, list[OperationData]]]:
    """Build operation indexes from raw operation list.

    Returns:
        (ops_by_name_lower, ops_by_prefix)
    """
    ops_by_name_lower = {op.name.lower(): op for op in operations}

    ops_by_prefix: dict[str, list[OperationData]] = {}
    for op in operations:
        name_lower = op.name.lower()
        slash_idx = name_lower.find("/")
        if slash_idx > 0:
            prefix = name_lower[:slash_idx]
            ops_by_prefix.setdefault(prefix, []).append(op)

    return ops_by_name_lower, ops_by_prefix
