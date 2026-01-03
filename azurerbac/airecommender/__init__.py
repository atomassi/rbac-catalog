"""Role recommendation engine."""

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
from .modes import RecommenderMode

__all__ = [
    "AIRecommendRequest",
    "AIRecommendation",
    "BaseRecommenderEngine",
    "EngineNotAvailableError",
    "HybridEngine",
    "LLMEngine",
    "RAGEngine",
    "RankedRole",
    "RecommenderMode",
    "TFIDFEngine",
    "ai_recommend_roles",
    "get_ai_recommender",
    "reset_ai_recommender",
]
