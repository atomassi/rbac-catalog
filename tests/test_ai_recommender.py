"""Tests for the AI recommender module."""

import pytest

from azurerbac.airecommender.ai_recommender import (
    AIRecommendation,
    AIRoleRecommender,
    EngineNotAvailableError,
)
from azurerbac.airecommender.modes import RecommenderMode

# =============================================================================
# AIRecommendation Tests
# =============================================================================


class TestAIRecommendation:
    """Tests for AIRecommendation dataclass."""

    def test_create_recommendation_minimal(self):
        """Test creating an AI recommendation with minimal fields."""
        rec = AIRecommendation(
            role_id="test-guid",
            role_name="Storage Blob Data Reader",
            description="Read blob data",
            score=0.95,
        )
        assert rec.role_id == "test-guid"
        assert rec.role_name == "Storage Blob Data Reader"
        assert rec.score == 0.95
        assert rec.matched_keywords == []  # Default is empty list, not None

    def test_recommendation_with_optional_fields(self):
        """Test recommendation with all optional fields."""
        rec = AIRecommendation(
            role_id="test-guid",
            role_name="Storage Blob Data Reader",
            description="Read blob data",
            score=0.95,
            matched_keywords=["storage", "blob", "read"],
        )
        assert rec.matched_keywords == ["storage", "blob", "read"]

    def test_recommendation_to_dict(self):
        """Test converting recommendation to dictionary."""
        rec = AIRecommendation(
            role_id="test-guid",
            role_name="Test Role",
            description="Test description",
            score=0.8567,
            matched_keywords=["test"],
        )
        result = rec.to_dict()

        assert result["role_id"] == "test-guid"
        assert result["role_name"] == "Test Role"
        assert result["description"] == "Test description"
        assert result["score"] == 0.857  # Rounded to 3 decimals
        assert result["matched_keywords"] == ["test"]

    def test_recommendation_score_rounding(self):
        """Test that scores are rounded to 3 decimal places."""
        rec = AIRecommendation(
            role_id="test",
            role_name="Test",
            description="Test",
            score=0.123456789,
        )
        result = rec.to_dict()
        assert result["score"] == 0.123


# =============================================================================
# RecommenderMode Tests
# =============================================================================


class TestRecommenderMode:
    """Tests for RecommenderMode enum."""

    @pytest.mark.parametrize(
        "string_val,expected",
        [
            ("tfidf", RecommenderMode.TFIDF),
            ("llm", RecommenderMode.LLM),
            ("rag", RecommenderMode.RAG),
            ("hybrid", RecommenderMode.HYBRID),
            ("TFIDF", RecommenderMode.TFIDF),  # Case insensitive
            ("TfIdf", RecommenderMode.TFIDF),
            ("invalid", None),
            (None, None),
            (" tfidf", None),  # Whitespace not stripped
            ("tfidf ", None),
        ],
    )
    def test_from_string(self, string_val, expected):
        """Test converting string to RecommenderMode."""
        assert RecommenderMode.from_string(string_val) == expected

    @pytest.mark.parametrize(
        "string_val,expected",
        [
            ("tfidf", True),
            ("llm", True),
            ("rag", True),
            ("hybrid", True),
            ("invalid", False),
            (None, False),
        ],
    )
    def test_is_valid(self, string_val, expected):
        """Test mode validation."""
        assert RecommenderMode.is_valid(string_val) is expected

    @pytest.mark.parametrize(
        "mode,requires_llm",
        [
            (RecommenderMode.TFIDF, False),
            (RecommenderMode.LLM, True),
            (RecommenderMode.RAG, True),
            (RecommenderMode.HYBRID, True),
        ],
    )
    def test_requires_llm(self, mode, requires_llm):
        """Test which modes require LLM connection."""
        assert mode.requires_llm is requires_llm

    @pytest.mark.parametrize(
        "mode,requires_embeddings",
        [
            (RecommenderMode.TFIDF, False),
            (RecommenderMode.LLM, False),
            (RecommenderMode.RAG, True),
            (RecommenderMode.HYBRID, True),
        ],
    )
    def test_requires_embeddings(self, mode, requires_embeddings):
        """Test which modes require embedding model."""
        assert mode.requires_embeddings is requires_embeddings

    def test_all_modes_have_description(self):
        """Test mode descriptions are meaningful."""
        for mode in RecommenderMode:
            assert len(mode.description) > 10
            assert isinstance(mode.description, str)


