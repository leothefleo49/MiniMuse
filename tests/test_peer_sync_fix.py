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


def test_hands_off_mode_persists_and_auto_resolves_pending_approvals(tmp_path: Path) -> None:
    from nanomuse.config import apply_app_settings, load_app_settings
    from nanomuse.schema import RiskLevel
    from nanomuse.ui import ApprovalRequest

    svc = _make_service(tmp_path, "handsoff")
    assert svc.settings.sentinel.mode != "auto"

    # Simulate an in-flight approval waiting when the user switches to Hands-off mid-task
    async def _run_check() -> None:
        req = ApprovalRequest(
            tool="shell",
            args={"command": "echo hi"},
            summary="shell: echo hi",
            risk=RiskLevel.SENSITIVE,
            reasons=["sensitive"],
        )
        task = asyncio.create_task(svc.ui.ask_approval(req))
        await asyncio.sleep(0.05)
        assert len(svc.ui.pending_approvals) == 1

        # Switching to Hands-off ('auto') mid-task must immediately auto-resolve pending approvals
        svc.update_settings({"sentinel_mode": "auto"})
        verdict = await asyncio.wait_for(task, timeout=2.0)
        assert verdict.approved is True
        assert len(svc.ui.pending_approvals) == 0

    asyncio.run(_run_check())
    assert svc.settings.sentinel.mode == "auto"

    # And sentinel.mode == 'auto' must be saved to app-settings.json and restored on restart
    stored = load_app_settings(svc.data_dir)
    assert stored.get("sentinel", {}).get("mode") == "auto"
    fresh = Settings()
    apply_app_settings(fresh, stored)
    assert fresh.sentinel.mode == "auto"


def test_request_setup_and_zapier_quota_tracking(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, "setup_zapier")
    app = create_app(svc.settings, service=svc)
    tc = TestClient(app)

    # 1. Execute request_setup tool for wireless_debugging and verify setup_prompt event is emitted
    setup_tool = svc.app.tools.get("request_setup")
    assert setup_tool is not None
    res = asyncio.run(
        setup_tool.execute(
            kind="wireless_debugging",
            target="wireless_debugging",
            why="Need Wireless Debugging to control your phone screen.",
            resume_prompt="Continue phone control task.",
        )
    )
    assert res.ok
    payload = json.loads(res.output)
    assert payload["popup_shown"] is True
    events = svc.threads[MAIN_THREAD].timeline.events
    assert any(ev.get("type") == "setup_prompt" and ev.get("kind") == "wireless_debugging" for ev in events)

    # 2. Record Zapier calls and check quota + Direct API recommendation via API and api_connector
    from nanomuse.connectors.zapier_quota import ZapierQuotaTracker

    tracker = ZapierQuotaTracker(svc.data_dir)
    for _ in range(3):
        q = tracker.record_call("zapier", "slack_send_channel_message")
    assert q["used"] == 3
    assert q["recommend_direct"] is True
    assert q["matched_preset"] == "slack"
    assert "Zapier Quota Notice" in q["notice"]

    r_quota = tc.get("/api/connections/zapier-quota")
    assert r_quota.status_code == 200
    q_body = r_quota.json()
    assert q_body["used"] == 3
    assert any(rec["preset_id"] == "slack" for rec in q_body["recommendations"])

    api_tool = svc.app.tools.get("api_connector")
    assert api_tool is not None
    cmp_res = asyncio.run(api_tool.execute(action="compare_zapier", query="slack"))
    assert cmp_res.ok
    cmp_data = json.loads(cmp_res.output)
    assert cmp_data["direct_preset"]["preset_id"] == "slack"

    # 3. Live notifications endpoint
    r_post = tc.post(
        "/api/notifications/live",
        json={"app": "Messages", "title": "Mom", "text": "Dinner at 6 PM"},
    )
    assert r_post.status_code == 200
    r_get = tc.get("/api/notifications/live")
    assert r_get.status_code == 200
    assert r_get.json()["count"] == 1
    assert r_get.json()["notifications"][0]["title"] == "Mom"


