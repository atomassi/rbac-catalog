"""Role comparison functions.

Computes three-way splits (only-A, shared, only-B) between two roles'
effective operations.  Lives in the comparer layer so that both cache
seeding and web routes can call it without violating architectural
boundaries.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from azurerbac.comparer.models import RoleComparison, RoleComparisonSide

if TYPE_CHECKING:
    from azurerbac.cache import CacheService
    from azurerbac.cache.models import CachedRole

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_cache(cache: CacheService | None) -> CacheService:
    """Get cache service."""
    if cache is not None:
        return cache
    from azurerbac.cache import get_cache_service

    return get_cache_service()


def _extract_abac_conditions(role: CachedRole) -> list[str]:
    """Extract sorted unique ABAC condition expressions from a role."""
    return sorted({p.condition for p in role.definition.properties.permissions if p.condition})


def _extract_assignable_scopes(role: CachedRole) -> list[str]:
    """Extract sorted, de-duplicated assignable scopes from a role."""
    scopes = role.definition.properties.assignable_scopes
    return sorted(set(scopes)) if scopes else ["/"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_role_comparison(
    role_a_id: str,
    role_b_id: str,
    cache: CacheService | None = None,
) -> RoleComparison | None:
    """Compare effective operations between two roles.

    Uses precomputed role coverage sets to compute three-way split:
    operations only in A, shared, and operations only in B.

    Args:
        role_a_id: The first role's ID.
        role_b_id: The second role's ID.
        cache: Optional cache service override (for testing).

    Returns:
        RoleComparison with the three-way operation split, or None if
        either role is not found.
    """
    cache_resolved = _get_cache(cache)

    # Reject comparing a role with itself
    if role_a_id == role_b_id:
        return None

    # Cache key preserves argument order so role_a/role_b stay correct
    cache_key = f"{role_a_id}:{role_b_id}"
    if (cached := cache_resolved.get_comparison(cache_key)) is not None:
        return cached

    role_a = cache_resolved.get_role_by_id(role_a_id)
    role_b = cache_resolved.get_role_by_id(role_b_id)
    if not role_a or not role_b:
        return None

    cov_a = cache_resolved.get_role_coverage(role_a_id)
    cov_b = cache_resolved.get_role_coverage(role_b_id)

    ctrl_a = cov_a.control if cov_a else set()
    ctrl_b = cov_b.control if cov_b else set()
    data_a = cov_a.data if cov_a else set()
    data_b = cov_b.data if cov_b else set()

    # Restore original casing from the lowered coverage sets
    restore = cache_resolved.restore_operation_casing

    result = RoleComparison(
        role_a=RoleComparisonSide(
            role_id=role_a_id,
            role_name=role_a.definition.properties.role_name,
            description=role_a.definition.properties.description or "",
            control_count=len(ctrl_a),
            data_count=len(data_a),
            conditions=_extract_abac_conditions(role_a),
            assignable_scopes=_extract_assignable_scopes(role_a),
        ),
        role_b=RoleComparisonSide(
            role_id=role_b_id,
            role_name=role_b.definition.properties.role_name,
            description=role_b.definition.properties.description or "",
            control_count=len(ctrl_b),
            data_count=len(data_b),
            conditions=_extract_abac_conditions(role_b),
            assignable_scopes=_extract_assignable_scopes(role_b),
        ),
        only_a_control=sorted(restore(ctrl_a - ctrl_b)),
        only_a_data=sorted(restore(data_a - data_b)),
        shared_control=sorted(restore(ctrl_a & ctrl_b)),
        shared_data=sorted(restore(data_a & data_b)),
        only_b_control=sorted(restore(ctrl_b - ctrl_a)),
        only_b_data=sorted(restore(data_b - data_a)),
    )
    cache_resolved.set_comparison(cache_key, result)
    return result
