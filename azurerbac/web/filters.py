"""Jinja2 template filters for the Azure RBAC Catalog web application."""

from __future__ import annotations

import difflib
import json
from datetime import datetime
from typing import Any, Final

# Diff line type codes
_CODE_HINT: Final = "? "
_CODE_REMOVED: Final = "- "
_CODE_ADDED: Final = "+ "
_CODE_UNCHANGED: Final = "  "


def _json_to_str(value: Any) -> str:
    """Convert a value to a JSON string, or empty string if None."""
    if value is None:
        return ""
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _ensure_str(item: Any) -> str:
    """Convert item to string, using JSON serialization for non-strings."""
    return item if isinstance(item, str) else _json_to_str(item)


def _process_ndiff(diff_lines_iter: list[str]) -> list[dict]:
    """Process ndiff output into a list of {type, text} dicts."""
    result = []
    i = 0
    while i < len(diff_lines_iter):
        line = diff_lines_iter[i]
        code = line[:2]
        text = line[2:]

        # Skip ndiff's "?" hint lines
        if code == _CODE_HINT:
            i += 1
            continue

        # Look ahead for comma-only changes (JSON formatting noise)
        if code == _CODE_REMOVED and i + 1 < len(diff_lines_iter):
            next_idx = i + 1
            next_line = diff_lines_iter[next_idx]
            next_code = next_line[:2]
            next_text = next_line[2:]

            # Skip "?" hint if present
            if next_code == _CODE_HINT and next_idx + 1 < len(diff_lines_iter):
                next_idx += 1
                next_line = diff_lines_iter[next_idx]
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


def diff_lines(change: dict) -> list[dict]:
    """Compute a unified diff between 'from' and 'to' in a change object.

    Handles multiple change formats:
    - {"from": X, "to": Y} - standard diff
    - {"to": Y} - created (show all as added)
    - {"from": X} - deleted (show all as removed)
    - {"added": [...], "removed": [...]} - list modifications
    """
    # Handle list-style changes (added/removed arrays)
    if "added" in change or "removed" in change:
        result: list[dict[str, str]] = []
        for item in change.get("removed", []):
            result.extend(
                {"type": "removed", "text": line} for line in _ensure_str(item).splitlines()
            )

        for item in change.get("added", []):
            result.extend(
                {"type": "added", "text": line} for line in _ensure_str(item).splitlines()
            )

        return result

    # Handle from/to style changes (also support old/new keys)
    old_val = change.get("from") if "from" in change else change.get("old")
    new_val = change.get("to") if "to" in change else change.get("new")

    old_str = _json_to_str(old_val)
    new_str = _json_to_str(new_val)

    if not old_str and not new_str:
        return []

    diff = list(difflib.ndiff(old_str.splitlines(), new_str.splitlines()))
    return _process_ndiff(diff)


def full_json_diff(before_json: dict | None, after_json: dict | None) -> list[dict]:
    """Compute a unified diff between two full JSON objects.

    Returns a list of {type: 'added'|'removed'|'unchanged', text: str} for each line.
    """
    before_str = _json_to_str(before_json)
    after_str = _json_to_str(after_json)

    if not before_str and not after_str:
        return []

    diff = list(difflib.ndiff(before_str.splitlines(), after_str.splitlines()))
    return _process_ndiff(diff)


def _parse_datetime_value(value: Any) -> datetime | None:
    """Parse value to datetime if possible.

    Returns:
        datetime object if parseable, None otherwise.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
    if hasattr(value, "strftime"):
        return value
    return None


def format_datetime(value: Any) -> str:
    """Format a datetime to show only up to seconds (no microseconds)."""
    if (dt_obj := _parse_datetime_value(value)) is not None:
        return dt_obj.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value is not None else ""


def format_date(value: Any) -> str:
    """Format a datetime to show only the date (no time)."""
    if (dt_obj := _parse_datetime_value(value)) is not None:
        return dt_obj.strftime("%Y-%m-%d")
    return str(value) if value is not None else ""


def remove_is_service_role(obj: Any) -> Any:
    """Recursively remove isServiceRole from a JSON object for display purposes."""
    if isinstance(obj, dict):
        return {k: remove_is_service_role(v) for k, v in obj.items() if k != "isServiceRole"}
    if isinstance(obj, list):
        return [remove_is_service_role(item) for item in obj]
    return obj
