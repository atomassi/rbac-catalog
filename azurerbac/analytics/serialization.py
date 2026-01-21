"""Serialization mixin for analytics dataclasses."""

from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import fields
from typing import Any, ClassVar, TypeVar, get_origin, get_type_hints

from azurerbac.core.types import JsonDict
from azurerbac.core.utils import format_datetime, parse_datetime

T = TypeVar("T", bound="SerializableMixin")


class SerializableMixin:
    """Mixin providing generic to_dict/from_dict for frozen dataclasses.

    Handles datetime serialization automatically. For nested dataclasses
    or lists of dataclasses, subclasses should override with custom logic.
    """

    # Subclasses can define defaults for missing dict keys (field_name -> default)
    _field_defaults: ClassVar[dict[str, Any]] = {}

    def to_dict(self) -> JsonDict:
        """Serialize dataclass to dictionary."""
        result: JsonDict = {}
        for f in fields(self):  # type: ignore[arg-type]
            value = getattr(self, f.name)
            result[f.name] = self._serialize_value(value)
        return result

    @classmethod
    def from_dict(cls: type[T], data: JsonDict) -> T:
        """Deserialize dictionary to dataclass instance."""
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}

        for f in fields(cls):  # type: ignore[arg-type]
            field_type = hints.get(f.name, type(None))
            raw_value = data.get(f.name)

            # Use explicit default if field missing
            if raw_value is None and f.name in cls._field_defaults:
                kwargs[f.name] = cls._field_defaults[f.name]
            else:
                kwargs[f.name] = cls._deserialize_value(raw_value, field_type)

        return cls(**kwargs)

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        """Serialize a single value (handles datetime)."""
        if isinstance(value, dt.datetime):
            return format_datetime(value)
        if isinstance(value, dt.date):
            return value.isoformat()
        return value

    @staticmethod
    def _deserialize_value(value: Any, field_type: type) -> Any:
        """Deserialize a single value based on type hint."""
        if value is None:
            return None

        # Handle Optional[datetime] -> datetime | None
        origin = get_origin(field_type)
        if origin is type(None):
            return value

        # Unwrap Union types (e.g., datetime | None)
        type_args = getattr(field_type, "__args__", ())
        actual_types = [t for t in type_args if t is not type(None)]
        if actual_types:
            field_type = actual_types[0]

        # Datetime handling
        if field_type is dt.datetime or (
            isinstance(field_type, type) and issubclass(field_type, dt.datetime)
        ):
            return parse_datetime(value)

        # Date handling (may come as string from JSON)
        if field_type is dt.date or (
            isinstance(field_type, type) and issubclass(field_type, dt.date)
        ):
            if isinstance(value, str):
                with contextlib.suppress(ValueError):
                    return dt.date.fromisoformat(value)
            return value

        return value
