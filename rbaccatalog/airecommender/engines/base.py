"""Base recommendation engine abstraction."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from rbaccatalog.core.types import JsonDict

type Embedding = tuple[float, ...]

if TYPE_CHECKING:
    from rbaccatalog.airecommender.embeddings import EmbeddingModel
    from rbaccatalog.airecommender.engines.enhanced_tfidf import EnhancedTFIDFRecommender
    from rbaccatalog.airecommender.knowledge import RoleKnowledgeBase
    from rbaccatalog.airecommender.llm import OllamaClient

logger = logging.getLogger(__name__)

_LLM_DESC_TRUNCATE: Final = 100


@dataclass(slots=True)
class RankedRole:
    """Role with ranking scores."""

    role_id: str
    role_name: str
    description: str
    tfidf_score: float = 0.0
    embedding_score: float = 0.0
    llm_score: float = 0.0
    final_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_embedding(
        cls,
        role_id: str,
        role_name: str,
        description: str,
        score: float,
        keywords: list[str] | None = None,
    ) -> RankedRole:
        return cls(
            role_id=role_id,
            role_name=role_name,
            description=description,
            embedding_score=score,
            final_score=score,
            matched_keywords=keywords or [],
        )


class BaseRecommenderEngine(ABC):
    """Base class for all recommendation engines."""

    def __init__(
        self,
        knowledge_base: RoleKnowledgeBase,
        ollama_client: OllamaClient | None = None,
        embedding_model: EmbeddingModel | None = None,
        tfidf_recommender: EnhancedTFIDFRecommender | None = None,
    ) -> None:
        self.knowledge_base = knowledge_base
        self.ollama_client = ollama_client
        self.embedding_model = embedding_model
        self.tfidf_recommender = tfidf_recommender

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def requires_llm(self) -> bool: ...

    @property
    @abstractmethod
    def requires_embeddings(self) -> bool: ...

    @abstractmethod
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]: ...

    def is_available(self) -> bool:
        llm_ok = not self.requires_llm or self.is_llm_available
        embeddings_ok = not self.requires_embeddings or self.is_embeddings_available
        return llm_ok and embeddings_ok

    def _log_start(self, query: str, top_k: int) -> None:
        logger.info("%s: query='%s...' top_k=%d", self.name, query[:50], top_k)

    def _log_complete(self, results: list[RankedRole], show_top: int = 3) -> None:
        if results and show_top > 0:
            top_names = [r.role_name for r in results[:show_top]]
            logger.info("%s: %d results. Top: %s", self.name, len(results), top_names)
        else:
            logger.info("%s: %d results", self.name, len(results))

    def _encode_cached(self, text: str) -> Embedding | None:
        if not self.is_embeddings_available:
            logger.warning("%s: Embedding model not loaded", self.__class__.__name__)
            return None
        assert self.embedding_model is not None
        return self.embedding_model.encode_single_cached(text)

    @staticmethod
    def _filter_min_confidence(
        candidates: list[RankedRole], *, threshold: float
    ) -> list[RankedRole]:
        if not candidates:
            return candidates
        return [c for c in candidates if c.final_score >= threshold]

    def _finalize_results(
        self,
        candidates: list[RankedRole],
        *,
        threshold: float,
        normalizer: Callable[[list[RankedRole]], list[RankedRole]] | None = None,
    ) -> list[RankedRole]:
        original_count = len(candidates)
        candidates = self._filter_min_confidence(candidates, threshold=threshold)

        if original_count > len(candidates):
            logger.debug(
                "%s: Filtered %d low-confidence results (threshold=%.2f)",
                self.__class__.__name__,
                original_count - len(candidates),
                threshold,
            )

        if normalizer is not None:
            candidates = normalizer(candidates)
        else:
            from rbaccatalog.airecommender.engines.common import normalize_candidates

            candidates = normalize_candidates(candidates)

        return candidates

    @property
    def is_llm_available(self) -> bool:
        return self.ollama_client is not None and self.ollama_client.is_connected

    @property
    def is_embeddings_available(self) -> bool:
        return self.embedding_model is not None and self.embedding_model.is_loaded

    def get_role_document(self, role_id: str) -> JsonDict | None:
        return self.knowledge_base.role_documents.get(role_id)

    def _llm_rerank(
        self,
        query: str,
        candidates: list[RankedRole],
        top_k: int,
    ) -> list[RankedRole]:
        if not self.is_llm_available:
            logger.warning("%s: No LLM connection", self.__class__.__name__)
            return candidates[:top_k]

        candidate_text = "\n".join(
            f"- {c.role_name}: {c.description[:_LLM_DESC_TRUNCATE]}..." for c in candidates
        )

        prompt = f"""Given this Azure RBAC request: "{query}"

