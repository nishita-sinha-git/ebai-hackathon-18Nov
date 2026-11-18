"""Triage Agent (design.md §1.2.6, KPI-04/05/06 surrogate tests)."""

import time

import pytest

from app.agents.triage import TriageAgent
from app.agents.types import TriageOut

agent = TriageAgent()


@pytest.mark.parametrize(
    "query, route",
    [
        ("hi there", "DIRECT_ANSWER"),
        ("thanks!", "DIRECT_ANSWER"),
        ("Write me a poem about spring", "REJECT_OOS"),
        ("What is the refund policy for invoices?", "HYBRID_RAG"),
        ("Explain the liability clause in our supplier contract", "HYBRID_RAG"),
        ("Summarize the recall procedure", "HYBRID_RAG"),
        ("Approve invoice INV-22 for payment", "TRANSACTION_EXEC"),
        ("please submit the leave request form", "TRANSACTION_EXEC"),
        ("I want to adjust the dose for patient 12", "TRANSACTION_EXEC"),
    ],
)
def test_routes(query, route):
    assert agent.classify(query).route == route


def test_smalltalk_and_oos_have_high_confidence():
    assert agent.classify("hello").confidence >= 0.9
    assert agent.classify("tell me a joke").route == "REJECT_OOS"


def test_question_about_approving_is_not_an_action():
    # "approve" appears, but as the subject of a question. It must stay read-only.
    out = agent.classify("What is the approval process for payments?")
    assert out.route == "HYBRID_RAG"
    assert out.risk_tier == "TIER_1_READ"


def test_action_sets_tier_3_and_form_intent():
    out = agent.classify("submit the expense form for March")
    assert out.route == "TRANSACTION_EXEC"
    assert out.risk_tier == "TIER_3_MUTATE"
    assert out.intent == "FORM"


def test_unrecognised_query_is_forced_read_only_by_confidence_floor():
    out = agent.classify("blorp zint quux")
    assert out.route == "HYBRID_RAG"
    assert out.confidence < 0.80
    assert "forced read-only" in out.reason


def test_confidence_floor_downgrades_a_low_confidence_mutation():
    # Direct check of the floor: a mutation proposed at low confidence must become read-only.
    out = TriageAgent._out("FORM", "clinical", "TIER_3_MUTATE", "low", "TRANSACTION_EXEC", 0.50, "weak signal")
    assert out.route == "HYBRID_RAG"
    assert out.risk_tier == "TIER_1_READ"
    assert out.intent == "POLICY_QA"


def test_never_defaults_to_transaction_exec():
    for q in ["", "   ", "???", "the", "policy", "lot", "sku-099 status"]:
        assert agent.classify(q).route != "TRANSACTION_EXEC", q


def test_unicode_homoglyph_action_is_still_detected():
    # Fullwidth letters fold to ASCII under NFKC, so the action rule still fires.
    out = agent.classify("ｐｌｅａｓｅ ａｐｐｒｏｖｅ the payment")
    assert out.route == "TRANSACTION_EXEC"


def test_compute_and_summary_complexity():
    assert agent.classify("calculate the total budget for Q3").risk_tier == "TIER_2_COMPUTE"
    assert agent.classify("summarize the audit findings").complexity == "high"


def test_output_is_valid_contract():
    out = agent.classify("Which SKU has the most stock?")
    assert isinstance(out, TriageOut)
    assert out.domain == "inventory"
    assert out.intent == "PRODUCT_LOOKUP"


def test_latency_is_far_inside_25ms_budget():
    queries = ["What is the refund policy?", "approve invoice 4"] * 200
    start = time.perf_counter()
    for q in queries:
        agent.classify(q)
    per_call_ms = (time.perf_counter() - start) * 1000 / len(queries)
    # Rule classifier only. Not a KPI-05 result (that is measured on the ONNX model).
    assert per_call_ms < 25
