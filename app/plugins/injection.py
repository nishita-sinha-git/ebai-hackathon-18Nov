"""Injection Shield, pre-execution hook (KPI-03). Rule layer only.

The ML classifier layer from Gate 2 is NOT implemented yet. This layer is
deterministic and catches the phrasings in the baseline failure report.
It will miss paraphrases a model would catch, so KPI-03 must not be claimed
from this layer alone.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Zero-width and bidi control characters are removed before matching.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\ufeff]")

RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_previous",
        re.compile(
            r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|prompts?|rules?)\b"
        ),
    ),
    (
        "reveal_prompt",
        re.compile(
            r"\b(?:reveal|print|show|repeat|output|dump)\s+(?:your\s+|the\s+)?(?:system\s+)?(?:prompt|instructions)\b"
        ),
    ),
    (
        "persona_override",
        re.compile(r"\byou\s+are\s+now\b|\bact\s+as\s+(?:an?\s+)?(?:unrestricted|jailbroken|unfiltered)\b"),
    ),
    ("mode_switch", re.compile(r"\b(?:developer|god|jailbreak|dan)\s+mode\b")),
    (
        "hypothetical_bypass",
        re.compile(r"\bhypothetical\w*\b.*\b(?:bypass|circumvent|evade|break)\w*\b"),
    ),
    (
        "pretend_no_rules",
        re.compile(r"\bpretend\b.*\b(?:no|without)\s+(?:rules|restrictions|limits)\b"),
    ),
    (
        "role_tag",
        re.compile(
            r"<\s*/?\s*(?:system|assistant|developer)\s*>|^\s*(?:system|assistant)\s*:",
            re.MULTILINE,
        ),
    ),
    ("new_instructions", re.compile(r"\bnew\s+instructions?\s*:")),
]


class InjectionBlockedError(Exception):
    code = "E_INJECTION_BLOCKED"


@dataclass(frozen=True)
class InjectionVerdict:
    blocked: bool
    rule: str | None


def normalize_for_matching(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def inspect(text: str) -> InjectionVerdict:
    """Return a verdict. Any internal error blocks (fail-closed)."""
    try:
        normalized = normalize_for_matching(text)
        for rule_name, pattern in RULES:
            if pattern.search(normalized):
                return InjectionVerdict(blocked=True, rule=rule_name)
        return InjectionVerdict(blocked=False, rule=None)
    except Exception:
        return InjectionVerdict(blocked=True, rule="internal_error")


def inspect_chunk(chunk_text: str) -> InjectionVerdict:
    """Same rules applied to retrieved chunks (indirect injection, Gate 14 class 2)."""
    return inspect(chunk_text)
