"""Per-model circuit breaker (design.md §1.8.2).

Health is tenant-neutral: it describes the model, not any user's request, so it
is safe to share across requests and tenants.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ModelBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.failure_threshold = failure_threshold
        self.cooldown_s = cooldown_s
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_inflight = False

    @property
    def state(self) -> BreakerState:
        if self._state is BreakerState.OPEN and self._clock() - self._opened_at >= self.cooldown_s:
            self._state = BreakerState.HALF_OPEN
            self._probe_inflight = False
        return self._state

    def allow(self) -> bool:
        """True if a call may be attempted. In HALF_OPEN only one probe is admitted."""
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.OPEN:
            return False
        if self._probe_inflight:
            return False
        self._probe_inflight = True
        return True

    def on_success(self) -> None:
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._probe_inflight = False

    def on_failure(self) -> None:
        self._probe_inflight = False
        if self.state is BreakerState.HALF_OPEN or self._failures + 1 >= self.failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = self._clock()
            self._failures = 0
        else:
            self._failures += 1

    def release_probe(self) -> None:
        """Free the probe slot when an attempt ends with a non-retryable error."""
        self._probe_inflight = False


class BreakerSet:
    """One breaker per model name, all sharing the same configuration."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._breakers: dict[str, ModelBreaker] = {}

    def get(self, model: str) -> ModelBreaker:
        if model not in self._breakers:
            self._breakers[model] = ModelBreaker(
                failure_threshold=self._failure_threshold,
                cooldown_s=self._cooldown_s,
                clock=self._clock,
            )
        return self._breakers[model]
