"""Tests for web utilities, filters, and pattern functions."""

import pytest

from azurerbac.core.diffing import DiffChange
from azurerbac.core.utils import slugify
from azurerbac.web.filters import (
    diff_lines,
    format_date,
    format_datetime,
    full_json_diff,
)
from azurerbac.web.utils import role_json_pretty, urlencode_path


class TestDiffLines:
    """Tests for the diff_lines filter function."""

    @pytest.mark.parametrize(
        ("change", "expected_type"),
        [
            pytest.param(
                DiffChange(path="root", from_value=None, to_value={"key": "value"}),
                "added",
                id="added_lines",
            ),
            pytest.param(
                DiffChange(path="root", from_value={"key": "value"}, to_value=None),
                "removed",
                id="removed_lines",
            ),
        ],
    )
    def test_diff_single_type(self, change: DiffChange, expected_type: str):
        """Test diff showing only added or removed lines."""
        result = diff_lines(change)
        assert len(result) > 0
        assert all(r["type"] == expected_type for r in result)

    def test_from_to_diff_modified(self):
        """Test diff showing modifications."""
        change = DiffChange(path="root", from_value={"key": "old"}, to_value={"key": "new"})
        result = diff_lines(change)

        assert len(result) > 0
        types = {r["type"] for r in result}
        assert "added" in types or "removed" in types

    def test_empty_change_returns_empty(self):
        """Test that empty from/to returns empty result."""
        change = DiffChange(path="root", from_value=None, to_value=None)
        result = diff_lines(change)

        assert result == []

    def test_handles_complex_nested_objects(self):
        """Test diffing complex nested objects."""
        change = DiffChange(
            path="root",
            from_value={"nested": {"a": 1, "b": 2}},
            to_value={"nested": {"a": 1, "b": 3}},
        )
        result = diff_lines(change)

        assert len(result) > 0

    def test_comma_only_changes_treated_as_unchanged(self):
        """Test that trailing comma differences are ignored."""
        # This tests the comma normalization logic
        change = DiffChange(
            path="root",
            from_value={"a": 1, "b": 2},
            to_value={"a": 1, "b": 2},  # Same content
        )
        result = diff_lines(change)

        # Should be mostly unchanged
        changed = [r for r in result if r["type"] != "unchanged"]
        assert len(changed) == 0

    def test_ndiff_hint_line_before_comma_change_collapsed(self):
        """Hint '?' lines between comma-only remove/add are collapsed to unchanged.

        This covers the _process_ndiff branch where a '?' hint line appears between
        a removed line and an added line that differ only by a trailing comma.
        """
        from azurerbac.web.filters import _process_ndiff

        # Simulate ndiff output: removed with comma, hint, added without comma
        lines = [
            '- "value": 1,',
            "?            -",  # hint line
            '+ "value": 1',
            "  }",
        ]
        result = _process_ndiff(lines)
        # The comma-only change should be collapsed to "unchanged"
        types = [r["type"] for r in result]
        assert "removed" not in types
        assert "added" not in types
        assert types.count("unchanged") == 2

    def test_ensure_str_with_non_string(self):
        """_ensure_str JSON-serializes non-string values."""
        from azurerbac.web.filters import _ensure_str

        result = _ensure_str({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result


class TestFullJsonDiff:
    """Tests for the full_json_diff function."""

    def test_identical_jsons_are_unchanged(self):
        """Test that identical JSONs show all lines as unchanged."""
        obj = {"key": "value", "nested": {"a": 1}}
        result = full_json_diff(obj, obj)

        assert len(result) > 0
        assert all(r["type"] == "unchanged" for r in result)

    @pytest.mark.parametrize(
        ("before", "after", "expected_type"),
        [
            pytest.param(None, {"key": "value"}, "added", id="added_json"),
            pytest.param({"key": "value"}, None, "removed", id="removed_json"),
        ],
    )
    def test_single_type_diff(self, before, after, expected_type: str):
        """Test diff when one side is None (creation or deletion)."""
        result = full_json_diff(before, after)
        assert len(result) > 0
        assert all(r["type"] == expected_type for r in result)

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


class TestDateFormatting:
    """Tests for datetime and date formatting filters."""

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

    def test_datetime_object_formatted(self):
        """Test formatting a datetime object."""
        from datetime import datetime

        dt_obj = datetime(2021, 11, 15, 10, 30, 45, 123456)

        # format_datetime includes time
        result_datetime = format_datetime(dt_obj)
        assert result_datetime == "2021-11-15 10:30:45"
        assert "123456" not in result_datetime

        # format_date excludes time
        result_date = format_date(dt_obj)
        assert result_date == "2021-11-15"
        assert "10:30" not in result_date

    def test_date_object_formatted(self):
        """Test formatting a date object."""
        from datetime import date

        d_obj = date(2024, 6, 15)
        result = format_date(d_obj)

        assert result == "2024-06-15"


"""Additional tests for web/utils.py module."""


class TestWildcardToSqlLike:
    """Tests for wildcard_to_sql_like function."""

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            pytest.param("Microsoft.*", "Microsoft.%", id="star_to_percent"),
            pytest.param("Microsoft.*/*/read", "Microsoft.%/%/read", id="multiple_wildcards"),
            pytest.param(
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Compute/virtualMachines/read",
                id="no_wildcards",
            ),
        ],
    )
    def test_wildcard_conversion(self, pattern: str, expected: str):
        """Test wildcard to SQL LIKE pattern conversion."""
        from azurerbac.core.patterns import wildcard_to_sql_like

        result = wildcard_to_sql_like(pattern)
        assert result == expected

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


