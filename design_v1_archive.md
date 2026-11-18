# design.md — "The Unreliable Assistant" → Enterprise Grade

**Status:** UNLOCKED — v1.0 was locked on 2026-11-13 and has been reopened for revision. Decisions D-1 … D-8 remain answered by the project owner. Changes from v0.1 are listed in §0 under "Decision outcomes".

Where this file's numbers differ from the source specification, the difference is recorded in the relevant section and in Section 0. Nothing was silently changed.

---

## 0. Open Decisions (blocking lock)

The source specification contains conflicts that cannot be resolved without a product or infrastructure decision. Each one lists a recommended default. Items marked **[needs sign-off]** change what gets built.

| ID | Conflict | Options | Recommended default |
|---|---|---|---|
| **D-1** [needs sign-off] | An LLM-generated answer cannot complete inside e2e p95 ≤ 450 ms (see §1.4: ~25 output tokens max at 100 tok/s). | (a) Extractive-first: answer = verified sentences from top-4 chunks with citations, zero generation. Generation only for Summary/Complex. (b) Generative everywhere; e2e gate applies to TTFT only. | **(a)**. The e2e gate then holds for the default path. Generative path reports e2e separately and is gated on TTFT. |
| **D-2** | Gate 0 says `FAISS-IVF-PQ`; Gate 3 says `FAISS HNSW, efSearch=64`. | HNSW (exact match to Gate 3 params) or IVF-PQ (smaller memory). | **HNSW**. Revisit with IVF-PQ only if the corpus exceeds RAM on one shard. |
| **D-3** [needs sign-off] | Cache key `SHA256(tenant_id:user_id:norm_query)` is user-scoped. At 50K users a user rarely repeats a query, so KPI-27 (≥35% hit ratio) is at serious risk. | (a) Keep user-scoped keys (spec mandate). (b) Add a tenant-scoped cache tier for policy-only answers that contain no user-specific entities. | **(a) as the mandate**, with (b) as an explicit exception if KPI-27 is to be met. Requires your approval because it weakens the isolation rule for some responses. |
| **D-4** [needs sign-off] | Cross-encoder scores 40 candidates × up to 512 tokens ≈ 20K tokens per request. At the KPI-35 floor (8,500 RPS, 65% cache miss) that is ~113 M tok/s. That is hundreds of accelerators. | (a) Keep pool=40, max_len=512 (spec). (b) Rerank top-20 with max_len=256 (≈ 4× cheaper). (c) Add an early-exit cascade. | **(b)**, validated against KPI-09 and KPI-11 in the GPU phase. |
| **D-5** [needs sign-off] | 50,000 VUs cannot run on the current sandbox (4 vCPU, 15 GiB RAM, no GPU). Several latency KPIs need an accelerator. | (a) Provide a load cluster (10 injectors + SUT). (b) Verify at reduced scale locally and declare the 50K gate as cluster-only. | **(b) now, (a) before release.** Every KPI is tagged `CI / GPU / LOAD / SOAK` in §1.5 so the status is clear. |
| **D-6** [needs sign-off] | KPI-29 (≤ $0.45 per 1,000 queries) with ~1,320 input tokens per query means ≤ ~$0.34 per million input tokens, averaged across cache hits and misses. | Small self-hosted model with caching, or a small hosted tier. | Decide after the model bake-off. Until then KPI-29 is **unverified**. |
| **D-7** | Several KPIs cannot be statistically resolved at the stated sample sizes. KPI-03 (≥99.5%) over 100 prompts allows 0 misses. KPI-23 (≥98%) over 37 negatives requires 37/37. KPI-02 (≥99.8%) over fewer than 500 spans is meaningless. | Enlarge the datasets. | Injection ≥ 1,000 prompts. Negatives ≥ 200. PII spans ≥ 1,000. Triage labels ≥ 500. Benign controls ≥ 500. **Data authoring effort, needs your go-ahead.** |
| **D-8** [needs sign-off] | The 250-question Golden Dataset does not exist in this workspace. The stratified counts also do not divide evenly. | Author and label it, or import it. | Stratified counts: Factoid 63, Complex 62, Multi-Hop 50, Summary 38, Negative 37 (sum 250). Labeling owner needed. |

**Decision outcomes (locked 2026-11-13):**

| ID | Answer | Effect on the design |
|---|---|---|
| D-1 | Extractive-first | Default answer mode is extractive (ADR-09). Generative mode only for Summary/Complex; its e2e is reported separately. |
| D-2 | HNSW | FAISS HNSW, efSearch=64 (ADR-06 closed). |
| D-3 | Keep user-scoped keys | Mandate kept. KPI-27 is tracked as **at risk**; no tenant-scoped tier is built. |
| D-4 | Top-20 rerank | Stage 3 scores the top 20 of the 40-item RRF pool at max_len 256. RRF pool stays 40. See §1.4.5 and ADR-02. |
| D-5 | Local now, cluster later | Build and verify at reduced scale on this host. LOAD-tagged KPIs stay `NOT MEASURED` until a cluster run. |
| D-6 | GPT-5 family | Generative path uses an OpenAI GPT-5-family model, set by config. Confirmed default `gpt-5-mini` (confirmed by the project owner, 2026-11-13). |
| D-7 | Yes, author the datasets | Project authors injection, PII, benign, negative and triage sets. These are **synthetic** and measure the generator and the detector together, not production traffic. |
| D-8 | Project owner authors the golden set | The 250-question golden set is owned by the project owner. The schema is in `eval/golden_schema.md`. |

