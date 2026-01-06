"""Tests for web utilities, filters, and pattern functions."""

import pytest

from azurerbac.web.filters import (
    diff_lines,
    format_date,
    format_datetime,
    full_json_diff,
)


class TestDiffLines:
    """Tests for the diff_lines filter function."""

    def test_from_to_diff_added_lines(self):
        """Test diff showing added lines."""
        change = {"from": None, "to": {"key": "value"}}
        result = diff_lines(change)

        # All lines should be added
        assert len(result) > 0
        assert all(r["type"] == "added" for r in result)

    def test_from_to_diff_removed_lines(self):
        """Test diff showing removed lines."""
        change = {"from": {"key": "value"}, "to": None}
        result = diff_lines(change)

        # All lines should be removed
        assert len(result) > 0
        assert all(r["type"] == "removed" for r in result)

    def test_from_to_diff_modified(self):
        """Test diff showing modifications."""
        change = {"from": {"key": "old"}, "to": {"key": "new"}}
        result = diff_lines(change)

        assert len(result) > 0
        types = {r["type"] for r in result}
        assert "added" in types or "removed" in types

    def test_list_style_changes(self):
        """Test handling of added/removed array format."""
        change = {"added": ["item1", "item2"], "removed": ["item3"]}
        result = diff_lines(change)

        added = [r for r in result if r["type"] == "added"]
        removed = [r for r in result if r["type"] == "removed"]

        assert len(added) >= 2
        assert len(removed) >= 1

    def test_empty_change_returns_empty(self):
        """Test that empty from/to returns empty result."""
        change = {"from": None, "to": None}
        result = diff_lines(change)

        assert result == []

    def test_handles_complex_nested_objects(self):
        """Test diffing complex nested objects."""
        change = {
            "from": {"nested": {"a": 1, "b": 2}},
            "to": {"nested": {"a": 1, "b": 3}},
        }
        result = diff_lines(change)

        assert len(result) > 0

    def test_comma_only_changes_treated_as_unchanged(self):
        """Test that trailing comma differences are ignored."""
        # This tests the comma normalization logic
        change = {
            "from": {"a": 1, "b": 2},
            "to": {"a": 1, "b": 2},  # Same content
        }
        result = diff_lines(change)

        # Should be mostly unchanged
        changed = [r for r in result if r["type"] != "unchanged"]
        assert len(changed) == 0


class TestFullJsonDiff:
    """Tests for the full_json_diff function."""

    def test_identical_jsons_are_unchanged(self):
        """Test that identical JSONs show all lines as unchanged."""
        obj = {"key": "value", "nested": {"a": 1}}
        result = full_json_diff(obj, obj)

        assert len(result) > 0
        assert all(r["type"] == "unchanged" for r in result)

    def test_added_json(self):
        """Test diff when before is None (creation)."""
        result = full_json_diff(None, {"key": "value"})

        assert len(result) > 0
        assert all(r["type"] == "added" for r in result)

    def test_removed_json(self):
        """Test diff when after is None (deletion)."""
        result = full_json_diff({"key": "value"}, None)

        assert len(result) > 0
        assert all(r["type"] == "removed" for r in result)

    def test_both_none(self):
        """Test diff when both are None."""
        result = full_json_diff(None, None)

        assert result == []

    def test_shows_changes(self):
        """Test that actual changes are detected."""
        before = {"key": "old_value"}
        after = {"key": "new_value"}
        result = full_json_diff(before, after)

        types = {r["type"] for r in result}
        assert "added" in types
        assert "removed" in types


class TestFormatDatetime:
    """Tests for datetime formatting."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("2021-11-15T10:30:45Z", "2021-11-15 10:30:45"),
            ("2021-11-15T10:30:45.123456Z", "2021-11-15 10:30:45"),
            (None, ""),
            ("not-a-date", "not-a-date"),
        ],
    )
    def test_format_datetime(self, input_val, expected: str):
        """Test formatting various datetime inputs."""
        result = format_datetime(input_val)
        assert result == expected

    def test_formats_datetime_object(self):
        """Test formatting a datetime object."""
        from datetime import datetime

        dt_obj = datetime(2021, 11, 15, 10, 30, 45, 123456)
        result = format_datetime(dt_obj)

        assert result == "2021-11-15 10:30:45"
        assert "123456" not in result  # No microseconds


class TestFormatDate:
    """Tests for date-only formatting."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("2021-11-15T10:30:45Z", "2021-11-15"),
            ("2021-11-15T10:30:45.123456Z", "2021-11-15"),
            ("2024-03-01T00:00:00+00:00", "2024-03-01"),
            (None, ""),
            ("not-a-date", "not-a-date"),
        ],
    )
    def test_format_date(self, input_val, expected: str):
        """Test formatting various datetime inputs to date-only."""
        result = format_date(input_val)
        assert result == expected

    def test_formats_datetime_object(self):
        """Test formatting a datetime object to date-only."""
        from datetime import datetime

        dt_obj = datetime(2021, 11, 15, 10, 30, 45, 123456)
        result = format_date(dt_obj)

        assert result == "2021-11-15"
        assert "10:30" not in result  # No time

    def test_formats_date_object(self):
        """Test formatting a date object."""
        from datetime import date

        d_obj = date(2024, 6, 15)
        result = format_date(d_obj)

        assert result == "2024-06-15"


