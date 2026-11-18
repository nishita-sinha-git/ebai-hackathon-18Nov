"""Model router, fallback, rollback and admission (design.md §1.8)."""

import asyncio

import pytest

from app.routing.admission import Bulkhead, TenantTokenBucket
from app.routing.breaker import BreakerSet, BreakerState, ModelBreaker
from app.routing.errors import NoPreviousVersion, RateLimited, UpstreamUnavailable
from app.routing.fallback import ModelHandle, call_with_fallback
from app.routing.registry import RouteRegistry


class FakeClock:
    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


# ---------- breaker ----------


def test_breaker_opens_after_threshold_and_blocks():
    clock = FakeClock()
    b = ModelBreaker(failure_threshold=3, cooldown_s=30, clock=clock)
    for _ in range(2):
        assert b.allow()
        b.on_failure()
    assert b.state is BreakerState.CLOSED
    assert b.allow()
    b.on_failure()
    assert b.state is BreakerState.OPEN
    assert not b.allow()


def test_breaker_half_open_admits_one_probe_then_closes_on_success():
    clock = FakeClock()
    b = ModelBreaker(failure_threshold=1, cooldown_s=30, clock=clock)
    assert b.allow()
    b.on_failure()
    clock.advance(29.9)
    assert not b.allow()
    clock.advance(0.2)
    assert b.state is BreakerState.HALF_OPEN
    assert b.allow()  # the single probe
    assert not b.allow()  # no second probe while it is in flight
    b.on_success()
    assert b.state is BreakerState.CLOSED
    assert b.allow()


def test_breaker_probe_failure_reopens_for_another_cooldown():
    clock = FakeClock()
    b = ModelBreaker(failure_threshold=1, cooldown_s=10, clock=clock)
    b.allow()
    b.on_failure()
    clock.advance(10)
    assert b.allow()
    b.on_failure()
    assert b.state is BreakerState.OPEN
    clock.advance(9)
    assert not b.allow()


def test_breakers_are_per_model():
    bs = BreakerSet(failure_threshold=1)
    bs.get("a").allow()
    bs.get("a").on_failure()
    assert bs.get("a").state is BreakerState.OPEN
    assert bs.get("b").state is BreakerState.CLOSED


# ---------- fallback ----------


def _ok(text):
    async def call(prompt, timeout_s):
        return text

    return call


def _raises(exc):
    async def call(prompt, timeout_s):
        raise exc

    return call


def _counting(text, counter):
    async def call(prompt, timeout_s):
        counter.append(prompt)
        return text

    return call


def test_primary_success_is_not_degraded():
    out = asyncio.run(
        call_with_fallback(
            [ModelHandle("primary", 100, _ok("gen answer"))],
            "q",
            deadline_ms=450,
            breakers=BreakerSet(),
            terminal=lambda: "extractive answer",
        )
    )
    assert out.served_by == "primary"
    assert not out.degraded
    assert out.fallback_reason is None


def test_secondary_serves_when_primary_rate_limited():
    out = asyncio.run(
        call_with_fallback(
            [
                ModelHandle("primary", 100, _raises(RateLimited(500))),
                ModelHandle("secondary", 100, _ok("s")),
            ],
            "q",
            deadline_ms=450,
            breakers=BreakerSet(),
            terminal=lambda: "e",
        )
    )
    assert out.served_by == "secondary"
    assert out.degraded
    assert "primary:failed(RateLimited)" in out.fallback_reason


def test_terminal_serves_when_every_model_fails():
    out = asyncio.run(
        call_with_fallback(
            [
                ModelHandle("a", 100, _raises(UpstreamUnavailable())),
                ModelHandle("b", 100, _raises(UpstreamUnavailable())),
            ],
            "q",
            deadline_ms=450,
            breakers=BreakerSet(),
            terminal=lambda: "extractive",
        )
    )
    assert out.served_by == "extractive"
    assert out.answer == "extractive"
    assert out.degraded
    assert [a.outcome for a in out.attempts] == ["failed", "failed"]


def test_open_breaker_model_is_skipped_without_being_called():
    breakers = BreakerSet(failure_threshold=1, cooldown_s=60)
    breakers.get("primary").allow()
    breakers.get("primary").on_failure()  # primary now OPEN
    calls: list[str] = []
    out = asyncio.run(
        call_with_fallback(
            [
                ModelHandle("primary", 100, _counting("p", calls)),
                ModelHandle("secondary", 100, _ok("s")),
            ],
            "q",
            deadline_ms=450,
            breakers=breakers,
            terminal=lambda: "e",
        )
    )
    assert calls == []
    assert out.served_by == "secondary"
    assert out.attempts[0].outcome == "skipped_breaker"


