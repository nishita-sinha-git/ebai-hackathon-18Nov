"""Subagent base class and tool-argument models (design.md §1.2.6–1.2.7, §1.9).

Every tool call goes through the same path: grant check → ToolExecutionHarness
(validate, auto-repair, RBAC tier, idempotency) → per-request circuit breaker →
implementation. A subagent cannot call a tool outside its grant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..breaker import IdempotentCircuitBreaker
from ..contracts import LotId, SkuId, TenantId, UserId, UuidV4
from ..harness import HarnessError, Tier, ToolExecutionHarness, ToolSpec


class ToolDenied(Exception):
    """Unknown or ungranted tool. Maps to -32601 MethodNotFound (§1.2.9)."""

    code = "E_METHOD_NOT_FOUND"

    def __init__(self, agent: str, tool: str):
        super().__init__(f"{agent} has no grant for tool {tool!r}")
        self.agent = agent
        self.tool = tool


class RetrieveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: TenantId
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=50, ge=1, le=50)


class WorkflowSubmitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: TenantId
    user_id: UserId
    idempotency_key: UuidV4
    action: Literal["form_submit", "workflow_transition"]
    sku_id: SkuId | None = None
    lot_id: LotId | None = None


@dataclass(frozen=True)
class ToolBinding:
    spec: ToolSpec
    impl: Callable[[Any], Awaitable[Any]]


class Subagent:
    name = "subagent"

    def __init__(self, caller_tier: Tier, bindings: Iterable[ToolBinding]):
        self.caller_tier = caller_tier
        self._bindings = {b.spec.name: b for b in bindings}
        self._harness = ToolExecutionHarness(caller_tier=caller_tier)

    @property
    def granted_tools(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    async def call_tool(self, tool: str, raw_args: dict[str, Any], breaker: IdempotentCircuitBreaker) -> Any:
        binding = self._bindings.get(tool)
        if binding is None:
            raise ToolDenied(self.name, tool)
        # HarnessError (E_INVALID_PARAMS, E_RBAC_DENIED) propagates to the caller.
        validated = self._harness.prepare(binding.spec, raw_args)
        key = breaker.check(tool, validated.model_dump(mode="json"))  # BreakerOpenError propagates
        try:
            result = await binding.impl(validated)
        except Exception:
            breaker.record(key, ok=False)
            raise
        breaker.record(key, ok=True)
        return result


__all__ = [
    "HarnessError",
    "RetrieveArgs",
    "Subagent",
    "ToolBinding",
    "ToolDenied",
    "WorkflowSubmitArgs",
]
