"""Versioned route chains with rollback and a model kill switch (design.md §1.8.4)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .errors import NoPreviousVersion


@dataclass(frozen=True)
class RouteVersion:
    version: int
    chain: tuple[str, ...]
    reason: str
    at: float


class RouteRegistry:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._history: dict[str, list[RouteVersion]] = {}
        self._active: dict[str, int] = {}
        self._disabled: set[str] = set()
        self.audit: list[dict[str, object]] = []

    def publish(self, route: str, chain: tuple[str, ...], reason: str) -> RouteVersion:
        """Append an immutable version and make it active."""
        history = self._history.setdefault(route, [])
        version = RouteVersion(len(history) + 1, tuple(chain), reason, self._clock())
        history.append(version)
        self._active[route] = len(history) - 1
        self.audit.append({"event": "publish", "route": route, "version": version.version, "reason": reason})
        return version

    def active(self, route: str) -> RouteVersion:
        return self._history[route][self._active[route]]

    def rollback(self, route: str, reason: str) -> RouteVersion:
        """Move the pointer one version back. History is never rewritten."""
        index = self._active.get(route, 0)
        if index == 0:
            raise NoPreviousVersion(f"route {route!r} has no earlier version to roll back to")
        self._active[route] = index - 1
        target = self._history[route][index - 1]
        self.audit.append(
            {
                "event": "rollback",
                "route": route,
                "from": index + 1,
                "to": target.version,
                "reason": reason,
            }
        )
        return target

    def disable_model(self, model: str, reason: str) -> None:
        self._disabled.add(model)
        self.audit.append({"event": "disable_model", "model": model, "reason": reason})

    def enable_model(self, model: str, reason: str) -> None:
        self._disabled.discard(model)
        self.audit.append({"event": "enable_model", "model": model, "reason": reason})

    def chain(self, route: str) -> tuple[str, ...]:
        """The active chain with killed models removed, order preserved."""
        return tuple(m for m in self.active(route).chain if m not in self._disabled)
