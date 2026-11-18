"""MCP tool and data contracts (design.md §1.2). Pydantic v2 models only."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

TenantId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
UserId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
SkuId = Annotated[str, StringConstraints(pattern=r"^SKU-\d{3}$")]
LotId = Annotated[str, StringConstraints(pattern=r"^LOT-\d{5}$")]
UuidV4 = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ChunkId = Annotated[str, StringConstraints(pattern=r"^CHK-[0-9a-f]{12}$")]

Route = Literal["DIRECT_ANSWER", "HYBRID_RAG", "TRANSACTION_EXEC", "REJECT_OOS"]
RiskTier = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class HybridRetrieveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: TenantId
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=50, ge=1, le=50)
    pool: int = Field(default=40, ge=1, le=40)
    rerank_input: int = Field(default=20, ge=1, le=40)  # D-4: top-20 into the cross-encoder
    rerank_k: int = Field(default=4, ge=1, le=4)
    k1: float = Field(default=1.25, ge=0.0, le=3.0)
    b: float = Field(default=0.75, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1, le=200)
    threshold: float = Field(default=0.42, ge=0.0, le=1.0)


class ChunkHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chunk_id: ChunkId
    chunk_hash: Sha256Hex
    tenant_id: TenantId
    bm25: float | None
    cosine: float | None
    rrf_rank: int = Field(ge=1)
    rerank_score: float | None
    confidence: float = Field(ge=0.0, le=1.0)  # sigmoid(rerank logit)


class HybridRetrieveOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hits: list[ChunkHit] = Field(max_length=4)
    abstain: bool
    stage_ms: dict[Literal["bm25", "dense", "rrf", "rerank"], float]


class StateMutateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: TenantId
    user_id: UserId
    idempotency_key: UuidV4
    sku_id: SkuId | None = None
    lot_id: LotId | None = None
    risk_tier: RiskTier
    action: Literal["form_submit", "workflow_transition"]


class AgentQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: TenantId
    user_id: UserId
    query: str = Field(min_length=1, max_length=2000)


class AgentQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: TenantId  # echoed so the k6 cross-tenant check can run
    request_id: UuidV4
    trace_id: str
    status: Literal["ANSWERED", "ABSTAINED", "HALTED_BREAKER", "PENDING_APPROVAL", "REJECTED", "DEGRADED"]
    route: Route
    answer: str | None
    citations: list[ChunkId] = Field(default_factory=list, max_length=4)
    fallback_reason: str | None = None


class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    intent: Literal["POLICY_QA", "PRODUCT_LOOKUP", "FORM", "TRANSITION", "SMALLTALK", "OOS"]
    domain: str = Field(pattern=r"^[a-z_]{2,32}$")
    risk_tier: RiskTier
    complexity: Literal["SIMPLE", "MULTI_HOP", "SUMMARY"]
    route: Route
    confidence: float = Field(ge=0.0, le=1.0)
