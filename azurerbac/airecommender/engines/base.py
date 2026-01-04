"""Base class for recommendation engines.

Provides RankedRole dataclass and BaseRecommenderEngine abstract base class.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

# Type alias for embedding vectors (tuple for hashability in LRU cache)
type Embedding = tuple[float, ...]

if TYPE_CHECKING:
    from azurerbac.airecommender.embeddings import EmbeddingModel
    from azurerbac.airecommender.engines.enhanced_tfidf import EnhancedTFIDFRecommender
    from azurerbac.airecommender.knowledge import RoleKnowledgeBase
    from azurerbac.airecommender.llm import OllamaClient

logger = logging.getLogger(__name__)

# Maximum description length in LLM prompts (avoids token bloat)
_LLM_DESC_TRUNCATE: Final[int] = 100


@dataclass(slots=True)
class RankedRole:
    """A role with ranking scores from different pipeline stages."""

    role_id: str
    role_name: str
    description: str
    tfidf_score: float = 0.0
    embedding_score: float = 0.0
    llm_score: float = 0.0
    final_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_embedding_search(
        cls,
        role_id: str,
        role_name: str,
        description: str,
        score: float,
        matched_keywords: list[str] | None = None,
    ) -> RankedRole:
        """Create from embedding search results (sets embedding_score = final_score)."""
        return cls(
            role_id=role_id,
            role_name=role_name,
            description=description,
            embedding_score=score,
            final_score=score,
            matched_keywords=matched_keywords or [],
        )


class BaseRecommenderEngine(ABC):
    """Abstract base class for all recommendation engines.

    Each engine must implement the `recommend` method to provide
    role recommendations based on a natural language query.
    """

    def __init__(
        self,
        knowledge_base: RoleKnowledgeBase,
        ollama_client: OllamaClient | None = None,
        embedding_model: EmbeddingModel | None = None,
        tfidf_recommender: EnhancedTFIDFRecommender | None = None,
    ) -> None:
        """Initialize the engine with required components.

        Args:
            knowledge_base: Role knowledge base with role documents
            ollama_client: Optional Ollama LLM client
            embedding_model: Optional sentence embedding model
            tfidf_recommender: Optional TF-IDF/BM25 recommender
        """
        self.knowledge_base = knowledge_base
        self.ollama_client = ollama_client
        self.embedding_model = embedding_model
        self.tfidf_recommender = tfidf_recommender

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this engine."""
        ...

    @property
    @abstractmethod
    def requires_llm(self) -> bool:
        """Whether this engine requires an LLM connection."""
        ...

    @property
    @abstractmethod
    def requires_embeddings(self) -> bool:
        """Whether this engine requires embedding model."""
        ...

    @abstractmethod
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]:
        """Get role recommendations for a natural language query.

        Args:
            query: Natural language query describing desired permissions
            top_k: Maximum number of recommendations to return
            exclude_owner: Whether to exclude Owner role (least privilege)

        Returns:
            List of RankedRole objects sorted by relevance (highest first)
        """
        ...

    def is_available(self) -> bool:
        """Check if all required components are available for this engine.

        Returns:
            True if engine has all required dependencies (LLM, embeddings)
            based on its declared requirements.
        """
        llm_ok = not self.requires_llm or self.is_llm_available
        embeddings_ok = not self.requires_embeddings or self.is_embeddings_available
        return llm_ok and embeddings_ok

    # -------------------------------------------------------------------------
    # Logging helpers - reduce boilerplate in subclasses
    # -------------------------------------------------------------------------

    def _log_start(self, query: str, top_k: int) -> None:
        """Log the start of a recommendation request."""
        logger.info(
            "%s: Starting for query='%s...' top_k=%d",
            self.name,
            query[:50],
            top_k,
        )

    def _log_complete(self, results: list[RankedRole], show_top: int = 3) -> None:
        """Log completion of a recommendation request."""
        if results and show_top > 0:
            top_names = [r.role_name for r in results[:show_top]]
            logger.info(
                "%s: Completed with %d results. Top %d: %s",
                self.name,
                len(results),
                show_top,
                top_names,
            )
        else:
            logger.info("%s: Completed with %d results", self.name, len(results))

    def _encode_cached(self, text: str) -> Embedding | None:
        """Encode text to an embedding vector with caching.

        Centralizes embedding model checks so subclasses don't repeat
        the same validation boilerplate. Uses LRU cache internally.

        Args:
            text: The text to encode (query or document)

        Returns:
            Embedding tuple if successful, None if model unavailable.
            Returns tuple (not list) for LRU cache hashability.
        """
        if self.embedding_model is None or not self.embedding_model.is_loaded:
            logger.warning("%s: Embedding model not loaded", self.__class__.__name__)
            return None
        return self.embedding_model.encode_single_cached(text)

    @staticmethod
    def _filter_min_confidence(
        candidates: list[RankedRole],
        *,
        threshold: float,
    ) -> list[RankedRole]:
        """Filter candidates below minimum confidence threshold."""
        if not candidates:
            return candidates
        return [c for c in candidates if c.final_score >= threshold]

    def _finalize_results(
        self,
        candidates: list[RankedRole],
        *,
        threshold: float,
        normalizer: Callable[[list[RankedRole]], list[RankedRole]] | None = None,
    ) -> list[RankedRole]:
        """Filter low-confidence results and normalize scores.

        Common end-of-pipeline logic for engines.

        Args:
            candidates: List of ranked roles to process
            threshold: Minimum confidence score to keep
            normalizer: Optional score normalization function (default: normalize_scores)

        Returns:
            Filtered and normalized candidates
        """
        original_count = len(candidates)
        candidates = self._filter_min_confidence(candidates, threshold=threshold)

        if original_count > len(candidates):
            logger.debug(
                "%s: Filtered %d low-confidence results (threshold=%.2f)",
                self.__class__.__name__,
                original_count - len(candidates),
                threshold,
            )

        if normalizer is not None:
            candidates = normalizer(candidates)
        else:
            from azurerbac.airecommender.engines.common import normalize_scores

            candidates = normalize_scores(candidates)

        return candidates

    @property
    def is_llm_available(self) -> bool:
        """Check if LLM client is connected and available."""
        return self.ollama_client is not None and self.ollama_client.is_connected

    @property
    def is_embeddings_available(self) -> bool:
        """Check if embedding model is loaded and available."""
        return self.embedding_model is not None and self.embedding_model.is_loaded

    def get_role_document(self, role_id: str) -> dict[str, Any] | None:
        """Get role document from knowledge base."""
        return self.knowledge_base.role_documents.get(role_id)

    def _llm_rerank(
        self,
        query: str,
        candidates: list[RankedRole],
        top_k: int,
    ) -> list[RankedRole]:
        """Re-rank candidates using LLM judgment.

        Shared by RAGEngine and HybridEngine. Constructs a prompt with
        candidate summaries, asks the LLM to rank by relevance, then
        re-orders candidates based on the LLM's response.

        Args:
            query: Original user query for context
            candidates: Pre-filtered candidates from retrieval stage
            top_k: Maximum results to return

        Returns:
            Re-ranked list with LLM scores applied. Falls back to
            original order if LLM unavailable or fails.
        """
        if not self.is_llm_available:
            logger.warning(
                "%s: No LLM connection, returning candidates as-is", self.__class__.__name__
            )
            return candidates[:top_k]

        # Build concise candidate summaries for LLM prompt
        candidate_text = "\n".join(
            f"- {c.role_name}: {c.description[:_LLM_DESC_TRUNCATE]}..." for c in candidates
        )

        prompt = f"""Given this Azure RBAC request: "{query}"

Rank these candidate roles from most to least appropriate (top 5 only):
{candidate_text}

Return ONLY the role names in order, one per line, most appropriate first."""

        try:
            if self.ollama_client is None:
                logger.warning(
                    "%s: Ollama client not available for reranking", self.__class__.__name__
                )
                return candidates[:top_k]
            response = self.ollama_client.generate(prompt)
            if not response:
                logger.warning("%s: Empty response from LLM", self.__class__.__name__)
                return candidates[:top_k]

            # Parse LLM response - extract and normalize role names
            llm_ranked_names = self._parse_llm_ranking_response(response)

            # Re-order candidates based on LLM ranking
            name_to_candidate = {c.role_name.lower(): c for c in candidates}
            reranked: list[RankedRole] = []
            seen_ids: set[str] = set()  # O(1) lookup for deduplication

            for i, name in enumerate(llm_ranked_names[:top_k]):
                if name in name_to_candidate:
                    c = name_to_candidate[name]
                    if c.role_id not in seen_ids:
                        c.llm_score = 1.0 - (i * 0.1)
                        c.final_score = (c.embedding_score + c.llm_score) / 2
                        reranked.append(c)
                        seen_ids.add(c.role_id)

            # Add remaining candidates not ranked by LLM (penalized score)
            for c in candidates:
                if c.role_id not in seen_ids and len(reranked) < top_k:
                    c.final_score = c.embedding_score * 0.5
                    reranked.append(c)
                    seen_ids.add(c.role_id)

            reranked.sort(key=lambda r: r.final_score, reverse=True)
            return reranked[:top_k]

        except Exception:
            logger.exception("%s: LLM rerank failed", self.__class__.__name__)
            return candidates[:top_k]

    @staticmethod
    def _parse_llm_ranking_response(response: str) -> list[str]:
        """Parse LLM response into normalized role names.

        Handles common LLM output formats:
        - "1. Role Name" or "1) Role Name"
        - "- Role Name"
        - "Role Name" (plain)

        Args:
            response: Raw LLM response text

        Returns:
            List of lowercase role names, in order.
        """
        names: list[str] = []
        for line in response.strip().split("\n"):
            # Strip numbering/bullets: "1. ", "1) ", "- ", etc.
            name = line.strip().lstrip("0123456789.-) ").strip()
            if name:
                names.append(name.lower())
        return names

    def should_exclude_role(self, role_name: str, exclude_owner: bool) -> bool:
        """Check if a role should be excluded from results.

        Currently excludes only the Owner role when exclude_owner=True,
        following the principle of least privilege.

        Args:
            role_name: The role name to check
            exclude_owner: Whether Owner role should be excluded

        Returns:
            True if role should be filtered out.
        """
        return exclude_owner and role_name.lower() == "owner"

    def retrieve_by_embedding(
        self,
        query: str,
        query_embedding: Embedding | list[float],
        top_k: int,
        exclude_owner: bool,
        *,
        use_search_vector: bool = False,
    ) -> list[RankedRole]:
        """Retrieve candidates by embedding similarity.

        Core retrieval method shared by SemanticEngine, HyDEEngine,
        RAGEngine, and CrossEncoderEngine. Implements efficient
        similarity search with early termination.

        Args:
            query: Original query (for keyword extraction)
            query_embedding: Pre-computed query embedding vector
            top_k: Number of results to return
            exclude_owner: Whether to exclude Owner role
            use_search_vector: If True, use embedding_model.search_vector
                              which leverages pre-built numpy matrix.
                              If False, use top_k_similar from common module.

        Returns:
            List of RankedRole objects sorted by descending similarity.
            Empty list if embedding model unavailable.
        """
        # Fast path: check availability once
        if self.embedding_model is None or not self.embedding_model.is_loaded:
            logger.warning("%s: Embedding model not available", self.__class__.__name__)
            return []

        embeddings = self.embedding_model.embeddings
        if not embeddings:
            logger.warning(
                "%s: No embeddings available in model",
                self.__class__.__name__,
            )
            return []

        logger.debug(
            "%s: Computing cosine similarity against %d role embeddings",
            self.__class__.__name__,
            len(embeddings),
        )

        # Compute similarity scores with 2x candidates for filtering headroom
        retrieval_k = top_k * 2
        if use_search_vector and self.embedding_model is not None:
            scores = self.embedding_model.search_vector(
                list(query_embedding),
                retrieval_k,
            )
        else:
            from azurerbac.airecommender.engines.common import top_k_similar

            scores = top_k_similar(query_embedding, embeddings, retrieval_k)

        # Build RankedRole list with filtering and early termination
        return self._build_ranked_roles(query, scores, top_k, exclude_owner)

    def _build_ranked_roles(
        self,
        query: str,
        scores: Iterable[tuple[str, float]],
        top_k: int,
        exclude_owner: bool,
    ) -> list[RankedRole]:
        """Convert similarity scores to RankedRole objects.

        Extracted for testability and single responsibility.
        Applies filtering and early termination.

        Args:
            query: Original query for keyword extraction
            scores: Iterable of (role_id, similarity_score) pairs
            top_k: Maximum results to return
            exclude_owner: Whether to exclude Owner role

        Returns:
            List of RankedRole objects, length <= top_k
        """
        from azurerbac.airecommender.knowledge import extract_keywords

        keywords = extract_keywords(query)
        candidates: list[RankedRole] = []

        for role_id, score in scores:
            doc = self.get_role_document(role_id)
            if not doc:
                continue
            if self.should_exclude_role(doc["role_name"], exclude_owner):
                continue

            candidates.append(
                RankedRole.from_embedding_search(
                    role_id=role_id,
                    role_name=doc["role_name"],
                    description=doc["description"],
                    score=score,
                    matched_keywords=keywords,
                )
            )
            # Early termination once we have enough
            if len(candidates) >= top_k:
                break

        return candidates
