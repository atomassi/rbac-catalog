"""Cross-Encoder reranking engine.

Uses bi-encoder for fast initial retrieval, then cross-encoder for
accurate reranking. Cross-encoders see query+document together,
achieving 10-15% higher accuracy than bi-encoders alone.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Final, override

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.common import normalize_candidates
from azurerbac.airecommender.engines.config import CROSSENCODER_THRESHOLDS, CROSSENCODER_WEIGHTS
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.core.singleton import ThreadSafeSingleton

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = logging.getLogger(__name__)

_CROSS_ENCODER_MODEL: Final = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_RETRIEVAL_K: Final = 50


@EngineRegistry.register(RecommenderMode.CROSSENCODER)
class CrossEncoderEngine(BaseRecommenderEngine):
    """Bi-encoder retrieval + cross-encoder reranking."""

    @property
    @override
    def name(self) -> str:
        return "Cross-Encoder Reranking"

    @property
    @override
    def requires_llm(self) -> bool:
        return False

    @property
    @override
    def requires_embeddings(self) -> bool:
        return True

    @override
    def is_available(self) -> bool:
        if not super().is_available():
            return False
        return is_cross_encoder_available()

    @override
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]:
        self._log_start(query, top_k)

        # Retrieve candidates with bi-encoder
        query_embedding = self._encode_cached(query)
        if query_embedding is None:
            return []

        candidates = self.retrieve_by_embedding(query, query_embedding, _RETRIEVAL_K, exclude_owner)
        if not candidates:
            return []

        logger.debug("CrossEncoder: Retrieved %d bi-encoder candidates", len(candidates))

        # Rerank with cross-encoder
        cross_encoder = get_cross_encoder()
        candidates = self._rerank_with_cross_encoder(query, candidates, cross_encoder)

        # Filter and normalize
        candidates = self._filter_min_confidence(
            candidates, threshold=CROSSENCODER_THRESHOLDS.min_confidence
        )
        candidates = normalize_candidates(candidates[:top_k])

        self._log_complete(candidates, show_top=6)
        return candidates

    def _rerank_with_cross_encoder(
        self,
        query: str,
        candidates: list[RankedRole],
        cross_encoder: CrossEncoder,
    ) -> list[RankedRole]:
        pairs = []
        for candidate in candidates:
            # Use full document_text (includes curated patterns, role name, description, keywords)
            # This matches what the bi-encoder embeddings were trained on
            doc = self.get_role_document(candidate.role_id)
            if doc and "document_text" in doc:
                doc_text = doc["document_text"]
            else:
                # Fallback to role name + description
                doc_text = f"{candidate.role_name}: {candidate.description}"
            pairs.append([query, doc_text])

        # Get cross-encoder scores
        try:
            ce_scores = cross_encoder.predict(pairs)

            # Log top candidates with their scores
            logger.debug(
                "CrossEncoder reranking: top 6 bi-encoder candidates: %s",
                [(c.role_name, f"{c.embedding_score:.3f}") for c in candidates[:6]],
            )

            # Update candidates with cross-encoder scores
            for i, candidate in enumerate(candidates):
                # Cross-encoder scores can be negative, normalize to 0-1
                raw_score = float(ce_scores[i])
                # Sigmoid-like normalization for cross-encoder scores
                normalized = 1 / (1 + math.exp(-raw_score))
                candidate.llm_score = normalized  # Reuse llm_score field for CE score
                # Combine bi-encoder and cross-encoder scores
                # Weight cross-encoder higher as it's more accurate
                candidate.final_score = (
                    CROSSENCODER_WEIGHTS.secondary * candidate.embedding_score
                    + CROSSENCODER_WEIGHTS.primary * normalized
                )

            # Sort by final score
            candidates.sort(key=lambda c: c.final_score, reverse=True)

            # Log reranked results
            top6 = [
                (c.role_name, f"ce={c.llm_score:.3f}", f"final={c.final_score:.3f}")
                for c in candidates[:6]
            ]
            logger.debug("CrossEncoder reranking: top 6 after rerank: %s", top6)

            return candidates

        except Exception as e:
            logger.exception("Cross-encoder reranking failed: %s", e)
            # Fall back to bi-encoder scores
            return candidates


def _load_cross_encoder() -> CrossEncoder:
    from sentence_transformers import CrossEncoder

    logger.debug("Loading cross-encoder model: %s", _CROSS_ENCODER_MODEL)
    model = CrossEncoder(_CROSS_ENCODER_MODEL)
    logger.debug("Cross-encoder model loaded successfully")
    return model


_cross_encoder: ThreadSafeSingleton[CrossEncoder] = ThreadSafeSingleton(factory=_load_cross_encoder)


def get_cross_encoder() -> CrossEncoder:
    """Get the cross-encoder model singleton."""
    return _cross_encoder.get()


def is_cross_encoder_available() -> bool:
    """Check if cross-encoder is available without raising."""
    if _cross_encoder.is_initialized:
        return True
    try:
        get_cross_encoder()
        return True
    except Exception:
        return False
