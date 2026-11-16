"""Verification suite for MiniMuse v1.0.16 fork enhancements.

Covers:
1. Default agent name ("MiniMuse") & legacy "nanoMuse" migration while keeping custom names.
2. ReleaseNotesTracker upgrade detection + /api/update whats_new & dismiss.
3. ChromeBridge + /api/chrome/* + /ws/chrome + ChromeBridgeBackend + Playwright isolation flags.
4. WinWindowHands + ComputerLink background window mode + live hands JPEG frames endpoint.
5. Free Personal MiniMuse Cloud (LAN/Tailscale/Cloudflare Quick Tunnel) + /api/personal-cloud/*.
6. Live Call capabilities, model override (/api/call/config), and /ws/call voice loop.
7. Wear OS Watch pairing & companion endpoints (/api/watch/{code,claim,state,ask,approve}).
"""

from __future__ import annotations

import base64
import io
import json
import sys
import types
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nanomuse import __version__
from nanomuse.computer.mac_window import WindowInfo
from nanomuse.computer.win_window import WinWindowHands
from nanomuse.config import AgentSettings, GUISettings, HandsSettings, Settings
from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server import create_app
from nanomuse.server.chrome_bridge import ChromeBridge, build_extension_zip, find_extension_dir
from nanomuse.server.personal_cloud import PersonalCloudManager
from nanomuse.server.realtime import detect_realtime_kind
from nanomuse.server.release_notes import ReleaseNotesTracker
from nanomuse.server.service import MuseService, Profile
from nanomuse.tools.browser_backends import ChromeBridgeBackend, PlaywrightBackend


def _png_bytes(w: int = 800, h: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


def _jpeg_bytes(w: int = 160, h: int = 120) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (30, 120, 80)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return Settings(
        data_dir=tmp_path / "data",
        agent=AgentSettings(workspace=ws),
        gui=GUISettings(enabled=False),
        hands=HandsSettings(enabled=True, settle_s=0.0),
    )


@pytest.fixture()
def server(settings: Settings) -> Iterator[tuple[TestClient, MuseService, MockLLM]]:
    settings.server.token = "secret-watch-token"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer secret-watch-token"
        yield client, service, llm


# ---------------------------------------------------------------------------
# 1. Branding & Legacy "nanoMuse" -> "MiniMuse" Migration
# ---------------------------------------------------------------------------


def test_agent_and_profile_migrate_legacy_nanomuse_to_minimuse(settings: Settings) -> None:
    assert AgentSettings().name == "MiniMuse"
    assert AgentSettings(name="nanoMuse").name == "MiniMuse"
    assert AgentSettings(name="  nanomuse  ").name == "MiniMuse"
    assert AgentSettings(name="Jarvis").name == "Jarvis"

    migrated = Profile.from_dict({"name": "nanoMuse", "bio": "helper", "avatar": ""})
    assert migrated.name == "MiniMuse"

    custom = Profile.from_dict({"name": "Nova", "bio": "helper", "avatar": ""})
    assert custom.name == "Nova"

    # Legacy profile persisted on disk as "nanoMuse" must migrate automatically on load.
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "profile.json").write_text(
        json.dumps({"name": "nanoMuse", "bio": "", "avatar": ""}), encoding="utf-8"
    )
    svc = MuseService(settings, llm=MockLLM([]))
    assert svc.profile.name == "MiniMuse"


# ---------------------------------------------------------------------------
# 2. Release Notes & Post-Update "What's New"
# ---------------------------------------------------------------------------


def test_release_notes_tracker_upgrade_and_api_dismiss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: tuple[TestClient, MuseService, MockLLM]
) -> None:
    data = tmp_path / "upgrade_data"
    data.mkdir(parents=True)
    (data / "state.db").write_bytes(b"sqlite")
    (data / "update_seen.json").write_text(
        json.dumps({"last_version": "1.0.15"}), encoding="utf-8"
    )

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    tracker = ReleaseNotesTracker(data, current=__version__)
    pending = tracker.detect_on_startup()
    assert pending is not None
    assert pending["version"] == __version__.lstrip("v")
    assert pending["previous"] == "1.0.15"
    assert pending["added"] and pending["improved"] and pending["fixed"]

    tracker.dismiss()
    assert tracker.pending() is None

    # API /api/update + dismiss endpoint
    client, _svc, _llm = server
    upd = client.get("/api/update", params={"check": "false"}).json()
    assert "current_notes" in upd
    assert upd["current_notes"]["version"] == __version__.lstrip("v")
    dismiss = client.post("/api/update/whats-new/dismiss")
    assert dismiss.status_code == 200
    assert dismiss.json()["ok"] is True


