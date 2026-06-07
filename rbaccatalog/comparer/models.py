"""Role comparison models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoleComparisonSide:
    """One side of a role comparison."""

    role_id: str
    role_name: str
    description: str
    control_count: int
    data_count: int
    conditions: list[str]  # ABAC condition expressions
    assignable_scopes: list[str]  # Assignable scopes (e.g. ["/"])


@dataclass(frozen=True, slots=True)
class RoleComparison:
    """Result of comparing two roles' effective operations."""

    role_a: RoleComparisonSide
    role_b: RoleComparisonSide
    only_a_control: list[str]  # Control plane ops only in role A
    only_a_data: list[str]  # Data plane ops only in role A
    shared_control: list[str]  # Control plane ops in both
    shared_data: list[str]  # Data plane ops in both
    only_b_control: list[str]  # Control plane ops only in role B
    only_b_data: list[str]  # Data plane ops only in role B
