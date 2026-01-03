"""HyDE (Hypothetical Document Embeddings) recommendation engine.

Given a vague query like "manage VMs", HyDE asks the LLM to generate a
hypothetical role description, then searches for roles similar to that
generated description. Works well for ambiguous queries.

Paper: https://arxiv.org/abs/2212.10496
"""

from __future__ import annotations

import logging
from typing import Final, override

from azurerbac.airecommender.engines.base import BaseRecommenderEngine, RankedRole
from azurerbac.airecommender.engines.config import HYDE_THRESHOLDS
from azurerbac.airecommender.engines.registry import EngineRegistry
from azurerbac.airecommender.modes import RecommenderMode

logger = logging.getLogger(__name__)

# HyDE LLM model - qwen2.5 for Azure terminology accuracy
HYDE_MODEL: Final = "qwen2.5:0.5b"

# Prompt template for generating hypothetical role descriptions
HYDE_PROMPT_TEMPLATE: Final = """Complete this Azure role description in ONE sentence:

For "{query}": Lets you"""


@EngineRegistry.register(RecommenderMode.HYDE)
class HyDEEngine(BaseRecommenderEngine):
    """HyDE (Hypothetical Document Embeddings) recommendation engine."""

    @property
    @override
    def name(self) -> str:
        return "HyDE: Hypothetical Document Embeddings"

    @property
    @override
    def requires_llm(self) -> bool:
        return True

    @property
    @override
    def requires_embeddings(self) -> bool:
        return True

    @override
    def recommend(
        self,
        query: str,
        top_k: int = 5,
        exclude_owner: bool = True,
    ) -> list[RankedRole]:
        """Get recommendations using HyDE approach.

        Args:
            query: Natural language query (can be vague)
            top_k: Number of final recommendations
            exclude_owner: Whether to exclude Owner role

        Returns:
            List of RankedRole objects sorted by similarity to hypothetical doc
        """
        self._log_start(query, top_k)

        if self.embedding_model is None or not self.embedding_model.is_loaded:
            logger.warning("HyDE: Embedding model not loaded")
            return []

        if not self.is_llm_available:
            logger.warning("HyDE: Ollama not connected")
            return []

        # Step 1: Generate hypothetical role description
        hypothetical_doc = self._generate_hypothetical_document(query)
        if not hypothetical_doc:
            hypothetical_doc = query  # Fall back to direct query

        logger.debug("HyDE: Generated hypothetical doc: '%s...'", hypothetical_doc[:100])

        # Step 2: Embed and search
        hypo_embedding = self._encode_cached(hypothetical_doc)
        if hypo_embedding is None:
            return []

        candidates = self.retrieve_by_embedding(query, hypo_embedding, top_k, exclude_owner)
        if not candidates:
            return []

        # Filter and normalize
        candidates = self._finalize_results(candidates, threshold=HYDE_THRESHOLDS.min_confidence)

        self._log_complete(candidates)
        return candidates

    def _generate_hypothetical_document(self, query: str) -> str | None:
        """Generate a hypothetical role description using LLM.

        Uses qwen2.5:0.5b - a fast, small, general-purpose model that
        produces coherent Azure role descriptions.

        Args:
            query: User's original query

        Returns:
            Generated role description or None if generation fails
        """
        prompt = HYDE_PROMPT_TEMPLATE.format(query=query)

        try:
            # Use qwen2.5:0.5b with low temp for focused, concise output
            # Short max_tokens (60) forces single-sentence descriptions
            response = self.ollama_client.generate(
                prompt, max_tokens=60, model=HYDE_MODEL, temperature=0.3
            )

            if response:
                # Log the raw LLM response for debugging
                log_response = response[:200] + "..." if len(response) > 200 else response
                logger.info("HyDE LLM response (%s): %s", HYDE_MODEL, log_response)

                # Clean up the response - build full description
                doc = "Lets you " + response.strip()

                # Remove quotes the LLM might add
                doc = doc.replace('"', "").replace("'", "")

                # Remove repeated "For X:" if LLM echoed the prompt
                if "For " in doc and ": " in doc[:60]:
                    doc = doc.split(": ", 1)[-1]
                    if not doc.lower().startswith("lets you"):
                        doc = "Lets you " + doc

                logger.debug("HyDE: Cleaned hypothetical doc: %s...", doc[:100])
                return doc
            logger.warning("HyDE: LLM returned empty response")
            return None
        except Exception as e:
            logger.exception("HyDE: Failed to generate hypothetical document: %s", e)
            return None
