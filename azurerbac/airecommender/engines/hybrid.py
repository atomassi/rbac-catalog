"""Hybrid multi-stage recommendation engine.

Combines TF-IDF, embedding, and LLM stages in a progressive pipeline.
Each stage filters and refines candidates for maximum accuracy.
"""

from __future__ import annotations

import heapq
import logging
from typing import Final, override

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.common import cosine_similarity, normalize_candidates
from azurerbac.airecommender.engines.config import HYBRID_WEIGHTS
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.knowledge import extract_keywords
from azurerbac.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)

_DEFAULT_TFIDF_K: Final = 100
_DEFAULT_EMBEDDING_K: Final = 20


@EngineRegistry.register(RecommenderMode.HYBRID)
class HybridEngine(BaseRecommenderEngine):
    """Multi-stage hybrid ranking: TF-IDF → Embedding → LLM."""

    @property
    @override
    def name(self) -> str:
        return "Hybrid: TF-IDF → Embeddings → LLM"

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
        tfidf_k: int | None = None,
        embedding_k: int | None = None,
    ) -> list[RankedRole]:
        tfidf_k = tfidf_k or _DEFAULT_TFIDF_K
        embedding_k = embedding_k or _DEFAULT_EMBEDDING_K

        self._log_start(query, top_k)

        # Stage 1: TF-IDF
        tfidf_candidates = self._stage_tfidf(query, tfidf_k, exclude_owner)
        if not tfidf_candidates:
            return []

        logger.debug("Hybrid: %d TF-IDF candidates", len(tfidf_candidates))

        # Stage 2: Embedding re-rank
        if self.is_embeddings_available:
            embedding_candidates = self._stage_embedding(query, tfidf_candidates, embedding_k)
        else:
            embedding_candidates = tfidf_candidates[:embedding_k]
            for c in embedding_candidates:
                c.embedding_score = c.tfidf_score

        # Stage 3: LLM final ranking
        if self.is_llm_available:
            final = self._stage_llm(query, embedding_candidates, top_k)
            final.sort(key=lambda r: r.final_score, reverse=True)
        else:
            for c in embedding_candidates:
                c.final_score = (c.tfidf_score + c.embedding_score) / 2
            final = heapq.nlargest(top_k, embedding_candidates, key=lambda r: r.final_score)

        final = normalize_candidates(final)

        self._log_complete(final)
        return final

    def _stage_tfidf(
        self,
        query: str,
        top_k: int,
        exclude_owner: bool,
    ) -> list[RankedRole]:
        if not self.tfidf_recommender:
            logger.warning("Hybrid Stage 1: No TF-IDF recommender available")
            return []

        results = self.tfidf_recommender.recommend(query, top_k=top_k)
        keywords = extract_keywords(query)
        candidates: list[RankedRole] = []

        # EnhancedTFIDFRecommender returns (role_id, role_name, score, metadata)
        for role_id, role_name, score, _metadata in results:
            if self.should_exclude_role(role_name, exclude_owner):
                continue

            if not role_id:
                continue

            doc = self.get_role_document(role_id)
            if not doc:
                continue
            candidates.append(
                RankedRole(
                    role_id=role_id,
                    role_name=role_name,
                    description=doc.get("description", ""),
                    tfidf_score=score,
                    final_score=score,
                    matched_keywords=keywords,
                )
            )

        return candidates

    def _stage_embedding(
        self,
        query: str,
        candidates: list[RankedRole],
        top_k: int,
    ) -> list[RankedRole]:
        if not self.embedding_model or not self.embedding_model.is_loaded:
            return candidates[:top_k]

        query_embedding = self.embedding_model.encode_single_cached(query)
        embeddings = self.embedding_model.embeddings

        for c in candidates:
            if c.role_id in embeddings:
                c.embedding_score = cosine_similarity(
                    list(query_embedding),
                    embeddings[c.role_id],  # type: ignore[arg-type]
                )
            else:
                c.embedding_score = c.tfidf_score * 0.5  # Penalize if no embedding

        # Combine scores: TF-IDF weighted higher (curated patterns are more reliable)
        for c in candidates:
            c.final_score = (
                HYBRID_WEIGHTS.primary * c.tfidf_score
                + HYBRID_WEIGHTS.secondary * c.embedding_score
            )

        candidates.sort(key=lambda r: r.final_score, reverse=True)
        return candidates[:top_k]

    def _stage_llm(
        self,
        query: str,
        candidates: list[RankedRole],
        top_k: int,
    ) -> list[RankedRole]:
        if not self.is_llm_available:
            return candidates[:top_k]

        return self._llm_rerank(query, candidates, top_k)
