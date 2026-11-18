# Release Notes

## Build v0.2

Working tree, uncommitted. The agent layer from design v1.2 (routing, fallback, rollback, admission, triage, subagents, HITL, verification) is integrated under `app/`.

### Changes from Build v0
- Request lifecycle: PII redaction and injection shield run before the cache and the orchestrator. Responses carry `served_by`, `degraded`, `fallback_reason`, and `hitl_ticket_id`.
- Statuses: `DONE`, `PENDING_APPROVAL`, `HALTED_BREAKER`, `HALTED_SECURITY`, `HALTED_OOS`, `ABSTAINED`, `THROTTLED` (HTTP 429 with `Retry-After`), `INVALID`.
- Tenant ids follow the contract pattern `^[a-z][a-z0-9_]{2,63}$`. Uppercase returns 422.
- Cache keys follow ADR-3 (`t:{tenant}:sc:{sha}`). Only `DONE` and `ABSTAINED` are cached. TTL is 300 s ± 45 s.
- Tier 3 requests create a durable `pending_mutations` row. A client `idempotency_key` reuses one ticket.
- PII redaction no longer leaks the local part of an obfuscated email.
- Dependencies: `sqlalchemy[asyncio]`, `mcp<2`, `nltk`, `numpy`. Dev tooling: pytest-cov, ruff, mypy, bandit.
- Pipeline: `make ci` and GitHub Actions, with a branch-coverage gate at 90 % and an installed-wheel smoke run.

### Verified evidence
- Unit, lifecycle, API, and orchestrator tests: 145 passed on Python 3.12 (`make test`).
- `make ci` on Python 3.12: lint, mypy (41 files), bandit, tests, and smoke all passed. Coverage 90.9 %.
- Smoke suite (installed wheel, run from outside the source tree): 11 of 11 passed.
- Live server (uvicorn, no Redis, no Groq key): `/health` ok, cited query `DONE`, injection `HALTED_SECURITY`, Tier 3 `PENDING_APPROVAL`, uppercase tenant `422`, `/metrics` served.
- Streamlit workbench started headless and passed its health check.

### Pending evidence (not run)
- Red-team suite (100 prompts): pending
- RAGAS golden dataset: pending
- 5K, 10K, 50K load: pending
- Soak: pending
- Live Redis and Postgres: pending. The state store is SQLite.
- Production readiness: not established

### Known gaps
- No reviewer API for approving or rejecting tickets.
- Groq generation has not been measured against TTFT (KPI-25) or cost (KPI-29).
- The triage classifier is rule-based. The ONNX classifier and its labelled set do not exist yet.
- Retrieval is BM25 only. The dense stage and cross-encoder with the 0.42 abstention threshold are not built.
