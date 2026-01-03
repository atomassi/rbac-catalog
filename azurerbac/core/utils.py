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
