"""RAG (Retrieval-Augmented Generation) recommendation engine."""

from __future__ import annotations

import heapq
import logging
from typing import Final, override

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.common import ScoreNormalizer
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)

_DEFAULT_RETRIEVAL_K: Final = 20


@EngineRegistry.register(RecommenderMode.RAG)
class RAGEngine(BaseRecommenderEngine):
    """Semantic search + LLM re-ranking engine."""

    @property
    @override
    def name(self) -> str:
        return "RAG: Semantic Search + LLM"

    @property
    @override
    def requires_llm(self) -> bool:
        return True

    @property
    @override
    def requires_embeddings(self) -> bool:
        return True

    @override
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
        retrieval_k: int | None = None,
    ) -> list[RankedRole]:
        retrieval_k = retrieval_k or _DEFAULT_RETRIEVAL_K
        self._log_start(query, top_k)

        query_embedding = self._encode_cached(query)
        if query_embedding is None:
            return []

        # Retrieve candidates by embedding similarity
        candidates = self.retrieve_by_embedding(query, query_embedding, retrieval_k, exclude_owner)
        if not candidates:
            return []

        logger.debug("RAG: Retrieved %d candidates", len(candidates))

        # LLM re-ranking
        if self.is_llm_available:
            candidates = self._llm_rerank(query, candidates, top_k)
        else:
            candidates = heapq.nlargest(top_k, candidates, key=lambda r: r.embedding_score)
            for c in candidates:
                c.final_score = c.embedding_score

        candidates = ScoreNormalizer.normalize_candidates(candidates)
        self._log_complete(candidates)
        return candidates
