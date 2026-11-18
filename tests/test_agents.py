"""Subagents: grants, RBAC, repair, idempotency, HITL, breaker, verification.

Design references: §1.2.6–1.2.7 and §1.9.
"""

import uuid

import pytest

from app.agents.composer import ExtractiveComposer
from app.agents.execution import ApprovalError
from app.agents.retrieval import RetrievalAgent
from app.agents.tools import ToolDenied
from app.agents.verification import VerificationAgent
from app.breaker import BreakerOpenError, IdempotentCircuitBreaker
from app.harness import HarnessError, Tier
from tests.fixtures_agents import ACME, ACME_CHUNKS, GLOBEX, Harness, build_corpus, run


def test_retrieval_returns_only_own_tenant_chunks():
    h = Harness()
    breaker = IdempotentCircuitBreaker()
    hits = run(h.retrieval.retrieve(ACME, "refund invoices", breaker))
    assert hits and all(e.chunk_id in ACME_CHUNKS for e in hits)
    other = run(h.retrieval.retrieve(GLOBEX, "refund invoices", IdempotentCircuitBreaker()))
    assert other == []  # globex cannot see acme refund policy


def test_unknown_tenant_gets_no_evidence():
    h = Harness()
    assert run(h.retrieval.retrieve("tenant_nobody", "refund", IdempotentCircuitBreaker())) == []


def test_retrieval_agent_cannot_call_state_mutating_tool():
    h = Harness()
    with pytest.raises(ToolDenied):
        run(
            h.retrieval.call_tool(
                "workflow_submit",
                {
                    "tenant_id": ACME,
                    "user_id": "u1",
                    "idempotency_key": str(uuid.uuid4()),
                    "action": "form_submit",
                },
                IdempotentCircuitBreaker(),
            )
        )


def test_rbac_denies_tier3_tool_to_tier1_caller_at_harness_layer():
    # Build a subagent that holds a Tier 3 grant but runs at Tier 1. The harness must refuse.
    from app.agents.tools import Subagent, ToolBinding, WorkflowSubmitArgs
    from app.harness import ToolSpec

    async def never(args):
        raise AssertionError("must not run")

    agent = Subagent(
        Tier.TIER_1_READ,
        [ToolBinding(ToolSpec("workflow_submit", Tier.TIER_3_MUTATE, WorkflowSubmitArgs, True), never)],
    )
    with pytest.raises(HarnessError) as info:
        run(
            agent.call_tool(
                "workflow_submit",
                {
                    "tenant_id": ACME,
                    "user_id": "u1",
                    "idempotency_key": str(uuid.uuid4()),
                    "action": "form_submit",
                },
                IdempotentCircuitBreaker(),
            )
        )
    assert info.value.code == "E_RBAC_DENIED"


def test_sku_auto_repair_before_execution():
    h = Harness()
    result = run(
        h.execution.submit(
            tenant_id=ACME,
            user_id="u1",
            idempotency_key=str(uuid.uuid4()),
            action="form_submit",
            sku_id="SKU99",
            breaker=IdempotentCircuitBreaker(),
        )
    )
    ticket = h.queue.get(result["hitl_ticket_id"])
    assert ticket.payload["sku_id"] == "SKU-099"


def test_invalid_idempotency_key_is_rejected():
    h = Harness()
    with pytest.raises(HarnessError):
        run(
            h.execution.submit(
                tenant_id=ACME,
                user_id="u1",
                idempotency_key="not-a-uuid",
                action="form_submit",
                breaker=IdempotentCircuitBreaker(),
            )
        )


def test_duplicate_idempotency_key_returns_same_ticket_once():
    h = Harness()
    key = str(uuid.uuid4())
    a = run(
        h.execution.submit(
            tenant_id=ACME,
            user_id="u1",
            idempotency_key=key,
            action="form_submit",
            breaker=IdempotentCircuitBreaker(),
        )
    )
    b = run(
        h.execution.submit(
            tenant_id=ACME,
            user_id="u1",
            idempotency_key=key,
            action="form_submit",
            breaker=IdempotentCircuitBreaker(),
        )
    )
    assert a["hitl_ticket_id"] == b["hitl_ticket_id"]
    assert len(h.queue.pending(ACME)) == 1


def test_tier3_never_executes_on_submit_only_on_approval():
    h = Harness()
    result = run(
        h.execution.submit(
            tenant_id=ACME,
            user_id="u1",
            idempotency_key=str(uuid.uuid4()),
            action="form_submit",
            breaker=IdempotentCircuitBreaker(),
        )
    )
    assert result["status"] == "PENDING_APPROVAL"
    assert h.executed == []
    run(h.queue.approve(result["hitl_ticket_id"], "reviewer1", ACME))
    assert len(h.executed) == 1


def test_rejected_ticket_never_executes_and_cannot_be_approved_later():
    h = Harness()
    result = run(
        h.execution.submit(
            tenant_id=ACME,
            user_id="u1",
            idempotency_key=str(uuid.uuid4()),
            action="form_submit",
            breaker=IdempotentCircuitBreaker(),
        )
    )
    h.queue.reject(result["hitl_ticket_id"], "reviewer1", ACME)
    with pytest.raises(ApprovalError):
        run(h.queue.approve(result["hitl_ticket_id"], "reviewer1", ACME))
    assert h.executed == []


def test_reviewer_from_another_tenant_cannot_approve():
    h = Harness()
    result = run(
        h.execution.submit(
            tenant_id=ACME,
            user_id="u1",
            idempotency_key=str(uuid.uuid4()),
            action="form_submit",
            breaker=IdempotentCircuitBreaker(),
        )
    )
    with pytest.raises(ApprovalError):
        run(h.queue.approve(result["hitl_ticket_id"], "reviewer_x", GLOBEX))
    assert h.executed == []


def test_identical_tool_calls_trip_the_breaker():
    h = Harness()
    breaker = IdempotentCircuitBreaker(max_identical_calls=2)
    run(h.retrieval.retrieve(ACME, "refund", breaker))
    run(h.retrieval.retrieve(ACME, "refund", breaker))
    with pytest.raises(BreakerOpenError):
        run(h.retrieval.retrieve(ACME, "refund", breaker))


def test_composer_quotes_verbatim_sentences_with_citations():
    index, texts = build_corpus()
    hits = run(RetrievalAgent(index, texts).retrieve(ACME, "recall quality lead", IdempotentCircuitBreaker()))
    out = ExtractiveComposer().compose("recall quality lead", hits)
    assert "CHK-00000000000c" in out.citations
    assert "The recall procedure requires notifying the quality lead within 24 hours." in out.answer


def test_verifier_passes_grounded_text_and_vetoes_invented_text():
    index, texts = build_corpus()
    evidence = run(RetrievalAgent(index, texts).retrieve(ACME, "recall", IdempotentCircuitBreaker()))
    v = VerificationAgent()
    assert v.check("The recall procedure requires notifying the quality lead within 24 hours.", evidence).ok
    bad = v.check("Recalls are handled by drone teams on Mars.", evidence)
    assert not bad.ok
    assert bad.unsupported
