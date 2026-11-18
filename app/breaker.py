"""Idempotent circuit breaker (design.md Gate 9, ADR-04).

Scope is one agent request. State is never shared across requests, so one
user's failures cannot influence another user's calls.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any


class BreakerOpenError(Exception):
    """Raised when a (tool, args) key may not run again in this request."""

    code = "E_CIRCUIT_OPEN"

    def __init__(self, key: str, reason: str):
        super().__init__(reason)
        self.key = key
        self.reason = reason


def canonical_args_hash(tool_name: str, args: dict[str, Any]) -> str:
    payload = json.dumps({"tool": tool_name, "args": args}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class IdempotentCircuitBreaker:
    def __init__(self, max_failures: int = 2, max_identical_calls: int = 2):
        self.max_failures = max_failures
        self.max_identical_calls = max_identical_calls
        self._attempts: Counter[str] = Counter()
        self._failures: Counter[str] = Counter()

    def check(self, tool_name: str, args: dict[str, Any]) -> str:
        """Return the key if the call may run; raise BreakerOpenError otherwise."""
        key = canonical_args_hash(tool_name, args)
        if self._failures[key] >= self.max_failures:
            raise BreakerOpenError(key, f"{tool_name}: {self._failures[key]} identical failures; halted")
        if self._attempts[key] >= self.max_identical_calls:
            raise BreakerOpenError(key, f"{tool_name}: identical call repeated {self._attempts[key]} times; halted")
        self._attempts[key] += 1
        return key

    def record(self, key: str, ok: bool) -> None:
        if not ok:
            self._failures[key] += 1
