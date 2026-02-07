"""Serialization mixin for analytics dataclasses."""

from __future__ import annotations

import datetime as dt
from dataclasses import fields
from typing import Any

from azurerbac.core.types import JsonDict


class SerializableMixin:
    """Mixin providing generic to_dict for frozen dataclasses.

    Handles datetime serialization automatically. For nested dataclasses
    or lists of dataclasses, subclasses should override with custom logic.
    """

    def to_dict(self) -> JsonDict:
        """Serialize dataclass to dictionary."""
        result: JsonDict = {}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            result[f.name] = self._serialize_value(value)
        return result

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Serialize a single value (handles datetime and nested dataclasses)."""
        if isinstance(value, dt.datetime):
            return value.isoformat()
        if isinstance(value, dt.date):
            return value.isoformat()
        if isinstance(value, SerializableMixin):
            return value.to_dict()
        if isinstance(value, list):
            return [SerializableMixin._serialize_value(item) for item in value]
        return value