def test_avatar_mp4_explicit_mime_and_dark_fallback_and_bg_profile(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, "avatar_mime")
    face_dir = svc.workspace() / "avatar" / "1791236806916264653"
    face_dir.mkdir(parents=True, exist_ok=True)
    (face_dir / "avatar-1791236806916264653-1-darkmode-idle-attempt.mp4").write_bytes(b"MP4_DARK_BYTES")
    (face_dir / "idle.mp4").write_bytes(b"MP4_LIGHT_BYTES")

    svc.update_profile(
        {
            "avatar": "1791236806916264653",
            "avatar_bg_mode": "color",
            "avatar_bg_color": "#7c3aed",
            "avatar_bg_image": "attachments/bg.png",
        }
    )
    prof = svc.profile.to_dict()
    assert prof["avatar_bg_mode"] == "color"
    assert prof["avatar_bg_color"] == "#7c3aed"
    assert prof["avatar_bg_image"] == "attachments/bg.png"

    app = create_app(svc.settings, service=svc)
    tc = TestClient(app)
    r_mp4 = tc.get("/api/files/avatar/1791236806916264653/dark-idle.mp4")
    assert r_mp4.status_code == 200
    assert r_mp4.headers["content-type"].startswith("video/mp4")
    assert r_mp4.content == b"MP4_DARK_BYTES"


def test_avatar_library_situations_border_glow_and_ip_healing(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, "avatar_lib")
    face_dir = svc.workspace() / "avatar" / "1791236806916264653"
    face_dir.mkdir(parents=True, exist_ok=True)
    (face_dir / "idle.mp4").write_bytes(b"CANONICAL_IDLE_MP4")
    (face_dir / "avatar-1791236806916264653-1-making_something.mp4").write_bytes(b"MAKING_SOMETHING_MP4")
    (face_dir / "idle.webp").write_bytes(b"IDLE_WEBP")

    svc.update_profile(
        {
            "avatar": "1791236806916264653",
            "avatar_border_mode": "color",
            "avatar_border_color": "#0891b2",
            "avatar_border_glow": 70,
        }
    )
    prof = svc.profile.to_dict()
    assert prof["avatar_border_mode"] == "color"
    assert prof["avatar_border_color"] == "#0891b2"
    assert prof["avatar_border_glow"] == 70

    app = create_app(svc.settings, service=svc)
    tc = TestClient(app)

    # 1. Avatar library lists face and unified assets array (preventing undefined.length crashes)
    #    Also test auto-healing if idle.mp4 was previously corrupted with a 7x12 call-dark-idle clip.
    (face_dir / "avatar-1791236806916264653-1.mp4").write_bytes(b"CANONICAL_SQUARE_IDLE_MP4")
    (face_dir / "avatar-1791236806916264653-1-call-dark-idle-7x12.mp4").write_bytes(b"BAD_PORTRAIT_CALL_DARK_MP4_999")
    (face_dir / "idle.mp4").write_bytes(b"BAD_PORTRAIT_CALL_DARK_MP4_999")

    r_lib = tc.get("/api/avatar/library")
    assert r_lib.status_code == 200
    lib = r_lib.json()
    face_entry = next(f for f in lib["faces"] if f["id"] == "1791236806916264653")
    assert isinstance(face_entry["assets"], list) and len(face_entry["assets"]) > 0
    # Corrupted idle.mp4 must have been auto-healed back to canonical square avatar-*-1.mp4
    assert (face_dir / "idle.mp4").read_bytes() == b"CANONICAL_SQUARE_IDLE_MP4"

    # 2. Customize situation animation mapping
    r_sit = tc.put(
        "/api/avatar/1791236806916264653/situations",
        json={"situations": {"waiting": "avatar-1791236806916264653-1-making_something.mp4"}},
    )
    assert r_sit.status_code == 200
    assert (face_dir / "waiting.mp4").read_bytes() == b"MAKING_SOMETHING_MP4"
    assert svc.profile.avatar_situation_clips.get("waiting") == "avatar-1791236806916264653-1-making_something.mp4"

    # 3. Inbound peer contact heals changed Wi-Fi IP address
    svc.sync.peers = [
        {
            "id": "phone-zflip7",
            "name": "Galaxy Z Flip 7",
            "kind": "phone",
            "url": "http://192.168.1.50:8787",
            "token": "tok123",
        }
    ]
    healed = svc.sync.note_inbound_peer_contact(
        device_id="phone-zflip7",
        sender_ip="10.0.0.88",
        port=8787,
        name="Galaxy Z Flip 7",
        kind="phone",
    )
    assert healed is True
    assert svc.sync.peers[0]["url"] == "http://10.0.0.88:8787"

    # 4. Device overrides for sentinel & notifications
    r_ov = tc.put(
        "/api/sync/device-overrides",
        json={
            "enabled": True,
            "keep_local_appearance": True,
            "keep_local_sentinel": True,
            "keep_local_notifications": True,
        },
    )
    assert r_ov.status_code == 200
    ov = r_ov.json()["device_overrides"]
    assert ov["enabled"] is True
    assert ov["keep_local_sentinel"] is True
    assert ov["keep_local_notifications"] is True

    # 5. Zero-typing watch auto-claim works over local network
    r_wc = tc.get("/api/watch/auto-claim")
    assert r_wc.status_code == 200
    assert r_wc.json()["token"]


