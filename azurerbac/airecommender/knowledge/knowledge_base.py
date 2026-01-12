"""Role knowledge base for semantic search."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from azurerbac.airecommender.knowledge.azure_knowledge import USE_CASE_PATTERNS
from azurerbac.core.types import JsonDict

if TYPE_CHECKING:
    from azurerbac.azure.models import RoleDefinition
    from azurerbac.cache import CacheContainer

logger = logging.getLogger(__name__)

_MODELS_DIR: Final[Path] = Path(__file__).parent.parent / "models"
_KB_PATH: Final[Path] = _MODELS_DIR / "knowledge_base.json"
_FUZZY_THRESHOLD: Final[float] = 0.7
_MAX_DOC_KEYWORDS: Final[int] = 15


class RoleKnowledgeBase:
    """Knowledge base for role recommendations."""

    __slots__ = ("_knowledge_base", "_name_to_role_id", "_name_word_sets", "_role_documents")

    def __init__(self) -> None:
        self._knowledge_base: JsonDict = {}
        self._role_documents: dict[str, JsonDict] = {}
        self._name_to_role_id: dict[str, str] = {}
        self._name_word_sets: dict[str, frozenset[str]] = {}

    @property
    def role_documents(self) -> dict[str, JsonDict]:
        return self._role_documents

    @property
    def knowledge_base(self) -> JsonDict:
        return self._knowledge_base

    def get_all_role_names(self) -> list[str]:
        """Get all known role names."""
        role_names = list(self._knowledge_base.get("role_details", {}).keys())
        for doc in self._role_documents.values():
            name = doc.get("role_name", "")
            if name and name not in role_names:
                role_names.append(name)
        return role_names

    def load_from_file(self) -> bool:
        """Load pre-built knowledge base from disk. Returns True on success."""
        if not _KB_PATH.exists():
            logger.info("Knowledge base not found. Run build_knowledge.py to generate it.")
            return False

        try:
            self._knowledge_base = json.loads(_KB_PATH.read_text(encoding="utf-8"))
            logger.info(
                "Loaded knowledge base: %d roles", self._knowledge_base.get("total_roles", 0)
            )
            return True
        except (OSError, json.JSONDecodeError) as e:
            logger.exception("Failed to load knowledge base: %s", e)
            return False

    def build_from_roles(
        self,
        roles: list[RoleDefinition],
        *,
        cache: CacheContainer | None = None,
    ) -> None:
        """Build searchable knowledge base from role definitions."""
        from azurerbac.cache import get_cache_service

        cache_resolved = cache if cache is not None else get_cache_service().container

        self._role_documents = {}
        self._name_to_role_id = {}
        self._name_word_sets = {}

        role_to_patterns = _build_role_to_patterns_index()

        for role in roles:
            role_id = role.name
            role_name = role.properties.role_name
            description = role.properties.description

            first_perm = role.properties.permissions[0] if role.properties.permissions else None
            raw_actions = first_perm.actions if first_perm else []
            raw_data_actions = first_perm.data_actions if first_perm else []

            cached_coverage = cache_resolved.get_role_coverage(role_id)
            if cached_coverage:
                effective_control, effective_data = cached_coverage
            else:
                effective_control = _filter_wildcards(raw_actions)
                effective_data = _filter_wildcards(raw_data_actions)

            curated_patterns = role_to_patterns.get(role_name, [])
            # Use expanded operations (wildcards resolved) for keyword extraction
            all_effective = effective_control | effective_data
            action_keywords = extract_operation_keywords(all_effective)

            doc_text = _build_document_text(
                curated_patterns, role_name, description, action_keywords
            )

            self._role_documents[role_id] = {
                "role_id": role_id,
                "role_name": role_name,
                "description": description,
                "actions": list(effective_control),
                "data_actions": list(effective_data),
                "keywords": action_keywords,
                "curated_patterns": curated_patterns,
                "document_text": doc_text,
            }

            role_name_lower = role_name.lower()
            self._name_to_role_id[role_name_lower] = role_id
            self._name_word_sets[role_id] = frozenset(role_name_lower.split())

        logger.info("Built knowledge base with %d role documents", len(self._role_documents))

    def find_role_id_by_name(self, role_name: str) -> str | None:
        """Find role_id by matching role name with fuzzy fallback."""
        name_lower = role_name.lower().strip()

        if name_lower in self._name_to_role_id:
            return self._name_to_role_id[name_lower]

        query_words = frozenset(name_lower.split())
        return self._find_best_fuzzy_match(query_words) if query_words else None

    def _find_best_fuzzy_match(self, query_words: frozenset[str]) -> str | None:
        """Find the best fuzzy match using precomputed word sets."""
        best_match: str | None = None
        best_score: float = 0.0
        query_len = len(query_words)

        for role_id, doc_words in self._name_word_sets.items():
            # Use precomputed word sets - no allocation per iteration
            common_count = len(query_words & doc_words)
            if common_count == 0:
                continue

            overlap_score = common_count / max(query_len, len(doc_words))

            if overlap_score >= _FUZZY_THRESHOLD and overlap_score > best_score:
                best_match = role_id
                best_score = overlap_score

                # Early exit on perfect match
                if best_score >= 1.0:
                    return best_match

        return best_match


def _build_role_to_patterns_index() -> dict[str, list[str]]:
    """Build reverse lookup from role name to query patterns."""
    role_to_patterns: dict[str, list[str]] = {}
    for pattern, role_names in USE_CASE_PATTERNS.items():
        for role_name in role_names:
            role_to_patterns.setdefault(role_name, []).append(pattern)
    return role_to_patterns


def _filter_wildcards(actions: Iterable[str]) -> set[str]:
    """Filter out wildcard patterns from actions."""
    return {a for a in actions if "*" not in a}


def _build_document_text(
    curated_patterns: list[str],
    role_name: str,
    description: str,
    action_keywords: list[str],
) -> str:
    """Build document text optimized for embeddings."""
    doc_parts: list[str] = []

    if curated_patterns:
        doc_parts.append(" ".join(curated_patterns))

    doc_parts.append(role_name)
    doc_parts.append(description)

    if action_keywords:
        doc_parts.append(" ".join(action_keywords[:_MAX_DOC_KEYWORDS]))

    return " ".join(doc_parts)


# Precompiled regex patterns for performance
_OPERATION_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(r"[/.]")
_CAMEL_CASE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z]+")

# Tokens to skip when extracting keywords
_SKIP_TOKENS: Final[frozenset[str]] = frozenset({"microsoft", "*", ""})


def extract_operation_keywords(operations: Iterable[str]) -> list[str]:
    """Extract meaningful keywords from operation names.

    Example: 'Microsoft.Storage/storageAccounts/read' -> ['storage', 'accounts', 'read']
    """
    keywords: set[str] = set()

    for op in operations:
        # Split by / and . using precompiled pattern
        parts = _OPERATION_SPLIT_PATTERN.split(op.lower())
        for part in parts:
            if part in _SKIP_TOKENS:
                continue
            # Extract lowercase words from camelCase
            words = _CAMEL_CASE_PATTERN.findall(part)
            keywords.update(words)

    return list(keywords)
