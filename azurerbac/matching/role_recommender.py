"""Role Recommender - Suggests least-privilege roles based on selected operations.

This module provides the main entry point for role recommendations.
The heavy lifting is delegated to RoleRecommendationService.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from azurerbac.azure.models import RoleDefinition
from azurerbac.core import HIGH_PRIVILEGE_ROLES
from azurerbac.matching.models import (
    RoleCoverage,
    RoleMatch,
    RoleNetPermissions,
)
from azurerbac.matching.recommendation_service import (
    RoleEvaluationContext,
    RoleRecommendationService,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _RoleCandidate:
    """Lightweight candidate for sorting before expensive expansion."""

    ctx: RoleEvaluationContext
    cached_coverage: RoleCoverage
    missing_ops: set[str]
    matched_count: int
    perms: RoleNetPermissions
    match_pct: float
    is_full_match: bool


def recommend_roles(
    requested_operations: list[str],
    roles: list[RoleDefinition] | None = None,
    max_results: int | None = None,
    requested_ops_data_flags: dict[str, bool] | None = None,
) -> list[RoleMatch]:
    """Recommend roles that grant the requested operations, sorted by least privilege.

    Args:
        requested_operations: Operations to find roles for.
        roles: Optional roles to evaluate. If None, uses roles from cache.
        max_results: Optional limit on results.
        requested_ops_data_flags: Optional mapping of operation names to is_data_action flags.

    Returns:
        List of matching roles sorted by least privilege.
    """
    if not requested_operations:
        return []

    # Initialize service (uses cached frozensets, O(1))
    svc = RoleRecommendationService(requested_ops_data_flags=requested_ops_data_flags)

    # Use provided roles or get from cache
    roles = roles or svc.get_all_roles()

    classified = svc.classify_operations(requested_operations)
    logger.debug(
        "Classified %d requested ops: control=%d, data=%d, control_wildcards=%d, data_wildcards=%d",
        len(requested_operations),
        len(classified.control),
        len(classified.data),
        len(classified.control_wildcards),
        len(classified.data_wildcards),
    )

    total_requested = svc.compute_wildcard_matches(classified)
    logger.debug(
        "Expanded wildcards: total_requested=%d ops (control_wc_ops=%d, data_wc_ops=%d)",
        total_requested,
        sum(len(ops) for ops in svc.control_wildcard_ops.values()),
        sum(len(ops) for ops in svc.data_wildcard_ops.values()),
    )

    # Phase 1: Evaluate roles and collect lightweight candidates
    candidates: list[_RoleCandidate] = []
    roles_evaluated = roles_with_matches = 0

    for role in roles:
        if not svc.is_builtin_role(role):
            continue

        roles_evaluated += 1
        role_info = svc.extract_role_info(role)
        cached_coverage = svc.get_cached_coverage(role_info.role_id)

        # Cache must be available (built at startup)
        if not cached_coverage:
            raise RuntimeError(f"RoleCoverage cache unavailable for role {role_info.role_id}")

        # Create evaluation context
        ctx = RoleEvaluationContext(
            role_id=role_info.role_id,
            role_name=role_info.role_name,
            description=role_info.description,
            permissions=role_info.permissions,
        )

        # Evaluate role coverage using cached data
        svc.evaluate_role_fast_path(ctx, classified, cached_coverage)

        # Finalize partial coverage
        svc.finalize_partial_coverage(ctx)

        if not ctx.matched_ops:
            continue

        roles_with_matches += 1

        # Calculate lightweight stats (no expensive expansion yet)
        missing_ops = svc.calculate_missing_ops(ctx, classified)
        matched_count = svc.calculate_matched_ops_count(ctx, cached_coverage)
        perms = svc.calculate_permissions_count(ctx)
        match_pct = (matched_count / total_requested * 100) if total_requested > 0 else 0.0
        is_full = not missing_ops

        candidates.append(
            _RoleCandidate(
                ctx=ctx,
                cached_coverage=cached_coverage,
                missing_ops=missing_ops,
                matched_count=matched_count,
                perms=perms,
                match_pct=match_pct,
                is_full_match=is_full,
            )
        )

    # Phase 2: Sort and filter to get top candidates
    top_candidates = _sort_and_filter_candidates(candidates, max_results)

    # Phase 3: Expand missing operations ONLY for top candidates
    matches: list[RoleMatch] = []
    for c in top_candidates:
        # Expensive expansion - only for results we'll return
        expanded = svc.expand_missing_operations(
            c.ctx, c.missing_ops, classified, c.cached_coverage
        )

        # Restore original casing for display
        matched_ops_display = svc.restore_original_casing(c.ctx.matched_ops)
        missing_ops_display = svc.restore_original_casing(c.missing_ops)

        matches.append(
            RoleMatch(
                role_id=c.ctx.role_id,
                role_name=c.ctx.role_name,
                description=c.ctx.description,
                matched_operations=sorted(matched_ops_display),
                missing_operations=sorted(missing_ops_display),
                total_permissions=c.perms.control_count + c.perms.data_count,
                control_plane_permissions=c.perms.control_count,
                data_plane_permissions=c.perms.data_count,
                is_high_privilege=c.ctx.role_name in HIGH_PRIVILEGE_ROLES,
                match_percentage=c.match_pct,
                has_conditions=c.ctx.has_conditions,
                matched_operations_count=c.matched_count,
                requested_operations_count=total_requested,
                missing_operations_expanded=sorted(expanded.operations),
                missing_operations_count=expanded.total,
                has_partial_wildcard_match=bool(c.ctx.wildcard_partial_coverage)
                or bool(c.missing_ops),
            )
        )

    # Log evaluation summary
    full_match_count = sum(1 for m in matches if m.is_full_match)
    partial_match_count = len(matches) - full_match_count
    logger.debug(
        "Evaluated %d roles: %d with any match (full=%d, partial=%d)",
        roles_evaluated,
        roles_with_matches,
        full_match_count,
        partial_match_count,
    )

    # Log cache stats summary
    stats = svc.get_cache_stats()
    logger.debug(
        "Cache stats: role_coverage=%d, pattern_match=%d, partial_coverage=%d, wildcard_count=%d",
        stats.role_coverage,
        stats.pattern_match,
        stats.partial_coverage,
        stats.wildcard_count,
    )

    return matches


def _sort_and_filter_candidates(
    candidates: list[_RoleCandidate], max_results: int | None
) -> list[_RoleCandidate]:
    """Sort and filter candidates by least privilege before expensive expansion."""
    full = [c for c in candidates if c.is_full_match]
    partial = [c for c in candidates if not c.is_full_match]

    if full:
        # Return only full matches, sorted by least privilege
        full.sort(
            key=lambda c: (
                c.ctx.role_name in HIGH_PRIVILEGE_ROLES,
                c.perms.control_count + c.perms.data_count,
            )
        )
        return full[:max_results] if max_results else full

    # No full matches - return best partial matches
    partial.sort(
        key=lambda c: (
            -c.match_pct,
            c.ctx.role_name in HIGH_PRIVILEGE_ROLES,
            c.perms.control_count + c.perms.data_count,
        )
    )
    # Limit partial matches to 10 unless explicitly requested more
    limit = max_results if max_results else 10
    return partial[:limit]
