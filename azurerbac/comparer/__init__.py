"""Role comparison: three-way permission splits (only-A, shared, only-B)."""

from azurerbac.comparer.compare import build_comparison, compute_role_comparison
from azurerbac.comparer.models import RoleComparison

__all__ = [
    "RoleComparison",
    "build_comparison",
    "compute_role_comparison",
]
