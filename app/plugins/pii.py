"""PII Redactor, pre-execution hook (KPI-01, KPI-02). Fail-closed on error."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MAX_INPUT_CHARS = 10_000

_DOT = r"(?:\.|\[dot\]|\(dot\))"
_AT = r"(?:@|\[at\]|\(at\))"

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "EMAIL",
        re.compile(
            rf"[A-Za-z0-9._%+-]+(?:\s?{_DOT}\s?[A-Za-z0-9._%+-]+)*\s?{_AT}\s?[A-Za-z0-9-]+(?:\s?{_DOT}\s?[A-Za-z0-9-]+)*\s?{_DOT}\s?[A-Za-z]{{2,}}",
            re.IGNORECASE,
        ),
    ),
    ("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "PHONE",
        re.compile(r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w-])"),
    ),
]


class PIIBlockedError(Exception):
    code = "E_PII_BLOCKED"


@dataclass(frozen=True)
class RedactionResult:
    text: str
    findings: dict[str, int]

    @property
    def found_any(self) -> bool:
        return bool(self.findings)


def redact(text: str) -> RedactionResult:
    """Redact PII. Raises PIIBlockedError on any failure (fail-closed)."""
    try:
        if not isinstance(text, str) or len(text) > MAX_INPUT_CHARS:
            raise ValueError("input must be a string of at most 10,000 characters")
        cleaned = unicodedata.normalize("NFKC", text)
        findings: dict[str, int] = {}
        for label, pattern in PATTERNS:
            cleaned, count = pattern.subn(f"[REDACTED_{label}]", cleaned)
            if count:
                findings[label] = count
        return RedactionResult(cleaned, findings)
    except Exception as error:  # fail-closed: any error refuses the request
        raise PIIBlockedError(str(error)) from error