# ---------------------------------------------------------------------------
# 3. Chrome Companion Extension & Playwright Extension Isolation
# ---------------------------------------------------------------------------


def test_chrome_extension_zip_and_websocket_rpc(
    server: tuple[TestClient, MuseService, MockLLM]
) -> None:
    ext_dir = find_extension_dir()
    assert ext_dir is not None and (ext_dir / "manifest.json").is_file()
    zip_bytes = build_extension_zip(ext_dir)
    assert zip_bytes is not None
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "MiniMuse-Chrome-Extension/manifest.json" in names
        assert "MiniMuse-Chrome-Extension/background.js" in names
        assert "MiniMuse-Chrome-Extension/sidepanel.html" in names

    client, svc, _llm = server
    resp = client.get("/api/chrome/extension.zip")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/zip")

    st0 = client.get("/api/chrome/status").json()
    assert st0["extension_packaged"] is True
    assert st0["connected"] is False

    with client.websocket_connect("/ws/chrome") as ws:
        ws.send_json({"kind": "auth", "token": "secret-watch-token"})
        ws.send_json(
            {
                "kind": "chrome_state",
                "version": "1.0.16",
                "browser": "Chrome",
                "attached": True,
                "tab_id": 42,
                "url": "https://example.org/app",
                "title": "Example App",
                "width": 1440,
                "height": 900,
            }
        )
        # Allow server task loop to process inbound frame
        for _ in range(10):
            st = client.get("/api/chrome/status").json()
            if st["attached"]:
                break
        assert st["connected"] is True
        assert st["attached"] is True
        assert st["tab_id"] == 42
        assert st["title"] == "Example App"
        assert svc.chrome_bridge.ready is True


async def test_chrome_bridge_backend_roundtrip() -> None:
    sent_requests: list[dict[str, Any]] = []
    bridge: ChromeBridge

    async def fake_send(msg: dict[str, Any]) -> None:
        sent_requests.append(msg)
        rid = str(msg.get("id") or "")
        op = str(msg.get("action") or "")
        result: dict[str, Any] = {"width": 1280, "height": 800}
        if op == "state":
            result = {"url": "https://minimuse.test/", "title": "MiniMuse Tab"}
        elif op == "eval":
            result = {"value": [1, 2, 3]}
        elif op == "screenshot":
            result = {"jpeg": base64.b64encode(_jpeg_bytes()).decode("ascii")}
        elif op == "fetch":
            result = {"status": 200, "headers": {"x": "1"}, "body": "ok", "url": msg.get("url")}
        bridge.handle_message(
            session,
            {"kind": "chrome_response", "id": rid, "ok": True, "result": result},
        )

    bridge = ChromeBridge()
    session = bridge.attach_socket(fake_send, version="1.0.16", browser="Chrome")
    bridge.handle_message(
        session,
        {
            "kind": "chrome_state",
            "attached": True,
            "tab_id": 9,
            "url": "https://minimuse.test/",
            "title": "MiniMuse Tab",
            "width": 1280,
            "height": 800,
        },
    )

    backend = ChromeBridgeBackend(bridge)
    await backend.ensure()
    assert backend.open is True
    await backend.goto("https://minimuse.test/next")
    assert await backend.evaluate("() => [1,2,3]") == [1, 2, 3]
    shot = await backend.screenshot(quality=60)
    assert shot.startswith(b"\xff\xd8")
    fetched = await backend.fetch("https://minimuse.test/api")
    assert fetched["status"] == 200 and fetched["body"] == "ok"
    actions_sent = [m["action"] for m in sent_requests]
    assert "ping" in actions_sent
    assert [a for a in actions_sent if a != "ping"] == ["navigate", "eval", "screenshot", "fetch"]


async def test_playwright_isolates_interfering_extensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_kwargs: dict[str, Any] = {}

    class _FakePage:
        def is_closed(self) -> bool:
            return False

        def set_default_timeout(self, _ms: int) -> None:
            pass

        def on(self, _event: str, _fn: Any) -> None:
            pass

    class _FakeContext:
        def __init__(self) -> None:
            self.pages = [_FakePage()]

        def on(self, _event: str, _fn: Any) -> None:
            pass

        def set_default_timeout(self, _ms: int) -> None:
            pass

    class _FakeChromium:
        async def launch_persistent_context(self, _user_data_dir: str, **kwargs: Any) -> _FakeContext:
            captured_kwargs.update(kwargs)
            return _FakeContext()

    class _FakePlaywright:
        chromium = _FakeChromium()

    class _FakeStarter:
        async def start(self) -> _FakePlaywright:
            return _FakePlaywright()

    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.async_playwright = lambda: _FakeStarter()  # type: ignore[attr-defined]
    fake_pw = types.ModuleType("playwright")
    fake_pw.async_api = fake_async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", fake_pw)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

    backend = PlaywrightBackend(tmp_path / "profile", headless=True)
    await backend.ensure()
    args = list(captured_kwargs.get("args") or [])
    assert "--disable-extensions" in args
    assert "--disable-default-apps" in args
    assert "--no-first-run" in args
    assert "--disable-background-timer-throttling" in args


