"""Serialization mixin for analytics dataclasses."""

from __future__ import annotations

import datetime as dt
from dataclasses import fields
from typing import Any

from azurerbac.core.types import JsonDict
from azurerbac.core.utils import format_datetime


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
        """Serialize a single value (handles datetime)."""
        if isinstance(value, dt.datetime):
            return format_datetime(value)
        if isinstance(value, dt.date):
            return value.isoformat()
        return value
