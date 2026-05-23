"""Analytics service helpers for web layer."""

from __future__ import annotations

from azurerbac.analytics.models import AnalyticsData
from azurerbac.cache import get_cache_service


class AnalyticsNotAvailableError(Exception):
    """Raised when analytics data is not present in the cache.

    Indicates the cache was not fully initialized at startup. Route
    handlers should translate this into an HTTP 503 response.
    """


def get_analytics_from_cache() -> AnalyticsData:
    """Get analytics data from cache.

    Raises:
        AnalyticsNotAvailableError: If analytics data is not in cache.
    """
    cache = get_cache_service().cache
    if cache.analytics is None:
        raise AnalyticsNotAvailableError(
            "Analytics data not available. Cache may not be fully initialized."
        )
    return cache.analytics