# ---------------------------------------------------------------------------
# 4. Background Windows Control & Live Hands Frame Streaming
# ---------------------------------------------------------------------------


def test_win_window_hands_and_live_frame_streaming(
    settings: Settings, server: tuple[TestClient, MuseService, MockLLM]
) -> None:
    actions: list[tuple[Any, ...]] = []
    png = _png_bytes(1000, 500)

    class _FakeWinAdapter:
        def windows(self) -> list[WindowInfo]:
            return [
                WindowInfo(
                    id=101,
                    pid=4242,
                    owner="chrome.exe",
                    bundle_id="chrome.exe",
                    title="Background Tab",
                    x=100,
                    y=50,
                    width=1000,
                    height=500,
                )
            ]

        def capture(self, hwnd: int) -> bytes:
            actions.append(("capture", hwnd))
            return png

        def move(self, hwnd: int, sx: float, sy: float) -> None:
            actions.append(("move", hwnd, round(sx, 1), round(sy, 1)))

        def click(self, hwnd: int, sx: float, sy: float, button: str = "left", clicks: int = 1) -> None:
            actions.append(("click", hwnd, round(sx, 1), round(sy, 1), button, clicks))

        def drag(self, hwnd: int, sx: float, sy: float, sx2: float, sy2: float) -> None:
            actions.append(("drag", hwnd, sx, sy, sx2, sy2))

        def scroll(self, hwnd: int, sx: float, sy: float, dy: float) -> None:
            actions.append(("scroll", hwnd, sx, sy, dy))

        def type_text(self, hwnd: int, text: str) -> None:
            actions.append(("type", hwnd, text))

        def key(self, hwnd: int, keys: list[str]) -> None:
            actions.append(("key", hwnd, tuple(keys)))

    hands = WinWindowHands(adapter=_FakeWinAdapter())
    shot, frame = hands.look("chrome", "Background")
    assert shot == png and frame.image_width == 1000 and frame.image_height == 500
    hands.click(499, 499)
    hands.type("hi")
    hands.key(["ctrl", "l"])
    assert actions[0] == ("capture", 101)
    assert actions[1][0:2] == ("click", 101)
    assert actions[2] == ("type", 101, "hi")
    assert actions[3] == ("key", 101, ("ctrl", "l"))

    # ComputerLink pinning & /api/hands/{thread}/frames/{fid}.jpg
    client, svc, _llm = server
    t_resp = client.post(
        "/api/computer/target", json={"app": "notepad.exe", "title": "Notes"}
    )
    assert t_resp.status_code == 200
    assert svc.app.computer is not None
    assert svc.app.computer.target_app == "notepad.exe"
    assert svc.app.computer.target_title == "Notes"

    before_events = len(svc.ui.get_timeline("main").events)
    svc.ui.begin_run("main")
    svc.ui.on_hands({"event": "begin", "text": "Click background button"})
    jpeg = _jpeg_bytes()
    svc.ui.on_hands(
        {
            "event": "frame",
            "jpeg": jpeg,
            "mode": "window",
            "app": "chrome.exe",
            "title": "Background Tab",
            "width": 160,
            "height": 120,
        }
    )
    svc.ui.on_hands({"event": "end"})
    svc.ui.end_run("main")
    # Frame emission attaches frame ID to the hands card and hands_frames without extra cards.
    frames: Any = svc.ui.hands_frames.get("main") or {}
    assert len(frames) >= 1
    fid = next(iter(frames.keys()))
    img_res = client.get(f"/api/hands/main/frames/{fid}.jpg")
    assert img_res.status_code == 200
    assert img_res.content == jpeg
    timeline = svc.ui.get_timeline("main").events
    assert len(timeline) == before_events + 1
    assert timeline[-1]["type"] == "hands"
    assert timeline[-1]["frame"] == fid
    assert timeline[-1]["app"] == "chrome.exe"


# ---------------------------------------------------------------------------
# 5. Free Personal MiniMuse Cloud
# ---------------------------------------------------------------------------


