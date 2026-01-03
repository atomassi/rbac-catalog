"""Recommender mode enumeration for AI role recommendations."""

from enum import StrEnum
from typing import Final, Self

# Module-level constant for mode descriptions (avoid recreating dict per call)
_MODE_DESCRIPTIONS: Final[dict[str, str]] = {
    "tfidf": "Enhanced TF-IDF + BM25 (fast, no LLM)",
    "llm": "Qwen-Curated LLM (~1.2s response)",
    "rag": "RAG: Semantic search + LLM re-ranking",
    "hybrid": "Hybrid: TF-IDF → Embeddings → LLM",
    "semantic": "Semantic Search: Pure embedding similarity",
    "crossencoder": "Cross-Encoder: Neural reranking (~300ms)",
    "colbert": "ColBERT: Token-level late interaction (~150ms)",
    "hyde": "HyDE: Hypothetical document embeddings",
}


class RecommenderMode(StrEnum):
    """Available recommendation engine modes.

    Each mode offers different trade-offs between speed, accuracy, and resource usage.
    """

    TFIDF = "tfidf"
    """Enhanced TF-IDF + BM25 - Fast lexical search, no LLM required (~50ms)."""

    LLM = "llm"
    """Qwen-Curated LLM - Direct LLM inference, 90% accuracy (~1.2s)."""

    RAG = "rag"
    """RAG (Retrieval-Augmented Generation) - Embedding retrieval + LLM re-ranking (~1.5s)."""

    HYBRID = "hybrid"
    """Hybrid multi-stage - TF-IDF → Embedding → LLM pipeline (~2s)."""

    SEMANTIC = "semantic"
    """Semantic Search - Pure embedding similarity, no LLM (~100ms)."""

    CROSSENCODER = "crossencoder"
    """Cross-Encoder Reranking - Bi-encoder retrieval + cross-encoder reranking (~300ms)."""

    COLBERT = "colbert"
    """ColBERT - Token-level late interaction for precise matching (~150ms)."""

    HYDE = "hyde"
    """HyDE - Hypothetical document generation + semantic search (~1.5s)."""

    @classmethod
    def from_string(cls, value: str | None) -> Self | None:
        """Convert string to RecommenderMode, returns None if invalid."""
        if value is None:
            return None
        try:
            return cls(value.lower())
        except ValueError:
            return None

    @classmethod
    def is_valid(cls, value: str | None) -> bool:
        """Check if a string is a valid recommender mode."""
        return cls.from_string(value) is not None

    @property
    def requires_llm(self) -> bool:
        """Whether this mode requires an LLM connection."""
        return self in (
            RecommenderMode.LLM,
            RecommenderMode.RAG,
            RecommenderMode.HYBRID,
            RecommenderMode.HYDE,
        )

    @property
    def requires_embeddings(self) -> bool:
        """Whether this mode requires embedding model."""
        return self in (
            RecommenderMode.RAG,
            RecommenderMode.HYBRID,
            RecommenderMode.SEMANTIC,
            RecommenderMode.CROSSENCODER,
            RecommenderMode.HYDE,
        )

    @property
    def description(self) -> str:
        """Human-readable description of the mode."""
        return _MODE_DESCRIPTIONS.get(self.value, self.value)
