"""Orchestrator (design.md §1.3, §1.8, §1.9): the only component that composes agents.

Stateless per request. Nothing about a request is kept on the instance after
`handle` returns, so any worker can serve any request (design §1.10.2).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from ..breaker import BreakerOpenError, IdempotentCircuitBreaker
from ..harness import HarnessError
from ..routing.admission import Bulkhead, TenantTokenBucket
from .composer import ExtractiveComposer
from .execution import ExecutionAgent
from .generation import GenerationAgent
from .retrieval import RetrievalAgent
from .triage import TriageAgent
from .types import TriageOut
from .verification import VerificationAgent

# Placeholder wording. Legal sign-off is pending (design Appendix B-15).
DISCLAIMERS = {
    "clinical": "Informational only. This is not clinical advice.",
    "financial": "Informational only. This is not financial advice.",
    "legal": "Informational only. This is not legal advice.",
}
_SKU = re.compile(r"\bsku[\s-]*\d{1,3}\b", re.IGNORECASE)
_LOT = re.compile(r"\blot[\s-]*\d{1,5}\b", re.IGNORECASE)


@dataclass(frozen=True)
class AgentRequest:
    tenant_id: str
    user_id: str
    query: str
    deadline_ms: float = 450.0
    idempotency_key: str | None = None  # client-supplied UUIDv4 for safe retries of Tier 3 actions


@dataclass(frozen=True)
class AgentResponse:
    # ANSWERED | DEGRADED | ABSTAINED | PENDING_APPROVAL | REJECTED | THROTTLED | HALTED_BREAKER
    status: str
    route: str
    answer: str
    citations: tuple[str, ...] = ()
    served_by: str | None = None
    fallback_reason: str | None = None
    hitl_ticket_id: str | None = None
    retry_after_ms: int | None = None
    triage: TriageOut | None = None
    trace: tuple[str, ...] = field(default_factory=tuple)


REJECT_TEXT = "I can only help with your organisation's policies, products and records."
SMALLTALK_TEXT = "Hello. What would you like to know about your policies, products or records?"


class Orchestrator:
    def __init__(
        self,
        *,
        triage: TriageAgent,
        retrieval: RetrievalAgent,
        execution: ExecutionAgent,
        generation: GenerationAgent,
        verifier: VerificationAgent,
        composer: ExtractiveComposer,
        bucket: TenantTokenBucket,
        bulkhead: Bulkhead,
    ):
        self.triage = triage
        self.retrieval = retrieval
        self.execution = execution
        self.generation = generation
        self.verifier = verifier
        self.composer = composer
        self.bucket = bucket
        self.bulkhead = bulkhead

    async def handle(self, req: AgentRequest) -> AgentResponse:
        if not self.bucket.try_acquire(req.tenant_id):
            return AgentResponse(
                status="THROTTLED",
                route="",
                answer="",
                retry_after_ms=self.bucket.retry_after_ms(req.tenant_id),
                fallback_reason="tenant rate limit",
            )
        with self.bulkhead.slot() as entered:
            if not entered:
                return AgentResponse(
                    status="THROTTLED",
                    route="",
                    answer="",
                    retry_after_ms=250,
                    fallback_reason="worker at capacity",
                )
            return await self._handle_admitted(req)

    async def _handle_admitted(self, req: AgentRequest) -> AgentResponse:
        triage = self.triage.classify(req.query)
        trace = [f"triage:{triage.route}:{triage.confidence:.2f}"]
        breaker = IdempotentCircuitBreaker()

        if triage.route == "REJECT_OOS":
            return AgentResponse("REJECTED", triage.route, REJECT_TEXT, triage=triage, trace=tuple(trace))
        if triage.route == "DIRECT_ANSWER":
            return AgentResponse(
                "ANSWERED",
                triage.route,
                SMALLTALK_TEXT,
                served_by="triage",
                triage=triage,
                trace=tuple(trace),
            )
        if triage.route == "TRANSACTION_EXEC":
            return await self._execute(req, triage, breaker, trace)
        return await self._retrieve_and_answer(req, triage, breaker, trace)

    async def _execute(
        self,
        req: AgentRequest,
        triage: TriageOut,
        breaker: IdempotentCircuitBreaker,
        trace: list[str],
    ) -> AgentResponse:
        sku = _SKU.search(req.query)
        lot = _LOT.search(req.query)
        key = req.idempotency_key or str(uuid.uuid4())
        try:
            result = await self.execution.submit(
                tenant_id=req.tenant_id,
                user_id=req.user_id,
                idempotency_key=key,
                action="form_submit" if triage.intent == "FORM" else "workflow_transition",
                sku_id=sku.group(0) if sku else None,
                lot_id=lot.group(0) if lot else None,
                breaker=breaker,
            )
        except HarnessError as error:
            return AgentResponse(
                "REJECTED",
                triage.route,
                f"Request could not be validated: {error.message}",
                triage=triage,
                trace=tuple(trace),
            )
        except BreakerOpenError as error:
            return AgentResponse("HALTED_BREAKER", triage.route, error.reason, triage=triage, trace=tuple(trace))
        trace.append("execution:PENDING_APPROVAL")
        return AgentResponse(
            "PENDING_APPROVAL",
            triage.route,
            "This action needs approval from a reviewer before it runs.",
            hitl_ticket_id=result["hitl_ticket_id"],
            triage=triage,
            trace=tuple(trace),
        )

    async def _retrieve_and_answer(
        self,
        req: AgentRequest,
        triage: TriageOut,
        breaker: IdempotentCircuitBreaker,
        trace: list[str],
    ) -> AgentResponse:
        try:
            evidence = await self.retrieval.retrieve(req.tenant_id, req.query, breaker)
        except BreakerOpenError as error:
            return AgentResponse("HALTED_BREAKER", triage.route, error.reason, triage=triage, trace=tuple(trace))
        except HarnessError as error:
            return AgentResponse(
                "REJECTED",
                triage.route,
                f"Request could not be validated: {error.message}",
                triage=triage,
                trace=tuple(trace),
            )
        trace.append(f"retrieval:{len(evidence)}")
        if not evidence:
            return AgentResponse(
                "ABSTAINED",
                triage.route,
                "I could not find evidence for that in your records.",
                triage=triage,
                trace=tuple(trace),
            )

        extractive = self.composer.compose(req.query, evidence)
        generative = triage.complexity == "high"
        if generative:
            outcome = await self.generation.generate(
                req.query,
                evidence,
                deadline_ms=req.deadline_ms,
                extractive=lambda: extractive.answer,
            )
            answer, served_by = outcome.answer, outcome.served_by
            citations = extractive.citations
            reason = outcome.fallback_reason
            degraded = outcome.degraded
            trace.append(f"generation:{served_by}")
        else:
            verdict = self.verifier.check(extractive.answer, evidence)
            if not verdict.ok:
                return AgentResponse(
                    "ABSTAINED",
                    triage.route,
                    "I could not verify an answer from your records.",
                    triage=triage,
                    trace=tuple(trace),
                    fallback_reason="verification veto",
                )
            answer, served_by, citations, reason, degraded = (
                extractive.answer,
                "extractive",
                extractive.citations,
                None,
                False,
            )
            trace.append("composer:extractive")

        disclaimer = DISCLAIMERS.get(triage.domain)
        if disclaimer:
            answer = f"{disclaimer} {answer}"
        status = "DEGRADED" if degraded else "ANSWERED"
        return AgentResponse(
            status,
            triage.route,
            answer,
            citations=citations,
            served_by=served_by,
            fallback_reason=reason,
            triage=triage,
            trace=tuple(trace),
        )
