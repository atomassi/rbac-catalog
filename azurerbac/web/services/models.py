"""Typed models for web service layer.

These dataclasses replace untyped dicts returned by service functions,
providing better type safety and IDE support.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from azurerbac.core.types import JsonDict

if TYPE_CHECKING:
    from azurerbac.azure.models import RoleDefinition
    from azurerbac.cache.models import CachedChangeEvent, CachedRole


@dataclass(frozen=True, slots=True)
class PaginationInfo:
    """Computed pagination metadata.

    Holds derived values (total_pages, start/end indices) to avoid
    repeating the same calculations in multiple routes.
    """

    total_pages: int
    start_idx: int
    end_idx: int

    @staticmethod
    def compute(total_items: int, page: int, page_size: int) -> PaginationInfo:
        """Compute pagination values from item count and page parameters.

        Args:
            total_items: Total number of items before pagination.
            page: Current page number (1-based).
            page_size: Number of items per page.

        Returns:
            PaginationInfo with total_pages, start_idx, end_idx.
        """
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        # Clamp page to available pages
        clamped_page = min(page, total_pages)
        start_idx = (clamped_page - 1) * page_size
        end_idx = start_idx + page_size
        return PaginationInfo(
            total_pages=total_pages,
            start_idx=start_idx,
            end_idx=end_idx,
        )


@dataclass(frozen=True, slots=True)
class PaginatedResult[T]:
    """Generic paginated result container.

    Replaces tuple[list[T], int, int] returns with a typed dataclass.
    """

    items: list[T]
    total_count: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class PatternMatchResult:
    """Result of finding a pattern that matches an operation.

    Used when checking which permission pattern grants an operation
    and whether conditions apply.
    """

    matched_pattern: str | None
    has_condition: bool
    condition_text: str | None


@dataclass(frozen=True, slots=True)
class ScanMetadata:
    """Scan timestamp metadata from cache or database.

    Contains first and last scan timestamps for display purposes.
    """

    last_scan: datetime | None
    first_scan: datetime | None


@dataclass(slots=True)
class RoleEffectivePermissions:
    """Effective permissions for a role after applying notActions/notDataActions.

    Contains control plane and data plane actions, counts, and metadata
    about wildcards and conditions.
    """

    control_plane_actions: list[str]
    data_plane_actions: list[str]
    control_plane_count: int
    data_plane_count: int
    has_conditions: bool
    has_wildcards: bool
    has_unresolved_permissions: bool
    raw_actions: list[str]
    raw_not_actions: list[str]
    raw_data_actions: list[str]
    raw_not_data_actions: list[str]

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "control_plane_actions": self.control_plane_actions,
            "data_plane_actions": self.data_plane_actions,
            "control_plane_count": self.control_plane_count,
            "data_plane_count": self.data_plane_count,
            "has_conditions": self.has_conditions,
            "has_wildcards": self.has_wildcards,
            "has_unresolved_permissions": self.has_unresolved_permissions,
            "raw_actions": self.raw_actions,
            "raw_not_actions": self.raw_not_actions,
            "raw_data_actions": self.raw_data_actions,
            "raw_not_data_actions": self.raw_not_data_actions,
        }


@dataclass(slots=True)
class EnrichedChangeEvent:
    """A role change event enriched with processed diff data.

    Used for displaying role history with formatted diff output.
    """

    scan_timestamp: datetime | None
    azure_updated_on: datetime | None
    event_type: str
    summary: str | None
    diff: JsonDict | None
    diff_pretty: str
    role_json_pretty: str

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "scan_timestamp": self.scan_timestamp,
            "azure_updated_on": self.azure_updated_on,
            "event_type": self.event_type,
            "summary": self.summary,
            "diff": self.diff,
            "diff_pretty": self.diff_pretty,
            "role_json_pretty": self.role_json_pretty,
        }


@dataclass(slots=True)
class RoleAllowingOperation:
    """A role that allows a specific operation.

    Contains role identification, the matched pattern, action counts,
    and condition information.
    """

    role_id: str
    role_name: str
    role_type: str
    matched_pattern: str
    actions_count: int
    data_actions_count: int
    has_condition: bool
    condition_text: str | None

    @classmethod
    def from_match(
        cls,
        role_id: str,
        role_name: str,
        role_type: str,
        control_count: int,
        data_count: int,
        match_result: PatternMatchResult,
    ) -> RoleAllowingOperation:
        """Create from role data and pattern match result.

        Args:
            role_id: The role's unique identifier.
            role_name: Display name of the role.
            role_type: Type of the role (e.g., "BuiltInRole").
            control_count: Number of control plane actions.
            data_count: Number of data plane actions.
            match_result: The pattern match result.

        Returns:
            RoleAllowingOperation instance.
        """
        return cls(
            role_id=role_id,
            role_name=role_name,
            role_type=role_type,
            matched_pattern=match_result.matched_pattern or "*",
            actions_count=control_count,
            data_actions_count=data_count,
            has_condition=match_result.has_condition,
            condition_text=match_result.condition_text,
        )

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_type": self.role_type,
            "matched_pattern": self.matched_pattern,
            "actions_count": self.actions_count,
            "data_actions_count": self.data_actions_count,
            "has_condition": self.has_condition,
            "condition_text": self.condition_text,
        }


@dataclass(slots=True)
class RoleWithCounts:
    """A role enriched with action counts from cache.

    Used for dashboard role listings where counts are needed.
    """

    role_id: str
    role_name: str
    role_type: str
    status: str
    updated_on: datetime | None
    actions_count: int
    data_actions_count: int

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_type": self.role_type,
            "status": self.status,
            "updated_on": self.updated_on,
            "actions_count": self.actions_count,
            "data_actions_count": self.data_actions_count,
        }


@dataclass(slots=True)
class DashboardSummary:
    """Summary data for dashboard pages.

    Contains total counts and scan timestamps.
    """

    total_roles: int
    total_operations: int
    last_scan: datetime | None
    first_scan: datetime | None

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "total_roles": self.total_roles,
            "total_operations": self.total_operations,
            "last_scan": self.last_scan,
            "first_scan": self.first_scan,
        }


@dataclass(slots=True)
class RoleDetailResult:
    """Result of fetching role detail data from cache or database.

    Replaces tuple return from get_role_from_cache_or_db for better type safety.
    """

    cached_role: CachedRole | None
    definition: RoleDefinition | None
    events: list[CachedChangeEvent]
    first_scan: datetime | None
