"""Triage Agent (design.md §1.2.6, ADR-5).

v0 is a deterministic rule classifier. It is a stand-in for the distilled ONNX
classifier, which needs the labeled intent set (decision I, KPI-04). The
contract, the route rules, and the confidence floor are the final ones, so the
ONNX model can replace this class without touching the orchestrator.

Rules are conservative: an action verb is needed before anything routes to
TRANSACTION_EXEC, and the agent never defaults to it.
"""

from __future__ import annotations

import re
import unicodedata

from .types import CONFIDENCE_FLOOR, TriageOut

_WS = re.compile(r"\s+")
_LEADIN = r"(?:please |kindly |can you |could you |i want to |i need to |go ahead and )*"

_SMALLTALK = re.compile(r"^(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening))\b")
_OOS = re.compile(r"\b(poem|joke|recipe|horoscope|lyrics|song|write a story|weather forecast)\b")
_ACTION_VERBS = (
    "approve|submit|pay|transfer|adjust|update|delete|cancel|transition|move|order|"
    "confirm|reject|close|reopen|change|set|authorize|release"
)
_ACTION = re.compile(
    rf"^{_LEADIN}({_ACTION_VERBS})\b"
    rf"|\b(please|can you|could you|i want to|i need to)\s+({_ACTION_VERBS})\b"
)
_QUESTION_START = re.compile(
    r"^(what|how|why|when|which|who|where|is|are|can|does|do|explain|"
    r"summari[sz]e|compare|list|tell me|show)\b"
)
_LOOKUP = re.compile(r"\b(sku|lot|stock|price|catalog|warehouse|shipment|inventory)\b|\b(sku|lot)[- ]?\d+", re.I)
_DOMAIN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "clinical",
        re.compile(r"\b(dose|dosage|patient|clinical|diagnos\w*|symptom\w*|medication)\b"),
    ),
    ("financial", re.compile(r"\b(payment|invoice|refund|budget|billing|ledger|loan)\b")),
    ("legal", re.compile(r"\b(contract|clause|liability|lawsuit|legal|jurisdiction|compliance)\b")),
    ("inventory", _LOOKUP),
)
_COMPUTE = re.compile(r"\b(calculate|compute|total|sum|how much|average|percentage)\b")
_SUMMARY = re.compile(r"\bsummari[sz]\w*")
_MULTI_HOP = re.compile(r"\b(compare|versus|vs|difference|relationship|across|both|which of|and then)\b")


def normalize(query: str) -> str:
    text = unicodedata.normalize("NFKC", query).casefold()
    return _WS.sub(" ", text).strip()


class TriageAgent:
    def classify(self, query: str) -> TriageOut:
        text = normalize(query)

        if not text:
            return self._out("OOS", "other", "TIER_1_READ", "low", "REJECT_OOS", 0.95, "empty query")

        if _SMALLTALK.search(text):
            return self._out("SMALLTALK", "general", "TIER_1_READ", "low", "DIRECT_ANSWER", 0.95, "smalltalk")

        if _OOS.search(text):
            return self._out("OOS", "other", "TIER_1_READ", "low", "REJECT_OOS", 0.95, "out-of-scope request")

        if _ACTION.search(text):
            domain = self._domain(text)
            intent = "FORM" if re.search(r"\b(form|submit|application|request)\b", text) else "TRANSITION"
            complexity = self._complexity(text)
            # Actions are confident only when the verb is clear. The floor below still
            # downgrades any low-confidence case to a read-only route.
            return self._out(intent, domain, "TIER_3_MUTATE", complexity, "TRANSACTION_EXEC", 0.90, "action verb")

        domain, domain_matched = self._domain_with_flag(text)
        risk = "TIER_2_COMPUTE" if _COMPUTE.search(text) else "TIER_1_READ"
        complexity = self._complexity(text)
        intent = "PRODUCT_LOOKUP" if domain == "inventory" else "POLICY_QA"
        if _QUESTION_START.search(text) or text.endswith("?") or domain_matched:
            confidence = 0.85 if domain_matched else 0.70
            reason = "question with domain match" if domain_matched else "question, no domain match"
            return self._out(intent, domain, risk, complexity, "HYBRID_RAG", confidence, reason)

        # Nothing recognised. Retrieval is still safe; the confidence floor sends it there.
        return self._out("POLICY_QA", "general", risk, complexity, "HYBRID_RAG", 0.60, "no rule matched")

    def _domain(self, text: str) -> str:
        return self._domain_with_flag(text)[0]

    def _domain_with_flag(self, text: str) -> tuple[str, bool]:
        for name, pattern in _DOMAIN_RULES:
            if pattern.search(text):
                return name, True
        return "general", False

    @staticmethod
    def _complexity(text: str) -> str:
        if _SUMMARY.search(text):
            return "high"
        if _MULTI_HOP.search(text):
            return "high"
        if len(text.split()) > 12:
            return "medium"
        return "low"

    @staticmethod
    def _out(intent, domain, risk, complexity, route, confidence, reason) -> TriageOut:
        # Confidence floor (design §1.2.6). Never escalate to a mutation on low confidence.
        if confidence < CONFIDENCE_FLOOR and route != "REJECT_OOS":
            route = "HYBRID_RAG"
            risk = "TIER_1_READ" if risk == "TIER_3_MUTATE" else risk
            reason = f"{reason}; confidence {confidence:.2f} < {CONFIDENCE_FLOOR}, forced read-only"
            intent = "POLICY_QA" if intent in ("FORM", "TRANSITION") else intent
        return TriageOut(
            intent=intent,
            domain=domain,
            risk_tier=risk,
            complexity=complexity,
            route=route,
            confidence=confidence,
            reason=reason,
        )
