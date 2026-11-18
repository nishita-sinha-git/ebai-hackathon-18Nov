from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Response statuses. DONE/PENDING_APPROVAL/HALTED_BREAKER/ABSTAINED come from design.md §1.2.8.
# HALTED_SECURITY and HALTED_OOS are guardrail and scope halts. THROTTLED and INVALID are
# added for admission control and validation failures.
Status = Literal[
    "DONE",
    "PENDING_APPROVAL",
    "HALTED_BREAKER",
    "HALTED_SECURITY",
    "HALTED_OOS",
    "ABSTAINED",
    "THROTTLED",
    "INVALID",
]


class Route(StrEnum):
    DIRECT = "DIRECT_ANSWER"
    RAG = "HYBRID_RAG"
    EXECUTION = "TRANSACTION_EXEC"
    REJECT = "REJECT_OOS"


class PermissionTier(StrEnum):
    READ = "TIER_1_READ"
    COMPUTE = "TIER_2_COMPUTE"
    MUTATE = "TIER_3_MUTATE"


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Lowercase contract pattern (design §1.2.1). The cache key depends on it.
    tenant_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    session_id: str | None = Field(default=None, max_length=128)
    query: str = Field(min_length=1, max_length=4096)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Client-supplied UUIDv4 so a retried Tier 3 request reuses one approval ticket.
    idempotency_key: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    )


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    canonical_args: dict[str, Any]
    permission_tier: PermissionTier
    idempotency_key: str


class AgentResponse(BaseModel):
    request_id: str
    tenant_id: str
    route: Route | None
    answer: str
    citations: list[str] = Field(default_factory=list)
    status: Status
    traceparent: str
    served_by: str | None = None
    degraded: bool = False
    fallback_reason: str | None = None
    hitl_ticket_id: str | None = None
    retry_after_ms: int | None = None
