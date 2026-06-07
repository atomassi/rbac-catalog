"""Recommendation engines package."""

from rbaccatalog.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from rbaccatalog.airecommender.engines.common import (
    cosine_similarity,
    normalize_candidates,
    sigmoid_normalize,
    top_k_similar,
)
from rbaccatalog.airecommender.engines.crossencoder import CrossEncoderEngine
from rbaccatalog.airecommender.engines.enhanced_tfidf import (
    BM25Index,
    EnhancedTFIDFRecommender,
)
from rbaccatalog.airecommender.engines.hybrid import HybridEngine
from rbaccatalog.airecommender.engines.hyde import HyDEEngine
from rbaccatalog.airecommender.engines.llm import LLMEngine
from rbaccatalog.airecommender.engines.rag import RAGEngine
from rbaccatalog.airecommender.engines.registry import EngineRegistry
from rbaccatalog.airecommender.engines.semantic import SemanticEngine
from rbaccatalog.airecommender.engines.tfidf import TFIDFEngine

__all__ = [
    "BM25Index",
    "BaseRecommenderEngine",
    "CrossEncoderEngine",
    "EngineRegistry",
    "EnhancedTFIDFRecommender",
    "HyDEEngine",
    "HybridEngine",
    "LLMEngine",
    "RAGEngine",
    "RankedRole",
    "SemanticEngine",
    "TFIDFEngine",
    "cosine_similarity",
    "normalize_candidates",
    "sigmoid_normalize",
    "top_k_similar",
]
