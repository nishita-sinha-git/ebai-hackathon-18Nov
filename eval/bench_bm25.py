"""KPI-07 (BM25 p95 <= 12 ms) and a correctness cross-check against rank_bm25.

Corpus: SYNTHETIC. 50,000 chunks of ~512 tokens drawn from a Zipf vocabulary,
which is the corpus size assumed in design.md ADR-01. Latency is valid only for
this host and this corpus shape, not for a real policy corpus.
"""

import random
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rank_bm25 import BM25Okapi  # noqa: E402

from app.retrieval.bm25 import BM25Index, Document, tokenize  # noqa: E402

CHUNKS = 50_000
CHUNK_TOKENS = 512
VOCAB = 20_000
QUERIES = 500
TOP_K = 50


def zipf_word(rng: random.Random, vocab: list[str]) -> str:
    return vocab[min(int(rng.paretovariate(1.1)) - 1, len(vocab) - 1)]


def main() -> None:
    rng = random.Random(1234)
    vocab = [f"term{i:05d}" for i in range(VOCAB)]
    documents = []
    for i in range(CHUNKS):
        words = [zipf_word(rng, vocab) for _ in range(CHUNK_TOKENS)]
        documents.append(Document(chunk_id=f"CHK-{i:012x}", tenant_id="tenant_001", text=" ".join(words)))
    queries = [" ".join(zipf_word(rng, vocab) for _ in range(rng.randint(2, 6))) for _ in range(QUERIES)]

    t0 = time.perf_counter()
    index = BM25Index(documents)
    print(f"built index: {len(documents)} chunks in {time.perf_counter() - t0:.1f}s")

    samples = []
    for query in queries:
        start = time.perf_counter()
        index.search(query, top_k=TOP_K)
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[int(0.95 * len(samples)) - 1]
    print(f"latency n={len(samples)} p50={p50:.2f}ms p95={p95:.2f}ms")
    print(f"KPI-07 (p95 <= 12 ms) on this host, synthetic corpus: {'PASS' if p95 <= 12 else 'FAIL'}")

    # Correctness: score-level comparison with rank_bm25 at the same k1/b.
    subset = documents[:5000]
    reference = BM25Okapi([tokenize(doc.text) for doc in subset], k1=1.25, b=0.75)
    ours = BM25Index(subset)
    worst = 0.0
    for query in queries[:50]:
        ref_scores = reference.get_scores(tokenize(query))
        ours_scores = {int(h.chunk_id.split("-")[1], 16): h.score for h in ours.search(query, top_k=TOP_K)}
        top_ref = sorted(range(len(subset)), key=lambda i: -ref_scores[i])[:TOP_K]
        for i in top_ref:
            if ref_scores[i] > 0:
                worst = max(worst, abs(ref_scores[i] - ours_scores.get(i, float("nan"))))
    print(f"max |score diff| vs rank_bm25 (k1=1.25, b=0.75, top-50 of 50 queries): {worst:.2e}")


if __name__ == "__main__":
    main()
