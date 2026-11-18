"""End-to-end request paths through the orchestrator (design.md §1.3, §1.8, §1.9)."""

import asyncio

from app.agents.orchestrator import AgentRequest
from app.routing.admission import Bulkhead, TenantTokenBucket
from app.routing.errors import RateLimited, UpstreamUnavailable
from tests.fixtures_agents import ACME, GLOBEX, Harness, failing_model, ok_model, run

GROUNDED_RECALL = "The recall procedure requires notifying the quality lead within 24 hours."


def req(query, tenant=ACME, user_id="user_1", **kw):
    return AgentRequest(tenant_id=tenant, user_id=user_id, query=query, **kw)


# ---- routes ----


def test_policy_question_is_answered_extractively_with_citation():
    h = Harness()
    out = run(h.orchestrator.handle(req("What is the refund policy for invoices?")))
    assert out.status == "ANSWERED"
    assert out.served_by == "extractive"
    assert "CHK-00000000000a" in out.citations
    assert "Refunds for invoices are issued within 14 days of approval." in out.answer


def test_out_of_scope_is_rejected_without_retrieval():
    h = Harness()
    out = run(h.orchestrator.handle(req("Write me a poem about the sea")))
    assert out.status == "REJECTED"
    assert out.triage.route == "REJECT_OOS"


def test_smalltalk_is_answered_by_triage():
    h = Harness()
    out = run(h.orchestrator.handle(req("hello")))
    assert out.status == "ANSWERED"
    assert out.served_by == "triage"


def test_action_goes_to_hitl_and_does_not_execute():
    h = Harness()
    out = run(h.orchestrator.handle(req("Submit the expense form for SKU99")))
    assert out.status == "PENDING_APPROVAL"
    assert out.hitl_ticket_id
    assert h.executed == []
    assert h.queue.get(out.hitl_ticket_id).payload["sku_id"] == "SKU-099"


def test_no_evidence_abstains():
    h = Harness()
    out = run(h.orchestrator.handle(req("What is the warranty on the zebra enclosure?")))
    assert out.status == "ABSTAINED"


def test_other_tenant_cannot_get_this_tenants_answer():
    h = Harness()
    out = run(h.orchestrator.handle(req("What is the refund policy for invoices?", tenant=GLOBEX)))
    assert out.status == "ABSTAINED"
    assert "CHK-00000000000a" not in out.citations


def test_clinical_answer_carries_disclaimer():
    h = Harness()
    out = run(h.orchestrator.handle(req("What dosage should adult patients get?")))
    assert out.status == "ANSWERED"
    assert out.answer.startswith("Informational only. This is not clinical advice.")


# ---- generative path, fallback, rollback ----

SUMMARY_Q = "Summarize the recall procedure"


def test_summary_uses_primary_model_when_grounded():
    h = Harness(
        models={"primary": ok_model("primary", GROUNDED_RECALL + " [CHK-00000000000c]")},
        chain=("primary",),
    )
    out = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert out.status == "ANSWERED"
    assert out.served_by == "primary"


def test_primary_rate_limited_falls_back_to_secondary_and_is_degraded():
    h = Harness(
        models={
            "primary": failing_model("primary", RateLimited(500)),
            "secondary": ok_model("secondary", GROUNDED_RECALL),
        },
        chain=("primary", "secondary"),
    )
    out = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert out.status == "DEGRADED"
    assert out.served_by == "secondary"
    assert "primary:failed(RateLimited)" in out.fallback_reason


def test_full_model_outage_still_answers_from_extractive_terminal():
    h = Harness(
        models={
            "primary": failing_model("primary", UpstreamUnavailable()),
            "secondary": failing_model("secondary", UpstreamUnavailable()),
        },
        chain=("primary", "secondary"),
    )
    out = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert out.status == "DEGRADED"
    assert out.served_by == "extractive"
    assert out.answer  # never an empty error during an outage
    assert "CHK-00000000000c" in out.citations


def test_ungrounded_model_output_is_vetoed_by_verifier():
    h = Harness(
        models={"primary": ok_model("primary", "Recalls are handled by drone teams on Mars.")},
        chain=("primary",),
    )
    out = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert out.status == "DEGRADED"
    assert out.served_by == "extractive"
    assert "verification veto" in out.fallback_reason


def test_rollback_restores_previous_model_route_for_next_request():
    h = Harness(
        models={
            "good": ok_model("good", GROUNDED_RECALL),
            "bad": ok_model("bad", "Recalls are handled by drone teams on Mars."),
        },
        chain=("good",),
    )
    h.registry.publish("GENERATIVE", ("bad",), "experiment: new model")
    before = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert before.served_by == "extractive"  # bad model vetoed
    h.registry.rollback("GENERATIVE", "faithfulness regression")
    after = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert after.served_by == "good"
    assert after.status == "ANSWERED"


def test_kill_switch_skips_disabled_model_without_deploy():
    h = Harness(
        models={"a": ok_model("a", GROUNDED_RECALL), "b": ok_model("b", GROUNDED_RECALL)},
        chain=("a", "b"),
    )
    h.registry.disable_model("a", "vendor incident")
    out = run(h.orchestrator.handle(req(SUMMARY_Q)))
    assert out.served_by == "b"


# ---- admission control ----


def test_tenant_over_rate_limit_is_throttled_with_retry_hint():
    h = Harness(bucket=TenantTokenBucket(rate_per_s=1, burst=1))
    first = run(h.orchestrator.handle(req("What is the refund policy?")))
    second = run(h.orchestrator.handle(req("What is the refund policy?")))
    assert first.status == "ANSWERED"
    assert second.status == "THROTTLED"
    assert second.retry_after_ms and second.retry_after_ms >= 1


def test_other_tenants_are_not_throttled_by_a_noisy_neighbour():
    h = Harness(bucket=TenantTokenBucket(rate_per_s=1, burst=1))
    run(h.orchestrator.handle(req("refund policy", tenant=ACME)))
    assert run(h.orchestrator.handle(req("refund policy", tenant=ACME))).status == "THROTTLED"
    assert run(h.orchestrator.handle(req("refund policy", tenant=GLOBEX))).status != "THROTTLED"


def test_full_worker_bulkhead_sheds_load_instead_of_queueing():
    bh = Bulkhead(1)
    h = Harness(bulkhead=bh)
    assert bh.try_enter()  # simulate one request already in flight
    out = run(h.orchestrator.handle(req("refund policy")))
    assert out.status == "THROTTLED"
    assert out.fallback_reason == "worker at capacity"
    bh.leave()


# ---- statelessness and concurrency ----


def test_concurrent_multi_tenant_requests_never_cross_tenants():
    h = Harness(bucket=TenantTokenBucket(rate_per_s=10_000, burst=10_000), bulkhead=Bulkhead(10_000))

    async def burst():
        tasks = []
        for i in range(300):
            tenant = ACME if i % 2 == 0 else GLOBEX
            query = "refund policy invoices" if tenant == ACME else "Globex ships pallets Tuesday"
            tasks.append(h.orchestrator.handle(req(query, tenant=tenant, user_id=f"user_{i}")))
        return await asyncio.gather(*tasks)  # results keep request order

    results = run(burst())
    acme_results = results[0::2]
    globex_results = results[1::2]
    assert all(r.status == "ANSWERED" and "CHK-00000000000a" in r.citations for r in acme_results)
    assert all(r.status == "ANSWERED" and "CHK-00000000000e" in r.citations for r in globex_results)
