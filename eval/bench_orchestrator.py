"""Single-process throughput benchmark for the orchestrator (design.md §1.10.3).

Measures one Python process running the full agent path (admission, triage,
retrieval, composer, verifier) with in-memory models. It excludes the network,
the FastAPI layer, Redis, Postgres and any LLM call. It is NOT a KPI result
(KPI-31 to KPI-35 are measured in Appendix C). Output is labelled accordingly.

Usage: python eval/bench_orchestrator.py [--requests N] [--concurrency C]
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.orchestrator import AgentRequest  # noqa: E402
from app.routing.admission import Bulkhead, TenantTokenBucket  # noqa: E402
from tests.fixtures_agents import ACME, GLOBEX, Harness  # noqa: E402

QUERIES = [
    (ACME, "What is the refund policy for invoices?"),
    (ACME, "What is the recall procedure?"),
    (ACME, "Store SKU-099 at what temperature?"),
    (GLOBEX, "When does Globex ship pallets?"),
    (ACME, "hello"),
]


async def bench(n: int, concurrency: int) -> dict[str, float]:
    h = Harness(
        bucket=TenantTokenBucket(rate_per_s=1e9, burst=1e9),
        bulkhead=Bulkhead(concurrency),
    )
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []

    async def one(i: int) -> str:
        tenant, query = QUERIES[i % len(QUERIES)]
        async with sem:
            t0 = time.perf_counter()
            out = await h.orchestrator.handle(AgentRequest(tenant_id=tenant, user_id=f"u{i}", query=query))
            latencies.append((time.perf_counter() - t0) * 1000)
            return out.status

    start = time.perf_counter()
    statuses = await asyncio.gather(*(one(i) for i in range(n)))
    wall = time.perf_counter() - start
    latencies.sort()
    return {
        "requests": n,
        "concurrency": concurrency,
        "rps": n / wall,
        "p50_ms": statistics.median(latencies),
        "p95_ms": latencies[int(0.95 * (len(latencies) - 1))],
        "p99_ms": latencies[int(0.99 * (len(latencies) - 1))],
        "throttled": sum(1 for s in statuses if s == "THROTTLED"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=100)
    args = parser.parse_args()
    r = asyncio.run(bench(args.requests, args.concurrency))
    print("LOCAL SINGLE-PROCESS BENCHMARK, NOT A KPI RESULT")
    print(f"host: {platform.machine()} python {platform.python_version()}")
    for key, value in r.items():
        print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")


if __name__ == "__main__":
    main()
