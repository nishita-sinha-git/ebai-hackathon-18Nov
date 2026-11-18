"""ToolExecutionHarness (design.md Gate 4): validate → repair → RBAC → idempotency."""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, ValidationError


class Tier(IntEnum):
    TIER_1_READ = 1
    TIER_2_COMPUTE = 2
    TIER_3_MUTATE = 3


class HarnessError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tier: Tier
    input_model: type[BaseModel]
    requires_idempotency: bool = False


# Deterministic repair rules (design.md §1.2.5). Keyed by value shape, not field
# name: any string field holding a malformed SKU or LOT is repaired. Applied only
# when the first validation fails. The whole string must match and the number
# must be in range.
REPAIRS: dict[str, tuple[re.Pattern[str], str, int]] = {
    "SKU": (re.compile(r"^\s*sku[\s-]*(\d{1,3})\s*$", re.IGNORECASE), "SKU-{:03d}", 999),
    "LOT": (re.compile(r"^\s*lot[\s-]*(\d{1,5})\s*$", re.IGNORECASE), "LOT-{:05d}", 99999),
}


@dataclass
class HarnessStats:
    total: int = 0
    first_pass_ok: int = 0  # KPI-14 numerator
    malformed: int = 0  # first validation failed
    repaired_ok: int = 0  # KPI-15 numerator (over `malformed`)
    rejected: int = 0


def normalize_value(obj: Any) -> Any:
    """NFKC-normalize every string so homoglyphs and fullwidth digits fold to ASCII."""
    if isinstance(obj, str):
        return unicodedata.normalize("NFKC", obj)
    if isinstance(obj, dict):
        return {k: normalize_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_value(v) for v in obj]
    return obj


def repair_args(args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    repaired = dict(args)
    applied: list[str] = []
    for name, value in args.items():
        if not isinstance(value, str):
            continue
        for pattern, fmt, max_value in REPAIRS.values():
            match = pattern.fullmatch(value)
            if not match:
                continue
            number = int(match.group(1))
            if number <= max_value:
                repaired[name] = fmt.format(number)
                applied.append(f"{name}: {value!r} -> {repaired[name]!r}")
            break
    return repaired, applied


@dataclass
class ToolExecutionHarness:
    caller_tier: Tier
    stats: HarnessStats = field(default_factory=HarnessStats)

    def prepare(self, spec: ToolSpec, raw_args: dict[str, Any]) -> BaseModel:
        """Return validated input, or raise HarnessError. Never executes the tool."""
        self.stats.total += 1
        normalized = normalize_value(raw_args)

        try:
            validated = spec.input_model.model_validate(normalized)
            self.stats.first_pass_ok += 1
        except ValidationError as first_error:
            self.stats.malformed += 1
            repaired, applied = repair_args(normalized)
            if not applied:
                self.stats.rejected += 1
                raise HarnessError("E_INVALID_PARAMS", _summary(first_error)) from first_error
            try:
                validated = spec.input_model.model_validate(repaired)
            except ValidationError as second_error:
                self.stats.rejected += 1
                raise HarnessError("E_INVALID_PARAMS", _summary(second_error)) from second_error
            self.stats.repaired_ok += 1

        if spec.tier > self.caller_tier:
            self.stats.rejected += 1
            raise HarnessError(
                "E_RBAC_DENIED",
                f"{spec.name} requires {spec.tier.name}; caller is {self.caller_tier.name}",
            )

        if spec.requires_idempotency:
            key = getattr(validated, "idempotency_key", None)
            if not _is_uuid4(key):
                self.stats.rejected += 1
                raise HarnessError("E_INVALID_PARAMS", "idempotency_key must be a UUIDv4")
        return validated


def _is_uuid4(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return uuid.UUID(value).version == 4
    except ValueError:
        return False


def _summary(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"])
    return f"{location}: {first['msg']}"
