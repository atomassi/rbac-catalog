"""Cache utility functions."""

from __future__ import annotations

from typing import Any

from cachetools import LRUCache


def create_lru_cache(maxsize: int) -> LRUCache[str, Any]:
    """Create an LRU cache with the given max size."""
    return LRUCache(maxsize=maxsize)


def sitemap_url(
    loc: str,
    lastmod: str,
    changefreq: str = "weekly",
    priority: float = 0.5,
) -> str:
    """Generate a sitemap URL entry.

    Args:
        loc: Full URL location.
        lastmod: Last modification date (ISO format).
        changefreq: Change frequency (daily, weekly, monthly).
        priority: Priority from 0.0 to 1.0.

    Returns:
        Formatted sitemap URL XML element.
    """
    return f"""  <url>
    <loc>{loc}</loc>
    <lastmod>{lastmod}</lastmod>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>"""
