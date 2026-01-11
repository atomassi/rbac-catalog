"""Role matching module for Azure RBAC role recommendations.

This module provides pattern-based exact matching for Azure RBAC roles.
The main entry point is `recommend_roles()` which finds roles granting
specific operations, sorted by least privilege.

Main components:
- recommend_roles: Find roles granting specific operations (main entry point)
- RoleMatch: Result object with detailed match information
- RoleRecommendationService: Service class with focused matching methods
- ClassifiedOperations: Value object for classified operation sets
- OperationSets: Value object for pre-computed operation sets
"""

from azurerbac.matching.models import (
    ClassifiedOperations,
    OperationSets,
    RoleMatch,
    WildcardCoverage,
    WildcardCoverageResult,
)
from azurerbac.matching.recommendation_service import (
    RoleEvaluationContext,
    RoleRecommendationService,
)
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
    "OperationSets",
    "RoleEvaluationContext",
    "RoleMatch",
    "RoleRecommendationService",
    "WildcardCoverage",
    "WildcardCoverageResult",
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
