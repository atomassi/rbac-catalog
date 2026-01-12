"""Azure RBAC domain knowledge and role indexing."""

from azurerbac.airecommender.knowledge.azure_knowledge import (
    ABBREVIATIONS,
    AZURE_SERVICE_SYNONYMS,
    PERMISSION_LEVELS,
    USE_CASE_PATTERNS,
    expand_query_with_synonyms,
    extract_keywords,
    find_matching_use_cases,
    get_negative_patterns,
)
from azurerbac.airecommender.knowledge.knowledge_base import RoleKnowledgeBase

__all__ = [
    "ABBREVIATIONS",
    "AZURE_SERVICE_SYNONYMS",
    "PERMISSION_LEVELS",
    "USE_CASE_PATTERNS",
    "RoleKnowledgeBase",
    "expand_query_with_synonyms",
    "extract_keywords",
    "find_matching_use_cases",
    "get_negative_patterns",
]
