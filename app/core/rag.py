"""Tenant-scoped BM25 retrieval over the demo corpus.

Stage 1 of the design's hybrid pipeline only. Dense retrieval and the cross-encoder
(with the 0.42 abstention threshold) are not built yet.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.retrieval.bm25 import Document, TenantBM25


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    tenant_id: str
    text: str


DEMO_CHUNKS: tuple[Chunk, ...] = (
    Chunk("pol-001", "tenant_demo", "Batch LOT-88392 safety protocol requires temperature review before release."),
    Chunk("pol-002", "tenant_demo", "A Tier 3 mutation requires asynchronous human-in-the-loop approval."),
    Chunk("pol-003", "tenant_demo", "Obfuscated PII such infomation@example[dot]com must be rejected or redacted."),
    Chunk("pol-004", "tenant_demo", "The 50K load target requires p95 below 450 ms and p99 below 590 ms."),
)


class HybridRAG:
    def __init__(self, chunks: tuple[Chunk, ...] = DEMO_CHUNKS) -> None:
        self.chunks = tuple(chunks)
        self.index = TenantBM25()
        self._texts: dict[tuple[str, str], str] = {}
        by_tenant: dict[str, list[Document]] = defaultdict(list)
        for chunk in self.chunks:
            by_tenant[chunk.tenant_id].append(Document(chunk.chunk_id, chunk.tenant_id, chunk.text))
            self._texts[(chunk.tenant_id, chunk.chunk_id)] = chunk.text
        for docs in by_tenant.values():
            self.index.add_tenant(docs)

    def text_map(self) -> dict[tuple[str, str], str]:
        return dict(self._texts)

    def retrieve(self, tenant_id: str, query: str, limit: int = 4) -> list[tuple[Chunk, float]]:
        hits = self.index.search(tenant_id, query, top_k=min(max(limit, 1), 50))
        return [(Chunk(h.chunk_id, tenant_id, self._texts[(tenant_id, h.chunk_id)]), h.score) for h in hits]
