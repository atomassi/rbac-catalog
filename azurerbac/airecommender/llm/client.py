"""Ollama LLM client for role recommendations."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Final

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from azurerbac.airecommender.llm.json_repair import (
    extract_json_from_markdown,
    parse_json_with_repair,
)
from azurerbac.airecommender.llm.prompts import build_role_recommendation_prompt
from azurerbac.settings import Settings

logger = logging.getLogger(__name__)


def _get_ollama_base_url() -> str:
    """Get Ollama base URL from settings."""
    return Settings.get().ollama_base_url


def _get_ollama_model() -> str:
    """Get Ollama model from settings."""
    return Settings.get().ollama_model


# LLM generation constants
DEFAULT_LLM_MAX_TOKENS: Final[int] = 200
DEFAULT_LLM_TEMPERATURE: Final[float] = 0.3
DEFAULT_TIMEOUT_SECONDS: Final[int] = 30

# Confidence class to numeric mapping (percentage)
CONFIDENCE_MAP: Final[dict[str, float]] = {
    "very_high": 0.95,
    "high": 0.80,
    "medium": 0.60,
    "low": 0.40,
    "very_low": 0.15,
}

# Role suffixes for fuzzy match bonus
_ROLE_SUFFIXES: Final[tuple[str, ...]] = (
    "contributor",
    "reader",
    "owner",
    "operator",
    "administrator",
    "user",
)


@dataclass(slots=True)
class OllamaClient:
    """Client for Ollama LLM API.

    Handles connection management, text generation, and response parsing
    for Azure RBAC role recommendations.
    """

    base_url: str = field(default_factory=_get_ollama_base_url)
    model: str = field(default_factory=_get_ollama_model)
    _connected: bool = field(default=False, repr=False)
    # All known role names for fuzzy matching
    _known_role_names: list[str] = field(default_factory=list, repr=False)
    # Lowercase -> original casing lookup for exact match and fuzzy matching
    _known_roles_lookup: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def has_role_names(self) -> bool:
        return bool(self._known_role_names)

    def try_connect(self) -> bool:
        """Try to connect to Ollama server.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]

            if self.model in models or any(self.model.split(":")[0] in m for m in models):
                self._connected = True
                logger.info("Connected to Ollama server with model: %s", self.model)
                return True
            logger.warning("Ollama model '%s' not found. Available: %s", self.model, models)
            # Try to use first available model
            if models:
                self._connected = True
                self.model = models[0]
                logger.info("Using available Ollama model: %s", self.model)
                return True
            return False

        except httpx.HTTPError as e:
            logger.exception("Ollama server not available at %s: %s", self.base_url, e)
            return False
        except Exception as e:
            logger.exception("Failed to connect to Ollama: %s", e)
            return False

    def generate(
        self,
        prompt: str,
        max_tokens: int = DEFAULT_LLM_MAX_TOKENS,
        model: str | None = None,
        temperature: float = DEFAULT_LLM_TEMPERATURE,
    ) -> str | None:
        """Generate text using Ollama API with retry.

        Args:
            prompt: The prompt to send to the model
            max_tokens: Maximum number of tokens to generate
            model: Model to use (defaults to self.model, the fine-tuned model)
            temperature: Sampling temperature (0.3 for focused, 0.7 for creative)

        Returns:
            Generated text or None if generation failed
        """
        if not self._connected:
            return None

        use_model = model or self.model

        try:
            return self._generate_with_retry(
                prompt, max_tokens, use_model, temperature, DEFAULT_TIMEOUT_SECONDS
            )
        except Exception as e:
            logger.warning("Ollama generation failed after retries: %s", e)
            return None

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _generate_with_retry(
        self,
        prompt: str,
        max_tokens: int,
        model: str,
        temperature: float,
        timeout: int,
    ) -> str:
        """Internal method with retry decorator for Ollama API calls."""
        response = httpx.post(
            f"{self.base_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": temperature,
                },
            },
            timeout=timeout,
        )
        response.raise_for_status()
        result = response.json()
        return result.get("response", "").strip()

    def set_known_role_names(self, role_names: list[str]) -> None:
        """Set known role names for fuzzy matching.

        Args:
            role_names: List of valid role names
        """
        self._known_role_names = role_names
        self._known_roles_lookup = {name.lower(): name for name in role_names}
        logger.info("Set %d known role names for fuzzy matching", len(role_names))

    def _find_exact_match(self, predicted_lower: str) -> tuple[str, float] | None:
        """Try exact case-insensitive match."""
        if predicted_lower in self._known_roles_lookup:
            logger.info("found exact match for predicted role")
            return self._known_roles_lookup[predicted_lower], 1.0
        return None

    def _find_substring_match(self, predicted_lower: str) -> tuple[str, float] | None:
        """Check if known role is substring of predicted."""
        matches = [
            (known, len(known))
            for known in self._known_role_names
            if known.lower() in predicted_lower
        ]
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            return matches[0][0], 0.9
        return None

    def _find_fuzzy_match(self, predicted_role: str, predicted_lower: str) -> tuple[str, float]:
        """Find best fuzzy match using sequence similarity."""
        import difflib

        best_match, best_score = predicted_role, 0.0

        for known_lower, known_role in self._known_roles_lookup.items():
            ratio = difflib.SequenceMatcher(None, predicted_lower, known_lower).ratio()

            # Bonus for matching important suffixes
            suffix_bonus = (
                0.1
                if any(
                    predicted_lower.endswith(s) and known_lower.endswith(s) for s in _ROLE_SUFFIXES
                )
                else 0.0
            )

            if (total := ratio + suffix_bonus) > best_score:
                best_score, best_match = total, known_role

        if best_score >= 0.6:
            return best_match, min(best_score, 1.0)
        return predicted_role, 0.0

    def find_closest_role(self, predicted_role: str) -> tuple[str, float]:
        """Find the closest matching known role using fuzzy string matching.

        Uses multiple strategies:
        1. Exact case-insensitive match (returns 1.0)
        2. Substring match - known role contained in predicted (0.9)
        3. Sequence similarity ratio with suffix bonus

        Args:
            predicted_role: Role name predicted by the model

        Returns:
            Tuple of (best_matching_role_name, similarity_score 0-1)
        """
        if not self._known_role_names:
            logger.warning(
                "find_closest_role called but _known_role_names is empty "
                "- build_context may not have been called"
            )
            return predicted_role, 0.0

        predicted_lower = predicted_role.lower().strip()

        # Try matching strategies in order
        if result := self._find_exact_match(predicted_lower):
            return result
        if result := self._find_substring_match(predicted_lower):
            logger.debug("Substring matched '%s' -> '%s'", predicted_role, result[0])
            return result
        return self._find_fuzzy_match(predicted_role, predicted_lower)

    def _build_explanation(
        self,
        signals_matched: list[str],
        signals_missing: list[str],
        ambiguity_note: str,
        confidence_class: str,
    ) -> tuple[str, list[str]]:
        """Build explanation string from parsed JSON fields."""
        parts = []
        if signals_matched:
            parts.append(f"Matched: {', '.join(signals_matched)}")
        if signals_missing:
            parts.append(f"Missing: {', '.join(signals_missing)}")
        if ambiguity_note:
            parts.append(ambiguity_note)
        return "; ".join(parts) if parts else f"Confidence: {confidence_class}", parts

    def _apply_fuzzy_match(
        self,
        role_name: str,
        confidence_score: float,
        explanation_parts: list[str],
    ) -> tuple[str, float, str]:
        """Apply fuzzy matching and adjust confidence accordingly."""
        matched_role, match_score = self.find_closest_role(role_name)
        logger.debug("Fuzzy match result: '%s' (score: %.2f)", matched_role, match_score)

        if match_score >= 1.0:
            return matched_role, confidence_score, "; ".join(explanation_parts)
        if match_score >= 0.6:
            logger.info(
                "Fuzzy matched '%s' -> '%s' (score: %.2f)",
                role_name,
                matched_role,
                match_score,
            )
            explanation_parts.append(f"Corrected from: {role_name}")
            return matched_role, confidence_score * match_score, "; ".join(explanation_parts)

        logger.warning(
            "No match for '%s' (best: '%s' score: %.2f) - may be hallucinated",
            role_name,
            matched_role,
            match_score,
        )
        return role_name, confidence_score, "; ".join(explanation_parts)

    def _parse_llm_response(
        self, response: str, query: str
    ) -> list[tuple[str, float, str, list[str]]]:
        """Parse the structured JSON response from the LLM."""
        raw_output = extract_json_from_markdown(response)
        logger.debug("Cleaned output for parsing: %s...", raw_output[:200])

        data = parse_json_with_repair(raw_output)
        if not data:
            raise json.JSONDecodeError("Failed to parse after repair", raw_output, 0)

        role_name = data.get("role", "")
        confidence_class = data.get("confidence_class", "medium")
        signals_matched = data.get("signals_matched", [])
        signals_missing = data.get("signals_missing", [])
        ambiguity_note = data.get("ambiguity_note", "")

        confidence_score = CONFIDENCE_MAP.get(confidence_class, 0.5)
        explanation, parts = self._build_explanation(
            signals_matched, signals_missing, ambiguity_note, confidence_class
        )

        if not role_name:
            logger.debug("No role_name in parsed data")
            return []

        role_name, confidence_score, explanation = self._apply_fuzzy_match(
            role_name, confidence_score, parts
        )
        logger.info(
            "Ollama recommended '%s' with %.0f%% for '%s'",
            role_name,
            confidence_score * 100,
            query,
        )
        return [(role_name, confidence_score, explanation, signals_matched)]

    def _fallback_parse(self, response: str) -> list[tuple[str, float, str, list[str]]]:
        """Fallback parsing when JSON fails - extract role from first line."""
        lines = response.strip().split("\n")
        if not lines:
            return []
        role_name = lines[0].strip().strip('"').strip("'")
        if not role_name or role_name.startswith("{"):
            return []
        matched_role, match_score = self.find_closest_role(role_name)
        if match_score >= 0.7:
            role_name = matched_role
        logger.info("Ollama fallback: extracted role '%s'", role_name)
        return [(role_name, 0.4, "JSON parse failed", [])]

    def recommend_roles(
        self, query: str, top_k: int = 3
    ) -> list[tuple[str, float, str, list[str]]]:
        """Use fine-tuned Ollama model to recommend roles.

        The qwen-rbac-v5 model outputs structured JSON with:
        - role: The exact Azure built-in role name
        - confidence_class: very_low, low, medium, high, or very_high
        - signals_matched: Keywords from the query that match the role
        - signals_missing: Important context that was missing

        If the predicted role doesn't exactly match a known role,
        fuzzy matching is used to find the closest match.

        Args:
            query: User's natural language query
            top_k: Maximum number of roles to recommend (currently returns 1)

        Returns:
            List of (role_name, confidence_score, explanation, signals_matched) tuples
        """
        logger.debug("recommend_roles called: query='%s...', model=%s", query[:50], self.model)

        if not self._connected:
            logger.debug("Not connected to Ollama, returning empty")
            return []

        prompt = build_role_recommendation_prompt(query)
        response = self.generate(prompt, max_tokens=150)
        if not response:
            logger.debug("No response from Ollama generate()")
            return []

        logger.debug("Ollama raw response (%d chars): %s...", len(response), response[:300])

        try:
            return self._parse_llm_response(response, query)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse Ollama JSON response: %s", e)
            return self._fallback_parse(response)
        except Exception as e:
            logger.exception("Failed to parse Ollama response: %s", e)
            return []
