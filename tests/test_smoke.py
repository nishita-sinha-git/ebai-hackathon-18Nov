"""Smoke suite as a pytest gate. The same scenarios run against the installed wheel in CI."""

import pytest

from app.smoke import SCENARIOS, run_smoke


@pytest.mark.smoke
def test_every_smoke_scenario_passes():
    results = run_smoke()
    failed = [r for r in results if not r.passed]
    assert not failed, "\n".join(f"{r.name}: {r.detail}" for r in failed)


@pytest.mark.smoke
def test_smoke_covers_every_scenario_once():
    names = [r.name for r in run_smoke()]
    assert len(names) == len(SCENARIOS) == len(set(names))
