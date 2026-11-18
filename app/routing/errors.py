"""Retryable upstream error types (design.md §1.2.9). Only these trigger fallback."""


class RetryableError(Exception):
    code = "E_RETRYABLE"


class UpstreamUnavailable(RetryableError):
    code = "E_UPSTREAM_UNAVAILABLE"  # -32002


class RateLimited(RetryableError):
    code = "E_RATE_LIMITED"  # -32003

    def __init__(self, retry_after_ms: int = 1000):
        super().__init__(f"rate limited; retry after {retry_after_ms} ms")
        self.retry_after_ms = retry_after_ms


class DeadlineExceeded(RetryableError):
    code = "E_DEADLINE_EXCEEDED"  # -32001


class NoPreviousVersion(Exception):
    """Raised when rollback is requested but no earlier route version exists."""
