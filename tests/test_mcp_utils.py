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
        assert limiter.bucket_count == 0

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

        assert limiter.bucket_count == 10
        limiter.is_allowed("session-new")
        assert limiter.bucket_count == 10

    def test_bucket_count(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        assert limiter.bucket_count == 0
        limiter.is_allowed("session-1")
        assert limiter.bucket_count == 1
        limiter.is_allowed("session-2")
        assert limiter.bucket_count == 2

    def test_get_tokens_new_bucket(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        assert limiter.get_tokens("nonexistent") == 5.0

    def test_get_tokens_after_usage(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.is_allowed("session-1")
        limiter.is_allowed("session-1")
        tokens = limiter.get_tokens("session-1")
        assert 2.9 <= tokens <= 3.1

    def test_reset_single_bucket(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.is_allowed("session-1")
        limiter.is_allowed("session-2")
        assert limiter.bucket_count == 2
        limiter.reset("session-1")
        assert limiter.bucket_count == 1
        assert limiter.get_tokens("session-1") == 5.0

    def test_reset_all_buckets(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.is_allowed("session-1")
        limiter.is_allowed("session-2")
        limiter.reset()
        assert limiter.bucket_count == 0

    def test_reset_nonexistent(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=1.0)
        limiter.reset("nonexistent")
        assert limiter.bucket_count == 0

    def test_tokens_capped_at_capacity(self) -> None:
        limiter = TokenBucketRateLimiter(capacity=5, refill_rate=100.0)
        for _ in range(5):
            limiter.is_allowed("session-1")
        time.sleep(0.1)
        assert limiter.get_tokens("session-1") == 5.0


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
