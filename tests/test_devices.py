"""The ``device_*`` tools: how the pictures a device sends are kept."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from nanomuse.tools.devices import DeviceScreen


class _FakeHub:
    """Answers every call with the next payload: one screen picture per call."""

    def __init__(self, payloads: list[dict[str, Any]]):
        self._payloads: Iterator[dict[str, Any]] = iter(payloads)

    def find(self, query: str) -> dict[str, Any]:
        return {"id": "d1", "name": "Pixel", "online": True}

    async def call(
        self, device_id: str, action: str, args: dict[str, Any], timeout: float = 120
    ) -> dict[str, Any]:
        return next(self._payloads)


def _screen(data: bytes) -> dict[str, Any]:
    return {"data": base64.b64encode(data).decode(), "mime": "image/jpeg"}


async def test_two_screens_in_the_same_second_are_two_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from nanomuse.tools import devices as devices_module

    hub = _FakeHub([_screen(b"before"), _screen(b"after")])
    tool = DeviceScreen(hub=hub, workspace=tmp_path)

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 1, 1, 12, 0, 0)

    monkeypatch.setattr(devices_module, "datetime", _FrozenNow)
    first = await tool.execute(device="Pixel")
    second = await tool.execute(device="Pixel")
    assert first.ok and second.ok
    # the "before" picture survives the "after" one: look, act, then look again
    assert Path(first.images[0]) != Path(second.images[0])
    assert Path(first.images[0]).read_bytes() == b"before"
    assert Path(second.images[0]).read_bytes() == b"after"


def test_friendly_device_model_resolution():
    from nanomuse.server.service import MuseService

    resolve = MuseService._friendly_device_model

    assert resolve("SM-F766B", "") == "Galaxy Z Flip 7"
    assert resolve("SM-F741U", "") == "Galaxy Z Flip 6"
    assert resolve("SM-S928B", "") == "Galaxy S (SM-S928B)"
    assert resolve("Pixel 8 Pro", "") == "Pixel 8 Pro"
    # When model is empty or K, extract from Android UA
    assert (
        resolve("", "Mozilla/5.0 (Linux; Android 15; SM-F766B Build/AP3A.240905.015)")
        == "Galaxy Z Flip 7"
    )


def test_device_rename_and_nicknames(tmp_path: Path):
    from nanomuse.server.service import MuseService

    # Service mock instance with temp data_dir
    svc = object.__new__(MuseService)
    svc.data_dir = tmp_path
    svc._live_clients = {
        "conn-1": {
            "ua": "Mozilla/5.0 (Linux; Android 15; SM-F766B)",
            "model": "Galaxy Z Flip 7",
            "online": True,
        }
    }

    # Nicknames start empty
    assert svc.device_nicknames() == {}

    # Initial view shows resolved hardware model
    init_view = svc.local_devices_view()
    phone_id = init_view["phones"][0]["id"]
    assert init_view["phones"][0]["name"] == "Galaxy Z Flip 7"

    # Rename local phone
    res = svc.rename_local_device(phone_id, "My Flip Phone")
    assert svc.device_nicknames().get(phone_id) == "My Flip Phone"
    phone_entry = next(p for p in res["phones"] if p["id"] == phone_id)
    assert phone_entry["name"] == "My Flip Phone"
    assert phone_entry["model"] == "Galaxy Z Flip 7"
    assert phone_entry["nickname"] == "My Flip Phone"

    # Clear nickname
    res2 = svc.rename_local_device(phone_id, "")
    assert svc.device_nicknames().get(phone_id) is None
    phone_entry2 = next(p for p in res2["phones"] if p["id"] == phone_id)
    assert phone_entry2["name"] == "Galaxy Z Flip 7"
    assert phone_entry2.get("nickname") is None


def test_cloud_mode_and_slots(tmp_path: Path):
    from nanomuse.server.service import MuseService

    svc = object.__new__(MuseService)
    svc.data_dir = tmp_path

    # Default is hybrid
    assert svc.cloud_mode() == "hybrid"
    assert svc.cloud_slots() == {"chat": "cloud", "media": "cloud", "sync": "cloud"}

    from nanomuse.config import save_app_settings

    # Local mode
    save_app_settings(tmp_path, {"cloud_mode": "local"})
    assert svc.cloud_mode() == "local"
    assert svc.cloud_slots() == {"chat": "local", "media": "local", "sync": "local"}

    # Hybrid per-slot
    save_app_settings(
        tmp_path, {"cloud_mode": "hybrid", "cloud_slots": {"chat": "local", "media": "cloud"}}
    )
    assert svc.cloud_mode() == "hybrid"
    assert svc.cloud_slots()["chat"] == "local"
    assert svc.cloud_slots()["media"] == "cloud"
    assert svc.cloud_prompts() is False

    save_app_settings(tmp_path, {"cloud_mode": "hybrid", "cloud_prompts": True})
    assert svc.cloud_prompts() is True

    save_app_settings(tmp_path, {"cloud_mode": "local", "cloud_prompts": True})
    assert svc.cloud_prompts() is False

    save_app_settings(tmp_path, {"cloud_mode": "cloud"})
    assert svc.cloud_prompts() is True


def test_local_devices_view_on_android_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nanomuse.server.service import MuseService

    monkeypatch.setenv("NANOMUSE_DEVICE", "android")
    monkeypatch.setenv("NANOMUSE_DEVICE_MODEL", "SM-F766B")
    monkeypatch.setenv("NANOMUSE_DEVICE_SDK", "35")

    svc = object.__new__(MuseService)
    svc.data_dir = tmp_path
    svc._live_clients = {
        "c-wv": {
            "ua": "Mozilla/5.0 (Linux; Android 15; SM-F766B; wv)",
            "model": "Galaxy Z Flip 7",
            "online": True,
        },
        "c-notify": {
            "ua": "MiniMuse-Android/1.0",
            "model": "Galaxy Z Flip 7",
            "online": True,
        },
    }

    view = svc.local_devices_view(
        current_ua="Mozilla/5.0 (Linux; Android 15; SM-F766B; wv)",
        client_device="phone-self",
    )
    assert view["computer"]["kind"] == "phone"
    assert view["computer"]["os"] == "Android"
    assert view["phones"] == []
