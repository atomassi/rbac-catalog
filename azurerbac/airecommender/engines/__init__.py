"""Recommendation engines package."""

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.common import (
    cosine_similarity,
    normalize_candidates,
    sigmoid_normalize,
    top_k_similar,
)
from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine
from azurerbac.airecommender.engines.enhanced_tfidf import (
    BM25Index,
    EnhancedTFIDFRecommender,
)
from azurerbac.airecommender.engines.hybrid import HybridEngine
from azurerbac.airecommender.engines.hyde import HyDEEngine
from azurerbac.airecommender.engines.llm import LLMEngine
from azurerbac.airecommender.engines.rag import RAGEngine
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.engines.semantic import SemanticEngine
from azurerbac.airecommender.engines.tfidf import TFIDFEngine

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
