"""Verification Agent (design.md §1.9): independent support check. Its veto is final.

Lexical stand-in for the NLI claim-to-chunk check (Phase 5). A sentence is
supported only if one evidence chunk contains at least `min_overlap` of its
content tokens. The generator cannot influence this agent's model or context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..retrieval.bm25 import tokenize
from .retrieval import Evidence

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_CITATION_TAG = re.compile(r"\s*\[[A-Za-z]+-[0-9a-f]+\]")  # e.g. [pol-001], [CHK-...]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    unsupported: tuple[str, ...]


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


class VerificationAgent:
    name = "verification"

    def __init__(self, min_overlap: float = 0.6):
        if not 0.0 < min_overlap <= 1.0:
            raise ValueError("min_overlap must be in (0, 1]")
        self.min_overlap = min_overlap

    def check(self, answer: str, evidence: list[Evidence]) -> Verdict:
        chunks = [set(tokenize(e.text)) for e in evidence]
        unsupported: list[str] = []
        for sentence in split_sentences(_CITATION_TAG.sub("", answer)):
            claim = set(tokenize(sentence))
            if not claim:
                continue
            best = max((len(claim & chunk) / len(claim) for chunk in chunks), default=0.0)
            if best < self.min_overlap:
                unsupported.append(sentence)
        return Verdict(ok=not unsupported, unsupported=tuple(unsupported))
