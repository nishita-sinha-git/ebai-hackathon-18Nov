"""Request lifecycle: pre-hooks, cache, orchestrator, audit, cache write-back.

Order (design §1.3): PII redaction and injection shield run before anything else,
then the tenant-scoped cache, then the agents. Only final answers are cached.
Pending approvals and throttled responses are never cached.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from groq import Groq

from app.agents.composer import ExtractiveComposer
from app.agents.execution import ApprovalQueue, ExecutionAgent
from app.agents.generation import GenerationAgent
from app.agents.orchestrator import AgentRequest as OrchestratorRequest
from app.agents.orchestrator import AgentResponse as OrchestratorResponse
from app.agents.orchestrator import Orchestrator
from app.agents.retrieval import RetrievalAgent
from app.agents.triage import TriageAgent
from app.agents.verification import VerificationAgent
from app.config import settings
from app.core.cache import SemanticCache
from app.core.llm import groq_handle
from app.core.rag import HybridRAG
from app.core.store import StateStore
from app.models import AgentRequest, AgentResponse, Route, Status
from app.plugins.guardrails import injection_detected, normalize_unicode, redact_pii
from app.plugins.pii import PIIBlockedError
from app.routing.admission import Bulkhead, TenantTokenBucket
from app.routing.breaker import BreakerSet
from app.routing.registry import RouteRegistry

GENERATIVE_ROUTE = "GENERATIVE"


def _no_downstream_connector(payload: dict[str, Any]) -> Any:
    raise RuntimeError("no downstream connector is configured; approved actions cannot run")


async def _execute_approved(payload: dict[str, Any]) -> Any:
    return _no_downstream_connector(payload)


def build_orchestrator(rag: HybridRAG, groq_client: Any | None) -> Orchestrator:
    queue = ApprovalQueue(_execute_approved)
    registry = RouteRegistry()
    handles = {}
    chain: tuple[str, ...] = ()
    if groq_client is not None:
        handle = groq_handle(groq_client, settings.groq_model, settings.llm_timeout_ms)
        handles[handle.name] = handle
        chain = (handle.name,)
    registry.publish(GENERATIVE_ROUTE, chain, "initial: groq if configured, else extractive only")
    verifier = VerificationAgent()
    return Orchestrator(
        triage=TriageAgent(),
        retrieval=RetrievalAgent(rag.index, rag.text_map()),
        execution=ExecutionAgent(queue),
        generation=GenerationAgent(registry, GENERATIVE_ROUTE, handles, BreakerSet(), verifier),
        verifier=verifier,
        composer=ExtractiveComposer(),
        bucket=TenantTokenBucket(settings.tenant_rate_per_s, settings.tenant_burst),
        bulkhead=Bulkhead(settings.worker_concurrency),
    )


class BoundedAgent:
    def __init__(self, rag: HybridRAG, state: StateStore, cache: SemanticCache) -> None:
        self.rag = rag
        self.state = state
        self.cache = cache
        self.client = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None
        self.orchestrator = build_orchestrator(rag, self.client)

    async def handle(self, request: AgentRequest) -> AgentResponse:
        request_id = str(uuid.uuid4())
        traceparent = f"00-{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-01"
        started = time.perf_counter()

        query = normalize_unicode(request.query)
        try:
            redacted, pii_found = redact_pii(query)
        except PIIBlockedError:
            await self.state.write_audit(request.tenant_id, request_id, "pii.blocked", {"blocked": True})
            return self._halt(request, request_id, traceparent, "HALTED_SECURITY", "Request blocked by the PII guard.")
        if pii_found:
            await self.state.write_audit(request.tenant_id, request_id, "pii.redacted", {"pii_detected": True})
        if injection_detected(redacted):
            await self.state.write_audit(request.tenant_id, request_id, "injection.blocked", {"blocked": True})
            return self._halt(
                request, request_id, traceparent, "HALTED_SECURITY", "Request rejected by the injection shield."
            )

        cached = await self.cache.get(request.tenant_id, request.user_id, redacted)
        if cached:
            cached["request_id"] = request_id
            cached["traceparent"] = traceparent
            return AgentResponse(**cached)

        outcome = await self.orchestrator.handle(
            OrchestratorRequest(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                query=redacted,
                deadline_ms=float(settings.generative_deadline_ms),
                idempotency_key=request.idempotency_key,
            )
        )
        response = self._from_orchestrator(request, request_id, traceparent, outcome)

        if response.status == "PENDING_APPROVAL" and response.hitl_ticket_id:
            ticket = self.orchestrator.execution.queue.get(response.hitl_ticket_id)
            await self.state.create_pending_mutation(
                request.tenant_id,
                request.user_id,
                request_id,
                "domain_mutation",
                ticket.payload,
                ticket.idempotency_key,
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        await self.state.write_audit(
            request.tenant_id,
            request_id,
            "agent.completed",
            {
                "route": response.route.value if response.route else None,
                "status": response.status,
                "served_by": response.served_by,
                "degraded": response.degraded,
                "elapsed_ms": elapsed_ms,
                "citations": response.citations,
            },
        )
        if response.status in ("DONE", "ABSTAINED"):
            await self.cache.set(request.tenant_id, request.user_id, redacted, response.model_dump(mode="json"))
        return response

    @staticmethod
    def _halt(request: AgentRequest, request_id: str, traceparent: str, status: Status, answer: str) -> AgentResponse:
        return AgentResponse(
            request_id=request_id,
            tenant_id=request.tenant_id,
            route=Route.REJECT,
            answer=answer,
            status=status,
            traceparent=traceparent,
        )

    @staticmethod
    def _from_orchestrator(
        request: AgentRequest, request_id: str, traceparent: str, out: OrchestratorResponse
    ) -> AgentResponse:
        route = Route(out.route) if out.route else None
        if out.status in ("ANSWERED", "DEGRADED"):
            status: Status = "DONE"
        elif out.status == "REJECTED":
            status = "HALTED_OOS" if route is Route.REJECT else "INVALID"
        elif out.status == "THROTTLED":
            status = "THROTTLED"
        else:  # ABSTAINED, PENDING_APPROVAL, HALTED_BREAKER
            status = out.status  # type: ignore[assignment]
        answer = out.answer
        if out.status == "PENDING_APPROVAL" and out.hitl_ticket_id:
            answer = f"Mutation paused for asynchronous approval. Pending review ID: {out.hitl_ticket_id}."
        return AgentResponse(
            request_id=request_id,
            tenant_id=request.tenant_id,
            route=route,
            answer=answer,
            citations=list(out.citations),
            status=status,
            traceparent=traceparent,
            served_by=out.served_by,
            degraded=out.status == "DEGRADED",
            fallback_reason=out.fallback_reason,
            hitl_ticket_id=out.hitl_ticket_id,
            retry_after_ms=out.retry_after_ms,
        )
