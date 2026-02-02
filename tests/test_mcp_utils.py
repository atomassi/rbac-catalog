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

    def test_initialization(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=10, refill_rate=1.0, max_buckets=100)
        assert limiter.capacity == 10
        assert limiter.refill_rate == 1.0

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

    def test_is_suspicious_safe(self) -> None:
        assert InputValidator.is_suspicious("hello world") is False
        assert InputValidator.is_suspicious("Microsoft.Storage/read") is False

    def test_is_suspicious_html(self) -> None:
        assert InputValidator.is_suspicious("<script>") is True
        assert InputValidator.is_suspicious("test>value") is True

    def test_is_suspicious_template(self) -> None:
        assert InputValidator.is_suspicious("{{config}}") is True
        assert InputValidator.is_suspicious("{%import%}") is True

    def test_is_suspicious_command(self) -> None:
        assert InputValidator.is_suspicious("test;ls") is True
        assert InputValidator.is_suspicious("`whoami`") is True
        assert InputValidator.is_suspicious("$HOME") is True

    def test_is_suspicious_path_traversal(self) -> None:
        assert InputValidator.is_suspicious("../../../etc") is True

    def test_is_suspicious_dunder(self) -> None:
        assert InputValidator.is_suspicious("__class__") is True
        assert InputValidator.is_suspicious("_single") is False

    def test_validate_success(self) -> None:
        result = InputValidator.validate("hello", 100, 2, "Query")
        assert result == "hello"

    def test_validate_strips_whitespace(self) -> None:
        result = InputValidator.validate("  hello  ", 100, 1, "Query")
        assert result == "hello"

    def test_validate_too_short(self) -> None:
        with pytest.raises(ValidationError, match="at least 2 characters"):
            InputValidator.validate("a", 100, 2, "Query")

    def test_validate_too_long(self) -> None:
        with pytest.raises(ValidationError, match="Maximum 5 characters"):
            InputValidator.validate("hello world", 5, 1, "Query")

    def test_validate_suspicious(self) -> None:
        with pytest.raises(ValidationError, match="Invalid query format"):
            InputValidator.validate("<script>", 100, 1, "Query")


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
