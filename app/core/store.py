"""Durable state: hash-chained audit log and pending Tier 3 approvals (SQLite).

Each audit row stores SHA-256(previous_hash + canonical_payload), chained per tenant
(CLAUDE.md, Security and Audit). Payloads must already be redacted by the caller.
Postgres replaces this store in Phase 3 (design.md Appendix F).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings

GENESIS_HASH = "0" * 64

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        event TEXT NOT NULL,
        redacted_payload TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_mutations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        request_id TEXT NOT NULL,
        action TEXT NOT NULL,
        canonical_args TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id, idempotency_key)
    )
    """,
)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain_digest(previous_hash: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256((previous_hash + _canonical(payload)).encode("utf-8")).hexdigest()


class StateStore:
    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or settings.database_url
        if url.startswith("sqlite+aiosqlite:///") and not url.startswith("sqlite+aiosqlite:///:memory:"):
            Path(url.removeprefix("sqlite+aiosqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine: AsyncEngine = create_async_engine(url, future=True)

    async def initialize(self) -> None:
        async with self.engine.begin() as conn:
            for statement in _SCHEMA:
                await conn.execute(text(statement))

    async def write_audit(self, tenant_id: str, request_id: str, event: str, payload: dict[str, Any]) -> str:
        """Append one event to the tenant's chain and return its hash."""
        async with self.engine.begin() as conn:
            row = await conn.execute(
                text("SELECT hash FROM audit_events WHERE tenant_id = :tenant ORDER BY id DESC LIMIT 1"),
                {"tenant": tenant_id},
            )
            previous = row.scalar() or GENESIS_HASH
            digest = chain_digest(previous, payload)
            await conn.execute(
                text(
                    """
                    INSERT INTO audit_events (tenant_id, request_id, event, redacted_payload, previous_hash, hash)
                    VALUES (:tenant, :request, :event, :payload, :previous, :hash)
                    """
                ),
                {
                    "tenant": tenant_id,
                    "request": request_id,
                    "event": event,
                    "payload": _canonical(payload),
                    "previous": previous,
                    "hash": digest,
                },
            )
            return digest

    async def verify_chain(self, tenant_id: str) -> bool:
        """Recompute every hash for one tenant. False means the log was altered."""
        async with self.engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT previous_hash, redacted_payload, hash FROM audit_events "
                    "WHERE tenant_id = :tenant ORDER BY id"
                ),
                {"tenant": tenant_id},
            )
            expected_previous = GENESIS_HASH
            for previous, payload, digest in rows.fetchall():
                if previous != expected_previous:
                    return False
                if digest != hashlib.sha256((previous + payload).encode("utf-8")).hexdigest():
                    return False
                expected_previous = digest
        return True

    async def create_pending_mutation(
        self,
        tenant_id: str,
        user_id: str,
        request_id: str,
        action: str,
        canonical_args: dict[str, Any],
        idempotency_key: str,
    ) -> int:
        """Insert a PENDING_APPROVAL row. A repeated key returns the existing row id."""
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT OR IGNORE INTO pending_mutations
                        (tenant_id, user_id, request_id, action, canonical_args, idempotency_key, status)
                    VALUES (:tenant, :user, :request, :action, :args, :key, 'PENDING_APPROVAL')
                    """
                ),
                {
                    "tenant": tenant_id,
                    "user": user_id,
                    "request": request_id,
                    "action": action,
                    "args": _canonical(canonical_args),
                    "key": idempotency_key,
                },
            )
            row = await conn.execute(
                text("SELECT id FROM pending_mutations WHERE tenant_id = :tenant AND idempotency_key = :key"),
                {"tenant": tenant_id, "key": idempotency_key},
            )
            return int(row.scalar_one())
