"""SHA-256 hash-chained audit log (Gate 13). In-memory; Postgres is the target store.

entry.hash = sha256(prev_hash + canonical_json({tenant_id, event}))
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .plugins.pii import redact

GENESIS = "0" * 64


class AuditIntegrityError(Exception):
    pass


class PIIInAuditPayloadError(Exception):
    """Audit payloads must never contain raw PII (zero raw PII persistence)."""


@dataclass(frozen=True)
class AuditEntry:
    tenant_id: str
    event: dict[str, Any]
    prev_hash: str
    hash: str


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, tenant_id: str, event: dict[str, Any]) -> str:
    return hashlib.sha256(
        (prev_hash + _canonical({"tenant_id": tenant_id, "event": event})).encode("utf-8")
    ).hexdigest()


class AuditChain:
    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    @property
    def head(self) -> str:
        return self._entries[-1].hash if self._entries else GENESIS

    def append(self, tenant_id: str, event: dict[str, Any]) -> AuditEntry:
        if redact(_canonical(event)).found_any:
            raise PIIInAuditPayloadError("audit event contains PII-shaped content; redact before logging")
        prev = self.head
        entry = AuditEntry(tenant_id, event, prev, _digest(prev, tenant_id, event))
        self._entries.append(entry)
        return entry

    def verify(self) -> None:
        prev = GENESIS
        for index, entry in enumerate(self._entries):
            if entry.prev_hash != prev or _digest(prev, entry.tenant_id, entry.event) != entry.hash:
                raise AuditIntegrityError(f"chain broken at entry {index}")
            prev = entry.hash

    def tamper_for_test(self, index: int, event: dict[str, Any]) -> None:
        """Test-only hook: rewrite an entry without fixing its hash."""
        old = self._entries[index]
        self._entries[index] = AuditEntry(old.tenant_id, event, old.prev_hash, old.hash)

    def __len__(self) -> int:
        return len(self._entries)
