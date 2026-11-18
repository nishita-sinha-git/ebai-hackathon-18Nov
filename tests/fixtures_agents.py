"""Shared fixtures: a small two-tenant corpus and a fully wired orchestrator."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from app.agents.composer import ExtractiveComposer
from app.agents.execution import ApprovalQueue, ExecutionAgent
from app.agents.generation import GenerationAgent
from app.agents.orchestrator import Orchestrator
from app.agents.retrieval import RetrievalAgent
from app.agents.triage import TriageAgent
from app.agents.verification import VerificationAgent
from app.retrieval.bm25 import Document, TenantBM25
from app.routing.admission import Bulkhead, TenantTokenBucket
from app.routing.breaker import BreakerSet
from app.routing.fallback import ModelHandle
from app.routing.registry import RouteRegistry

ACME = "tenant_acme"
GLOBEX = "tenant_globex"

ACME_CHUNKS = {
    "CHK-00000000000a": (
        "Refunds for invoices are issued within 14 days of approval. Refunds above 5000 need a second reviewer."
    ),
    "CHK-00000000000b": ("Product SKU-099 is a sealed cold-chain container. Store SKU-099 below 4 degrees Celsius."),
    "CHK-00000000000c": (
        "The recall procedure requires notifying the quality lead within 24 hours. The quality lead logs each recall."
    ),
    "CHK-00000000000d": (
        "Dosage for adult patients is set by the attending clinician. "
        "Dosage changes must be recorded in the patient chart."
    ),
}
GLOBEX_CHUNKS = {
    "CHK-00000000000e": "Globex ships pallets every Tuesday to regional depots.",
}


def build_corpus():
    index = TenantBM25()
    texts: dict[tuple[str, str], str] = {}
    for tenant, chunks in ((ACME, ACME_CHUNKS), (GLOBEX, GLOBEX_CHUNKS)):
        docs = [Document(chunk_id=cid, tenant_id=tenant, text=text) for cid, text in chunks.items()]
        index.add_tenant(docs)
        for cid, text in chunks.items():
            texts[(tenant, cid)] = text
    return index, texts


class Harness:
    """Holds the wired parts so tests can inspect them."""

    def __init__(
        self,
        *,
        models: dict[str, ModelHandle] | None = None,
        chain: tuple[str, ...] = (),
        bucket: TenantTokenBucket | None = None,
        bulkhead: Bulkhead | None = None,
        executor: Callable[[dict], Awaitable[object]] | None = None,
    ):
        index, texts = build_corpus()
        self.executed: list[dict] = []

        async def default_executor(payload: dict) -> object:
            self.executed.append(payload)
            return {"ok": True}

        self.queue = ApprovalQueue(executor or default_executor)
        self.registry = RouteRegistry()
        self.registry.publish("GENERATIVE", chain, "initial")
        self.verifier = VerificationAgent()
        self.retrieval = RetrievalAgent(index, texts)
        self.execution = ExecutionAgent(self.queue)
        self.generation = GenerationAgent(self.registry, "GENERATIVE", models or {}, BreakerSet(), self.verifier)
        self.orchestrator = Orchestrator(
            triage=TriageAgent(),
            retrieval=self.retrieval,
            execution=self.execution,
            generation=self.generation,
            verifier=self.verifier,
            composer=ExtractiveComposer(),
            bucket=bucket or TenantTokenBucket(rate_per_s=1000, burst=1000),
            bulkhead=bulkhead or Bulkhead(256),
        )


def ok_model(name: str, text: str) -> ModelHandle:
    async def call(prompt: str, timeout_s: float) -> str:
        return text

    return ModelHandle(name, 200, call)


def failing_model(name: str, exc: Exception) -> ModelHandle:
    async def call(prompt: str, timeout_s: float) -> str:
        raise exc

    return ModelHandle(name, 200, call)


def run(coro):
    return asyncio.run(coro)
