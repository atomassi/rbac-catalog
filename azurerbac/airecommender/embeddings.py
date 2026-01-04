"""Embeddings module for semantic similarity search.

This module provides sentence embedding functionality using MiniLM
for semantic search in role recommendations. Embeddings are vector
representations that enable finding semantically similar content.

Note: Embeddings are NOT LLMs - they are vector representation models
for similarity computation, not generative text models.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np

from azurerbac.settings import Settings, is_running_in_pytest

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# Model and cache paths
MODELS_DIR: Final[Path] = Path(__file__).parent / "models"
MINILM_MODEL_PATH: Final[Path] = MODELS_DIR / "all-MiniLM-L6-v2"
EMBEDDINGS_CACHE_PATH: Final[Path] = MODELS_DIR / "role_embeddings.json"


class EmbeddingModel:
    """Sentence embedding model for semantic similarity.

    Uses MiniLM (all-MiniLM-L6-v2) for fast, accurate semantic embeddings.
    Supports caching embeddings to disk for faster startup.
    """

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._embeddings: dict[str, list[float]] = {}
        self._loaded = False
        # Optimizations for fast similarity search
        self._embedding_matrix: np.ndarray | None = None  # (n_docs, dim) matrix
        self._embedding_ids: list[str] = []  # doc_id for each row

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._loaded

    @property
    def embeddings(self) -> dict[str, list[float]]:
        """Get the stored embeddings."""
        return self._embeddings

    def try_load(self) -> bool:
        """Try to load the sentence transformer model. Returns True on success."""
        settings = Settings.get()

        if settings.disable_embeddings:
            logger.info("Embeddings disabled via AZURERBAC_DISABLE_EMBEDDINGS=1")
            return False

        # Unit tests should not pay the cost of importing torch/transformers
        # unless explicitly opted-in via environment variable.
        if is_running_in_pytest() and not settings.enable_embeddings_in_tests:
            logger.info(
                "Skipping embedding model load under pytest. "
                "Set AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS=1 to enable."
            )
            return False

        try:
            from sentence_transformers import SentenceTransformer

            # Try to load from local path first, then download
            if MINILM_MODEL_PATH.exists():
                self._model = SentenceTransformer(str(MINILM_MODEL_PATH))
            else:
                # Download and cache the model
                logger.info("Downloading MiniLM embedding model (first time only)...")
                self._model = SentenceTransformer("all-MiniLM-L6-v2")
                # Save for future use
                MODELS_DIR.mkdir(parents=True, exist_ok=True)
                self._model.save(str(MINILM_MODEL_PATH))

            self._loaded = True
            logger.info("Loaded sentence embedding model (MiniLM)")
            return True
        except ImportError:
            logger.warning("sentence-transformers not installed. Using TF-IDF fallback.")
            return False
        except Exception as e:
            logger.exception("Failed to load embedding model: %s. Using TF-IDF fallback.", e)
            return False

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts into embedding vectors."""
        if not self._model:
            raise RuntimeError("Embedding model not loaded")
        embeddings = self._model.encode(texts, show_progress_bar=False)
        return [emb.tolist() for emb in embeddings]

    def encode_single(self, text: str) -> list[float]:
        """Encode a single text into an embedding vector."""
        if not self._model:
            raise RuntimeError("Embedding model not loaded")
        return self._model.encode(text, show_progress_bar=False).tolist()

    def build_embeddings(
        self,
        documents: dict[str, str],
        cache_hash: str | None = None,
    ) -> None:
        """Build embeddings for all documents. Uses cache if hash matches."""
        if not self._model:
            raise RuntimeError("Embedding model not loaded")

        # Check cache
        if cache_hash and self._load_cache(cache_hash):
            logger.info("Loaded embeddings from cache")
            self._build_matrix()  # Build matrix for fast search
            return

        logger.info("Computing embeddings for %d documents...", len(documents))

        doc_ids = list(documents.keys())
        texts = list(documents.values())

        # Compute embeddings in batch
        embeddings = self._model.encode(texts, show_progress_bar=False)

        # Store embeddings
        self._embeddings = {
            doc_id: embedding.tolist()
            for doc_id, embedding in zip(doc_ids, embeddings, strict=True)
        }

        # Build numpy matrix for fast vectorized search
        self._build_matrix()

        # Save cache
        if cache_hash:
            self._save_cache(cache_hash)

        logger.info("Computed and cached %d embeddings", len(self._embeddings))

    def _load_cache(self, expected_hash: str) -> bool:
        """Load embeddings from cache if valid."""
        if not EMBEDDINGS_CACHE_PATH.exists():
            return False

        try:
            with open(EMBEDDINGS_CACHE_PATH) as f:
                cache = json.load(f)

            if cache.get("hash") != expected_hash:
                logger.info("Embeddings cache outdated, recomputing...")
                return False

            self._embeddings = cache.get("embeddings", {})
            return len(self._embeddings) > 0
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load embeddings cache: %s", e)
            return False

    def _save_cache(self, cache_hash: str) -> None:
        """Save embeddings to cache."""
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            with open(EMBEDDINGS_CACHE_PATH, "w") as f:
                json.dump(
                    {
                        "hash": cache_hash,
                        "embeddings": self._embeddings,
                    },
                    f,
                )
        except OSError as e:
            logger.warning("Failed to save embeddings cache: %s", e)

    @lru_cache(maxsize=512)  # noqa: B019 - singleton pattern, no memory leak risk
    def encode_single_cached(self, text: str) -> tuple[float, ...]:
        """Encode a single text with LRU caching. Returns tuple for hashability."""
        return tuple(self.encode_single(text))

    def _build_matrix(self) -> None:
        """Build numpy matrix from embeddings for vectorized operations."""
        if not self._embeddings:
            return

        self._embedding_ids = list(self._embeddings.keys())
        embeddings_list = [self._embeddings[doc_id] for doc_id in self._embedding_ids]
        self._embedding_matrix = np.array(embeddings_list, dtype=np.float32)

        # Normalize rows for fast cosine similarity (just dot product after normalization)
        norms = np.linalg.norm(self._embedding_matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)  # Avoid division by zero
        self._embedding_matrix = self._embedding_matrix / norms

        logger.debug("Built embedding matrix: %s", self._embedding_matrix.shape)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search for similar documents using cosine similarity."""
        if not self._model:
            raise RuntimeError("Embedding model not loaded")

        # Use cached query embedding
        query_embedding = self.encode_single_cached(query)
        return self.search_vector(list(query_embedding), top_k)

    def search_vector(self, query_embedding: list[float], top_k: int) -> list[tuple[str, float]]:
        """Search for similar documents using a pre-computed query vector."""
        # Use vectorized search if matrix is built
        if self._embedding_matrix is not None:
            return self._search_vectorized(query_embedding, top_k)

        # Fallback to vectorized batch similarity from common
        from azurerbac.airecommender.engines.common import top_k_similar

        return top_k_similar(query_embedding, self._embeddings, top_k)

    def _search_vectorized(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[str, float]]:
        """Vectorized similarity search using numpy.

        5-10x faster than loop-based search for 800+ documents.
        """
        # Normalize query embedding
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm > 0:
            query_vec = query_vec / query_norm

        # Compute all similarities in one matrix multiplication
        similarities = self._embedding_matrix @ query_vec  # type: ignore[operator]

        # Get top-k indices efficiently
        n_docs = len(similarities)
        if top_k >= n_docs:
            top_indices = np.argsort(similarities)[::-1]
        else:
            # argpartition is O(n)
            partition_idx = np.argpartition(similarities, -top_k)[-top_k:]
            # Sort the top k
            top_indices = partition_idx[np.argsort(similarities[partition_idx])[::-1]]

        # Return (doc_id, score) tuples
        return [(self._embedding_ids[i], float(similarities[i])) for i in top_indices]


def compute_documents_hash(documents: dict[str, str]) -> str:
    """Compute hash of document texts for cache validation."""
    from azurerbac.core.utils import content_hash

    # Sort by key and hash both keys and values
    sorted_items = sorted(documents.items())
    doc_str = json.dumps(sorted_items)
    return content_hash(doc_str)
