"""Cache data models.

CacheData Structure
===================
CacheData is the unified cache container holding all application state.
It's designed for atomic swaps - the entire object is replaced, never mutated.

    CacheData (immutable snapshot)
    ├── metadata: CacheMetadata          # Versioning and invalidation
    ├── source: SourceData               # Raw DB data (immutable after load)
    ├── indexes: Indexes                 # Fast lookups (deterministic from source)
    ├── analysis: RoleAnalysis           # Expensive precomputation (built once at refresh)
    ├── computed: ComputedCaches         # Expensive caches, SAVED to disk
    └── content: PrerenderedContent      # Pre-built responses (analytics, sitemap)

    CacheContainer (mutable wrapper)
    └── _request_caches: RequestCaches   # Cheap LRU caches, NOT saved (rebuilt lazily)
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import quote

from cachetools import LRUCache

from azurerbac.cache.utils import create_lru_cache, sitemap_url
from azurerbac.core.constants import RoleStatus
from azurerbac.core.types import JsonDict
from azurerbac.core.utils import format_datetime, parse_datetime

logger = logging.getLogger(__name__)
from azurerbac.matching.models import (
    CoverageResult,
    PartialCoverageCacheKey,
    PatternCacheKey,
    Plane,
    RoleCoverage,
    RoleNetPermissions,
)

if TYPE_CHECKING:
    from azurerbac.analytics.models import AnalyticsData
    from azurerbac.azure.models import OperationData, RoleDefinition

CACHE_VERSION: Final[str] = "v9"


@dataclass(slots=True)
class CachedRole:
    """Cached role with definition and tracking metadata."""

    definition: RoleDefinition
    status: RoleStatus
    last_seen_at: dt.datetime | None = None

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
    def created_on(self) -> dt.datetime | None:
        return self.definition.properties.created_on

    @property
    def updated_on(self) -> dt.datetime | None:
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
    scan_timestamp: dt.datetime | None = None
    azure_updated_on: dt.datetime | None = None
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
class Sitemap:
    """Pre-built sitemap XML content.

    Built once during cache refresh, avoiding iteration over 21k+ operations
    on every sitemap request.
    """

    content: str
    built_at: dt.datetime

    @classmethod
    def build(
        cls,
        roles_by_id: dict[str, CachedRole],
        all_operations: list[OperationData],
        site_url: str,
    ) -> Sitemap:
        """Build sitemap XML from roles and operations.

        Args:
            roles_by_id: Dict of role_id -> CachedRole.
            all_operations: List of all operations.
            site_url: Base URL for the site (e.g. https://azurerbac.com).

        Returns:
            Sitemap instance with pre-built XML content.
        """
        from azurerbac.core.utils import slugify

        active_roles_count = sum(1 for r in roles_by_id.values() if r.status == RoleStatus.ACTIVE)
        logger.debug(
            "Building sitemap: %d roles (%d active), %d operations",
            len(roles_by_id),
            active_roles_count,
            len(all_operations),
        )

        today = dt.datetime.now(dt.UTC).date().isoformat()

        urls = [
            sitemap_url(f"{site_url}/", today, "daily", 1.0),
            sitemap_url(f"{site_url}/roles", today, "daily", 0.95),
            sitemap_url(f"{site_url}/operations", today, "weekly", 0.9),
            sitemap_url(f"{site_url}/recommend", today, "weekly", 0.85),
            sitemap_url(f"{site_url}/analytics", today, "weekly", 0.7),
            sitemap_url(f"{site_url}/about", today, "monthly", 0.5),
        ]

        # Add role pages (sorted by role name) - include all roles (active + deleted)
        roles = [(role.role_id, role.role_name) for role in roles_by_id.values()]
        roles.sort(key=lambda x: x[1].lower())

        for role_id, role_name in roles:
            slug = slugify(role_name)
            urls.append(sitemap_url(f"{site_url}/roles/{role_id}/{slug}", today, "weekly", 0.8))

        # Add operation pages
        for op in all_operations:
            if op.name:
                encoded_name = quote(op.name, safe="")
                loc = f"{site_url}/operations/{encoded_name}"
                urls.append(sitemap_url(loc, today, "weekly", 0.7))

        content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

        logger.debug(
            "Sitemap built: %d URLs, %d bytes",
            len(urls),
            len(content.encode("utf-8")),
        )

        return cls(content=content, built_at=dt.datetime.now(dt.UTC))

    def to_dict(self) -> JsonDict:
        """Serialize to dict for cache storage."""
        return {
            "content": self.content,
            "built_at": format_datetime(self.built_at),
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> Sitemap:
        """Deserialize from dict."""
        return cls(
            content=data["content"],
            built_at=parse_datetime(data["built_at"]) or dt.datetime.now(dt.UTC),
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
class SourceData:
    """Raw data loaded from database (immutable after load)."""

    all_operations: list[OperationData] = field(default_factory=list)
    roles_by_id: dict[str, CachedRole] = field(default_factory=dict)
    all_change_events: list[CachedChangeEvent] = field(default_factory=list)
    unique_providers: list[str] = field(default_factory=list)
    last_scan: dt.datetime | None = None
    first_scan: dt.datetime | None = None


@dataclass
class Indexes:
    """Fast lookup structures (deterministic, built from source)."""

    ops_by_name_lower: dict[str, OperationData] = field(default_factory=dict)
    ops_by_prefix: dict[str, list[OperationData]] = field(default_factory=dict)
    ops_by_prefix_by_plane: dict[Plane, dict[str, set[str]]] = field(default_factory=dict)


@dataclass
class RoleAnalysis:
    """Precomputed role permission analysis (built once at refresh, expensive)."""

    role_coverage: dict[str, RoleCoverage] = field(default_factory=dict)
    operation_to_roles: dict[str, list[str]] = field(default_factory=dict)


# Maximum entries in the allowing_roles cache (roles granting each operation)
# Estimated ~10KB per entry ≈ 50MB max memory
_ALLOWING_ROLES_CACHE_MAX_SIZE: Final[int] = 5000

# Maximum entries in the filtered events cache (by days/event_type)
_FILTERED_EVENTS_CACHE_MAX_SIZE: Final[int] = 100

# Maximum entries in role_pages cache (paginated role lists)
# Each page is ~50 roles x ~1KB = ~50KB per entry, ~25MB max
_ROLE_PAGES_CACHE_MAX_SIZE: Final[int] = 500

# Maximum entries in operation_pages cache (paginated operation lists)
# Each page is ~50 operations x ~0.5KB = ~25KB per entry, ~12.5MB max
_OPERATION_PAGES_CACHE_MAX_SIZE: Final[int] = 500


@dataclass
class ComputedCaches:
    """Deterministic caches - expensive to compute, SAVED to disk.

    These caches are built from source data and are expensive to recompute
    (e.g., wildcard expansion across 20K+ operations). They are persisted
    to disk and loaded on restart.
    """

    pattern_match: dict[PatternCacheKey, set[str]] = field(default_factory=dict)
    partial_coverage: dict[PartialCoverageCacheKey, CoverageResult] = field(default_factory=dict)
    wildcard_count: dict[PatternCacheKey, int] = field(default_factory=dict)


@dataclass
class RequestCaches:
    """Request-specific caches - cheap to rebuild, NOT saved to disk.

    These caches vary by request parameters (page number, filters, etc.)
    and are cheap to rebuild from in-memory data. They use LRU eviction
    to bound memory usage.
    """

    role_pages: LRUCache[str, Any] = field(
        default_factory=lambda: create_lru_cache(_ROLE_PAGES_CACHE_MAX_SIZE)
    )
    operation_pages: LRUCache[str, Any] = field(
        default_factory=lambda: create_lru_cache(_OPERATION_PAGES_CACHE_MAX_SIZE)
    )
    allowing_roles: LRUCache[str, Any] = field(
        default_factory=lambda: create_lru_cache(_ALLOWING_ROLES_CACHE_MAX_SIZE)
    )
    filtered_events: LRUCache[str, Any] = field(
        default_factory=lambda: create_lru_cache(_FILTERED_EVENTS_CACHE_MAX_SIZE)
    )


@dataclass
class PrerenderedContent:
    """Pre-built content to avoid expensive re-computation per request."""

    analytics: AnalyticsData | None = None
    sitemap: Sitemap | None = None


@dataclass
class CacheData:
    """Unified cache container - all data in one atomically-swappable object.

    Structure:
        CacheData
        ├── metadata        # Version, hashes (for invalidation)
        ├── source          # Raw DB data (roles, operations, events)
        ├── indexes         # Lookup indexes (ops_by_name, ops_by_prefix)
        ├── analysis        # Precomputed (role_coverage, operation_to_roles)
        ├── computed        # Expensive caches, SAVED to disk
        └── content         # Pre-rendered (analytics, sitemap)

    Note: RequestCaches (role_pages, operation_pages, etc.) live on CacheContainer,
    not here, since they're mutable LRU caches that get cleared on swap.
    """

    # Metadata (for versioning and invalidation)
    metadata: CacheMetadata = field(default_factory=CacheMetadata)

    # Data layers (ordered by lifecycle)
    source: SourceData = field(default_factory=SourceData)  # From DB
    indexes: Indexes = field(default_factory=Indexes)  # Built from source
    analysis: RoleAnalysis = field(default_factory=RoleAnalysis)  # Built from source + indexes
    computed: ComputedCaches = field(default_factory=ComputedCaches)  # Persisted to disk
    content: PrerenderedContent = field(default_factory=PrerenderedContent)  # Pre-built responses

    # =========================================================================
    # Factory method for flat construction (test convenience)
    # =========================================================================
    @classmethod
    def create(
        cls,
        *,
        metadata: CacheMetadata | None = None,
        all_operations: list[OperationData] | None = None,
        roles_by_id: dict[str, CachedRole] | None = None,
        all_change_events: list[CachedChangeEvent] | None = None,
        unique_providers: list[str] | None = None,
        last_scan: dt.datetime | None = None,
        first_scan: dt.datetime | None = None,
        ops_by_name_lower: dict[str, OperationData] | None = None,
        ops_by_prefix: dict[str, list[OperationData]] | None = None,
        ops_by_prefix_by_plane: dict[Plane, dict[str, set[str]]] | None = None,
        role_coverage: dict[str, RoleCoverage] | None = None,
        operation_to_roles: dict[str, list[str]] | None = None,
        pattern_match: dict[PatternCacheKey, set[str]] | None = None,
        partial_coverage: dict[PartialCoverageCacheKey, CoverageResult] | None = None,
        wildcard_count: dict[PatternCacheKey, int] | None = None,
        analytics: AnalyticsData | None = None,
        sitemap: Sitemap | None = None,
    ) -> CacheData:
        """Create CacheData with flat arguments (for test convenience)."""
        return cls(
            metadata=metadata or CacheMetadata(),
            source=SourceData(
                all_operations=all_operations or [],
                roles_by_id=roles_by_id or {},
                all_change_events=all_change_events or [],
                unique_providers=unique_providers or [],
                last_scan=last_scan,
                first_scan=first_scan,
            ),
            indexes=Indexes(
                ops_by_name_lower=ops_by_name_lower or {},
                ops_by_prefix=ops_by_prefix or {},
                ops_by_prefix_by_plane=ops_by_prefix_by_plane or {},
            ),
            analysis=RoleAnalysis(
                role_coverage=role_coverage or {},
                operation_to_roles=operation_to_roles or {},
            ),
            computed=ComputedCaches(
                pattern_match=pattern_match or {},
                partial_coverage=partial_coverage or {},
                wildcard_count=wildcard_count or {},
            ),
            content=PrerenderedContent(
                analytics=analytics,
                sitemap=sitemap,
            ),
        )

    # =========================================================================
    # Convenience accessors (backward compatibility)
    # =========================================================================
    @property
    def all_operations(self) -> list[OperationData]:
        return self.source.all_operations

    @property
    def roles_by_id(self) -> dict[str, CachedRole]:
        return self.source.roles_by_id

    @property
    def all_change_events(self) -> list[CachedChangeEvent]:
        return self.source.all_change_events

    @property
    def unique_providers(self) -> list[str]:
        return self.source.unique_providers

    @property
    def last_scan(self) -> dt.datetime | None:
        return self.source.last_scan

    @property
    def first_scan(self) -> dt.datetime | None:
        return self.source.first_scan

    @property
    def ops_by_name_lower(self) -> dict[str, OperationData]:
        return self.indexes.ops_by_name_lower

    @property
    def ops_by_prefix(self) -> dict[str, list[OperationData]]:
        return self.indexes.ops_by_prefix

    @property
    def ops_by_prefix_by_plane(self) -> dict[Plane, dict[str, set[str]]]:
        return self.indexes.ops_by_prefix_by_plane

    @property
    def role_coverage(self) -> dict[str, RoleCoverage]:
        return self.analysis.role_coverage

    @cached_property
    def role_net_permissions(self) -> dict[str, RoleNetPermissions]:
        """Derived from role_coverage: count of control/data ops per role."""
        return {
            role_id: RoleNetPermissions(len(cov.control), len(cov.data))
            for role_id, cov in self.analysis.role_coverage.items()
        }

    @property
    def operation_to_roles(self) -> dict[str, list[str]]:
        return self.analysis.operation_to_roles

    @property
    def pattern_match(self) -> dict[PatternCacheKey, set[str]]:
        return self.computed.pattern_match

    @property
    def partial_coverage(self) -> dict[PartialCoverageCacheKey, CoverageResult]:
        return self.computed.partial_coverage

    @property
    def wildcard_count(self) -> dict[PatternCacheKey, int]:
        return self.computed.wildcard_count

    @property
    def analytics(self) -> AnalyticsData | None:
        return self.content.analytics

    @property
    def sitemap(self) -> Sitemap | None:
        return self.content.sitemap

    # =========================================================================
    # Derived properties (computed lazily from source data)
    # =========================================================================

    @cached_property
    def ops_names_set(self) -> set[str]:
        """Set of all operation names (original case)."""
        return {op.name for op in self.all_operations}

    @cached_property
    def ops_lowered_to_orig(self) -> dict[str, str]:
        """Mapping from lowered operation name to original casing."""
        return {op.name.lower(): op.name for op in self.all_operations}

    @cached_property
    def control_ops_lowered(self) -> frozenset[str]:
        """Frozenset of control plane operation names (lowered)."""
        return frozenset(op.name.lower() for op in self.all_operations if not op.is_data_action)

    @cached_property
    def data_ops_lowered(self) -> frozenset[str]:
        """Frozenset of data plane operation names (lowered)."""
        return frozenset(op.name.lower() for op in self.all_operations if op.is_data_action)

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
