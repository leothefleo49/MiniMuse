"""The automatic watch install flow, against a stateful fake `adb` that behaves like a real watch.

A real watch only advertises its *pairing* service while "Pair new device with pairing code" is open
on it, and refuses `adb connect` until this computer has paired once. These tests model exactly that,
so the flow is checked end to end: discover -> (code only) pair -> connect -> classify -> install.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse import __version__
from nanomuse.config import Settings
from nanomuse.server import create_app, watch_discovery
from nanomuse.server.service import MuseService

PHONE_ID = "adb-PHONE0SERIAL-Ab12cD._adb-tls-connect._tcp"
PHONE_HP = "192.168.1.30:35347"
WATCH_HOST = "192.168.1.77"
WATCH_CONNECT = f"{WATCH_HOST}:38291"
WATCH_PAIR = f"{WATCH_HOST}:40123"
CODE = "123456"

PROPS = {
    "phone": "phone,nosdcard\n|\nSM_F766U\n|\nfalse\n|\nPHONE0SERIAL\n",
    "watch": "watch,nosdcard\n|\nSM-R895F\n|\ntrue\n|\nWATCH0SERIAL\n",
}


class World:
    """What is on the network, and what a fake adb answers for it."""

    def __init__(self) -> None:
        self.attached: dict[str, str] = {}  # adb id -> "phone" | "watch"
        self.unauthorized: set[str] = set()
        self.phone_on_lan = False
        self.watch_connect_visible = False  # wireless debugging on
        self.watch_pairing_open = False  # "Pair new device with pairing code" showing
        self.paired = False
        self.legacy_watch: str | None = None  # "host:5555" of an old-style watch
        self.installed: list[str] = []
        self.commands: list[list[str]] = []
        self.installed_version = "0.0.1"

    def mdns_text(self) -> str:
        rows = ["List of discovered mdns services"]
        if self.phone_on_lan:
            rows.append(f"adb-PHONE0SERIAL-Ab12cD\t_adb-tls-connect._tcp\t{PHONE_HP}")
        if self.watch_connect_visible:
            rows.append(f"adb-WATCH0SERIAL-abc123\t_adb-tls-connect._tcp\t{WATCH_CONNECT}")
        if self.watch_pairing_open:
            rows.append(f"adb-WATCH0SERIAL-abc123\t_adb-tls-pairing._tcp\t{WATCH_PAIR}")
        return "\n".join(rows) + "\n"

    def run(self, cmd: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(cmd))
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")  # noqa: E731
        verb = cmd[1] if len(cmd) > 1 else ""
        if verb == "mdns":
            return ok(self.mdns_text())
        if verb == "devices":
            rows = ["List of devices attached"]
            for dev_id, kind in self.attached.items():
                state = "unauthorized" if dev_id in self.unauthorized else "device"
                model = "SM_F766U" if kind == "phone" else "SM_R895F"
                rows.append(f"{dev_id}    {state} product:x model:{model} device:x")
            return ok("\n".join(rows) + "\n")
        if verb == "pair":
            hp, code = cmd[2], cmd[3]
            if self.watch_pairing_open and hp == WATCH_PAIR and code == CODE:
                self.paired = True
                self.watch_pairing_open = False
                return ok(f"Successfully paired to {hp} [guid=adb-WATCH0SERIAL-abc123]\n")
            return ok("Failed: Wrong password or password and server are not in sync\n")
        if verb == "connect":
            hp = cmd[2]
            if hp == WATCH_CONNECT and self.watch_connect_visible and self.paired:
                self.attached[hp] = "watch"
                return ok(f"connected to {hp}\n")
            if self.legacy_watch and hp == self.legacy_watch:
                self.attached[hp] = "watch"
                return ok(f"connected to {hp}\n")
            return ok(f"failed to connect to '{hp}': Connection refused\n")
        if "-s" in cmd:
            dev = cmd[cmd.index("-s") + 1]
            if "install" in cmd:
                self.installed.append(dev)
                return ok("Success\n")
            if "shell" in cmd:
                shell = " ".join(cmd[cmd.index("shell") + 1 :])
                if "ro.build.characteristics" in shell:
                    return ok(PROPS[self.attached.get(dev, "phone")])
                if "dumpsys" in shell:
                    return ok(f"    versionName={self.installed_version}\n")
        return ok("")


@pytest.fixture()
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    s = Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws")
    (s.data_dir / "updates").mkdir(parents=True, exist_ok=True)
    (s.data_dir / "updates" / f"MiniMuse-{__version__}-wear.apk").write_bytes(b"PK\x03\x04" + b"x" * 60_000)
    pt = s.data_dir / "platform-tools"
    pt.mkdir(parents=True, exist_ok=True)
    (pt / ("adb.exe" if subprocess.os.name == "nt" else "adb")).write_text("stub", encoding="utf-8")

    world = World()
    monkeypatch.setattr(subprocess, "run", world.run)
    monkeypatch.setattr(watch_discovery, "local_ipv4_addresses", lambda: [])
    monkeypatch.setattr(watch_discovery, "lan_neighbors", lambda own=(): [])
    monkeypatch.setattr(watch_discovery, "probe_tcp_open", lambda hosts, port=5555, timeout_s=0.5: [])
    monkeypatch.setattr("time.sleep", lambda _s: None)

    svc = MuseService(s)
    app = create_app(settings=s, service=svc)
    headers = {"Authorization": f"Bearer {svc.token}"}
    with TestClient(app) as client:
        yield world, client, headers, app, s


def _install(client: TestClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    return client.post("/api/watch/install", headers=headers, json=body).json()


# ------------------------------------------------------------------------------ the happy paths
def test_only_the_code_is_needed_when_the_watch_shows_its_pairing_screen(rig) -> None:  # noqa: ANN001
    world, client, headers, _app, s = rig
    world.phone_on_lan = True
    world.attached[PHONE_ID] = "phone"
    world.watch_connect_visible = True
    world.watch_pairing_open = True

    res = _install(client, headers, pair_code=CODE)  # no IP, no port

    assert res["ok"] is True, res
    assert res["target"] == WATCH_CONNECT
    assert world.installed == [WATCH_CONNECT]  # the watch, never the phone
    assert any(c[1:3] == ["pair", WATCH_PAIR] for c in world.commands)  # host:port came from discovery
    saved = json.loads((s.data_dir / "watch_target.json").read_text(encoding="utf-8"))
    assert saved["serial"] == "WATCH0SERIAL"  # remembered by identity, not only by a port
    assert saved["host"] == WATCH_HOST


def test_an_already_paired_watch_is_found_and_installed_with_no_input_at_all(rig) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.watch_connect_visible = True
    world.paired = True

    res = _install(client, headers)

    assert res["ok"] is True, res
    assert world.installed == [WATCH_CONNECT]


def test_the_connect_service_that_shows_up_late_is_waited_for(rig, monkeypatch) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.watch_pairing_open = True
    world.watch_connect_visible = False  # only the pairing screen is up at first
    polls = {"n": 0}
    original = world.mdns_text

    def mdns_text() -> str:
        polls["n"] += 1
        if polls["n"] >= 3:
            world.watch_connect_visible = True  # appears a moment after pairing
        return original()

    world.mdns_text = mdns_text  # type: ignore[method-assign]
    res = _install(client, headers, pair_code=CODE)
    assert res["ok"] is True, res
    assert world.installed == [WATCH_CONNECT]


def test_an_old_debug_over_wifi_watch_on_port_5555_is_found(rig, monkeypatch) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.legacy_watch = "192.168.1.90:5555"
    monkeypatch.setattr(watch_discovery, "lan_neighbors", lambda own=(): ["192.168.1.90", "192.168.1.91"])
    monkeypatch.setattr(
        watch_discovery, "probe_tcp_open", lambda hosts, port=5555, timeout_s=0.5: [h for h in hosts if h == "192.168.1.90"]
    )

    res = _install(client, headers)

    assert res["ok"] is True, res
    assert res["target"] == "192.168.1.90:5555"
    assert world.installed == ["192.168.1.90:5555"]


# ------------------------------------------------------------------------------ clear reasons, no guessing
def test_nothing_on_the_network_says_so_and_how_to_turn_it_on(rig) -> None:  # noqa: ANN001
    _world, client, headers, *_ = rig
    res = _install(client, headers)
    assert res["ok"] is False
    assert res["code"] == "no_watch"
    assert "Build" in res["error"] and "Wireless debugging" in res["error"]


def test_a_phone_alone_is_refused_and_never_contacted_again(rig) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.phone_on_lan = True
    world.attached[PHONE_ID] = "phone"

    res = _install(client, headers)

    assert res["ok"] is False
    assert res["code"] == "phone_only"
    assert world.installed == []
    assert not any(c[1] == "connect" and "192.168.1.30" in c[2] for c in world.commands)


def test_a_watch_showing_its_pairing_screen_asks_for_the_code(rig) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.watch_connect_visible = True
    world.watch_pairing_open = True

    res = _install(client, headers)

    assert res["ok"] is False
    assert res["code"] == "need_pairing_code"
    assert [e["host_port"] for e in res["pairing"]] == [WATCH_PAIR]


def test_a_watch_not_yet_paired_is_told_how_to_pair(rig) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.watch_connect_visible = True  # wireless debugging on, pairing screen closed, never paired

    res = _install(client, headers)

    assert res["ok"] is False
    assert res["code"] == "needs_pairing"
    assert WATCH_CONNECT in res["connect_failed"]
    assert world.installed == []


def test_a_wrong_or_expired_code_is_reported_not_swallowed(rig) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.watch_connect_visible = True
    world.watch_pairing_open = True

    res = _install(client, headers, pair_code="000000")

    assert res["ok"] is False
    assert res["code"] == "pair_failed"
    assert "expires" in res["error"]


def test_a_code_with_no_pairing_screen_open_explains_what_to_open(rig) -> None:  # noqa: ANN001
    _world, client, headers, *_ = rig
    res = _install(client, headers, pair_code=CODE)
    assert res["ok"] is False
    assert res["code"] == "no_pairing_service"


def test_an_unauthorized_watch_is_told_to_tap_allow(rig) -> None:  # noqa: ANN001
    world, client, headers, *_ = rig
    world.attached["192.168.1.90:5555"] = "watch"
    world.unauthorized.add("192.168.1.90:5555")
    res = _install(client, headers)
    assert res["ok"] is False
    assert res["code"] == "unauthorized"


# ------------------------------------------------------------------------------ going forward
def test_the_background_finder_follows_the_watch_when_its_port_changes(rig) -> None:  # noqa: ANN001
    world, client, headers, app, s = rig
    # the watch was installed on once, when its wireless-debugging port was 38291
    (s.data_dir / "watch_target.json").write_text(
        json.dumps({"target": WATCH_CONNECT, "serial": "WATCH0SERIAL", "model": "SM-R895F", "host": WATCH_HOST}),
        encoding="utf-8",
    )
    # later wireless debugging was toggled: same watch, same serial, new port
    new_hp = f"{WATCH_HOST}:44444"
    world.paired = True
    world.mdns_text = lambda: f"adb-WATCH0SERIAL-zzz999\t_adb-tls-connect._tcp\t{new_hp}\n"  # type: ignore[method-assign]
    original_run = world.run

    def run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if len(cmd) > 2 and cmd[1] == "connect" and cmd[2] == new_hp:
            world.attached[new_hp] = "watch"
            return subprocess.CompletedProcess(cmd, 0, stdout=f"connected to {new_hp}\n", stderr="")
        return original_run(cmd, **kw)

    subprocess.run = run  # type: ignore[assignment]  # the fixture's monkeypatch restores the original

    asyncio.run(app.state.watch_autodetect_once())

    status = app.state.watch_auto_status
    assert status["watch_online"] is True, status
    assert world.installed == [new_hp]  # an outdated watch is brought up to date on its own
    saved = json.loads((s.data_dir / "watch_target.json").read_text(encoding="utf-8"))
    assert saved["target"] == new_hp  # the remembered address healed itself


def test_the_background_finder_ignores_a_phone_and_other_watches(rig) -> None:  # noqa: ANN001
    world, _client, _headers, app, s = rig
    (s.data_dir / "watch_target.json").write_text(
        json.dumps({"target": WATCH_CONNECT, "serial": "SOMEONEELSE", "model": "", "host": ""}), encoding="utf-8"
    )
    world.phone_on_lan = True
    world.attached[PHONE_ID] = "phone"
    world.watch_connect_visible = True  # a different person's watch (other serial)
    world.paired = True

    asyncio.run(app.state.watch_autodetect_once())

    assert app.state.watch_auto_status["watch_online"] is False
    assert world.installed == []
    assert not any(c[1] == "connect" and c[2] == WATCH_CONNECT for c in world.commands)
