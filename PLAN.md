# PLAN.md — Reliable Assistant delivery plan

Date: 2026-11-18 · Source: Blueprint v2 (Gates 0–18, 36 KPIs) · Design: `design_v1.1.md` (refined) and `design.md` (v1, unlocked)

This plan is sequenced by gates and exit criteria, not calendar dates. Each phase ends with evidence, not with code written.

## 0. Where we are

| Area | State |
|---|---|
| Design | Two versions exist (`design.md`, `design_v1.1.md`). Neither is locked. Appendix B open items 13–20 remain. |
| Gate 2 diagram | Done, `docs/workflow_orchestration.png` (300 DPI). |
| Core code | Parked in `_parked_code/`: contracts, harness, breaker, loop, PII, injection, cache, audit, BM25. 55 tests pass. |
| Measured KPIs | 2 of 36, locally: KPI-01 (pre-hook p95 0.065 ms), KPI-07 (BM25 p95 9.65 ms, synthetic 50K). Neither is production evidence. |
| Infrastructure | 4 vCPU, 15 GiB, no GPU. No Redis, Postgres, MCP servers, or FastAPI yet. |
| Owner inputs | Golden set (D-8), HITL legal wording, GPU SKU, arrival model, KPI-27 acceptance. |

## 1. Decisions needed before lock (blocking)

| # | Decision | Why it blocks | Recommended |
|---|---|---|---|
| A | Which file is canonical: `design.md` or `design_v1.1.md` | Two drafts will drift | `design_v1.1.md`, with the §0 decision record copied in. Archive v1 |
| B | Legal wording for clinical/financial/legal disclaimers (App. B-15) | Gate 9 HITL, NG-1 | Legal review before Phase 5 |
| C | GPU SKU and cloud budget (App. B-14) | KPI-08, 09, 25 cannot be measured without it | One GPU node for measurement, then the 50K cluster later |
| D | Arrival model for k6/JMeter (App. B-17) | Offered load is ~4× the KPI-35 floor | Constant arrival rate at the floor × 1.5 margin, with `sleep(1)` kept only as a secondary scenario |
| E | Accept KPI-27 risk or reopen ADR-3 (App. B-16) | Per-user keys likely miss ≥ 35 % hit | Accept, and measure. Tenant-shared tier only with security sign-off |
| F | Golden set authorship (D-8), 250 questions | Blocks KPI-10–13, 17, 18, 20–23 | Owner authors. Schema in `eval/golden_schema.md` |
| G | Confirm `gpt-5-mini` and reasoning-token billing | Blocks KPI-25 and KPI-29 measurement | Measure with a 1K-call metered run |
| H | Generative model: hosted `gpt-5-mini` or self-hosted SLM on one L4 | Decides KPI-25 and KPI-29 feasibility | Keep `gpt-5-mini` for Phase 6. Run a bake-off of two SLMs on the golden Summary/Complex subset against the RAGAS gates, then decide on data |
| I | Triage model: encoder classifier or generative SLM | Triage must be ≤ 25 ms (KPI-05) | Encoder classifier (ONNX). The guide's ~45 ms figure is generative TTFT, not a classifier budget, and is unverified on our hardware |
| J | Fine-tuning on tenant data | Conflicts with NG-3 ("no training on tenant data") | Do not fine-tune on tenant data. Fine-tune only on owner-approved or synthetic corpora. Any tenant-data training needs an NG-3 change, a privacy review, and legal sign-off |

## 2. Phases

Each phase has entry conditions, deliverables, and an exit gate. Nothing in a later phase can be marked green while an earlier exit gate is open.

### Phase 0 — Lock the design
- **Entry:** Decisions A–G answered, or explicitly deferred with a named owner.
- **Deliverables:** One canonical `design.md`, Appendix B reduced to zero open blocking items, and a `design-locked` tag (git tag once a repo exists).
- **Exit:** Owner sign-off recorded in §0 of the design.

### Phase 1 — Repository foundation and contracts
- **Deliverables:**
  - Restore `src/unreliable/`, `tests/`, and `pyproject.toml` from `_parked_code/`.
  - Fix the six mismatches in design Appendix F: `ChunkId` format, `bm25_k1` range, cache key format, `RerankIn` contract with `max_len=256`, `SKU` pattern length, and repair alignment.
  - Add `answer_mode` to `AuditRecord` and the response models.
  - Lint and type-check gates (ruff, mypy) in CI.
  - Contract tests that parse every JSON/Pydantic example in the design.
- **Exit:** Tests pass. Every design contract example validates. No code-to-design mismatch remains.

### Phase 2 — Ingress and security hooks
- **Deliverables:**
  - PII redactor and injection shield expanded to the D-7 corpora: ≥ 1,000 PII spans, ≥ 1,000 injection prompts, ≥ 500 benign queries.
  - ML injection classifier layer (the rule layer alone will miss paraphrases).
  - Gateway JWT → tenant derivation, with the `X-Tenant-ID` assertion rule (`-32011`).