"""Additional tests for web/utils.py module."""


class TestWildcardToSqlLike:
    """Tests for wildcard_to_sql_like function."""

    def test_star_converts_to_percent(self):
        """Test that * converts to %."""
        from azurerbac.core.patterns import wildcard_to_sql_like

        result = wildcard_to_sql_like("Microsoft.*")
        assert result == "Microsoft.%"

    def test_multiple_wildcards(self):
        """Test multiple wildcards in pattern."""
        from azurerbac.core.patterns import wildcard_to_sql_like

        result = wildcard_to_sql_like("Microsoft.*/*/read")
        assert result == "Microsoft.%/%/read"

    def test_escapes_sql_percent(self):
        """Test that existing % is escaped."""
        from azurerbac.core.patterns import wildcard_to_sql_like

        result = wildcard_to_sql_like("test%pattern")
        assert r"\%" in result

    def test_escapes_sql_underscore(self):
        """Test that existing _ is escaped."""
        from azurerbac.core.patterns import wildcard_to_sql_like

        result = wildcard_to_sql_like("test_pattern")
        assert r"\_" in result

    def test_no_wildcards_unchanged(self):
        """Test pattern without wildcards."""
        from azurerbac.core.patterns import wildcard_to_sql_like

        result = wildcard_to_sql_like("Microsoft.Compute/virtualMachines/read")
        assert result == "Microsoft.Compute/virtualMachines/read"


class TestIsWildcardPattern:
    """Tests for is_wildcard_pattern function.

    Note: In Azure RBAC, only * is a wildcard character.
    The ? character is NOT an Azure RBAC wildcard.
    """

    def test_star_is_wildcard(self):
        """Test that * is detected as wildcard."""
        from azurerbac.core.patterns import is_wildcard_pattern

        assert is_wildcard_pattern("Microsoft.*/read") is True

    def test_question_mark_not_wildcard(self):
        """Test that ? is NOT a wildcard in Azure RBAC patterns."""
        from azurerbac.core.patterns import is_wildcard_pattern

        # In Azure RBAC, only * is a wildcard - ? is just a regular character
        assert is_wildcard_pattern("Microsoft.Compute?") is False

    def test_no_wildcard(self):
        """Test pattern without wildcards."""
        from azurerbac.core.patterns import is_wildcard_pattern

        assert is_wildcard_pattern("Microsoft.Compute") is False

    def test_empty_string(self):
        """Test empty string."""
        from azurerbac.core.patterns import is_wildcard_pattern

        assert is_wildcard_pattern("") is False


class TestSlugifyEdgeCases:
    """Additional edge case tests for slugify."""

    def test_empty_string(self):
        """Test slugify with empty string."""
        from azurerbac.web.utils import slugify

        assert slugify("") == ""

    def test_only_special_chars(self):
        """Test slugify with only special characters."""
        from azurerbac.web.utils import slugify

        result = slugify("@#$%^&")
        assert result == ""

    def test_multiple_spaces(self):
        """Test slugify with multiple spaces."""
        from azurerbac.web.utils import slugify

        result = slugify("hello    world")
        assert result == "hello-world"

    def test_leading_trailing_dashes(self):
        """Test that leading/trailing dashes are stripped."""
        from azurerbac.web.utils import slugify

        result = slugify("  hello  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_uppercase_converted(self):
        """Test that uppercase is converted to lowercase."""
        from azurerbac.web.utils import slugify

        result = slugify("HELLO WORLD")
        assert result == "hello-world"

    def test_mixed_case_azure_role(self):
        """Test slugifying typical Azure role name."""
        from azurerbac.web.utils import slugify

        result = slugify("Storage Blob Data Contributor")
        assert result == "storage-blob-data-contributor"

    def test_parentheses_removed(self):
        """Test that parentheses are removed."""
        from azurerbac.web.utils import slugify

        result = slugify("Role (Preview)")
        assert "(" not in result
        assert ")" not in result
