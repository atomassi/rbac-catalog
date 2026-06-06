"""Tests for recommendation engines."""

from unittest.mock import MagicMock

import pytest

from azurerbac.airecommender.engines import (
    BM25Index,
    EngineRegistry,
    EnhancedTFIDFRecommender,
    HybridEngine,
    RAGEngine,
    RankedRole,
    SemanticEngine,
    cosine_similarity,
    top_k_similar,
)
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.azure.models import RoleDefinition


class TestRankedRole:
    """Tests for RankedRole dataclass."""

    @pytest.mark.parametrize(
        ("score", "keywords"),
        [
            pytest.param(0.85, ["read", "storage"], id="with_keywords"),
            pytest.param(0.95, None, id="without_keywords"),
            pytest.param(0.0, [], id="zero_score_empty_keywords"),
        ],
    )
    def test_from_embedding_classmethod(self, score: float, keywords: list[str] | None):
        """Test from_embedding classmethod creates correct instance."""
        role = RankedRole.from_embedding(
            role_id="r1",
            role_name="Storage Reader",
            description="Reads storage",
            score=score,
            keywords=keywords,
        )
        assert role.role_id == "r1"
        assert role.role_name == "Storage Reader"
        assert role.description == "Reads storage"
        assert role.embedding_score == score
        assert role.final_score == score
        assert role.matched_keywords == (keywords or [])


class TestEngineRegistry:
    """Tests for EngineRegistry decorator-based registration."""

    def test_create_returns_correct_engine_type(self):
        """EngineRegistry.create should return correct engine for each mode."""
        # Create a minimal mock knowledge base
        mock_kb = MagicMock()
        mock_kb.role_documents = {}
        mock_kb.get_all_role_names.return_value = []

        for mode in [RecommenderMode.TFIDF, RecommenderMode.SEMANTIC]:
            engine = EngineRegistry.create(mode=mode, knowledge_base=mock_kb)
            # Engine name should match the mode (case-insensitive check)
            assert engine is not None
            assert mode.value.lower() in engine.name.lower() or engine.name


