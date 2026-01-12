"""ColBERT recommendation engine.

Uses ColBERT (Contextualized Late Interaction over BERT) for token-level
matching between queries and role documents. Unlike bi-encoders that compress
everything into a single vector, ColBERT preserves token-level embeddings
and uses MaxSim (maximum similarity) scoring.

Note: Requires a C++ compiler (g++) at runtime to build the segmented_maxsim
extension. If unavailable, the engine will raise ColBERTInitializationError.
"""

from __future__ import annotations

import logging
import os
import threading
import warnings
from pathlib import Path
from typing import Any, Final, override

# Suppress GitPython errors in environments without git (e.g., Azure App Service)
# Must be set before any import of ragatouille/colbert
os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")

# Suppress HuggingFace tokenizers parallelism warning after fork
# ColBERT/RAGatouille uses tokenizers internally, and FastAPI workers may fork
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# Suppress torch.cuda.amp deprecation warnings from colbert library
# These come from colbert/utils/amp.py using deprecated GradScaler API
warnings.filterwarnings("ignore", message=".*torch.cuda.amp.GradScaler.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*CUDA is not available.*", category=UserWarning)

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.common import normalize_with_sigmoid
from azurerbac.airecommender.engines.config import COLBERT_SIGMOID, COLBERT_THRESHOLDS
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.knowledge import extract_keywords
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.core.singleton import ThreadSafeSingleton

logger = logging.getLogger(__name__)

# Type alias
type SearchResult = tuple[str, float]

# ColBERT model - the v2 is trained for better retrieval
COLBERT_MODEL: Final = "colbert-ir/colbertv2.0"

# Pre-built index location (shipped with repo for fast startup)
INDEX_DIR: Final = Path(__file__).parent.parent / "models" / "azure_roles"


class ColBERTInitializationError(Exception):
    """Raised when ColBERT fails to initialize (e.g., missing git, failed index load)."""


# =============================================================================
# ColBERTEngine - Main Entry Point
# =============================================================================


@EngineRegistry.register(RecommenderMode.COLBERT)
class ColBERTEngine(BaseRecommenderEngine):
    """ColBERT recommendation engine with token-level matching."""

    @property
    @override
    def name(self) -> str:
        return "ColBERT: Token-level late interaction"

    @property
    @override
    def requires_llm(self) -> bool:
        return False

    @property
    @override
    def requires_embeddings(self) -> bool:
        # ColBERT uses its own embeddings, not the shared embedding model
        return False

    @override
    def is_available(self) -> bool:
        """Check if ColBERT is available."""
        import importlib.util

        return importlib.util.find_spec("ragatouille") is not None

    @staticmethod
    def _normalize_scores(candidates: list[RankedRole]) -> list[RankedRole]:
        """Normalize ColBERT MaxSim scores to 0-1 confidence range.

        Uses COLBERT_SIGMOID config for sigmoid normalization parameters.
        """
        candidates = normalize_with_sigmoid(
            candidates,
            midpoint=COLBERT_SIGMOID.midpoint,
            steepness=COLBERT_SIGMOID.steepness,
            output_min=COLBERT_SIGMOID.output_min,
            output_max=COLBERT_SIGMOID.output_max,
        )
        logger.info(
            "Normalized %d ColBERT scores: [%s]",
            len(candidates),
            ", ".join(f"{c.role_name}({c.final_score:.0%})" for c in candidates[:3]),
        )
        return candidates

    def _ensure_index(self) -> bool:
        """Ensure ColBERT index is built from knowledge base."""
        index = get_colbert_index()

        # Fast path: already loaded, skip document processing
        if index.is_loaded:
            return True

        # Build index from knowledge base documents
        if not self.knowledge_base or not self.knowledge_base.role_documents:
            logger.warning("ColBERT: No role documents available for indexing")
            return False

        documents = {
            role_id: doc["document_text"]
            for role_id, doc in self.knowledge_base.role_documents.items()
        }

        # build_index returns early if hash matches (no work needed)
        return index.build_index(documents)

    @override
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]:
        """Get recommendations using ColBERT token-level matching.

        Args:
            query: Natural language query
            top_k: Number of recommendations
            exclude_owner: Whether to exclude Owner role

        Returns:
            List of RankedRole objects sorted by ColBERT score
        """
        self._log_start(query, top_k)

        # Ensure index is built
        if not self._ensure_index():
            raise ColBERTInitializationError("ColBERT: Index not available")

        # Search with ColBERT
        index = get_colbert_index()
        search_results = index.search(query, top_k=top_k * 2)

        if not search_results:
            logger.debug("ColBERT: No search results")
            return []

        logger.debug("ColBERT: Got %d search results", len(search_results))

        # Convert to RankedRole objects
        candidates: list[RankedRole] = []
        keywords = extract_keywords(query)

        for role_id, score in search_results:
            doc = self.get_role_document(role_id)
            if not doc or self.should_exclude_role(doc["role_name"], exclude_owner):
                continue

            candidates.append(
                RankedRole(
                    role_id=role_id,
                    role_name=doc["role_name"],
                    description=doc["description"],
                    embedding_score=score,
                    final_score=score,
                    matched_keywords=keywords,
                )
            )

            if len(candidates) >= top_k:
                break

        # Filter and normalize
        candidates = self._filter_min_confidence(
            candidates, threshold=COLBERT_THRESHOLDS.min_confidence
        )
        candidates = self._normalize_scores(candidates)

        self._log_complete(candidates)
        return candidates


# =============================================================================
# ColBERTIndex - Implementation Detail
# =============================================================================


