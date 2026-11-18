"""Guardrail helpers backed by the fail-closed hooks.

The implementations are the hooks in app/plugins/pii.py and app/plugins/injection.py.
"""

from __future__ import annotations

import unicodedata

from app.harness import REPAIRS
from app.plugins import injection, pii


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def redact_pii(text: str) -> tuple[str, bool]:
    """Return (redacted_text, found_any). Raises PIIBlockedError on failure (fail-closed)."""
    result = pii.redact(text)
    return result.text, result.found_any


def injection_detected(text: str) -> bool:
    return injection.inspect(text).blocked


def repair_argument(value: str) -> str:
    """Deterministic repair (design §1.2.6 step 2): SKU99 -> SKU-099, lot 44 -> LOT-00044."""
    for pattern, template, max_value in REPAIRS.values():
        match = pattern.fullmatch(value)
        if match and int(match.group(1)) <= max_value:
            return template.format(int(match.group(1)))
    return value.strip()
