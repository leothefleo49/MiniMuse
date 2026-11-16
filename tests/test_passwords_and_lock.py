"""Tests for PasswordStore, PasswordsTool, biometric-only AppLockStore, and UsageLimits."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanomuse.passwords import PasswordStore, compute_totp
from nanomuse.server.app_lock import AppLockStore
from nanomuse.server.usage_limits import UsageLimitStore
from nanomuse.tools.passwords_tool import PasswordsTool
from nanomuse.vault.vault import CredentialVault


def test_password_store_crud_and_vault_sync(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    store = PasswordStore(tmp_path, vault)

    entry, status = store.add_or_update(
        title="GitHub",
        url="https://github.com/login",
        username="alex@example.com",
        password="SuperSecretPassword123!",
        totp="JBSWY3DPEHPK3PXP",
        notes="Main dev account",
    )
    assert status == "added"
    assert entry["title"] == "GitHub"
    assert entry["domain"] == "github.com"
    assert entry["has_password"] is True
    assert entry["has_totp"] is True
    assert vault.get(entry["vault_key"]) == "SuperSecretPassword123!"

    # Reload from disk to verify encryption persistence
    store2 = PasswordStore(tmp_path, vault)
    listed = store2.list_entries("github")
    assert len(listed) == 1
    assert listed[0]["username"] == "alex@example.com"

    best = store2.find_best("https://gist.github.com")
    assert best is not None
    assert best["password"] == "SuperSecretPassword123!"

    # TOTP generation
    code, rem = store2.totp_code(entry["id"])
    assert len(code) == 6 and code.isdigit()
    assert 1 <= rem <= 30

    # Delete removes from vault as well
    assert store2.delete(entry["id"]) is True
    assert vault.get(entry["vault_key"]) is None


def test_password_store_import_csv_and_json(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    store = PasswordStore(tmp_path, vault)

    chrome_csv = (
        "name,url,username,password,note\n"
        "University,https://portal.example.edu,alex_u,CampusPass99,Student portal\n"
        "Amazon,https://www.amazon.com,alex@example.com,PrimePass42,\n"
    )
    res_csv = store.import_data(chrome_csv, "chrome-passwords.csv")
    assert res_csv["added"] == 2
    assert res_csv["total"] == 2

    bitwarden_json = json.dumps(
        {
            "items": [
                {
                    "name": "Discord",
                    "login": {
                        "username": "alex_dev",
                        "password": "DiscordPassword!",
                        "totp": "otpauth://totp/Discord?secret=JBSWY3DPEHPK3PXP&digits=6",
                        "uris": [{"uri": "https://discord.com/login"}],
                    },
                }
            ]
        }
    )
    res_json = store.import_data(bitwarden_json, "bitwarden.json")
    assert res_json["added"] == 1
    assert res_json["total"] == 3

    exported = store.export_csv()
    assert "CampusPass99" in exported
    assert "DiscordPassword!" in exported

    code, _ = compute_totp(
        "otpauth://totp/Discord?secret=JBSWY3DPEHPK3PXP&digits=6", now=1700000000
    )
    assert len(code) == 6 and code.isdigit()


@pytest.mark.asyncio
async def test_passwords_tool(tmp_path: Path) -> None:
    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    store = PasswordStore(tmp_path, vault)
    tool = PasswordsTool(store=store)

    r_add = await tool.execute(
        action="add",
        title="Spotify",
        url="https://accounts.spotify.com",
        username="music_fan",
        password="SpotifySecretPassword",
        totp="JBSWY3DPEHPK3PXP",
    )
    assert r_add.error is None
    assert "vault:LOGIN_" in r_add.output

    r_search = await tool.execute(action="search", query="spotify")
    assert r_search.error is None
    assert "music_fan" in r_search.output
    assert "SpotifySecretPassword" not in r_search.output

    r_totp = await tool.execute(action="totp", query="spotify")
    assert r_totp.error is None
    assert "TOTP 2FA code" in r_totp.output


def test_app_lock_biometric_only_and_optional_pin(tmp_path: Path) -> None:
    lock = AppLockStore(tmp_path)
    device = "phone-1"
    assert lock.view(device) == {
        "enabled": False,
        "biometric": False,
        "passkey": False,
        "pin_enabled": False,
        "biometric_credential_id": None,
        "passkey_credential_id": None,
        "timeout_seconds": 3600,
        "lock_on_leave": False,
    }

    # Enable with Device Fingerprint / Face ID only (no PIN set)
    v1 = lock.register_biometric(device, "cred_abc_123", kind="biometric")
    assert v1["enabled"] is True
    assert v1["biometric"] is True
    assert v1["passkey"] is False
    assert v1["pin_enabled"] is False
    assert lock.biometric_credential(device, "biometric") == "cred_abc_123"
    # no PIN set: nothing may walk in without the device's own WebAuthn proof — the old
    # "__DEVICE_FALLBACK__" shortcut that opened the lock with nothing is gone
    assert lock.verify(device, "__DEVICE_FALLBACK__") is False

    # Register a separate Passkey (NordPass / password manager) alongside Device Fingerprint
    v_pk = lock.register_biometric(
        device, "M2JkZjc1NWQtMzU5OS00ZDliLWJmYzAtYWVlNGFjNzUwY2Rm", kind="passkey"
    )
    assert v_pk["biometric"] is True
    assert v_pk["passkey"] is True
    assert lock.biometric_credential(device, "passkey") == (
        "M2JkZjc1NWQtMzU5OS00ZDliLWJmYzAtYWVlNGFjNzUwY2Rm"
    )

    # Add a backup PIN while keeping biometric & passkey
    v2 = lock.set_pin(device, "1234", keep_biometric=True, timeout_seconds=900)
    assert v2["enabled"] is True
    assert v2["biometric"] is True
    assert v2["passkey"] is True
    assert v2["pin_enabled"] is True
    assert v2["timeout_seconds"] == 900
    assert lock.verify(device, "1234") is True
    assert lock.verify(device, "__DEVICE_FALLBACK__") is False

    # lock when switching away
    v_leave = lock.set_timeout(device, 60, lock_on_leave=True)
    assert v_leave["timeout_seconds"] == 60 and v_leave["lock_on_leave"] is True

    # Disable PIN while keeping Fingerprint / Face ID & Passkey active
    v3 = lock.set_pin(device, "", keep_biometric=True)
    assert v3["enabled"] is True
    assert v3["biometric"] is True
    assert v3["passkey"] is True
    assert v3["pin_enabled"] is False

    # Remove Passkey while keeping Device Fingerprint
    v_rem = lock.register_biometric(device, "", kind="passkey")
    assert v_rem["biometric"] is True
    assert v_rem["passkey"] is False

    # Turn off lock completely
    v4 = lock.set_pin(device, "", keep_biometric=False)
    assert v4["enabled"] is False
    assert v4["biometric"] is False
    assert v4["passkey"] is False
    assert v4["pin_enabled"] is False


def test_the_lock_is_per_device(tmp_path: Path) -> None:
    """A lock set on the phone does not lock the computer, and the other way round."""
    lock = AppLockStore(tmp_path)
    lock.set_pin("phone-1", "1234")
    assert lock.view("phone-1")["enabled"] is True
    assert lock.view("desktop")["enabled"] is False
    assert lock.verify("phone-1", "1234") is True
    assert lock.verify("desktop", "1234") is True  # no lock there: nothing to refuse
    # the desktop's own PIN is separate from the phone's
    lock.set_pin("desktop", "9999")
    assert lock.verify("phone-1", "9999") is False
    assert lock.verify("phone-1", "1234") is True


def test_a_device_with_the_matching_credential_claims_the_old_global_lock(tmp_path: Path) -> None:
    """The pre-device shape moves to the device that carries its credential; others stay free."""
    from nanomuse.config import load_app_settings, save_app_settings

    stored = load_app_settings(tmp_path)
    stored["app_lock"] = {"biometric_credential_id": "cred_legacy_1", "timeout_seconds": 900}
    save_app_settings(tmp_path, stored)
    lock = AppLockStore(tmp_path)
    # a device without the credential sees no lock
    assert lock.view("desktop")["enabled"] is False
    # the device whose browser holds that credential id claims the entry
    view = lock.view("phone-1", ("cred_legacy_1",))
    assert view["enabled"] is True
    assert view["biometric"] is True
    # from now on it is the phone's own
    assert lock.view("phone-1")["biometric_credential_id"] == "cred_legacy_1"
    assert lock.view("desktop")["enabled"] is False


def test_a_pin_only_legacy_lock_stays_shared(tmp_path: Path) -> None:
    """A PIN is knowledge, not a device: the old PIN-only entry applies everywhere until
    a device sets its own."""
    from nanomuse.config import load_app_settings, save_app_settings

    stored = load_app_settings(tmp_path)
    stored["app_lock"] = {
        "pin_hash": "f".join(["0"] * 64),
        "salt": "00",
        "iterations": 120000,
        "timeout_seconds": 900,
    }
    save_app_settings(tmp_path, stored)
    lock = AppLockStore(tmp_path)
    assert lock.view("desktop")["enabled"] is True
    # its own PIN shadows the shared one for that device only
    lock.set_pin("desktop", "1234")
    assert lock.verify("desktop", "1234") is True
    assert lock.view("phone-1")["enabled"] is True


def test_app_lock_reset_code(tmp_path: Path) -> None:
    """The rescue hatch: one code, one use, thirty minutes, a new PIN takes over."""
    lock = AppLockStore(tmp_path)
    device = "phone-1"
    lock.set_pin(device, "1234")
    info = lock.request_reset(device)
    assert len(info["code"]) == 8 and info["expires_in"] == 1800
    with pytest.raises(ValueError, match="wrong or expired"):
        lock.reset_with_code(device, "WRONG", "5678")
    view = lock.reset_with_code(device, info["code"], "5678")
    assert view["pin_enabled"] is True
    assert lock.verify(device, "5678") is True
    assert lock.verify(device, "1234") is False
    # the code is consumed: a second use fails
    with pytest.raises(ValueError, match="wrong or expired"):
        lock.reset_with_code(device, info["code"], "9999")
    # and it is this device's code: another device cannot spend it
    lock.request_reset(device)
    second = lock.request_reset(device)
    with pytest.raises(ValueError, match="wrong or expired"):
        lock.reset_with_code("desktop", second["code"], "1111")


def test_app_lock_alphanumeric_and_disable_with_code(tmp_path: Path) -> None:
    """Passwords with letters/symbols work alongside PINs, and a reset code can turn off the lock."""
    from fastapi.testclient import TestClient

    from nanomuse.config import Settings
    from nanomuse.server import create_app
    from nanomuse.server.service import MuseService

    lock = AppLockStore(tmp_path)
    device = "desktop"
    lock.set_pin(device, "MyPass!99")
    assert lock.verify(device, "MyPass!99") is True
    assert lock.verify(device, "wrong-pass") is False

    # Request reset & disable biometric-only / password lock cleanly via code
    info = lock.request_reset(device)
    view = lock.disable_with_code(device, info["code"])
    assert view["enabled"] is False
    assert view["pin_enabled"] is False
    assert view["biometric"] is False

    with pytest.raises(ValueError, match="wrong or expired"):
        lock.disable_with_code(device, info["code"])

    # Biometric-only lock (no PIN) can also be disabled via POST /api/lock/reset/disable
    s = Settings(data_dir=tmp_path / "srv_data", workspace=tmp_path / "srv_ws")
    s.server.token = "secret-token"
    svc = MuseService(s)
    app = create_app(settings=s, service=svc)
    with TestClient(app, raise_server_exceptions=True) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        reg = client.post(
            "/api/lock/webauthn",
            headers={"X-NanoMuse-Device": "win-pc-1"},
            json={"credential_id": "cred_xyz", "kind": "biometric"},
        )
        assert reg.status_code == 200
        assert reg.json()["enabled"] is True

        req_reset = client.post("/api/lock/reset", headers={"X-NanoMuse-Device": "win-pc-1"})
        assert req_reset.status_code == 200
        reset_file = s.data_dir / "lock-reset-code.txt"
        assert reset_file.is_file()
        code_line = reset_file.read_text(encoding="utf-8").splitlines()[0].strip()

        dis = client.post(
            "/api/lock/reset/disable",
            headers={"X-NanoMuse-Device": "win-pc-1"},
            json={"code": code_line.lower()},
        )
        assert dis.status_code == 200
        assert dis.json()["enabled"] is False
        assert not reset_file.exists()


def test_usage_limits_on_full_and_action(tmp_path: Path) -> None:
    ledger = tmp_path / "usage.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "ts": "2099-01-01T00:00:00+00:00",
                "model": "deepseek-flash",
                "cost_usd": 12.0,
                "prompt_tokens": 1000,
                "completion_tokens": 500,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    limits = UsageLimitStore(tmp_path)
    limits.save(
        [
            {
                "id": "cap1",
                "name": "Monthly warn cap",
                "scope": "global",
                "unit": "cost",
                "window": "month",
                "amount": 10.0,
                "warning_pct": 80,
                "on_full": "warn",
            }
        ]
    )
    ev = limits.evaluate("deepseek-flash")
    assert ev is not None
    assert ev["action"] == "warn"
    assert ev["level"] == "warn"
