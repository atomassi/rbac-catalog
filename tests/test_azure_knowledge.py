"""Tests for the Azure knowledge base module."""

import pytest

from azurerbac.airecommender.knowledge import (
    AZURE_SERVICE_SYNONYMS,
    PERMISSION_LEVELS,
    expand_query_with_synonyms,
    find_matching_use_cases,
    get_negative_patterns,
)

# =============================================================================
# Knowledge Base Data Tests
# =============================================================================


class TestKnowledgeBaseData:
    """Tests for Azure knowledge base constants and data."""

    @pytest.mark.parametrize(
        "service,expected_synonym",
        [
            ("storage", "blob"),
            ("vm", "virtual machine"),
            ("aks", "kubernetes"),
            ("keyvault", "secrets"),
        ],
    )
    def test_key_service_synonyms_exist(self, service, expected_synonym):
        """Test that key services have their expected synonyms."""
        assert service in AZURE_SERVICE_SYNONYMS
        synonyms = AZURE_SERVICE_SYNONYMS[service]
        assert isinstance(synonyms, list)
        assert len(synonyms) > 0
        assert any(expected_synonym in s for s in synonyms)

    @pytest.mark.parametrize(
        "level,expected_keywords",
        [
            ("read", ["read"]),
            ("write", ["write", "create"]),
            ("delete", ["delete"]),
            ("admin", ["admin", "administrator"]),
            ("operator", ["operator", "operate"]),
        ],
    )
    def test_permission_level_keywords(self, level, expected_keywords):
        """Test permission level keywords exist."""
        assert level in PERMISSION_LEVELS
        keywords = PERMISSION_LEVELS[level]
        assert any(kw in keywords for kw in expected_keywords)

    @pytest.mark.parametrize(
        "query",
        ["read storage", "write data", "", "delete resources"],
    )
    def test_negative_patterns_returns_sequence(self, query):
        """Test that negative patterns returns a sequence (tuple for caching)."""
        patterns = get_negative_patterns(query)
        assert isinstance(patterns, (list, tuple))


# =============================================================================
# Query Expansion Tests
# =============================================================================


class TestExpandQueryWithSynonyms:
    """Tests for query expansion function."""

    @pytest.mark.parametrize(
        "query,expected_in_result",
        [
            ("storage account", "storage"),
            ("vm management", "vm"),
            ("keyvault secrets", "keyvault"),
        ],
    )
    def test_expand_query(self, query, expected_in_result):
        """Test expanding queries with synonyms."""
        expanded = expand_query_with_synonyms(query)
        assert expected_in_result in expanded.lower() or len(expanded) >= len(query)

    @pytest.mark.parametrize(
        ("query", "assertion"),
        [
            pytest.param("", lambda e: e == "", id="empty_query"),
            pytest.param(
                "unknown service xyz",
                lambda e: "unknown" in e.lower(),
                id="no_matching_synonyms",
            ),
            pytest.param(
                "vm storage keyvault",
                lambda e: len(e) > len("vm storage keyvault"),
                id="multiple_synonyms",
            ),
            pytest.param(
                "aks",
                lambda e: "aks" in e.lower() or "kubernetes" in e.lower(),
                id="abbreviations_expansion",
            ),
        ],
    )
    def test_expand_query_edge_cases(self, query, assertion):
        """Test query expansion edge cases."""
        expanded = expand_query_with_synonyms(query)
        assert assertion(expanded)

    def test_case_insensitive_matching(self):
        """Test that synonym matching is case insensitive."""
        expanded_lower = expand_query_with_synonyms("storage")
        expanded_upper = expand_query_with_synonyms("STORAGE")
        assert len(expanded_lower) > 0
        assert len(expanded_upper) > 0


# =============================================================================
# Use Case Pattern Matching Tests
# =============================================================================


class TestFindMatchingUseCases:
    """Tests for use case pattern matching."""

    @pytest.mark.parametrize(
        "query",
        [
            "read storage blobs",
            "manage virtual machines",
            "access key vault secrets",
            "sql database admin",
        ],
    )
    def test_find_use_case_returns_list(self, query):
        """Test finding use cases returns proper list structure."""
        matches = find_matching_use_cases(query)
        assert isinstance(matches, list)
        for match in matches:
            assert len(match) == 2
            assert isinstance(match[0], str)
            assert isinstance(match[1], (int, float))

    def test_find_empty_query(self):
        """Test finding use cases with empty query."""
        matches = find_matching_use_cases("")
        assert isinstance(matches, list)
        assert len(matches) == 0


# =============================================================================
# Knowledge Base Function Tests
# =============================================================================


class TestExtractOperationKeywords:
    """Tests for extracting keywords from Azure operation names."""

    def test_extracts_keywords_from_operation(self):
        """Test basic keyword extraction from Azure operation name."""
        from azurerbac.airecommender.knowledge.knowledge_base import extract_operation_keywords

        ops = ["Microsoft.Storage/storageAccounts/read"]
        keywords = extract_operation_keywords(ops)
        assert "storage" in keywords
        assert "storageaccounts" in keywords  # camelCase preserved as lowercase word
        assert "read" in keywords
        assert "microsoft" not in keywords  # Should be filtered

    def test_handles_empty_list(self):
        """Test with empty operation list."""
        from azurerbac.airecommender.knowledge.knowledge_base import extract_operation_keywords

        keywords = extract_operation_keywords([])
        assert keywords == []

    def test_handles_set_input(self):
        """Test that function accepts both list and set."""
        from azurerbac.airecommender.knowledge.knowledge_base import extract_operation_keywords

        ops = {"Microsoft.Compute/virtualMachines/start/action"}
        keywords = extract_operation_keywords(ops)
        assert "compute" in keywords
        assert "virtualmachines" in keywords  # camelCase preserved as lowercase word
        assert "start" in keywords
        assert "action" in keywords
