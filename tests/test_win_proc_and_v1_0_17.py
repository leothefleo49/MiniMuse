"""Tests for v1.0.17 features:
- Hidden Windows subprocess flags & native process lookup (`win_proc`)
- OpenRouter multi-model lineup persistence & `/api/models` options
- Google OAuth PKCE, XOAUTH2 email creds, Calendar/Contacts serializers & API routes
- Wear OS watch device probe / install route & Chrome private companion
- Release notes entry for 1.0.17
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from nanomuse import __version__
from nanomuse.coding import agents as coding_agents
from nanomuse.config import EmailSettings, Settings
from nanomuse.connectors.google_oauth import (
    VAULT_ACCESS_TOKEN,
    VAULT_CLIENT_ID,
    VAULT_EMAIL,
    VAULT_EXPIRES_AT,
    VAULT_REFRESH_TOKEN,
    GoogleOAuthManager,
    build_xoauth2_string,
    calendar_events_to_ics,
    make_pkce_pair,
    people_connections_to_vcf,
    refresh_access_token_sync,
)
from nanomuse.llm import MockLLM
from nanomuse.server import create_app
from nanomuse.server.connections import normalize_model_options
from nanomuse.server.release_notes import BUILTIN_NOTES
from nanomuse.server.service import MuseService
from nanomuse.tools.email_tool import resolve_email_creds
from nanomuse.vault.vault import CredentialVault
from nanomuse.win_proc import (
    hidden_creationflags,
    hidden_startupinfo,
    hidden_subprocess_kwargs,
)


def _make_server(tmp_path: Path) -> Iterator[tuple[TestClient, MuseService]]:
    settings = Settings(data_dir=tmp_path)
    settings.server.token = "secret-token"
    svc = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, svc)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer secret-token"
    with client:
        yield client, svc


@pytest.fixture()
def server_v17(tmp_path: Path) -> Iterator[tuple[TestClient, MuseService]]:
    yield from _make_server(tmp_path)


# ------------------------------------------------------------------ win_proc & subprocess hiding
def test_hidden_subprocess_kwargs_windows_and_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nanomuse.win_proc.sys.platform", "linux")
    assert hidden_creationflags() == 0
    assert hidden_startupinfo() is None
    assert hidden_subprocess_kwargs() == {}

    monkeypatch.setattr("nanomuse.win_proc.sys.platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)

    flags = hidden_creationflags(extra=0x00000200)
    assert flags == (0x08000000 | 0x00000200)

    kw_hidden = hidden_subprocess_kwargs()
    assert kw_hidden["creationflags"] == 0x08000000
    assert "startupinfo" in kw_hidden
    si = kw_hidden["startupinfo"]
    assert si.dwFlags & 0x00000001
    assert si.wShowWindow == 0

    kw_gui = hidden_subprocess_kwargs(hide_window=False)
    assert kw_gui == {"creationflags": 0x08000000}


def test_version_of_is_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "dummy.exe"
    exe.write_text("x", encoding="utf-8")
    calls = 0

    def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1

        class _R:
            returncode = 0
            stdout = "tool 9.9.9\n"
            stderr = ""

        return _R()

    coding_agents._VERSION_CACHE.clear()
    monkeypatch.setattr(coding_agents.subprocess, "run", fake_run)
    assert coding_agents.version_of(str(exe)) == "tool 9.9.9"
    assert coding_agents.version_of(str(exe)) == "tool 9.9.9"
    assert calls == 1


# ------------------------------------------------------------------ multi-model options
def test_normalize_model_options_and_api_models(
    server_v17: tuple[TestClient, MuseService],
) -> None:
    raw = [
        {"model": "anthropic/claude-sonnet-4", "base_url": "https://openrouter.ai/api/v1/"},
        "openai/gpt-5-mini",
        {"model": "anthropic/claude-sonnet-4", "base_url": "https://openrouter.ai/api/v1"},
        {"model": ""},
    ]
    cleaned = normalize_model_options(raw)
    assert len(cleaned) == 2
    assert cleaned[0]["model"] == "anthropic/claude-sonnet-4"
    assert cleaned[0]["base_url"] == "https://openrouter.ai/api/v1"
    assert cleaned[1]["model"] == "openai/gpt-5-mini"

    client, _svc = server_v17
    put_resp = client.put(
        "/api/connections/llm/options",
        json={
            "options": [
                {
                    "model": "anthropic/claude-sonnet-4",
                    "label": "Claude Sonnet 4",
                    "provider": "openai",
                    "base_url": "https://openrouter.ai/api/v1",
                },
                {
                    "model": "google/gemini-2.5-flash",
                    "label": "Gemini 2.5 Flash",
                    "provider": "openai",
                    "base_url": "https://openrouter.ai/api/v1",
                },
            ]
        },
    )
    assert put_resp.status_code == 200
    opts = put_resp.json()
    assert len(opts) == 2

    models_resp = client.get("/api/models")
    assert models_resp.status_code == 200
    body = models_resp.json()
    assert len(body["options"]) == 2
    assert "base_url" in body["default"]
    assert "endpoint" in body["default"]


# ------------------------------------------------------------------ Google OAuth & Sync
def test_google_pkce_xoauth2_ics_vcf_and_refresh(tmp_path: Path) -> None:
    verifier, challenge = make_pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected

    sasl = build_xoauth2_string("alice@gmail.com", "ya29.token")
    assert "user=alice@gmail.com\x01auth=Bearer ya29.token\x01\x01" == sasl

    ics = calendar_events_to_ics(
        [
            {
                "id": "evt1",
                "summary": "Standup, Team",
                "description": "Line1\nLine2",
                "location": "Room A",
                "start": {"dateTime": "2026-11-14T10:00:00Z"},
                "end": {"dateTime": "2026-11-14T10:30:00Z"},
            },
            {
                "id": "evt2",
                "status": "cancelled",
                "summary": "Skip me",
                "start": {"date": "2026-11-15"},
            },
        ]
    )
    assert "BEGIN:VCALENDAR" in ics
    assert "SUMMARY:Standup\\, Team" in ics
    assert "Skip me" not in ics

    vcf = people_connections_to_vcf(
        [
            {
                "names": [{"displayName": "Bob Smith", "familyName": "Smith", "givenName": "Bob"}],
                "emailAddresses": [{"value": "bob@example.com"}],
                "phoneNumbers": [{"value": "+15551234567"}],
                "organizations": [{"name": "Acme", "title": "Engineer"}],
            }
        ]
    )
    assert "BEGIN:VCARD" in vcf
    assert "FN:Bob Smith" in vcf
    assert "EMAIL;TYPE=INTERNET:bob@example.com" in vcf
    assert "ORG:Acme" in vcf

    vault = CredentialVault(tmp_path / "vault.enc", tmp_path / "vault.key")
    vault.set(VAULT_CLIENT_ID, "cid.apps.googleusercontent.com")
    vault.set(VAULT_EMAIL, "alice@gmail.com")
    vault.set(VAULT_ACCESS_TOKEN, "ya29.fresh")
    vault.set(VAULT_EXPIRES_AT, str(time.time() + 3600))
    vault.set(VAULT_REFRESH_TOKEN, "1//refresh")

    assert refresh_access_token_sync(vault) == "ya29.fresh"
    addr, secret, use_oauth = resolve_email_creds(
        EmailSettings(enabled=True, auth_mode="oauth"),
        vault,
    )
    assert addr == "alice@gmail.com"
    assert secret == "ya29.fresh"
    assert use_oauth is True

    mgr = GoogleOAuthManager(tmp_path, vault)
    st = mgr.status()
    assert st["signed_in"] is True
    assert st["email"] == "alice@gmail.com"
    assert st["has_client_id"] is True


def test_google_routes_and_watch_install_routes(
    server_v17: tuple[TestClient, MuseService],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _svc = server_v17
    st = client.get("/api/connections/google/status")
    assert st.status_code == 200
    assert st.json()["signed_in"] is False

    # A default public client id ships with the app, so sign-in works without the user's own
    monkeypatch.delenv("MINIMUSE_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    ok_default = client.post("/api/connections/google/oauth/start", json={})
    assert ok_default.status_code == 200
    assert "accounts.google.com" in ok_default.json()["auth_url"]
    assert client.post("/api/connections/google/oauth/logout").status_code == 200

    # With client_id, generates valid Google OAuth2 PKCE URL
    ok = client.post(
        "/api/connections/google/oauth/start",
        json={"client_id": "test-client.apps.googleusercontent.com"},
    )
    assert ok.status_code == 200
    auth_url = ok.json()["auth_url"]
    parsed = urlparse(auth_url)
    qs = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert qs["client_id"] == ["test-client.apps.googleusercontent.com"]
    assert qs["code_challenge_method"] == ["S256"]

    # Clean up pending server listener via logout
    assert client.post("/api/connections/google/oauth/logout").status_code == 200

    # Watch ADB devices & install probe route
    w_dev = client.get("/api/watch/devices")
    assert w_dev.status_code == 200
    assert "adb_available" in w_dev.json()
    assert "devices" in w_dev.json()

    monkeypatch.setattr("shutil.which", lambda _name: None)
    w_ins = client.post("/api/watch/install", json={})
    assert w_ins.status_code == 200
    assert w_ins.json()["ok"] is False


def test_release_notes_and_version_v17() -> None:
    parts = tuple(int(x) for x in __version__.split("."))
    assert parts >= (1, 0, 17)
    notes = BUILTIN_NOTES["1.0.17"]
    assert notes["added"]
    assert notes["improved"]
    assert notes["fixed"]
