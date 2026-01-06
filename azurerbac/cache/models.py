"""Cache data models.

Single unified cache container that holds all data - both raw and computed.
Everything is swapped atomically to prevent race conditions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any, Final

from azurerbac.core.constants import RoleStatus

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, RoleDefinition

CACHE_VERSION: Final[str] = (
    "v7"  # Bump to force cache rebuild (simplified CachedRole, OperationData)
)


@dataclass(slots=True)
class CachedRole:
    """A cached role combining RoleDefinition with database tracking metadata.

    Provides convenience properties (role_id, role_name, etc.) that delegate
    to the underlying RoleDefinition. This allows CachedRole to be used
    interchangeably with SQLAlchemy Role in many contexts.

    This class only adds fields that come from our database, not from Azure:
    - status: RoleStatus enum (ACTIVE or DELETED)
    - last_seen_at: Last time we saw this role in Azure
    """

    definition: RoleDefinition
    status: RoleStatus  # From our database tracking
    last_seen_at: datetime | None = None  # Last time we saw this role in Azure

    @property
    def role_id(self) -> str:
        """Get role ID (GUID) from definition."""
        return self.definition.role_id

    @property
    def role_name(self) -> str:
        """Get role display name from definition."""
        return self.definition.role_name

    @property
    def role_type(self) -> str:
        """Get role type from definition."""
        return self.definition.role_type

    @property
    def description(self) -> str:
        """Get role description from definition."""
        return self.definition.description

    @property
    def created_on(self) -> datetime | None:
        """Get created timestamp from definition."""
        return self.definition.properties.created_on

    @property
    def updated_on(self) -> datetime | None:
        """Get updated timestamp from definition."""
        return self.definition.properties.updated_on

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization (disk persistence)."""
        return {
            "definition": self.definition.to_dict(),
            "status": self.status.value,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CachedRole:
        """Create from dict (disk persistence)."""
        from azurerbac.azure.models import RoleDefinition

        def parse_dt(val: str | datetime | None) -> datetime | None:
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            return datetime.fromisoformat(val)

        return cls(
            definition=RoleDefinition.model_validate(data["definition"]),
            status=RoleStatus(data["status"]),
            last_seen_at=parse_dt(data.get("last_seen_at")),
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

    def is_valid_for(
        self,
        roles_count: int,
        operations_count: int,
        roles_hash: str = "",
        operations_hash: str = "",
    ) -> bool:
        """Check if cache is still valid."""
        if self.version != CACHE_VERSION:
            return False
        if self.roles_count != roles_count or self.operations_count != operations_count:
            return False
        if roles_hash and self.roles_hash and self.roles_hash != roles_hash:
            return False
        return not (
            operations_hash and self.operations_hash and self.operations_hash != operations_hash
        )


@dataclass
class CacheData:
    """Unified cache container - all data in one atomically-swappable object.

    Contains:
    - Metadata: version, counts, hashes for invalidation
    - Raw data: operations, roles, events (loaded from DB/disk)
    - Indexes: fast lookup structures (built from raw data)
    - Computed: role coverage, pattern matching (expensive analysis)

    All fields are saved to disk including computed caches.
    On reload, everything is loaded directly - no recomputation needed.
    All fields are immutable after creation - to update, create new instance and swap.
    """

    # ─────────────────────────────────────────────────────────────────────────
    # Metadata (for versioning and invalidation)
    # ─────────────────────────────────────────────────────────────────────────

    metadata: CacheMetadata = field(default_factory=CacheMetadata)

    # ─────────────────────────────────────────────────────────────────────────
    # Raw data (from database)
    # ─────────────────────────────────────────────────────────────────────────

    all_operations: list[OperationData] = field(default_factory=list)
    roles_by_id: dict[str, CachedRole] = field(default_factory=dict)
    all_change_events: list[dict] = field(default_factory=list)
    unique_providers: list[str] = field(default_factory=list)
    last_scan: Any = None
    first_scan: Any = None

    # ─────────────────────────────────────────────────────────────────────────
    # Indexes (built from raw data for fast lookup)
    # ─────────────────────────────────────────────────────────────────────────

    # name.lower() -> OperationData (for case-insensitive lookup)
    ops_by_name_lower: dict[str, OperationData] = field(default_factory=dict)

    # provider prefix -> list of operations (for wildcard search optimization)
    ops_by_prefix: dict[str, list[OperationData]] = field(default_factory=dict)

    # ─────────────────────────────────────────────────────────────────────────
    # Computed caches (expensive analysis, rebuilt on data change)
    # ─────────────────────────────────────────────────────────────────────────

    # role_id -> (control_ops: set, data_ops: set) with original casing
    role_coverage: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)

    # role_id -> (control_perms_count, data_perms_count)
    role_net_permissions: dict[str, tuple[int, int]] = field(default_factory=dict)

    # operation_name.lower() -> count of roles granting it
    operation_role_count: dict[str, int] = field(default_factory=dict)

    # (pattern, cache_key) -> set of matching operation names
    pattern_match: dict[tuple[str, int], set[str]] = field(default_factory=dict)

    # (pattern, cache_key, actions, not_actions) -> (covered, total, uncovered, sample)
    partial_coverage: dict[tuple, tuple[int, int, int, list[str]]] = field(default_factory=dict)

    # (pattern, cache_key) -> match count
    wildcard_count: dict[tuple[str, int], int] = field(default_factory=dict)

    # cache_key -> {provider_prefix -> set[operation_names]}
    operations_by_prefix_computed: dict[int, dict[str, set[str]]] = field(default_factory=dict)

    # [control_ops_count, data_ops_count] when cache was built
    cache_ops_count: list[int] = field(default_factory=lambda: [0, 0])

    # ─────────────────────────────────────────────────────────────────────────
    # Derived properties (computed on-demand, not stored)
    # ─────────────────────────────────────────────────────────────────────────

    @cached_property
    def ops_names_set(self) -> set[str]:
        """Set of all operation names (original case)."""
        return {op.name for op in self.all_operations}

    def get_role_definitions(self) -> list[RoleDefinition]:
        """Get all roles as RoleDefinition objects.

        Extracts the definition from each CachedRole.
        Filters out deleted roles (status != 'active').
        """
        return [r.definition for r in self.roles_by_id.values() if r.status == "active"]

    def get_role_definition_by_id(self, role_id: str) -> RoleDefinition | None:
        """Get a single role as RoleDefinition by ID.

        Returns None if role not found.
        """
        if cached_role := self.roles_by_id.get(role_id):
            return cached_role.definition
        return None


def compute_roles_hash(roles: list[RoleDefinition]) -> str:
    """Compute hash of role data for change detection."""
    from azurerbac.core.utils import content_hash

    data = []
    for role in roles:
        updated = role.properties.updated_on.isoformat() if role.properties.updated_on else ""
        data.append(f"{role.role_id}:{updated}")
    combined = "|".join(sorted(data))
    return content_hash(combined)


def compute_operations_hash(operations: list[OperationData]) -> str:
    """Compute hash of operation data for change detection."""
    from azurerbac.core.utils import content_hash

    names = sorted(op.name for op in operations)
    combined = "|".join(names)
    return content_hash(combined)


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
        name = op.name.lower()
        slash_idx = name.find("/")
        if slash_idx > 0:
            prefix = name[:slash_idx]
            ops_by_prefix.setdefault(prefix, []).append(op)

    return ops_by_name_lower, ops_by_prefix
