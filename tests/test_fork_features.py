"""Tests for MiniMuse fork fixes: native window launch, live browser take-over, and auto-updates."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from nanomuse.agent.holds import Holds
from nanomuse.config import Settings
from nanomuse.server.events import EventBus, Timeline
from nanomuse.server.selfupdate import (
    SelfUpdate,
    _frozen_asset_candidates,
    swap_and_restart,
)
from nanomuse.server.service import MuseService
from nanomuse.server.update import DEFAULT_REPO, UpdateCheck
from nanomuse.server.webui import WebUI
from nanomuse.tools.browser import Browser, BrowserFrame
from nanomuse.tools.browser_backends import BrowserBackend, BrowserProfile


class _DummyBackend(BrowserBackend):
    kind = "playwright"
    profile = BrowserProfile("desktop", 1280, 900)

    def __init__(self) -> None:
        self._open = True
        self._url = "https://example.com"
        self._title = "Example"
        self.reloaded = False

    @property
    def open(self) -> bool:
        return self._open

    async def ensure(self) -> None:
        self._open = True

    async def goto(self, url: str) -> None:
        self._url = url

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        if "location.reload" in js:
            self.reloaded = True
            return None
        return []

    async def click_at(self, x: float, y: float) -> None:
        pass

    async def type_text(self, text: str) -> None:
        pass

    async def press(self, key: str) -> None:
        pass

    async def scroll(self, dy: float) -> None:
        pass

    async def back(self) -> None:
        pass

    async def screenshot(self, quality: int = 55) -> bytes:
        return b"\xff\xd8\xff\xd9"

    async def url(self) -> str:
        return self._url

    async def title(self) -> str:
        return self._title

    async def settle(self, timeout_ms: int) -> None:
        pass

    async def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        return {"status": 200, "headers": {}, "body": "", "url": url}

    async def set_profile(self, profile: BrowserProfile) -> None:
        self.profile = profile

    async def close(self) -> None:
        self._open = False


def test_update_check_uses_fork_repo_by_default() -> None:
    check = UpdateCheck(True, current="1.0.11")
    assert check.repo == DEFAULT_REPO == "leothefleo49/MiniMuse"
    assert "leothefleo49/MiniMuse" in check.url


def test_update_check_queries_github_for_fork(monkeypatch: Any) -> None:
    requested: list[str] = []

    class FakeResp:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {
                "tag_name": "v9.9.9",
                "html_url": "https://github.com/leothefleo49/MiniMuse/releases/tag/v9.9.9",
            }

    class FakeClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def get(self, url: str, **_: Any) -> FakeResp:
            requested.append(url)
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    view = asyncio.run(UpdateCheck(True, current="1.0.11").view(force=True))
    assert view["latest"] == "9.9.9"
    assert view["newer"] is True
    assert requested == ["https://api.github.com/repos/leothefleo49/MiniMuse/releases/latest"]


def test_selfupdate_download_to_streams_async(tmp_path: Path, monkeypatch: Any) -> None:
    class FakeStreamResp:
        def raise_for_status(self) -> None:
            pass

        async def __aenter__(self) -> FakeStreamResp:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def aiter_bytes(self):  # type: ignore[no-untyped-def]
            yield b"hello-"
            yield b"minimuse"

    class FakeClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        def stream(self, method: str, url: str, **_: Any) -> FakeStreamResp:
            return FakeStreamResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    upd = SelfUpdate(UpdateCheck(True, current="1.0.11"))
    dest = tmp_path / "MiniMuse.bin"
    asyncio.run(upd._download_to("https://example.com/MiniMuse.bin", dest))
    assert dest.read_bytes() == b"hello-minimuse"


def test_frozen_asset_candidates_include_expected_names() -> None:
    names = _frozen_asset_candidates("1.0.12")
    assert any("1.0.12" in n for n in names)


def test_swap_and_restart_preserves_app_mode(tmp_path: Path, monkeypatch: Any) -> None:
    exe = tmp_path / "MiniMuse.exe"
    new = tmp_path / "MiniMuse.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")
    monkeypatch.setattr("sys.argv", ["MiniMuse.exe"])
    spawned: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd: list[str], **_: Any) -> None:
            spawned.append(cmd)

    with (
        patch("nanomuse.server.selfupdate.subprocess.Popen", FakePopen),
        patch("_thread.interrupt_main"),
    ):
        swap_and_restart(exe, new)

    assert spawned
    script = Path(spawned[0][-1])
    text = script.read_text(encoding="utf-8", errors="replace")
    assert " serve" not in text


def test_browser_instant_take_over_and_reload() -> None:
    backend = _DummyBackend()
    holds = Holds()
    frames: list[BrowserFrame] = []
    tool = Browser(holds=holds, backend_factory=lambda: backend, on_frame=frames.append)

    async def scenario() -> None:
        # Hold the lock like an active long navigation, then request take_over: must not hang.
        await tool.lock.acquire()
        holds.open("main", "browser", by="user", reason="test")
        try:
            res = await tool.user_action("take_over", thread="main")
            assert res.get("pending") is True
        finally:
            tool.lock.release()
            holds.release("main", "browser")

        rel = await tool.user_action("reload", thread="main")
        assert backend.reloaded is True
        assert rel["url"] == "https://example.com"

    asyncio.run(scenario())
    assert any(f.action == "You reloaded the page" and f.by_user for f in frames)


def test_webui_keeps_live_status_during_user_browser_control(tmp_path: Path) -> None:
    tl = Timeline("main", tmp_path / "tl.jsonl")
    ui = WebUI(EventBus(), lambda _: tl)
    ui.on_browser_frame(
        BrowserFrame(
            "https://example.com",
            "Example",
            "Opened example.com",
            b"\xff\xd8\xff\xd9",
            False,
            "main",
        )
    )
    ui.end_run("main")
    card = next(e for e in tl.events if e["type"] == "browser")
    assert card["status"] == "done"

    ui.on_browser_frame(
        BrowserFrame(
            "https://example.com", "Example", "You have the page", b"\xff\xd8\xff\xd9", True, "main"
        )
    )
    card_after = next(e for e in tl.events if e["type"] == "browser")
    assert card_after["status"] == "live"


def test_runtime_entry_uses_native_window_instead_of_browser(
    monkeypatch: Any, tmp_path: Path
) -> None:
    spec = importlib.util.spec_from_file_location(
        "minimuse_runtime_entry",
        Path(__file__).resolve().parents[1] / "scripts" / "desktop-app" / "runtime_entry.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    created: list[tuple[str, str]] = []
    started: list[dict[str, Any]] = []

    class FakeEvents:
        def __init__(self) -> None:
            self.closed: list[Any] = []

        def __iadd__(self, cb: Any) -> FakeEvents:
            self.closed.append(cb)
            return self

    class FakeWindow:
        def __init__(self) -> None:
            self.events = FakeEvents()

        def destroy(self) -> None:
            pass

    class FakeWebview:
        def create_window(self, title: str, url: str, **_: Any) -> FakeWindow:
            created.append((title, url))
            return FakeWindow()

        def start(self, **kwargs: Any) -> None:
            started.append(kwargs)

    monkeypatch.delenv("MINIMUSE_NO_BROWSER", raising=False)
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.delenv("NANOMUSE_OPEN_BROWSER", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "webview", FakeWebview())

    assert mod._run_native_window("http://127.0.0.1:8787/", storage_dir=tmp_path) is True
    assert created == [("MiniMuse", "http://127.0.0.1:8787/")]
    assert started and started[0]["private_mode"] is False


def test_service_browser_control_accepts_reload(settings: Settings) -> None:
    backend = _DummyBackend()
    service = MuseService(settings)
    if "browser" in service.app.tools:
        service.app.tools.remove("browser")
    service.app.tools.add(
        Browser(
            holds=service.app.holds,
            backend_factory=lambda: backend,
            on_frame=service.ui.on_browser_frame,
        )
    )

    async def run() -> None:
        await service.browser_control("main", {"action": "navigate", "url": "https://example.com"})
        res = await service.browser_control("main", {"action": "reload"})
        assert backend.reloaded is True
        assert res["url"] == "https://example.com"

    asyncio.run(run())


def test_selfupdate_install_uses_user_flag_when_purelib_not_writable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], **_: Any) -> SimpleNamespace:
        recorded.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setattr(sysconfig, "get_paths", lambda: {"purelib": "/readonly/site-packages"})
    monkeypatch.setattr(os, "access", lambda *_: False)
    with patch("subprocess.run", fake_run):
        SelfUpdate("1.0.12")._install(tmp_path / "nanomuse.whl")

    pip_cmds = [c for c in recorded if "pip" in c]
    assert pip_cmds and "--user" in pip_cmds[0]

    recorded.clear()
    monkeypatch.setattr(sys, "prefix", f"{sys.base_prefix}-venv")
    with patch("subprocess.run", fake_run):
        SelfUpdate("1.0.12")._install(tmp_path / "nanomuse.whl")

    pip_cmds = [c for c in recorded if "pip" in c]
    assert pip_cmds and "--user" not in pip_cmds[0]


def test_runtime_entry_refuses_stale_version_on_same_port(tmp_path: Path, monkeypatch: Any) -> None:
    spec = importlib.util.spec_from_file_location(
        "minimuse_runtime_entry_app",
        Path(__file__).resolve().parents[1] / "scripts" / "desktop-app" / "runtime_entry.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import nanomuse
    import nanomuse.cli as cli_mod
    import nanomuse.server as srv_mod

    fake_settings = SimpleNamespace(
        server=SimpleNamespace(port=8787),
        data_dir=tmp_path,
    )
    served_ports: list[int] = []
    opened_urls: list[str] = []

    def fake_serve(
        settings: Any,
        *,
        host: str,
        port: int,
        print_qr: bool,
        on_server: Any,
    ) -> None:
        served_ports.append(port)
        on_server(SimpleNamespace(should_exit=False))

    def fake_window(url: str, *, storage_dir: Any = None, on_close: Any = None) -> bool:
        opened_urls.append(url)
        if on_close is not None:
            on_close()
        return True

    monkeypatch.setattr(cli_mod, "_settings", lambda *_: fake_settings)
    monkeypatch.setattr(srv_mod, "port_taken", lambda *_: "nanoMuse 1.0.9")
    monkeypatch.setattr(srv_mod, "serve", fake_serve)
    monkeypatch.setattr(mod, "_install_app_entry", lambda: None)
    monkeypatch.setattr(mod, "_next_free_port", lambda start: start)
    monkeypatch.setattr(mod, "_wait_for_health", lambda *_, **__: True)
    monkeypatch.setattr(mod, "_app_url", lambda port: f"http://127.0.0.1:{port}/")
    monkeypatch.setattr(mod, "_run_native_window", fake_window)

    mod._app_mode()
    assert served_ports == [8788]
    assert opened_urls == ["http://127.0.0.1:8788/"]

    # Matching version attaches cleanly to the existing server without spawning another.
    served_ports.clear()
    opened_urls.clear()
    monkeypatch.setattr(srv_mod, "port_taken", lambda *_: f"nanoMuse {nanomuse.__version__}")
    with pytest.raises(SystemExit) as exc_info:
        mod._app_mode()
    assert exc_info.value.code == 0
    assert served_ports == []
    assert opened_urls == ["http://127.0.0.1:8787/"]


def test_manifest_and_mm_icons_served_when_ui_unset(tmp_path: Path) -> None:
    """Fresh installs have _ui_view() == None; /manifest.webmanifest and MM icons must serve cleanly."""
    from fastapi.testclient import TestClient

    from nanomuse.server import create_app

    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    svc = MuseService(s)
    assert svc._ui_view() is None
    app = create_app(settings=s, service=svc)
    with TestClient(app, raise_server_exceptions=True) as client:
        mf_resp = client.get("/manifest.webmanifest")
        assert mf_resp.status_code == 200
        mf = mf_resp.json()
        assert mf["name"] == "MiniMuse"
        purposes = {icon.get("purpose") for icon in mf.get("icons", [])}
        assert "any" in purposes and "maskable" in purposes

        svg_resp = client.get("/icon.svg")
        assert svg_resp.status_code == 200
        assert "M14.5 70" in svg_resp.text
        assert "M57.5 70" in svg_resp.text

        mask_resp = client.get("/icon-maskable-512.png")
        assert mask_resp.status_code == 200
        assert len(mask_resp.content) > 1000
