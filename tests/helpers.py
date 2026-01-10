"""Test helper functions.

Utility functions for tests that aren't pytest fixtures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from azurerbac.cache.container import CacheContainer


def clear_computed_caches(container: CacheContainer | None = None) -> None:
    """Clear computed caches by swapping to cache with empty computed fields.

    Test-only helper to reset cache state between tests.
    """
    from azurerbac.cache import get_cache_container
    from azurerbac.cache.models import CacheData

    if container is None:
        container = get_cache_container()

    current = container.cache
    new_cache = CacheData(
        all_operations=current.all_operations,
        roles_by_id=current.roles_by_id,
        all_change_events=current.all_change_events,
        unique_providers=current.unique_providers,
        last_scan=current.last_scan,
        first_scan=current.first_scan,
        ops_by_name_lower=current.ops_by_name_lower,
        ops_by_prefix=current.ops_by_prefix,
    )
    container.swap(new_cache)
