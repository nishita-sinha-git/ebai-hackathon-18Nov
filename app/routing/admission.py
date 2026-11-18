"""Admission control, checked before any agent work (design.md §1.8.5)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


class TenantTokenBucket:
    """Per-tenant token bucket. One noisy tenant cannot consume the whole fleet."""

    def __init__(self, rate_per_s: float, burst: float, clock: Callable[[], float] = time.monotonic):
        if rate_per_s <= 0 or burst <= 0:
            raise ValueError("rate_per_s and burst must be positive")
        self.rate_per_s = rate_per_s
        self.burst = burst
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}  # tenant -> (tokens, last_ts)

    def try_acquire(self, tenant_id: str, cost: float = 1.0) -> bool:
        now = self._clock()
        tokens, last = self._buckets.get(tenant_id, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate_per_s)
        if tokens < cost:
            self._buckets[tenant_id] = (tokens, now)
            return False
        self._buckets[tenant_id] = (tokens - cost, now)
        return True

    def retry_after_ms(self, tenant_id: str, cost: float = 1.0) -> int:
        tokens, _ = self._buckets.get(tenant_id, (self.burst, 0.0))
        missing = max(0.0, cost - tokens)
        return int(missing / self.rate_per_s * 1000) + 1


class Bulkhead:
    """Non-blocking concurrency cap. A full bulkhead rejects instead of queueing."""

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self.limit = limit
        self.in_flight = 0

    def try_enter(self) -> bool:
        if self.in_flight >= self.limit:
            return False
        self.in_flight += 1
        return True

    def leave(self) -> None:
        if self.in_flight == 0:
            raise RuntimeError("leave() without a matching try_enter()")
        self.in_flight -= 1

    @contextmanager
    def slot(self) -> Iterator[bool]:
        entered = self.try_enter()
        try:
            yield entered
        finally:
            if entered:
                self.leave()
