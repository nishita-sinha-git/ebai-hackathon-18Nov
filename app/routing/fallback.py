"""Fallback executor (design.md §1.8.3).

Walks a model chain under one request deadline. Each failed or skipped model is
recorded, and the request always ends with an answer: either from a model, or
from the deterministic terminal (the extractive composer).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from .breaker import BreakerSet
from .errors import RetryableError


@dataclass(frozen=True)
class ModelHandle:
    name: str
    timeout_ms: float
    # (prompt, timeout_s) -> answer text. Raises RetryableError subclasses on upstream failure.
    call: Callable[[str, float], Awaitable[str]]


@dataclass(frozen=True)
class Attempt:
    model: str
    outcome: str  # "ok" | "failed" | "skipped_breaker" | "skipped_deadline"
    elapsed_ms: float
    error: str | None = None


@dataclass(frozen=True)
class FallbackOutcome:
    answer: str
    served_by: str
    degraded: bool
    attempts: tuple[Attempt, ...]
    fallback_reason: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


async def call_with_fallback(
    chain: Sequence[ModelHandle],
    prompt: str,
    *,
    deadline_ms: float,
    breakers: BreakerSet,
    terminal: Callable[[], str],
    clock: Callable[[], float] = time.monotonic,
) -> FallbackOutcome:
    start = clock()
    attempts: list[Attempt] = []

    def elapsed_ms() -> float:
        return (clock() - start) * 1000.0

    for position, handle in enumerate(chain):
        remaining = deadline_ms - elapsed_ms()
        if remaining <= 0:
            attempts.append(Attempt(handle.name, "skipped_deadline", 0.0))
            break
        breaker = breakers.get(handle.name)
        if not breaker.allow():
            attempts.append(Attempt(handle.name, "skipped_breaker", 0.0))
            continue

        timeout_ms = min(handle.timeout_ms, remaining)
        attempt_start = clock()
        resolved = False
        try:
            answer = await asyncio.wait_for(handle.call(prompt, timeout_ms / 1000.0), timeout_ms / 1000.0)
            resolved = True
        except (RetryableError, TimeoutError) as error:
            breaker.on_failure()
            resolved = True
            attempts.append(Attempt(handle.name, "failed", (clock() - attempt_start) * 1000.0, type(error).__name__))
            continue
        finally:
            if not resolved:
                # Non-retryable error: it propagates, but the probe slot must not stay taken.
                breaker.release_probe()

        breaker.on_success()
        attempts.append(Attempt(handle.name, "ok", (clock() - attempt_start) * 1000.0))
        degraded = position > 0
        reason = _reason(attempts) if degraded else None
        return FallbackOutcome(
            answer=answer,
            served_by=handle.name,
            degraded=degraded,
            attempts=tuple(attempts),
            fallback_reason=reason,
        )

    answer = terminal()
    return FallbackOutcome(
        answer=answer,
        served_by="extractive",
        degraded=True,
        attempts=tuple(attempts),
        fallback_reason=_reason(attempts) or "no model in chain",
    )


def _reason(attempts: list[Attempt]) -> str:
    parts = []
    for a in attempts:
        if a.outcome == "ok":
            continue
        detail = f"{a.model}:{a.outcome}"
        if a.error:
            detail += f"({a.error})"
        parts.append(detail)
    return "; ".join(parts)
