"""Generation Agent (design.md §1.8, ADR-8): generative answers through the router.

Used only for Summary and Complex queries (ADR-6). Every generated draft must
pass the Verification Agent. A vetoed or failed draft becomes the extractive answer.
"""

from __future__ import annotations

from collections.abc import Callable

from ..routing.breaker import BreakerSet
from ..routing.fallback import FallbackOutcome, ModelHandle, call_with_fallback
from ..routing.registry import RouteRegistry
from .retrieval import Evidence
from .verification import VerificationAgent


def build_prompt(query: str, evidence: list[Evidence]) -> str:
    # The goal is first and immutable (GoalAnchorBlock, design §Gate 5). Evidence follows as data.
    lines = [f"GOAL: {query}", "EVIDENCE:"]
    lines += [f"[{e.chunk_id}] {e.text}" for e in evidence]
    lines.append("Answer only from the evidence above and cite chunk IDs.")
    return "\n".join(lines)


class GenerationAgent:
    name = "generation"

    def __init__(
        self,
        registry: RouteRegistry,
        route: str,
        handles: dict[str, ModelHandle],
        breakers: BreakerSet,
        verifier: VerificationAgent,
    ):
        self.registry = registry
        self.route = route
        self.handles = handles
        self.breakers = breakers
        self.verifier = verifier

    async def generate(
        self,
        query: str,
        evidence: list[Evidence],
        *,
        deadline_ms: float,
        extractive: Callable[[], str],
    ) -> FallbackOutcome:
        chain = [self.handles[name] for name in self.registry.chain(self.route) if name in self.handles]
        outcome = await call_with_fallback(
            chain,
            build_prompt(query, evidence),
            deadline_ms=deadline_ms,
            breakers=self.breakers,
            terminal=extractive,
        )
        if outcome.served_by == "extractive":
            return outcome
        if not outcome.answer.strip():
            # An empty draft has no claims to verify, so it must not pass as grounded.
            return FallbackOutcome(
                answer=extractive(),
                served_by="extractive",
                degraded=True,
                attempts=outcome.attempts,
                fallback_reason="empty generated answer",
            )
        verdict = self.verifier.check(outcome.answer, evidence)
        if verdict.ok:
            return outcome
        return FallbackOutcome(
            answer=extractive(),
            served_by="extractive",
            degraded=True,
            attempts=outcome.attempts,
            fallback_reason=f"verification veto on {len(verdict.unsupported)} sentence(s)",
        )
