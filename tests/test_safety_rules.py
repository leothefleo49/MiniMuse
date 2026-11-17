"""Safety: what each capability may do (always / ask first / never) really reaches the policy engine.

These check enforcement at the decision point (`Policy.evaluate`), not just that a setting was saved,
because a switch that is saved but not enforced is worse than no switch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.sentinel.policy import Decision, Policy
from nanomuse.server.service import MuseService
from nanomuse.tools.base import CallAssessment


def make(tmp_path: Path) -> tuple[Settings, MuseService]:
    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    return s, MuseService(s, llm=MockLLM([]))


def decide(svc: MuseService, tool: str) -> Decision:
    return Policy(svc.settings.sentinel).evaluate(tool, {}, CallAssessment()).decision


def set_policy(svc: MuseService, device: str, **caps: str) -> None:
    svc.update_settings({"permissions": {device: caps}})


def test_never_denies_and_always_allows_the_tools_behind_a_capability(tmp_path: Path) -> None:
    _s, svc = make(tmp_path)
    set_policy(svc, "computer", run_commands="never", send_email="always")
    assert decide(svc, "shell") is Decision.DENY
    assert decide(svc, "python_execute") is Decision.DENY
    assert decide(svc, "send_email") is Decision.ALLOW


def test_the_saved_policy_is_still_enforced_after_a_restart(tmp_path: Path) -> None:
    s, svc = make(tmp_path)
    set_policy(svc, "computer", run_commands="never")
    assert decide(svc, "shell") is Decision.DENY

    # a fresh process: new settings object, same data directory
    again = Settings(data_dir=s.data_dir, workspace=tmp_path / "ws")
    svc2 = MuseService(again, llm=MockLLM([]))
    assert svc2._permissions_view()["computer"]["run_commands"] == "never"  # it is shown as Never ...
    assert decide(svc2, "shell") is Decision.DENY  # ... so it must still be Never


def test_ask_first_keeps_the_configured_baseline(tmp_path: Path) -> None:
    _s, svc = make(tmp_path)
    assert "shell" in svc.settings.sentinel.always_ask_tools  # the shipped default
    set_policy(svc, "computer", files_read="always")  # an unrelated change
    assert "shell" in svc.settings.sentinel.always_ask_tools
    assert decide(svc, "shell") is Decision.ASK


def test_a_tool_shared_by_several_capabilities_obeys_the_strictest_one(tmp_path: Path) -> None:
    _s, svc = make(tmp_path)
    # Downloads and Files both use the files tool: one Never wins, whatever the others say
    set_policy(svc, "computer", downloads="always", files_write="never", files_read="always")
    assert decide(svc, "files") is Decision.DENY
    # Always only when every capability behind the tool says Always
    set_policy(svc, "computer", downloads="always", files_write="always", files_read="always")
    assert "files" in svc.settings.sentinel.always_allow_tools
    assert "files" not in svc.settings.sentinel.deny_tools
    set_policy(svc, "computer", files_write="ask")  # one row asks again: the tool no longer skips asking
    assert "files" not in svc.settings.sentinel.always_allow_tools


def test_the_new_safety_categories_are_offered_and_validated(tmp_path: Path) -> None:
    _s, svc = make(tmp_path)
    view = svc._permissions_view()["computer"]
    for cap in (
        "run_commands",
        "send_email",
        "browse_web",
        "control_screen",
        "passwords",
        "install_software",
        "share_location",
        "other_devices",
    ):
        assert view[cap] == "ask"  # nothing is silently allowed or blocked until the person says so
    set_policy(svc, "computer", share_location="never", browse_web="sometimes", not_a_capability="never")
    view = svc._permissions_view()["computer"]
    assert view["share_location"] == "never"
    assert view["browse_web"] == "ask"  # an invalid mode is ignored
    assert "not_a_capability" not in view
    assert decide(svc, "location") is Decision.DENY


def test_every_tool_a_capability_stands_for_really_exists() -> None:
    declared: set[str] = set()
    for src in Path("nanomuse/tools").glob("*.py"):
        declared.update(re.findall(r'^\s+name\s*[:=].*?"([a-z_0-9]+)"', src.read_text(encoding="utf-8"), flags=re.M))
    mapped = {tool for tools in MuseService.CAPABILITY_TOOLS.values() for tool in tools}
    assert mapped, "no capability maps to a tool"
    assert mapped <= declared, f"capabilities point at tools that do not exist: {sorted(mapped - declared)}"
    caps = {cap for cap, _ in MuseService.CAPABILITIES["computer"]}
    assert set(MuseService.CAPABILITY_TOOLS) <= caps


@pytest.mark.parametrize("device", ["computer", "phone"])
def test_policy_rows_survive_a_round_trip(tmp_path: Path, device: str) -> None:
    _s, svc = make(tmp_path)
    cap = MuseService.CAPABILITIES[device][0][0]
    set_policy(svc, device, **{cap: "never"})
    assert svc._permissions_view()[device][cap] == "never"
