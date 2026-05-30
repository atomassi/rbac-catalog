"""Tests for MCP utility classes."""

import time

import pytest

from azurerbac.mcp.utils import (
    TokenBucketRateLimiter,
    ValidationError,
    validate_input,
)


class TestTokenBucketRateLimiter:
    """Tests for TokenBucketRateLimiter class."""

    def test_first_request_allowed(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=0.1)
        result = limiter.is_allowed("session-1")
        assert result.allowed is True
        assert result.wait_seconds == 0.0
        assert result.remaining == 4

    def test_burst_capacity(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=3, refill_rate=0.1)

        for i in range(3):
            result = limiter.is_allowed("session-1")
            assert result.allowed is True
            assert result.wait_seconds == 0.0
            assert result.remaining == 2 - i

        result = limiter.is_allowed("session-1")
        assert result.allowed is False
        assert result.wait_seconds > 0
        assert result.remaining == 0

    def test_token_refill(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=1, refill_rate=10.0)

        result = limiter.is_allowed("session-1")
        assert result.allowed is True

        result = limiter.is_allowed("session-1")
        assert result.allowed is False
        assert result.wait_seconds > 0

        time.sleep(0.15)
        result = limiter.is_allowed("session-1")
        assert result.allowed is True

    def test_separate_buckets(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=2, refill_rate=0.1)

        limiter.is_allowed("session-1")
        limiter.is_allowed("session-1")
        result1 = limiter.is_allowed("session-1")
        assert result1.allowed is False

        result2 = limiter.is_allowed("session-2")
        assert result2.allowed is True
        assert result2.remaining == 1

    def test_wait_time_calculation(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=1, refill_rate=0.5)
        limiter.is_allowed("session-1")

        result = limiter.is_allowed("session-1")
        assert result.allowed is False
        assert 1.9 <= result.wait_seconds <= 2.1

    def test_max_buckets_cleanup(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0, max_buckets=10)

        # Create 10 buckets
        for i in range(10):
            limiter.is_allowed(f"session-{i}")

        # After cleanup, new session should still be allowed
        result = limiter.is_allowed("session-new")
        assert result.allowed is True

    def test_new_bucket_has_full_capacity(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        # A new session should have full capacity
        result = limiter.is_allowed("nonexistent")
        assert result.remaining == 4  # 5 - 1 = 4

    def test_remaining_after_usage(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.is_allowed("session-1")
        result = limiter.is_allowed("session-1")
        # After 2 uses, remaining should be ~3
        assert result.remaining == 3

    def test_reset_single_bucket(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.is_allowed("session-1")
        limiter.is_allowed("session-2")
        limiter.reset("session-1")
        # After reset, session-1 should have full tokens (first request allowed with remaining=4)
        result = limiter.is_allowed("session-1")
        assert result.allowed is True
        assert result.remaining == 4

    def test_reset_all_buckets(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.is_allowed("session-1")
        limiter.is_allowed("session-2")
        limiter.reset()
        # After reset, all sessions should have full tokens
        result1 = limiter.is_allowed("session-1")
        result2 = limiter.is_allowed("session-2")
        assert result1.remaining == 4
        assert result2.remaining == 4

    def test_reset_nonexistent(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        # Resetting nonexistent bucket should not raise
        limiter.reset("nonexistent")

    def test_tokens_capped_at_capacity(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=100.0)
        for _ in range(5):
            limiter.is_allowed("session-1")
        time.sleep(0.1)
        # After refill, bucket should be back to full capacity
        result = limiter.is_allowed("session-1")
        assert result.remaining == 4  # Capped at 5, minus 1 for this request


class TestInputValidation:
    """Tests for input validation functions."""

    @pytest.mark.parametrize(
        ("value", "is_control"),
        [
            pytest.param("hello world", False, id="safe_plain"),
            pytest.param("Microsoft.Storage/read", False, id="safe_dotted"),
            pytest.param("<script>", False, id="html_now_allowed"),
            pytest.param("line\nbreak", True, id="control_newline"),
            pytest.param("null\x00byte", True, id="control_null"),
            pytest.param("tab\tsep", True, id="control_tab"),
            pytest.param("café", False, id="unicode_printable"),
            pytest.param("_single", False, id="single_underscore"),
        ],
    )
    def test_non_printable_detected(self, value: str, is_control: bool):
        """``validate_input`` rejects control characters and accepts ordinary
        printable text (returning it unchanged)."""
        if is_control:
            with pytest.raises(ValidationError, match="Invalid"):
                validate_input(value, max_length=100, min_length=1)
        else:
            assert validate_input(value, max_length=100, min_length=1) == value

    @pytest.mark.parametrize(
        ("value", "max_len", "min_len", "label", "expected"),
        [
            pytest.param("hello", 100, 2, "Query", "hello", id="success"),
            pytest.param("  hello  ", 100, 1, "Query", "hello", id="strips_whitespace"),
        ],
    )
    def test_validate_success(
        self, value: str, max_len: int, min_len: int, label: str, expected: str
    ):
        """Test successful input validation."""
        result = validate_input(value, max_len, min_len, label)
        assert result == expected

    @pytest.mark.parametrize(
        ("value", "max_len", "min_len", "label", "match"),
        [
            pytest.param("a", 100, 2, "Query", "at least 2 characters", id="too_short"),
            pytest.param("hello world", 5, 1, "Query", "Maximum 5 characters", id="too_long"),
            pytest.param("bad\x00null", 100, 1, "Query", "Invalid query format", id="control"),
        ],
    )
    def test_validate_raises(self, value: str, max_len: int, min_len: int, label: str, match: str):
        """Test validation raises for invalid inputs."""
        with pytest.raises(ValidationError, match=match):
            validate_input(value, max_len, min_len, label)


class TestPrintableValidation:
    """Tests for printable-character input validation."""

    @pytest.mark.parametrize(
        "value",
        ["line\nbreak", "null\x00byte", "tab\tsep", "bell\x07"],
        ids=["newline", "null", "tab", "bell"],
    )
    def test_rejects_control_chars(self, value: str) -> None:
        with pytest.raises(ValidationError, match="Invalid"):
            validate_input(value, max_length=100, min_length=1)

    @pytest.mark.parametrize(
        "value",
        ["hello", "Microsoft.Storage", "_single", "normal-text", "foo_bar", "café", "<x>"],
        ids=["plain", "dotted", "single_underscore", "hyphenated", "snake_case", "unicode", "html"],
    )
    def test_accepts_printable(self, value: str) -> None:
        assert validate_input(value, max_length=100, min_length=1) == value

    @pytest.mark.parametrize(
        "value",
        ["hello\n", "\thello", "hello\r", "hi\x0bthere"],
        ids=["trailing_newline", "leading_tab", "trailing_cr", "vertical_tab"],
    )
    def test_rejects_edge_control_chars(self, value: str) -> None:
        """Control characters at the edges must be rejected, not silently
        removed by ``str.strip()`` before the printability check."""
        with pytest.raises(ValidationError, match="Invalid"):
            validate_input(value, max_length=100, min_length=1)
