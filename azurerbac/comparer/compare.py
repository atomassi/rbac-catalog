"""Role comparison functions.

Computes three-way splits (only-A, shared, only-B) between two roles'
effective operations.  Lives in the comparer layer so that both cache
seeding and web routes can call it without violating architectural
boundaries.
"""

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from azurerbac.comparer.models import RoleComparison, RoleComparisonSide

if TYPE_CHECKING:
    from azurerbac.cache.models import CachedRole
    from azurerbac.cache.service import CacheService
    from azurerbac.matching.models import RoleCoverage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_abac_conditions(role: "CachedRole") -> list[str]:
    """Extract sorted unique ABAC condition expressions from a role."""
    return sorted({p.condition for p in role.definition.properties.permissions if p.condition})


def _extract_assignable_scopes(role: "CachedRole") -> list[str]:
    """Extract sorted, de-duplicated assignable scopes from a role."""
    scopes = role.definition.properties.assignable_scopes
    return sorted(set(scopes)) if scopes else ["/"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_comparison(
    role_a_id: str,
    role_b_id: str,
    role_a: "CachedRole",
    role_b: "CachedRole",
    cov_a: "RoleCoverage | None",
    cov_b: "RoleCoverage | None",
    ops_casing: Mapping[str, str],
) -> RoleComparison:
    """Pure computation of a three-way permission diff between two roles.

    Args:
        role_a_id: ID of the first role.
        role_b_id: ID of the second role.
        role_a: Cached data for the first role.
        role_b: Cached data for the second role.
        cov_a: Coverage (lowered operation sets) for role A, or None.
        cov_b: Coverage (lowered operation sets) for role B, or None.
        ops_casing: Mapping from lowered operation name to original casing.

    Returns:
        A RoleComparison with only-A, shared, and only-B operation sets.
    """
    ctrl_a = cov_a.control if cov_a else set()
    ctrl_b = cov_b.control if cov_b else set()
    data_a = cov_a.data if cov_a else set()
    data_b = cov_b.data if cov_b else set()

    def restore(ops: set[str]) -> list[str]:
        return [ops_casing.get(op, op) for op in ops]

    return RoleComparison(
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


def compute_role_comparison(
    role_a_id: str,
    role_b_id: str,
    cache: "CacheService | None" = None,
) -> RoleComparison | None:
    """Compare two roles with caching. Thin wrapper around build_comparison."""
    if role_a_id == role_b_id:
        return None

    if cache is None:
        from azurerbac.cache import get_cache_service

        cache = get_cache_service()

    cache_key = f"{role_a_id}:{role_b_id}"
    if (cached := cache.get_comparison(cache_key)) is not None:
        return cached

    role_a = cache.get_role_by_id(role_a_id)
    role_b = cache.get_role_by_id(role_b_id)
    if not role_a or not role_b:
        return None

    result = build_comparison(
        role_a_id,
        role_b_id,
        role_a,
        role_b,
        cache.get_role_coverage(role_a_id),
        cache.get_role_coverage(role_b_id),
        cache.cache.ops_lowered_to_orig,
    )
    cache.set_comparison(cache_key, result)
    return result
