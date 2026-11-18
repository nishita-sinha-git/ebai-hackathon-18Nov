"""Render docs/design_v1.2_workflow.png (Gate 2) at 300 DPI via Graphviz."""

from pathlib import Path

import graphviz

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

dot = graphviz.Digraph(
    "workflow_orchestration",
    graph_attr={
        "rankdir": "TB",
        "dpi": "300",
        "fontname": "DejaVu Sans",
        "fontsize": "13",
        "labelloc": "t",
        "label": "The Unreliable Assistant: Agentic Workflow Orchestration (Gate 2)",
        "pad": "0.4",
        "nodesep": "0.45",
        "ranksep": "0.6",
        "compound": "true",
        "newrank": "true",
    },
    node_attr={"fontname": "DejaVu Sans", "fontsize": "11", "style": "rounded,filled", "fillcolor": "#F4F6F8", "color": "#44546A"},
    edge_attr={"fontname": "DejaVu Sans", "fontsize": "9", "color": "#44546A"},
)

with dot.subgraph(name="cluster_ingress") as c:
    c.attr(label="Ingress & Security Layer", color="#2E75B6", style="rounded,dashed", fontcolor="#2E75B6")
    c.node("ingress", "Ingress\nNGINX / Envoy · FastAPI\nX-Tenant-ID check", shape="box")
    c.node("pii", "Pre-Exec: PII Redactor\n(fail-closed, prio 10)", shape="box", fillcolor="#FDF2E9")
    c.node("inj", "Injection Classifier\n(Pre-Exec, fail-closed, prio 20)", shape="box", fillcolor="#FDF2E9")
    c.node("cache", "Redis Semantic Cache Check\ncache:v1:{tenant}:{sha256}", shape="box", fillcolor="#E9F7EF")
    c.edges([("ingress", "pii"), ("pii", "inj"), ("inj", "cache")])

with dot.subgraph(name="cluster_triage") as c:
    c.attr(label="Triage & Subagent Routing", color="#7030A0", style="rounded,dashed", fontcolor="#7030A0")
    c.node("triage", "Triage Agent\ntemperature = 0.0 · ≤ 25 ms\n(intent, domain, risk_tier, complexity)", shape="box", fillcolor="#F3EBFA")
    c.node("direct", "Direct Response\nDIRECT_ANSWER", shape="box")
    c.node("retr", "Retrieval Subagent\nread-only · HYBRID_RAG", shape="box")
    c.node("exec", "Execution Subagent\nTIER_2 / TIER_3 · TRANSACTION_EXEC", shape="box")
    c.node("reject", "Refusal\nREJECT_OOS", shape="box", fillcolor="#FBE5E5")
    c.node("audit", "Audit (Verification) Subagent\nNLI claim check · RAGAS triad · OTel", shape="box", fillcolor="#F3EBFA")

with dot.subgraph(name="cluster_mcp") as c:
    c.attr(label="MCP JSON-RPC 2.0 & 3-Stage Hybrid RAG Pipeline", color="#C55A11", style="rounded,dashed", fontcolor="#C55A11")
    c.node("rag", "mcp-hybrid-rag  (stdio)\nhybrid_retrieve · rag://chunks/{id}", shape="component", fillcolor="#FDF2E9")
    c.node("bm25", "Stage 1: BM25Okapi\nk1=1.25 · b=0.75 · top_k=50\n< 12 ms", shape="box")
    c.node("bi", "Stage 2: Bi-Encoder\nbge-large ONNX + FAISS HNSW\nefSearch=64 · top_k=50 · < 25 ms", shape="box")
    c.node("rrf", "RRF Fusion\nk=60 · dedup chunk_hash\npool=40 · < 3 ms", shape="box")
    c.node("ce", "Stage 3: Cross-Encoder\nbge-reranker-v2-m3 ONNX FP16\nbatch=32 · sigmoid > 0.42 · < 45 ms", shape="box")
    c.node("graph", "mcp-graphify-vault  (stdio)\ngraph_subgraph · depth ≤ 2\n~320 tokens", shape="component", fillcolor="#FDF2E9")
    c.node("state", "mcp-state-store  (Streamable HTTP)\nsession_facts · pending_approvals · audit", shape="component", fillcolor="#FDF2E9")
    c.edges([("rag", "bm25"), ("rag", "bi"), ("bm25", "rrf"), ("bi", "rrf"), ("rrf", "ce")])

with dot.subgraph(name="cluster_control") as c:
    c.attr(label="Control Plane & Egress", color="#375623", style="rounded,dashed", fontcolor="#375623")
    c.node("react", "Bounded ReAct Loop\nmax_steps = 5 · per_step 120 ms\nloop budget 450 ms", shape="box", fillcolor="#E9F7EF")
    c.node("cb", "Circuit Breaker\n(tool, args_hash) ≥ 2 fails\n→ HALTED_BREAKER", shape="diamond", fillcolor="#FBE5E5")
    c.node("hitl", "Tier 3 HITL Gate\nPENDING_APPROVAL in Postgres", shape="box", fillcolor="#FBE5E5")
    c.node("post", "Post-Execution\nRAGAS auditor · OTel export", shape="box", fillcolor="#E9F7EF")
    c.node("resp", "Response\nANSWERED · ABSTAINED · HALTED_BREAKER\nPENDING_APPROVAL · REJECTED", shape="box")
    c.edges([("react", "cb"), ("cb", "hitl"), ("hitl", "post"), ("post", "resp")])

dot.node("redis", "Redis Cluster", shape="cylinder", fillcolor="#E9F7EF")
dot.node("pg", "PostgreSQL + pgvector", shape="cylinder", fillcolor="#E9F7EF")
dot.node("otel", "OTel Collector → Prometheus · Tempo · Grafana", shape="box", style="rounded,dashed")

# Ingress → cache hit short-circuit
dot.edge("cache", "resp", label="HIT (tenant-verified)", color="#375623", fontcolor="#375623", constraint="false")
dot.edge("cache", "triage", label="MISS")

# Triage branches
dot.edge("triage", "direct", label="DIRECT_ANSWER")
dot.edge("triage", "retr", label="HYBRID_RAG")
dot.edge("triage", "exec", label="TRANSACTION_EXEC")
dot.edge("triage", "reject", label="REJECT_OOS")

# Retrieval → MCP
dot.edge("retr", "rag", label="JSON-RPC tools/call")
dot.edge("retr", "graph", label="JSON-RPC tools/call")
dot.edge("exec", "state", label="JSON-RPC tools/call")
dot.edge("ce", "react", label="top-4 verified chunks\n+ confidence")
dot.edge("graph", "react", label="minimal subgraph")
dot.edge("state", "hitl", label="TIER_3 write\nPENDING_APPROVAL", style="dashed")

dot.edge("direct", "react", label="grounded")
dot.edge("exec", "react", label="validated\nargs")
dot.edge("react", "audit", label="draft answer", constraint="false")
dot.edge("audit", "post", label="vetoes ungrounded drafts")
dot.edge("reject", "resp", label="refusal")
dot.edge("post", "otel", style="dashed", label="spans")

# Persistence
dot.edge("cache", "redis", style="dotted", arrowhead="none")
dot.edge("state", "pg", style="dotted", arrowhead="none")

out_base = DOCS / "design_v1.2_workflow"
dot.render(str(out_base), format="png", cleanup=True)
dot.save(str(out_base) + ".dot")
print(f"wrote {out_base}.png and {out_base}.dot")