Rank these candidate roles from most to least appropriate (top 5 only):
{candidate_text}

Return ONLY the role names in order, one per line, most appropriate first."""

        try:
            if self.ollama_client is None:
                logger.warning(
                    "%s: Ollama client not available for reranking", self.__class__.__name__
                )
                return candidates[:top_k]
            response = self.ollama_client.generate(prompt)
            if not response:
                logger.warning("%s: Empty response from LLM", self.__class__.__name__)
                return candidates[:top_k]

            # Parse LLM response - extract and normalize role names
            llm_ranked_names = self._parse_llm_ranking_response(response)

            # Re-order candidates based on LLM ranking
            name_to_candidate = {c.role_name.lower(): c for c in candidates}
            reranked: list[RankedRole] = []
            seen_ids: set[str] = set()

            for i, name in enumerate(llm_ranked_names[:top_k]):
                if name in name_to_candidate:
                    c = name_to_candidate[name]
                    if c.role_id not in seen_ids:
                        c.llm_score = 1.0 - (i * 0.1)
                        c.final_score = (c.embedding_score + c.llm_score) / 2
                        reranked.append(c)
                        seen_ids.add(c.role_id)

            # Add remaining candidates not ranked by LLM (penalized score)
            for c in candidates:
                if c.role_id not in seen_ids and len(reranked) < top_k:
                    c.final_score = c.embedding_score * 0.5
                    reranked.append(c)
                    seen_ids.add(c.role_id)

            reranked.sort(key=lambda r: r.final_score, reverse=True)
            return reranked[:top_k]

        except Exception:
            logger.exception("%s: LLM rerank failed", self.__class__.__name__)
            return candidates[:top_k]

    @staticmethod
    def _parse_llm_ranking_response(response: str) -> list[str]:
        names: list[str] = []
        for line in response.strip().split("\n"):
            name = line.strip().lstrip("0123456789.-) ").strip()
            if name:
                names.append(name.lower())
        return names

    def should_exclude_role(self, role_name: str, exclude_owner: bool) -> bool:
        return exclude_owner and role_name.lower() == "owner"

    def retrieve_by_embedding(
        self,
        query: str,
        query_embedding: Embedding | list[float],
        top_k: int,
        exclude_owner: bool,
        *,
        use_search_vector: bool = False,
    ) -> list[RankedRole]:
        if self.embedding_model is None or not self.embedding_model.is_loaded:
            logger.warning("%s: Embedding model not available", self.__class__.__name__)
            return []

        embeddings = self.embedding_model.embeddings
        if not embeddings:
            logger.warning("%s: No embeddings available", self.__class__.__name__)
            return []

        retrieval_k = top_k * 2
        if use_search_vector and self.embedding_model is not None:
            scores = self.embedding_model.search_vector(list(query_embedding), retrieval_k)
        else:
            from rbaccatalog.airecommender.engines.common import top_k_similar

            scores = top_k_similar(query_embedding, embeddings, retrieval_k)

        return self._build_ranked_roles(query, scores, top_k, exclude_owner)

    def _build_ranked_roles(
        self,
        query: str,
        scores: Iterable[tuple[str, float]],
        top_k: int,
        exclude_owner: bool,
    ) -> list[RankedRole]:
        from rbaccatalog.airecommender.knowledge import extract_keywords

        keywords = extract_keywords(query)
        candidates: list[RankedRole] = []

        for role_id, score in scores:
            doc = self.get_role_document(role_id)
            if not doc or self.should_exclude_role(doc["role_name"], exclude_owner):
                continue

            candidates.append(
                RankedRole.from_embedding(
                    role_id=role_id,
                    role_name=doc["role_name"],
                    description=doc["description"],
                    score=score,
                    keywords=keywords,
                )
            )
            if len(candidates) >= top_k:
                break

        return candidates
