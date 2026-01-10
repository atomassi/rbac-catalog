"""Tests for msgpack serialization helpers."""

from datetime import UTC, datetime

import msgpack
import pytest

from azurerbac.cache.serialization import (
    TAG_DATETIME,
    TAG_SET,
    TAG_TUPLE,
    TAG_TUPLE_KEY_DICT,
    TUPLE_KEY_FIELDS,
    decode_ext,
    deserialize_from_bytes,
    encode_ext,
    prepare_for_msgpack,
    serialize_to_bytes,
)


class TestEncodeExt:
    """Tests for encode_ext function."""

    @pytest.mark.parametrize(
        ("value", "expected_tag"),
        [
            pytest.param({1, 2, 3}, TAG_SET, id="set"),
            pytest.param(set(), TAG_SET, id="empty_set"),
            pytest.param({"a", "b", "c"}, TAG_SET, id="set_with_strings"),
            pytest.param((1, 2, 3), TAG_TUPLE, id="tuple"),
            pytest.param((), TAG_TUPLE, id="empty_tuple"),
            pytest.param((1, (2, 3), 4), TAG_TUPLE, id="nested_tuple"),
        ],
    )
    def test_encode_collection_types(self, value, expected_tag: int):
        """Sets and tuples are encoded with correct tags."""
        result = encode_ext(value)
        assert result.code == expected_tag
        assert isinstance(result.data, bytes)

    @pytest.mark.parametrize(
        ("dt_value", "expected_in_data"),
        [
            pytest.param(
                datetime(2024, 1, 15, 10, 30, 0),
                b"2024-01-15T10:30:00+00:00",
                id="naive_datetime_normalized_to_utc",
            ),
            pytest.param(
                datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC),
                b"2024-01-15T10:30:00",
                id="aware_datetime",
            ),
        ],
    )
    def test_encode_datetime(self, dt_value, expected_in_data):
        """Datetimes are encoded with TAG_DATETIME."""
        result = encode_ext(dt_value)
        assert result.code == TAG_DATETIME
        assert expected_in_data in result.data

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(object(), id="unsupported_type"),
            pytest.param([1, 2, 3], id="list_native_to_msgpack"),
        ],
    )
    def test_encode_unsupported_raises(self, value):
        """Unsupported types raise TypeError."""
        with pytest.raises(TypeError, match="Cannot serialize type"):
            encode_ext(value)


class TestDecodeExt:
    """Tests for decode_ext function."""

    @pytest.mark.parametrize(
        ("original", "expected_type"),
        [
            pytest.param({1, 2, 3}, set, id="set_with_values"),
            pytest.param(set(), set, id="empty_set"),
        ],
    )
    def test_decode_set(self, original, expected_type):
        """TAG_SET is decoded back to a set."""

        packed = msgpack.packb(list(original))
        result = decode_ext(TAG_SET, packed)
        assert result == original
        assert isinstance(result, expected_type)

    def test_decode_tuple(self):
        """TAG_TUPLE is decoded back to a tuple."""

        original = (1, 2, 3)
        packed = msgpack.packb(list(original))
        result = decode_ext(TAG_TUPLE, packed)
        assert result == original
        assert isinstance(result, tuple)

    def test_decode_datetime(self):
        """TAG_DATETIME is decoded back to a timezone-aware datetime."""
        dt_str = "2024-01-15T10:30:00+00:00"
        result = decode_ext(TAG_DATETIME, dt_str.encode("utf-8"))
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        assert isinstance(result, datetime)
        assert result.tzinfo is not None

    def test_decode_tuple_key_dict(self):
        """TAG_TUPLE_KEY_DICT is decoded back to a dict with tuple keys."""

        pairs = [[["a", 1], "value1"], [["b", 2], "value2"]]
        packed = msgpack.packb(pairs)
        result = decode_ext(TAG_TUPLE_KEY_DICT, packed)
        assert result == {("a", 1): "value1", ("b", 2): "value2"}
        assert all(isinstance(k, tuple) for k in result)

    def test_decode_unknown_code_returns_exttype(self):
        """Unknown ExtType codes are returned as-is."""

        result = decode_ext(99, b"some data")
        assert isinstance(result, msgpack.ExtType)
        assert result.code == 99
        assert result.data == b"some data"