class TestRecommenderModeUsage:
    """Tests for typical usage patterns."""

    def test_mode_as_dictionary_key(self):
        """Test mode can be used as dictionary key."""
        config = {
            RecommenderMode.TFIDF: {"latency": 50},
            RecommenderMode.LLM: {"latency": 1200},
        }
        assert config[RecommenderMode.LLM]["latency"] == 1200

    @pytest.mark.parametrize(
        "invalid_string",
        ["rag!", "tf-idf", "llm*", "hybrid "],
    )
    def test_from_string_rejects_special_chars(self, invalid_string):
        """Test from_string rejects special characters."""
        assert RecommenderMode.from_string(invalid_string) is None


# =============================================================================
# AIRoleRecommender Tests
# =============================================================================


class TestAIRoleRecommender:
    """Tests for AIRoleRecommender class."""

    def test_compute_roles_hash_deterministic(self):
        """Test that hash is deterministic for same input."""
        recommender = AIRoleRecommender()
        roles = [
            {"name": "role-1", "properties": {"roleName": "Role One"}},
            {"name": "role-2", "properties": {"roleName": "Role Two"}},
        ]
        hash1 = recommender._compute_roles_hash(roles)
        hash2 = recommender._compute_roles_hash(roles)
        assert hash1 == hash2

    def test_compute_roles_hash_order_independent(self):
        """Test that hash is same regardless of role order."""
        recommender = AIRoleRecommender()
        roles_a = [
            {"name": "role-1", "properties": {"roleName": "Role One"}},
            {"name": "role-2", "properties": {"roleName": "Role Two"}},
        ]
        roles_b = [
            {"name": "role-2", "properties": {"roleName": "Role Two"}},
            {"name": "role-1", "properties": {"roleName": "Role One"}},
        ]
        assert recommender._compute_roles_hash(roles_a) == recommender._compute_roles_hash(roles_b)

    def test_compute_roles_hash_changes_with_different_roles(self):
        """Test that hash changes when roles are different."""
        recommender = AIRoleRecommender()
        roles_a = [{"name": "role-1"}]
        roles_b = [{"name": "role-2"}]
        assert recommender._compute_roles_hash(roles_a) != recommender._compute_roles_hash(roles_b)

    def test_compute_roles_hash_detects_added_role(self):
        """Test that hash changes when a role is added."""
        recommender = AIRoleRecommender()
        roles_a = [{"name": "role-1"}]
        roles_b = [{"name": "role-1"}, {"name": "role-2"}]
        assert recommender._compute_roles_hash(roles_a) != recommender._compute_roles_hash(roles_b)

    def test_compute_roles_hash_empty_list(self):
        """Test hash of empty roles list."""
        recommender = AIRoleRecommender()
        hash_empty = recommender._compute_roles_hash([])
        assert hash_empty is not None
        assert len(hash_empty) == 32  # Full MD5 hash

    def test_compute_roles_hash_uses_name_or_id(self):
        """Test that hash uses 'name' field, falling back to 'id'."""
        recommender = AIRoleRecommender()
        roles_with_name = [{"name": "role-1"}]
        roles_with_id = [{"id": "role-1"}]
        # Should produce same hash since both resolve to "role-1"
        assert recommender._compute_roles_hash(roles_with_name) == recommender._compute_roles_hash(
            roles_with_id
        )

    def test_compute_roles_hash_length(self):
        """Test that hash is exactly 32 characters (full MD5)."""
        recommender = AIRoleRecommender()
        roles = [{"name": f"role-{i}"} for i in range(100)]
        hash_value = recommender._compute_roles_hash(roles)
        assert len(hash_value) == 32
        assert hash_value.isalnum()

    def test_initialize_creates_knowledge_base(self):
        """Test that initialize creates a knowledge base."""
        recommender = AIRoleRecommender()
        test_roles = [
            {
                "id": "test-guid",
                "role_name": "Test Role",
                "description": "Test description",
                "permissions": [{"actions": ["Microsoft.Test/*/read"]}],
            }
        ]
        recommender.initialize(test_roles)
        assert recommender._knowledge_base is not None
        assert recommender._initialized is True


# =============================================================================
# EngineNotAvailableError Tests
# =============================================================================


