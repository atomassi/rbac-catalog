"""Tests for MCP utility classes."""

import time

import pytest

from azurerbac.mcp.utils import (
    _SUSPICIOUS_PATTERN,
    InputValidator,
    TokenBucketRateLimiter,
    ValidationError,
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


class TestInputValidator:
    """Tests for InputValidator class."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("hello world", False, id="safe_plain"),
            pytest.param("Microsoft.Storage/read", False, id="safe_dotted"),
            pytest.param("<script>", True, id="html_script"),
            pytest.param("test>value", True, id="html_gt"),
            pytest.param("{{config}}", True, id="template_braces"),
            pytest.param("{%import%}", True, id="template_percent"),
            pytest.param("test;ls", True, id="command_semicolon"),
            pytest.param("`whoami`", True, id="command_backtick"),
            pytest.param("$HOME", True, id="command_dollar"),
            pytest.param("../../../etc", True, id="path_traversal"),
            pytest.param("__class__", True, id="dunder"),
            pytest.param("_single", False, id="single_underscore"),
        ],
    )
    def test_is_suspicious(self, value: str, expected: bool):
        """Test suspicious input detection for various patterns."""
        assert InputValidator.is_suspicious(value) is expected

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
        result = InputValidator.validate(value, max_len, min_len, label)
        assert result == expected

    @pytest.mark.parametrize(
        ("value", "max_len", "min_len", "label", "match"),
        [
            pytest.param("a", 100, 2, "Query", "at least 2 characters", id="too_short"),
            pytest.param("hello world", 5, 1, "Query", "Maximum 5 characters", id="too_long"),
            pytest.param("<script>", 100, 1, "Query", "Invalid query format", id="suspicious"),
        ],
    )
    def test_validate_raises(self, value: str, max_len: int, min_len: int, label: str, match: str):
        """Test validation raises for invalid inputs."""
        with pytest.raises(ValidationError, match=match):
            InputValidator.validate(value, max_len, min_len, label)


class TestSuspiciousPattern:
    """Tests for _SUSPICIOUS_PATTERN regex."""

    @pytest.mark.parametrize(
        "value",
        ["<", ">", "{", "}", "\\", ";", "`", "$", "../", "__"],
        ids=[
            "lt",
            "gt",
            "lbrace",
            "rbrace",
            "backslash",
            "semicolon",
            "backtick",
            "dollar",
            "path_traversal",
            "dunder",
        ],
    )
    def test_matches_suspicious(self, value: str) -> None:
        assert _SUSPICIOUS_PATTERN.search(value) is not None

    @pytest.mark.parametrize(
        "value",
        ["hello", "Microsoft.Storage", "_single", "normal-text", "foo_bar"],
        ids=["plain", "dotted", "single_underscore", "hyphenated", "snake_case"],
    )
    def test_safe_chars(self, value: str) -> None:
        assert _SUSPICIOUS_PATTERN.search(value) is None
