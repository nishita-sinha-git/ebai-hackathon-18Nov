"""Retrieval Agent (design.md §1.9): read-only evidence lookup.

Stage 1 (BM25) only. Stages 2–3 (bi-encoder, RRF, cross-encoder with the 0.42
abstention threshold) belong to Phase 4. Until then, abstention means "no
lexical match", not "score below 0.42".
"""

from __future__ import annotations

from dataclasses import dataclass

from ..breaker import IdempotentCircuitBreaker
from ..harness import Tier, ToolSpec
from ..retrieval.bm25 import TenantBM25
from .tools import RetrieveArgs, Subagent, ToolBinding


@dataclass(frozen=True)
class Evidence:
    chunk_id: str
    text: str
    score: float
    rank: int


class RetrievalAgent(Subagent):
    name = "retrieval"

    def __init__(self, index: TenantBM25, texts: dict[tuple[str, str], str]):
        self._index = index
        self._texts = texts  # (tenant_id, chunk_id) -> text. Keyed by tenant so lookups cannot cross tenants.
        binding = ToolBinding(
            spec=ToolSpec(name="hybrid_retrieve", tier=Tier.TIER_1_READ, input_model=RetrieveArgs),
            impl=self._search,
        )
        super().__init__(Tier.TIER_1_READ, [binding])

    async def _search(self, args: RetrieveArgs) -> list[Evidence]:
        hits = self._index.search(args.tenant_id, args.query, top_k=args.top_k)
        return [
            Evidence(hit.chunk_id, self._texts[(args.tenant_id, hit.chunk_id)], hit.score, hit.rank) for hit in hits
        ]

    async def retrieve(self, tenant_id: str, query: str, breaker: IdempotentCircuitBreaker) -> list[Evidence]:
        return await self.call_tool("hybrid_retrieve", {"tenant_id": tenant_id, "query": query}, breaker)
