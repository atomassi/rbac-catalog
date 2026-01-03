"""Role Recommender - Suggests least-privilege roles based on selected operations.

This module provides the main entry point for role recommendations.
The heavy lifting is delegated to RoleRecommendationService.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from azurerbac.core import HIGH_PRIVILEGE_ROLES
from azurerbac.matching.recommendation_service import (
    RoleEvaluationContext,
    RoleRecommendationService,
)


@dataclass(slots=True)
class RoleMatch:
    """Represents a role that matches the requested permissions.

    This is the result object returned by recommend_roles, containing
    all information about how well a role matches the requested operations.
    """

    role_id: str
    role_name: str
    description: str
    matched_operations: list[str] = field(default_factory=list)
    missing_operations: list[str] = field(default_factory=list)
    total_permissions_granted: int = 0
    control_plane_permissions: int = 0
    data_plane_permissions: int = 0
    is_high_privilege: bool = False
    match_percentage: float = 0.0
    has_conditions: bool = False
    matched_operations_count: int = 0
    requested_operations_count: int = 0
    missing_operations_expanded: list[str] = field(default_factory=list)
    missing_operations_count: int = 0
    has_partial_wildcard_match: bool = False

    @property
    def is_full_match(self) -> bool:
        """Check if all requested operations are covered."""
        return len(self.missing_operations) == 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "matched_operations": self.matched_operations,
            "missing_operations": self.missing_operations,
            "missing_operations_expanded": self.missing_operations_expanded,
            "missing_operations_count": self.missing_operations_count,
            "has_partial_wildcard_match": self.has_partial_wildcard_match,
            "total_permissions": self.total_permissions_granted,
            "control_plane_permissions": self.control_plane_permissions,
            "data_plane_permissions": self.data_plane_permissions,
            "is_high_privilege": self.is_high_privilege,
            "has_conditions": self.has_conditions,
            "match_percentage": self.match_percentage,
            "is_full_match": self.is_full_match,
            "matched_operations_count": self.matched_operations_count,
            "requested_operations_count": self.requested_operations_count,
        }


def recommend_roles(
    requested_operations: list[str],
    roles: list[dict[str, Any]],
    all_operations: list[dict[str, Any]],
    max_results: int | None = None,
    requested_ops_data_flags: dict[str, bool] | None = None,
) -> list[RoleMatch]:
    """Recommend roles that grant the requested operations.

    This function finds Azure built-in roles that best match the requested
    operations, sorted by least privilege (fewest total permissions first).

    Args:
        requested_operations: List of operation names the user needs
        roles: List of role definitions (from Role.role_json)
        all_operations: List of all known operations
        max_results: Maximum number of results to return
        requested_ops_data_flags: Optional explicit is_data_action flags

    Returns:
        List of RoleMatch objects, sorted by least privilege with
        high-privilege roles at the bottom.
    """
    if not requested_operations:
        return []

    # Initialize service and prepare classification
    svc = RoleRecommendationService(all_operations, requested_ops_data_flags)
    svc.check_cache_staleness()

    classified = svc.classify_operations(requested_operations)
    total_requested = svc.compute_wildcard_matches(classified)

    # Evaluate each role
    matches: list[RoleMatch] = []
    has_cache = svc.has_full_cache()

    for role in roles:
        if not svc.is_builtin_role(role):
            continue

        role_id, role_name, description, permissions = svc.extract_role_info(role)
        cached_coverage = svc.get_cached_coverage(role_id)

        # Create evaluation context
        ctx = RoleEvaluationContext(
            role_id=role_id,
            role_name=role_name,
            description=description,
            permissions=permissions,
        )

        # Evaluate role coverage
        if cached_coverage and has_cache:
            svc.evaluate_role_fast_path(ctx, classified, cached_coverage)
        else:
            svc.evaluate_role_slow_path(ctx, classified, cached_coverage)

        # Finalize partial coverage
        svc.finalize_partial_coverage(ctx)

        if not ctx.matched_ops:
            continue

        # Calculate missing operations
        missing_ops = svc.calculate_missing_ops(ctx, classified)

        # Calculate statistics
        matched_count = svc.calculate_matched_ops_count(ctx, cached_coverage)
        control_perms, data_perms = svc.calculate_permissions_count(ctx)
        expanded, missing_count = svc.expand_missing_operations(ctx, missing_ops, classified)

        # Calculate match percentage
        match_pct = (matched_count / total_requested * 100) if total_requested > 0 else 0.0

        # Build result
        matches.append(
            RoleMatch(
                role_id=role_id,
                role_name=role_name,
                description=description,
                matched_operations=sorted(ctx.matched_ops),
                missing_operations=sorted(missing_ops),
                total_permissions_granted=control_perms + data_perms,
                control_plane_permissions=control_perms,
                data_plane_permissions=data_perms,
                is_high_privilege=role_name in HIGH_PRIVILEGE_ROLES,
                match_percentage=match_pct,
                has_conditions=ctx.has_conditions,
                matched_operations_count=matched_count,
                requested_operations_count=total_requested,
                missing_operations_expanded=sorted(expanded),
                missing_operations_count=missing_count,
                has_partial_wildcard_match=bool(ctx.wildcard_partial_coverage) or bool(missing_ops),
            )
        )

    return _sort_and_filter_results(matches, max_results)


def _sort_and_filter_results(matches: list[RoleMatch], max_results: int | None) -> list[RoleMatch]:
    """Sort and filter role matches by least privilege.

    Full matches are returned before partial matches. Within each group,
    non-high-privilege roles come first, sorted by fewest permissions.

    Args:
        matches: List of role matches to sort
        max_results: Maximum results to return (None = no limit)
    """
    full_matches = [m for m in matches if m.is_full_match]
    partial_matches = [m for m in matches if not m.is_full_match]

    if full_matches:
        # Return only full matches, sorted by least privilege
        full_matches.sort(
            key=lambda m: (
                m.is_high_privilege,
                m.total_permissions_granted,
            )
        )
        return full_matches[:max_results] if max_results else full_matches

    # No full matches - return best partial matches
    partial_matches.sort(
        key=lambda m: (
            -m.match_percentage,
            m.is_high_privilege,
            m.total_permissions_granted,
        )
    )
    # Limit partial matches to 10 unless explicitly requested more
    limit = max_results if max_results else 10
    return partial_matches[:limit]
