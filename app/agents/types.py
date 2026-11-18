"""Triage contract (design.md §1.2.6). Design-accurate enums.

Note: app/contracts.py still uses the older LOW/MEDIUM/HIGH risk tiers.
That mismatch is tracked in design.md Appendix F and is not resolved here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Route = Literal["DIRECT_ANSWER", "HYBRID_RAG", "TRANSACTION_EXEC", "REJECT_OOS"]
Intent = Literal["SMALLTALK", "POLICY_QA", "PRODUCT_LOOKUP", "FORM", "TRANSITION", "OOS"]
Domain = Literal["general", "clinical", "financial", "legal", "inventory", "other"]
RiskTier = Literal["TIER_1_READ", "TIER_2_COMPUTE", "TIER_3_MUTATE"]
Complexity = Literal["low", "medium", "high"]

CONFIDENCE_FLOOR = 0.80  # design §1.2.6: below this, force HYBRID_RAG (read-only)


class TriageOut(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    intent: Intent
    domain: Domain
    risk_tier: RiskTier
    complexity: Complexity
    route: Route
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str  # which rule fired; for audit and the Tab 1 triage console
