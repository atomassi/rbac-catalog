"""API request and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field

from azurerbac.azure.models import OperationData


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


class HealthResponse(BaseModel):
    """Health check response."""

    ok: bool


class VersionResponse(BaseModel):
    """Version response."""

    version: str


class OperationItem(BaseModel):
    """Operation in recommendation request."""

    name: str
    is_data_action: bool = False


class OperationWithCount(BaseModel):
    """Operation data with role count."""

    name: str
    display_name: str | None
    description: str | None
    is_data_action: bool
    provider_display_name: str
    resource_type: str | None
    resource_type_display_name: str | None
    role_count: int

    @classmethod
    def from_operation(cls, op: OperationData, role_count: int) -> OperationWithCount:
        """Create from OperationData and role count."""
        return cls(
            name=op.name,
            display_name=op.display_name,
            description=op.description,
            is_data_action=op.is_data_action,
            provider_display_name=op.provider_display_name,
            resource_type=op.resource_type,
            resource_type_display_name=op.resource_type_display_name,
            role_count=role_count,
        )


class RecommendRolesRequest(BaseModel):
    """Role recommendation request."""

    operations: list[OperationItem] = Field(..., min_length=1, max_length=100)

    def parse_operations(self) -> tuple[list[str], dict[str, bool] | None]:
        """Parse operations into deduplicated names and data-plane flags."""
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

    operations: list[OperationData]
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


class AIRecommendationItem(BaseModel):
    """A single AI role recommendation result."""

    role_id: str
    role_name: str
    description: str
    score: float
    matched_keywords: list[str] = Field(default_factory=list)


class AIRecommendResponse(BaseModel):
    """Response model for AI recommend endpoint."""

    query: str | None = None
    recommendations: list[AIRecommendationItem] = Field(default_factory=list)
    total: int = 0
    engine: AIEngineInfo | None = None
    error: str | None = None
