"""One-tap browser control install: the steps run in order and say how they ended."""

from __future__ import annotations

import sys

import pytest

from nanomuse.server import browser_install


@pytest.fixture(autouse=True)
def fresh_state() -> None:
    browser_install._state.update(status="idle", step="", message="", log=[], started=0.0)


def test_both_steps_run_in_order_and_finish() -> None:
    seen: list[list[str]] = []

    def runner(cmd: list[str]) -> tuple[int, str]:
        seen.append(cmd)
        return 0, "ok\n"

    out = browser_install.start(runner, wait=True)
    assert out["status"] == "done"
    assert [c[1:3] for c in seen] == [["-m", "pip"], ["-m", "playwright"]]
    assert seen[0][0] == sys.executable


def test_a_failing_step_stops_and_names_itself() -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        return 1, "no network\n"

    out = browser_install.start(runner, wait=True)
    assert out["status"] == "error"
    assert "Installing the browser library failed: no network" in out["message"]
    assert len(calls) == 1


def test_a_packaged_app_explains_instead_of_trying(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    out = browser_install.start(lambda c: (0, ""), wait=True)
    assert out["status"] == "error"
    assert "packaged" in out["message"]
