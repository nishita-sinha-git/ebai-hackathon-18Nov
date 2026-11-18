"""Baseline failure 1: the infinite SKU retry loop. Gate 4 harness, Gate 9 breaker."""

import uuid

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.breaker import BreakerOpenError, IdempotentCircuitBreaker
from app.contracts import StateMutateInput
from app.harness import HarnessError, Tier, ToolExecutionHarness, ToolSpec


class InventoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: str = Field(pattern=r"^SKU-\d{3}$")


INVENTORY = ToolSpec("inventory_lookup", Tier.TIER_1_READ, InventoryInput)
MUTATE = ToolSpec("form_submit", Tier.TIER_3_MUTATE, StateMutateInput, requires_idempotency=True)


def test_sku99_is_repaired_to_sku_099():
    harness = ToolExecutionHarness(Tier.TIER_1_READ)
    validated = harness.prepare(INVENTORY, {"item_id": "SKU99"})
    assert validated.item_id == "SKU-099"
    assert harness.stats.malformed == 1
    assert harness.stats.repaired_ok == 1


def test_valid_args_pass_first_time_without_repair():
    harness = ToolExecutionHarness(Tier.TIER_1_READ)
    harness.prepare(INVENTORY, {"item_id": "SKU-101"})
    assert harness.stats.first_pass_ok == 1
    assert harness.stats.malformed == 0


def test_unrepairable_args_are_rejected_not_guessed():
    harness = ToolExecutionHarness(Tier.TIER_1_READ)
    with pytest.raises(HarnessError) as exc:
        harness.prepare(INVENTORY, {"item_id": "widget"})
    assert exc.value.code == "E_INVALID_PARAMS"


def test_out_of_range_sku_is_not_repaired():
    harness = ToolExecutionHarness(Tier.TIER_1_READ)
    with pytest.raises(HarnessError):
        harness.prepare(INVENTORY, {"item_id": "SKU1234"})


def test_fullwidth_homoglyph_is_normalized_before_validation():
    harness = ToolExecutionHarness(Tier.TIER_1_READ)
    validated = harness.prepare(INVENTORY, {"item_id": "ＳＫＵ-101"})
    assert validated.item_id == "SKU-101"


def test_tier3_mutation_denied_for_read_caller():
    harness = ToolExecutionHarness(Tier.TIER_1_READ)
    args = {
        "tenant_id": "tenant_001",
        "user_id": "user_7",
        "idempotency_key": str(uuid.uuid4()),
        "risk_tier": "HIGH",
        "action": "form_submit",
    }
    with pytest.raises(HarnessError) as exc:
        harness.prepare(MUTATE, args)
    assert exc.value.code == "E_RBAC_DENIED"


def test_mutation_requires_uuid4_idempotency_key():
    harness = ToolExecutionHarness(Tier.TIER_3_MUTATE)
    args = {
        "tenant_id": "tenant_001",
        "user_id": "user_7",
        "idempotency_key": "not-a-uuid",
        "risk_tier": "LOW",
        "action": "form_submit",
    }
    with pytest.raises(HarnessError) as exc:
        harness.prepare(MUTATE, args)
    assert exc.value.code == "E_INVALID_PARAMS"


def test_breaker_halts_repeated_identical_failure():
    breaker = IdempotentCircuitBreaker()
    args = {"item_id": "SKU-099"}
    key = breaker.check("inventory_lookup", args)
    breaker.record(key, ok=False)
    key = breaker.check("inventory_lookup", args)
    breaker.record(key, ok=False)
    with pytest.raises(BreakerOpenError):
        breaker.check("inventory_lookup", args)


def test_breaker_refuses_third_identical_call_even_after_success():
    breaker = IdempotentCircuitBreaker()
    args = {"item_id": "SKU-099"}
    breaker.record(breaker.check("inventory_lookup", args), ok=True)
    breaker.record(breaker.check("inventory_lookup", args), ok=True)
    with pytest.raises(BreakerOpenError):
        breaker.check("inventory_lookup", args)


def test_breaker_does_not_affect_different_args():
    breaker = IdempotentCircuitBreaker()
    for _ in range(2):
        breaker.record(breaker.check("inventory_lookup", {"item_id": "SKU-001"}), ok=False)
    breaker.check("inventory_lookup", {"item_id": "SKU-002"})  # must not raise
