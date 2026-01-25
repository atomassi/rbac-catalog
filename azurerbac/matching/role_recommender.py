"""Role Recommender - Suggests least-privilege roles based on selected operations.

This module provides the main entry point for role recommendations.
The heavy lifting is delegated to RoleRecommendationService.
"""

from __future__ import annotations

import logging

from azurerbac.azure.models import RoleDefinition
from azurerbac.core import HIGH_PRIVILEGE_ROLES
from azurerbac.matching.models import RoleMatch
from azurerbac.matching.recommendation_service import (
    RoleEvaluationContext,
    RoleRecommendationService,
)

logger = logging.getLogger(__name__)


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

    # Evaluate each role
    matches: list[RoleMatch] = []
    has_cache = svc.has_full_cache()
    roles_evaluated = roles_with_matches = 0

    for role in roles:
        if not svc.is_builtin_role(role):
            continue

        roles_evaluated += 1
        role_info = svc.extract_role_info(role)
        cached_coverage = svc.get_cached_coverage(role_info.role_id)

        # Create evaluation context
        ctx = RoleEvaluationContext(
            role_id=role_info.role_id,
            role_name=role_info.role_name,
            description=role_info.description,
            permissions=role_info.permissions,
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

        roles_with_matches += 1

        # Calculate missing operations
        missing_ops = svc.calculate_missing_ops(ctx, classified)

        # Calculate statistics
        matched_count = svc.calculate_matched_ops_count(ctx, cached_coverage)
        perms = svc.calculate_permissions_count(ctx)
        expanded = svc.expand_missing_operations(ctx, missing_ops, classified)

        # Calculate match percentage
        match_pct = (matched_count / total_requested * 100) if total_requested > 0 else 0.0

        # Restore original casing for display
        matched_ops_display = svc.restore_original_casing(ctx.matched_ops)
        missing_ops_display = svc.restore_original_casing(missing_ops)

        # Build result
        matches.append(
            RoleMatch(
                role_id=role_info.role_id,
                role_name=role_info.role_name,
                description=role_info.description,
                matched_operations=sorted(matched_ops_display),
                missing_operations=sorted(missing_ops_display),
                total_permissions=perms.control_count + perms.data_count,
                control_plane_permissions=perms.control_count,
                data_plane_permissions=perms.data_count,
                is_high_privilege=role_info.role_name in HIGH_PRIVILEGE_ROLES,
                match_percentage=match_pct,
                has_conditions=ctx.has_conditions,
                matched_operations_count=matched_count,
                requested_operations_count=total_requested,
                missing_operations_expanded=sorted(expanded.operations),
                missing_operations_count=expanded.total,
                has_partial_wildcard_match=bool(ctx.wildcard_partial_coverage) or bool(missing_ops),
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

    return _sort_and_filter_results(matches, max_results)


def _sort_and_filter_results(matches: list[RoleMatch], max_results: int | None) -> list[RoleMatch]:
    """Sort and filter role matches by least privilege."""
    full_matches = [m for m in matches if m.is_full_match]
    partial_matches = [m for m in matches if not m.is_full_match]

    if full_matches:
        # Return only full matches, sorted by least privilege
        full_matches.sort(
            key=lambda m: (
                m.is_high_privilege,
                m.total_permissions,
            )
        )
        return full_matches[:max_results] if max_results else full_matches

    # No full matches - return best partial matches
    partial_matches.sort(
        key=lambda m: (
            -m.match_percentage,
            m.is_high_privilege,
            m.total_permissions,
        )
    )
    # Limit partial matches to 10 unless explicitly requested more
    limit = max_results if max_results else 10
    return partial_matches[:limit]
