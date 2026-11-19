"""The small things a device list and a phone need: true message counts, a store identity
the other side can trust, and a phone that listens on its network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nanomuse.server import bind_host_for
from nanomuse.server.events import Timeline
from nanomuse.sync.peer import SyncPeerStore


def test_event_count_is_the_total_not_the_window(tmp_path: Path) -> None:
    # the timeline keeps the newest 2000 events in memory; the count a person reads is how
    # many the conversation ever had
    timeline = Timeline("t", tmp_path / "t.json", max_events=10)
    for i in range(25):
        timeline.add({"type": "user", "text": f"m{i}"})
    assert len(timeline.events) == 10
    assert timeline.total == 25

    # the total survives a reload (it is written beside the events)
    again = Timeline("t", tmp_path / "t.json", max_events=10)
    assert len(again.events) == 10
    assert again.total == 25


def test_a_deleted_message_leaves_the_count(tmp_path: Path) -> None:
    timeline = Timeline("t", tmp_path / "t.json")
    ev = timeline.add({"type": "user", "text": "hello"})
    assert timeline.total == 1
    timeline.remove(ev["id"])
    assert timeline.total == 0


def test_an_old_file_without_a_total_reports_what_it_holds(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text(
        '{"thread": "t", "events": [{"id": "a", "type": "user", "text": "x", "ts": "2026-10-01T10:00:00+00:00"}]}',
        encoding="utf-8",
    )
    timeline = Timeline("t", path)
    assert timeline.total == 1


def test_a_peer_store_has_an_identity(tmp_path: Path) -> None:
    store = SyncPeerStore(tmp_path / "a.db")
    sid = store.store_id()
    assert sid and store.store_id() == sid
    assert store.state()["store"] == sid
    assert store.changes(0, 10)["store"] == sid
    # a different store (another data folder, a re-pair) is not the same sequence space
    other = SyncPeerStore(tmp_path / "b.db")
    assert other.store_id() != sid


def test_an_android_runtime_listens_on_its_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NANOMUSE_DEVICE", "android")
    monkeypatch.delenv("NANOMUSE_BIND_HOST", raising=False)
    assert bind_host_for("127.0.0.1") == "0.0.0.0"
    # the opt-out for a phone on a public network
    monkeypatch.setenv("NANOMUSE_BIND_HOST", "127.0.0.1")
    assert bind_host_for("127.0.0.1") == "127.0.0.1"


def test_a_computer_keeps_the_host_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NANOMUSE_DEVICE", raising=False)
    monkeypatch.delenv("NANOMUSE_BIND_HOST", raising=False)
    assert bind_host_for("127.0.0.1") == "127.0.0.1"
    assert bind_host_for("0.0.0.0") == "0.0.0.0"
    assert bind_host_for("192.168.1.5") == "192.168.1.5"


def test_an_imported_avatar_wins_over_a_name_without_its_animation(tmp_path: Any) -> None:
    # the Muse profile can name a face ("goku") while the bundle is stored under its id;
    # apply_plan prefers the folder that actually holds the imported animation
    from nanomuse.server import data_import

    root = tmp_path
    workspace = root / "workspace"
    imported = workspace / "avatar" / "1791236806916264653"
    imported.mkdir(parents=True)
    (imported / "idle.webp").write_bytes(b"webp")
    (imported / "idle.mp4").write_bytes(b"mp4" * 4000)
    named = workspace / "avatar" / "goku"
    named.mkdir(parents=True)
    (named / "idle.webp").write_bytes(b"webp")

    class Profile:
        def __init__(self) -> None:
            self.fields = {"avatar": "goku"}

        def to_dict(self) -> dict[str, Any]:
            return dict(self.fields)

    class Svc:
        profile = Profile()

        def workspace(self) -> Path:
            return workspace

        def update_profile(self, **fields: Any) -> None:
            self.profile.fields.update(fields)

    plan = data_import.ImportPlan(option_key="k")
    plan.profile_data = {"avatar": "goku"}
    plan.avatar_face = "1791236806916264653"
    svc = Svc()
    chosen = data_import._avatar_to_wear(svc, plan, {"avatar": "goku"})
    assert chosen == "1791236806916264653"
    # a profile that already wears the animated folder keeps it
    assert data_import._avatar_to_wear(svc, plan, {"avatar": "1791236806916264653"}) == (
        "1791236806916264653"
    )
