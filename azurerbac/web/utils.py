"""Utility functions for the Azure RBAC Catalog web application."""

from __future__ import annotations

import re
from typing import Final

_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Create a URL-friendly slug from text."""
    if not text:
        return ""
    return _SLUG_PATTERN.sub("-", text.lower()).strip("-")


def clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp value to bounds [min_val, max_val].

    Example:
        clamp(page, 1, MAX_PAGE_NUMBER)  # Ensures 1 <= page <= MAX_PAGE_NUMBER
    """
    return max(min_val, min(value, max_val))