class TestIsWildcardPattern:
    """Tests for is_wildcard_pattern function.

    Note: In Azure RBAC, only * is a wildcard character.
    The ? character is NOT an Azure RBAC wildcard.
    """

    @pytest.mark.parametrize(
        ("pattern", "expected"),
        [
            pytest.param("Microsoft.*/read", True, id="star_is_wildcard"),
            pytest.param("Microsoft.Compute?", False, id="question_mark_not_wildcard"),
            pytest.param("Microsoft.Compute", False, id="no_wildcard"),
            pytest.param("", False, id="empty_string"),
        ],
    )
    def test_is_wildcard_pattern(self, pattern: str, expected: bool):
        """Test wildcard pattern detection."""
        from azurerbac.core.patterns import is_wildcard_pattern

        assert is_wildcard_pattern(pattern) is expected


class TestSlugify:
    """Tests for the slugify utility function."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("Hello World", "hello-world", id="spaces_to_hyphens"),
            pytest.param("Role (Preview)", "role-preview", id="removes_parentheses"),
            pytest.param("TEST-NAME", "test-name", id="lowercase"),
            pytest.param("multiple   spaces", "multiple-spaces", id="collapses_spaces"),
            pytest.param("special!@#chars", "special-chars", id="removes_special_chars"),
            pytest.param("--leading-trailing--", "leading-trailing", id="strips_hyphens"),
            pytest.param("", "", id="empty_string"),
            pytest.param("already-slugified", "already-slugified", id="already_slug"),
            pytest.param("CamelCaseText", "camelcasetext", id="camelcase"),
            pytest.param("Numbers123Here", "numbers123here", id="preserves_numbers"),
            pytest.param("@#$%^&", "", id="only_special_chars"),
            pytest.param(
                "Storage Blob Data Contributor",
                "storage-blob-data-contributor",
                id="azure_role_name",
            ),
        ],
    )
    def test_slugify(self, text: str, expected: str) -> None:
        """Test slugify converts text to URL-friendly slugs."""
        assert slugify(text) == expected


class TestUrlEncodePath:
    """Tests for the urlencode_path utility function."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            pytest.param("simple", "simple", id="simple_text"),
            pytest.param("with/slash", "with%2Fslash", id="encodes_slash"),
            pytest.param("a/b/c", "a%2Fb%2Fc", id="multiple_slashes"),
            pytest.param("", "", id="empty_string"),
            pytest.param("hello world", "hello%20world", id="encodes_spaces"),
            pytest.param("special&chars=here", "special%26chars%3Dhere", id="encodes_special"),
            pytest.param(
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Storage%2FstorageAccounts%2Fread",
                id="azure_operation",
            ),
        ],
    )
    def test_urlencode_path(self, text: str, expected: str) -> None:
        """Test urlencode_path encodes path segments correctly."""
        assert urlencode_path(text) == expected


class TestRoleJsonPretty:
    """Tests for the role_json_pretty utility function."""

    def test_formats_dict_with_indent(self) -> None:
        """Test that role JSON is formatted with 2-space indent."""
        role = {"name": "Reader", "permissions": ["read"]}
        result = role_json_pretty(role)
        assert '"name": "Reader"' in result
        assert "\n" in result  # Has newlines
        assert "  " in result  # Has indentation

    def test_handles_nested_objects(self) -> None:
        """Test formatting of nested role structures."""
        role = {"properties": {"roleName": "Test", "permissions": [{"actions": ["*"]}]}}
        result = role_json_pretty(role)
        assert "roleName" in result
        assert "actions" in result

    def test_empty_dict(self) -> None:
        """Test formatting of empty dict."""
        assert role_json_pretty({}) == "{}"

    def test_preserves_unicode_characters(self) -> None:
        """Test that Unicode characters are not escaped (ensure_ascii=False)."""
        role = {"description": "Full control of the agent—manage chats"}
        result = role_json_pretty(role)
        assert "—" in result  # Em dash preserved
        assert "\\u2014" not in result  # Not escaped
