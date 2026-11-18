# The Unreliable Assistant — Build v0.2

Enterprise MCP agent: tenant-isolated bounded routing, PII and injection guardrails
(fail-closed), tenant-scoped Redis cache and SQLite audit, BM25 retrieval, Groq generation
with a router, fallback and rollback, and a six-tab Streamlit workbench.


## Source documents

- `Unreliable_Assistant_MidCheck_Enterprise_Blueprint_v2.docx` (not in this repo)
- `Post_Hardening_Engineering_Audit_Report.docx` (not in this repo)
- `SLM_Comprehensive_Guide.docx` (not in this repo)
- [design.md](design.md): design v1.2, the source of truth for contracts and thresholds
- [CLAUDE.md](CLAUDE.md): governance rules for load testing and failure handling

## Setup

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # optional: set groq_api_key to enable generation
```

## Run

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
.venv/bin/streamlit run app.py --server.port 8501
```

Groq is read from `.env` as `groq_api_key`. Without a key the assistant answers
extractively, with citations, and the generative route degrades to that answer.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | status, Groq configured, cache available |
| GET | `/metrics` | Prometheus text |
| POST | `/api/v1/agent/query` | JSON answer. `429` with `Retry-After` when throttled |
| POST | `/api/v1/agent/stream` | the same answer as a chunked text stream |

`tenant_id` must match `^[a-z][a-z0-9_]{2,63}$`. An uppercase tenant returns `422`.
`idempotency_key`, if sent, must be a UUIDv4. A retried Tier 3 request reuses one approval ticket.

Statuses: `DONE`, `PENDING_APPROVAL`, `HALTED_BREAKER`, `HALTED_SECURITY`, `HALTED_OOS`,
`ABSTAINED`, `THROTTLED`, `INVALID`.

## Test and quality gates

```bash
make ci            # lint, format check, mypy, bandit, tests with coverage gate, installed-wheel smoke
make test          # pytest with branch coverage (fails under 90 %)
make smoke-installed
```

| Target | What it does |
|---|---|
| `make lint` | ruff lint and format check |
| `make typecheck` | mypy over `app/` |
| `make security` | bandit, medium and high severity fail |
| `make test` | pytest with branch coverage and the 90 % gate |
| `make build` | wheel into `dist/` |
| `make smoke-installed` | installs the wheel in a clean venv and runs `python -m app.smoke` from outside the source tree |
| `make ci` | all of the above, in pipeline order |

The smoke suite runs eleven scenarios: eight through the orchestrator (cited answer, tenant
isolation, out-of-scope rejection, Tier 3 approval gating, SKU repair, generative-outage
degradation, tenant rate limiting, idempotent retry) and three through the HTTP API (health,
cited query, injection halt).

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs lint and types on Python 3.12, the
test matrix on Python 3.11 to 3.14 with the coverage gate, and the package smoke job.

## Load (not yet run)

```bash
k6 run -e TIER=5k load_tests/k6_progressive.js
k6 run -e TIER=10k load_tests/k6_progressive.js
k6 run -e TIER=50k load_tests/k6_progressive.js
```

No 5K, 10K, 50K, RAGAS, or red-team result makes the system production-ready until recorded
evidence passes the gates in [design.md](design.md). See [release_notes.md](release_notes.md).

## Layout

```
app/
  main.py              FastAPI app
  config.py            settings (model keys, design deadlines, admission, cache TTL)
  models.py            request and response contracts
  smoke.py             installed-wheel smoke suite
  core/                agent (request lifecycle), rag, cache, state (SQLite audit), llm (Groq)
  agents/              triage, retrieval, execution (HITL), verification, composer, generation, orchestrator
  routing/             per-model breaker, fallback, versioned registry (rollback), admission
  plugins/             PII redactor, injection shield, guardrail wrapper
  retrieval/bm25.py    tenant-scoped BM25
  harness.py           tool validation, repair, RBAC, idempotency
  breaker.py, loop.py, audit.py, cache.py, contracts.py
  mcp_servers/         hybrid-rag MCP server (stdio)
tests/                 unit, lifecycle, API, orchestrator, smoke
eval/                  benchmarks (not run by the pipeline)
scripts/               smoke_installed.sh
docs/                  workflow diagram and KPI status
```
