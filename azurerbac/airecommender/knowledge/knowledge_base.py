"""Knowledge base management for role recommendations.

This module handles building and loading the role knowledge base
used by the AI recommender for semantic search.
"""

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

logger = logging.getLogger(__name__)

# =============================================================================
# Module Constants
# =============================================================================

# Paths - models directory is at airecommender/models, not airecommender/knowledge/models
MODELS_DIR: Final[Path] = Path(__file__).parent.parent / "models"
KNOWLEDGE_BASE_PATH: Final[Path] = MODELS_DIR / "knowledge_base.json"

# Minimum role name length to avoid false positives
_MIN_ROLE_NAME_LENGTH: Final[int] = 3

# Patterns that indicate invalid role names (LLM hallucinations)
_INVALID_NAME_PATTERNS: Final[frozenset[str]] = frozenset(
    {
        "day time",
        "what is",
        "how to",
        "when",
        "where",
        "why",
    }
)

# Minimum overlap ratio for fuzzy matching
_FUZZY_MATCH_THRESHOLD: Final[float] = 0.7

# Maximum keywords to include in document text
_MAX_DOCUMENT_KEYWORDS: Final[int] = 15


class RoleKnowledgeBase:
    """Knowledge base for role recommendations.

    Stores role documents with searchable text, keywords, and indexes.
    Precomputes word sets for efficient fuzzy matching.
    """

    __slots__ = (
        "_knowledge_base",
        "_name_to_role_id",
        "_name_word_sets",
        "_role_documents",
    )

    def __init__(self) -> None:
        """Initialize an empty knowledge base."""
        self._knowledge_base: JsonDict = {}
        self._role_documents: dict[str, JsonDict] = {}  # role_id -> document
        self._name_to_role_id: dict[str, str] = {}  # lowercase name -> role_id
        # Precomputed word sets for fuzzy matching (role_id -> frozenset of words)
        self._name_word_sets: dict[str, frozenset[str]] = {}

    @property
    def role_documents(self) -> dict[str, JsonDict]:
        return self._role_documents

    @property
    def knowledge_base(self) -> JsonDict:
        return self._knowledge_base

    def get_all_role_names(self) -> list[str]:
        """Get all known role names from the knowledge base.

        Returns:
            List of all role names
        """
        # Get role names from role_details (the primary source)
        role_names = list(self._knowledge_base.get("role_details", {}).keys())

        # Also include names from role_documents if built
        for doc in self._role_documents.values():
            name = doc.get("role_name", "")
            if name and name not in role_names:
                role_names.append(name)

        return role_names

    def load_from_file(self) -> bool:
        """Load the pre-built knowledge base from disk.

        Returns True if loaded successfully, False otherwise.
        """
        if not KNOWLEDGE_BASE_PATH.exists():
            logger.info("Knowledge base not found. Run build_knowledge.py to generate it.")
            return False

        try:
            with KNOWLEDGE_BASE_PATH.open(encoding="utf-8") as f:
                self._knowledge_base = json.load(f)

            total_roles = self._knowledge_base.get("total_roles", 0)
            logger.info("Loaded knowledge base: %d roles", total_roles)
            return True
        except (OSError, json.JSONDecodeError) as e:
            logger.exception("Failed to load knowledge base: %s", e)
            return False

    def build_from_roles(
        self,
        roles: list[RoleDefinition],
    ) -> None:
        """Build searchable knowledge base from role definitions.

        Uses EFFECTIVE permissions from the precomputed cache and
        curated USE_CASE_PATTERNS to build document text for embeddings.
        Also precomputes word sets for fuzzy name matching.
        """
        # Import inside function to avoid circular import:
        # cache.refresh imports airecommender, airecommender imports knowledge_base
        from azurerbac.cache import app_cache

        # Reset state for fresh build
        self._role_documents = {}
        self._name_to_role_id = {}
        self._name_word_sets = {}

        # Build reverse lookup: role_name -> list of query patterns
        # Key insight: include the query patterns users will search for!
        role_to_patterns = _build_role_to_patterns_index()

        for role in roles:
            role_id = role.name  # GUID is in 'name' field
            role_name = role.properties.role_name
            description = role.properties.description

            # Get permissions from first permission entry (standard Azure format)
            first_perm = role.properties.permissions[0] if role.properties.permissions else None
            raw_actions = first_perm.actions if first_perm else []
            raw_data_actions = first_perm.data_actions if first_perm else []

            # Get EFFECTIVE permissions from precomputed cache
            cached_coverage = app_cache.get_role_coverage(role_id)
            if cached_coverage:
                effective_control, effective_data = cached_coverage
            else:
                # Fallback: filter out wildcards from raw actions
                effective_control = _filter_wildcards(raw_actions)
                effective_data = _filter_wildcards(raw_data_actions)

            # Get curated query patterns for this role
            curated_patterns = role_to_patterns.get(role_name, [])

            # Build action keywords from raw patterns (not expanded wildcards)
            all_actions = raw_actions + raw_data_actions
            limited_actions = [a for a in all_actions if "*" not in a]
            action_keywords = extract_operation_keywords(limited_actions)

            # Build document text with priority ordering for embeddings
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

            # Build indexes for fast lookup
            role_name_lower = role_name.lower()
            self._name_to_role_id[role_name_lower] = role_id
            # Precompute word set for fuzzy matching (O(1) per lookup later)
            self._name_word_sets[role_id] = frozenset(role_name_lower.split())

        logger.info("Built knowledge base with %d role documents", len(self._role_documents))

    def find_role_id_by_name(self, role_name: str) -> str | None:
        """Find role_id by matching role name.

        Uses strict matching with optimized fuzzy fallback.
        Returns role_id if found, None otherwise.
        """
        role_name_lower = role_name.lower().strip()

        # Early exit: skip obviously invalid role names
        if len(role_name_lower) < _MIN_ROLE_NAME_LENGTH:
            return None

        # Check for common invalid patterns (LLM hallucinations)
        if any(pattern in role_name_lower for pattern in _INVALID_NAME_PATTERNS):
            return None

        # O(1) exact match using pre-built index
        if role_name_lower in self._name_to_role_id:
            return self._name_to_role_id[role_name_lower]

        # Fuzzy match using precomputed word sets
        query_words = frozenset(role_name_lower.split())
        if not query_words:
            return None

        return self._find_best_fuzzy_match(query_words)

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

            if overlap_score >= _FUZZY_MATCH_THRESHOLD and overlap_score > best_score:
                best_match = role_id
                best_score = overlap_score

                # Early exit on perfect match
                if best_score >= 1.0:
                    return best_match

        return best_match


# =============================================================================
# Helper Functions
# =============================================================================


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
        doc_parts.append(" ".join(action_keywords[:_MAX_DOCUMENT_KEYWORDS]))

    return " ".join(doc_parts)


# Precompiled regex patterns for performance
_OPERATION_SPLIT_PATTERN: Final[re.Pattern[str]] = re.compile(r"[/.]")
_CAMEL_CASE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-z]+")

# Tokens to skip when extracting keywords
_SKIP_TOKENS: Final[frozenset[str]] = frozenset({"microsoft", "*", ""})


def extract_operation_keywords(operations: list[str]) -> list[str]:
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
