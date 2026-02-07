"""Role matching for Azure RBAC recommendations."""

from azurerbac.matching.models import (
    ClassifiedOperations,
    CoverageResult,
    OperationSets,
    Plane,
    PlaneContext,
    RoleEvaluationContext,
    RoleMatch,
    WildcardKey,
)
from azurerbac.matching.recommendation_service import RoleRecommendationService
from azurerbac.matching.role_matching import (
    check_operation_allowed,
    check_wildcard_operation_allowed,
    count_net_permissions,
    count_operations_matching_pattern,
    count_wildcard_partial_coverage,
    get_matching_operations,
    has_any_wildcard_coverage,
    pattern_covers_pattern,
)
from azurerbac.matching.role_recommender import recommend_roles

__all__ = [
    "ClassifiedOperations",
    "CoverageResult",
    "OperationSets",
    "Plane",
    "PlaneContext",
    "RoleEvaluationContext",
    "RoleMatch",
    "RoleRecommendationService",
    "WildcardKey",
    "check_operation_allowed",
    "check_wildcard_operation_allowed",
    "count_net_permissions",
    "count_operations_matching_pattern",
    "count_wildcard_partial_coverage",
    "get_matching_operations",
    "has_any_wildcard_coverage",
    "pattern_covers_pattern",
    "recommend_roles",
]