**D-6 cost and latency consequences (from OpenAI's pricing page, fetched 2026-11-13):**

| Model | Input $/1M | Cached $/1M | Output $/1M | KPI-29 cost per 1,000 queries (1,320 in + 27 out, all generative) | Blended at 40% generative share |
|---|---|---|---|---|---|
| gpt-5 | 1.25 | 0.125 | 10.00 | ≈ $1.92 | ≈ $0.77 — **fails** ≤ $0.45 |
| gpt-5-mini | 0.25 | 0.025 | 2.00 | ≈ $0.38 | ≈ $0.15 — passes |

- **Recommendation:** `gpt-5-mini`, with `reasoning_effort=minimal`. The pricing page does not document how reasoning tokens are billed. If they bill as output, the cost math above is optimistic. KPI-29 is therefore **unverified** until a metered run.
- **Latency risk (KPI-25):** TTFT for hosted GPT-5-family calls was not found in any source checked. A hosted call adds network time that the 60 ms prefill assumption in §1.4.2 does not include. Expect KPI-25 to **fail** on the generative path until measured. The extractive path makes no LLM call and is unaffected. The "Fast" tier (priority) buys lower latency at roughly 2× the standard price.
- **Config:** `LLM_MODEL` is an environment variable. The default is `gpt-5-mini`. Changing it does not require code changes.

**Other resolved conflicts (no sign-off needed, recorded for traceability):**

- **Reranker placement.** Gate 0 says "in-process Triton/ONNX microservice". Resolved as a **separate worker** so it has its own bulkhead (Gate 9). See ADR-08.
- **Audit Subagent** (Gate 2) = **Verification Subagent** (Gate 7). Same component. Gate 2 lists it as a triage branch, but Gate 7 defines it as a veto on *drafted* answers. It is therefore drawn after the ReAct loop, and triage never routes to it. The diagram follows the Gate 7 meaning.
- **REJECT_OOS** is in Gate 6 but missing from the Gate 2 list. It is included as a fourth triage branch.
- **Triage target** is ≤ 25 ms (Gate 6, KPI-05). Gate 2's "< 30 ms" is superseded.
- **p99 ceiling.** §1.4 proves p99 ≤ 600 ms structurally through hard timeouts. KPI-33 is set at ≤ 590 ms so the gate sits just under the ceiling. KPI-31 is set at ≤ 240 ms (k6 script said 250; aligned to the KPI).
- **Loop budget.** `total_loop_budget = 450 ms` is a cap on the ReAct loop only, not the whole request. Pre-loop stages consume part of the 450 ms e2e budget. See §1.4 and ADR-04.
- **Per-step timeout conflict.** `per_step_timeout = 120 ms` is less than the `hybrid-rag` MCP timeout of `150 ms`. Resolution: `hybrid_retrieve` uses 150 ms (config), all other tool calls use 120 ms. ADR-04.
- **Concurrency arithmetic.** With `sleep(1)` think time, 50K VUs at 0.4 s latency offer ≈ 35,700 RPS, not 8,500. KPI-35 (≥ 8,500 RPS) is therefore a *floor*. The load test will offer ~4× the floor. Both numbers are recorded in §1.4.

---

## 1.1 System Charter & Hard Boundaries

**Purpose.** An enterprise assistant that answers policy, SOP, and product-catalog questions for tenants using verified, cited knowledge, and can execute form submissions and workflow transitions on the user's behalf.

**In scope**
- Tenant-isolated question answering over a curated Obsidian vault (`knowledge_vault/`) and chunked source documents.
- Read-only retrieval and citation (Retrieval Subagent).
- Schema-validated state mutations through `mcp-state-store` (Execution Subagent), gated by RBAC tiers and HITL.
- Full audit trail, hash-chained (Governance).

**Non-goals (hard boundaries)**
1. **Zero autonomous clinical, financial, or legal execution.** Any action whose `risk_tier` is `HIGH` or `CRITICAL` is classified `TIER_3_MUTATE` and cannot complete without an approved `PENDING_APPROVAL` record.
2. No open-web access. The Execution Subagent has no retrieval tools. The Retrieval Subagent cannot call state-mutating tools.
3. No raw PII persisted. Requests are redacted before any write, including logs, traces, cache keys, and audit payloads.
4. No cross-tenant read or write under any path, including failure paths.
5. The assistant never states a fact it cannot cite. Ungrounded answers are replaced by abstention.
6. No model-generated text is executed as code, SQL, or a tool call without passing the ToolExecutionHarness (§1.2).

**User personas**

| Persona | Goal | Constraints |
|---|---|---|
| **Employee (end user)** | Get a cited answer or start a routine form. | Sees only their tenant. Cannot approve their own mutations. |
| **Reviewer (HITL approver)** | Approve or reject `PENDING_APPROVAL` mutations. | Separate role from the requester. Decisions are audited. |
| **Knowledge Owner** | Author and version vault notes (`id`, `domain`, `risk_tier`, `version`). | Cannot approve mutations. |
| **Platform SRE** | Run, scale, and recover the system. Read traces and KPIs. | No access to tenant content, only to redacted telemetry. |
| **Compliance Auditor** | Verify the hash chain and HITL enforcement. | Read-only on the audit log. |
| **Developer** | Use the Streamlit workbench to verify each gate. | Non-production tenants only. |

---

## 1.2 MCP Tool & Data Contracts

### 1.2.1 Transport and servers

All tools use **MCP over JSON-RPC 2.0**. Methods used: `initialize`, `tools/list`, `tools/call`, `resources/list`, `resources/read`.

| Server | Transport | Tools | Resources |
|---|---|---|---|
| `mcp-hybrid-rag` | stdio (local worker) | `hybrid_retrieve` (TIER_1_READ) | `rag://chunks/{id}` |
| `mcp-graphify-vault` | stdio | `graph_subgraph` (TIER_1_READ) | `vault://note/{id}` |
| `mcp-state-store` | Streamable HTTP (`:8081/mcp`) | `session_facts_get` (TIER_1), `session_facts_upsert` (TIER_2_COMPUTE), `workflow_transition` (TIER_2 or TIER_3 by `risk_tier`), `form_submit` (TIER_3_MUTATE), `pending_approval_decide` (reviewer only), `audit_append` (internal) | — |

`mcp_config.json` is reproduced from the specification. One change: `hybrid-rag` `timeout_ms` is **150**, per the spec. The per-step budget conflict is handled as described in §0.

### 1.2.2 Error codes

| Code | Name | Meaning | Caller behaviour |
|---|---|---|---|
| -32602 | `E_INVALID_PARAMS` | Pydantic validation failed after repair | Return 422 to user with the field and the expected format |
| -32001 | `E_TIMEOUT` | Hard per-call timeout exceeded | Fallback per FMEA §1.6 |
| -32002 | `E_CIRCUIT_OPEN` | `(tool_name, canonical_args_hash)` breaker tripped, or server bulkhead open | `HALTED_BREAKER` plus remediation prompt |
| -32003 | `E_RBAC_DENIED` | Caller's tier is below the tool's tier | Refuse, audit the attempt |
| -32004 | `E_HITL_PENDING` | Tier 3 mutation written as `PENDING_APPROVAL` | Return `PENDING_APPROVAL` status, not an error to the model |
| -32005 | `E_TENANT_MISMATCH` | `X-Tenant-ID` header ≠ body `tenant_id`, or a record's tenant ≠ request tenant | 403, audit, no body returned |
| -32006 | `E_INJECTION_BLOCKED` | Injection Shield tripped | Refuse with a generic message; do not echo the trigger |
| -32007 | `E_PII_BLOCKED` | PII Redactor fail-closed on an unredactable span | Refuse with a generic message |
| -32008 | `E_IDEMPOTENCY_CONFLICT` | Same `idempotency_key` with a different payload hash | 409 |
| -32009 | `E_LOOP_BUDGET` | `max_iterations` or `total_loop_budget` exhausted | `HALTED` with partial result if one exists |
| -32010 | `E_DEPENDENCY_UNAVAILABLE` | MCP server or Postgres unreachable | Fallback per FMEA §1.6 |
| -32011 | `E_STATE_READ_ONLY` | Mutation requested while `state-store` is degraded | Reject mutation, allow reads |

### 1.2.3 Pydantic v2 models (contract, not implementation)

```python
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

TenantId  = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
UserId    = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
SkuId     = Annotated[str, StringConstraints(pattern=r"^SKU-\d{3}$")]
LotId     = Annotated[str, StringConstraints(pattern=r"^LOT-\d{5}$")]
UuidV4    = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ChunkId   = Annotated[str, StringConstraints(pattern=r"^CHK-[0-9a-f]{12}$")]
AnchorId  = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2,5}-[A-Z0-9]{2,8}(-\d{3,6})?$")]


class HybridRetrieveInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tenant_id: TenantId
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=50, ge=1, le=50)
    pool: int = Field(default=40, ge=1, le=40)
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
    confidence: float = Field(ge=0.0, le=1.0)   # sigmoid(rerank logit)


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
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    action: Literal["form_submit", "workflow_transition"]


class AgentQueryRequest(BaseModel):                       # POST /api/v1/agent/query
    model_config = ConfigDict(extra="forbid")
    tenant_id: TenantId
    user_id: UserId
    query: str = Field(min_length=1, max_length=2000)


class AgentQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: TenantId        # echoed so the k6 cross-tenant check can run
    request_id: UuidV4
    trace_id: str              # W3C traceparent trace-id
    status: Literal["ANSWERED", "ABSTAINED", "HALTED_BREAKER", "PENDING_APPROVAL", "REJECTED", "DEGRADED"]
    route: Literal["DIRECT_ANSWER", "HYBRID_RAG", "TRANSACTION_EXEC", "REJECT_OOS"]
    answer: str | None
    citations: list[ChunkId] = Field(default_factory=list, max_length=4)
    fallback_reason: str | None = None
```

### 1.2.4 Triage schema

```python
class TriageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    intent: Literal["POLICY_QA", "PRODUCT_LOOKUP", "FORM", "TRANSITION", "SMALLTALK", "OOS"]
    domain: str = Field(pattern=r"^[a-z_]{2,32}$")
    risk_tier: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    complexity: Literal["SIMPLE", "MULTI_HOP", "SUMMARY"]
    route: Literal["DIRECT_ANSWER", "HYBRID_RAG", "TRANSACTION_EXEC", "REJECT_OOS"]
    confidence: float = Field(ge=0.0, le=1.0)
```

Rule: `confidence < 0.80` forces `route = HYBRID_RAG` (read-only, safe). Triage never defaults to `TRANSACTION_EXEC`.

### 1.2.5 Deterministic repair rules (ToolExecutionHarness step 2)

| Input | Pattern | Repaired | Rule |
|---|---|---|---|
| `SKU99`, `sku 99`, `SKU-99` | `^\s*sku[\s-]*(\d{1,3})\s*$` (i) | `SKU-099` | Zero-pad to 3 digits |
| `lot 44`, `LOT44`, `lot-44` | `^\s*lot[\s-]*(\d{1,5})\s*$` (i) | `LOT-00044` | Zero-pad to 5 digits |
| Homoglyph / fullwidth input | any | NFKC-normalized first | Applied before every regex |

A repair is accepted only when exactly one pattern matches and the numeric value is in range. Otherwise the call fails with `E_INVALID_PARAMS`. No guessing.

### 1.2.6 Normalization and cache key

```
norm_query = casefold(NFKC(collapse_ws(query))) |> canonical_entities
cache_key  = f"cache:v1:{tenant_id}:{sha256(f'{tenant_id}:{user_id}:{norm_query}')}"
ttl        = 300 + uniform(-45, +45) seconds
```

---

## 1.3 Distributed Topology & Data Flow

```
                         ┌──────────────────────────────────────────────┐
  Clients (50K VUs) ───▶ │ NGINX / Envoy  (TLS, X-Tenant-ID check,       │
                         │                 rate limit, L7 LB)            │
                         └──────────────────┬───────────────────────────┘
                                            │ HTTP/2
                         ┌──────────────────▼───────────────────────────┐
                         │ FastAPI workers (stateless, N replicas)       │
                         │  ├─ Pre-hooks: PII Redactor → Injection Shield│
                         │  ├─ Cache lookup ───────────▶ Redis Cluster   │
                         │  ├─ Triage (distilled, ≤25 ms, temp=0.0)      │
                         │  ├─ Agent loop (bounded ReAct, max_steps=5)   │
                         │  │     ToolExecutionHarness → Circuit Breaker │
                         │  ├─ Subagents: Retrieval / Execution /        │
                         │  │             Verification                   │
                         │  └─ Post-hooks: RAGAS auditor, OTel exporter  │
                         └───┬──────────────┬──────────────┬─────────────┘
              stdio/JSON-RPC │              │ Streamable   │ gRPC/HTTP
                             ▼              ▼ HTTP         ▼
              ┌────────────────────┐ ┌─────────────────┐ ┌──────────────────────┐
              │ mcp-hybrid-rag     │ │ mcp-state-store │ │ mcp-graphify-vault   │
              │  BM25 (in-memory)  │ │  Postgres 16    │ │  graph.json + vault  │
              │  FAISS HNSW        │ │  (pgvector)     │ │  depth ≤ 2 traversal │
              │  bge-large ONNX    │ │  session_facts  │ └──────────────────────┘
              │  RRF               │ │  pending_appr.  │
              └─────────┬──────────┘ │  audit_log(hash)│
                        │ gRPC       └────────┬────────┘
                        ▼                     │
              ┌────────────────────┐          │
              │ Reranker workers   │          │
              │ bge-reranker-v2-m3 │          │
              │ ONNX FP16, batch=32│          │
              │ dyn. batch 8 ms    │          │
              └────────────────────┘          │
                                             │
  Redis Cluster ◀──── cache, hot ReAct window (last 3 turns) ────┘ (tenant-prefixed keys)

  OTel SDK (all hops) ──OTLP/gRPC──▶ OTel Collector ──▶ Prometheus (metrics) · Tempo (traces) ──▶ Grafana
```

**Bulkheads (Gate 9):** separate pools per MCP server — `rag_pool=64`, `db_pool=128`, `llm_pool=256`. The health-check pool is separate from all three.

**Data-at-rest owners**

| Store | Holds | Tenant control |
|---|---|---|
| Redis Cluster | Semantic cache, 3-turn ReAct window, circuit-breaker counters | Key prefix `cache:v1:{tenant_id}:` and per-tenant ACL |
| Postgres | `session_facts`, `pending_approvals`, `audit_log` (hash-chained) | `tenant_id` NOT NULL on every table; row-level security policy; every query includes the tenant predicate |
| FAISS / BM25 index | Chunks and embeddings | Per-tenant shard or tenant filter applied before ranking |
| Obsidian vault / graph.json | Policy notes and code graph | Notes carry `domain`, `risk_tier`; tenant overlays are separate directories |

---

## 1.4 End-to-End Latency Budget (200–600 ms)

### 1.4.1 Critical path, cache miss, HYBRID_RAG route, extractive response (D-1 default)

Service-time budget at p95 for one request. Stages marked ∥ run in parallel and only the longest one counts.

| # | Stage | Budget (ms) | On critical path? | Source of budget |
|---|---|---:|:---:|---|
| 1 | Ingress: TLS, auth, tenant header check | 5 | Yes | Envoy/FastAPI overhead |
| 2 | `hook.pre`: PII Redactor → Injection Shield | 15 | Yes | KPI-01 |
| 3 | `cache.lookup`: Redis GET; semantic probe on miss | 10 | Yes | Redis sub-ms GET + query encoding for probe |
| 4 | MCP JSON-RPC call to `hybrid-rag` (serialization + transport) | 6 | Yes | KPI-30 (≤4 ms) + 2 ms transport |
| 5a | `mcp.rag.bm25` | 12 | ∥ | KPI-07 |
| 5b | `mcp.rag.biencoder` (query encode + FAISS HNSW, efSearch=64) | 25 | ∥ (longest) | KPI-08 |
| 6 | `mcp.rag.rrf` (k=60, dedup by chunk_hash) | 3 | Yes | Gate 3 |
| 7 | `mcp.rag.rerank` (cross-encoder, sigmoid ≥ 0.42) | 45 | Yes | KPI-09 |
| 8 | Abstention check (deterministic) | 1 | Yes | Gate 9 |
| 9 | Compose extractive answer + citations | 5 | Yes | Local compute |
| 10 | Egress serialization + response | 5 | Yes | KPI-30-class budget |
| — | `triage.classify` | 25 | **Hidden** (speculative, parallel with step 4–7; ≤ 79 ms) | KPI-05 |
| — | `graph_subgraph` (`mcp-graphify-vault`) | ≤10 | **Hidden** (parallel with rerank) | Gate 17 |
| | **Sum on critical path** | **120** | | Headroom to 450 ms p95: **330 ms** for network, queueing, and tail |

Triage runs speculatively. If the route turns out to be `DIRECT_ANSWER` or `REJECT_OOS`, the retrieval work is discarded. That costs CPU but saves ~25 ms on every RAG-bound request. ADR-07.

### 1.4.2 Generative path (D-1 option b, or Summary/Complex under option a)

| Stage | ms |
|---|---:|
| Steps 1–7 above, plus prompt assembly (GoalAnchorBlock + compressed observations ≤ 180 tok each, ~320 tok subgraph) | 5 + 15 + 10 + 6 + 25 + 3 + 45 + 5 = **114** |
| Prefill + first token, 8B-class model, accelerator | **60** |
| **TTFT (p95 target ≤ 180)** | **≈ 174 → 6 ms margin** |
| Output generation at ≥ 100 tok/s | 10 ms / token |
| Max output tokens to stay inside 450 ms p95 e2e (before queueing) | (450 − 174 − 5) / 10 ≈ **27 tokens** |

This is why D-1 matters. A generative answer fits the e2e gate only if it is ~27 tokens or fewer. Anything longer must be extractive or accept a separate e2e SLO.

### 1.4.3 Worst-case bound (p99 ≤ 600 ms is structural, not statistical)

Hard timeouts per stage (fallback fires on expiry):

| Stage | Hard timeout | Fallback |
|---|---:|---|
| Ingress | 10 | Reject 503 |
| `hook.pre` | 25 | Fail-closed → `E_PII_BLOCKED`/`E_INJECTION_BLOCKED` |
| `cache.lookup` | 15 | Treat as miss (fail-open for cache only) |
| `hybrid_retrieve` | 150 (`timeout_ms` in config) | FMEA rows 3, 4, 5 |
| `triage` (hidden) | 25 | Default route `HYBRID_RAG` |
| LLM TTFT | 250 | Extractive fallback (no generation) |
| Compose + egress | 25 | — |

Worst case: 10 + 25 + 15 + 150 + 250 + 25 = **475 ms**, plus a fallback allowance of 25 ms = **500 ms** (< 600). Gated p99 is set at 590 (KPI-33). The 100 ms of headroom absorbs queueing spikes below the timeout ceiling.

### 1.4.4 Load arithmetic (why KPI-35 is a floor)

- With `sleep(1)` think time and 50,000 VUs at ~0.4 s mean latency: offered load ≈ 50,000 / 1.4 ≈ **35,700 RPS**.
- Little's law: 8,500 RPS at 1.4 s per iteration implies only ~11,900 concurrent VUs. The 50K VU test therefore offers ~4× the KPI-35 floor. This is intended: the stress test proves the floor with margin.
- Utilization target: ≤ 60% per stage at the 50K offered load. Queueing above that breaks the p95 gate regardless of the service-time budget above.

### 1.4.5 Reranker capacity check (D-4, locked to top-20 at max_len 256)

- Before D-4: pool 40 × max_len 512 ≈ 20,480 tokens per cache-miss request ≈ 113 M tok/s at the KPI-35 floor. Hundreds of accelerators.
- After D-4: top-20 × max_len 256 = 5,120 tokens per request. At 5,525 misses/s that is ≈ **28 M tok/s**. Still GPU-scale, but ~4× smaller.
- The 45 ms KPI-09 budget is unchanged. It must be re-measured on the GPU phase with the top-20 input, because the latency figure was never measured.

---

## 1.5 KPI & SLA Gate Matrix (36 KPIs)

**Verification tags:** `CI` = runnable on a CPU sandbox with a labeled dataset. `GPU` = needs an accelerator for the latency to mean anything. `EVAL` = needs the golden dataset and an LLM judge. `LOAD` = needs the 50K cluster (D-5). `SOAK` = 1-hour run, single node acceptable. **Status** is `NOT STARTED` for every row until the code exists; no KPI is claimed as passed.

| ID | Stage | KPI | Hard threshold | Source | Tag | Data needed |
|---|---|---|---|---|---|---|
| KPI-01 | 1 Ingress & Triage | Pre-hook guardrail latency (p95) | ≤ 15 ms | OTel `hook.pre` | CI | — |
| KPI-02 | 1 Ingress & Triage | PII redaction recall (email/phone/SSN/obfuscated) | ≥ 99.8% | Presidio + regex audit | CI | ≥ 1,000 labeled spans (D-7) |
| KPI-03 | 1 Ingress & Triage | Prompt-injection block rate (direct + indirect) | ≥ 99.5% | Red-team benchmark | CI | ≥ 1,000 prompts (D-7) |
| KPI-04 | 1 Ingress & Triage | Triage routing accuracy | ≥ 97.0% | Labeled intent set | CI | ≥ 500 labeled queries (D-7) |
| KPI-05 | 1 Ingress & Triage | Triage classification latency (p95) | ≤ 25 ms | OTel `triage.classify` | CI | — |
| KPI-06 | 1 Ingress & Triage | False-positive refusal on benign queries | ≤ 1.0% | Benign control set | CI | ≥ 500 benign queries (D-7) |
| KPI-07 | 2 Hybrid Retrieval | Stage 1 BM25 latency (p95) | ≤ 12 ms | OTel `mcp.rag.bm25` | CI | Corpus size fixed in ADR-01 |
| KPI-08 | 2 Hybrid Retrieval | Stage 2 bi-encoder + FAISS latency (p95) | ≤ 25 ms | OTel `mcp.rag.biencoder` | GPU | Index built |
| KPI-09 | 2 Hybrid Retrieval | Stage 3 cross-encoder rerank latency (p95) | ≤ 45 ms | OTel `mcp.rag.rerank` | GPU | Model + D-4 choice |
| KPI-10 | 2 Hybrid Retrieval | Hybrid context recall@50 | ≥ 0.92 | RAGAS / IR eval | EVAL | Golden set (D-8) |
| KPI-11 | 2 Hybrid Retrieval | Post-rerank context precision@4 | ≥ 0.88 | RAGAS | EVAL | Golden set |
| KPI-12 | 2 Hybrid Retrieval | MRR@4 | ≥ 0.85 | IR script | EVAL | Golden set |
| KPI-13 | 2 Hybrid Retrieval | nDCG@4 | ≥ 0.86 | IR script | EVAL | Golden set |
| KPI-14 | 3 Agent & Loop | Tool schema first-pass validation rate | ≥ 96.0% | Pydantic telemetry | CI | Traffic simulator |
| KPI-15 | 3 Agent & Loop | Auto-repair success on malformed args | ≥ 90.0% | Harness metrics | CI | Malformed-arg corpus |
| KPI-16 | 3 Agent & Loop | Infinite-loop escape rate (circuit breaker) | 100.0% | Chaos loop suite | CI | — |
| KPI-17 | 3 Agent & Loop | Mean ReAct steps per completed task | ≤ 2.2 | OTel trace count | EVAL | Golden set |
| KPI-18 | 3 Agent & Loop | Context drift score (goal cosine) | ≥ 0.85 | Embedding drift audit | EVAL | Embedding model |
| KPI-19 | 3 Agent & Loop | Tier 3 HITL enforcement rate | 100.0% | Audit-log integrity | CI | — |
| KPI-20 | 4 Generation & RAGAS | RAGAS faithfulness | ≥ 0.92 | Post-hook RAGAS | EVAL | Golden set + judge |
| KPI-21 | 4 Generation & RAGAS | RAGAS answer relevancy | ≥ 0.90 | RAGAS | EVAL | Golden set + judge |
| KPI-22 | 4 Generation & RAGAS | RAGAS context entity recall | ≥ 0.85 | RAGAS | EVAL | Golden set |
| KPI-23 | 4 Generation & RAGAS | Unanswerable abstention accuracy | ≥ 98.0% | Negative set | EVAL | ≥ 200 negatives (D-7) |
| KPI-24 | 4 Generation & RAGAS | Unsupported claim / hallucination rate | ≤ 0.5% | NLI claim verifier | EVAL | Verifier model |
| KPI-25 | 4 Generation & RAGAS | TTFT (p95) | ≤ 180 ms | FastAPI stream metric | GPU | Accelerator + LLM |
| KPI-26 | 5 Token & Graphify | Prompt token reduction (Graphify + Obsidian) | ≥ 45.0% | Token counter vs raw baseline | CI | Raw-chunk baseline |
| KPI-27 | 5 Token & Graphify | Semantic cache hit ratio (steady state) | ≥ 35.0% | Redis hit/miss | LOAD | At risk under D-3 |
| KPI-28 | 5 Token & Graphify | Cross-tenant cache isolation | 100.0% (0 leaks) | Concurrency probe | CI + LOAD | — |
| KPI-29 | 5 Token & Graphify | Cost per 1,000 queries | ≤ $0.45 | OTel token cost | EVAL | D-6 |
| KPI-30 | 5 Token & Graphify | MCP JSON-RPC serialization overhead (p95) | ≤ 4 ms | MCP client timer | CI | — |
| KPI-31 | 6 Scale & Load | e2e response latency p50 @ 50K VUs | ≤ 240 ms | k6 + JMeter | LOAD | D-5 |
| KPI-32 | 6 Scale & Load | e2e response latency p95 @ 50K VUs | ≤ 450 ms | k6 + JMeter | LOAD | D-5 |
| KPI-33 | 6 Scale & Load | e2e response latency p99 @ 50K VUs | ≤ 590 ms | k6 + JMeter | LOAD | D-5 |
| KPI-34 | 6 Scale & Load | HTTP 5xx rate @ 50K VUs | ≤ 0.05% | k6 / JMeter | LOAD | D-5 |
| KPI-35 | 6 Scale & Load | Sustained throughput | ≥ 8,500 RPS (floor, §1.4.4) | k6 / JMeter | LOAD | D-5 |
| KPI-36 | 6 Scale & Load | RSS growth over 1-hour soak | ≤ 3.0% | Prometheus node exporter | SOAK | Single node OK |

**Note on KPI-33 and the k6 script.** The script's threshold `p(99)<600` is kept as a hard ceiling. KPI-33 gates at ≤ 590 ms. The k6 script's `p(50)<250` is changed to `p(50)<240` to match KPI-31. The check `r.timings.duration <= 600` is kept.

**Note on KPI-33 and KPI-31 statistics.** Percentiles at 50K VUs need ≥ 1M samples per run for p99 to be stable. The 10-minute window at 8,500+ RPS gives ~5M, which is enough.

---

## 1.6 Failure Mode & Effects Analysis (FMEA, 12 rows)

RPN is not used; each row has a deterministic fallback and a fail-open or fail-closed classification. Fail-closed means the request is refused rather than served degraded.

| # | Failure | Effect | Detection | Deterministic fallback | Fail mode |
|---|---|---|---|---|---|
| 1 | **Redis down** | No semantic cache; ReAct window degrades | Connect error / 2 s health probe | Bypass cache (cache is an optimization); ReAct window read from in-process LRU limited to the current request; tenant predicates unchanged | Fail-open for cache, fail-closed for tenant isolation |
| 2 | **Cache key collision / stale cross-tenant entry** | Cross-tenant data disclosure | Response `tenant_id` check on every hit; KPI-28 probe | Drop the hit, recompute, alert `CACHE_TENANT_MISMATCH`, quarantine the key prefix | Fail-closed |
| 3 | **Reranker timeout or crash** (ONNX worker) | Cannot apply the 0.42 threshold | Hard timeout 80 ms or gRPC error; bulkhead breaker | Abstain with `status=DEGRADED` and a retry hint. The RRF top-4 is never served as "verified" | Fail-closed for claims |
| 4 | **FAISS index missing, stale, or failed load** | Dense leg unavailable | Startup checksum; index version in every response | Serve BM25-only results from the replica index, flagged `DEGRADED`, with `threshold` applied to BM25-normalized score and the answer forced extractive | Fail-open with flag |
| 5 | **MCP `hybrid-rag` hang or crash** | No retrieval | JSON-RPC timeout > 150 ms (> 1% rate triggers bulkhead trip) | Trip bulkhead; route to cached read-replica index; restart worker under supervisor | Fail-open to replica, flagged |
| 6 | **MCP `state-store` unavailable** | Cannot persist facts or approvals | Streamable HTTP connect failure | Reject all Tier 2/3 writes with `E_STATE_READ_ONLY`; allow reads from cache; `session_facts` not written | Fail-closed for writes |
| 7 | **MCP `graphify-vault` unavailable** | No subgraph pruning | Timeout 10 ms budget | Inject the top-4 chunk texts compressed to ≤ 180 tokens each; token saving lost for this request only | Fail-open, flagged |
| 8 | **LLM 429 / rate limit** | Generation blocked | HTTP 429 with `retry-after` | One retry with jitter, capped at 40 ms of remaining budget. If still blocked, force extractive mode (no LLM needed) | Fail-open to extractive |
| 9 | **LLM TTFT over 250 ms hard timeout** | Latency breach | Stream first-token timer | Cancel stream, return extractive answer; record `llm.generate` span with `timeout=true` | Fail-open to extractive |
| 10 | **Triage misroute or low confidence** | Wrong route (e.g., a read sent to Execution) | `confidence < 0.80` or disagreement with a rule check on `TRANSACTION` verbs | Force `HYBRID_RAG` (read-only). Never default to `TRANSACTION_EXEC` | Fail-closed for mutations |
| 11 | **Injection classifier false negative (indirect injection inside a retrieved chunk)** | Model follows instructions in a document | Verification Subagent NLI check; ToolExecutionHarness RBAC ignores any tool call not in the active route's allow-list | Output vetoed by Verification Subagent if ungrounded; mutation tools unavailable on the Retrieval route regardless of model output | Fail-closed (defence in depth) |
| 12 | **Postgres unavailable** (audit, approvals, session facts) | Cannot write the audit chain or HITL queue | Connection pool error; `db_pool` saturation | Reject Tier 2/3 actions (no audit = no action). Reads of the vault continue. Local hash-chained spool is **not** used, to avoid two chains | Fail-closed for mutations |

**Additional rows covered elsewhere (not counted in the 12):** runaway ReAct loop → §1.7 ADR-04 and KPI-16; OTel collector down → exporter is non-blocking and drops spans with a counter, requests unaffected.

---

## 1.7 Architecture Decision Records

### ADR-01 — Hybrid retrieval weights and RRF

- **Decision:** BM25Okapi (k1=1.25, b=0.75, top_k=50) and FAISS HNSW (efSearch=64, top_k=50) are fused with Reciprocal Rank Fusion, k=60, pool=40, dedup by `chunk_hash`. Equal weights (w_bm25 = w_dense = 1.0).
- **Why RRF:** Its inputs are ranks, not scores. BM25 and cosine scores are on incomparable scales, and RRF avoids normalizing them against each other. The `k=60` constant is the standard published default and is not tuned on this corpus yet.
- **Why equal weights:** No labeled data exists yet to justify unequal weights. Tab 2 exposes `k1`, `b`, `rrf_k` and `threshold` sliders so the team can tune on the golden set. Any change must be recorded here with its KPI-10–13 evidence.
- **Consequence:** Lexical-only queries (exact SKU/LOT IDs) rely on BM25 for recall. Entity normalization (§1.2.5) runs before BM25 tokenization so `SKU99` and `SKU-099` hit the same postings.
- **Stage 1 implementation notes (added with the BM25 build):**
  - **Corpus size:** 50,000 chunks total, the size used for KPI-07. Confirmed by the project owner (2026-11-13). This was missing from v1.0 and is now stated here.
  - **Stopwords:** A 25-word English function-word list is dropped before stemming. Without it, "for" and "the" let short chunks outrank the policy chunk.
  - **Repeated query terms** add weight, as in `rank_bm25`'s BM25Okapi. This makes the scores match that reference to about 4e-13.
  - **Measured on this host (4 vCPU, synthetic Zipf corpus, 500 queries):** p50 7.4 ms, p95 9.65 ms. KPI-07 passes here with a thin margin. It is not a production number.

### ADR-02 — ONNX quantization

- **Decision:** Reranker `bge-reranker-v2-m3` in **FP16** on accelerators. INT8 is permitted only for CPU-only fallback workers.
- **Gate:** INT8 is accepted only if nDCG@4 drops ≤ 0.5 points versus FP16 on the golden set and KPI-09 still holds. Otherwise FP16 only.
- **Bi-encoder:** `bge-large-en-v1.5` ONNX, FP16 on GPU, dynamic batching with `max_wait = 8 ms`.
- **Consequence:** INT8 is not used for the default path, so the GPU requirement in §1.4.5 stands.

### ADR-03 — Redis key isolation

- **Decision:** `cache:v1:{tenant_id}:{sha256(tenant_id:user_id:norm_query)}` with TTL 300 ± 45 s. Semantic probe searches only inside the same `{tenant_id, user_id}` partition, threshold cosine ≥ 0.92.
- **Why both tenant and user in the hash:** Defence in depth. A key collision in one dimension cannot cross the other. The tenant also appears in plaintext for ACL and for scan-based cleanup.
- **Cost:** The per-user partition limits hit ratio (D-3). This is the cause of KPI-27 risk and the reason D-3 needs sign-off.
- **Verification:** KPI-28 concurrency probe with ≥ 50 tenants and ≥ 1,000 users; plus a unit test that a forged tenant prefix is rejected.

### ADR-04 — Bounded ReAct loops

- **Decision:** State machine `INIT → TRIAGE → REASONING → TOOL_CALL → OBSERVATION → VERIFY → DONE | HALTED`. `max_iterations = 5`. `per_step_timeout = 120 ms` for tool calls other than `hybrid_retrieve`, which uses the 150 ms MCP timeout (resolves the conflict in §0). `total_loop_budget = 450 ms` is a cap on the loop only; the loop receives `min(450, 450 − pre_loop_elapsed)`.
- **Recursion rule:** No self-call without a state mutation. The same `(tool_name, canonical_args_hash)` may run at most twice before `HALTED_BREAKER`.
- **Why 5:** KPI-17 targets a mean of 2.2. A cap of 5 gives ~2× headroom. Gate 2 says `max_steps=5`, which matches.
- **Consequence:** Some complex multi-hop queries will halt with partial results; that is preferable to a loop.

### ADR-05 — Context anchoring and observation compression

- **Decision:** `GoalAnchorBlock` at index 0 of every ReAct prompt (original intent, normalized entity IDs, active policy). Observations compressed to ≤ 180 tokens by an extractive compressor that drops boilerplate JSON keys. Sliding window of 3 turns in Redis; verified facts persisted to `session_facts` in Postgres.
- **Why:** Tool output noise is the main drift source. Anchoring at index 0 keeps the goal in the most-attended position. KPI-18 measures it.

### ADR-06 — Index type

**Decision (D-2):** FAISS HNSW, efSearch=64. Exact match with Gate 3. Higher recall than IVF-PQ at the cost of RAM. Revisit only if the corpus exceeds RAM on one shard.

### ADR-07 — Triage as a distilled classifier, not an LLM

- **Decision:** Triage is a small distilled classifier (ONNX, ≤ 25 ms, `temperature=0.0` semantics: deterministic argmax) returning `TriageResult`. Speculative parallel execution with retrieval.
- **Why:** An LLM triage call cannot meet 25 ms. Speculative execution hides the 25 ms behind retrieval.
- **Fallback:** Confidence < 0.80 forces `HYBRID_RAG`.

### ADR-08 — Reranker as a separate worker, not in-process

- **Decision:** Reranker runs as its own worker pool with its own bulkhead, reached over gRPC. This overrides Gate 0's "in-process" wording.
- **Why:** Gate 9 requires a slow reranker to never starve health checks or triage. An in-process model shares the event loop and the GIL with the API. Separation is the only way to meet that requirement.

### ADR-09 — Extractive-first answering

- **Decision:** Default answer mode is extractive: selected sentences from the top-4 verified chunks, quoted verbatim, with citation IDs. Generative mode is used for Summary and Complex queries, and its e2e result is reported separately (see D-1).
- **Why:** Gate 10's "strict extractive quote mode" already exists as a runbook mitigation. Extraction cannot hallucinate beyond the verified span, which supports KPI-20 and KPI-24 directly.

---

## 1.8 Gate Checklist (for Streamlit Tab 1)

- [x] 1.1 System charter & hard boundaries — drafted
- [x] 1.2 MCP tool & data contracts — drafted (contracts only, no implementation)
- [x] 1.3 Distributed topology — drafted
- [x] 1.4 Latency budget — drafted; p95 ≤ 450 and p99 ≤ 600 are **budgeted, not measured**
- [x] 1.5 KPI matrix — 36 rows, thresholds fixed, **0 of 36 measured**
- [x] 1.6 FMEA — 12 rows
- [x] 1.7 ADRs — 9 records; ADR-06 defers to D-2
- [x] D-1 … D-8 answered
- [ ] `design.md` re-locked (currently UNLOCKED for revision)
- [x] `docs/workflow_orchestration.png` rendered at 300 DPI (Gate 2) — source `tools/render_workflow.py`, DOT in `docs/workflow_orchestration.dot`
