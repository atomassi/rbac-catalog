"""LLM module for Ollama-based role recommendations.

This module provides the Ollama client and prompts for LLM-powered
role recommendations.
"""

from azurerbac.airecommender.llm.client import OllamaClient
from azurerbac.airecommender.llm.json_repair import (
    extract_json_from_markdown,
    parse_json_with_repair,
)

__all__ = [
    "OllamaClient",
    "extract_json_from_markdown",
    "parse_json_with_repair",
]
