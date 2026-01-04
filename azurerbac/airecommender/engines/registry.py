"""Engine Registry for recommendation engine discovery and instantiation."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar, TypeVar

if TYPE_CHECKING:
    from azurerbac.airecommender.embeddings import EmbeddingModel
    from azurerbac.airecommender.engines.base import BaseRecommenderEngine
    from azurerbac.airecommender.engines.enhanced_tfidf import EnhancedTFIDFRecommender
    from azurerbac.airecommender.knowledge import RoleKnowledgeBase
    from azurerbac.airecommender.llm import OllamaClient
    from azurerbac.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)

# Type variable for engine class decorator (bound to BaseRecommenderEngine)
_EngineT = TypeVar("_EngineT", bound="BaseRecommenderEngine")


class EngineRegistry:
    """Registry for recommendation engines with decorator-based registration.

    Engines register themselves using the @EngineRegistry.register decorator,
    which associates a RecommenderMode with an engine class. This eliminates
    the need for hardcoded mode→engine mappings.

    Example:
        @EngineRegistry.register(RecommenderMode.TFIDF)
        class TFIDFEngine(BaseRecommenderEngine):
            ...

        # Later, get engine instance:
        engine = EngineRegistry.create(
            mode=RecommenderMode.TFIDF,
            knowledge_base=kb,
            ...
        )
    """

    _engines: ClassVar[dict[RecommenderMode, type[BaseRecommenderEngine]]] = {}
    _default_mode: ClassVar[RecommenderMode | None] = None

    @classmethod
    def register(
        cls, mode: RecommenderMode, *, is_default: bool = False
    ) -> Callable[[type[_EngineT]], type[_EngineT]]:
        """Decorator to register an engine class for a specific mode.

        Args:
            mode: The RecommenderMode this engine handles
            is_default: Whether this should be the default fallback engine

        Returns:
            Decorator function that registers the engine class
        """

        def decorator(engine_class: type[_EngineT]) -> type[_EngineT]:
            cls._engines[mode] = engine_class
            if is_default:
                cls._default_mode = mode
            logger.debug("Registered %s for mode %s", engine_class.__name__, mode.value)
            return engine_class

        return decorator

    @classmethod
    def create(
        cls,
        mode: RecommenderMode,
        *,
        knowledge_base: RoleKnowledgeBase,
        ollama_client: OllamaClient | None = None,
        embedding_model: EmbeddingModel | None = None,
        tfidf_recommender: EnhancedTFIDFRecommender | None = None,
    ) -> BaseRecommenderEngine:
        """Create an engine instance for the specified mode.

        Args:
            mode: The recommender mode to get engine for
            knowledge_base: Role knowledge base with role documents
            ollama_client: Optional Ollama LLM client
            embedding_model: Optional sentence embedding model
            tfidf_recommender: Optional TF-IDF/BM25 recommender

        Returns:
            Instantiated engine for the requested mode

        Raises:
            ValueError: If the mode is not registered
        """
        engine_class = cls._engines.get(mode)

        if engine_class is None:
            if cls._default_mode is not None:
                engine_class = cls._engines.get(cls._default_mode)
            if engine_class is None:
                registered = [m.value for m in cls._engines]
                msg = (
                    f"No engine registered for mode '{mode.value}'. Registered modes: {registered}"
                )
                raise ValueError(msg)

        return engine_class(
            knowledge_base=knowledge_base,
            ollama_client=ollama_client,
            embedding_model=embedding_model,
            tfidf_recommender=tfidf_recommender,
        )

    @classmethod
    def get_registered_modes(cls) -> list[RecommenderMode]:
        """Get all registered recommender modes."""
        return list(cls._engines.keys())