def test_personal_cloud_status_and_tunnel_api(
    server: tuple[TestClient, MuseService, MockLLM], monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = PersonalCloudManager(port=8787)
    st = mgr.status(probe_tailscale=False)
    assert st["port"] == 8787
    assert isinstance(st["lan_urls"], list)
    assert st["tunnel"]["running"] is False

    client, _svc, _llm = server
    res = client.get("/api/personal-cloud/status")
    assert res.status_code == 200
    body = res.json()
    assert "best_url" in body and "tunnel" in body

    stopped = client.post("/api/personal-cloud/stop")
    assert stopped.status_code == 200
    assert stopped.json()["tunnel"]["running"] is False


# ---------------------------------------------------------------------------
# 6. Live Call Mode & /ws/call
# ---------------------------------------------------------------------------


def test_live_call_capabilities_override_and_websocket(
    server: tuple[TestClient, MuseService, MockLLM]
) -> None:
    assert detect_realtime_kind("openai", "gpt-realtime") == "openai_realtime"
    assert detect_realtime_kind("openai", "gpt-4o-realtime-preview") == "openai_realtime"
    assert detect_realtime_kind(
        "gemini", "gemini-2.5-flash-native-audio-preview-12-2025"
    ) == "gemini_live"
    assert detect_realtime_kind("deepseek", "deepseek-chat") == "voice_loop"

    client, svc, llm = server
    caps = client.get("/api/call/capabilities").json()
    assert caps["supported"] is True and caps["voice_loop"] is True

    updated = client.put(
        "/api/call/config", json={"provider": "openai", "model": "gpt-realtime"}
    ).json()
    assert updated["native_realtime"] is True
    assert updated["mode"] == "openai_realtime"
    assert updated["override_active"] is True

    cleared = client.put("/api/call/config", json={"provider": "", "model": ""}).json()
    assert cleared["override_active"] is False

    # Test Universal Hands-Free Call WebSocket turn
    llm.script.append(LLMResponse(content="Loud and clear from MiniMuse!"))
    with client.websocket_connect("/ws/call?thread=main&patience_ms=3500") as ws:
        ws.send_json({"kind": "auth", "token": "secret-watch-token"})
        ready = ws.receive_json()
        assert ready["kind"] == "call_ready"
        assert ready["mode"] == "voice_loop"

        ws.send_json({"kind": "user_text", "text": "Can you hear me?"})
        seen_kinds: list[str] = []
        done_text = ""
        for _ in range(10):
            ev = ws.receive_json()
            seen_kinds.append(ev.get("kind", ""))
            if ev.get("kind") == "assistant_done":
                done_text = str(ev.get("text") or "")
                break
        assert "assistant_done" in seen_kinds
        assert "Loud and clear" in done_text


# ---------------------------------------------------------------------------
# 7. Watch Pairing & Companion REST Endpoints
# ---------------------------------------------------------------------------


def test_watch_code_claim_state_ask_and_approve(
    server: tuple[TestClient, MuseService, MockLLM]
) -> None:
    client, svc, llm = server
    code_resp = client.post("/api/watch/code").json()
    code = code_resp["code"]
    assert len(code) == 6
    assert code_resp["agent_name"] == "MiniMuse"

    # /api/watch/claim works without prior bearer header (unpaired watch!)
    anon = TestClient(client.app)  # type: ignore[arg-type]
    claim = anon.post("/api/watch/claim", json={"code": code})
    assert claim.status_code == 200
    claimed = claim.json()
    assert claimed["ok"] is True
    assert claimed["token"] == "secret-watch-token"
    assert claimed["name"] == "MiniMuse"

    # Re-claiming the same single-use code fails cleanly
    reclaim = anon.post("/api/watch/claim", json={"code": code})
    assert reclaim.status_code in (400, 404)

    # Watch fetches state with claimed bearer token
    watch_headers = {"Authorization": f"Bearer {claimed['token']}"}
    st = anon.get("/api/watch/state", headers=watch_headers).json()
    assert st["agent_name"] == "MiniMuse"
    assert "call" in st

    # Watch queues a hold and approves/finishes it from wrist
    hold = svc.app.holds.open("main", "computer", by="agent", reason="Finish login on computer")
    st2 = anon.get("/api/watch/state", headers=watch_headers).json()
    assert any(h["id"] == hold.id for h in st2["pending_holds"])

    done_hold = anon.post(
        "/api/watch/approve",
        headers=watch_headers,
        json={"id": hold.id, "approved": True, "scope": "once", "kind": "hold"},
    )
    assert done_hold.status_code == 200
    assert done_hold.json()["ok"] is True

    # Watch asks a voice/text question onto main thread
    llm.script.append(LLMResponse(content="Checked on the watch."))
    ask = anon.post(
        "/api/watch/ask",
        headers=watch_headers,
        json={"text": "Status check"},
    )
    assert ask.status_code == 200
    assert ask.json()["ok"] is True
