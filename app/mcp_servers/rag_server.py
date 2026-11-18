"""hybrid-rag MCP server (design.md §1.2.2). Exposes tenant-scoped BM25 retrieval over stdio.

Run: python -m app.mcp_servers.rag_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.core.rag import HybridRAG

server = FastMCP("hybrid-rag")
_rag = HybridRAG()


@server.tool()
def hybrid_retrieve(tenant_id: str, query: str, limit: int = 4) -> list[dict]:
    """Return the top chunks for a tenant's query, with their BM25 scores."""
    hits = _rag.retrieve(tenant_id, query, limit)
    return [{"chunk_id": chunk.chunk_id, "text": chunk.text, "score": score} for chunk, score in hits]


if __name__ == "__main__":
    server.run(transport="stdio")
