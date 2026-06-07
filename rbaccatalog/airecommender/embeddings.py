"""Embeddings for semantic similarity search."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

import numpy as np

from rbaccatalog.settings import Settings, is_running_in_pytest

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_MODELS_DIR: Final[Path] = Path(__file__).parent / "models"
_MODEL_PATH: Final[Path] = _MODELS_DIR / "all-MiniLM-L6-v2"
_CACHE_PATH: Final[Path] = _MODELS_DIR / "role_embeddings.json"
_MODEL_NAME: Final[str] = "all-MiniLM-L6-v2"
_MODEL_NOT_LOADED = "Embedding model not loaded"


class EmbeddingModel:
    """Sentence embedding model for semantic similarity search."""

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._embeddings: dict[str, list[float]] = {}
        self._matrix: np.ndarray | None = None
        self._doc_ids: list[str] = []
        # Per-instance cache bound to the method so ``self`` stays out of the
        # cache key and instances stay garbage-collectable (avoids B019).
        self.encode_single_cached: Callable[[str], tuple[float, ...]] = lru_cache(maxsize=512)(
            self._encode_single_tuple
        )

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def embeddings(self) -> dict[str, list[float]]:
        return self._embeddings

    def try_load(self) -> bool:
        """Load the sentence transformer model. Returns False if skipped or failed."""
        if self._should_skip_in_tests():
            return False
        try:
            return self._load_model()
        except ImportError:
            logger.warning("sentence-transformers not installed; embedding model unavailable.")
        except Exception as e:
            logger.exception("Failed to load embedding model: %s", e)
        return False

    def _should_skip_in_tests(self) -> bool:
        settings = Settings.get()
        if is_running_in_pytest() and not settings.enable_embeddings_in_tests:
            logger.info(
                "Skipping embedding model load under pytest. "
                "Set AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS=1 to enable."
            )
            return True
        return False

    def _load_model(self) -> bool:
        from sentence_transformers import SentenceTransformer

        if _MODEL_PATH.exists():
            self._model = SentenceTransformer(str(_MODEL_PATH))
        else:
            logger.info("Downloading MiniLM embedding model (first time only)...")
            self._model = SentenceTransformer(_MODEL_NAME)
            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            self._model.save(str(_MODEL_PATH))

        logger.info("Loaded sentence embedding model (MiniLM)")
        return True

    def _require_model(self) -> SentenceTransformer:
        if not self._model:
            raise RuntimeError(_MODEL_NOT_LOADED)
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode multiple texts into embedding vectors."""
        embeddings = self._require_model().encode(texts, show_progress_bar=False)
        return [emb.tolist() for emb in embeddings]

    def encode_single(self, text: str) -> list[float]:
        """Encode a single text into an embedding vector."""
        return self._require_model().encode(text, show_progress_bar=False).tolist()

    def _encode_single_tuple(self, text: str) -> tuple[float, ...]:
        """Encode a single text, returning a tuple for cache hashability."""
        return tuple(self.encode_single(text))

    def build_embeddings(self, documents: dict[str, str], cache_hash: str | None = None) -> None:
        """Build embeddings for all documents. Uses cache if hash matches."""
        model = self._require_model()

        if cache_hash and self._load_cache(cache_hash):
            logger.info("Loaded embeddings from cache")
            self._build_matrix()
            return

        logger.info("Computing embeddings for %d documents...", len(documents))

        doc_ids = list(documents.keys())
        texts = list(documents.values())
        embeddings = model.encode(texts, show_progress_bar=False)

        self._embeddings = {
            doc_id: emb.tolist() for doc_id, emb in zip(doc_ids, embeddings, strict=True)
        }
        self._build_matrix()

        if cache_hash:
            self._save_cache(cache_hash)

        logger.info("Computed and cached %d embeddings", len(self._embeddings))

    def _load_cache(self, expected_hash: str) -> bool:
        if not _CACHE_PATH.exists():
            return False
        try:
            cache = json.loads(_CACHE_PATH.read_text())
            if cache.get("hash") != expected_hash:
                logger.info("Embeddings cache outdated, recomputing...")
                return False
            self._embeddings = cache.get("embeddings", {})
            return bool(self._embeddings)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load embeddings cache: %s", e)
            return False

    def _save_cache(self, cache_hash: str) -> None:
        try:
            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(json.dumps({"hash": cache_hash, "embeddings": self._embeddings}))
        except OSError as e:
            logger.warning("Failed to save embeddings cache: %s", e)

    def _build_matrix(self) -> None:
        """Build normalized numpy matrix for fast vectorized cosine similarity."""
        if not self._embeddings:
            return

        self._doc_ids = list(self._embeddings.keys())
        matrix = np.array([self._embeddings[d] for d in self._doc_ids], dtype=np.float32)

        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        self._matrix = matrix / norms

        logger.debug("Built embedding matrix: %s", self._matrix.shape)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """Search for similar documents by query text."""
        self._require_model()
        return self.search_vector(list(self.encode_single_cached(query)), top_k)

    def search_vector(self, query_embedding: list[float], top_k: int) -> list[tuple[str, float]]:
        """Search for similar documents using a pre-computed query vector."""
        if self._matrix is not None:
            return self._search_matrix(query_embedding, top_k)

        from rbaccatalog.airecommender.engines.common import top_k_similar

        return top_k_similar(query_embedding, self._embeddings, top_k)

    def _search_matrix(self, query_embedding: list[float], top_k: int) -> list[tuple[str, float]]:
        """Vectorized similarity search. O(n) via argpartition."""
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm > 0:
            query_vec = query_vec / query_norm

        similarities = self._matrix @ query_vec  # type: ignore[operator]
        top_indices = self._top_k_indices(similarities, top_k)
        return [(self._doc_ids[i], float(similarities[i])) for i in top_indices]

    @staticmethod
    def _top_k_indices(scores: np.ndarray, k: int) -> np.ndarray:
        """Get indices of top-k scores efficiently."""
        n = len(scores)
        if k >= n:
            return np.argsort(scores)[::-1]
        partition_idx = np.argpartition(scores, -k)[-k:]
        return partition_idx[np.argsort(scores[partition_idx])[::-1]]


def compute_documents_hash(documents: dict[str, str]) -> str:
    """Compute hash of document texts for cache validation."""
    from rbaccatalog.core.utils import content_hash

    return content_hash(json.dumps(sorted(documents.items())))
