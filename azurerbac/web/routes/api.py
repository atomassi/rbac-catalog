"""API route handlers for the Azure RBAC Catalog."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends, Query

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
from azurerbac.web.constants import (
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    MAX_TOP_K,
    MIN_AI_QUERY_CHARS,
    MIN_SEARCH_CHARS,
)
from azurerbac.web.dependencies import APIDeps, get_api_deps
from azurerbac.web.routes.models import (
    AIRecommendResponse,
    CountMatchesResponse,
    OperationSearchResponse,
    RecommendRolesRequest,
    RecommendRolesResponse,
)
from azurerbac.web.routes.responses import (
    ErrorMessages,
    ai_error_response,
    empty_search_response,
)
from azurerbac.web.utils import clamp

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])

# Type aliases for FastAPI query parameters
type SearchQuery = Annotated[str, Query(max_length=MAX_QUERY_LENGTH)]
type SearchLimit = Annotated[int, Query(ge=1, le=MAX_SEARCH_LIMIT)]


@router.get("/operations/search", response_model=OperationSearchResponse)
async def api_search_operations(
    deps: Annotated[APIDeps, Depends(get_api_deps)],
    q: SearchQuery = "",
    limit: SearchLimit = DEFAULT_SEARCH_LIMIT,
) -> OperationSearchResponse:
    """Search operations by name or description.

    Supports wildcards:
    - * matches any characters (e.g., Microsoft.Authorization/*/read)

    Returns a list of operations matching the query.
    """
    # Early return for insufficient query length
    if len(q) < MIN_SEARCH_CHARS:
        return empty_search_response(ErrorMessages.SEARCH_TOO_SHORT)

    # Ensure operations are loaded (builds index if needed)
    await deps.get_all_operations()

    # Perform indexed search
    is_wildcard = is_wildcard_pattern(q)
    matching = deps.app_cache.search_operations(q, limit=limit, is_wildcard=is_wildcard)

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
    deps: Annotated[APIDeps, Depends(get_api_deps)],
    pattern: SearchQuery,
    is_data_action: bool = False,
) -> CountMatchesResponse:
    """Count how many operations match a wildcard pattern.

    Used to show "(matches N operations)" when user selects a wildcard pattern.
    """
    # Non-wildcard patterns always return zero matches
    if not pattern or not is_wildcard_pattern(pattern):
        return CountMatchesResponse(pattern=pattern, count=0, is_data_action=is_data_action)

    # Ensure operations are loaded
    await deps.get_all_operations()

    # Fast indexed count
    count = deps.app_cache.count_wildcard_matches(pattern, is_data_action)

    return CountMatchesResponse(pattern=pattern, count=count, is_data_action=is_data_action)


@router.post("/recommend-roles", response_model=RecommendRolesResponse)
async def api_recommend_roles(
    request: RecommendRolesRequest,
    deps: Annotated[APIDeps, Depends(get_api_deps)],
) -> RecommendRolesResponse:
    """Recommend roles based on selected operations.

    Expects JSON body: {"operations": [{"name": "op1", "is_data_action": false}, ...]}
    Returns roles that grant all requested operations, sorted by least privilege.
    """
    requested_ops, data_flags = request.parse_operations()

    # Get data from cache (preloaded at startup)
    role_jsons, all_operations = await asyncio.gather(
        deps.get_all_role_jsons(),
        deps.get_operations_for_recommender(),
    )

    # Run CPU-bound recommendation in thread pool to avoid blocking event loop
    loop = asyncio.get_running_loop()
    matches = await loop.run_in_executor(
        None,
        partial(
            recommend_roles,
            requested_ops,
            role_jsons,
            all_operations,
            requested_ops_data_flags=data_flags,
        ),
    )

    # Get expanded count from first match, or fall back to raw count
    requested_ops_count = matches[0].requested_operations_count if matches else len(requested_ops)

    logger.info(
        "Role recommendation: ops_requested=%d ops_expanded=%d matches=%d",
        len(requested_ops),
        requested_ops_count,
        len(matches),
    )

    return RecommendRolesResponse(
        requested_operations=requested_ops,
        requested_operations_count=requested_ops_count,
        total_matches=len(matches),
        roles=[m.to_dict() for m in matches],
    )


@router.post("/ai-recommend", response_model=AIRecommendResponse)
async def ai_recommend_endpoint(
    request: AIRecommendRequest,
    deps: Annotated[APIDeps, Depends(get_api_deps)],
) -> AIRecommendResponse:
    """AI-powered role recommendations based on natural language query.

    This endpoint uses semantic search to find roles matching a natural language
    description like "I need to read storage blobs" or "manage virtual machines".

    Engines (in order of speed/accuracy tradeoff):
    - TFIDF: Fast lexical search (~50ms)
    - Semantic: Pure embedding similarity (~100ms)
    - ColBERT: Token-level late interaction (~150ms)
    - CrossEncoder: Bi-encoder + reranking (~300ms)
    - LLM: Fine-tuned Qwen model (~1.2s)
    - RAG/Hybrid/HyDE: Multi-stage pipelines (~1.5-2s)
    """
    # ─── Input Validation ─────────────────────────────────────────────────────
    query = (request.query or "").strip()[:MAX_QUERY_LENGTH]

    if not query:
        return ai_error_response(ErrorMessages.QUERY_EMPTY)
    if len(query) < MIN_AI_QUERY_CHARS:
        return ai_error_response(ErrorMessages.QUERY_TOO_SHORT)

    # ─── Request Normalization ────────────────────────────────────────────────
    requested_mode = (
        request.recommender_mode
        if RecommenderMode.is_valid(request.recommender_mode)
        else RecommenderMode.LLM.value
    )
    top_k = clamp(request.top_k, 1, MAX_TOP_K)
    role_jsons = await deps.get_all_role_jsons()

    # ─── Execute AI Recommendation ────────────────────────────────────────────
    try:
        recommendations, actual_mode = await _execute_ai_recommendation(
            query=query,
            role_jsons=role_jsons,
            top_k=top_k,
            requested_mode=requested_mode,
        )
    except EngineNotAvailableError as e:
        logger.warning("Engine not available: mode=%s missing=%s", e.mode, e.missing_components)
        return ai_error_response(
            ErrorMessages.engine_unavailable((e.mode or "Selected").upper()),
            mode=e.mode,
        )
    except ColBERTInitializationError:
        logger.warning("ColBERT initialization failed")
        return ai_error_response(ErrorMessages.engine_unavailable("COLBERT"), mode="colbert")
    except Exception:
        logger.exception("AI recommendation failed")
        return ai_error_response(ErrorMessages.GENERIC_ERROR)

    # ─── Success Response ─────────────────────────────────────────────────────
    logger.info(
        "AI recommendation: query='%s' requested=%s actual=%s results=%d",
        query[:50],
        requested_mode,
        actual_mode,
        len(recommendations),
    )

    return AIRecommendResponse(
        query=query,
        recommendations=recommendations,
        total=len(recommendations),
        engine={"mode": actual_mode, "fallback": actual_mode != requested_mode},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Private Helpers (Single Responsibility)
# ─────────────────────────────────────────────────────────────────────────────
async def _execute_ai_recommendation(
    query: str,
    role_jsons: list[dict],
    top_k: int,
    requested_mode: str,
) -> tuple[list, str]:
    """Execute AI recommendation in thread pool for CPU-bound engines.

    Returns:
        Tuple of (recommendations_list, actual_mode_used)

    Raises:
        EngineNotAvailableError: If the requested engine is not available
        ColBERTInitializationError: If ColBERT fails to initialize
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(
            ai_recommend_roles,
            query=query,
            roles=role_jsons,
            top_k=top_k,
            requested_mode=requested_mode,
        ),
    )
