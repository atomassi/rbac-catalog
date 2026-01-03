"""Pydantic models for API request and response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OperationItem(BaseModel):
    """Model for an operation in the recommendation request."""

    name: str
    is_data_action: bool = False


class RecommendRolesRequest(BaseModel):
    """Request model for role recommendation."""

    operations: list[OperationItem] = Field(..., min_length=1)

    def parse_operations(self) -> tuple[list[str], dict[str, bool] | None]:
        """Parse operations into deduplicated names and data-plane flags.

        When the same operation appears with both data planes, we omit the flag
        entirely to let auto-detection handle it.

        Returns:
            Tuple of (unique_operation_names, data_flags_dict_or_none)
        """
        seen_ops: list[str] = []
        data_flags: dict[str, bool] = {}
        conflicted: set[str] = set()

        for op in self.operations:
            if not op.name:
                continue

            if op.name not in seen_ops:
                seen_ops.append(op.name)

            if op.name in conflicted:
                continue

            if op.name in data_flags and data_flags[op.name] != op.is_data_action:
                del data_flags[op.name]
                conflicted.add(op.name)
            else:
                data_flags[op.name] = op.is_data_action

        return seen_ops, data_flags or None


class OperationSearchResponse(BaseModel):
    """Response model for operation search."""

    operations: list
    total: int
    is_wildcard_search: bool
    message: str | None = None


class CountMatchesResponse(BaseModel):
    """Response model for wildcard count."""

    pattern: str
    count: int
    is_data_action: bool


class RoleMatchResponse(BaseModel):
    """Response model for a single role match."""

    role_id: str
    role_name: str
    matched_operations: list[str]
    missing_operations: list[str]
    missing_operations_expanded: list[str]
    missing_operations_count: int
    total_permissions: int
    control_plane_permissions: int
    data_plane_permissions: int
    is_high_privilege: bool
    has_conditions: bool
    has_partial_wildcard_match: bool
    match_percentage: float
    is_full_match: bool
    matched_operations_count: int
    requested_operations_count: int


class RecommendRolesResponse(BaseModel):
    """Response model for role recommendation results."""

    requested_operations: list[str]
    requested_operations_count: int
    total_matches: int
    roles: list[RoleMatchResponse]


class AIEngineInfo(BaseModel):
    """Metadata about the AI recommender engine used."""

    mode: str
    fallback: bool | None = None
    available: bool | None = None
    missing_components: list[str] | None = None


class AIRecommendResponse(BaseModel):
    """Response model for AI recommend endpoint."""

    query: str | None = None
    recommendations: list = Field(default_factory=list)
    total: int = 0
    engine: AIEngineInfo | None = None
    error: str | None = None
