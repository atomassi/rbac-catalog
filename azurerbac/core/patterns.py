"""Azure RBAC wildcard pattern matching (uses * as wildcard)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Final

# Bounded LRU to avoid memory growth from attacker-supplied unique patterns
# arriving via /api/operations/{search,count-matches}. 2000 entries comfortably
# fits every legitimate Azure RBAC pattern family and bounds the worst-case
# resident set under sustained adversarial input.
_CACHE_SIZE: Final[int] = 2000


@lru_cache(maxsize=_CACHE_SIZE)
def pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert Azure wildcard pattern to compiled regex (cached)."""
    regex_str = re.escape(pattern).replace(r"\*", ".*")
    return re.compile(f"^{regex_str}$", re.IGNORECASE)


def matches_pattern(operation: str, pattern: str) -> bool:
    """Check if operation matches an Azure RBAC pattern."""
    return pattern_to_regex(pattern).match(operation) is not None


def is_wildcard_pattern(pattern: str) -> bool:
    """Check if pattern contains wildcards."""
    return "*" in pattern


def expand_patterns_to_operations(patterns: list[str], all_ops: set[str]) -> set[str]:
    """Expand patterns (with wildcards) to matching operations.

    Pattern matching is case-insensitive. Expects all_ops to contain
    lowercased operation names.
    """
    result: set[str] = set()
    for pattern in patterns:
        if pattern == "*":
            return set(all_ops)
        if "*" in pattern:
            regex = pattern_to_regex(pattern)
            result.update(op for op in all_ops if regex.match(op))
        else:
            # Case-insensitive exact match
            pattern_lower = pattern.lower()
            if pattern_lower in all_ops:
                result.add(pattern_lower)
    return result