def test_slow_model_times_out_and_falls_back():
    async def slow(prompt, timeout_s):
        await asyncio.sleep(1.0)
        return "too late"

    out = asyncio.run(
        call_with_fallback(
            [ModelHandle("slow", 30, slow), ModelHandle("fast", 100, _ok("fast"))],
            "q",
            deadline_ms=450,
            breakers=BreakerSet(),
            terminal=lambda: "e",
        )
    )
    assert out.served_by == "fast"
    assert out.attempts[0].outcome == "failed"
    assert out.attempts[0].error == "TimeoutError"


def test_exhausted_deadline_goes_straight_to_terminal():
    clock = FakeClock()
    out = asyncio.run(
        call_with_fallback(
            [ModelHandle("primary", 100, _ok("p"))],
            "q",
            deadline_ms=0,
            breakers=BreakerSet(),
            terminal=lambda: "e",
            clock=clock,
        )
    )
    assert out.served_by == "extractive"
    assert out.attempts[0].outcome == "skipped_deadline"


def test_non_retryable_error_propagates_and_frees_half_open_probe():
    clock = FakeClock()
    breakers = BreakerSet(failure_threshold=1, cooldown_s=5, clock=clock)
    breakers.get("m").allow()
    breakers.get("m").on_failure()
    clock.advance(5)  # HALF_OPEN

    async def buggy(prompt, timeout_s):
        raise ValueError("bad request shape")

    with pytest.raises(ValueError):
        asyncio.run(
            call_with_fallback(
                [ModelHandle("m", 100, buggy)],
                "q",
                deadline_ms=450,
                breakers=breakers,
                terminal=lambda: "e",
                clock=clock,
            )
        )
    # The probe slot must be free again, so the next attempt may probe.
    assert breakers.get("m").allow()


# ---------- registry: versions, rollback, kill switch ----------


def test_publish_then_rollback_moves_pointer_back_and_is_audited():
    reg = RouteRegistry(clock=lambda: 0.0)
    reg.publish("GENERATIVE", ("hosted:gpt-5-mini",), "initial")
    reg.publish("GENERATIVE", ("hosted:gpt-5", "hosted:gpt-5-mini"), "experiment")
    assert reg.active("GENERATIVE").version == 2
    back = reg.rollback("GENERATIVE", "faithfulness below 0.90")
    assert back.version == 1
    assert reg.chain("GENERATIVE") == ("hosted:gpt-5-mini",)
    assert reg.audit[-1]["event"] == "rollback"
    assert reg.audit[-1]["reason"] == "faithfulness below 0.90"


def test_rollback_without_previous_version_raises():
    reg = RouteRegistry()
    reg.publish("GENERATIVE", ("a",), "initial")
    with pytest.raises(NoPreviousVersion):
        reg.rollback("GENERATIVE", "nothing earlier")


def test_publish_after_rollback_appends_and_becomes_active():
    reg = RouteRegistry()
    reg.publish("R", ("a",), "v1")
    reg.publish("R", ("b",), "v2")
    reg.rollback("R", "bad")
    v3 = reg.publish("R", ("c",), "fixed")
    assert v3.version == 3
    assert reg.chain("R") == ("c",)
    assert [v.version for v in reg._history["R"]] == [1, 2, 3]  # history never rewritten


def test_kill_switch_removes_model_from_every_chain_immediately():
    reg = RouteRegistry()
    reg.publish("GENERATIVE", ("hosted:gpt-5-mini", "self:slm"), "initial")
    reg.disable_model("hosted:gpt-5-mini", "vendor incident")
    assert reg.chain("GENERATIVE") == ("self:slm",)
    reg.enable_model("hosted:gpt-5-mini", "vendor recovered")
    assert reg.chain("GENERATIVE") == ("hosted:gpt-5-mini", "self:slm")


# ---------- admission ----------


def test_token_bucket_bursts_refills_and_isolates_tenants():
    clock = FakeClock()
    tb = TenantTokenBucket(rate_per_s=10, burst=3, clock=clock)
    assert [tb.try_acquire("t1") for _ in range(4)] == [True, True, True, False]
    assert tb.try_acquire("t2")  # another tenant is unaffected
    clock.advance(0.1)  # one token refilled
    assert tb.try_acquire("t1")
    assert not tb.try_acquire("t1")
    assert tb.retry_after_ms("t1") >= 1


def test_bulkhead_rejects_when_full_and_releases_slot():
    bh = Bulkhead(2)
    assert bh.try_enter() and bh.try_enter()
    assert not bh.try_enter()
    bh.leave()
    assert bh.try_enter()


def test_bulkhead_context_releases_on_exception():
    bh = Bulkhead(1)
    with pytest.raises(RuntimeError):
        with bh.slot() as ok:
            assert ok
            raise RuntimeError("boom")
    assert bh.in_flight == 0
