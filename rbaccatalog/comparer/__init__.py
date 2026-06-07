"""Role comparison: three-way permission splits (only-A, shared, only-B)."""

from rbaccatalog.comparer.compare import build_comparison, compute_role_comparison
from rbaccatalog.comparer.models import RoleComparison

__all__ = [
    "RoleComparison",
    "build_comparison",
    "compute_role_comparison",
]
