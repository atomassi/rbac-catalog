"""Enhanced TF-IDF + BM25 recommender for improved accuracy.

This module implements an optimized text-based role recommendation system
targeting 90%+ accuracy through:
1. BM25 ranking (better than pure TF-IDF for short queries)
2. Enhanced document representation with role metadata
3. Multi-signal scoring combining TF-IDF, pattern matching, and fuzzy matching
4. Query expansion with Azure-specific synonyms
5. Custom term weighting for role names and Azure services

Performance: Uses scipy sparse matrices for memory-efficient BM25 scoring.
"""

import logging
import re
from collections import Counter
from typing import Final

import numpy as np
from scipy.sparse import csr_matrix

from azurerbac.airecommender.engines.config import DEFAULT_TFIDF_WEIGHTS
from azurerbac.airecommender.knowledge import (
    ABBREVIATIONS,
    AZURE_SERVICE_SYNONYMS,
    PERMISSION_LEVELS,
    expand_query_with_synonyms,
    find_matching_use_cases,
    get_negative_patterns,
)
from azurerbac.azure.models import RoleDefinition

logger = logging.getLogger(__name__)

# Precompiled regex for tokenization
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[a-zA-Z0-9]+")

# Type aliases
type Document = tuple[str, str]
type SearchResult = tuple[str, float]


def _action_to_keywords(action: str) -> str:
    """Convert Azure action to searchable keywords."""
    return action.replace("Microsoft.", "").replace("/", " ").replace("*", "all")