class ColBERTIndex:
    """Wrapper for ColBERT index using RAGatouille.

    Loads pre-built index from disk if available (fast, ~2s).
    Falls back to building index on-the-fly if not found (~20s).
    The pre-built index should be generated locally and committed to the repo.

    Use get_colbert_index() to get the shared singleton instance.
    """

    def __init__(self) -> None:
        self._rag: Any = None
        self._is_loaded = False
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        """Check if the ColBERT index is loaded."""
        return self._is_loaded

    def _try_load_ragatouille(self) -> bool:
        """Try to import RAGatouille library."""
        import importlib.util

        if importlib.util.find_spec("ragatouille") is not None:
            return True
        logger.warning("RAGatouille not installed. Install with: pip install ragatouille")
        return False

    def _has_prebuilt_index(self) -> bool:
        """Check if a pre-built index exists on disk."""
        # Check for key ColBERT index files
        required_files = ["metadata.json", "plan.json", "0.codes.pt"]
        return all((INDEX_DIR / f).exists() for f in required_files)

    def _try_load_from_disk(self) -> bool:
        """Try to load pre-built index from disk.

        Returns:
            True if index was loaded successfully
        """
        if not self._has_prebuilt_index():
            logger.debug("ColBERT: No pre-built index found on disk")
            return False

        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=UserWarning)
                from ragatouille import RAGPretrainedModel

                logger.info("ColBERT: Loading pre-built index from %s...", INDEX_DIR)
                self._rag = RAGPretrainedModel.from_index(str(INDEX_DIR))
            self._is_loaded = True
            logger.info("ColBERT: Successfully loaded pre-built index")
            return True

        except Exception as e:
            error_msg = f"ColBERT: Failed to load index from disk: {e}"
            logger.exception(error_msg)
            raise ColBERTInitializationError(error_msg) from e

    def warmup(self) -> bool:
        """Fully initialize ColBERT: load index from disk and pre-init searcher.

        This avoids slow first request by:
        1. Loading pre-built index from disk
        2. Running a dummy search to force RAGatouille to initialize its internal searcher

        Returns:
            True if fully initialized, False otherwise
        """
        # Step 1: Load index from disk if not already loaded
        if not self._is_loaded:
            if not self._try_load_ragatouille():
                logger.warning("ColBERT: RAGatouille not available")
                return False
            try:
                self._try_load_from_disk()
            except ColBERTInitializationError:
                logger.warning("ColBERT: No pre-built index found")
                return False

        if not self._is_loaded or self._rag is None:
            return False

        # Step 2: Run a dummy search to force RAGatouille to initialize its searcher
        # This is more reliable than calling internal _load_searcher() which RAGatouille
        # may bypass when .search() is called
        try:
            logger.info("ColBERT: Warming up searcher with dummy query...")
            _ = self._rag.search("warmup query", k=1)
            logger.info("ColBERT: Searcher warmed up successfully")
            return True
        except Exception as e:
            logger.exception("ColBERT: Searcher warmup failed: %s", e)
            return False

    def build_index(self, documents: dict[str, str], force_rebuild: bool = False) -> bool:
        """Build or load ColBERT index.

        Tries to load pre-built index first (fast). Only rebuilds if:
        - No pre-built index exists
        - force_rebuild=True

        Args:
            documents: Dict of role_id -> document_text
            force_rebuild: Force rebuild even if pre-built index exists

        Returns:
            True if index was built successfully
        """
        if not self._try_load_ragatouille():
            return False

        with self._lock:
            # Already loaded
            if self._is_loaded and not force_rebuild:
                logger.debug("ColBERT: Index already loaded in memory")
                return True

            # Try loading pre-built index from disk first (fast, ~2s)
            if not force_rebuild and self._try_load_from_disk():
                return True

            # Fall back to building index on-the-fly (slow, ~20s)
            try:
                from ragatouille import RAGPretrainedModel

                logger.info("ColBERT: Building index for %d documents...", len(documents))
                logger.info("ColBERT: This may take ~20s. Consider pre-building the index.")

                # Initialize RAGatouille with ColBERT model
                self._rag = RAGPretrainedModel.from_pretrained(COLBERT_MODEL)

                # Prepare documents for indexing
                doc_texts = []
                doc_ids = []

                for role_id, doc_text in documents.items():
                    doc_texts.append(doc_text)
                    doc_ids.append(role_id)

                # Build index (saves to .ragatouille/ by default)
                self._rag.index(
                    collection=doc_texts,
                    document_ids=doc_ids,
                    index_name="azure_roles",
                    max_document_length=512,
                    split_documents=False,
                )

                self._is_loaded = True
                logger.info("ColBERT: Index built with %d documents", len(documents))
                return True

            except Exception as e:
                error_msg = f"ColBERT: Failed to build index: {e}"
                logger.exception(error_msg)
                self._is_loaded = False
                raise ColBERTInitializationError(error_msg) from e

    def search(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """Search the ColBERT index.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of (role_id, score) tuples sorted by relevance
        """
        if not self._is_loaded or self._rag is None:
            logger.warning("ColBERT: Index not loaded, cannot search")
            return []

        try:
            # RAGatouille returns list of dicts with 'content', 'score', 'document_id'
            results = self._rag.search(query, k=top_k)

            scored_roles = []
            for result in results:
                # document_id is the role_id we passed during indexing
                role_id = result.get("document_id")
                score = result.get("score", 0.0)
                if role_id:
                    scored_roles.append((role_id, score))

            return scored_roles

        except Exception as e:
            logger.exception("ColBERT: Search failed: %s", e)
            return []


# Module-level singleton
_colbert_index: ThreadSafeSingleton[ColBERTIndex] = ThreadSafeSingleton(ColBERTIndex)


# For external access (e.g., startup warmup)
def get_colbert_index() -> ColBERTIndex:
    """Get the global ColBERT index instance (thread-safe singleton)."""
    return _colbert_index.get()
