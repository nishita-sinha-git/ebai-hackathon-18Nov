"""Stage 1: sparse lexical retrieval (design.md Gate 3, KPI-07).

BM25Okapi over an inverted index of Porter-stemmed terms. One index per tenant,
so a query can only ever score its own tenant's chunks.

Postings are stored as numpy arrays per term, so scoring a query is a handful of
vector operations instead of a Python loop over every posting.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from nltk.stem import PorterStemmer

TOP_K_MAX = 50
EPSILON_IDF_FACTOR = 0.25  # rank_bm25 convention: floor negative IDF at 25% of the mean IDF

_TOKEN = re.compile(r"[a-z0-9]+")
# Function words only. Dropping them stops "for" or "the" from ranking short
# chunks above the policy chunk that matters (design.md ADR-01).
STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it of on or that the this to was were will with".split()
)
_stemmer = PorterStemmer()


@lru_cache(maxsize=65536)
def _stem(token: str) -> str:
    return _stemmer.stem(token)


def tokenize(text: str) -> list[str]:
    """NFKC, casefold, split on alphanumerics, drop stopwords, Porter-stem."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return [_stem(token) for token in _TOKEN.findall(normalized) if token not in STOPWORDS]


@dataclass(frozen=True)
class Document:
    chunk_id: str
    tenant_id: str
    text: str


@dataclass(frozen=True)
class LexicalHit:
    chunk_id: str
    score: float
    rank: int


class BM25Index:
    """Index for one tenant. Built once, queried many times."""

    def __init__(self, documents: list[Document]):
        if not documents:
            raise ValueError("cannot build an index from zero documents")
        tenants = {doc.tenant_id for doc in documents}
        if len(tenants) != 1:
            raise ValueError("BM25Index holds one tenant; build one index per tenant")
        self.tenant_id = next(iter(tenants))

        self._chunk_ids: list[str] = [doc.chunk_id for doc in documents]
        doc_len = np.zeros(len(documents), dtype=np.float64)
        raw_postings: dict[str, tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))

        for index, doc in enumerate(documents):
            tokens = tokenize(doc.text)
            doc_len[index] = len(tokens)
            for term, tf in Counter(tokens).items():
                indices, counts = raw_postings[term]
                indices.append(index)
                counts.append(tf)

        self._doc_len = doc_len
        self.doc_count = len(documents)
        self.avgdl = float(doc_len.mean()) if self.doc_count else 0.0
        self._postings: dict[str, tuple[np.ndarray, np.ndarray]] = {
            term: (np.asarray(indices, dtype=np.int64), np.asarray(counts, dtype=np.float64))
            for term, (indices, counts) in raw_postings.items()
        }
        self._idf = self._build_idf()

    def _build_idf(self) -> dict[str, float]:
        n = self.doc_count
        raw = {
            term: math.log((n - len(indices) + 0.5) / (len(indices) + 0.5))
            for term, (indices, _) in self._postings.items()
        }
        if not raw:
            return {}
        mean_idf = sum(raw.values()) / len(raw)
        floor = EPSILON_IDF_FACTOR * mean_idf
        return {term: (value if value > 0 else floor) for term, value in raw.items()}

    def search(self, query: str, top_k: int = 50, k1: float = 1.25, b: float = 0.75) -> list[LexicalHit]:
        if not 1 <= top_k <= TOP_K_MAX:
            raise ValueError(f"top_k must be in [1, {TOP_K_MAX}]")
        if k1 < 0 or not 0.0 <= b <= 1.0:
            raise ValueError("k1 must be >= 0 and b must be in [0, 1]")

        # Repeated query terms add weight, as in rank_bm25's BM25Okapi.
        scores = np.zeros(self.doc_count, dtype=np.float64)
        norm_all = k1 * (1.0 - b + b * self._doc_len / self.avgdl) if self.avgdl else np.full(self.doc_count, k1)
        for term, q_freq in Counter(tokenize(query)).items():
            posting = self._postings.get(term)
            if posting is None:
                continue
            indices, tf = posting
            contribution = self._idf[term] * tf * (k1 + 1.0) / (tf + norm_all[indices])
            scores[indices] += q_freq * contribution

        candidates = np.flatnonzero(scores)
        if candidates.size == 0:
            return []
        order = candidates[np.lexsort((candidates, -scores[candidates]))][:top_k]
        return [
            LexicalHit(self._chunk_ids[doc_index], float(scores[doc_index]), rank)
            for rank, doc_index in enumerate(order, start=1)
        ]


class TenantBM25:
    """Routes each query to its own tenant's index. Unknown tenants get nothing."""

    def __init__(self) -> None:
        self._indexes: dict[str, BM25Index] = {}

    def add_tenant(self, documents: list[Document]) -> None:
        index = BM25Index(documents)
        self._indexes[index.tenant_id] = index

    def search(self, tenant_id: str, query: str, **kwargs) -> list[LexicalHit]:
        index = self._indexes.get(tenant_id)
        if index is None:
            return []
        return index.search(query, **kwargs)