class BM25Index:
    """BM25 ranking algorithm using sparse matrices for memory efficiency."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.doc_count = 0
        self.avg_doc_len = 0.0
        self.doc_ids: list[str] = []
        self.term_to_idx: dict[str, int] = {}  # term -> column index
        self.idf_values: np.ndarray | None = None  # shape: (n_terms,)
        self.tf_matrix: csr_matrix | None = None  # sparse (n_docs, n_terms)
        self.norm_factors: np.ndarray | None = None  # shape: (n_docs,) precomputed denominators

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into lowercase terms."""
        tokens = _TOKEN_PATTERN.findall(text.lower())
        return [t for t in tokens if len(t) >= 2]

    def index(self, documents: list[Document]) -> None:
        """Build the BM25 index with sparse CSR matrix for memory efficiency."""
        self.doc_count = len(documents)
        if self.doc_count == 0:
            return

        self.doc_ids = []
        doc_lens: list[int] = []
        doc_term_freqs: list[dict[str, int]] = []
        term_doc_counts: dict[str, int] = {}  # term -> number of docs containing term

        # First pass: tokenize and collect vocabulary
        for doc_id, text in documents:
            tokens = self._tokenize(text)
            self.doc_ids.append(doc_id)
            doc_lens.append(len(tokens))

            term_freqs = Counter(tokens)
            doc_term_freqs.append(dict(term_freqs))

            for term in term_freqs:
                term_doc_counts[term] = term_doc_counts.get(term, 0) + 1

        # Build term vocabulary index
        self.term_to_idx = {term: idx for idx, term in enumerate(term_doc_counts.keys())}
        n_terms = len(self.term_to_idx)

        self.avg_doc_len = sum(doc_lens) / self.doc_count

        # Compute IDF values: log((N - df + 0.5) / (df + 0.5) + 1)
        self.idf_values = np.zeros(n_terms, dtype=np.float32)
        for term, df in term_doc_counts.items():
            idx = self.term_to_idx[term]
            self.idf_values[idx] = np.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)

        # Build sparse CSR term frequency matrix
        # Collect COO format data for efficient CSR construction
        rows: list[int] = []
        cols: list[int] = []
        data: list[float] = []
        for doc_idx, term_freqs in enumerate(doc_term_freqs):
            for term, freq in term_freqs.items():
                rows.append(doc_idx)
                cols.append(self.term_to_idx[term])
                data.append(float(freq))

        self.tf_matrix = csr_matrix(
            (data, (rows, cols)),
            shape=(self.doc_count, n_terms),
            dtype=np.float32,
        )

        # Precompute normalization factors: 1 - b + b * (doc_len / avg_doc_len)
        doc_lens_arr = np.array(doc_lens, dtype=np.float32)
        self.norm_factors = 1 - self.b + self.b * (doc_lens_arr / self.avg_doc_len)

        # Log memory savings
        dense_size = self.doc_count * n_terms * 4  # float32 = 4 bytes
        sparse_size = (
            self.tf_matrix.data.nbytes
            + self.tf_matrix.indices.nbytes
            + self.tf_matrix.indptr.nbytes
        )
        savings_pct = (1 - sparse_size / dense_size) * 100 if dense_size > 0 else 0

        logger.info(
            "Built BM25 index: %d docs, %d terms (sparse CSR, %.1f%% memory saved)",
            self.doc_count,
            n_terms,
            savings_pct,
        )

    def search(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """Search the index using sparse matrix BM25 scoring."""
        query_tokens = self._tokenize(query)
        if (
            not query_tokens
            or self.tf_matrix is None
            or self.idf_values is None
            or self.norm_factors is None
        ):
            return []

        # Get term indices for query terms that exist in vocabulary
        query_term_indices = [
            self.term_to_idx[term] for term in query_tokens if term in self.term_to_idx
        ]
        if not query_term_indices:
            return []

        # Extract columns for query terms from sparse matrix -> dense for small subset
        # CSR column slicing returns sparse, convert to dense for vectorized ops
        tf_subset = self.tf_matrix[:, query_term_indices].toarray()  # (n_docs, n_query_terms)
        idf_subset = self.idf_values[query_term_indices]  # shape: (n_query_terms,)

        # BM25 formula vectorized:
        # score = sum_terms(idf * (tf * (k1 + 1)) / (tf + k1 * norm_factor))
        # numerator: tf * (k1 + 1)
        numerator = tf_subset * (self.k1 + 1)

        # denominator: tf + k1 * norm_factor (broadcast norm_factors to all terms)
        # norm_factors: (n_docs,) -> (n_docs, 1) for broadcasting
        denominator = tf_subset + self.k1 * self.norm_factors[:, np.newaxis]

        # Avoid division by zero (shouldn't happen with non-negative tf)
        denominator = np.maximum(denominator, 1e-10)

        # Per-term scores: (n_docs, n_query_terms)
        term_scores = idf_subset * (numerator / denominator)

        # Sum across query terms to get document scores: (n_docs,)
        doc_scores = term_scores.sum(axis=1)

        # Get top-k using argpartition for O(n) complexity
        if top_k >= self.doc_count:
            top_indices = np.argsort(doc_scores)[::-1]
        else:
            # argpartition gives us the top-k indices (unsorted)
            partition_indices = np.argpartition(doc_scores, -top_k)[-top_k:]
            # Sort just those indices by score
            top_indices = partition_indices[np.argsort(doc_scores[partition_indices])[::-1]]

        # Filter out zero scores and build result
        results = []
        for idx in top_indices:
            score = doc_scores[idx]
            if score > 0:
                results.append((self.doc_ids[idx], float(score)))

        return results[:top_k]


class EnhancedTFIDFRecommender:
    """Enhanced role recommender using BM25 + pattern matching + fuzzy matching."""

    def __init__(self) -> None:
        self._bm25_index = BM25Index()
        self._role_data: dict[str, RoleDefinition] = {}  # role_id -> role metadata
        # lowercase name -> (original_name, role_id) for lookup and iteration
        self._role_name_to_id: dict[str, tuple[str, str]] = {}
        self._initialized = False

    def _build_document(self, role: RoleDefinition) -> str:
        """Build an enhanced document representation for a role.

        Creates a rich text document that captures:
        - Role name (repeated for emphasis)
        - Description
        - Actions/permissions as readable text
        - Related keywords
        """
        name = role.properties.role_name
        description = role.properties.description

        # Start with name (repeated for emphasis)
        parts = [name, name.lower(), name]

        # Add description
        if description:
            parts.append(description)

        # Extract action keywords from permissions
        for perm in role.properties.permissions:
            parts.extend(_action_to_keywords(action) for action in perm.actions)
            parts.extend(_action_to_keywords(action) for action in perm.data_actions)

        # Add Azure service synonyms if role name matches
        name_lower = name.lower()
        for service, synonyms in AZURE_SERVICE_SYNONYMS.items():
            if service in name_lower:
                parts.extend(synonyms[:3])

        # Add permission level keywords
        for level, keywords in PERMISSION_LEVELS.items():
            if level in name_lower or level in description.lower():
                parts.extend(keywords[:3])

        return " ".join(parts)

    def initialize(self, roles: list[RoleDefinition]) -> None:
        """Initialize the recommender with role data.

        Args:
            roles: List of RoleDefinition Pydantic models
        """
        if self._initialized:
            return

        logger.info("Initializing Enhanced TF-IDF recommender with %d roles...", len(roles))

        documents = []

        for role in roles:
            role_id = role.name  # GUID is in 'name' field
            role_name = role.properties.role_name

            if not role_id or not role_name:
                continue

            role_name_lower = role_name.lower()
            self._role_data[role_id] = role
            self._role_name_to_id[role_name_lower] = (role_name, role_id)

            # Build document
            doc_text = self._build_document(role)
            documents.append((role_id, doc_text))

        # Build BM25 index
        self._bm25_index.index(documents)
        self._initialized = True

        logger.info("Enhanced TF-IDF initialized: %d roles indexed", len(self._role_data))

    def _get_role_name(self, role_id: str) -> str:
        role = self._role_data.get(role_id)
        return role.properties.role_name if role else role_id

    def _score_pattern_match(self, query: str) -> dict[str, float]:
        """Score roles based on USE_CASE_PATTERNS matching.

        Returns:
            Dict of role_name -> score (0-1)
        """
        matches = find_matching_use_cases(query)
        return dict(matches)

    def _score_name_match(self, query: str) -> dict[str, float]:
        """Score roles based on direct name matching.

        Checks if query contains role name or vice versa.
        """
        query_lower = query.lower()
        query_words = set(query_lower.split())
        scores = {}

        for role_name_lower, (role_name, _role_id) in self._role_name_to_id.items():
            role_words = set(role_name_lower.split())

            # Exact match
            if role_name_lower in query_lower or query_lower in role_name_lower:
                scores[role_name] = 1.0
                continue

            # Word overlap
            overlap = len(query_words & role_words)
            if overlap > 0:
                # Score based on overlap ratio
                score = overlap / max(len(query_words), len(role_words))
                if score >= 0.3:
                    scores[role_name] = score

        return scores

    def _score_fuzzy_match(self, query: str) -> dict[str, float]:
        """Score roles using fuzzy/approximate matching.

        Handles typos and variations using ABBREVIATIONS from azure_knowledge.
        """
        scores: dict[str, float] = {}
        query_lower = query.lower()

        # Expand abbreviations in query
        expanded_query = query_lower
        for abbr, expansion in ABBREVIATIONS.items():
            if abbr in expanded_query.split():
                expanded_query = expanded_query.replace(abbr, expansion)

        # Check for partial matches in role names
        for role_name_lower, (role_name, _role_id) in self._role_name_to_id.items():
            # Check if expanded query keywords match role
            for word in expanded_query.split():
                if len(word) >= 4 and word in role_name_lower:
                    scores[role_name] = max(scores.get(role_name, 0), 0.6)

        return scores

    def recommend(self, query: str, top_k: int = 10) -> list[tuple[str, str, float, dict]]:
        """Get role recommendations for a query.

        Uses multi-signal scoring:
        1. BM25 text search
        2. Pattern matching against USE_CASE_PATTERNS
        3. Direct name matching
        4. Fuzzy matching for typos/abbreviations
        5. Negative pattern penalties for unsuitable roles

        Args:
            query: Natural language query
            top_k: Number of recommendations to return

        Returns:
            List of (role_id, role_name, score, metadata) tuples
        """
        if not self._initialized:
            raise RuntimeError("Recommender not initialized. Call initialize() first.")

        # Expand query with synonyms
        expanded_query = expand_query_with_synonyms(query)

        # Get scores from each signal
        bm25_results = self._bm25_index.search(expanded_query, top_k=50)
        pattern_scores = self._score_pattern_match(query)
        name_scores = self._score_name_match(query)
        fuzzy_scores = self._score_fuzzy_match(query)

        # Get roles that should be penalized for this query
        penalized_roles = set(get_negative_patterns(query))

        # Normalize BM25 scores to 0-1 range
        bm25_scores = {}
        if bm25_results:
            max_bm25 = max(score for _, score in bm25_results)
            if max_bm25 > 0:
                bm25_scores = {
                    self._get_role_name(role_id): score / max_bm25
                    for role_id, score in bm25_results
                }

        # Combine all scores
        all_roles = (
            set(bm25_scores.keys())
            | set(pattern_scores.keys())
            | set(name_scores.keys())
            | set(fuzzy_scores.keys())
        )

        combined_scores = []
        for role_name in all_roles:
            bm25_score = bm25_scores.get(role_name, 0)
            pattern_score = pattern_scores.get(role_name, 0)
            name_score = name_scores.get(role_name, 0)
            fuzzy_score = fuzzy_scores.get(role_name, 0)

            # Weighted combination using centralized config
            weights = DEFAULT_TFIDF_WEIGHTS
            final_score = (
                weights.bm25 * bm25_score
                + weights.pattern * pattern_score
                + weights.name_match * name_score
                + weights.fuzzy * fuzzy_score
            )

            # Boost if multiple signals agree
            signal_count = sum(
                1 for s in [bm25_score, pattern_score, name_score, fuzzy_score] if s > 0
            )
            if signal_count >= 2:
                final_score *= 1.1
            if signal_count >= 3:
                final_score *= 1.15

            # Apply penalty for roles in negative patterns
            is_penalized = role_name in penalized_roles
            if is_penalized:
                # Heavy penalty - reduce score to near zero
                # This role has notActions or limitations making it unsuitable
                final_score *= 0.05

            metadata = {
                "bm25_score": round(bm25_score, 3),
                "pattern_score": round(pattern_score, 3),
                "name_score": round(name_score, 3),
                "fuzzy_score": round(fuzzy_score, 3),
                "signals": signal_count,
                "penalized": is_penalized,
            }

            entry = self._role_name_to_id.get(role_name.lower())
            role_id = entry[1] if entry else None
            combined_scores.append((role_id, role_name, final_score, metadata))

        # Sort by score descending
        combined_scores.sort(key=lambda x: x[2], reverse=True)

        return combined_scores[:top_k]
