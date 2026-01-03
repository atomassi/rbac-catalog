"""AI-powered Role Recommender using multiple recommendation engines."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel

from azurerbac.airecommender.engines import (
    BaseRecommenderEngine,
    EngineRegistry,
    RankedRole,
)
from azurerbac.airecommender.knowledge import RoleKnowledgeBase
from azurerbac.airecommender.llm import OllamaClient
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.azure.roles import get_role_id
from azurerbac.core.singleton import ThreadSafeSingleton

if TYPE_CHECKING:
    from azurerbac.airecommender.embeddings import EmbeddingModel
    from azurerbac.airecommender.engines import EnhancedTFIDFRecommender

logger = logging.getLogger(__name__)

# Model paths (relative to this file)
MODELS_DIR: Final = Path(__file__).parent / "models"


class EngineNotAvailableError(Exception):
    """Raised when a requested recommender engine is not available."""

    def __init__(self, engine_name: str, mode: str, missing_components: list[str]) -> None:
        self.engine_name = engine_name
        self.mode = mode
        self.missing_components = missing_components
        super().__init__(
            "This recommendation mode is not available on this server. "
            'Please select "TF-IDF" mode or LLM instead.'
        )


class AIRecommendRequest(BaseModel):
    """Request body for AI-powered role recommendations."""

    query: str
    top_k: int = 5
    recommender_mode: str = RecommenderMode.LLM.value  # llm, rag, hybrid, or tfidf


@dataclass(slots=True)
class AIRecommendation:
    """Result from AI role recommendation."""

    role_id: str
    role_name: str
    description: str
    score: float  # Similarity score 0-1
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the recommendation to a dictionary."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "description": self.description,
            "score": round(self.score, 3),
            "matched_keywords": self.matched_keywords,
        }

    @classmethod
    def from_ranked_role(cls, ranked: RankedRole) -> AIRecommendation:
        """Create AIRecommendation from RankedRole."""
        return cls(
            role_id=ranked.role_id,
            role_name=ranked.role_name,
            description=ranked.description,
            score=ranked.final_score,
            matched_keywords=ranked.matched_keywords,
        )


class AIRoleRecommender:
    """AI-powered role recommender with multiple engine options."""

    __slots__ = (
        "_embedding_model",
        "_enhanced_tfidf",
        "_initialized",
        "_knowledge_base",
        "_ollama_client",
        "_roles_hash",
    )

    def __init__(self) -> None:
        self._ollama_client: OllamaClient | None = None
        self._knowledge_base: RoleKnowledgeBase | None = None
        self._enhanced_tfidf: EnhancedTFIDFRecommender | None = None
        self._embedding_model: EmbeddingModel | None = None
        self._initialized = False
        self._roles_hash: str | None = None

    def _init_enhanced_tfidf(self, roles: list[dict]) -> None:
        """Initialize enhanced TF-IDF + BM25 recommender."""
        try:
            from azurerbac.airecommender.engines import EnhancedTFIDFRecommender

            self._enhanced_tfidf = EnhancedTFIDFRecommender()
            self._enhanced_tfidf.initialize(roles)
            logger.info("Initialized enhanced TF-IDF + BM25 recommender")
        except Exception as e:
            logger.exception("Failed to initialize enhanced TF-IDF: %s", e)
            self._enhanced_tfidf = None

    def _init_embedding_model(self) -> None:
        """Initialize sentence embedding model for semantic search."""
        try:
            from .embeddings import EmbeddingModel, compute_documents_hash

            self._embedding_model = EmbeddingModel()
            if self._embedding_model.try_load():
                # Build document texts for embedding from knowledge base
                # This includes curated patterns which are critical for good retrieval
                documents = {}
                for role_id, doc in self._knowledge_base.role_documents.items():
                    documents[role_id] = doc["document_text"]

                # Compute hash for cache validation
                cache_hash = compute_documents_hash(documents)
                self._embedding_model.build_embeddings(documents, cache_hash)
                logger.info("Initialized embedding model for RAG/Hybrid engines")
            else:
                self._embedding_model = None
        except Exception as e:
            logger.exception("Failed to initialize embedding model: %s", e)
            self._embedding_model = None

    def initialize(
        self,
        roles: list[dict],
    ) -> None:
        """Initialize the recommender with role data from the database.

        Args:
            roles: List of role JSON objects from the database
        """
        # Compute hash of sorted role IDs to detect changes
        current_hash = self._compute_roles_hash(roles)

        if self._initialized and self._roles_hash == current_hash:
            # Already initialized with same data - skip expensive re-init
            logger.debug("AI recommender already initialized (hash=%s)", current_hash)
            return

        logger.info("Initializing AI recommender with %d roles...", len(roles))

        # Initialize knowledge base
        self._knowledge_base = RoleKnowledgeBase()
        self._knowledge_base.load_from_file()  # Load pre-built KB if available
        self._knowledge_base.build_from_roles(roles)

        # Create Ollama client (lazy connection - only connects when LLM mode is used)
        self._ollama_client = OllamaClient()

        # Initialize enhanced TF-IDF (BM25 + pattern matching)
        self._init_enhanced_tfidf(roles)

        # Initialize embedding model for RAG/Hybrid engines
        self._init_embedding_model()

        self._initialized = True
        self._roles_hash = current_hash
        logger.info(
            f"AI recommender initialized. "
            f"TF-IDF: {self._enhanced_tfidf is not None}, "
            f"Embeddings: {self._embedding_model is not None and self._embedding_model.is_loaded}, "
            f"Ollama: {self._ollama_client.is_connected if self._ollama_client else False}"
        )

    def _compute_roles_hash(self, roles: list[dict]) -> str:
        """Compute a hash of sorted role IDs to detect changes."""
        from azurerbac.core.utils import content_hash

        role_ids = sorted(get_role_id(role) for role in roles)
        combined = ",".join(role_ids)
        return content_hash(combined)

    def _get_engine(self, mode: RecommenderMode) -> BaseRecommenderEngine:
        """Get the appropriate engine for the requested mode.

        Uses the EngineRegistry to create engine instances. Engines self-register
        using the @EngineRegistry.register decorator, eliminating hardcoded mappings.

        Lazily connects to Ollama only when an LLM-requiring mode is requested.
        """
        # Lazy Ollama connection - only connect when LLM mode is actually used
        if mode.requires_llm and self._ollama_client and not self._ollama_client.is_connected:
            logger.debug("LLM mode requested, attempting lazy Ollama connection...")
            if self._ollama_client.try_connect():
                logger.debug("Ollama connected successfully, initializing role names")
                # Initialize role names for fuzzy matching
                role_names = self._knowledge_base.get_all_role_names()
                if role_names:
                    self._ollama_client.set_known_role_names(role_names)
            else:
                logger.warning("Ollama connection failed, LLM mode will be unavailable")

        return EngineRegistry.create(
            mode=mode,
            knowledge_base=self._knowledge_base,
            ollama_client=self._ollama_client,
            embedding_model=self._embedding_model,
            tfidf_recommender=self._enhanced_tfidf,
        )

    def _should_exclude_owner(self, query: str) -> bool:
        """Check if Owner role should be excluded (least privilege principle)."""
        query_lower = query.lower()
        return "owner" not in query_lower and "full access" not in query_lower

    def recommend(
        self,
        query: str,
        top_k: int = 5,
        requested_mode: str | None = None,
    ) -> tuple[list[AIRecommendation], str]:
        """Get AI-powered role recommendations for a natural language query.

        Args:
            query: Natural language description like "I need to read storage blobs"
            top_k: Number of recommendations to return
            requested_mode: Requested recommender mode (tfidf, llm, rag, hybrid)

        Returns:
            Tuple of (recommendations, actual_mode_used)
        """
        if not self._initialized:
            raise RuntimeError("Recommender not initialized. Call initialize() first.")

        # Parse mode
        mode = (
            RecommenderMode.from_string(requested_mode)
            if isinstance(requested_mode, str)
            else requested_mode
        )
        if mode is None:
            mode = RecommenderMode.LLM  # Default mode

        # Check if we should exclude Owner
        exclude_owner = self._should_exclude_owner(query)

        # Get the appropriate engine
        engine = self._get_engine(mode)

        # Check if engine is available - raise error instead of silent fallback
        if not engine.is_available():
            missing = []
            if mode.requires_llm and (
                not self._ollama_client or not self._ollama_client.is_connected
            ):
                missing.append("Ollama LLM")
            if mode.requires_embeddings and (
                not self._embedding_model or not self._embedding_model.is_loaded
            ):
                missing.append("sentence-transformers")
            # ColBERT requires ragatouille
            if mode == RecommenderMode.COLBERT:
                missing.append("ragatouille")
            raise EngineNotAvailableError(
                engine_name=engine.name,
                mode=mode.value,
                missing_components=missing,
            )

        logger.debug("Using %s engine for query: %s...", engine.name, query[:50])

        # Get recommendations from engine
        ranked_roles = engine.recommend(query, top_k=top_k, exclude_owner=exclude_owner)

        # Convert to AIRecommendation objects, return with actual mode used
        recommendations = [AIRecommendation.from_ranked_role(r) for r in ranked_roles]
        return recommendations, mode.value

    @property
    def is_initialized(self) -> bool:
        """Check if the recommender is initialized."""
        return self._initialized


# Singleton using ThreadSafeSingleton for consistency with rest of codebase
_recommender = ThreadSafeSingleton(AIRoleRecommender)


def get_ai_recommender() -> AIRoleRecommender:
    """Get the global AI recommender instance."""
    return _recommender.get()


def reset_ai_recommender() -> None:
    """Reset the AI recommender singleton (for testing)."""
    _recommender.reset()


def ai_recommend_roles(
    query: str,
    roles: list[dict],
    top_k: int = 5,
    requested_mode: str | None = None,
) -> tuple[list[dict], str]:
    """Convenience function for AI role recommendations.

    Args:
        query: Natural language description
        roles: List of role JSON objects from database
        top_k: Number of recommendations
        requested_mode: Requested recommender mode

    Returns:
        Tuple of (recommendations, actual_mode_used)
    """
    recommender = get_ai_recommender()

    if not recommender.is_initialized:
        recommender.initialize(roles)

    recommendations, actual_mode = recommender.recommend(query, top_k, requested_mode)
    return [r.to_dict() for r in recommendations], actual_mode
