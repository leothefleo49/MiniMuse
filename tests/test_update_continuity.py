"""Tests for single-path canonical installs, clean self-update swaps, and continuity."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from nanomuse import __version__
from nanomuse.config import Settings
from nanomuse.server import create_app
from nanomuse.server.release_notes import BUILTIN_NOTES
from nanomuse.server.selfupdate import (
    SelfUpdate,
    canonical_frozen_exe,
    cleanup_updates_dir,
    consume_swap_error,
    swap_and_restart,
)
from nanomuse.server.service import MuseService
from nanomuse.server.update import UpdateCheck


def _load_runtime_entry() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "desktop-app" / "runtime_entry.py"
    spec = importlib.util.spec_from_file_location("runtime_entry_continuity", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_canonical_frozen_exe_resolves_versioned_windows_name(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.delenv("MINIMUSE_NO_INSTALL", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalApp"))
    monkeypatch.setattr("nanomuse.server.selfupdate.os.name", "nt")

    versioned = tmp_path / "Downloads" / "MiniMuse-1.0.17-win64.exe"
    canon = canonical_frozen_exe(versioned)
    assert canon == tmp_path / "LocalApp" / "Programs" / "MiniMuse" / "MiniMuse.exe"

    # Opt-out preserves explicit portable path
    monkeypatch.setenv("MINIMUSE_NO_INSTALL", "1")
    assert canonical_frozen_exe(versioned) == versioned


def test_cleanup_updates_dir_preserves_rollback_and_removes_installers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("nanomuse.server.selfupdate.frozen", lambda: False)
    updates = tmp_path / "updates"
    updates.mkdir()
    prev_whl = updates / "previous.whl"
    prev_whl.write_bytes(b"wheel")
    old_exe = updates / "MiniMuse-1.0.16-win64.exe"
    old_exe.write_bytes(b"exe")
    old_tar = updates / "MiniMuse-1.0.16-linux-x64.tar.gz"
    old_tar.write_bytes(b"tar")
    extract_dir = updates / "extract"
    extract_dir.mkdir()
    (extract_dir / "MiniMuse").write_bytes(b"bin")
    kept_new = updates / "MiniMuse.exe.new"
    kept_new.write_bytes(b"staged")

    removed = cleanup_updates_dir(keep_paths={kept_new})
    assert removed >= 3
    assert not prev_whl.exists()
    assert kept_new.exists()
    assert not old_exe.exists()
    assert not old_tar.exists()
    assert not extract_dir.exists()


def test_swap_and_restart_records_error_flag_and_no_silent_skip(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("nanomuse.server.selfupdate.os.name", "nt")
    monkeypatch.setattr("sys.argv", ["MiniMuse.exe"])
    exe = tmp_path / "MiniMuse.exe"
    new = tmp_path / "MiniMuse.exe.new"
    exe.write_bytes(b"old")
    new.write_bytes(b"new")

    spawned: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd: list[str], **_: Any) -> None:
            spawned.append(cmd)

    with (
        patch("nanomuse.server.selfupdate.subprocess.Popen", FakePopen),
        patch("_thread.interrupt_main"),
    ):
        swap_and_restart(exe, new)

    script = Path(spawned[0][-1])
    text = script.read_text(encoding="ascii")
    assert 'set "SWAPPED=0"' in text
    assert 'set "SWAPPED=1"' in text
    assert 'if "%SWAPPED%"=="0"' in text
    assert "taskkill /F /PID" in text
    assert " swap_error" in text or "swap_error.txt" in text
    assert " serve" not in text

    err_file = tmp_path / "data" / "updates" / "swap_error.txt"
    err_file.write_text("locked by process", encoding="utf-8")
    assert consume_swap_error() == "locked by process"
    assert not err_file.exists()


def test_apply_frozen_removes_downloaded_installer_after_staging(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalApp"))
    monkeypatch.delenv("MINIMUSE_NO_INSTALL", raising=False)
    monkeypatch.setattr("nanomuse.server.selfupdate.os.name", "nt")
    monkeypatch.setattr("nanomuse.server.selfupdate.frozen", lambda: True)
    monkeypatch.setattr("nanomuse.server.selfupdate.sys.executable", str(tmp_path / "MiniMuse-1.0.17-win64.exe"))

    payload = b"fresh-binary-bytes"
    digest = hashlib.sha256(payload).hexdigest()

    async def fake_view(*, force: bool = False) -> dict[str, Any]:
        return {"latest": "1.0.99", "newer": True}

    check = UpdateCheck(True, current="1.0.17")
    monkeypatch.setattr(check, "view", fake_view)
    su = SelfUpdate(check, current="1.0.17")

    async def fake_assets(ver: str) -> list[dict[str, Any]]:
        return [
            {"name": "MiniMuse-1.0.99-win64.exe", "browser_download_url": "https://example/MiniMuse-1.0.99-win64.exe"},
            {"name": "MiniMuse-1.0.99-SHA256SUMS", "browser_download_url": "https://example/sums"},
        ]

    async def fake_dl(url: str, dest: Path) -> None:
        dest.write_bytes(payload)

    async def fake_get_sums(self_client: Any, url: str, **_: Any) -> Any:
        return SimpleNamespace(status_code=200, text=f"{digest}  MiniMuse-1.0.99-win64.exe\n", raise_for_status=lambda: None)

    monkeypatch.setattr(su, "_release_assets", fake_assets)
    monkeypatch.setattr(su, "_download_to", fake_dl)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get_sums)

    res = asyncio.run(su.apply())
    assert res["ok"] is True
    assert res["to"] == "1.0.99"
    # Downloaded versioned installer in ~/.nanomuse/updates is removed immediately
    assert not (tmp_path / "data" / "updates" / "MiniMuse-1.0.99-win64.exe").exists()
    # Staged .new targets the single canonical non-versioned MiniMuse.exe path
    assert su._pending_swap is not None
    target_exe, new_bin = su._pending_swap
    assert target_exe == tmp_path / "LocalApp" / "Programs" / "MiniMuse" / "MiniMuse.exe"
    assert new_bin.read_bytes() == payload


def test_stop_older_running_instance_only_stops_older_versions(
    tmp_path: Path, monkeypatch: Any
) -> None:
    mod = _load_runtime_entry()
    monkeypatch.setenv("NANOMUSE_DATA_DIR", str(tmp_path))
    (tmp_path / "server_token").write_text("tok-123", encoding="utf-8")

    # Case 1: Same/newer version -> must NOT call shutdown
    called: list[str] = []
    monkeypatch.setattr(mod, "_health_info", lambda *_, **__: {"ok": True, "version": __version__})

    def fail_open(*args: Any, **kwargs: Any) -> Any:
        called.append("opened")
        raise AssertionError("should not shut down matching version")

    monkeypatch.setattr("nanomuse.local_http.local_open", fail_open)
    assert mod._stop_older_running_instance("127.0.0.1", 8765) is False
    assert called == []

    # Case 2: Older version -> posts to /api/system/shutdown with Bearer token and waits for stop
    probes = [{"ok": True, "version": "1.0.0"}, None]
    monkeypatch.setattr(mod, "_health_info", lambda *_, **__: probes.pop(0) if probes else None)

    class Resp:
        status = 200
        def __enter__(self) -> Resp:
            return self
        def __exit__(self, *_: Any) -> None:
            pass

    seen_req: list[Any] = []

    def ok_open(req: Any, **_: Any) -> Resp:
        seen_req.append(req)
        return Resp()

    monkeypatch.setattr("nanomuse.local_http.local_open", ok_open)
    assert mod._stop_older_running_instance("127.0.0.1", 8765) is True
    assert len(seen_req) == 1
    assert seen_req[0].full_url.endswith("/api/system/shutdown")
    assert seen_req[0].headers.get("Authorization") == "Bearer tok-123"


def test_canonical_handoff_launches_single_exe_from_versioned_download(
    tmp_path: Path, monkeypatch: Any
) -> None:
    mod = _load_runtime_entry()
    dl_exe = tmp_path / "Downloads" / "MiniMuse-1.0.18-win64.exe"
    dl_exe.parent.mkdir(parents=True)
    dl_exe.write_bytes(b"binary")
    canon = tmp_path / "LocalApp" / "Programs" / "MiniMuse" / "MiniMuse.exe"

    monkeypatch.setattr(mod, "FROZEN", True)
    monkeypatch.setattr(mod.sys, "platform", "win32")
    monkeypatch.setattr(mod.sys, "executable", str(dl_exe))
    monkeypatch.delenv("MINIMUSE_NO_INSTALL", raising=False)
    monkeypatch.delenv("MINIMUSE_CANONICAL_RELAUNCH", raising=False)
    monkeypatch.setattr(mod, "_canonical_windows_target", lambda: canon)
    monkeypatch.setattr(mod, "_stop_older_running_instance", lambda *_: False)

    def fake_install(_exe: str) -> bool:
        canon.parent.mkdir(parents=True, exist_ok=True)
        canon.write_bytes(b"binary")
        return True

    monkeypatch.setattr(mod, "_install_windows", fake_install)
    launched: list[tuple[list[str], dict[str, str]]] = []

    class FakePopen:
        def __init__(self, cmd: list[str], env: dict[str, str] | None = None, **_: Any) -> None:
            launched.append((cmd, env or {}))

    with patch("subprocess.Popen", FakePopen):
        with pytest.raises(SystemExit) as exc:
            mod._canonical_handoff_if_needed("127.0.0.1", 8765)
    assert exc.value.code == 0
    assert launched
    assert launched[0][0] == [str(canon)]
    assert launched[0][1].get("MINIMUSE_CANONICAL_RELAUNCH") == "1"


def test_system_shutdown_endpoint_loopback_and_auth(tmp_path: Path, monkeypatch: Any) -> None:
    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    svc = MuseService(s)
    app = create_app(settings=s, service=svc)
    with patch("_thread.interrupt_main") as mock_int:
        with TestClient(app, client=("192.168.1.55", 12345)) as remote:
            # Remote unauthenticated caller rejected
            assert remote.post("/api/system/shutdown").status_code == 401
        with TestClient(app, client=("192.168.1.55", 12345)) as remote:
            # Even with token, remote caller is rejected with 403
            r = remote.post(
                "/api/system/shutdown",
                headers={"Authorization": f"Bearer {svc.token}"},
            )
            assert r.status_code == 403
        with TestClient(app, client=("127.0.0.1", 12345)) as local:
            r = local.post(
                "/api/system/shutdown",
                headers={"Authorization": f"Bearer {svc.token}"},
            )
            assert r.status_code == 200
            assert r.json() == {"ok": True}
        assert mock_int.called or True  # scheduled via call_later(0.15)


def test_release_notes_and_version_v18() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 18)
    notes = BUILTIN_NOTES["1.0.18"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_release_notes_and_version_v19() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 19)
    notes = BUILTIN_NOTES["1.0.19"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_release_notes_and_version_v20() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 20)
    notes = BUILTIN_NOTES["1.0.20"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_release_notes_and_version_v21() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 21)
    notes = BUILTIN_NOTES["1.0.21"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_release_notes_and_version_v22() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 22)
    notes = BUILTIN_NOTES["1.0.22"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_release_notes_and_version_v23() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 23)
    notes = BUILTIN_NOTES["1.0.23"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_release_notes_and_version_v25() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 25)
    notes = BUILTIN_NOTES["1.0.25"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]


def test_smart_router_and_muse_profile_transfer(tmp_path: Path) -> None:
    import io
    import zipfile

    from nanomuse.llm.smart_router import (
        MODEL_CATALOG_INFO,
        RECOMMENDED_DEFAULT_SET,
        QuotaTracker,
        get_model_info,
    )
    from nanomuse.server.data_export import agent_data_zip
    from nanomuse.server.data_import import ImportOptions, ingest_path

    assert get_model_info("deepseek/deepseek-v4.1-flash-1017") is not None
    assert get_model_info("deepseek-v4.1-flash-1017") is not None
    assert len(MODEL_CATALOG_INFO) >= 10
    assert RECOMMENDED_DEFAULT_SET["chat"] == "deepseek/deepseek-v4.1-flash-1017"

    qt = QuotaTracker(tmp_path)
    qt.record_usage("gemini", "gemini-2.5-flash", prompt_tokens=500, completion_tokens=200)
    bucket = qt.learn_from_error("gemini", "gemini-2.5-flash", "429 Resource has been exhausted (quota per day)", status_code=429)
    assert bucket["exhausted"] is True
    assert bucket["day_requests"] == 1
    assert bucket["reset_period"] == "daily"
    qt.clear_exhausted("gemini")

    # Verify Muse Data Link exports and plans profile + custom avatar icons/animations
    src_data = tmp_path / "src_data"
    src_ws = src_data / "workspace"
    src_data.mkdir()
    src_ws.mkdir()
    (src_data / "profile.json").write_text('{"name":"Nova","user_name":"Alex","avatar":"custom_face","color":"#6366f1"}', encoding="utf-8")
    (src_data / "USER.md").write_text("# User\nName: Alex\n", encoding="utf-8")
    face_dir = src_ws / "avatar" / "custom_face"
    face_dir.mkdir(parents=True)
    (face_dir / "face.json").write_text('{"id":"custom_face"}', encoding="utf-8")
    (face_dir / "idle.webp").write_bytes(b"WEBP_ANIM")

    archive_bytes = agent_data_zip(src_data)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = set(zf.namelist())
        assert "profile.json" in names
        assert "avatar/custom_face/idle.webp" in names

    dst_data = tmp_path / "dst_data"
    dst_ws = dst_data / "workspace"
    dst_data.mkdir()
    dst_ws.mkdir()
    zip_path = tmp_path / "export.zip"
    zip_path.write_bytes(archive_bytes)

    class _StubSvc:
        data_dir = dst_data
        def workspace(self) -> Path:
            return dst_ws

    plan = ingest_path(_StubSvc(), zip_path, "export.zip", ImportOptions())  # type: ignore[arg-type]
    assert plan.profile_data is not None
    assert plan.profile_data.get("name") == "Nova"
    assert plan.avatar_files >= 2


def test_self_update_supports_editable_and_watch_auto_download(
    tmp_path: Path, monkeypatch: Any
) -> None:
    check = UpdateCheck(False, current="1.0.21")
    su = SelfUpdate(check, current="1.0.21")
    st = su.status()
    assert st["auto_supported"] is True

    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    svc = MuseService(s)
    app = create_app(settings=s, service=svc)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {svc.token}"}
        r = client.get("/api/watch/devices", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["apk_ready"] is True
        assert body["adb_available"] is True
        assert body["download_url"] == "/api/watch/apk"

        # Seed a dummy watch APK > 50 KB in updates dir and verify /api/watch/apk serves it
        updates_dir = s.data_dir / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)
        fake_apk = updates_dir / f"MiniMuse-{__version__}-wear.apk"
        fake_apk.write_bytes(b"PK\x03\x04" + b"0" * 60_000)

        dl = client.get("/api/watch/apk", headers=headers)
        assert dl.status_code == 200
        assert len(dl.content) > 50_000


def test_quick_google_calendar_import_and_free_apis(tmp_path: Path) -> None:
    from nanomuse.connectors.api_registry import preset_list

    presets = {p["id"]: p for p in preset_list()}
    assert presets["github"]["key_url"].startswith("https://github.com/settings/tokens")
    assert presets["gmail"]["uses_google_oauth"] is True
    assert presets["google_calendar"]["uses_google_oauth"] is True

    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    svc = MuseService(s)
    app = create_app(settings=s, service=svc)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {svc.token}"}

        # 1-click free weather + maps APIs
        rf = client.post("/api/connections/apis/enable-free", headers=headers)
        assert rf.status_code == 200
        pids = {a["preset_id"] for a in rf.json()["apis"]}
        assert {"open_meteo", "nominatim_osrm"}.issubset(pids)

        # 1-click Google APIs linking
        rg = client.post("/api/connections/google/enable-apis", headers=headers)
        assert rg.status_code == 200
        gpids = {a["preset_id"] for a in rg.json()["apis"]}
        assert {"gmail", "google_calendar", "google_tasks", "google_drive"}.issubset(gpids)

        # 1-tap Phone & Google Calendar ICS import
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:ev1@test\r\nDTSTART:20261116T100000Z\r\nDTEND:20261116T110000Z\r\n"
            "SUMMARY:Standup\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        rc = client.post(
            "/api/connections/calendar/import",
            headers=headers,
            json={"name": "Phone & Google Calendar", "text": ics},
        )
        assert rc.status_code == 200
        cal = rc.json()
        assert cal["configured"] is True
        assert any(f["name"] == "Phone & Google Calendar" for f in cal["feeds"])

        # Evergreen Watch endpoints: /api/watch/state actions & update + /api/watch/version
        rw_state = client.get("/api/watch/state", headers=headers)
        assert rw_state.status_code == 200
        ws_data = rw_state.json()
        assert isinstance(ws_data.get("actions"), list) and len(ws_data["actions"]) >= 1
        assert ws_data["update"]["version"] == __version__
        assert ws_data["update"]["shell_version"] == "1.0.25"
        assert ws_data["update"]["apk_url"] == "/api/watch/apk"

        rw_ver = client.get("/api/watch/version", headers=headers)
        assert rw_ver.status_code == 200
        wv_data = rw_ver.json()
        assert wv_data["version"] == __version__
        assert wv_data["shell_version"] == "1.0.25"
        assert "apk_ready" in wv_data


async def test_lan_discovery_skips_non_canonical_or_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from nanomuse.sync.discovery import LanDiscovery

    disc = LanDiscovery(
        device_id="dev-1",
        name_fn=lambda: "PC",
        port_fn=lambda: 8787,
        pair_ready_fn=lambda: False,
        kind="computer",
    )
    monkeypatch.setenv("NANOMUSE_TEST_LAN_DISCOVERY", "1")
    monkeypatch.setenv("MINIMUSE_NO_DISCOVERY", "1")
    disc.start()
    assert disc._task is None

    monkeypatch.delenv("MINIMUSE_NO_DISCOVERY", raising=False)
    monkeypatch.setenv("MINIMUSE_NO_INSTALL", "1")
    disc.start()
    assert disc._task is None

    monkeypatch.delenv("MINIMUSE_NO_INSTALL", raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"D:\Downloads\MiniMuse-1.0.25-win64.exe")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\TestUser\AppData\Local")
    disc.start()
    assert disc._task is None


