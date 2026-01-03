"""Azure RBAC wildcard pattern matching utilities.

Azure RBAC uses * as a wildcard that matches any characters:
- * -> matches everything
- */read -> matches anything ending in /read
- Microsoft.Storage/* -> matches anything starting with Microsoft.Storage/
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

# Maximum cached patterns (covers typical usage)
_PATTERN_CACHE_SIZE: Final[int] = 10000


@lru_cache(maxsize=_PATTERN_CACHE_SIZE)
def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert an Azure RBAC wildcard pattern to a compiled regex. Cached."""
    escaped = re.escape(pattern)
    regex_pattern = escaped.replace(r"\*", ".*")
    return re.compile(f"^{regex_pattern}$", re.IGNORECASE)


def matches_pattern(operation: str, pattern: str) -> bool:
    """Check if an operation matches an Azure RBAC pattern."""
    regex = pattern_to_regex(pattern)
    return regex.match(operation) is not None


def is_wildcard_pattern(pattern: str) -> bool:
    """Check if a pattern contains Azure RBAC wildcards (*)."""
    return "*" in pattern


def wildcard_to_sql_like(pattern: str) -> str:
    """Convert Azure RBAC wildcard pattern to SQL LIKE pattern (* -> %)."""
    # Escape SQL special characters first
    pattern = pattern.replace("%", r"\%").replace("_", r"\_")
    # Convert Azure RBAC wildcard
    return pattern.replace("*", "%")
