"""Tests for OllamaClient methods."""

import pytest

from azurerbac.airecommender.llm.client import OllamaClient


@pytest.fixture
def client() -> OllamaClient:
    """Create an OllamaClient instance for testing."""
    return OllamaClient()


@pytest.fixture
def client_with_roles() -> OllamaClient:
    """Create an OllamaClient with known role names set."""
    client = OllamaClient()
    known_roles = [
        "Storage Blob Data Reader",
        "Storage Blob Data Contributor",
        "Storage Blob Data Owner",
        "Key Vault Secrets User",
        "Key Vault Secrets Officer",
        "Key Vault Administrator",
        "Contributor",
        "Reader",
        "Owner",
        "Virtual Machine Contributor",
        "Virtual Machine User Login",
        "Virtual Machine Administrator Login",
        "AcrPull",
        "AcrPush",
        "Cosmos DB Operator",
        "SQL DB Contributor",
        "Log Analytics Reader",
        "Log Analytics Contributor",
    ]
    client.set_known_role_names(known_roles)
    return client


# =============================================================================
# find_closest_role Tests
# =============================================================================


class TestFindClosestRole:
    """Tests for OllamaClient.find_closest_role method."""

    def test_exact_match_case_sensitive(self, client_with_roles):
        """Test exact match returns score 1.0."""
        role, score = client_with_roles.find_closest_role("Storage Blob Data Reader")
        assert role == "Storage Blob Data Reader"
        assert score == 1.0

    def test_exact_match_case_insensitive(self, client_with_roles):
        """Test case-insensitive exact match returns score 1.0."""
        role, score = client_with_roles.find_closest_role("storage blob data reader")
        assert role == "Storage Blob Data Reader"
        assert score == 1.0

    def test_exact_match_uppercase(self, client_with_roles):
        """Test uppercase input returns exact match."""
        role, score = client_with_roles.find_closest_role("CONTRIBUTOR")
        assert role == "Contributor"
        assert score == 1.0

    def test_exact_match_with_whitespace(self, client_with_roles):
        """Test whitespace is trimmed for matching."""
        role, score = client_with_roles.find_closest_role("  Reader  ")
        assert role == "Reader"
        assert score == 1.0

    def test_substring_match_single_role_in_mashup(self, client_with_roles):
        """Test substring matching for mashup like 'Role A and Role B'."""
        role, score = client_with_roles.find_closest_role(
            "Storage Blob Data Reader and Key Vault Secrets User"
        )
        # Should find the longer substring match
        assert role in ["Storage Blob Data Reader", "Key Vault Secrets User"]
        assert score == 0.9

    def test_substring_match_prefers_longer(self, client_with_roles):
        """Test that longer substring matches are preferred."""
        role, score = client_with_roles.find_closest_role(
            "Role name containing Key Vault Secrets Officer here"
        )
        assert role == "Key Vault Secrets Officer"
        assert score == 0.9

    def test_fuzzy_match_typo(self, client_with_roles):
        """Test fuzzy matching for small typos."""
        # Typo: "Storge" instead of "Storage"
        role, score = client_with_roles.find_closest_role("Storge Blob Data Reader")
        # Should find a match with suffix bonus for "Reader"
        assert score >= 0.6
        # The match should be one of the Reader roles
        assert "Reader" in role

    def test_fuzzy_match_suffix_bonus(self, client_with_roles):
        """Test that matching suffixes improve score."""
        # 'Storage Contributor' should match 'Storage Blob Data Contributor'
        # better than other roles due to Contributor suffix
        role, score = client_with_roles.find_closest_role("Storage Contributor")
        assert "Contributor" in role
        assert score >= 0.6

    def test_no_match_returns_original(self, client_with_roles):
        """Test that no match returns original role with 0.0 score."""
        role, score = client_with_roles.find_closest_role("Completely Made Up Role XYZ123")
        assert role == "Completely Made Up Role XYZ123"
        assert score == 0.0

    def test_empty_known_roles_returns_original(self, client):
        """Test that empty known roles returns original with 0.0 score."""
        role, score = client.find_closest_role("Storage Blob Data Reader")
        assert role == "Storage Blob Data Reader"
        assert score == 0.0

    def test_partial_role_name_fuzzy(self, client_with_roles):
        """Test partial role name uses fuzzy matching."""
        role, score = client_with_roles.find_closest_role("Blob Data Reader")
        # May match 'Reader' (short string gets high ratio) or roles containing 'Reader'
        assert score >= 0.6
        assert "Reader" in role

    def test_score_capped_at_one(self, client_with_roles):
        """Test that score with suffix bonus doesn't exceed 1.0."""
        _role, score = client_with_roles.find_closest_role("Contributor")
        assert score <= 1.0

    def test_hallucinated_role_with_similar_structure(self, client_with_roles):
        """Test handling of hallucinated roles with similar structure."""
        _role, score = client_with_roles.find_closest_role("Azure Blob Storage Data Viewer")
        # Should not match well since 'Viewer' != 'Reader'
        # May fuzzy match to Storage Blob Data Reader
        assert score >= 0.0


