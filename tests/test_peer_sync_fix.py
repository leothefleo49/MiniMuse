"""Regression tests for direct PC <-> phone pairing, peer sync, Muse data sync, and watch API."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server import create_app
from nanomuse.server.events import MAIN_THREAD
from nanomuse.server.service import MuseService
from nanomuse.sync import engine as engine_mod
from nanomuse.sync.peer import add_pair


def _make_service(tmp_path: Path, name: str) -> MuseService:
    data_dir = tmp_path / f"data_{name}"
    ws_dir = tmp_path / f"ws_{name}"
    data_dir.mkdir(parents=True, exist_ok=True)
    ws_dir.mkdir(parents=True, exist_ok=True)
    s = Settings()
    s.data_dir = data_dir
    s.agent.workspace = ws_dir
    s.agent.name = "MiniMuse"
    s.server.token = f"tok-{name}"
    s.server.auth = False
    llm = MockLLM([LLMResponse(content="ok")] * 10)
    return MuseService(s, llm=llm)


def test_setup_bundle_never_clobbers_device_id_and_prioritizes_canonical_avatar(tmp_path: Path) -> None:
    pc = _make_service(tmp_path, "pc")
    phone = _make_service(tmp_path, "phone")

    pc_id = pc.hub.device_id
    phone_id = phone.hub.device_id
    assert pc_id != phone_id

    # Populate PC avatar folder with >25 MB of raw "avatar-..." files plus canonical idle.webp/idle.mp4
    face_dir = pc.workspace() / "avatar" / "1791236806916264653"
    face_dir.mkdir(parents=True, exist_ok=True)
    (face_dir / "avatar-01-huge.mp4").write_bytes(b"A" * (14 * 1024 * 1024))
    (face_dir / "avatar-02-huge.mp4").write_bytes(b"B" * (14 * 1024 * 1024))
    (face_dir / "idle.webp").write_bytes(b"WEBP_STILL")
    (face_dir / "idle.mp4").write_bytes(b"MP4_IDLE_CLIP" * 2000)
    (face_dir / "working.mp4").write_bytes(b"MP4_WORK_CLIP" * 2000)
    pc.update_profile({"name": "Sir Diddlage", "avatar": "1791236806916264653", "emoji": "⚡", "color": "#ff2a85"})

    # Add a memory, goal, and reminder on PC
    pc.app.memory.add("Tyler loves bouldering at The Quarry", category="hobby", source="muse")
    pc.app.goals.create("Ship MiniMuse watch OS", description="Match the original Muse watch app")
    pc.app.reminders.create("Evening check-in", repeat="daily 21:00")

    # Put device-local app_lock on PC
    pc.connections.data["app_lock"] = {"biometric_credential_id": "pc-windows-hello-id"}
    pc.connections._save()

    bundle = pc.sync.export_setup_bundle()
    assert "hub" not in bundle["app_settings"]
    assert "app_lock" not in bundle["app_settings"]
    assert "1791236806916264653/idle.webp" in bundle["avatar_files"]
    assert "1791236806916264653/idle.mp4" in bundle["avatar_files"]
    assert "1791236806916264653/working.mp4" in bundle["avatar_files"]

    # Apply bundle on phone: phone MUST keep its own device_id and still receive avatar + Muse data!
    res = phone.sync.apply_setup_bundle(bundle)
    assert res["applied"] is True
    assert phone.hub.device_id == phone_id
    assert phone.connections.data.get("hub", {}).get("device_id") == phone_id
    assert "app_lock" not in phone.connections.data
    assert phone.profile.name == "Sir Diddlage"
    assert phone.profile.avatar == "1791236806916264653"
    assert (phone.workspace() / "avatar" / "1791236806916264653" / "idle.webp").read_bytes() == b"WEBP_STILL"
    assert (phone.workspace() / "avatar" / "1791236806916264653" / "idle.mp4").is_file()
    assert len(phone.app.memory.all()) >= 1
    assert any("bouldering" in m.content for m in phone.app.memory.all())
    assert any("MiniMuse watch OS" in g.title for g in phone.app.goals.list())
    assert any("Evening check-in" in r.text for r in phone.app.reminders.list())


def test_android_identity_uses_model_and_heals_collided_pc_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NANOMUSE_DEVICE", "android")
    monkeypatch.setenv("NANOMUSE_DEVICE_MODEL", "Samsung SM-S928U")

    # Pre-seed a collided pc-* device_id that matches pairs.json (reproducing the exact live bug)
    data_dir = tmp_path / "data_android"
    ws_dir = tmp_path / "ws_android"
    data_dir.mkdir(parents=True, exist_ok=True)
    ws_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "app-settings.json").write_text(
        json.dumps({"version": 1, "hub": {"device_id": "pc-ceff25c24442"}}), encoding="utf-8"
    )
    add_pair(data_dir, "http://192.168.1.223:8787", "tok-peer", name="DESKTOP-F71C82T", device_id="pc-ceff25c24442")

    s = Settings()
    s.data_dir = data_dir
    s.agent.workspace = ws_dir
    s.server.auth = False
    svc = MuseService(s, llm=MockLLM([LLMResponse(content="ok")]))

    assert svc.hub.device_id.startswith("phone-")
    assert svc.hub.device_id != "pc-ceff25c24442"
    assert svc.hub.device_name.lower() != "localhost"
    assert "galaxy" in svc.hub.device_name.lower() or "samsung" in svc.hub.device_name.lower() or "sm-s928u" in svc.hub.device_name.lower()


def test_sync_one_peer_executes_without_name_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly exercise _sync_one_peer against an in-memory SyncPeerStore to verify `client`,
    `applied`, and `pushed` work end-to-end (regression for NameError: name 'client' is not defined)."""
    pc = _make_service(tmp_path, "pc2")
    phone = _make_service(tmp_path, "phone2")

    # Add a real message on PC and mirror it into PC's local_store
    pc_main = pc.threads[MAIN_THREAD]
    pc_main.timeline.add({"type": "user", "text": "Hello from PC!", "ts": "2026-11-19T10:00:00Z"})
    pc_main.timeline.add({"type": "assistant", "text": "Synced to phone!", "final": True, "ts": "2026-11-19T10:00:02Z"})
    pc.sync.mirror_local_now()

    # Fake SyncClient backed directly by pc.sync.local_store
    class _DirectStoreClient:
        def __init__(self, cloud: Any) -> None:
            self.cloud = cloud

        async def set_enabled(self, enabled: bool) -> dict[str, Any]:
            return pc.sync.local_store.set_enabled(enabled)

        async def changes(self, since: int = 0, limit: int = 200, scope: str = "all", tail: int = 0) -> dict[str, Any]:
            return pc.sync.local_store.changes(since, limit, scope=scope, tail=tail)

        async def push(self, device: str, conversations: list[dict[str, Any]], messages: list[dict[str, Any]]) -> dict[str, Any]:
            return pc.sync.local_store.push(device, conversations, messages)

    monkeypatch.setattr(engine_mod, "SyncClient", _DirectStoreClient)

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(phone.sync, "_refresh_peer_identity", _noop_async)
    monkeypatch.setattr(phone.sync, "_sync_muse_data_with_peer", _noop_async)

    peer = {
        "id": pc.hub.device_id,
        "name": "DESKTOP-TEST",
        "kind": "computer",
        "url": "http://192.168.1.223:8787",
        "token": "test-peer-token",
    }
    applied, pushed = asyncio.run(phone.sync._sync_one_peer(peer))
    assert applied >= 2
    texts = [ev.get("text") for ev in phone.threads[MAIN_THREAD].timeline.events]
    assert "Hello from PC!" in texts
    assert "Synced to phone!" in texts
    st = phone.sync._peer_status[phone.sync._peer_key_of(peer)]
    assert st["online"] is True
    assert st["last_error"] == ""


