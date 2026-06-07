"""Rate limiting and input validation for MCP server."""

import time
from collections import OrderedDict
from dataclasses import dataclass
from types import TracebackType

from rbaccatalog.telemetry import MetricName, track_duration, track_event, track_gauge


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Rate limit check result."""

    allowed: bool
    wait_seconds: float
    remaining: int


class ValidationError(Exception):
    """Raised when input validation fails."""


class TokenBucketRateLimiter:
    """Token bucket rate limiter with LRU eviction."""

    __slots__ = ("_buckets", "_capacity", "_max_buckets", "_refill_rate")

    def __init__(self, capacity: int, refill_rate: float, max_buckets: int = 1000) -> None:
        self._capacity = capacity
        self._refill_rate = refill_rate
        self._max_buckets = max_buckets
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def refill_rate(self) -> float:
        return self._refill_rate

    def is_allowed(self, bucket_id: str) -> RateLimitResult:
        """Check if request allowed and consume a token."""
        now = time.monotonic()
        self._evict_if_full()
        tokens = self._get_tokens(bucket_id, now)

        if tokens >= 1.0:
            self._store(bucket_id, tokens - 1.0, now)
            return RateLimitResult(allowed=True, wait_seconds=0.0, remaining=int(tokens - 1.0))

        wait = float("inf") if self._refill_rate == 0 else (1.0 - tokens) / self._refill_rate
        self._store(bucket_id, tokens, now)
        return RateLimitResult(allowed=False, wait_seconds=wait, remaining=0)

    def reset(self, bucket_id: str | None = None) -> None:
        """Reset one or all buckets."""
        if bucket_id is None:
            self._buckets.clear()
        else:
            self._buckets.pop(bucket_id, None)

    def _evict_if_full(self) -> None:
        if len(self._buckets) >= self._max_buckets:
            for _ in range(self._max_buckets // 10):
                self._buckets.popitem(last=False)

    def _get_tokens(self, bucket_id: str, now: float) -> float:
        if bucket_id not in self._buckets:
            return float(self._capacity)
        tokens, last = self._buckets[bucket_id]
        return min(self._capacity, tokens + (now - last) * self._refill_rate)

    def _store(self, bucket_id: str, tokens: float, now: float) -> None:
        self._buckets[bucket_id] = (tokens, now)
        self._buckets.move_to_end(bucket_id)


def validate_input(
    value: str,
    max_length: int,
    min_length: int = 0,
    field_name: str = "Input",
) -> str:
    """Validate and normalize free-text input. Raises ``ValidationError`` on failure.

    Input hygiene only (length bounds + must be printable), not an injection
    defense: callers feed the result into read-only in-memory lookups.
    """
    stripped = value.strip()
    if len(stripped) > max_length:
        raise ValidationError(f"Input too long. Maximum {max_length} characters allowed.")
    if len(stripped) < min_length:
        raise ValidationError(f"{field_name} must be at least {min_length} characters")
    # Check printability with only spaces trimmed, so edge control characters
    # (e.g. a trailing newline) are rejected instead of stripped away.
    if not value.strip(" ").isprintable():
        raise ValidationError(f"Invalid {field_name.lower()} format")
    return stripped


class ToolTimer:
    """Context manager for tool execution metrics."""

    __slots__ = ("_success", "result_count", "start", "tool_name")

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.start = time.perf_counter()
        self.result_count = 0
        self._success = True

    def __enter__(self) -> "ToolTimer":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self._success = False
        self._record()

    def fail(self) -> None:
        self._success = False

    def _record(self) -> None:
        duration = time.perf_counter() - self.start
        props = {
            "tool": self.tool_name,
            "success": str(self._success),
        }
        track_event(MetricName.MCP_TOOL_CALL, props)
        track_duration(MetricName.MCP_TOOL_DURATION_SECONDS, duration, props)
        if self.result_count > 0:
            track_gauge(MetricName.MCP_TOOL_RESULT_COUNT, self.result_count, props)