- **Exit:** KPI-01 re-measured under the realistic corpus. KPI-02, KPI-03, KPI-06 measured on the labeled sets. Reported with the caveat that synthetic data measures generator plus detector together.

### Phase 3 — MCP servers and state
- **Deliverables:**
  - `mcp-hybrid-rag`: stdio server exposing `hybrid_retrieve` and `rag://chunks/{id}`, with BM25 (done), the dense leg, RRF, and the top-20 rerank client.
  - `mcp-state-store`: Streamable HTTP server on Postgres with pgvector. Row-level security by `tenant_id`. Tables: `session_facts`, `pending_approvals`, `audit_log` (hash-chained). Optimistic concurrency (`-32021`). WAL spool for fail-closed audit (FMEA #6).
  - `mcp-graphify-vault`: `graphify build` output, depth ≤ 2 traversal, `token_budget`.
  - Signed manifests (NG-7), verified at startup.
  - Redis Cluster with the `KeyBuilder` class (ADR-3). Ban raw string keys in lint.
- **Exit:** MCP conformance suite passes (`initialize`, `tools/list`, `tools/call`, `resources/read`). Missing `_meta.tenant_id` is rejected. Cross-tenant read attempts fail on the state store. KPI-30 (MCP serialization p95 ≤ 4 ms) measured.

### Phase 4 — Retrieval stages 2–3 (functional)
- **Deliverables:**
  - `bge-large-en-v1.5` ONNX export. FAISS HNSW with `efSearch=64`, chunks of 512 tokens with 64-token overlap, per-tenant shards or filters.
  - RRF (k=60, dedup by `chunk_hash`, pool 40).
  - `bge-reranker-v2-m3` ONNX on top 20 at `max_len=256`, with the 0.42 threshold and abstention.
  - Reranker as a separate worker with its own bulkhead (ADR-7).
- **Exit (functional, CPU):** Golden-set IR metrics computed (KPI-10–13) once the golden set exists. Abstention path correct. Latency numbers recorded but **not** claimed against KPI-08 or KPI-09.

### Phase 5 — Agent core and safety controls
- **Deliverables:**
  - Triage classifier (decision I): encoder ONNX model trained on the D-7 labeled intent set (≥ 500). Target ≤ 25 ms. Confirm it meets the budget on the target CPU before adopting it.
  - Speculative retrieval alongside triage, discarded on `DIRECT_ANSWER` / `REJECT_OOS`.
  - Bounded ReAct state machine wired to the harness and breaker (`max_iterations=5`, 120 ms per step, 450 ms loop).
  - `GoalAnchorBlock` at index 0. Observation compressor to ≤ 180 tokens. Three-turn window in Redis, with `session_facts` in Postgres.
  - Tier 3 HITL: `PENDING_APPROVAL` in Postgres, reviewer approve/reject API, and the Streamlit queue (Tab 1).
  - Deterministic abstention (< 90 ms, no LLM call).
  - Extractive composer (default answer mode, ADR-6).
  - Verification subagent (NLI claim-to-chunk).
- **Exit:**
  - Chaos loop suite: 100 % escape (KPI-16, zero tolerance).
  - HITL enforcement: 100 % (KPI-19, zero tolerance). No Tier 3 path executes without an approved record.
  - Triage accuracy and latency measured (KPI-04, KPI-05).
  - Abstention latency measured.

### Phase 6 — Generation path, RAGAS, and cost
- **Deliverables:**
  - `gpt-5-mini` adapter behind the `LLM_MODEL` variable. Zero retention configured (NG-3). Streaming SSE. Generative path limited to Summary and Complex queries.
  - SLM bake-off (decision H): two open-weight SLMs from the guide (for example Qwen2.5-7B-Instruct and Phi-4-mini, after license check) served on one L4. Evaluated on the golden Summary/Complex subset against the RAGAS gates, with TTFT and cost per 1,000 queries measured. Required before choosing a self-hosted option.
  - License check for each candidate before use. The guide lists Qwen2.5-3B as Apache 2.0, but its published license is the Qwen research license. Verify on each model card. The guide lists Ministral-3B as commercial. Treat it as unusable until its terms are reviewed.
  - Post-hook RAGAS auditor (faithfulness, relevancy, context precision/recall/entity recall) on the 250-question golden set.
  - Metered cost logging for KPI-29.
  - TTFT measurement on the hosted path (KPI-25).
- **Exit:** KPI-20–22 measured against the release gates. KPI-24 measured. KPI-23 measured with enough negatives. KPI-25 and KPI-29 reported as measured, including if they fail. The failure is a decision input, not something to hide.

### Phase 7 — Token optimization (Obsidian + Graphify)
- **Deliverables:** Vault notes with the required frontmatter (`id`, `domain`, `risk_tier`, `version`) and `[[wikilinks]]`. `graphify build` over `src/` and `knowledge_vault/`. Subgraph pruning measured against a raw-chunk baseline.
- **Exit:** KPI-26 (≥ 45 % token reduction) measured with the token counter hook. The 55 % figure in Gate 0 is a stretch target and is not the gate.

### Phase 8 — Reliability and observability
- **Deliverables:**
  - Bulkheads per dependency (`rag_pool=64`, `db_pool=128`, `llm_pool=256`).
  - Admission control and per-tenant fair-share buckets (FMEA #12).
  - OTel spans for every required hop, exported to the collector, then Tempo, Prometheus, and Grafana.
  - All 12 FMEA scenarios injected in a chaos suite, each checked against its fallback and degraded SLO (p99 ≤ 900 ms, zero cross-tenant leaks).
  - KPI-27 (cache hit ratio) and KPI-28 (cross-tenant probe ≥ 50 tenants, ≥ 1,000 users) measured.
- **Exit:** Every FMEA row has a passing chaos test. KPI-28 is 0 leaks, zero tolerance.

### Phase 9 — Streamlit workbench (Gate 18)
- **Deliverables:** Six tabs built in order of dependency:
  1. Tab 1: design checklist, PNG, triage console, ReAct trace, HITL queue.
  2. Tab 2: hybrid RAG lab with `k1`, `b`, `rrf_k`, `threshold` sliders.
  3. Tab 3: MCP status, JSON-RPC inspector, plugin toggles.
  4. Tab 4: Graphify subgraph, Obsidian viewer, session-state browser, token gauge.
  5. Tab 5: red-team runner and breaker controls.
  6. Tab 6: KPI scorecard, span waterfall, RAGAS gauges, k6/JMeter histogram.
- **Exit:** Each tab shows live data from the component it inspects. No tab displays a static KPI value.

### Phase 10 — Red team and governance
- **Deliverables:**
  - 100-prompt red-team suite (Gate 14) covering the five required categories.
  - Expanded corpora for KPI measurement (D-7).
  - Audit hash-chain verifier. Zero-raw-PII scan of every persisted store.
- **Exit:** Zero unexplained misses in the red-team suite. The audit chain verifies end to end.

### Phase 11 — Load, soak, and release
- **Deliverables:**
  - Reduced-scale local load runs first (D-5). Then the 50K cluster run.
  - `load_tests/k6_50k_load.js` with p50 threshold 240 (KPI-31), and the JMeter plan. Both use the arrival model from decision D.
  - Tolerance agreement between k6 and JMeter summaries, set before green is claimed.
  - 1-hour soak for KPI-36.
  - Release scorecard generated automatically for all 36 KPIs.
- **Exit:** Every KPI green on the target environment, with zero-tolerance KPIs (16, 19, 28) not re-run to get green. Otherwise release is blocked.

## 3. Immediate next steps (no blockers)

These can start now without any open decision:

1. Phase 1: restore the code into `src/`, fix the six mismatches, and add contract tests. Effort is small and removes drift risk.
2. Phase 2: expand the PII and injection corpora. Generating them is mechanical. The owner should still review the labels.
3. Phase 0 (A): choose the canonical design file. Everything else depends on it.

## 4. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Golden set (D-8) and labels are the slowest input | Blocks RAG quality, RAGAS, triage | Owner schedule agreed in Phase 0. Start with the labels needed for Phase 4 |
| No GPU | KPI-08, 09, 25 unmeasurable | Cloud GPU for measurement only, budgeted in decision C |
| Hosted LLM TTFT (KPI-25) | Generative path may fail | Extractive default keeps the 450 ms gate green. Measure before promoting generative mode |
| Reasoning-token billing (KPI-29) | Cost gate may fail | Metered run in Phase 6. Model switch is config, not code |
| Fine-tuning on tenant data (NG-3 conflict) | Privacy and contract breach if done without approval | Decision J. Fine-tune only on approved corpora. Redact PII before any training and check for memorization |
| Guide's fine-tuning script is a sketch | One training example; no validation split; `lora_alpha=16` with `r=64` gives a scale of 0.25; TRL API may differ by version | Treat it as a template. Pin library versions. Use a real dataset with a held-out split and tune alpha |
| Per-user cache keys (KPI-27) | Hit ratio may miss | Decision E. Measure before deciding on tenant-shared tier |
| 50K cluster cost and availability | Blocks KPI-31–35 | Decision D, plus a budget for the cluster run |
| Statistical resolvability | False confidence on KPI-03, KPI-23 | Dataset minimums from D-7, stated in every report |
| Blueprint conflicts (App. B) | Rework late in the project | Resolved in Phase 0, not during implementation |

## 5. Reporting rule

Every KPI report states: value, threshold, host, corpus (synthetic or real), sample size, and the phase whose exit gate produced it. A KPI not measured on the target environment is reported as **not measured**, never as pass.
