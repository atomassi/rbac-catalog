"""Msgpack serialization for cache data.

Provides custom encoding/decoding for Python types not natively supported:
- set -> list (restored as set on decode)
- tuple -> list (restored as tuple on decode)
- datetime -> UTC ISO format string (restored as timezone-aware)
- dict with tuple keys -> list of [key, value] pairs (restored on decode)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

import msgpack

from azurerbac.core.types import JsonDict

__all__ = [
    "deserialize_from_bytes",
    "serialize_to_bytes",
]

# Marker tags for msgpack ExtType custom types
TAG_SET: Final[int] = 1
TAG_TUPLE: Final[int] = 2
TAG_DATETIME: Final[int] = 3
TAG_TUPLE_KEY_DICT: Final[int] = 4

# Fields in CacheData that use tuple keys (need special handling)
TUPLE_KEY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "pattern_match",
        "partial_coverage",
        "wildcard_count",
    }
)


def encode_ext(obj: Any) -> msgpack.ExtType:
    """Encode sets, tuples, and datetime as msgpack ExtType."""
    if isinstance(obj, set):
        return msgpack.ExtType(TAG_SET, msgpack.packb(list(obj), default=encode_ext))
    if isinstance(obj, tuple):
        return msgpack.ExtType(TAG_TUPLE, msgpack.packb(list(obj), default=encode_ext))
    if isinstance(obj, datetime):
        # Normalize to UTC for consistent storage
        obj = obj.replace(tzinfo=UTC) if obj.tzinfo is None else obj.astimezone(UTC)
        return msgpack.ExtType(TAG_DATETIME, obj.isoformat().encode("utf-8"))
    raise TypeError(f"Cannot serialize type: {type(obj).__name__}")


def decode_ext(code: int, data: bytes) -> Any:
    """Decode msgpack ExtType back to sets, tuples, datetime, and tuple-keyed dicts."""
    if code == TAG_SET:
        return set(msgpack.unpackb(data, ext_hook=decode_ext))
    if code == TAG_TUPLE:
        return tuple(msgpack.unpackb(data, ext_hook=decode_ext))
    if code == TAG_DATETIME:
        return datetime.fromisoformat(data.decode("utf-8"))
    if code == TAG_TUPLE_KEY_DICT:
        # Stored as list of [key, value] pairs
        pairs = msgpack.unpackb(data, ext_hook=decode_ext)
        return {tuple(k): v for k, v in pairs}
    return msgpack.ExtType(code, data)


def prepare_for_msgpack(data_dict: JsonDict) -> JsonDict:
    """Convert tuple-keyed dicts to list-of-pairs for msgpack compatibility."""
    result = {}
    for key, value in data_dict.items():
        if key in TUPLE_KEY_FIELDS and isinstance(value, dict):
            # Convert dict with tuple keys to ExtType with list of pairs
            pairs = [[list(k), v] for k, v in value.items()]
            result[key] = msgpack.ExtType(
                TAG_TUPLE_KEY_DICT, msgpack.packb(pairs, default=encode_ext)
            )
        else:
            result[key] = value
    return result


def serialize_to_bytes(data: JsonDict) -> bytes:
    """Serialize a dictionary to msgpack bytes."""
    prepared = prepare_for_msgpack(data)
    return msgpack.packb(prepared, default=encode_ext, strict_types=False)  # type: ignore[return-value]


def deserialize_from_bytes(data: bytes) -> JsonDict:
    """Deserialize msgpack bytes to a dictionary."""
    return msgpack.unpackb(data, ext_hook=decode_ext, strict_map_key=False)
