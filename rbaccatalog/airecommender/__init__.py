"""AI role recommendation engine."""

from .ai_recommender import (
    AIRecommendation,
    AIRecommendRequest,
    EngineNotAvailableError,
    ai_recommend_roles,
    get_ai_recommender,
    reset_ai_recommender,
)
from .engines import (
    BaseRecommenderEngine,
    HybridEngine,
    LLMEngine,
    RAGEngine,
    RankedRole,
    TFIDFEngine,
)
from .exceptions import (
    AIRecommenderError,
    KnowledgeBaseNotInitializedError,
    OllamaClientNotAvailableError,
)
from .modes import RecommenderMode

__all__ = [
    "AIRecommendRequest",
    "AIRecommendation",
    "AIRecommenderError",
    "BaseRecommenderEngine",
    "EngineNotAvailableError",
    "HybridEngine",
    "KnowledgeBaseNotInitializedError",
    "LLMEngine",
    "OllamaClientNotAvailableError",
    "RAGEngine",
    "RankedRole",
    "RecommenderMode",
    "TFIDFEngine",
    "ai_recommend_roles",
    "get_ai_recommender",
    "reset_ai_recommender",
]
