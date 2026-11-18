"""Tenant-isolated semantic cache keys (design.md §1.2.6, ADR-03)."""

from __future__ import annotations

import hashlib
import random
import re
import unicodedata
from typing import Any

_SKU = re.compile(r"\bsku[\s-]*(\d{1,3})\b", re.IGNORECASE)
_LOT = re.compile(r"\blot[\s-]*(\d{1,5})\b", re.IGNORECASE)


class CacheTenantMismatch(Exception):
    """A cache entry's tenant does not match the request. The entry must be dropped."""

    code = "CACHE_TENANT_MISMATCH"


def norm_query(query: str) -> str:
    text = unicodedata.normalize("NFKC", query).casefold()
    text = " ".join(text.split())
    text = _SKU.sub(lambda m: f"sku-{int(m.group(1)):03d}", text)
    text = _LOT.sub(lambda m: f"lot-{int(m.group(1)):05d}", text)
    return text


def cache_key(tenant_id: str, user_id: str, query: str) -> str:
    # Validating both identifiers first means ':' cannot appear in them, so the
    # composite string below is unambiguous and a forged prefix is rejected.
    _validate(tenant_id, r"^[a-z][a-z0-9_]{2,63}$", "tenant_id")
    _validate(user_id, r"^[A-Za-z0-9_-]{1,128}$", "user_id")
    digest = hashlib.sha256(f"{tenant_id}:{user_id}:{norm_query(query)}".encode()).hexdigest()
    # ADR-3: hash-tagged key so one tenant's keys co-locate in Redis Cluster.
    return f"t:{{{tenant_id}}}:sc:{digest}"


def ttl_seconds(rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    # Non-cryptographic jitter to spread cache expiry (anti-stampede). Not a security use.
    return round(300 + rng.uniform(-45.0, 45.0))  # nosec B311


def verify_hit(entry: dict[str, Any], tenant_id: str) -> dict[str, Any]:
    """Return the entry if it belongs to the tenant; otherwise raise (fail-closed)."""
    if entry.get("tenant_id") != tenant_id:
        raise CacheTenantMismatch(f"entry tenant {entry.get('tenant_id')!r} != request tenant {tenant_id!r}")
    return entry


def _validate(value: str, pattern: str, label: str) -> None:
    if not re.fullmatch(pattern, value):
        raise ValueError(f"{label} fails contract pattern")
