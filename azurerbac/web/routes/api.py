"""API route handlers."""

from __future__ import annotations

import logging
from functools import partial
from typing import Annotated

from anyio import to_thread
from fastapi import APIRouter, Depends, Query, Request

from azurerbac.airecommender import (
    AIRecommendRequest,
    EngineNotAvailableError,
    ai_recommend_roles,
)
from azurerbac.airecommender.engines import ColBERTInitializationError
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.matching import recommend_roles
from azurerbac.telemetry import track_ai_recommendation, track_role_recommendation
from azurerbac.web.constants import (
    AI_RATE_LIMIT_PER_MINUTE,
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    MIN_AI_QUERY_CHARS,
    MIN_SEARCH_CHARS,
)
from azurerbac.web.dependencies import BaseDeps, get_api_deps
from azurerbac.web.limiter import limiter
from azurerbac.web.routes.models import (
    AIEngineInfo,
    AIRecommendationItem,
    AIRecommendResponse,
    CountMatchesResponse,
    OperationSearchResponse,
    RecommendRolesRequest,
    RecommendRolesResponse,
    RoleMatchResponse,
)
from azurerbac.web.routes.responses import (
    ErrorMessages,
    ai_error_response,
    empty_search_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# Type aliases for FastAPI query parameters
type SearchQuery = Annotated[str, Query(max_length=MAX_QUERY_LENGTH)]
type SearchLimit = Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)]


@router.get("/operations/search", response_model=OperationSearchResponse)
async def api_search_operations(
    deps: Annotated[BaseDeps, Depends(get_api_deps)],
    q: SearchQuery = "",
    limit: SearchLimit = DEFAULT_SEARCH_LIMIT,
) -> OperationSearchResponse:
    """Search operations by name with optional wildcards."""
    # Early return for insufficient query length
    if len(q) < MIN_SEARCH_CHARS:
        return empty_search_response(ErrorMessages.SEARCH_TOO_SHORT)

    # Perform indexed search
    matching = deps.app_cache.search_operations(q, limit=limit)
    is_wildcard = is_wildcard_pattern(q)

    logger.info(
        "Operation search: query='%s' wildcard=%s results=%d",
        q,
        is_wildcard,
        len(matching),
    )

    return OperationSearchResponse(
        operations=matching,
        total=len(matching),
        is_wildcard_search=is_wildcard,
    )


@router.get("/operations/count-matches", response_model=CountMatchesResponse)
async def api_count_wildcard_matches(
    deps: Annotated[BaseDeps, Depends(get_api_deps)],
    pattern: SearchQuery,
    is_data_action: bool = False,
) -> CountMatchesResponse:
    """Count operations matching a wildcard pattern."""
    # Non-wildcard patterns always return zero matches
    if not pattern or not is_wildcard_pattern(pattern):
        return CountMatchesResponse(pattern=pattern, count=0, is_data_action=is_data_action)

    # Fast indexed count
    count = deps.app_cache.count_wildcard_matches(pattern, is_data_action)

    return CountMatchesResponse(pattern=pattern, count=count, is_data_action=is_data_action)


@router.post("/recommend-roles", response_model=RecommendRolesResponse)
async def api_recommend_roles(
    request: RecommendRolesRequest,
    deps: Annotated[BaseDeps, Depends(get_api_deps)],
) -> RecommendRolesResponse:
    """Recommend roles based on selected operations."""
    requested_ops, data_flags = request.parse_operations()

    # Run CPU-bound recommendation in thread pool to avoid blocking event loop
    matches = await to_thread.run_sync(
        partial(
            recommend_roles,
            requested_ops,
            requested_ops_data_flags=data_flags,
        )
    )

    # Get expanded count from first match, or fall back to raw count
    requested_ops_count = matches[0].requested_operations_count if matches else len(requested_ops)

    logger.info(
        "Role recommendation: ops_requested=%d ops_expanded=%d matches=%d",
        len(requested_ops),
        requested_ops_count,
        len(matches),
    )

    track_role_recommendation(
        operations_count=len(requested_ops),
        expanded_count=requested_ops_count,
        result_count=len(matches),
    )

    return RecommendRolesResponse(
        requested_operations=requested_ops,
        requested_operations_count=requested_ops_count,
        total_matches=len(matches),
        roles=[RoleMatchResponse.model_validate(m.to_dict()) for m in matches],
    )


@router.post("/ai-recommend", response_model=AIRecommendResponse)
@limiter.limit(f"{AI_RATE_LIMIT_PER_MINUTE}/minute")
async def ai_recommend_endpoint(
    request: Request,
    body: AIRecommendRequest,
    deps: Annotated[BaseDeps, Depends(get_api_deps)],
) -> AIRecommendResponse:
    """AI-powered role recommendations from natural language query."""
    query = body.query.strip()

    if len(query) < MIN_AI_QUERY_CHARS:
        return ai_error_response(ErrorMessages.QUERY_TOO_SHORT)

    requested_mode = (
        body.recommender_mode
        if RecommenderMode.is_valid(body.recommender_mode)
        else RecommenderMode.LLM.value
    )
    roles = deps.app_cache.get_all_roles()

    try:
        recommendations, actual_mode = await to_thread.run_sync(
            partial(
                ai_recommend_roles,
                query=query,
                roles=roles,
                top_k=body.top_k,
                requested_mode=requested_mode,
            )
        )
    except EngineNotAvailableError as e:
        logger.warning("Engine not available: mode=%s missing=%s", e.mode, e.missing_components)
        track_ai_recommendation(mode=requested_mode, result_count=0, is_error=True)
        return ai_error_response(
            ErrorMessages.engine_unavailable((e.mode or "Selected").upper()),
            mode=e.mode,
        )
    except ColBERTInitializationError:
        logger.warning("ColBERT initialization failed")
        track_ai_recommendation(mode=requested_mode, result_count=0, is_error=True)
        return ai_error_response(ErrorMessages.engine_unavailable("COLBERT"), mode="colbert")
    except Exception:
        logger.exception("AI recommendation failed")
        track_ai_recommendation(mode=requested_mode, result_count=0, is_error=True)
        return ai_error_response(ErrorMessages.GENERIC_ERROR)

    logger.info(
        "AI recommendation: query='%s' requested=%s actual=%s results=%d",
        query[:50],
        requested_mode,
        actual_mode,
        len(recommendations),
    )

    track_ai_recommendation(
        mode=actual_mode,
        result_count=len(recommendations),
    )

    return AIRecommendResponse(
        query=query,
        recommendations=[AIRecommendationItem.model_validate(r) for r in recommendations],
        total=len(recommendations),
        engine=AIEngineInfo(mode=actual_mode, fallback=actual_mode != requested_mode),
    )
