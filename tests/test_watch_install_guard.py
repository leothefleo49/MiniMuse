"""Verify Wear OS ADB watch detection refuses phones and only installs to real watches."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from nanomuse import __version__
from nanomuse.config import Settings
from nanomuse.server import create_app
from nanomuse.server.service import MuseService


def test_watch_install_refuses_phone_and_selects_real_watch(tmp_path: Path, monkeypatch: Any) -> None:
    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    updates = s.data_dir / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    (updates / f"MiniMuse-{__version__}-wear.apk").write_bytes(b"PK\x03\x04" + b"x" * 60_000)

    # Create a fake adb binary inside platform-tools so _ensure_adb resolves cleanly without network
    pt = s.data_dir / "platform-tools"
    pt.mkdir(parents=True, exist_ok=True)
    fake_adb_name = "adb.exe" if subprocess.os.name == "nt" else "adb"
    fake_adb = pt / fake_adb_name
    fake_adb.write_text("stub", encoding="utf-8")

    installed_targets: list[str] = []
    attached_mode = {"with_watch": False}

    def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if len(cmd) >= 2 and cmd[1] == "mdns":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if len(cmd) >= 2 and cmd[1] == "devices":
            lines = [
                "List of devices attached",
                "192.168.1.50:41234    device product:b6q model:SM_F766B device:b6q",
            ]
            if attached_mode["with_watch"]:
                lines.append("192.168.1.88:38291    device product:wisebl model:SM_R890 device:wisebl")
            return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(lines) + "\n", stderr="")
        if "-s" in cmd and "shell" in cmd:
            target = cmd[cmd.index("-s") + 1]
            shell_cmd = " ".join(cmd[cmd.index("shell") + 1 :])
            if "ro.build.characteristics" in shell_cmd:
                if "192.168.1.50" in target:
                    return subprocess.CompletedProcess(cmd, 0, stdout="phone,nosdcard\n|\nGalaxy Phone\n|\nfalse\n", stderr="")
                if "192.168.1.88" in target:
                    return subprocess.CompletedProcess(cmd, 0, stdout="watch,nosdcard\n|\nGalaxy Watch\n|\ntrue\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "-s" in cmd and "install" in cmd:
            installed_targets.append(cmd[cmd.index("-s") + 1])
            return subprocess.CompletedProcess(cmd, 0, stdout="Success\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    svc = MuseService(s)
    app = create_app(settings=s, service=svc)
    with TestClient(app) as client:
        headers = {"Authorization": f"Bearer {svc.token}"}

        # 1. Phone only attached -> flagged is_watch=False, /api/watch/install MUST refuse
        devs = client.get("/api/watch/devices", headers=headers).json()
        assert devs["devices"][0]["is_watch"] is False

        refused_auto = client.post("/api/watch/install", headers=headers, json={}).json()
        assert refused_auto["ok"] is False
        assert "phone" in refused_auto["error"].lower()
        assert installed_targets == []

        refused_explicit = client.post(
            "/api/watch/install",
            headers=headers,
            json={"target": "192.168.1.50:41234"},
        ).json()
        assert refused_explicit["ok"] is False
        assert "not a wear os watch" in refused_explicit["error"].lower()
        assert installed_targets == []

        # 2. Both phone and watch attached -> selects the watch, never the phone
        attached_mode["with_watch"] = True
        devs2 = client.get("/api/watch/devices", headers=headers).json()
        watch_flags = {d["id"]: d["is_watch"] for d in devs2["devices"]}
        assert watch_flags["192.168.1.50:41234"] is False
        assert watch_flags["192.168.1.88:38291"] is True

        ok_res = client.post("/api/watch/install", headers=headers, json={}).json()
        assert ok_res["ok"] is True
        assert ok_res["target"] == "192.168.1.88:38291"
        assert installed_targets == ["192.168.1.88:38291"]
