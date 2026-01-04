"""Custom exceptions for the AI recommender package."""

from __future__ import annotations


class AIRecommenderError(Exception):
    """Base exception for all AI recommender errors."""


class OllamaClientNotAvailableError(AIRecommenderError):
    """Raised when an Ollama client is required but not available."""

    def __init__(self, engine_name: str) -> None:
        self.engine_name = engine_name
        super().__init__(
            f"{engine_name} requires an Ollama LLM client but none was provided. "
            "Ensure Ollama is running and the client is properly configured."
        )


class EmbeddingModelNotAvailableError(AIRecommenderError):
    """Raised when an embedding model is required but not available."""

    def __init__(self, engine_name: str) -> None:
        self.engine_name = engine_name
        super().__init__(
            f"{engine_name} requires an embedding model but none was loaded. "
            "Check that sentence-transformers is installed and the model files exist."
        )


class KnowledgeBaseNotInitializedError(AIRecommenderError):
    """Raised when the knowledge base is required but not initialized."""

    def __init__(self) -> None:
        super().__init__(
            "Knowledge base not initialized. Call initialize() before using the recommender."
        )
