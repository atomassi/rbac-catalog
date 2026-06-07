"""JSON repair utilities for parsing malformed LLM output.

LLMs often produce invalid JSON with common issues like:
- Missing closing quotes: ["value] -> ["value"]
- Nested arrays in wrong places: ["a"], ["b"] -> ["a", "b"]
- Truncated JSON (missing closing braces)
- Extra text after JSON

This module provides robust parsing with automatic repair.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def _try_parse_json_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def parse_json_with_repair(raw_output: str) -> dict | None:
    """Parse JSON with fallback repair for common LLM malformed output."""
    if (parsed := _try_parse_json_object(raw_output)) is not None:
        return parsed

    # Try to extract just the JSON object (in case of trailing text)
    json_match = re.search(r"\{[^{}]*\}", raw_output, re.DOTALL)
    if json_match and (parsed := _try_parse_json_object(json_match.group())) is not None:
        return parsed

    # Repair common issues
    repaired = raw_output

    # Fix missing quotes before closing bracket: ["value] -> ["value"]
    repaired = re.sub(r'"\s*\]', '"]', repaired)
    repaired = re.sub(r'\["([^"]*)\]', r'["\1"]', repaired)

    # Fix nested array syntax: ["a"], ["b"] -> ["a", "b"]
    repaired = re.sub(r"\],\s*\[", ", ", repaired)

    # Fix unquoted strings in arrays that should be quoted
    # Pattern: [word, word] -> ["word", "word"]
    def quote_array_items(match: re.Match) -> str:
        content = match.group(1)
        # Skip if already looks like quoted strings
        if '"' in content:
            return match.group(0)
        items = [item.strip() for item in content.split(",")]
        quoted = ", ".join(f'"{item}"' for item in items if item)
        return f"[{quoted}]"

    repaired = re.sub(r'\[([^\[\]"]+)\]', quote_array_items, repaired)

    # Ensure JSON ends with }
    if repaired.count("{") > repaired.count("}"):
        repaired = repaired.rstrip() + "}"

    # Try parsing repaired JSON
    if (parsed := _try_parse_json_object(repaired)) is not None:
        logger.debug("JSON repair successful: %s...", repaired[:100])
        return parsed

    # Last resort: try to extract just the role name with regex
    role_match = re.search(r'"role"\s*:\s*"([^"]+)"', raw_output)
    conf_match = re.search(r'"confidence_class"\s*:\s*"([^"]+)"', raw_output)

    if role_match:
        return {
            "role": role_match.group(1),
            "confidence_class": conf_match.group(1) if conf_match else "low",
            "signals_matched": [],
            "signals_missing": [],
        }

    return None


def extract_json_from_markdown(response: str) -> str:
    """Extract JSON from markdown code blocks.

    Args:
        response: Raw LLM response that may contain markdown

    Returns:
        Cleaned response with JSON extracted
    """
    raw_output = response.strip()

    if "```json" in raw_output:
        raw_output = raw_output.split("```json")[1].split("```")[0]
        logger.debug("Extracted JSON from ```json block")
    elif "```" in raw_output:
        raw_output = raw_output.split("```")[1].split("```")[0]
        logger.debug("Extracted JSON from ``` block")

    return raw_output
