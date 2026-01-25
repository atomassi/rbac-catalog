"""Web utility functions."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote


def urlencode_path(text: str) -> str:
    """URL-encode a path segment, encoding / as %2F."""
    return quote(text, safe="") if text else ""


def clamp(value: int, min_val: int, max_val: int) -> int:
    """Clamp value to [min_val, max_val]."""
    return max(min_val, min(value, max_val))


def role_json_pretty(role: dict[str, Any]) -> str:
    """Pretty-print role JSON."""
    return json.dumps(role, indent=2, default=str)
