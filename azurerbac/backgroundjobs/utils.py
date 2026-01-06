"""Utility functions for background jobs."""

from __future__ import annotations

import datetime as dt


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
