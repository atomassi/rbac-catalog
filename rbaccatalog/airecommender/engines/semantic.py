"""Semantic search recommendation engine."""

from __future__ import annotations

import logging
from typing import override

from rbaccatalog.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from rbaccatalog.airecommender.engines.config import SEMANTIC_THRESHOLDS
from rbaccatalog.airecommender.engines.registry import EngineRegistry
from rbaccatalog.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)


@EngineRegistry.register(RecommenderMode.SEMANTIC)
class SemanticEngine(BaseRecommenderEngine):
    """Pure embedding similarity search."""

    @property
    @override
    def name(self) -> str:
        return "Semantic Search: Pure embedding similarity"

    @property
    @override
    def requires_llm(self) -> bool:
        return False

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
    ) -> list[RankedRole]:
        self._log_start(query, top_k)

        query_embedding = self._encode_cached(query)
        if query_embedding is None:
            logger.warning("Semantic: Failed to encode query")
            return []

        # Retrieve top-K similar roles
        candidates = self.retrieve_by_embedding(
            query,
            query_embedding,
            top_k,
            exclude_owner,
            use_search_vector=True,
        )

        logger.debug("Semantic: Retrieved %d candidates", len(candidates))
        candidates = self._finalize_results(
            candidates, threshold=SEMANTIC_THRESHOLDS.min_confidence
        )

        self._log_complete(candidates)
        return candidates
