"""Redis semantic cache adapter (design ADR-3, FMEA F01).

Keys come from app.cache.cache_key. Entries are re-verified on read. If Redis fails,
the cache switches off for the process and requests continue without it (fail-open
for latency only; authorization never depends on the cache).
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from app.cache import CacheTenantMismatch, cache_key, ttl_seconds, verify_hit
from app.config import settings


class SemanticCache:
    def __init__(self, redis_url: str | None = None) -> None:
        self.redis = Redis.from_url(redis_url or settings.redis_url, decode_responses=True)
        self.available = True

    async def get(self, tenant_id: str, user_id: str, query: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        key = cache_key(tenant_id, user_id, query)
        try:
            raw = await self.redis.get(key)
        except Exception:
            self.available = False
            return None
        if not raw:
            return None
        try:
            return verify_hit(json.loads(raw), tenant_id)
        except CacheTenantMismatch:
            await self.redis.delete(key)
            return None
        except (ValueError, TypeError):
            await self.redis.delete(key)
            return None

    async def set(self, tenant_id: str, user_id: str, query: str, value: dict[str, Any]) -> None:
        if not self.available:
            return
        try:
            await self.redis.setex(cache_key(tenant_id, user_id, query), ttl_seconds(), json.dumps(value))
        except Exception:
            self.available = False
