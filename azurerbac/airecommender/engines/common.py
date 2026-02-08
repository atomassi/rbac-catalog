"""Common utilities for recommendation engines."""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import ArrayLike

from azurerbac.airecommender.engines.config import (
    SCORE_CEILING,
    SCORE_FLOOR,
    SigmoidParams,
)

if TYPE_CHECKING:
    from azurerbac.airecommender.engines.base import RankedRole

logger = logging.getLogger(__name__)

_SCORE_RANGE = SCORE_CEILING - SCORE_FLOOR


def normalize_candidates(
    candidates: list[RankedRole],
    floor: float = SCORE_FLOOR,
    ceiling: float = SCORE_CEILING,
) -> list[RankedRole]:
    """Min-max normalize candidate scores to [floor, ceiling]. Mutates in-place."""
    if not candidates:
        return candidates

    scores = [c.final_score for c in candidates]
    min_score = min(scores)
    max_score = max(scores)
    score_range = max_score - min_score if max_score > min_score else 1.0
    output_range = ceiling - floor

    for c in candidates:
        normalized = (c.final_score - min_score) / score_range if score_range > 0 else 1.0
        c.final_score = floor + (normalized * output_range)

    logger.debug(
        "Normalized %d scores (min-max): [%s]",
        len(candidates),
        ", ".join(f"{c.role_name}({c.final_score:.0%})" for c in candidates[:3]),
    )
    return candidates


def sigmoid_normalize(
    candidates: list[RankedRole],
    params: SigmoidParams,
) -> list[RankedRole]:
    """Sigmoid-transform candidate scores. Mutates in-place."""
    if not candidates:
        return candidates

    output_range = params.output_max - params.output_min
    for candidate in candidates:
        raw = candidate.final_score
        sigmoid = 1 / (1 + math.exp(-params.steepness * (raw - params.midpoint)))
        candidate.final_score = params.output_min + (sigmoid * output_range)

    return candidates


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Cosine similarity between two vectors."""
    if len(vec1) != len(vec2):
        raise ValueError("Vectors must have the same length")

    dot_product = math.sumprod(vec1, vec2)
    norm1 = math.sqrt(math.sumprod(vec1, vec1))
    norm2 = math.sqrt(math.sumprod(vec2, vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot_product / (norm1 * norm2)


def top_k_similar(
    query: ArrayLike,
    embeddings: dict[str, list[float]],
    k: int,
) -> list[tuple[str, float]]:
    """Top-k most similar embeddings using numpy (O(n) via argpartition)."""
    if not embeddings:
        return []

    # Convert query to numpy array and normalize
    query_vec = np.asarray(query, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []
    query_vec = query_vec / query_norm

    # Stack embeddings into matrix
    doc_ids = list(embeddings.keys())
    embedding_matrix = np.array([embeddings[doc_id] for doc_id in doc_ids], dtype=np.float32)

    # Normalize rows
    norms = np.linalg.norm(embedding_matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    embedding_matrix = embedding_matrix / norms

    # Compute all similarities
    similarities = embedding_matrix @ query_vec

    # Get top-k indices efficiently (O(n) average via argpartition)
    k = min(k, len(doc_ids))
    if k == len(doc_ids):
        top_indices = np.argsort(similarities)[::-1]
    else:
        # argpartition is O(n), then we only sort the k elements
        partition_idx = np.argpartition(similarities, -k)[-k:]
        top_indices = partition_idx[np.argsort(similarities[partition_idx])[::-1]]

    return [(doc_ids[i], float(similarities[i])) for i in top_indices]