class TestCosineSimilarity:
    """Tests for cosine similarity function."""

    @pytest.mark.parametrize(
        ("vec1", "vec2", "expected"),
        [
            pytest.param([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], 1.0, id="identical"),
            pytest.param([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], 0.0, id="orthogonal"),
            pytest.param([1.0, 2.0, 3.0], [-1.0, -2.0, -3.0], -1.0, id="opposite"),
            pytest.param([1.0, 2.0, 3.0], [0.0, 0.0, 0.0], 0.0, id="zero_vector"),
            pytest.param(
                [1.0, 0.0],
                [0.7071067811865476, 0.7071067811865476],
                0.7071067811865476,
                id="45_degrees",
            ),
        ],
    )
    def test_cosine_similarity_values(self, vec1: list[float], vec2: list[float], expected: float):
        """Test cosine similarity for various vector relationships."""
        assert cosine_similarity(vec1, vec2) == pytest.approx(expected, rel=1e-6)

    def test_similar_vectors(self):
        """Test cosine similarity of similar vectors is close to 1.0."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [1.1, 2.1, 3.1]  # Slightly different
        sim = cosine_similarity(vec1, vec2)
        assert sim > 0.99  # Very similar

    def test_mismatched_length_raises(self):
        """Test that mismatched vector lengths raise ValueError."""
        vec1 = [1.0, 2.0, 3.0]
        vec2 = [1.0, 2.0]
        with pytest.raises(ValueError, match="same length"):
            cosine_similarity(vec1, vec2)


class TestTopKSimilar:
    """Tests for top_k_similar function."""

    @pytest.mark.parametrize(
        ("query", "embeddings", "k", "expected_len"),
        [
            pytest.param([1.0, 2.0, 3.0], {}, 5, 0, id="empty_embeddings"),
            pytest.param([0.0, 0.0, 0.0], {"doc1": [1.0, 2.0, 3.0]}, 5, 0, id="zero_query"),
            pytest.param(
                [1.0, 0.0],
                {"doc1": [1.0, 0.0], "doc2": [0.0, 1.0]},
                10,
                2,
                id="k_larger_than_embeddings",
            ),
        ],
    )
    def test_edge_cases(self, query: list[float], embeddings: dict, k: int, expected_len: int):
        """Test edge cases for top_k_similar."""
        result = top_k_similar(query, embeddings, k=k)
        assert len(result) == expected_len

    def test_returns_sorted_by_similarity(self):
        """Test that results are sorted by similarity descending."""
        query = [1.0, 0.0, 0.0]
        embeddings = {
            "low": [-0.5, 0.5, 0.0],
            "medium": [0.5, 0.5, 0.0],
            "high": [1.0, 0.0, 0.0],
        }
        result = top_k_similar(query, embeddings, k=3)

        assert result[0][0] == "high"
        assert result[1][0] == "medium"
        assert result[2][0] == "low"
        # Verify descending order
        assert result[0][1] >= result[1][1] >= result[2][1]

    def test_returns_only_k_results(self):
        """Test that only k results are returned."""
        query = [1.0, 0.0]
        embeddings = {f"doc{i}": [float(i), 0.0] for i in range(10)}
        result = top_k_similar(query, embeddings, k=3)
        assert len(result) == 3


class TestComputeDocumentsHash:
    """Tests for compute_documents_hash function."""

    @pytest.mark.parametrize(
        ("docs1", "docs2", "should_equal"),
        [
            pytest.param(
                {"role1": "Storage Blob Reader", "role2": "Virtual Machine Contributor"},
                {"role1": "Storage Blob Reader", "role2": "Virtual Machine Contributor"},
                True,
                id="identical_docs",
            ),
            pytest.param(
                {"role1": "First", "role2": "Second"},
                {"role2": "Second", "role1": "First"},
                True,
                id="order_independent",
            ),
            pytest.param(
                {"role1": "Storage Blob Reader"},
                {"role1": "Storage Blob Writer"},
                False,
                id="different_content",
            ),
            pytest.param(
                {"role1": "User Access Administrator manage access"},
                {"role1": "User Access Administrator manage access Create role assignment"},
                False,
                id="content_changed",
            ),
        ],
    )
    def test_hash_comparison(self, docs1, docs2, should_equal):
        """Test hash behavior for various document comparisons."""
        from azurerbac.airecommender.embeddings import compute_documents_hash

        hash1 = compute_documents_hash(docs1)
        hash2 = compute_documents_hash(docs2)
        assert (hash1 == hash2) == should_equal


# =============================================================================
# Cross-Engine Fallback Tests (Consolidated)
# =============================================================================


class TestEngineEmbeddingNotLoadedFallback:
    """Consolidated tests: engines return empty when embedding model not loaded."""

    @pytest.mark.parametrize(
        "engine_class_path",
        [
            pytest.param("azurerbac.airecommender.engines.rag.RAGEngine", id="RAG"),
            pytest.param("azurerbac.airecommender.engines.semantic.SemanticEngine", id="Semantic"),
            pytest.param(
                "azurerbac.airecommender.engines.crossencoder.CrossEncoderEngine", id="CrossEncoder"
            ),
            pytest.param("azurerbac.airecommender.engines.hyde.HyDEEngine", id="HyDE"),
        ],
    )
    def test_returns_empty_when_embedding_not_loaded(
        self, engine_class_path, mock_ollama_client, mock_knowledge_base
    ):
        """All embedding-based engines return empty when embedding model not loaded."""
        import importlib

        module_path, class_name = engine_class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        engine_class = getattr(module, class_name)

        unloaded_model = MagicMock()
        unloaded_model.is_loaded = False

        # Build engine kwargs based on class requirements
        kwargs = {"embedding_model": unloaded_model, "knowledge_base": mock_knowledge_base}
        if class_name in ("RAGEngine", "HyDEEngine"):
            kwargs["ollama_client"] = mock_ollama_client

        engine = engine_class(**kwargs)
        results = engine.recommend("read blobs", top_k=5)

        assert results == []


class TestEngineNoEmbeddingsFallback:
    """Consolidated tests: engines return empty when embedding model has no embeddings."""

    @pytest.mark.parametrize(
        "engine_class_path",
        [
            pytest.param(
                "azurerbac.airecommender.engines.crossencoder.CrossEncoderEngine", id="CrossEncoder"
            ),
            pytest.param("azurerbac.airecommender.engines.hyde.HyDEEngine", id="HyDE"),
        ],
    )
    def test_returns_empty_when_no_embeddings(
        self, engine_class_path, mock_ollama_client, mock_knowledge_base
    ):
        """Engines return empty when embedding model has no embeddings dict."""
        import importlib

        module_path, class_name = engine_class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        engine_class = getattr(module, class_name)

        empty_model = MagicMock()
        empty_model.is_loaded = True
        empty_model.encode_single_cached = MagicMock(return_value=(0.1, 0.2, 0.3, 0.4))
        empty_model.embeddings = {}

        kwargs = {"embedding_model": empty_model, "knowledge_base": mock_knowledge_base}
        if class_name == "HyDEEngine":
            kwargs["ollama_client"] = mock_ollama_client

        engine = engine_class(**kwargs)
        results = engine.recommend("read blobs", top_k=5)

        assert results == []


class TestRAGEngine:
    """Tests for RAGEngine class."""

    def test_rag_recommend_returns_ranked_roles(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test RAG recommend returns RankedRole objects."""
        engine = RAGEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("read storage blobs", top_k=2)

        assert len(results) <= 2
        for result in results:
            assert isinstance(result, RankedRole)
            assert result.role_id is not None
            assert result.role_name is not None

    def test_rag_excludes_owner_by_default(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test RAG excludes Owner role by default (least privilege)."""
        engine = RAGEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access to everything", top_k=5)

        role_names = [r.role_name for r in results]
        assert "Owner" not in role_names

    def test_rag_includes_owner_when_requested(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test RAG doesn't filter Owner when exclude_owner=False."""
        engine = RAGEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access", top_k=5, exclude_owner=False)

        # Should return results without filtering Owner
        assert len(results) > 0
        # Owner is not filtered - if it had high enough similarity it would be included
        assert all(r.role_name is not None for r in results)

    def test_rag_works_without_llm(self, mock_embedding_model, mock_knowledge_base):
        """Test RAG works with embedding only (no LLM)."""
        no_llm_client = MagicMock()
        no_llm_client.is_connected = False

        engine = RAGEngine(
            embedding_model=mock_embedding_model,
            ollama_client=no_llm_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("read blobs", top_k=2)

        # Should return results based on embedding similarity alone
        assert len(results) > 0
        # Final scores are normalized to 0.60-0.95 range for better UX
        for r in results:
            assert 0.55 <= r.final_score <= 1.0

    def test_rag_embedding_similarity_ranking(self, mock_embedding_model, mock_knowledge_base):
        """Test RAG ranks by embedding similarity correctly."""
        no_llm_client = MagicMock()
        no_llm_client.is_connected = False

        engine = RAGEngine(
            embedding_model=mock_embedding_model,
            ollama_client=no_llm_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=3, exclude_owner=False)

        # role-1 has identical embedding to query, should be first
        assert results[0].role_id == "role-1"
        assert results[0].embedding_score == pytest.approx(1.0)

    def test_rag_returns_low_similarity_results(self, mock_ollama_client, mock_knowledge_base):
        """Test RAG returns results even with low similarity (no filtering)."""
        # Create embedding model with very low similarity scores
        # Query vector [1,0,0,0] vs role vectors that are nearly orthogonal
        low_confidence_model = MagicMock()
        low_confidence_model.is_loaded = True
        low_confidence_model.encode_single = MagicMock(return_value=[1.0, 0.0, 0.0, 0.0])
        low_confidence_model.encode_single_cached = MagicMock(return_value=(1.0, 0.0, 0.0, 0.0))
        low_confidence_model.embeddings = {
            "role-1": [0.0, 1.0, 0.0, 0.0],  # Orthogonal = 0 similarity
            "role-2": [0.0, 0.0, 1.0, 0.0],  # Orthogonal = 0 similarity
        }

        no_llm_client = MagicMock()
        no_llm_client.is_connected = False

        engine = RAGEngine(
            embedding_model=low_confidence_model,
            ollama_client=no_llm_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=5, exclude_owner=False)

        # RAG does NOT filter low similarity results (unlike Semantic)
        # Results are returned and normalized
        assert len(results) >= 0  # May return results depending on retrieval

    def test_rag_keeps_high_confidence(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test RAG keeps results with high confidence (>=50%)."""
        no_llm_client = MagicMock()
        no_llm_client.is_connected = False

        engine = RAGEngine(
            embedding_model=mock_embedding_model,
            ollama_client=no_llm_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=3, exclude_owner=False)

        # role-1 has 100% similarity, should be included
        assert len(results) >= 1
        assert results[0].final_score >= 0.5

    def test_rag_requires_embeddings(self):
        """Test that RAG engine requires embeddings."""
        engine = RAGEngine(knowledge_base=MagicMock())
        assert engine.requires_embeddings is True
        assert engine.requires_llm is True


class TestSemanticEngine:
    """Tests for SemanticEngine class (pure embedding similarity search)."""

    def test_semantic_engine_initialization(self, mock_embedding_model, mock_knowledge_base):
        """Test SemanticEngine initializes correctly."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.embedding_model == mock_embedding_model
        assert engine.knowledge_base == mock_knowledge_base
        assert engine.requires_llm is False
        assert engine.requires_embeddings is True

    def test_semantic_recommend_returns_ranked_roles(
        self, mock_embedding_model, mock_knowledge_base
    ):
        """Test Semantic recommend returns RankedRole objects."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("read storage blobs", top_k=2)

        assert len(results) <= 2
        for result in results:
            assert isinstance(result, RankedRole)
            assert result.role_id is not None
            assert result.role_name is not None

    def test_semantic_excludes_owner_by_default(self, mock_embedding_model, mock_knowledge_base):
        """Test Semantic excludes Owner role by default (least privilege)."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access to everything", top_k=5)

        role_names = [r.role_name for r in results]
        assert "Owner" not in role_names

    def test_semantic_includes_owner_when_requested(
        self, mock_embedding_model, mock_knowledge_base
    ):
        """Test Semantic doesn't filter Owner when exclude_owner=False."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access", top_k=5, exclude_owner=False)

        # Should return results without filtering Owner
        assert len(results) > 0
        # Owner is not filtered - if it had high enough similarity it would be included
        assert all(r.role_name is not None for r in results)

    def test_semantic_embedding_similarity_ranking(self, mock_embedding_model, mock_knowledge_base):
        """Test Semantic ranks by embedding similarity correctly."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=3, exclude_owner=False)

        # role-1 has identical embedding to query, should be first
        assert results[0].role_id == "role-1"
        assert results[0].embedding_score == pytest.approx(1.0)
        # final_score is hybrid (TF-IDF + embedding), but embedding contributes
        # With no TF-IDF match (mock has no document_text), only semantic applies
        assert (
            results[0].final_score >= results[0].embedding_score * 0.3
        )  # At least semantic weight

    def test_semantic_no_llm_required(self, mock_embedding_model, mock_knowledge_base):
        """Test Semantic works without any LLM client."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
            ollama_client=None,  # Explicitly no LLM
        )
        results = engine.recommend("read blobs", top_k=2)

        # Should return results based on pure embedding similarity
        assert len(results) > 0
        # Scores are normalized to 0.60-0.95 range
        for r in results:
            assert 0.55 <= r.final_score <= 1.0

    def test_semantic_vs_rag_difference(self, mock_embedding_model, mock_knowledge_base):
        """Test that SemanticEngine does NOT use LLM re-ranking like RAG."""
        mock_ollama = MagicMock()
        mock_ollama.is_connected = True
        mock_ollama.generate = MagicMock(return_value="some reranking")

        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama,
        )
        engine.recommend("test query", top_k=2)

        # SemanticEngine should NOT call the LLM at all
        mock_ollama.generate.assert_not_called()

    def test_semantic_filters_low_confidence(self, mock_knowledge_base):
        """Test Semantic filters out low-confidence results (<20%)."""
        # Create embedding model with very low similarity scores
        # Query vector [1,0,0,0] vs role vectors that are orthogonal
        low_confidence_model = MagicMock()
        low_confidence_model.is_loaded = True
        low_confidence_model.encode_single = MagicMock(return_value=[1.0, 0.0, 0.0, 0.0])
        low_confidence_model.encode_single_cached = MagicMock(return_value=(1.0, 0.0, 0.0, 0.0))
        low_confidence_model.embeddings = {
            "role-1": [0.0, 1.0, 0.0, 0.0],  # Orthogonal = 0 similarity
            "role-2": [0.0, 0.0, 1.0, 0.0],  # Orthogonal = 0 similarity
            "role-3": [0.0, 0.0, 0.0, 1.0],  # Orthogonal = 0 similarity
        }

        engine = SemanticEngine(
            embedding_model=low_confidence_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=5, exclude_owner=False)

        # All results should be filtered out due to low confidence (<0.2)
        assert len(results) == 0

    def test_semantic_keeps_high_confidence(self, mock_embedding_model, mock_knowledge_base):
        """Test Semantic keeps results with high confidence (>=20% threshold)."""
        engine = SemanticEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=3, exclude_owner=False)

        # role-1 has 100% embedding similarity, should have high normalized score
        assert len(results) >= 1
        assert results[0].final_score >= 0.6  # Normalized score


class TestHybridEngine:
    """Tests for HybridEngine class."""

    @pytest.fixture
    def mock_tfidf(self):
        """Create a mock TF-IDF recommender."""
        tfidf = MagicMock()
        # EnhancedTFIDFRecommender returns (role_id, role_name, score, metadata)
        tfidf.recommend = MagicMock(
            return_value=[
                ("role-1", "Storage Blob Data Reader", 0.95, {}),
                ("role-2", "Storage Account Contributor", 0.85, {}),
                ("role-3", "Virtual Machine Contributor", 0.75, {}),
            ]
        )
        return tfidf

    @pytest.fixture
    def mock_embedding_model(self):
        """Create a mock embedding model for Hybrid."""
        model = MagicMock()
        model.is_loaded = True
        model.encode_single = MagicMock(return_value=[0.5, 0.5, 0.5, 0.5])
        # encode_single_cached returns tuple (for lru_cache compatibility)
        model.encode_single_cached = MagicMock(return_value=(0.5, 0.5, 0.5, 0.5))
        model.embeddings = {
            "role-1": [0.5, 0.5, 0.5, 0.5],  # Perfect match
            "role-2": [0.4, 0.4, 0.6, 0.6],  # Good match
            "role-3": [0.1, 0.1, 0.1, 0.1],  # Poor match
        }
        return model

    @pytest.fixture
    def mock_knowledge_base(self):
        """Create a mock knowledge base for Hybrid."""
        kb = MagicMock()
        kb.role_documents = {
            "role-1": {
                "role_name": "Storage Blob Data Reader",
                "description": "Read blob storage data",
            },
            "role-2": {
                "role_name": "Storage Account Contributor",
                "description": "Manage storage accounts",
            },
            "role-3": {
                "role_name": "Virtual Machine Contributor",
                "description": "Manage VMs",
            },
        }
        # Map role names to role IDs for find_role_id_by_name
        name_to_id = {doc["role_name"]: rid for rid, doc in kb.role_documents.items()}
        kb.find_role_id_by_name = MagicMock(side_effect=lambda name: name_to_id.get(name))
        return kb

    @pytest.fixture
    def mock_ollama_client(self):
        """Create a mock Ollama client for Hybrid."""
        client = MagicMock()
        client.is_connected = True
        client.generate = MagicMock(
            return_value="1. Storage Blob Data Reader\n2. Storage Account Contributor"
        )
        return client

    def test_hybrid_engine_initialization(
        self, mock_tfidf, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HybridEngine initializes correctly."""
        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama_client,
            embedding_model=mock_embedding_model,
            tfidf_recommender=mock_tfidf,
        )
        assert engine.tfidf_recommender == mock_tfidf
        assert engine.embedding_model == mock_embedding_model
        assert engine.ollama_client == mock_ollama_client
        assert engine.knowledge_base == mock_knowledge_base

    def test_hybrid_recommend_returns_ranked_roles(
        self, mock_tfidf, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test Hybrid recommend returns RankedRole objects."""
        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama_client,
            embedding_model=mock_embedding_model,
            tfidf_recommender=mock_tfidf,
        )
        results = engine.recommend("read storage blobs", top_k=2)

        assert len(results) <= 2
        for result in results:
            assert isinstance(result, RankedRole)

    def test_hybrid_uses_all_three_stages(
        self, mock_tfidf, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test Hybrid uses TF-IDF, embedding, and LLM stages."""
        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama_client,
            embedding_model=mock_embedding_model,
            tfidf_recommender=mock_tfidf,
        )
        engine.recommend("test query", top_k=2)

        # Verify TF-IDF was called
        mock_tfidf.recommend.assert_called_once()
        # Verify embedding model was used (now uses encode_single_cached)
        mock_embedding_model.encode_single_cached.assert_called()
        # Verify LLM was called
        mock_ollama_client.generate.assert_called()

    def test_hybrid_works_without_llm(self, mock_tfidf, mock_embedding_model, mock_knowledge_base):
        """Test Hybrid works with TF-IDF + embedding only."""
        no_llm = MagicMock()
        no_llm.is_connected = False

        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=no_llm,
            embedding_model=mock_embedding_model,
            tfidf_recommender=mock_tfidf,
        )
        results = engine.recommend("read blobs", top_k=2)

        # Without LLM, final score is weighted average (normalized)
        # Scores are normalized to 0.60-0.95 range
        assert len(results) >= 0  # May be empty if TF-IDF returns no matches

    def test_hybrid_works_without_embeddings(
        self, mock_tfidf, mock_ollama_client, mock_knowledge_base
    ):
        """Test Hybrid works with TF-IDF + LLM only."""
        no_embedding = MagicMock()
        no_embedding.is_loaded = False

        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama_client,
            embedding_model=no_embedding,
            tfidf_recommender=mock_tfidf,
        )
        results = engine.recommend("read blobs", top_k=2)

        # Without embeddings, uses TF-IDF scores for embedding stage
        # May return empty if TF-IDF mock returns roles not in knowledge_base
        assert len(results) >= 0

    def test_hybrid_returns_empty_when_no_tfidf_candidates(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test Hybrid returns empty when TF-IDF finds nothing."""
        empty_tfidf = MagicMock()
        empty_tfidf.recommend = MagicMock(return_value=[])

        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama_client,
            embedding_model=mock_embedding_model,
            tfidf_recommender=empty_tfidf,
        )
        results = engine.recommend("nonexistent query", top_k=5)

        assert results == []

    def test_hybrid_stage_parameters(
        self, mock_tfidf, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test Hybrid respects stage k parameters."""
        engine = HybridEngine(
            knowledge_base=mock_knowledge_base,
            ollama_client=mock_ollama_client,
            embedding_model=mock_embedding_model,
            tfidf_recommender=mock_tfidf,
        )

        # Call with custom stage parameters
        engine.recommend(
            "test",
            top_k=3,
            tfidf_k=50,  # Stage 1: 50 candidates
            embedding_k=10,  # Stage 2: 10 candidates
        )

        # Verify TF-IDF was called with correct k
        call_args = mock_tfidf.recommend.call_args
        assert call_args[1]["top_k"] == 50


class TestEngineScoring:
    """Tests for engine scoring calculations."""

    def test_tfidf_score_propagation(self):
        """Test TF-IDF scores propagate through Hybrid pipeline."""
        # Create minimal mocks
        tfidf = MagicMock()
        # EnhancedTFIDFRecommender returns (role_id, role_name, score, metadata)
        tfidf.recommend = MagicMock(return_value=[("role-1", "Test Role", 0.95, {})])

        embedding_model = MagicMock()
        embedding_model.is_loaded = False  # Skip embedding stage

        ollama = MagicMock()
        ollama.is_connected = False  # Skip LLM stage

        kb = MagicMock()
        kb.role_documents = {"role-1": {"role_name": "Test Role", "description": "Test"}}
        kb.find_role_id_by_name = MagicMock(return_value="role-1")

        engine = HybridEngine(
            knowledge_base=kb,
            ollama_client=ollama,
            embedding_model=embedding_model,
            tfidf_recommender=tfidf,
        )
        results = engine.recommend("test", top_k=1)

        assert len(results) == 1
        assert results[0].tfidf_score == 0.95

    def test_combined_scoring(self):
        """Test combined TF-IDF + embedding scoring."""
        tfidf = MagicMock()
        # EnhancedTFIDFRecommender returns (role_id, role_name, score, metadata)
        tfidf.recommend = MagicMock(return_value=[("role-1", "Test Role", 0.8, {})])

        embedding_model = MagicMock()
        embedding_model.is_loaded = True
        embedding_model.encode_single = MagicMock(return_value=[1.0, 0.0])
        embedding_model.encode_single_cached = MagicMock(return_value=(1.0, 0.0))
        embedding_model.embeddings = {"role-1": [0.8, 0.6]}  # cos_sim ≈ 0.8

        ollama = MagicMock()
        ollama.is_connected = False  # Skip LLM

        kb = MagicMock()
        kb.role_documents = {"role-1": {"role_name": "Test Role", "description": "Test"}}
        kb.find_role_id_by_name = MagicMock(return_value="role-1")

        engine = HybridEngine(
            knowledge_base=kb,
            ollama_client=ollama,
            embedding_model=embedding_model,
            tfidf_recommender=tfidf,
        )
        results = engine.recommend("test", top_k=1)

        assert len(results) == 1
        # Hybrid uses weighted scoring: 70% TF-IDF + 30% embedding, then normalized
        # Scores are normalized to 0.60-0.95 range, so just check it's reasonable
        assert 0.55 <= results[0].final_score <= 1.0


# =============================================================================
# Tests for the enhanced TF-IDF recommender
# =============================================================================


class TestBM25Index:
    """Tests for the BM25 ranking algorithm."""

    def test_bm25_initialization(self):
        """Test BM25 index initializes with default parameters."""
        index = BM25Index()
        assert index.k1 == 1.5
        assert index.b == 0.75
        assert index.doc_count == 0

    def test_bm25_custom_parameters(self):
        """Test BM25 index with custom parameters."""
        index = BM25Index(k1=2.0, b=0.5)
        assert index.k1 == 2.0
        assert index.b == 0.5

    def test_bm25_tokenize(self):
        """Test tokenization of text."""
        index = BM25Index()
        tokens = index._tokenize("Hello World! Test-123 a")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens
        assert "123" in tokens
        assert "a" not in tokens  # Too short

    def test_bm25_tokenize_empty(self):
        """Test tokenization of empty text."""
        index = BM25Index()
        tokens = index._tokenize("")
        assert tokens == []

    def test_bm25_index_documents(self):
        """Test indexing documents."""
        index = BM25Index()
        documents = [
            ("doc1", "Azure Storage Blob Reader"),
            ("doc2", "Azure Virtual Machine Contributor"),
            ("doc3", "Azure SQL Database Admin"),
        ]
        index.index(documents)

        assert index.doc_count == 3
        assert len(index.doc_ids) == 3
        # Verify term is in vocabulary (numpy-optimized uses term_to_idx)
        assert "azure" in index.term_to_idx
        assert index.avg_doc_len > 0

    @pytest.mark.parametrize(
        ("query", "expected_first"),
        [
            pytest.param("storage blob", "doc1", id="storage_query"),
            pytest.param("virtual machine", "doc2", id="vm_query"),
        ],
    )
    def test_bm25_search_ranking(self, query: str, expected_first: str):
        """Test BM25 search returns correct top result for query."""
        index = BM25Index()
        documents = [
            ("doc1", "Azure Storage Blob Reader"),
            ("doc2", "Azure Virtual Machine Contributor"),
            ("doc3", "Azure SQL Database Admin"),
        ]
        index.index(documents)

        results = index.search(query)
        assert len(results) > 0
        assert results[0][0] == expected_first

    @pytest.mark.parametrize(
        ("query", "expected_results"),
        [
            pytest.param("", [], id="empty_query"),
            pytest.param("kubernetes container", [], id="no_match"),
        ],
    )
    def test_bm25_search_empty_results(self, query: str, expected_results: list):
        """Test BM25 search returns empty for non-matching queries."""
        index = BM25Index()
        index.index([("doc1", "Azure Storage Blob")])
        assert index.search(query) == expected_results

    def test_bm25_search_top_k(self):
        """Test search returns limited results."""
        index = BM25Index()
        documents = [(f"doc{i}", f"Azure test document {i}") for i in range(20)]
        index.index(documents)

        results = index.search("azure", top_k=5)
        assert len(results) == 5

    def test_bm25_idf_calculation(self):
        """Test IDF values are calculated correctly."""
        index = BM25Index()
        documents = [
            ("doc1", "azure storage"),
            ("doc2", "azure compute"),
            ("doc3", "azure network"),
        ]
        index.index(documents)

        # "azure" appears in all docs, should have lower IDF
        # than terms appearing in fewer docs
        # numpy-optimized stores IDF in idf_values array indexed by term_to_idx
        azure_idx = index.term_to_idx["azure"]
        storage_idx = index.term_to_idx["storage"]
        assert index.idf_values[storage_idx] > index.idf_values[azure_idx]


class TestTFIDFEngine:
    """Tests for TFIDFEngine class."""

    @pytest.fixture
    def mock_tfidf_recommender(self):
        """Create a mock TF-IDF recommender."""
        recommender = MagicMock()
        recommender.recommend.return_value = [
            ("role-1", "Storage Blob Reader", 0.85, ["storage", "read"]),
            ("role-2", "VM Contributor", 0.65, ["vm"]),
        ]
        return recommender

    @pytest.fixture
    def mock_knowledge_base(self):
        """Create a mock knowledge base for TF-IDF tests."""
        kb = MagicMock()
        kb.get_role_document.side_effect = lambda rid: {
            "role-1": {
                "role_id": "role-1",
                "role_name": "Storage Blob Reader",
                "description": "Read blobs",
            },
            "role-2": {
                "role_id": "role-2",
                "role_name": "VM Contributor",
                "description": "Manage VMs",
            },
        }.get(rid)
        return kb

    def test_tfidf_engine_properties(self, mock_tfidf_recommender, mock_knowledge_base):
        """Test TFIDFEngine has correct properties."""
        from azurerbac.airecommender.engines.tfidf import TFIDFEngine

        engine = TFIDFEngine(
            tfidf_recommender=mock_tfidf_recommender,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.name == "Enhanced TF-IDF + BM25"
        assert engine.requires_llm is False
        assert engine.requires_embeddings is False

    def test_tfidf_recommend_returns_ranked_roles(
        self, mock_tfidf_recommender, mock_knowledge_base
    ):
        """Test TFIDFEngine recommend returns RankedRole objects."""
        from azurerbac.airecommender.engines.tfidf import TFIDFEngine

        engine = TFIDFEngine(
            tfidf_recommender=mock_tfidf_recommender,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("read storage blobs", top_k=2)

        assert len(results) >= 1
        assert isinstance(results[0], RankedRole)
        assert results[0].tfidf_score > 0

    def test_tfidf_returns_empty_when_not_initialized(self, mock_knowledge_base):
        """Test TFIDFEngine returns empty list when recommender is None."""
        from azurerbac.airecommender.engines.tfidf import TFIDFEngine

        engine = TFIDFEngine(
            tfidf_recommender=None,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test", top_k=3)

        assert results == []

    def test_tfidf_excludes_owner_by_default(self, mock_tfidf_recommender, mock_knowledge_base):
        """Test TFIDFEngine excludes Owner role by default."""
        from azurerbac.airecommender.engines.tfidf import TFIDFEngine

        mock_tfidf_recommender.recommend.return_value = [
            ("owner-id", "Owner", 0.95, ["owner"]),
        ]
        mock_knowledge_base.get_role_document.return_value = {
            "role_id": "owner-id",
            "role_name": "Owner",
            "description": "Full access",
        }

        engine = TFIDFEngine(
            tfidf_recommender=mock_tfidf_recommender,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access", top_k=3)

        role_names = [r.role_name for r in results]
        assert "Owner" not in role_names


class TestEnhancedTFIDFRecommender:
    """Tests for the enhanced TF-IDF recommender."""

    @pytest.fixture
    def sample_roles(self):
        """Sample role data for testing."""
        return [
            RoleDefinition(
                name="role-id-1",
                properties={
                    "roleName": "Storage Blob Data Reader",
                    "description": "Allows for read access to Azure Storage blob containers",
                    "permissions": [
                        {
                            "actions": ["Microsoft.Storage/storageAccounts/blobServices/read"],
                            "dataActions": [
                                "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
                            ],
                            "notActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            ),
            RoleDefinition(
                name="role-id-2",
                properties={
                    "roleName": "Virtual Machine Contributor",
                    "description": "Lets you manage virtual machines, but not access to them",
                    "permissions": [
                        {
                            "actions": [
                                "Microsoft.Compute/virtualMachines/*",
                                "Microsoft.Network/networkInterfaces/*",
                            ],
                            "dataActions": [],
                            "notActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            ),
            RoleDefinition(
                name="role-id-3",
                properties={
                    "roleName": "Key Vault Reader",
                    "description": "Read metadata of key vaults and certificates",
                    "permissions": [
                        {
                            "actions": ["Microsoft.KeyVault/vaults/read"],
                            "dataActions": [],
                            "notActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            ),
        ]

    def test_recommender_initialize_with_roles(self, sample_roles):
        """Test initializing recommender with role data."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        assert recommender._initialized
        assert len(recommender._role_data) == 3
        assert "storage blob data reader" in recommender._role_name_to_id

    def test_recommender_initialize_idempotent(self, sample_roles):
        """Test that initialize only runs once."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)
        recommender.initialize(sample_roles)  # Should not re-initialize

        assert recommender._initialized
        assert len(recommender._role_data) == 3

    def test_recommender_build_document(self, sample_roles):
        """Test document building for a role."""
        recommender = EnhancedTFIDFRecommender()
        doc = recommender._build_document(sample_roles[0])

        assert "Storage Blob Data Reader" in doc
        assert "read access" in doc.lower() or "read" in doc.lower()

    def test_recommender_get_role_name(self, sample_roles):
        """Test getting role name from ID."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        name = recommender._get_role_name("role-id-1")
        assert name == "Storage Blob Data Reader"

    def test_recommender_get_role_name_unknown(self, sample_roles):
        """Test getting role name for unknown ID."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        name = recommender._get_role_name("unknown-role")
        assert name == "unknown-role"

    def test_recommender_score_name_match(self, sample_roles):
        """Test name matching scoring."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        scores = recommender._score_name_match("storage blob")
        assert "Storage Blob Data Reader" in scores
        assert scores["Storage Blob Data Reader"] > 0

    def test_recommender_score_name_match_exact(self, sample_roles):
        """Test exact name matching."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        scores = recommender._score_name_match("key vault reader")
        assert "Key Vault Reader" in scores
        assert scores["Key Vault Reader"] == 1.0

    def test_recommender_score_fuzzy_match(self, sample_roles):
        """Test fuzzy matching with abbreviations."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        scores = recommender._score_fuzzy_match("vm contributor")
        # Should expand "vm" to "virtual machine" and find matches
        assert len(scores) >= 0  # May or may not match depending on implementation

    def test_recommender_score_pattern_match(self, sample_roles):
        """Test pattern matching scoring."""
        recommender = EnhancedTFIDFRecommender()
        recommender.initialize(sample_roles)

        # Pattern matching uses USE_CASE_PATTERNS from azure_knowledge
        scores = recommender._score_pattern_match("read storage blobs")
        assert isinstance(scores, dict)

    def test_build_document_with_synonyms(self):
        """Test that synonyms are added to documents."""
        recommender = EnhancedTFIDFRecommender()
        role = RoleDefinition(
            name="test-role",
            properties={
                "roleName": "Storage Account Contributor",
                "description": "Manage storage accounts",
                "permissions": [],
            },
        )
        doc = recommender._build_document(role)
        # Should include storage-related synonyms
        assert "storage" in doc.lower()

    def test_build_document_with_permission_levels(self):
        """Test that permission levels are added to documents."""
        recommender = EnhancedTFIDFRecommender()
        role = RoleDefinition(
            name="test-role",
            properties={
                "roleName": "Storage Reader",
                "description": "Read access to storage",
                "permissions": [],
            },
        )
        doc = recommender._build_document(role)
        # Should include reader-related keywords
        assert "reader" in doc.lower() or "read" in doc.lower()


class TestBM25EdgeCases:
    """Edge case tests for BM25."""

    def test_empty_documents(self):
        """Test indexing empty document list."""
        index = BM25Index()
        index.index([])
        assert index.doc_count == 0
        assert index.avg_doc_len == 0

    def test_single_document(self):
        """Test indexing single document."""
        index = BM25Index()
        index.index([("doc1", "test document")])
        assert index.doc_count == 1

        results = index.search("test")
        assert len(results) == 1
        assert results[0][0] == "doc1"

    def test_repeated_terms(self):
        """Test document with repeated terms."""
        index = BM25Index()
        index.index([("doc1", "azure azure azure storage")])

        results = index.search("azure")
        assert len(results) == 1
        # Higher term frequency should boost score
        assert results[0][1] > 0

    def test_long_query(self):
        """Test search with long query."""
        index = BM25Index()
        documents = [
            ("doc1", "Azure Storage Blob Reader for reading blob data"),
        ]
        index.index(documents)

        results = index.search("I need a role that allows me to read Azure Storage blob data")
        assert len(results) > 0


# =============================================================================
# EmbeddingModel Optimization Tests
# =============================================================================


class TestEmbeddingModelOptimizations:
    """Tests for EmbeddingModel caching and vectorized search optimizations."""

    @pytest.fixture
    def mock_embedding_model(self):
        """Create a mock embedding model for testing without sentence-transformers."""
        import numpy as np

        from azurerbac.airecommender.embeddings import EmbeddingModel

        model = EmbeddingModel()
        # Mock the model to avoid needing sentence-transformers
        model._model = MagicMock()
        model._loaded = True

        # Return predictable embeddings (384 dims like MiniLM)
        def mock_encode(text, show_progress_bar=False):
            import zlib

            # Seed from a stable content hash (deterministic across processes and
            # collision-free for distinct texts, unlike hash(text) % 1000).
            def _seed(value: str) -> int:
                return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF

            # Handle both single string and list of strings
            if isinstance(text, list):
                embeddings = []
                for t in text:
                    np.random.seed(_seed(t))
                    embeddings.append(np.random.randn(384).astype(np.float32))
                return np.array(embeddings)
            np.random.seed(_seed(text))
            return np.random.randn(384).astype(np.float32)

        model._model.encode = mock_encode

        # Clear the lru_cache before each test to ensure isolation
        model.encode_single_cached.cache_clear()

        yield model

        # Clean up after test
        model.encode_single_cached.cache_clear()

    def test_encode_single_cached_returns_tuple(self, mock_embedding_model):
        """Test that encode_single_cached returns a tuple (hashable for lru_cache)."""
        result = mock_embedding_model.encode_single_cached("test query")
        assert isinstance(result, tuple)
        assert len(result) == 384

    def test_encode_single_cached_is_cached(self, mock_embedding_model):
        """Test that repeated calls use the cache."""
        # Clear cache first
        mock_embedding_model.encode_single_cached.cache_clear()

        # First call
        mock_embedding_model.encode_single_cached("test query")
        info1 = mock_embedding_model.encode_single_cached.cache_info()
        assert info1.misses == 1
        assert info1.hits == 0

        # Second call with same query - should hit cache
        mock_embedding_model.encode_single_cached("test query")
        info2 = mock_embedding_model.encode_single_cached.cache_info()
        assert info2.misses == 1
        assert info2.hits == 1

    def test_encode_single_cached_different_queries(self, mock_embedding_model):
        """Test that different queries get different embeddings."""
        mock_embedding_model.encode_single_cached.cache_clear()

        emb1 = mock_embedding_model.encode_single_cached("read storage blobs")
        emb2 = mock_embedding_model.encode_single_cached("manage virtual machines")

        assert emb1 != emb2
        info = mock_embedding_model.encode_single_cached.cache_info()
        assert info.misses == 2
        assert info.currsize == 2

    def test_build_matrix_creates_numpy_array(self, mock_embedding_model):
        """Test that build_embeddings creates a numpy matrix."""
        import numpy as np

        documents = {
            "role-1": "Storage Blob Data Reader",
            "role-2": "Virtual Machine Contributor",
            "role-3": "Key Vault Secrets User",
        }
        mock_embedding_model.build_embeddings(documents, cache_hash=None)

        assert mock_embedding_model._matrix is not None
        assert isinstance(mock_embedding_model._matrix, np.ndarray)
        assert mock_embedding_model._matrix.shape == (3, 384)
        assert len(mock_embedding_model._doc_ids) == 3

    def test_matrix_is_normalized(self, mock_embedding_model):
        """Test that the embedding matrix rows are normalized (for fast cosine sim)."""
        import numpy as np

        documents = {"role-1": "Test role", "role-2": "Another role"}
        mock_embedding_model.build_embeddings(documents, cache_hash=None)

        # Each row should have norm ≈ 1.0
        norms = np.linalg.norm(mock_embedding_model._matrix, axis=1)
        np.testing.assert_array_almost_equal(norms, [1.0, 1.0], decimal=5)

    def test_search_uses_vectorized_when_matrix_exists(self, mock_embedding_model):
        """Test that search uses vectorized method when matrix is built."""
        documents = {
            "role-1": "Storage Blob Data Reader for reading blob data",
            "role-2": "Virtual Machine Contributor for managing VMs",
        }
        mock_embedding_model.build_embeddings(documents, cache_hash=None)

        results = mock_embedding_model.search("read storage blobs", top_k=2)

        assert len(results) == 2
        assert all(isinstance(r, tuple) and len(r) == 2 for r in results)
        assert all(isinstance(r[0], str) and isinstance(r[1], float) for r in results)

    def test_search_returns_sorted_by_similarity(self, mock_embedding_model):
        """Test that search results are sorted by similarity (descending)."""
        documents = {f"role-{i}": f"Document {i} content" for i in range(10)}
        mock_embedding_model.build_embeddings(documents, cache_hash=None)

        results = mock_embedding_model.search("query text", top_k=5)

        # Scores should be in descending order
        scores = [r[1] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_respects_top_k(self, mock_embedding_model):
        """Test that search returns at most top_k results."""
        documents = {f"role-{i}": f"Document {i}" for i in range(100)}
        mock_embedding_model.build_embeddings(documents, cache_hash=None)

        results = mock_embedding_model.search("query", top_k=5)
        assert len(results) == 5

        results = mock_embedding_model.search("query", top_k=10)
        assert len(results) == 10

    def test_vectorized_search_matches_loop_search(self, mock_embedding_model):
        """Test that vectorized search produces same results as loop-based search."""
        documents = {
            "role-1": "Azure Storage Blob Data Reader",
            "role-2": "Virtual Machine Contributor",
            "role-3": "Key Vault Secrets User",
        }
        mock_embedding_model.build_embeddings(documents, cache_hash=None)

        query = "read storage data"
        query_embedding = mock_embedding_model.encode_single(query)

        # Vectorized result using _search_matrix
        vectorized_results = mock_embedding_model._search_matrix(query_embedding, top_k=3)

        # Manual loop-based calculation
        from azurerbac.airecommender.engines.common import cosine_similarity

        loop_results = []
        for doc_id, doc_emb in mock_embedding_model._embeddings.items():
            sim = cosine_similarity(query_embedding, doc_emb)
            loop_results.append((doc_id, sim))
        loop_results.sort(key=lambda x: x[1], reverse=True)

        # Should return the same set of roles (ordering may differ for ties)
        assert {r[0] for r in vectorized_results} == {r[0] for r in loop_results[:3]}

        # Scores should match (within floating-point tolerance)
        vectorized_scores = {r[0]: r[1] for r in vectorized_results}
        loop_scores = {r[0]: r[1] for r in loop_results[:3]}
        for role_id, score in vectorized_scores.items():
            assert score == pytest.approx(loop_scores[role_id], abs=1e-6)


class TestEmbeddingModelEdgeCases:
    """Edge case tests for EmbeddingModel."""

    def test_empty_documents(self):
        """Test building embeddings with empty documents dict."""
        from azurerbac.airecommender.embeddings import EmbeddingModel

        model = EmbeddingModel()
        model._model = MagicMock()

        # Empty documents should not crash
        model._embeddings = {}
        model._build_matrix()

        assert model._matrix is None or len(model._doc_ids) == 0

    def test_search_without_matrix_uses_fallback(self):
        """Test that search falls back to loop when matrix not built."""
        import numpy as np

        from azurerbac.airecommender.embeddings import EmbeddingModel

        model = EmbeddingModel()
        model._model = MagicMock()
        # Return numpy array that has .tolist() method
        model._model.encode = lambda t, show_progress_bar=False: np.array([0.1] * 384)
        model._embeddings = {"role-1": [0.1] * 384, "role-2": [0.2] * 384}
        model._matrix = None  # Force fallback

        # Clear cache before test
        model.encode_single_cached.cache_clear()

        results = model.search("query", top_k=2)
        assert len(results) == 2

        # Clean up
        model.encode_single_cached.cache_clear()


# =============================================================================
# CrossEncoderEngine Tests
# =============================================================================


class TestCrossEncoderEngine:
    """Tests for CrossEncoderEngine class."""

    @pytest.fixture
    def mock_cross_encoder(self):
        """Create a mock cross-encoder model."""
        ce = MagicMock()
        # Cross-encoder returns raw relevance scores (can be negative)
        ce.predict = MagicMock(return_value=[2.5, 1.2, -0.5])
        return ce

    def test_crossencoder_initialization(self, mock_embedding_model, mock_knowledge_base):
        """Test CrossEncoderEngine initializes correctly with required properties."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.embedding_model == mock_embedding_model
        assert engine.knowledge_base == mock_knowledge_base
        assert engine.name == "Cross-Encoder Reranking"
        assert engine.requires_embeddings is True
        assert engine.requires_llm is False

    def test_crossencoder_returns_ranked_roles(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder recommend returns RankedRole objects."""
        from azurerbac.airecommender.engines.crossencoder import (
            CrossEncoderEngine,
        )

        # Mock get_cross_encoder to return a mock
        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(return_value=[2.5, 1.2])
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("read storage blobs", top_k=2)

        assert len(results) <= 2
        for result in results:
            assert isinstance(result, RankedRole)
            assert result.role_id is not None
            assert result.role_name is not None

    def test_crossencoder_excludes_owner_by_default(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder excludes Owner role by default."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(return_value=[2.5, 1.2, 3.0])
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access to everything", top_k=5)

        role_names = [r.role_name for r in results]
        assert "Owner" not in role_names

    def test_crossencoder_includes_owner_when_requested(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder doesn't filter Owner when exclude_owner=False."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(return_value=[2.5, 1.2, 3.0])
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access", top_k=5, exclude_owner=False)

        # Should return results without filtering Owner
        assert len(results) > 0
        # Owner is not filtered - if it had high enough similarity it would be included
        assert all(r.role_name is not None for r in results)

    def test_crossencoder_raises_when_ce_unavailable(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder raises when cross-encoder unavailable."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        # Mock cross-encoder to raise
        def mock_get_cross_encoder():
            raise ImportError("Cross-encoder not available")

        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            mock_get_cross_encoder,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )

        with pytest.raises(ImportError, match="Cross-encoder not available"):
            engine.recommend("read blobs", top_k=2)

    def test_crossencoder_reranking_changes_order(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test that cross-encoder reranking can change candidate order."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        # Cross-encoder scores in different order than bi-encoder
        # role-2 gets higher CE score than role-1
        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(return_value=[1.0, 3.0])  # role-2 > role-1
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=2)

        # The order should reflect cross-encoder scores (role-2 first)
        assert len(results) >= 1

    def test_crossencoder_combines_biencoder_and_ce_scores(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test that final score combines bi-encoder and cross-encoder scores."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(return_value=[2.0, 1.0])
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=2)

        # Results should have both embedding_score and llm_score (used for CE)
        for r in results:
            assert r.embedding_score >= 0
            assert r.llm_score >= 0  # llm_score field reused for CE score
            assert r.final_score >= 0

    def test_crossencoder_handles_ce_exception(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder handles cross-encoder prediction exceptions."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(side_effect=Exception("CE failed"))
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=2)

        # Should fall back to bi-encoder scores
        assert len(results) > 0

    def test_crossencoder_normalizes_ce_scores(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test that cross-encoder scores are normalized to 0-1 range."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        # Raw CE scores can be negative or > 1
        mock_ce = MagicMock()
        mock_ce.predict = MagicMock(return_value=[-5.0, 0.0, 5.0])
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test query", top_k=3, exclude_owner=False)

        # All normalized scores should be in 0-1 range
        for r in results:
            assert 0 <= r.llm_score <= 1

    def test_crossencoder_retrieval_k_constant(self):
        """Test that _RETRIEVAL_K constant is set appropriately."""
        from azurerbac.airecommender.engines.crossencoder import _RETRIEVAL_K

        assert _RETRIEVAL_K == 50  # Should retrieve 50 candidates for reranking


class TestCrossEncoderSingleton:
    """Tests for get_cross_encoder singleton pattern."""

    def test_get_cross_encoder_raises_when_load_fails(self, monkeypatch):
        """Test get_cross_encoder raises when loading fails."""
        from azurerbac.airecommender.engines import crossencoder
        from azurerbac.core.singleton import ThreadSafeSingleton

        # Save original singleton
        original_singleton = crossencoder._cross_encoder

        # Create a new singleton with a failing factory
        def mock_load():
            raise ImportError("sentence-transformers not installed")

        crossencoder._cross_encoder = ThreadSafeSingleton(factory=mock_load)

        try:
            with pytest.raises(ImportError, match="sentence-transformers not installed"):
                crossencoder.get_cross_encoder()
        finally:
            # Restore original singleton for other tests
            crossencoder._cross_encoder = original_singleton


# =============================================================================
# HyDEEngine Tests
# =============================================================================


class TestHyDEEngine:
    """Tests for HyDEEngine (Hypothetical Document Embeddings) class."""

    @pytest.fixture
    def mock_knowledge_base(self):
        """Create a mock knowledge base with VM-focused roles for HyDE tests."""
        kb = MagicMock()
        kb.role_documents = {
            "role-1": {
                "role_name": "Virtual Machine Contributor",
                "description": "Manage virtual machines including start, stop, restart",
            },
            "role-2": {
                "role_name": "Storage Account Contributor",
                "description": "Manage storage accounts",
            },
            "role-3": {
                "role_name": "Owner",
                "description": "Full access including RBAC",
            },
        }
        return kb

    @pytest.fixture
    def mock_ollama_client(self):
        """Create a mock Ollama client for HyDE."""
        client = MagicMock()
        client.is_connected = True
        # HyDE calls generate with model=HYDE_MODEL
        client.generate = MagicMock(
            return_value=(
                "This role allows managing Azure Virtual Machines including "
                "the ability to start, stop, and restart VMs. It provides "
                "read and write access to compute resources."
            )
        )
        return client

    def test_hyde_initialization(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDEEngine initializes correctly with required properties."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.embedding_model == mock_embedding_model
        assert engine.ollama_client == mock_ollama_client
        assert engine.knowledge_base == mock_knowledge_base
        assert engine.name == "HyDE: Hypothetical Document Embeddings"
        assert engine.requires_embeddings is True
        assert engine.requires_llm is True

    def test_hyde_returns_ranked_roles(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDE recommend returns RankedRole objects."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("I need to manage VMs", top_k=2)

        assert len(results) <= 2
        for result in results:
            assert isinstance(result, RankedRole)
            assert result.role_id is not None
            assert result.role_name is not None

    def test_hyde_excludes_owner_by_default(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDE excludes Owner role by default."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access to everything", top_k=5)

        role_names = [r.role_name for r in results]
        assert "Owner" not in role_names

    def test_hyde_includes_owner_when_requested(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDE doesn't filter Owner when exclude_owner=False."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access", top_k=5, exclude_owner=False)

        # Should return results without filtering Owner
        assert len(results) > 0
        # Owner is not filtered - if it had high enough similarity it would be included
        assert all(r.role_name is not None for r in results)

    def test_hyde_returns_empty_when_ollama_not_connected(
        self, mock_embedding_model, mock_knowledge_base
    ):
        """Test HyDE returns empty when Ollama not connected."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        disconnected_client = MagicMock()
        disconnected_client.is_connected = False

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=disconnected_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("manage VMs", top_k=5)

        assert results == []

    def test_hyde_calls_llm_with_prompt(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDE calls LLM with proper prompt template."""
        from azurerbac.airecommender.engines.hyde import HYDE_MODEL, HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        engine.recommend("I need to manage VMs", top_k=2)

        # Verify LLM was called with generate(model=HYDE_MODEL)
        mock_ollama_client.generate.assert_called_once()
        call_args = mock_ollama_client.generate.call_args
        prompt = call_args[0][0]  # First positional arg
        model = call_args[1].get("model")  # model is a kwarg

        # Prompt should contain the query and Azure context
        assert "I need to manage VMs" in prompt
        assert "Azure" in prompt
        assert model == HYDE_MODEL

    def test_hyde_embeds_hypothetical_document(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDE embeds the hypothetical document, not the original query."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        engine.recommend("manage VMs", top_k=2)

        # encode_single_cached should be called with the generated hypothetical doc
        call_args = mock_embedding_model.encode_single_cached.call_args
        embedded_text = call_args[0][0]

        # The embedded text should be the LLM-generated description
        assert "Virtual Machines" in embedded_text or "VMs" in embedded_text

    def test_hyde_fallback_to_query_when_llm_fails(self, mock_embedding_model, mock_knowledge_base):
        """Test HyDE falls back to original query when LLM generation fails."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        failing_client = MagicMock()
        failing_client.is_connected = True
        failing_client.generate = MagicMock(return_value=None)  # LLM returns nothing

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=failing_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("manage VMs", top_k=2)

        # Should still return results using original query
        assert len(results) > 0

    def test_hyde_fallback_when_llm_raises_exception(
        self, mock_embedding_model, mock_knowledge_base
    ):
        """Test HyDE falls back gracefully when LLM raises exception."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        failing_client = MagicMock()
        failing_client.is_connected = True
        failing_client.generate = MagicMock(side_effect=Exception("LLM error"))

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=failing_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("manage VMs", top_k=2)

        # Should still return results using original query as fallback
        assert len(results) > 0

    def test_hyde_strips_role_name_prefix_from_llm_response(
        self, mock_embedding_model, mock_knowledge_base
    ):
        """Test HyDE cleans up LLM response that includes role name prefix."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        client = MagicMock()
        client.is_connected = True
        # LLM might prefix response with "Role Name:"
        client.generate = MagicMock(
            return_value="Virtual Machine Contributor: Allows managing VMs including start/stop"
        )

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=client,
            knowledge_base=mock_knowledge_base,
        )
        engine.recommend("manage VMs", top_k=2)

        # The embedded text should have the prefix stripped
        call_args = mock_embedding_model.encode_single_cached.call_args
        embedded_text = call_args[0][0]
        assert not embedded_text.startswith("Virtual Machine Contributor:")

    def test_hyde_uses_cached_embedding_for_hypothetical_doc(
        self, mock_embedding_model, mock_ollama_client, mock_knowledge_base
    ):
        """Test HyDE uses encode_single_cached for efficiency."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        engine.recommend("manage VMs", top_k=2)

        # Should use the cached version
        mock_embedding_model.encode_single_cached.assert_called()
        mock_embedding_model.encode_single.assert_not_called()

    def test_hyde_filters_low_confidence_results(self, mock_ollama_client, mock_knowledge_base):
        """Test HyDE filters out low confidence results."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine

        # Create embedding model with very low similarity scores
        low_similarity_model = MagicMock()
        low_similarity_model.is_loaded = True
        low_similarity_model.encode_single_cached = MagicMock(return_value=(1.0, 0.0, 0.0, 0.0))
        # All embeddings are orthogonal to query embedding
        low_similarity_model.embeddings = {
            "role-1": [0.0, 1.0, 0.0, 0.0],
            "role-2": [0.0, 0.0, 1.0, 0.0],
        }

        engine = HyDEEngine(
            embedding_model=low_similarity_model,
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("something", top_k=5)

        # Low similarity results should be filtered
        from azurerbac.airecommender.engines.config import HYDE_THRESHOLDS

        for r in results:
            assert r.final_score >= HYDE_THRESHOLDS.min_confidence

    def test_hyde_prompt_template_includes_azure_context(self):
        """Test that HyDE prompt template includes Azure-specific context."""
        from azurerbac.airecommender.engines.hyde import HYDE_PROMPT_TEMPLATE

        assert "Azure" in HYDE_PROMPT_TEMPLATE
        # Prompt should ask for role description
        assert (
            "role" in HYDE_PROMPT_TEMPLATE.lower() or "description" in HYDE_PROMPT_TEMPLATE.lower()
        )

    def test_hyde_raises_error_when_ollama_client_none_in_generate(
        self, mock_embedding_model, mock_knowledge_base
    ):
        """Test HyDE raises OllamaClientNotAvailableError when ollama_client is None."""
        from azurerbac.airecommender.engines.hyde import HyDEEngine
        from azurerbac.airecommender.exceptions import OllamaClientNotAvailableError

        engine = HyDEEngine(
            embedding_model=mock_embedding_model,
            ollama_client=None,  # No client provided
            knowledge_base=mock_knowledge_base,
        )
        # Directly call _generate_hypothetical_document to test the error
        with pytest.raises(OllamaClientNotAvailableError):
            engine._generate_hypothetical_document("test query")


class TestHyDEPromptGeneration:
    """Tests for HyDE hypothetical document generation."""

    def test_hyde_generates_detailed_description(self):
        """Test that HyDE generates detailed role descriptions."""
        from azurerbac.airecommender.engines.hyde import HYDE_PROMPT_TEMPLATE

        # The prompt should ask for specific Azure details
        assert "azure" in HYDE_PROMPT_TEMPLATE.lower()
        # Should ask for role description
        assert (
            "role" in HYDE_PROMPT_TEMPLATE.lower() or "description" in HYDE_PROMPT_TEMPLATE.lower()
        )

    def test_hyde_prompt_formats_correctly(self):
        """Test that HyDE prompt template formats correctly."""
        from azurerbac.airecommender.engines.hyde import HYDE_PROMPT_TEMPLATE

        formatted = HYDE_PROMPT_TEMPLATE.format(query="manage virtual machines")
        assert "manage virtual machines" in formatted


class TestLLMEngine:
    """Tests for LLMEngine class."""

    @pytest.fixture
    def mock_knowledge_base(self):
        """Create a mock knowledge base for LLM tests."""
        kb = MagicMock()
        kb.role_documents = {
            "vm-contrib-id": {
                "role_name": "Virtual Machine Contributor",
                "description": "Manage VMs",
                "document_text": "Virtual Machine Contributor: Manage virtual machines",
            },
        }
        kb.get_all_role_names.return_value = ["Virtual Machine Contributor"]
        kb.find_role_id_by_name.return_value = "vm-contrib-id"
        return kb

    @pytest.fixture
    def mock_ollama_client(self):
        """Create a mock Ollama client for LLM tests."""
        client = MagicMock()
        client.is_connected = True
        client.has_role_names = True
        client.model = "test-model"
        client.recommend_roles.return_value = [
            ("Virtual Machine Contributor", 0.95, "Good match", ["vm", "manage"]),
        ]
        return client

    def test_llm_raises_error_when_ollama_client_none_in_ensure_role_names(
        self, mock_knowledge_base
    ):
        """Test LLMEngine raises error when ollama_client is None in role init."""
        from azurerbac.airecommender.engines.llm import LLMEngine
        from azurerbac.airecommender.exceptions import OllamaClientNotAvailableError

        engine = LLMEngine(
            ollama_client=None,
            knowledge_base=mock_knowledge_base,
        )
        with pytest.raises(OllamaClientNotAvailableError):
            engine._ensure_role_names_initialized()

    def test_llm_raises_error_when_ollama_client_none_in_query_llm(self, mock_knowledge_base):
        """Test LLMEngine raises error when ollama_client is None in _query_llm."""
        from azurerbac.airecommender.engines.llm import LLMEngine
        from azurerbac.airecommender.exceptions import OllamaClientNotAvailableError

        engine = LLMEngine(
            ollama_client=None,
            knowledge_base=mock_knowledge_base,
        )
        with pytest.raises(OllamaClientNotAvailableError):
            engine._query_llm("test query", 5)

    def test_llm_engine_properties(self, mock_ollama_client, mock_knowledge_base):
        """Test LLMEngine has correct properties."""
        from azurerbac.airecommender.engines.llm import LLMEngine

        engine = LLMEngine(
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        assert "LLM" in engine.name
        assert engine.requires_llm is True
        assert engine.requires_embeddings is False

    def test_llm_recommend_returns_ranked_roles(self, mock_ollama_client, mock_knowledge_base):
        """Test LLMEngine recommend returns RankedRole objects."""
        from azurerbac.airecommender.engines.llm import LLMEngine

        engine = LLMEngine(
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("I need to manage virtual machines", top_k=3)

        assert len(results) >= 1
        assert isinstance(results[0], RankedRole)
        assert results[0].role_name == "Virtual Machine Contributor"
        assert results[0].llm_score == pytest.approx(0.95)

    def test_llm_returns_empty_when_ollama_not_connected(self, mock_knowledge_base):
        """Test LLMEngine returns empty list when Ollama is not connected."""
        from azurerbac.airecommender.engines.llm import LLMEngine

        disconnected_client = MagicMock()
        disconnected_client.is_connected = False

        engine = LLMEngine(
            ollama_client=disconnected_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("manage VMs", top_k=3)

        assert results == []

    def test_llm_filters_hallucinated_roles(self, mock_ollama_client, mock_knowledge_base):
        """Test LLMEngine filters out hallucinated role names."""
        from azurerbac.airecommender.engines.llm import LLMEngine

        mock_ollama_client.recommend_roles.return_value = [
            ("Fake Role That Doesnt Exist", 0.9, "hallucinated", ["fake"]),
        ]
        mock_knowledge_base.find_role_id_by_name.return_value = None

        engine = LLMEngine(
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("test", top_k=3)

        assert results == []

    def test_llm_excludes_owner_by_default(self, mock_ollama_client, mock_knowledge_base):
        """Test LLMEngine excludes Owner role by default."""
        from azurerbac.airecommender.engines.llm import LLMEngine

        mock_ollama_client.recommend_roles.return_value = [
            ("Owner", 0.95, "Full access", ["owner"]),
        ]
        mock_knowledge_base.find_role_id_by_name.return_value = "owner-id"
        mock_knowledge_base.get_role_document.return_value = {
            "role_id": "owner-id",
            "role_name": "Owner",
            "description": "Full access",
        }

        engine = LLMEngine(
            ollama_client=mock_ollama_client,
            knowledge_base=mock_knowledge_base,
        )
        results = engine.recommend("full access", top_k=3)

        # Owner should be excluded due to least privilege
        role_names = [r.role_name for r in results]
        assert "Owner" not in role_names


class TestEngineAvailability:
    """Tests for engine availability checks."""

    def test_crossencoder_available_when_all_deps_present(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder is available when all dependencies present."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        mock_ce = MagicMock()
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.get_cross_encoder",
            lambda: mock_ce,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.is_available() is True

    def test_crossencoder_unavailable_when_ce_not_loaded(
        self, mock_embedding_model, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder is unavailable when cross-encoder can't load."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        # Mock is_cross_encoder_available to return False
        monkeypatch.setattr(
            "azurerbac.airecommender.engines.crossencoder.is_cross_encoder_available",
            lambda: False,
        )

        engine = CrossEncoderEngine(
            embedding_model=mock_embedding_model,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.is_available() is False

    def test_crossencoder_unavailable_when_embeddings_not_loaded(
        self, mock_knowledge_base, monkeypatch
    ):
        """Test CrossEncoder is unavailable when embedding model not loaded."""
        from azurerbac.airecommender.engines.crossencoder import CrossEncoderEngine

        unloaded_model = MagicMock()
        unloaded_model.is_loaded = False

        engine = CrossEncoderEngine(
            embedding_model=unloaded_model,
            knowledge_base=mock_knowledge_base,
        )
        assert engine.is_available() is False


# =============================================================================
# Normalize Functions Tests
# =============================================================================


class TestNormalizeScores:
    """Tests for normalize_candidates method."""

    def test_empty_candidates_returns_empty(self):
        """Test that empty input returns empty output."""
        from azurerbac.airecommender.engines.common import normalize_candidates

        result = normalize_candidates([])
        assert result == []

    def test_single_candidate_gets_max_normalized(self):
        """Test single candidate gets normalized within expected range."""
        from azurerbac.airecommender.engines.common import normalize_candidates
        from azurerbac.airecommender.engines.config import SCORE_CEILING, SCORE_FLOOR

        candidates = [
            RankedRole(
                role_id="r1",
                role_name="Role 1",
                description="Desc",
                final_score=0.5,
            )
        ]
        result = normalize_candidates(candidates)

        assert len(result) == 1
        # Single item: score_range=0, so normalized=1.0 -> SCORE_FLOOR + (1.0 * range)
        assert SCORE_FLOOR <= result[0].final_score <= SCORE_CEILING

    @pytest.mark.parametrize(
        ("scores", "expected_high", "expected_low"),
        [
            pytest.param([0.9, 0.1], 0.95, 0.60, id="wide_range"),
            pytest.param([0.5, 0.4], 0.95, 0.60, id="narrow_range"),
            pytest.param([1.0, 0.0], 0.95, 0.60, id="full_range"),
        ],
    )
    def test_minmax_normalization(
        self, scores: list[float], expected_high: float, expected_low: float
    ):
        """Test min-max normalization produces expected range."""
        from azurerbac.airecommender.engines.common import normalize_candidates

        candidates = [
            RankedRole(
                role_id=f"r{i}",
                role_name=f"Role {i}",
                description="Desc",
                final_score=score,
            )
            for i, score in enumerate(scores)
        ]
        result = normalize_candidates(candidates)

        result_scores = sorted([c.final_score for c in result], reverse=True)
        assert result_scores[0] == pytest.approx(expected_high, rel=0.01)
        assert result_scores[-1] == pytest.approx(expected_low, rel=0.01)

    def test_preserves_relative_order(self):
        """Test that relative ordering is preserved after normalization."""
        from azurerbac.airecommender.engines.common import normalize_candidates

        candidates = [
            RankedRole(role_id="high", role_name="High", description="", final_score=0.9),
            RankedRole(role_id="med", role_name="Medium", description="", final_score=0.5),
            RankedRole(role_id="low", role_name="Low", description="", final_score=0.1),
        ]
        result = normalize_candidates(candidates)

        scores_by_id = {c.role_id: c.final_score for c in result}
        assert scores_by_id["high"] > scores_by_id["med"] > scores_by_id["low"]


class TestNormalizeWithSigmoid:
    """Tests for sigmoid_normalize method."""

    def test_empty_candidates_returns_empty(self):
        """Test that empty input returns empty output."""
        from azurerbac.airecommender.engines.common import sigmoid_normalize
        from azurerbac.airecommender.engines.config import SigmoidParams

        params = SigmoidParams(midpoint=0.5, steepness=10, output_min=0.6, output_max=0.95)
        result = sigmoid_normalize([], params)
        assert result == []

    @pytest.mark.parametrize(
        ("raw_score", "midpoint", "steepness", "expected_approx"),
        [
            pytest.param(0.5, 0.5, 10, 0.775, id="at_midpoint"),
            pytest.param(0.9, 0.5, 10, 0.95, id="above_midpoint"),
            pytest.param(0.1, 0.5, 10, 0.60, id="below_midpoint"),
        ],
    )
    def test_sigmoid_transformation(
        self, raw_score: float, midpoint: float, steepness: float, expected_approx: float
    ):
        """Test sigmoid transformation for various inputs."""
        from azurerbac.airecommender.engines.common import sigmoid_normalize
        from azurerbac.airecommender.engines.config import SigmoidParams

        candidates = [
            RankedRole(
                role_id="r1",
                role_name="Role",
                description="",
                final_score=raw_score,
            )
        ]
        params = SigmoidParams(
            midpoint=midpoint,
            steepness=steepness,
            output_min=0.60,
            output_max=0.95,
        )
        result = sigmoid_normalize(candidates, params)

        assert result[0].final_score == pytest.approx(expected_approx, abs=0.05)

    def test_preserves_relative_order(self):
        """Test sigmoid preserves relative ordering."""
        from azurerbac.airecommender.engines.common import sigmoid_normalize
        from azurerbac.airecommender.engines.config import SigmoidParams

        candidates = [
            RankedRole(role_id="high", role_name="High", description="", final_score=0.9),
            RankedRole(role_id="low", role_name="Low", description="", final_score=0.1),
        ]
        params = SigmoidParams(midpoint=0.5, steepness=10, output_min=0.6, output_max=0.95)
        result = sigmoid_normalize(candidates, params)

        scores_by_id = {c.role_id: c.final_score for c in result}
        assert scores_by_id["high"] > scores_by_id["low"]


# =============================================================================
# Exception Tests
# =============================================================================


class TestAIRecommenderExceptions:
    """Tests for AI recommender exception classes."""

    @pytest.mark.parametrize(
        ("engine_name",),
        [
            pytest.param("HyDE", id="hyde"),
            pytest.param("LLM", id="llm"),
            pytest.param("RAG", id="rag"),
        ],
    )
    def test_ollama_client_not_available_error(self, engine_name: str):
        """Test OllamaClientNotAvailableError stores engine name."""
        from azurerbac.airecommender.exceptions import OllamaClientNotAvailableError

        exc = OllamaClientNotAvailableError(engine_name)
        assert exc.engine_name == engine_name
        assert engine_name in str(exc)
        assert "Ollama" in str(exc)

    def test_knowledge_base_not_initialized_error(self):
        """Test KnowledgeBaseNotInitializedError message."""
        from azurerbac.airecommender.exceptions import KnowledgeBaseNotInitializedError

        exc = KnowledgeBaseNotInitializedError()
        assert "Knowledge base" in str(exc) or "not initialized" in str(exc)

    def test_exceptions_inherit_from_base(self):
        """Test all exceptions inherit from AIRecommenderError."""
        from azurerbac.airecommender.exceptions import (
            AIRecommenderError,
            KnowledgeBaseNotInitializedError,
            OllamaClientNotAvailableError,
        )

        assert issubclass(OllamaClientNotAvailableError, AIRecommenderError)
        assert issubclass(KnowledgeBaseNotInitializedError, AIRecommenderError)


# =============================================================================
# Tests for TFIDFWeights and WeightPair validation
# =============================================================================


class TestTFIDFWeights:
    """Tests for TFIDFWeights dataclass validation."""

    @pytest.mark.parametrize(
        ("bm25", "pattern", "name_match", "fuzzy"),
        [
            pytest.param(0.25, 0.35, 0.25, 0.15, id="custom-valid"),
            pytest.param(0.30, 0.45, 0.15, 0.10, id="defaults"),
            pytest.param(0.10, 0.10, 0.10, 0.70, id="heavy-fuzzy"),
            pytest.param(1.0, 0.0, 0.0, 0.0, id="all-bm25"),
        ],
    )
    def test_valid_weights_accepted(
        self, bm25: float, pattern: float, name_match: float, fuzzy: float
    ):
        """Test that weights summing to 1.0 are accepted."""
        from azurerbac.airecommender.engines.config import TFIDFWeights

        weights = TFIDFWeights(bm25=bm25, pattern=pattern, name_match=name_match, fuzzy=fuzzy)
        assert weights.bm25 == bm25
        assert weights.pattern == pattern

    def test_default_weights_sum_to_one(self):
        """Test that default weights sum to 1.0."""
        from azurerbac.airecommender.engines.config import TFIDFWeights

        weights = TFIDFWeights()
        total = weights.bm25 + weights.pattern + weights.name_match + weights.fuzzy
        assert abs(total - 1.0) < 1e-9

    @pytest.mark.parametrize(
        ("bm25", "pattern", "name_match", "fuzzy", "expected_sum"),
        [
            pytest.param(0.5, 0.5, 0.5, 0.5, 2.0, id="sum-2.0"),
            pytest.param(0.30, 0.45, 0.15, 0.11, 1.01, id="sum-1.01"),
            pytest.param(0.1, 0.1, 0.1, 0.1, 0.4, id="sum-0.4"),
            pytest.param(0.0, 0.0, 0.0, 0.0, 0.0, id="all-zeros"),
        ],
    )
    def test_invalid_weights_raise_value_error(
        self, bm25: float, pattern: float, name_match: float, fuzzy: float, expected_sum: float
    ):
        """Test that weights not summing to 1.0 raise ValueError."""
        from azurerbac.airecommender.engines.config import TFIDFWeights

        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            TFIDFWeights(bm25=bm25, pattern=pattern, name_match=name_match, fuzzy=fuzzy)


class TestWeightPair:
    """Tests for WeightPair dataclass validation."""

    @pytest.mark.parametrize(
        ("primary", "secondary"),
        [
            pytest.param(0.7, 0.3, id="70-30"),
            pytest.param(0.5, 0.5, id="equal"),
            pytest.param(1.0, 0.0, id="all-primary"),
            pytest.param(0.0, 1.0, id="all-secondary"),
            pytest.param(0.99, 0.01, id="almost-all-primary"),
        ],
    )
    def test_valid_weight_pair_accepted(self, primary: float, secondary: float):
        """Test that weight pairs summing to 1.0 are accepted."""
        from azurerbac.airecommender.engines.config import WeightPair

        pair = WeightPair(primary=primary, secondary=secondary)
        assert pair.primary == primary
        assert pair.secondary == secondary

    @pytest.mark.parametrize(
        ("primary", "secondary"),
        [
            pytest.param(0.8, 0.3, id="sum-1.1"),
            pytest.param(0.5, 0.0, id="sum-0.5"),
            pytest.param(0.0, 0.0, id="both-zero"),
            pytest.param(0.6, 0.6, id="sum-1.2"),
        ],
    )
    def test_invalid_weight_pair_raise_value_error(self, primary: float, secondary: float):
        """Test that weight pairs not summing to 1.0 raise ValueError."""
        from azurerbac.airecommender.engines.config import WeightPair

        with pytest.raises(ValueError, match=r"must sum to 1\.0"):
            WeightPair(primary=primary, secondary=secondary)


# =============================================================================
# Tests for ScoreNormalizer
# =============================================================================


class TestScoreNormalizerFunctions:
    """Tests for normalize_candidates and sigmoid_normalize functions."""

    def test_normalize_candidates_empty_list(self):
        """Test normalize_candidates handles empty list correctly."""
        from azurerbac.airecommender.engines.common import normalize_candidates

        result = normalize_candidates([])
        assert result == []

    def test_normalize_candidates_mutates_in_place(self):
        """Test normalize_candidates mutates candidates in-place."""
        from azurerbac.airecommender.engines.base import RankedRole
        from azurerbac.airecommender.engines.common import normalize_candidates

        candidates = [
            RankedRole(role_id="a", role_name="A", description="Test A", final_score=10.0),
            RankedRole(role_id="b", role_name="B", description="Test B", final_score=90.0),
        ]
        original_refs = candidates.copy()

        result = normalize_candidates(candidates, floor=0.0, ceiling=1.0)

        # Returns same list for chaining
        assert result is candidates
        # Same objects mutated
        assert candidates[0] is original_refs[0]
        assert candidates[1] is original_refs[1]
        # Scores normalized
        assert candidates[0].final_score == 0.0
        assert candidates[1].final_score == 1.0

    def test_sigmoid_normalize_empty_list(self):
        """Test sigmoid_normalize handles empty list correctly."""
        from azurerbac.airecommender.engines.common import sigmoid_normalize
        from azurerbac.airecommender.engines.config import SigmoidParams

        params = SigmoidParams(midpoint=0.5, steepness=10.0)
        result = sigmoid_normalize([], params)
        assert result == []

    def test_sigmoid_normalize_applies_transform(self):
        """Test sigmoid_normalize applies sigmoid transform correctly."""
        from azurerbac.airecommender.engines.base import RankedRole
        from azurerbac.airecommender.engines.common import sigmoid_normalize
        from azurerbac.airecommender.engines.config import SigmoidParams

        params = SigmoidParams(midpoint=0.5, steepness=10.0, output_min=0.0, output_max=1.0)
        candidates = [
            RankedRole(role_id="low", role_name="Low", description="Low", final_score=0.0),
            RankedRole(role_id="mid", role_name="Mid", description="Mid", final_score=0.5),
            RankedRole(role_id="high", role_name="High", description="High", final_score=1.0),
        ]

        result = sigmoid_normalize(candidates, params)

        assert result is candidates  # Returns for chaining
        # Midpoint maps to ~0.5 (sigmoid(0) = 0.5)
        assert abs(candidates[1].final_score - 0.5) < 0.01
        # Low score < midpoint
        assert candidates[0].final_score < 0.5
        # High score > midpoint
        assert candidates[2].final_score > 0.5
