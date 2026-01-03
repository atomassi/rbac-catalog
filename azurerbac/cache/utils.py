"""Cache utility functions for pattern matching and index building."""

from __future__ import annotations

from azurerbac.core.patterns import matches_pattern


def get_matching_operations(
    pattern: str,
    ops: set[str],
    cache_key: int,
    pattern_cache: dict[tuple[str, int], set[str]],
) -> set[str]:
    """Get operations matching a pattern, using cache for performance."""
    key = (pattern.lower(), cache_key)
    if key in pattern_cache:
        return pattern_cache[key]

    matching = {op for op in ops if matches_pattern(op, pattern)}
    pattern_cache[key] = matching
    return matching


def build_operations_prefix_index(
    ops: set[str],
    cache_key: int,
    prefix_cache: dict[int, dict[str, set[str]]],
) -> dict[str, set[str]]:
    """Build an index of operations by their provider prefix."""
    if cache_key in prefix_cache:
        return prefix_cache[cache_key]

    index: dict[str, set[str]] = {}
    for op in ops:
        slash_idx = op.find("/")
        if slash_idx > 0:
            prefix = op[: slash_idx + 1].lower()
            index.setdefault(prefix, set()).add(op)

    prefix_cache[cache_key] = index
    return index