class TestEngineNotAvailableError:
    """Tests for EngineNotAvailableError exception."""

    def test_error_creation(self):
        """Test creating the error with all fields."""
        error = EngineNotAvailableError(
            engine_name="RAG Engine",
            mode="rag",
            missing_components=["sentence-transformers"],
        )
        assert error.engine_name == "RAG Engine"
        assert error.mode == "rag"
        assert error.missing_components == ["sentence-transformers"]

    def test_error_message_is_user_friendly(self):
        """Test that error message is user-friendly."""
        error = EngineNotAvailableError(
            engine_name="RAG Engine",
            mode="rag",
            missing_components=["sentence-transformers"],
        )
        message = str(error)
        # Should not contain technical details
        assert "sentence-transformers" not in message
        # Should suggest alternatives
        assert "TF-IDF" in message
        assert "LLM" in message

    def test_error_with_multiple_missing_components(self):
        """Test error with multiple missing components."""
        error = EngineNotAvailableError(
            engine_name="Hybrid Engine",
            mode="hybrid",
            missing_components=["Ollama LLM", "sentence-transformers"],
        )
        assert len(error.missing_components) == 2
        assert "Ollama LLM" in error.missing_components

    def test_error_with_empty_missing_components(self):
        """Test error with empty missing components list."""
        error = EngineNotAvailableError(
            engine_name="Test Engine",
            mode="test",
            missing_components=[],
        )
        assert error.missing_components == []
        assert str(error)  # Should not crash

    def test_error_is_exception(self):
        """Test that error is a proper Exception."""
        error = EngineNotAvailableError(
            engine_name="Test",
            mode="test",
            missing_components=[],
        )
        assert isinstance(error, Exception)

    def test_error_can_be_raised_and_caught(self):
        """Test that error can be raised and caught."""
        with pytest.raises(EngineNotAvailableError) as exc_info:
            raise EngineNotAvailableError(
                engine_name="RAG Engine",
                mode="rag",
                missing_components=["sentence-transformers"],
            )
        assert exc_info.value.mode == "rag"


class TestRecommenderEngineAvailability:
    """Tests for recommender engine availability checking."""

    @pytest.fixture
    def initialized_recommender(self):
        """Create an initialized recommender with minimal roles."""
        recommender = AIRoleRecommender()
        test_roles = [
            {
                "id": "test-guid",
                "role_name": "Test Role",
                "description": "Test description",
                "permissions": [{"actions": ["Microsoft.Test/*/read"]}],
            }
        ]
        recommender.initialize(test_roles)
        return recommender

    def test_tfidf_mode_always_available(self, initialized_recommender):
        """Test that TF-IDF mode is always available."""
        # TF-IDF should work without LLM or embeddings
        recommendations, mode = initialized_recommender.recommend(
            query="read test resources",
            top_k=3,
            requested_mode="tfidf",
        )
        assert mode == "tfidf"
        # Should return results without error
        assert isinstance(recommendations, list)

    def test_rag_mode_raises_error_without_embeddings(self, initialized_recommender):
        """Test that RAG mode raises error when embeddings not available."""
        # Force embedding model to be unavailable
        initialized_recommender._embedding_model = None
        # Also disable Ollama to avoid lazy connection timeout
        initialized_recommender._ollama_client = None

        with pytest.raises(EngineNotAvailableError) as exc_info:
            initialized_recommender.recommend(
                query="read test resources",
                top_k=3,
                requested_mode="rag",
            )
        assert exc_info.value.mode == "rag"
        assert "sentence-transformers" in exc_info.value.missing_components

    def test_llm_mode_raises_error_without_ollama(self, initialized_recommender):
        """Test that LLM mode raises error when Ollama not connected."""
        # Force Ollama client to be unavailable
        initialized_recommender._ollama_client = None

        with pytest.raises(EngineNotAvailableError) as exc_info:
            initialized_recommender.recommend(
                query="read test resources",
                top_k=3,
                requested_mode="llm",
            )
        assert exc_info.value.mode == "llm"
        assert "Ollama LLM" in exc_info.value.missing_components

    def test_hybrid_mode_raises_error_without_both(self, initialized_recommender):
        """Test that Hybrid mode raises error when both components missing."""
        # Force both to be unavailable
        initialized_recommender._ollama_client = None
        initialized_recommender._embedding_model = None

        with pytest.raises(EngineNotAvailableError) as exc_info:
            initialized_recommender.recommend(
                query="read test resources",
                top_k=3,
                requested_mode="hybrid",
            )
        assert exc_info.value.mode == "hybrid"
        # Should list both missing components
        assert "Ollama LLM" in exc_info.value.missing_components
        assert "sentence-transformers" in exc_info.value.missing_components
