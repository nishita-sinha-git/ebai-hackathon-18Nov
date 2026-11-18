# Golden Dataset Schema (D-8, owned by the project owner)

Target: 250 questions, stratified as below. Integer counts were chosen so the
total is exactly 250. The 15% and 25% strata are not whole numbers of 250
otherwise.

| Stratum | Count | Share |
|---|---:|---:|
| Factoid | 63 | 25% |
| Complex Reasoning | 62 | 25% |
| Multi-Hop | 50 | 20% |
| Summary | 38 | 15% |
| Negative / Unanswerable | 37 | 15% |

Note: at 37 negatives, KPI-23 (≥ 98%) passes only with 37/37. Adding negatives
(D-7 asks for ≥ 200 in a separate negative set) makes that gate meaningful.

## One record per line in `eval/golden.jsonl`

```json
{
  "id": "GQ-0001",
  "stratum": "FACTOID | COMPLEX | MULTI_HOP | SUMMARY | NEGATIVE",
  "tenant_id": "tenant_001",
  "query": "What is the return window for enterprise orders?",
  "gold_chunk_ids": ["CHK-0123456789ab"],
  "gold_answer": "Enterprise orders may be returned within 14 days.",
  "must_contain": ["14 days"],
  "entities": ["enterprise orders"],
  "risk_tier": "LOW",
  "expected_route": "DIRECT_ANSWER | HYBRID_RAG | TRANSACTION_EXEC | REJECT_OOS",
  "source_note": "policy RET-001 v3, section 2"
}
```

Rules:
- `gold_chunk_ids` must reference chunks that exist in the indexed corpus.
- `must_contain` is a list of strings that must appear in an extractive answer.
- `NEGATIVE` records have `gold_chunk_ids: []` and `gold_answer: null`. The expected behaviour is abstention.
- Every record needs a `source_note` that a reviewer can check against the vault.
