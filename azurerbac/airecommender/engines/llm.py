"""LLM recommendation engine."""

from __future__ import annotations

import logging
from typing import Final, override

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.config import LLM_THRESHOLDS
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.exceptions import OllamaClientNotAvailableError
from azurerbac.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)

# Maximum results to return (LLM returns high-confidence, focused results)
_MAX_LLM_RESULTS: Final[int] = 3


@EngineRegistry.register(RecommenderMode.LLM)
class LLMEngine(BaseRecommenderEngine):
    """LLM-based recommendation engine using fine-tuned Qwen model.

    The engine queries a fine-tuned Ollama model that outputs structured JSON
    with role predictions. Results are validated against the knowledge base
    to filter hallucinated roles.
    """

    @property
    @override
    def name(self) -> str:
        """Return engine name including the model identifier."""
        model = self.ollama_client.model if self.ollama_client else "unknown"
        return f"{model} LLM"

    @property
    @override
    def requires_llm(self) -> bool:
        """LLM engine requires an active Ollama connection."""
        return True

    @property
    @override
    def requires_embeddings(self) -> bool:
        """LLM engine operates without embeddings."""
        return False

    def _ensure_role_names_initialized(self) -> None:
        """Lazily initialize role names for fuzzy matching if needed.

        Role names may be missing after hot-reload. This method ensures
        the Ollama client has the full role name list for post-processing.
        """
        if self.ollama_client is None:
            raise OllamaClientNotAvailableError("LLM")

        if self.ollama_client.has_role_names:
            return

        role_names = self.knowledge_base.get_all_role_names()
        if role_names:
            logger.info("LLM: Initializing %d role names for fuzzy matching", len(role_names))
            self.ollama_client.set_known_role_names(role_names)
        else:
            logger.warning("LLM: No role names available for fuzzy matching")

    def _query_llm(self, query: str, top_k: int) -> list[tuple[str, float, str, list[str]]]:
        """Query the Ollama LLM for role recommendations.

        Args:
            query: Natural language query
            top_k: Maximum results to request

        Returns:
            List of (role_name, score, explanation, signals_matched) tuples,
            or empty list on failure.
        """
        if self.ollama_client is None:
            raise OllamaClientNotAvailableError("LLM")
        logger.debug("LLM: Querying Ollama %s model", self.ollama_client.model)
        try:
            return self.ollama_client.recommend_roles(query, top_k)
        except Exception:
            logger.exception("LLM: Ollama query failed")
            return []

    def _map_to_ranked_role(
        self,
        role_name: str,
        score: float,
        signals_matched: list[str],
        exclude_owner: bool,
    ) -> RankedRole | None:
        """Map an LLM prediction to a validated RankedRole."""
        # Validate role exists (no fuzzy matching - causes semantic drift)
        role_id = self.knowledge_base.find_role_id_by_name(role_name)
        if not role_id:
            logger.debug("LLM: Role '%s' not found (hallucinated)", role_name)
            return None

        doc = self.get_role_document(role_id)
        if not doc:
            return None

        if self.should_exclude_role(doc["role_name"], exclude_owner):
            logger.debug("LLM: Excluding '%s' (least privilege)", role_name)
            return None

        return RankedRole(
            role_id=role_id,
            role_name=doc["role_name"],
            description=doc["description"],
            llm_score=score,
            final_score=score,
            matched_keywords=signals_matched or [],
        )

    @override
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]:
        """Get recommendations using fine-tuned LLM.

        The qwen-rbac model returns structured JSON with:
        - role: The exact Azure built-in role name
        - confidence_class: Converted to percentage (very_high=95%, high=80%, etc.)
        - signals_matched: Keywords from query that match the role

        Args:
            query: Natural language query
            top_k: Number of recommendations to return (capped at _MAX_LLM_RESULTS)
            exclude_owner: Whether to exclude Owner role

        Returns:
            List of RankedRole objects sorted by LLM confidence
        """
        self._log_start(query, top_k)

        if not self.is_llm_available:
            logger.warning("LLM: Ollama client not available")
            return []

        self._ensure_role_names_initialized()

        # Query LLM
        ollama_results = self._query_llm(query, top_k)
        if not ollama_results:
            logger.debug("LLM: No results from Ollama")
            return []

        logger.debug("LLM: Got %d results from Ollama", len(ollama_results))

        # Map LLM predictions to validated RankedRole objects
        effective_limit = min(top_k, _MAX_LLM_RESULTS)
        results: list[RankedRole] = []

        for role_name, score, _explanation, signals_matched in ollama_results:
            if ranked_role := self._map_to_ranked_role(
                role_name, score, signals_matched, exclude_owner
            ):
                results.append(ranked_role)
                if len(results) >= effective_limit:
                    break

        results = self._filter_min_confidence(results, threshold=LLM_THRESHOLDS.min_confidence)

        self._log_complete(results)
        return results
