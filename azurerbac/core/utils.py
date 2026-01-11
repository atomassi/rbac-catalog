"""Shared utility functions."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import uuid


def content_hash(content: str) -> str:
    """Compute non-cryptographic MD5 hash for cache keys and change detection.

    Uses MD5 for speed and compact output. NOT for security purposes.

    Args:
        content: String content to hash

    Returns:
        Hexadecimal hash string (32 characters)
    """
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()


def normalize_uuid_or_none(value: str) -> str | None:
    """Normalize a UUID string to lowercase hyphenated format."""
    with contextlib.suppress(ValueError):
        return str(uuid.UUID(value.strip()))
    return None


def ensure_utc(d: dt.datetime | None) -> dt.datetime | None:
    """Ensure datetime is timezone-aware (UTC)."""
    if d is None:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=dt.UTC)


def ensure_utc_or_min(d: dt.datetime | None) -> dt.datetime:
    """Ensure datetime is timezone-aware, defaulting to datetime.min for None."""
    if d is None:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    return d if d.tzinfo is not None else d.replace(tzinfo=dt.UTC)


def utcnow() -> dt.datetime:
    """Return current UTC datetime with timezone info."""
    return dt.datetime.now(dt.UTC)


def truncate_microseconds(d: dt.datetime | None) -> dt.datetime | None:
    """Remove microseconds from datetime for cleaner display.

    Args:
        d: Datetime to truncate

    Returns:
        Datetime with microseconds set to 0, or None if input is None
    """
    return d.replace(microsecond=0) if d else None


def format_iso_z(d: dt.datetime | None) -> str | None:
    """Format datetime as ISO 8601 with 'Z' suffix for UTC.

    Azure uses 'Z' suffix format: 2025-12-17T09:58:12.949Z
    Python's isoformat() produces: 2025-12-17T09:58:12.949000+00:00

    This function ensures consistent JSON output matching Azure's format.

    Args:
        d: Datetime to format (should be UTC timezone-aware)

    Returns:
        ISO 8601 string with 'Z' suffix, or None if input is None
    """
    if d is None:
        return None
    # Ensure UTC timezone
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.UTC)
    # Format with milliseconds precision and Z suffix (matching Azure format)
    return d.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_datetime(val: str | dt.datetime | None) -> dt.datetime | None:
    """Parse datetime from string, datetime, or None.

    Used for deserializing cached data from disk (ISO format strings).
    """
    if val is None:
        return None
    if isinstance(val, dt.datetime):
        return val
    return dt.datetime.fromisoformat(val)


def format_datetime(val: dt.datetime | None) -> str | None:
    """Format datetime to ISO string for serialization."""
    return val.isoformat() if val else None
