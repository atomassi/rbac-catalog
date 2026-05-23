"""Jinja2 template filters."""

import difflib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from azurerbac.core.diffing import DiffChange

_CODE_HINT: Final = "? "
_CODE_REMOVED: Final = "- "
_CODE_ADDED: Final = "+ "
_CODE_UNCHANGED: Final = "  "


def _json_to_str(value: Any) -> str:
    """Convert value to JSON string, or empty if None."""
    if value is None:
        return ""
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _process_ndiff(diff_lines: list[str]) -> list[dict]:
    """Process ndiff output into {type, text} dicts."""
    result = []
    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        code = line[:2]
        text = line[2:]

        # Skip ndiff's "?" hint lines
        if code == _CODE_HINT:
            i += 1
            continue

        # Look ahead for comma-only changes (JSON formatting noise)
        if code == _CODE_REMOVED and i + 1 < len(diff_lines):
            next_idx = i + 1
            next_line = diff_lines[next_idx]
            next_code = next_line[:2]
            next_text = next_line[2:]

            # Skip "?" hint if present
            if next_code == _CODE_HINT and next_idx + 1 < len(diff_lines):
                next_idx += 1
                next_line = diff_lines[next_idx]
                next_code = next_line[:2]
                next_text = next_line[2:]

            if next_code == _CODE_ADDED and text.rstrip(",") == next_text.rstrip(","):
                result.append({"type": "unchanged", "text": next_text})
                i = next_idx + 1
                continue

        if code == _CODE_REMOVED:
            result.append({"type": "removed", "text": text})
        elif code == _CODE_ADDED:
            result.append({"type": "added", "text": text})
        elif code == _CODE_UNCHANGED:
            result.append({"type": "unchanged", "text": text})

        i += 1

    return result


def diff_lines(change: "DiffChange") -> list[dict]:
    """Compute unified diff between from_value and to_value in a DiffChange."""
    old_str = _json_to_str(change.from_value)
    new_str = _json_to_str(change.to_value)

    if not old_str and not new_str:
        return []

    diff = list(difflib.ndiff(old_str.splitlines(), new_str.splitlines()))
    return _process_ndiff(diff)


def full_json_diff(before_json: dict | None, after_json: dict | None) -> list[dict]:
    """Compute unified diff between two JSON objects."""
    before_str = _json_to_str(before_json)
    after_str = _json_to_str(after_json)

    if not before_str and not after_str:
        return []

    diff = list(difflib.ndiff(before_str.splitlines(), after_str.splitlines()))
    return _process_ndiff(diff)


def _parse_datetime_value(value: Any) -> datetime | None:
    """Parse value to datetime if possible."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
    return None


def format_datetime(value: Any) -> str:
    """Format datetime up to seconds."""
    if (dt_obj := _parse_datetime_value(value)) is not None:
        return dt_obj.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value is not None else ""


def format_date(value: Any) -> str:
    """Format datetime to date only."""
    if (dt_obj := _parse_datetime_value(value)) is not None:
        return dt_obj.strftime("%Y-%m-%d")
    return str(value) if value is not None else ""
