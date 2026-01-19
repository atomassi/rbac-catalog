"""Web utility functions."""

from __future__ import annotations

import json
import re
from typing import Any, Final
from urllib.parse import quote

_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Create URL-friendly slug from text."""
    if not text:
        return ""
    return _SLUG_PATTERN.sub("-", text.lower()).strip("-")


def urlencode_path(text: str) -> str:
    """URL-encode a path segment, encoding / as %2F."""
    if not text:
        return ""
    return quote(text, safe="")


def clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(value, max_val))


def role_json_pretty(role: dict[str, Any]) -> str:
    """Pretty-print role JSON."""
    return json.dumps(role, indent=2, default=str)