def test_sync_token_retention_multi_url_healing_avatar_animated_and_vr(tmp_path: Path) -> None:
    from nanomuse.sync.discovery import PairingManager
    from nanomuse.sync.peer import add_pair, load_pairs
    from nanomuse.tools.vr_quest import QuestVRTool, is_vr_headset_text

    # 1. PairingManager keeps prior working tokens for the same device_id so retries never trigger 401
    pm = PairingManager(tmp_path / "pm")
    tok1 = pm.mint_inbound_token("phone-zflip7", "Galaxy Z Flip 7")
    tok2 = pm.mint_inbound_token("phone-zflip7", "Galaxy Z Flip 7")
    assert pm.verify_inbound_token(tok1) is True
    assert pm.verify_inbound_token(tok2) is True

    # 2. Multi-URL Personal Cloud exchange + return_token auto-heals vault key in pairs.json
    pc = _make_service(tmp_path, "pc_heal")
    phone = _make_service(tmp_path, "phone_heal")
    pc.sync.set_personal_cloud_urls_provider(
        lambda: ["https://my-free-tunnel.trycloudflare.com", "http://100.88.1.2:8787", "http://192.168.1.10:8787"]
    )
    add_pair(phone.data_dir, "http://192.168.1.10:8787", "stale-bad-token", name="PC", device_id=pc.hub.device_id, kind="computer")
    phone.sync.reload_peers()

    fresh_tok = pc.sync._get_or_mint_return_token(phone.hub.device_id, "Phone")
    assert fresh_tok.startswith("nmpeer_")
    assert pc.sync.pairing.verify_inbound_token(fresh_tok) is True

    payload = pc.sync.export_muse_data()
    payload["peer_sync_info"] = {
        "device_id": pc.hub.device_id,
        "name": pc.hub.device_name,
        "kind": "computer",
        "urls": pc.sync._my_reachable_urls(),
        "return_token": fresh_tok,
    }
    phone.sync.apply_muse_data(payload)
    healed_pairs = load_pairs(phone.data_dir)
    assert any("https://my-free-tunnel.trycloudflare.com" in (p.get("urls") or []) for p in healed_pairs)
    phone.sync.reload_peers()
    assert phone.sync.peers[0]["token"] == fresh_tok

    # 3. Profile.avatar_animated toggle persists and round-trips
    pc.update_profile({"avatar_animated": False, "avatar_bg_mode": "default"})
    assert pc.profile.avatar_animated is False
    assert pc.profile.avatar_bg_mode == "default"
    pc.update_profile({"avatar_animated": True})
    assert pc.profile.avatar_animated is True

    # 4. Meta Quest / VR headset detection & tool registration + watch_target serial inference
    assert is_vr_headset_text("Oculus Quest 3 eureka") is True
    assert is_vr_headset_text("PICO 4 Ultra") is True
    assert is_vr_headset_text("Samsung SM-F741U") is False
    assert any(t.name == "quest_control" for t in pc.app.tools.tools)
    assert QuestVRTool(pc.data_dir, pc.workspace()).name == "quest_control"

    (pc.data_dir / "watch_target.json").write_text(
        json.dumps({"target": "adb-R5GL43AHMVF-Pb01xS._adb-tls-connect._tcp"}), encoding="utf-8"
    )
    app = create_app(pc.settings, service=pc)
    tc = TestClient(app)
    r_w = tc.get("/api/watch/devices")
    assert r_w.status_code == 200
    assert r_w.json()["auto"]["saved"]["serial"] == "R5GL43AHMVF"
    r_vr = tc.get("/api/vr/devices")
    assert r_vr.status_code == 200
    assert "headsets" in r_vr.json()




