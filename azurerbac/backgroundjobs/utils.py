"""Utility functions for background jobs."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def parse_azure_date(role_json: dict, field: str) -> dt.datetime | None:
    """Parse Azure date fields like 'updatedOn' or 'createdOn' from role JSON.

    Azure returns ISO 8601 dates with 'Z' suffix and variable fractional seconds.
    Python 3.12+ fromisoformat() handles these natively.
    """
    props = role_json.get("properties") or {}
    v = props.get(field)
    if not isinstance(v, str):
        return None

    try:
        return dt.datetime.fromisoformat(v)
    except ValueError:
        return None


async def rebuild_cache_if_needed(
    session: AsyncSession,
    *,
    reason: str,
    logger_name: str,
    update_in_memory: bool = False,
    **kwargs: Any,
) -> None:
    from azurerbac.cache import invalidate_and_rebuild_cache

    await invalidate_and_rebuild_cache(
        session,
        reason,
        logger_name,
        update_in_memory=update_in_memory,
        **kwargs,
    )