def test_watch_state_includes_threads_apps_reactions_and_clips(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, "watch")
    # Create an animated avatar clip and a workspace HTML watch app
    face_dir = svc.workspace() / "avatar" / "1791236806916264653"
    face_dir.mkdir(parents=True, exist_ok=True)
    (face_dir / "idle.mp4").write_bytes(b"0" * 20_000)
    svc.update_profile({"name": "Sir Diddlage", "avatar": "1791236806916264653"})

    apps_dir = svc.workspace() / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    (apps_dir / "watch-timer.html").write_text("<html><body>Timer</body></html>", encoding="utf-8")

    # Add a message with a reaction and a side chat
    svc.threads[MAIN_THREAD].timeline.add(
        {"type": "assistant", "text": "Bet, dropping it then", "final": True, "reactions": [{"emoji": "👍"}]}
    )
    side = svc.create_thread("Food Log")
    side.timeline.add({"type": "user", "text": "Ate steak", "ts": "2026-11-19T11:00:00Z"})

    app = create_app(svc.settings, service=svc)
    tc = TestClient(app)
    r = tc.get("/api/watch/state")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_name"] == "Sir Diddlage"
    assert "idle" in body["avatar"]["clips"]
    assert any(th["title"] == "Food Log" for th in body["threads"])
    assert any(a["name"] == "Watch Timer" and a["watch_optimized"] for a in body["apps"])
    assert any(m.get("reactions") == ["👍"] for m in body["recent"])

    # Querying a specific side thread returns that thread's messages
    r2 = tc.get(f"/api/watch/state?thread={side.id}")
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["thread_id"] == side.id
    assert any(m["text"] == "Ate steak" for m in b2["recent"])
