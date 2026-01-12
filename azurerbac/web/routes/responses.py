"""API response factories."""

from __future__ import annotations

from enum import StrEnum

from azurerbac.web.constants import MIN_AI_QUERY_CHARS, MIN_SEARCH_CHARS
from azurerbac.web.routes.models import (
    AIEngineInfo,
    AIRecommendResponse,
    OperationSearchResponse,
)

__all__ = [
    "MIN_AI_QUERY_CHARS",
    "MIN_SEARCH_CHARS",
    "ErrorMessages",
    "ai_error_response",
    "empty_search_response",
]


class ErrorMessages(StrEnum):
    """API error messages."""

    SEARCH_TOO_SHORT = f"Please enter at least {MIN_SEARCH_CHARS} characters to search"
    QUERY_EMPTY = "Query cannot be empty"
    QUERY_TOO_SHORT = "Query too short. Please describe what you need."
    GENERIC_ERROR = "An error occurred while processing your request. Please try again."

    @staticmethod
    def engine_unavailable(engine: str) -> str:
        """Format engine unavailable message."""
        return f"{engine} engine is unavailable. Try a different engine."


def empty_search_response(message: str) -> OperationSearchResponse:
    """Create empty search response."""
    return OperationSearchResponse(
        operations=[],
        total=0,
        is_wildcard_search=False,
        message=message,
    )


def ai_error_response(
    error: str,
    mode: str | None = None,
    available: bool = False,
) -> AIRecommendResponse:
    """Create AI error response."""
    engine = AIEngineInfo(mode=mode, available=available) if mode else None
    return AIRecommendResponse(error=error, recommendations=[], engine=engine)
