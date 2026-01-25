"""Shared utility functions."""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import re
import uuid
from typing import Final

_SLUG_PATTERN: Final = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Create URL-friendly slug from text."""
    return _SLUG_PATTERN.sub("-", text.lower()).strip("-") if text else ""


def content_hash(content: str) -> str:
    """Compute MD5 hash for cache keys (not for security)."""
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()


def normalize_uuid_or_none(value: str) -> str | None:
    """Normalize UUID to lowercase hyphenated format, or None if invalid."""
    with contextlib.suppress(ValueError):
        return str(uuid.UUID(value.strip()))
    return None


def ensure_utc(d: dt.datetime | None) -> dt.datetime | None:
    """Ensure datetime is UTC-aware."""
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def ensure_utc_or_min(d: dt.datetime | None) -> dt.datetime:
    """Ensure datetime is UTC-aware, defaulting to datetime.min."""
    if d is None:
        return dt.datetime.min.replace(tzinfo=dt.UTC)
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def utcnow() -> dt.datetime:
    """Current UTC datetime with timezone."""
    return dt.datetime.now(dt.UTC)


def truncate_microseconds(d: dt.datetime | None) -> dt.datetime | None:
    """Remove microseconds for cleaner display."""
    return d.replace(microsecond=0) if d else None


def format_iso_z(d: dt.datetime | None) -> str | None:
    """Format datetime as ISO 8601 with 'Z' suffix (Azure format)."""
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.UTC)
    return d.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_datetime(val: str | dt.datetime | None) -> dt.datetime | None:
    """Parse datetime from string or passthrough."""
    if val is None or isinstance(val, dt.datetime):
        return val
    return dt.datetime.fromisoformat(val)


def format_datetime(val: dt.datetime | None) -> str | None:
    """Format datetime to ISO string."""
    return val.isoformat() if val else None
