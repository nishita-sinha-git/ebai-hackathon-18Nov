"""Extractive Composer (design.md ADR-6): the default, deterministic answer path.

Quotes verbatim sentences from the retrieved evidence and cites their chunk IDs.
No LLM is involved, so it cannot invent content and it is the terminal fallback
for every generative chain.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..retrieval.bm25 import tokenize
from .retrieval import Evidence
from .verification import split_sentences


@dataclass(frozen=True)
class Composed:
    answer: str
    citations: tuple[str, ...]


class ExtractiveComposer:
    def __init__(self, max_sentences: int = 2):
        self.max_sentences = max_sentences

    def compose(self, query: str, evidence: list[Evidence]) -> Composed:
        if not evidence:
            return Composed(answer="", citations=())
        query_terms = set(tokenize(query))
        scored: list[tuple[int, int, str, str]] = []  # (-overlap, rank, sentence, chunk_id)
        for item in evidence:
            for sentence in split_sentences(item.text):
                overlap = len(query_terms & set(tokenize(sentence)))
                if overlap > 0:
                    scored.append((-overlap, item.rank, sentence, item.chunk_id))
        if not scored:
            # Retrieval matched, but no sentence shares a word with the query.
            # Fall back to the top chunk's first sentence.
            top = evidence[0]
            first = split_sentences(top.text)[0]
            scored = [(0, top.rank, first, top.chunk_id)]
        scored.sort()
        chosen = scored[: self.max_sentences]
        answer = " ".join(f"{sentence} [{chunk_id}]" for _, _, sentence, chunk_id in chosen)
        citations = tuple(dict.fromkeys(chunk_id for _, _, _, chunk_id in chosen))
        return Composed(answer=answer, citations=citations)
