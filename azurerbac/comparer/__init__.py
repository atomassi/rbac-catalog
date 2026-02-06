"""Role comparison package.

Provides comparison logic for Azure RBAC built-in roles, computing
three-way permission splits (only-A, shared, only-B).
"""

from azurerbac.comparer.compare import compute_role_comparison
from azurerbac.comparer.models import RoleComparison, RoleComparisonSide

__all__ = [
    "RoleComparison",
    "RoleComparisonSide",
    "compute_role_comparison",
]
