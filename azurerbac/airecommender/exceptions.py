"""AI recommender exceptions."""


class AIRecommenderError(Exception):
    """Base exception for AI recommender."""


class OllamaClientNotAvailableError(AIRecommenderError):
    """Ollama client required but not available."""

    def __init__(self, engine_name: str) -> None:
        self.engine_name = engine_name
        super().__init__(f"{engine_name} requires Ollama LLM client")


class KnowledgeBaseNotInitializedError(AIRecommenderError):
    """Knowledge base not initialized."""

    def __init__(self) -> None:
        super().__init__("Knowledge base not initialized")
