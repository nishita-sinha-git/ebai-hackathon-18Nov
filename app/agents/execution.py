"""Execution Agent and HITL approval queue (design.md §1.1 HITL rule, Gate 9 #4).

The Execution Agent never executes a Tier 3 action. Submitting writes a
PENDING_APPROVAL ticket. The action runs only when a reviewer from the same
tenant approves it. Postgres persistence is Phase 3; this queue is in memory.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..breaker import IdempotentCircuitBreaker
from ..harness import Tier, ToolSpec
from .tools import Subagent, ToolBinding, WorkflowSubmitArgs


class ApprovalError(Exception):
    pass


@dataclass
class Ticket:
    ticket_id: str
    tenant_id: str
    user_id: str
    idempotency_key: str
    payload: dict[str, Any]
    status: str = "PENDING_APPROVAL"  # PENDING_APPROVAL | EXECUTED | REJECTED
    reviewer_id: str | None = None
    result: Any = None
    events: list[str] = field(default_factory=list)


class ApprovalQueue:
    def __init__(self, executor: Callable[[dict[str, Any]], Awaitable[Any]]):
        self._executor = executor  # the only place a Tier 3 action can run
        self._tickets: dict[str, Ticket] = {}
        self._by_key: dict[tuple[str, str], str] = {}  # (tenant, idempotency_key) -> ticket_id
        self.executions = 0

    def submit(self, args: WorkflowSubmitArgs) -> Ticket:
        """Idempotent: a repeated key returns the existing ticket and never creates a second one."""
        key = (args.tenant_id, str(args.idempotency_key))
        if key in self._by_key:
            return self._tickets[self._by_key[key]]
        ticket = Ticket(
            ticket_id=str(uuid.uuid4()),
            tenant_id=args.tenant_id,
            user_id=args.user_id,
            idempotency_key=str(args.idempotency_key),
            payload=args.model_dump(mode="json", exclude={"tenant_id", "user_id", "idempotency_key"}),
        )
        ticket.events.append("PENDING_APPROVAL")
        self._tickets[ticket.ticket_id] = ticket
        self._by_key[key] = ticket.ticket_id
        return ticket

    def pending(self, tenant_id: str) -> list[Ticket]:
        return [t for t in self._tickets.values() if t.tenant_id == tenant_id and t.status == "PENDING_APPROVAL"]

    def get(self, ticket_id: str) -> Ticket:
        return self._tickets[ticket_id]

    async def approve(self, ticket_id: str, reviewer_id: str, reviewer_tenant: str) -> Ticket:
        ticket = self._tickets[ticket_id]
        self._authorise(ticket, reviewer_tenant)
        if ticket.status != "PENDING_APPROVAL":
            raise ApprovalError(f"ticket is {ticket.status}, not PENDING_APPROVAL")
        ticket.reviewer_id = reviewer_id
        ticket.result = await self._executor(ticket.payload)
        self.executions += 1
        ticket.status = "EXECUTED"
        ticket.events.append("EXECUTED")
        return ticket

    def reject(self, ticket_id: str, reviewer_id: str, reviewer_tenant: str) -> Ticket:
        ticket = self._tickets[ticket_id]
        self._authorise(ticket, reviewer_tenant)
        if ticket.status != "PENDING_APPROVAL":
            raise ApprovalError(f"ticket is {ticket.status}, not PENDING_APPROVAL")
        ticket.status = "REJECTED"
        ticket.reviewer_id = reviewer_id
        ticket.events.append("REJECTED")
        return ticket

    @staticmethod
    def _authorise(ticket: Ticket, reviewer_tenant: str) -> None:
        if reviewer_tenant != ticket.tenant_id:
            # Reviewers approve only within their own tenant (design §1.1 personas).
            raise ApprovalError("reviewer is not in the ticket's tenant")


class ExecutionAgent(Subagent):
    name = "execution"

    def __init__(self, queue: ApprovalQueue):
        self.queue = queue
        binding = ToolBinding(
            spec=ToolSpec(
                name="workflow_submit",
                tier=Tier.TIER_3_MUTATE,
                input_model=WorkflowSubmitArgs,
                requires_idempotency=True,
            ),
            impl=self._submit,
        )
        super().__init__(Tier.TIER_3_MUTATE, [binding])

    async def _submit(self, args: WorkflowSubmitArgs) -> dict[str, str]:
        ticket = self.queue.submit(args)
        return {"hitl_ticket_id": ticket.ticket_id, "status": ticket.status}

    async def submit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        idempotency_key: str,
        action: str,
        breaker: IdempotentCircuitBreaker,
        sku_id: str | None = None,
        lot_id: str | None = None,
    ) -> dict[str, str]:
        raw = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "idempotency_key": idempotency_key,
            "action": action,
        }
        if sku_id is not None:
            raw["sku_id"] = sku_id
        if lot_id is not None:
            raw["lot_id"] = lot_id
        return await self.call_tool("workflow_submit", raw, breaker)
