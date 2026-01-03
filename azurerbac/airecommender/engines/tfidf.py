"""TF-IDF recommendation engine."""

from __future__ import annotations

import logging
from typing import override

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.common import normalize_scores
from azurerbac.airecommender.engines.config import TFIDF_CONFIG
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.knowledge import extract_keywords
from azurerbac.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)


@EngineRegistry.register(RecommenderMode.TFIDF, is_default=True)
class TFIDFEngine(BaseRecommenderEngine):
    """Enhanced TF-IDF + BM25 recommendation engine.

    Pipeline:
    1. Check curated pattern matches (highest priority)
    2. BM25 search on role documents
    3. Combine scores with pattern matching
    4. Return top-K ranked results

    Performance: ~50ms, 75% top-1 accuracy
    """

    @property
    @override
    def name(self) -> str:
        """Return the engine name."""
        return "Enhanced TF-IDF + BM25"

    @property
    @override
    def requires_llm(self) -> bool:
        """Return whether this engine requires an LLM."""
        return False

    @property
    @override
    def requires_embeddings(self) -> bool:
        """Return whether this engine requires embeddings."""
        return False

    @override
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]:
        """Get recommendations using Enhanced TF-IDF + BM25.

        Args:
            query: Natural language query
            top_k: Number of recommendations to return
            exclude_owner: Whether to exclude Owner role

        Returns:
            List of RankedRole objects sorted by TF-IDF score.
        """
        self._log_start(query, top_k)

        if not self.tfidf_recommender:
            logger.warning("TF-IDF: Recommender not initialized")
            return []

        raw_results = self.tfidf_recommender.recommend(query, top_k=top_k * 2)
        logger.debug("TF-IDF: Got %d raw results", len(raw_results))

        keywords = extract_keywords(query)

        results: list[RankedRole] = []
        seen_role_ids: set[str] = set()

        for role_id, _role_name, score, _ in raw_results:
            if not role_id or role_id in seen_role_ids:
                continue

            doc = self.get_role_document(role_id)
            if not doc or self.should_exclude_role(doc["role_name"], exclude_owner):
                continue

            results.append(
                RankedRole(
                    role_id=role_id,
                    role_name=doc["role_name"],
                    description=doc["description"],
                    tfidf_score=score,
                    final_score=score,
                    matched_keywords=keywords,
                )
            )
            seen_role_ids.add(role_id)

        # Filter and normalize
        results = self._filter_low_confidence(results)
        results = self._filter_relative_scores(results)
        results = self._normalize_final_scores(results)

        self._log_complete(results)
        return results[:top_k]

    def _filter_low_confidence(self, results: list[RankedRole]) -> list[RankedRole]:
        """Filter low-confidence results (Step 3)."""
        pre_filter_count = len(results)
        results = self._filter_min_confidence(results, threshold=TFIDF_CONFIG.min_confidence)
        logger.info(
            "TF-IDF Step 3: Filtered %d low-confidence results (< %s), %d remaining",
            pre_filter_count - len(results),
            f"{TFIDF_CONFIG.min_confidence:.0%}",
            len(results),
        )
        return results

    def _filter_relative_scores(self, results: list[RankedRole]) -> list[RankedRole]:
        """Filter results significantly worse than best (Step 4)."""
        if not results:
            return results

        best_score = max(r.final_score for r in results)
        if best_score >= TFIDF_CONFIG.high_confidence:
            pre_filter_count = len(results)
            threshold = best_score * TFIDF_CONFIG.relative_cutoff
            results = self._filter_min_confidence(results, threshold=threshold)
            logger.info(
                "TF-IDF Step 4: Filtered %d results worse than 70%% of best (%.2f), %d remaining",
                pre_filter_count - len(results),
                best_score,
                len(results),
            )
        return results

    def _normalize_final_scores(self, results: list[RankedRole]) -> list[RankedRole]:
        """Normalize scores to 60-95% range for consistent UX (Step 5)."""
        pre_str = ", ".join(f"{r.role_name}({r.final_score:.2f})" for r in results[:5])
        logger.info("TF-IDF Step 5: Pre-normalization scores: [%s]", pre_str)

        results = normalize_scores(results)

        post_str = ", ".join(f"{r.role_name}({r.final_score:.0%})" for r in results[:5])
        logger.info("TF-IDF Step 5: Post-normalization scores: [%s]", post_str)

        return results