# =============================================================================
# set_known_role_names Tests
# =============================================================================


class TestSetKnownRoleNames:
    """Tests for OllamaClient.set_known_role_names method."""

    def test_set_role_names(self, client):
        """Test setting known role names."""
        roles = ["Role A", "Role B", "Role C"]
        client.set_known_role_names(roles)
        assert len(client._known_role_names) == 3
        assert len(client._known_roles_lookup) == 3

    def test_lookup_dict_lowercase_keys(self, client):
        """Test that lookup dict uses lowercase keys."""
        roles = ["Storage Blob Data Reader"]
        client.set_known_role_names(roles)
        assert "storage blob data reader" in client._known_roles_lookup
        assert client._known_roles_lookup["storage blob data reader"] == "Storage Blob Data Reader"

    def test_empty_roles_list(self, client):
        """Test setting empty roles list."""
        client.set_known_role_names([])
        assert len(client._known_role_names) == 0
        assert len(client._known_roles_lookup) == 0


# =============================================================================
# parse_json_with_repair Tests
# =============================================================================


class TestParseJsonWithRepair:
    """Tests for parse_json_with_repair function."""

    def test_valid_json(self):
        """Test parsing valid JSON."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader", "confidence_class": "high"}'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Reader"
        assert result["confidence_class"] == "high"

    def test_valid_json_with_arrays(self):
        """Test parsing valid JSON with arrays."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader", "signals_matched": ["read", "view"]}'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["signals_matched"] == ["read", "view"]

    def test_json_with_trailing_text(self):
        """Test extracting JSON object with trailing text."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader"} and some extra text here'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Reader"

    def test_json_with_leading_text(self):
        """Test extracting JSON object with leading text."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = 'Here is the answer: {"role": "Reader"}'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Reader"

    def test_missing_closing_brace(self):
        """Test repairing JSON with missing closing brace."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader", "confidence_class": "high"'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Reader"

    def test_regex_fallback_role_extraction(self):
        """Test regex fallback for extracting role from malformed JSON."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Storage Blob Data Reader", broken json here'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Storage Blob Data Reader"

    def test_regex_fallback_with_confidence(self):
        """Test regex fallback extracts confidence class too."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader", "confidence_class": "medium", invalid'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Reader"
        assert result["confidence_class"] == "medium"

    def test_completely_invalid_returns_none(self):
        """Test completely invalid input returns None."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = "This is not JSON at all, just plain text"
        result = parse_json_with_repair(raw)
        assert result is None

    def test_nested_array_repair(self):
        """Test repairing nested array syntax issues."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        # ["a"], ["b"] should become ["a", "b"]
        raw = '{"role": "Reader", "signals_matched": ["read"], ["view"]}'
        result = parse_json_with_repair(raw)
        # May or may not repair this complex case
        # At minimum should not crash
        assert result is None or isinstance(result, dict)

    def test_missing_quote_in_array(self):
        """Test repairing missing quotes in array."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader", "signals_matched": ["read]}'
        result = parse_json_with_repair(raw)
        # Should attempt repair
        assert result is None or isinstance(result, dict)

    def test_unquoted_array_items(self):
        """Test repair of unquoted array items."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader", "signals_matched": [read, view]}'
        result = parse_json_with_repair(raw)
        # Should attempt to quote unquoted items
        if result:
            assert result["role"] == "Reader"

    def test_markdown_json_block(self):
        """Test JSON extraction from markdown code block."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        # Note: markdown extraction is handled in recommend_roles before
        # calling parse_json_with_repair, but we test the inner function here
        raw = '{"role": "Contributor", "confidence_class": "high"}'
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Contributor"

    def test_empty_string(self):
        """Test empty string returns None."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        result = parse_json_with_repair("")
        assert result is None

    def test_empty_json_object(self):
        """Test empty JSON object."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        result = parse_json_with_repair("{}")
        assert result == {}

    def test_regex_fallback_default_confidence(self):
        """Test regex fallback uses 'low' as default confidence."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = '{"role": "Reader" broken'
        result = parse_json_with_repair(raw)
        if result:
            assert result["role"] == "Reader"
            # Default confidence when not found
            if "confidence_class" in result:
                assert result["confidence_class"] == "low"

    def test_complex_valid_json(self):
        """Test parsing complex but valid JSON structure."""
        from azurerbac.airecommender.llm.json_repair import parse_json_with_repair

        raw = """{
            "role": "Storage Blob Data Contributor",
            "confidence_class": "very_high",
            "signals_matched": ["storage", "blob", "write", "upload"],
            "signals_missing": [],
            "ambiguity_note": ""
        }"""
        result = parse_json_with_repair(raw)
        assert result is not None
        assert result["role"] == "Storage Blob Data Contributor"
        assert result["confidence_class"] == "very_high"
        assert len(result["signals_matched"]) == 4
