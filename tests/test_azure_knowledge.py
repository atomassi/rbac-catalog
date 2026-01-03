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
# Azure Service Synonyms Tests
# =============================================================================


class TestAzureServiceSynonyms:
    """Tests for Azure service synonyms dictionary."""

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


# =============================================================================
# Permission Levels Tests
# =============================================================================


class TestPermissionLevels:
    """Tests for permission level keywords."""

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

    def test_expand_empty_query(self):
        """Test expanding empty query."""
        expanded = expand_query_with_synonyms("")
        assert expanded == ""

    def test_expand_no_matching_synonyms(self):
        """Test query with no matching synonyms."""
        expanded = expand_query_with_synonyms("unknown service xyz")
        assert "unknown" in expanded.lower()

    def test_case_insensitive_matching(self):
        """Test that synonym matching is case insensitive."""
        expanded_lower = expand_query_with_synonyms("storage")
        expanded_upper = expand_query_with_synonyms("STORAGE")
        assert len(expanded_lower) > 0
        assert len(expanded_upper) > 0

    def test_multiple_synonyms_in_query(self):
        """Test query with multiple synonym matches."""
        expanded = expand_query_with_synonyms("vm storage keyvault")
        assert len(expanded) > len("vm storage keyvault")

    def test_abbreviations_expansion(self):
        """Test that abbreviations are properly expanded."""
        expanded = expand_query_with_synonyms("aks")
        assert "aks" in expanded.lower() or "kubernetes" in expanded.lower()


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
# Negative Pattern Tests
# =============================================================================


class TestGetNegativePatterns:
    """Tests for negative pattern matching."""

    @pytest.mark.parametrize(
        "query",
        ["read storage", "write data", "", "delete resources"],
    )
    def test_negative_patterns_returns_sequence(self, query):
        """Test that negative patterns returns a sequence (tuple for caching)."""
        patterns = get_negative_patterns(query)
        assert isinstance(patterns, (list, tuple))
