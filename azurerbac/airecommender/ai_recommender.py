"""AI-powered role recommender."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from azurerbac.airecommender.engines import (
    BaseRecommenderEngine,
    EngineRegistry,
    RankedRole,
)
from azurerbac.airecommender.exceptions import KnowledgeBaseNotInitializedError
from azurerbac.airecommender.knowledge import RoleKnowledgeBase
from azurerbac.airecommender.llm import OllamaClient
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.azure.models import RoleDefinition
from azurerbac.core.singleton import ThreadSafeSingleton
from azurerbac.core.types import JsonDict

if TYPE_CHECKING:
    from azurerbac.airecommender.embeddings import EmbeddingModel
    from azurerbac.airecommender.engines import EnhancedTFIDFRecommender

logger = logging.getLogger(__name__)

MAX_AI_QUERY_LENGTH = 100
MAX_TOP_K = 20


class EngineNotAvailableError(Exception):
    """Raised when a requested engine is not available."""

    def __init__(self, mode: str, missing_components: list[str]) -> None:
        self.mode = mode
        self.missing_components = missing_components
        super().__init__(
            'This recommendation mode is not available. Please select "TF-IDF" mode instead.'
        )


class AIRecommendRequest(BaseModel):
    """Role recommendation request."""

    query: str = Field(..., min_length=1, max_length=MAX_AI_QUERY_LENGTH)
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K)
    recommender_mode: str = Field(default=RecommenderMode.LLM.value, max_length=32)


@dataclass(slots=True)
class AIRecommendation:
    """Role recommendation result."""

    role_id: str
    role_name: str
    description: str
    score: float
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "description": self.description,
            "score": round(self.score, 3),
            "matched_keywords": self.matched_keywords,
        }

    @classmethod
    def from_ranked_role(cls, ranked: RankedRole) -> AIRecommendation:
        return cls(
            role_id=ranked.role_id,
            role_name=ranked.role_name,
            description=ranked.description,
            score=ranked.final_score,
            matched_keywords=ranked.matched_keywords,
        )


class AIRoleRecommender:
    """Main recommender coordinating engines and models."""

    __slots__ = (
        "_embedding_model",
        "_enhanced_tfidf",
        "_init_lock",
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
        self._init_lock = threading.Lock()

    def _init_enhanced_tfidf(self, roles: list[RoleDefinition]) -> None:
        try:
            from azurerbac.airecommender.engines import EnhancedTFIDFRecommender

            self._enhanced_tfidf = EnhancedTFIDFRecommender()
            self._enhanced_tfidf.initialize(roles)
            logger.info("Initialized TF-IDF + BM25 recommender")
        except Exception as e:
            logger.exception("Failed to initialize TF-IDF: %s", e)
            self._enhanced_tfidf = None

    def _init_embedding_model(self) -> None:
        if self._knowledge_base is None:
            logger.warning("Cannot initialize embedding model: knowledge base not loaded")
            return

        try:
            from .embeddings import EmbeddingModel, compute_documents_hash

            self._embedding_model = EmbeddingModel()
            if self._embedding_model.try_load():
                documents = {
                    role_id: doc["document_text"]
                    for role_id, doc in self._knowledge_base.role_documents.items()
                }
                cache_hash = compute_documents_hash(documents)
                self._embedding_model.build_embeddings(documents, cache_hash)
                logger.info("Initialized embedding model")
            else:
                self._embedding_model = None
        except Exception as e:
            logger.exception("Failed to initialize embedding model: %s", e)
            self._embedding_model = None

    def initialize(self, roles: list[RoleDefinition]) -> None:
        current_hash = self._compute_roles_hash(roles)

        # Serialize concurrent initialization: callers may race when the
        # recommender is invoked from worker threads (anyio.to_thread).
        with self._init_lock:
            if self._initialized and self._roles_hash == current_hash:
                logger.debug("AI recommender already initialized (hash=%s)", current_hash)
                return

            logger.info("Initializing AI recommender with %d roles...", len(roles))
            self._initialize_locked(roles, current_hash)

    def _initialize_locked(self, roles: list[RoleDefinition], current_hash: str) -> None:
        self._knowledge_base = RoleKnowledgeBase()
        self._knowledge_base.load_from_file()
        self._knowledge_base.build_from_roles(roles)

        self._ollama_client = OllamaClient()
        self._init_enhanced_tfidf(roles)
        self._init_embedding_model()

        self._initialized = True
        self._roles_hash = current_hash
        logger.info(
            "AI recommender initialized. TF-IDF: %s, Embeddings: %s, Ollama: %s",
            self._enhanced_tfidf is not None,
            self._embedding_model is not None and self._embedding_model.is_loaded,
            self._ollama_client.is_connected if self._ollama_client else False,
        )

    def _compute_roles_hash(self, roles: list[RoleDefinition]) -> str:
        from azurerbac.core.utils import content_hash

        role_ids = sorted(role.name for role in roles)
        return content_hash(",".join(role_ids))

    def _get_engine(self, mode: RecommenderMode) -> BaseRecommenderEngine:
        if self._knowledge_base is None:
            raise KnowledgeBaseNotInitializedError()

        missing = self._check_missing_components(mode)
        if missing:
            raise EngineNotAvailableError(mode=mode.value, missing_components=missing)

        return EngineRegistry.create(
            mode=mode,
            knowledge_base=self._knowledge_base,
            ollama_client=self._ollama_client,
            embedding_model=self._embedding_model,
            tfidf_recommender=self._enhanced_tfidf,
        )

    def _check_missing_components(self, mode: RecommenderMode) -> list[str]:
        """Check for missing components required by the mode."""
        missing: list[str] = []

        if mode.requires_llm:
            missing.extend(self._check_llm_available())

        if mode.requires_embeddings and (
            not self._embedding_model or not self._embedding_model.is_loaded
        ):
            missing.append("sentence-transformers")

        if mode == RecommenderMode.COLBERT:
            import importlib.util

            if importlib.util.find_spec("ragatouille") is None:
                missing.append("ragatouille")

        return missing

    def _check_llm_available(self) -> list[str]:
        """Check if LLM is available, attempting connection if needed."""
        if not self._ollama_client:
            return ["Ollama LLM"]

        if self._ollama_client.is_connected:
            return []

        if self._ollama_client.try_connect():
            if self._knowledge_base:
                role_names = self._knowledge_base.get_all_role_names()
                if role_names:
                    self._ollama_client.set_known_role_names(role_names)
            return []

        return ["Ollama LLM"]

    def _should_exclude_owner(self, query: str) -> bool:
        q = query.lower()
        return "owner" not in q and "full access" not in q

    def recommend(
        self,
        query: str,
        top_k: int = 5,
        requested_mode: str | None = None,
    ) -> tuple[list[AIRecommendation], str]:
        if not self._initialized:
            raise RuntimeError("Recommender not initialized. Call initialize() first.")

        mode = (
            RecommenderMode.from_string(requested_mode)
            if isinstance(requested_mode, str)
            else requested_mode
        ) or RecommenderMode.LLM

        engine = self._get_engine(mode)
        exclude_owner = self._should_exclude_owner(query)

        logger.debug("Using %s engine for query: %s...", engine.name, query[:50])
        ranked_roles = engine.recommend(query, top_k=top_k, exclude_owner=exclude_owner)

        return [AIRecommendation.from_ranked_role(r) for r in ranked_roles], mode.value

    @property
    def is_initialized(self) -> bool:
        return self._initialized


_recommender = ThreadSafeSingleton(AIRoleRecommender)


def get_ai_recommender() -> AIRoleRecommender:
    """Get the global AI recommender singleton."""
    return _recommender.get()


def reset_ai_recommender() -> None:
    """Reset the singleton (for testing)."""
    _recommender.reset()


def ai_recommend_roles(
    query: str,
    roles: list[RoleDefinition],
    top_k: int = 5,
    requested_mode: str | None = None,
) -> tuple[list[JsonDict], str]:
    """Get recommendations as dicts. Returns (results, mode_used)."""
    recommender = get_ai_recommender()

    if not recommender.is_initialized:
        recommender.initialize(roles)

    recommendations, actual_mode = recommender.recommend(query, top_k, requested_mode)
    return [r.to_dict() for r in recommendations], actual_mode