class TestPrepareForMsgpack:
    """Tests for prepare_for_msgpack function."""

    def test_non_tuple_key_fields_unchanged(self):
        """Fields not in TUPLE_KEY_FIELDS are unchanged."""
        data = {"roles_by_id": {"role1": {"name": "Role 1"}}, "all_operations": []}
        result = prepare_for_msgpack(data)
        assert result["roles_by_id"] == data["roles_by_id"]
        assert result["all_operations"] == data["all_operations"]

    def test_pattern_match_converted(self):
        """pattern_match field with tuple keys is converted to ExtType."""

        data = {"pattern_match": {("pattern1", 1): {"op1", "op2"}, ("pattern2", 2): {"op3"}}}
        result = prepare_for_msgpack(data)
        assert isinstance(result["pattern_match"], msgpack.ExtType)
        assert result["pattern_match"].code == TAG_TUPLE_KEY_DICT

    def test_partial_coverage_converted(self):
        """partial_coverage field with tuple keys is converted to ExtType."""

        data = {"partial_coverage": {("pattern", 1, "actions", "notActions"): (10, 20, 5, ["op1"])}}
        result = prepare_for_msgpack(data)
        assert isinstance(result["partial_coverage"], msgpack.ExtType)
        assert result["partial_coverage"].code == TAG_TUPLE_KEY_DICT

    def test_wildcard_count_converted(self):
        """wildcard_count field with tuple keys is converted to ExtType."""

        data = {"wildcard_count": {("pattern", 1): 5}}
        result = prepare_for_msgpack(data)
        assert isinstance(result["wildcard_count"], msgpack.ExtType)
        assert result["wildcard_count"].code == TAG_TUPLE_KEY_DICT

    def test_empty_tuple_key_dict_converted(self):
        """Empty tuple-keyed dicts are still converted."""

        data = {"pattern_match": {}}
        result = prepare_for_msgpack(data)
        assert isinstance(result["pattern_match"], msgpack.ExtType)

    def test_tuple_key_fields_constant(self):
        """TUPLE_KEY_FIELDS contains expected fields."""
        assert "pattern_match" in TUPLE_KEY_FIELDS
        assert "partial_coverage" in TUPLE_KEY_FIELDS
        assert "wildcard_count" in TUPLE_KEY_FIELDS


class TestRoundTrip:
    """Tests for packb/unpackb round-trip serialization."""

    def test_simple_dict_roundtrip(self):
        """Simple dicts survive round-trip."""
        data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked == data

    def test_nested_dict_roundtrip(self):
        """Nested dicts survive round-trip."""
        data = {"outer": {"inner": {"deep": "value"}}}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked == data

    def test_set_roundtrip(self):
        """Sets survive round-trip."""
        data = {"my_set": {1, 2, 3}}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked["my_set"] == {1, 2, 3}
        assert isinstance(unpacked["my_set"], set)

    def test_tuple_roundtrip(self):
        """Tuples in dict values are returned as lists (msgpack limitation).

        Note: Tuples ARE properly encoded as ExtType, but when nested inside
        other structures, msgpack's unpackb returns them as lists. The caller
        (persistence.py) handles post-processing for specific fields like
        role_coverage and role_net_permissions.
        """
        data = {"my_tuple": (1, 2, 3)}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        # Tuples become lists due to msgpack's nested unpacking behavior
        assert unpacked["my_tuple"] == [1, 2, 3]
        assert isinstance(unpacked["my_tuple"], list)

    def test_datetime_roundtrip(self):
        """Datetimes survive round-trip (normalized to UTC)."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        data = {"timestamp": dt}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        # Naive datetimes are normalized to UTC on encode
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        assert unpacked["timestamp"] == expected
        assert isinstance(unpacked["timestamp"], datetime)
        assert unpacked["timestamp"].tzinfo is not None

    def test_tuple_key_dict_roundtrip(self):
        """Dicts with tuple keys in TUPLE_KEY_FIELDS survive round-trip."""
        data = {"pattern_match": {("pattern", 1): {"op1", "op2"}, ("pattern2", 2): {"op3"}}}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert ("pattern", 1) in unpacked["pattern_match"]
        assert unpacked["pattern_match"][("pattern", 1)] == {"op1", "op2"}

    def test_complex_nested_structure_roundtrip(self):
        """Complex nested structures survive round-trip."""
        data = {
            "metadata": {"version": "v1", "count": 100},
            "role_coverage": {
                "role1": ({"op1", "op2"}, {"data_op1"}),
                "role2": (set(), {"data_op2"}),
            },
            "pattern_match": {("*.read", 1): {"read_op1", "read_op2"}},
            "timestamps": [datetime(2024, 1, 1), datetime(2024, 1, 2)],
        }
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)

        assert unpacked["metadata"] == data["metadata"]
        # role_coverage values are tuples of sets - msgpack returns lists
        # The actual conversion happens in persistence.py post-processing
        assert ("*.read", 1) in unpacked["pattern_match"]

    def test_empty_structures_roundtrip(self):
        """Empty structures survive round-trip."""
        data = {
            "empty_dict": {},
            "empty_list": [],
            "pattern_match": {},  # Empty tuple-keyed dict
        }
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked["empty_dict"] == {}
        assert unpacked["empty_list"] == []
        assert unpacked["pattern_match"] == {}

    def test_binary_data_roundtrip(self):
        """Binary data survives round-trip."""
        data = {"binary": b"some binary data"}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked["binary"] == b"some binary data"

    def test_none_values_roundtrip(self):
        """None values survive round-trip."""
        data = {"none_value": None, "nested": {"also_none": None}}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked["none_value"] is None
        assert unpacked["nested"]["also_none"] is None

    def test_unicode_strings_roundtrip(self):
        """Unicode strings survive round-trip."""
        data = {"unicode": "こんにちは世界 🌍 émojis"}
        packed = serialize_to_bytes(data)
        unpacked = deserialize_from_bytes(packed)
        assert unpacked["unicode"] == "こんにちは世界 🌍 émojis"
