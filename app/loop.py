"""Bounded ReAct state machine (design.md Gate 4, ADR-04).

INIT -> TRIAGE -> REASONING -> TOOL_CALL -> OBSERVATION -> VERIFY -> DONE | HALTED
"""

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum


class State(StrEnum):
    INIT = "INIT"
    TRIAGE = "TRIAGE"
    REASONING = "REASONING"
    TOOL_CALL = "TOOL_CALL"
    OBSERVATION = "OBSERVATION"
    VERIFY = "VERIFY"
    DONE = "DONE"
    HALTED = "HALTED"


ALLOWED: dict[State, frozenset[State]] = {
    State.INIT: frozenset({State.TRIAGE, State.HALTED}),
    State.TRIAGE: frozenset({State.REASONING, State.DONE, State.HALTED}),
    State.REASONING: frozenset({State.TOOL_CALL, State.VERIFY, State.DONE, State.HALTED}),
    State.TOOL_CALL: frozenset({State.OBSERVATION, State.HALTED}),
    State.OBSERVATION: frozenset({State.REASONING, State.VERIFY, State.HALTED}),
    State.VERIFY: frozenset({State.DONE, State.REASONING, State.HALTED}),
    State.DONE: frozenset(),
    State.HALTED: frozenset(),
}


class LoopHalt(Exception):
    """Raised when a bound is exceeded. The loop has already moved to HALTED."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


class IllegalTransition(Exception):
    pass


class BoundedReActLoop:
    def __init__(
        self,
        max_iterations: int = 5,
        per_step_timeout_ms: float = 120.0,
        total_budget_ms: float = 450.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_iterations = max_iterations
        self.per_step_timeout_ms = per_step_timeout_ms
        self.total_budget_ms = total_budget_ms
        self._clock = clock
        self.state = State.INIT
        self.iterations = 0
        self._started = clock()
        self._step_started: float | None = None
        self._step_timeout_ms: float | None = None

    @property
    def elapsed_ms(self) -> float:
        return (self._clock() - self._started) * 1000.0

    def transition(self, to: State, step_timeout_ms: float | None = None) -> None:
        if self.state in (State.DONE, State.HALTED):
            raise IllegalTransition(f"loop already terminal at {self.state.value}")
        if to not in ALLOWED[self.state]:
            raise IllegalTransition(f"{self.state.value} -> {to.value} is not allowed")
        if to is not State.HALTED and self.elapsed_ms > self.total_budget_ms:
            self.state = State.HALTED
            raise LoopHalt("E_LOOP_BUDGET", f"total_loop_budget {self.total_budget_ms} ms exhausted")

        if to is State.OBSERVATION and self._step_started is not None:
            step_ms = (self._clock() - self._step_started) * 1000.0
            limit = self._step_timeout_ms or self.per_step_timeout_ms
            if step_ms > limit:
                self.state = State.HALTED
                raise LoopHalt("E_TIMEOUT", f"tool step took {step_ms:.1f} ms (limit {limit} ms)")

        if to is State.TOOL_CALL:
            self.iterations += 1
            if self.iterations > self.max_iterations:
                self.state = State.HALTED
                raise LoopHalt("E_LOOP_BUDGET", f"max_iterations {self.max_iterations} exceeded")
            self._step_started = self._clock()
            self._step_timeout_ms = step_timeout_ms

        self.state = to
