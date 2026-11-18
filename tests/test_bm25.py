"""Stage 1 BM25 (Gate 3): ranking, stemming, tenant isolation, parameter bounds."""

import pytest

from app.retrieval.bm25 import Document, TenantBM25, tokenize


def docs(tenant, rows):
    return [Document(chunk_id=f"CHK-{i:012x}", tenant_id=tenant, text=text) for i, text in enumerate(rows)]


CORPUS = [
    "Enterprise orders may be returned within 14 days of delivery.",
    "Shipping takes three business days for standard orders.",
    "Batch LOT-88392 must pass the safety protocol before release.",
    "Returns for opened items are accepted within 30 days.",
    "Quarterly revenue reports are due on the first Monday.",
]


@pytest.fixture
def index():
    engine = TenantBM25()
    engine.add_tenant(docs("tenant_001", CORPUS))
    return engine


def test_stemming_matches_inflections():
    assert tokenize("returns") == tokenize("return")
    assert tokenize("shipping") == tokenize("ship")


def test_return_window_query_ranks_policy_chunk_first(index):
    hits = index.search("tenant_001", "return window for orders")
    assert hits[0].chunk_id == f"CHK-{0:012x}"
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


def test_exact_identifier_is_recalled(index):
    hits = index.search("tenant_001", "LOT-88392")
    assert hits[0].chunk_id == f"CHK-{2:012x}"


def test_unknown_terms_return_nothing(index):
    assert index.search("tenant_001", "zeppelin") == []


def test_unknown_tenant_gets_nothing(index):
    assert index.search("tenant_999", "return window") == []


def test_tenant_isolation_never_leaks_other_tenant_chunks():
    engine = TenantBM25()
    engine.add_tenant(docs("tenant_001", ["secret account balance for alpha"]))
    engine.add_tenant(docs("tenant_002", ["public product catalog for beta"]))
    hits = engine.search("tenant_001", "account balance catalog")
    assert all(h.chunk_id == f"CHK-{0:012x}" for h in hits)
    # tenant_002 owns only its own chunk; alpha's text must not be reachable from beta
    beta_hits = engine.search("tenant_002", "alpha account")
    assert beta_hits == []


def test_index_refuses_mixed_tenants():
    with pytest.raises(ValueError):
        from app.retrieval.bm25 import BM25Index

        BM25Index(docs("tenant_001", ["a"]) + docs("tenant_002", ["b"]))


def test_top_k_is_capped_at_50(index):
    with pytest.raises(ValueError):
        index.search("tenant_001", "orders", top_k=51)


def test_k1_and_b_are_bounded():
    engine = TenantBM25()
    engine.add_tenant(docs("tenant_001", CORPUS))
    with pytest.raises(ValueError):
        engine.search("tenant_001", "orders", b=1.5)
    with pytest.raises(ValueError):
        engine.search("tenant_001", "orders", k1=-1)


def test_b_changes_length_normalization():
    engine = TenantBM25()
    engine.add_tenant(
        docs(
            "tenant_001",
            [
                "orders " * 20 + "filler words here for length",
                "orders short",
            ],
        )
    )
    strict = engine.search("tenant_001", "orders", b=1.0)
    loose = engine.search("tenant_001", "orders", b=0.0)
    assert strict[0].chunk_id != loose[0].chunk_id or strict[0].score != pytest.approx(loose[0].score)


def test_scores_are_descending_and_deterministic(index):
    first = index.search("tenant_001", "orders returns days")
    second = index.search("tenant_001", "orders returns days")
    assert first == second
    assert all(a.score >= b.score for a, b in zip(first, first[1:], strict=False))
