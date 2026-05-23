"""LLM recommendation engine using fine-tuned Qwen model."""

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
    """LLM-based engine using fine-tuned Qwen model with JSON output."""

    @property
    @override
    def name(self) -> str:
        model = self.ollama_client.model if self.ollama_client else "unknown"
        return f"{model} LLM"

    @property
    @override
    def requires_llm(self) -> bool:
        return True

    @property
    @override
    def requires_embeddings(self) -> bool:
        return False

    def _ensure_role_names_initialized(self) -> None:
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
