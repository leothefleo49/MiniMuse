"""Google OAuth 2.0 (PKCE) + Calendar & Contacts sync for MiniMuse.

Supports two paths for connecting Gmail, Google Calendar, and Google Contacts:
1. OAuth 2.0 (PKCE loopback): when a Google Cloud Desktop OAuth Client ID is provided
   (in the vault as ``GOOGLE_CLIENT_ID`` or via environment / UI), signs in with Google,
   saves encrypted/private tokens, uses SASL XOAUTH2 for Gmail IMAP/SMTP without an app
   password, and syncs Google Calendar (v3) to a local ``.ics`` feed and Google Contacts
   (People API v1) to a local ``.vcf`` address book.
2. Zero-Cloud-Console popup & sync: users without a Cloud Console project can still click
   one-click popup helpers in the app for Google App Passwords, iCal links, and vCard
   imports, or ask their selected AI model to guide them interactively.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from nanomuse.logger import logger
from nanomuse.private import keep_private

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
CALENDAR_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
PEOPLE_CONNECTIONS_URL = "https://people.googleapis.com/v1/people/me/connections"

SCOPES: tuple[str, ...] = (
    "openid",
    "email",
    "profile",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
)

VAULT_CLIENT_ID = "GOOGLE_CLIENT_ID"
VAULT_CLIENT_SECRET = "GOOGLE_CLIENT_SECRET"
VAULT_ACCESS_TOKEN = "GOOGLE_OAUTH_ACCESS_TOKEN"
VAULT_REFRESH_TOKEN = "GOOGLE_OAUTH_REFRESH_TOKEN"
VAULT_EXPIRES_AT = "GOOGLE_OAUTH_EXPIRES_AT"
VAULT_EMAIL = "GOOGLE_OAUTH_EMAIL"

STORE_FILE = "google-oauth.json"
REFRESH_MARGIN_S = 90
LOGIN_TIMEOUT_S = 600


class GoogleOAuthError(Exception):
    """Typed error raised during Google OAuth or Google Workspace sync."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def build_xoauth2_string(address: str, access_token: str) -> str:
    """Standard SASL XOAUTH2 string for IMAP/SMTP ``AUTH XOAUTH2``."""
    return f"user={address}\x01auth=Bearer {access_token}\x01\x01"


def _escape_ics(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _format_ics_dt(raw: dict[str, Any]) -> tuple[str, str] | None:
    date_only = raw.get("date")
    if isinstance(date_only, str) and date_only:
        return ";VALUE=DATE", date_only.replace("-", "")
    dt_raw = raw.get("dateTime")
    if not isinstance(dt_raw, str) or not dt_raw:
        return None
    normalized = dt_raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized).astimezone(UTC)
        return "", parsed.strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        return None


def calendar_events_to_ics(items: list[dict[str, Any]], *, calendar_name: str = "Google Calendar") -> str:
    """Convert Google Calendar v3 event dicts into a valid RFC 5545 ``.ics`` payload."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MiniMuse//Google Calendar Sync//EN",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{_escape_ics(calendar_name)}",
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for item in items:
        if item.get("status") == "cancelled":
            continue
        start = _format_ics_dt(item.get("start") or {})
        end = _format_ics_dt(item.get("end") or {})
        if not start:
            continue
        uid = str(item.get("id") or secrets.token_hex(8))
        summary = str(item.get("summary") or "Untitled event").strip()
        description = str(item.get("description") or "").strip()
        location = str(item.get("location") or "").strip()
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_escape_ics(uid)}@minimuse.google",
                f"DTSTAMP:{stamp}",
                f"DTSTART{start[0]}:{start[1]}",
            ]
        )
        if end:
            lines.append(f"DTEND{end[0]}:{end[1]}")
        lines.append(f"SUMMARY:{_escape_ics(summary)}")
        if description:
            lines.append(f"DESCRIPTION:{_escape_ics(description[:1500])}")
        if location:
            lines.append(f"LOCATION:{_escape_ics(location[:400])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _escape_vcf(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def people_connections_to_vcf(connections: list[dict[str, Any]]) -> str:
    """Convert Google People API ``connections`` list into vCard 3.0 text."""
    cards: list[str] = []
    for person in connections:
        names = person.get("names") or []
        emails = person.get("emailAddresses") or []
        phones = person.get("phoneNumbers") or []
        orgs = person.get("organizations") or []
        bios = person.get("biographies") or []
        addresses = person.get("addresses") or []

        display_name = ""
        family = ""
        given = ""
        if names:
            primary_name = next((n for n in names if (n.get("metadata") or {}).get("primary")), names[0])
            display_name = str(primary_name.get("displayName") or "").strip()
            family = str(primary_name.get("familyName") or "").strip()
            given = str(primary_name.get("givenName") or "").strip()
        if not display_name and emails:
            display_name = str(emails[0].get("value") or "").strip()
        if not display_name:
            continue

        lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"FN:{_escape_vcf(display_name)}",
            f"N:{_escape_vcf(family)};{_escape_vcf(given)};;;",
        ]
        for em in emails:
            addr = str(em.get("value") or "").strip()
            if addr:
                lines.append(f"EMAIL;TYPE=INTERNET:{addr}")
        for ph in phones:
            num = str(ph.get("value") or "").strip()
            if num:
                lines.append(f"TEL;TYPE=CELL:{num}")
        for adr in addresses:
            street = _escape_vcf(str(adr.get("streetAddress") or "").strip())
            city = _escape_vcf(str(adr.get("city") or "").strip())
            region = _escape_vcf(str(adr.get("region") or "").strip())
            postal = _escape_vcf(str(adr.get("postalCode") or "").strip())
            country = _escape_vcf(str(adr.get("country") or "").strip())
            formatted = str(adr.get("formattedValue") or "").strip()
            if not any((street, city, region, postal, country)) and formatted:
                street = _escape_vcf(formatted)
            if any((street, city, region, postal, country)):
                atype = str(adr.get("type") or "HOME").upper()
                lines.append(f"ADR;TYPE={atype}:;;{street};{city};{region};{postal};{country}")
        if orgs:
            org_name = str(orgs[0].get("name") or "").strip()
            title = str(orgs[0].get("title") or "").strip()
            if org_name:
                lines.append(f"ORG:{_escape_vcf(org_name)}")
            if title:
                lines.append(f"TITLE:{_escape_vcf(title)}")
        if bios:
            note = str(bios[0].get("value") or "").strip()
            if note:
                lines.append(f"NOTE:{_escape_vcf(note[:400])}")
        lines.append("END:VCARD")
        cards.append("\r\n".join(lines))
    return "\r\n".join(cards) + ("\r\n" if cards else "")


def refresh_access_token_sync(vault: Any) -> str:
    """Synchronous Google OAuth access-token getter & refresher backed by the vault.

    Used inside IMAP/SMTP worker threads so background mail checks keep working after
    the 1-hour Google access token expires.
    """
    if vault is None:
        raise GoogleOAuthError("not_signed_in", "Google OAuth vault is unavailable")
    access = str(vault.get(VAULT_ACCESS_TOKEN) or "").strip()
    expires_raw = str(vault.get(VAULT_EXPIRES_AT) or "").strip()
    refresh = str(vault.get(VAULT_REFRESH_TOKEN) or "").strip()
    client_id = (
        str(vault.get(VAULT_CLIENT_ID) or "").strip()
        or os.environ.get("MINIMUSE_GOOGLE_CLIENT_ID", "").strip()
        or os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        # Google's public installed-app client id (no secret needed with PKCE). Users can
        # override it in Connections > Email & Google Accounts > advanced if they want their
        # own project's quota.
        or "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
    )
    client_secret = (
        str(vault.get(VAULT_CLIENT_SECRET) or "").strip()
        or os.environ.get("MINIMUSE_GOOGLE_CLIENT_SECRET", "").strip()
        or os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    )
    try:
        expires_at = float(expires_raw) if expires_raw else 0.0
    except ValueError:
        expires_at = 0.0

    if access and (expires_at <= 0.0 or expires_at - REFRESH_MARGIN_S > time.time()):
        return access
    if not refresh or not client_id:
        if access:
            return access
        raise GoogleOAuthError("not_signed_in", "Google session expired; please sign in again")

    payload: dict[str, str] = {
        "client_id": client_id,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }
    if client_secret:
        payload["client_secret"] = client_secret
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(TOKEN_URL, data=payload)
    except httpx.HTTPError as exc:
        if access:
            return access
        raise GoogleOAuthError("unreachable", f"Failed to reach Google OAuth: {exc}") from exc

    if resp.status_code >= 400:
        raise GoogleOAuthError("not_signed_in", f"Google token refresh failed ({resp.status_code})")
    data = resp.json()
    new_access = str(data.get("access_token") or "").strip()
    if not new_access:
        raise GoogleOAuthError("not_signed_in", "Google token refresh returned no access_token")
    expires_in = float(data.get("expires_in") or 3600)
    new_exp = time.time() + expires_in
    vault.set(VAULT_ACCESS_TOKEN, new_access)
    vault.set(VAULT_EXPIRES_AT, str(new_exp))
    if data.get("refresh_token"):
        vault.set(VAULT_REFRESH_TOKEN, str(data["refresh_token"]))
    return new_access


@dataclass
class _PendingLogin:
    state: str
    code_verifier: str
    redirect_uri: str
    client_id: str
    client_secret: str
    started_at: float = field(default_factory=time.time)
    done: bool = False
    error: str = ""
    server: asyncio.AbstractServer | None = None
    timeout_task: asyncio.Task[None] | None = None


class GoogleOAuthManager:
    """Manages Google OAuth PKCE sign-in, token lifecycle, and Calendar/Contacts sync."""

    def __init__(self, data_dir: Path, vault: Any):
        self.data_dir = Path(data_dir)
        self.vault = vault
        self._pending: _PendingLogin | None = None
        self._last_sync_at: str = ""
        self._last_sync_summary: dict[str, Any] = {}

    @property
    def store_path(self) -> Path:
        return self.data_dir / STORE_FILE

    def _client_id(self) -> str:
        return (
            str(self.vault.get(VAULT_CLIENT_ID) or "").strip()
            or os.environ.get("MINIMUSE_GOOGLE_CLIENT_ID", "").strip()
            or os.environ.get("GOOGLE_CLIENT_ID", "").strip()
            or "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
        )

    def _client_secret(self) -> str:
        return (
            str(self.vault.get(VAULT_CLIENT_SECRET) or "").strip()
            or os.environ.get("MINIMUSE_GOOGLE_CLIENT_SECRET", "").strip()
            or os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        )

    @staticmethod
    def _acct_vault_key(email: str, suffix: str) -> str:
        safe = re.sub(r"[^A-Z0-9]+", "_", email.upper()).strip("_") or "DEFAULT"
        return f"GOOGLE_ACCT_{safe}_{suffix}"

    def _read_store(self) -> dict[str, Any]:
        if not self.store_path.is_file():
            return {}
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            accounts = data.get("accounts")
            if not isinstance(accounts, dict):
                accounts = {}
            legacy_email = str(data.get("email") or "").strip()
            if legacy_email and legacy_email not in accounts:
                accounts[legacy_email] = {
                    "email": legacy_email,
                    "name": str(data.get("name") or "").strip(),
                    "access_token": data.get("access_token") or "",
                    "refresh_token": data.get("refresh_token") or "",
                    "expires_at": data.get("expires_at") or 0.0,
                    "scopes": list(data.get("scopes") or []),
                    "updated_at": data.get("updated_at") or "",
                    "last_sync_at": data.get("last_sync_at"),
                    "last_sync": data.get("last_sync"),
                }
                data["accounts"] = accounts
            return data
        except (OSError, ValueError):
            return {}

    def _write_store(self, payload: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        keep_private(self.store_path)

    def _sync_vault(self, data: dict[str, Any], account_email: str | None = None) -> None:
        if account_email:
            if data.get("access_token"):
                self.vault.set(self._acct_vault_key(account_email, "ACCESS_TOKEN"), str(data["access_token"]))
            if data.get("refresh_token"):
                self.vault.set(self._acct_vault_key(account_email, "REFRESH_TOKEN"), str(data["refresh_token"]))
            if data.get("expires_at"):
                self.vault.set(self._acct_vault_key(account_email, "EXPIRES_AT"), str(data["expires_at"]))
        if data.get("access_token"):
            self.vault.set(VAULT_ACCESS_TOKEN, str(data["access_token"]))
        if data.get("refresh_token"):
            self.vault.set(VAULT_REFRESH_TOKEN, str(data["refresh_token"]))
        if data.get("expires_at"):
            self.vault.set(VAULT_EXPIRES_AT, str(data["expires_at"]))
        if data.get("email"):
            self.vault.set(VAULT_EMAIL, str(data["email"]))

    def _accounts_map(self, store: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw = store.get("accounts")
        if isinstance(raw, dict) and raw:
            return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        legacy_email = str(store.get("email") or self.vault.get(VAULT_EMAIL) or "").strip()
        has_token = bool(
            store.get("access_token")
            or store.get("refresh_token")
            or self.vault.get(VAULT_ACCESS_TOKEN)
            or self.vault.get(VAULT_REFRESH_TOKEN)
        )
        if legacy_email and has_token:
            return {
                legacy_email: {
                    "email": legacy_email,
                    "name": str(store.get("name") or "").strip(),
                    "access_token": store.get("access_token") or self.vault.get(VAULT_ACCESS_TOKEN) or "",
                    "refresh_token": store.get("refresh_token") or self.vault.get(VAULT_REFRESH_TOKEN) or "",
                    "expires_at": store.get("expires_at") or self.vault.get(VAULT_EXPIRES_AT) or 0.0,
                    "scopes": list(store.get("scopes") or []),
                    "last_sync_at": store.get("last_sync_at"),
                    "last_sync": store.get("last_sync"),
                }
            }
        return {}

    def status(self) -> dict[str, Any]:
        store = self._read_store()
        accts = self._accounts_map(store)
        primary_email = str(store.get("primary_email") or store.get("email") or self.vault.get(VAULT_EMAIL) or "").strip()
        if primary_email not in accts and accts:
            primary_email = next(iter(accts))
        primary = accts.get(primary_email, {}) if primary_email else {}
        email = primary_email or str(store.get("email") or self.vault.get(VAULT_EMAIL) or "").strip()
        has_token = bool(
            primary.get("access_token")
            or primary.get("refresh_token")
            or store.get("access_token")
            or store.get("refresh_token")
            or self.vault.get(VAULT_ACCESS_TOKEN)
            or self.vault.get(VAULT_REFRESH_TOKEN)
        )
        client_id = self._client_id()
        pending = self._pending
        pending_active = bool(
            pending is not None
            and not pending.done
            and (time.time() - pending.started_at) < LOGIN_TIMEOUT_S
        )
        accounts_list = [
            {
                "email": acct_email,
                "name": str(info.get("name") or ""),
                "primary": acct_email == email,
                "scopes": list(info.get("scopes") or []),
                "last_sync_at": info.get("last_sync_at") or store.get("last_sync_at"),
                "last_sync": info.get("last_sync") or store.get("last_sync"),
            }
            for acct_email, info in accts.items()
        ]
        return {
            "signed_in": bool((has_token and email) or accounts_list),
            "email": email,
            "accounts": accounts_list,
            "has_client_id": bool(client_id),
            "client_id_preview": (client_id[:14] + "...") if len(client_id) > 16 else client_id,
            "scopes": list(primary.get("scopes") or store.get("scopes") or []),
            "pending": pending_active,
            "auth_error": pending.error if pending and pending.error else "",
            "last_sync_at": store.get("last_sync_at") or self._last_sync_at,
            "last_sync": store.get("last_sync") or self._last_sync_summary,
        }

    def configure_client(self, client_id: str, client_secret: str = "") -> dict[str, Any]:
        cid = client_id.strip()
        csec = client_secret.strip()
        if cid:
            self.vault.set(VAULT_CLIENT_ID, cid)
        if client_secret:
            self.vault.set(VAULT_CLIENT_SECRET, csec)
        return self.status()

    async def _stop_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        if pending.timeout_task and not pending.timeout_task.done():
            pending.timeout_task.cancel()
        if pending.server is not None:
            pending.server.close()
            try:
                await pending.server.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._pending = None

    async def start_login(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        on_connected: Any | None = None,
    ) -> dict[str, Any]:
        if client_id is not None and client_id.strip():
            self.vault.set(VAULT_CLIENT_ID, client_id.strip())
        if client_secret is not None and client_secret.strip():
            self.vault.set(VAULT_CLIENT_SECRET, client_secret.strip())

        cid = self._client_id()
        csec = self._client_secret()
        if not cid:
            raise GoogleOAuthError(
                "missing_client_id",
                "Enter your Google OAuth Client ID first, or use the one-click Google Popup & Sync flow below.",
            )

        await self._stop_pending()
        verifier, challenge = make_pkce_pair()
        state = secrets.token_urlsafe(24)

        pending = _PendingLogin(
            state=state,
            code_verifier=verifier,
            redirect_uri="",
            client_id=cid,
            client_secret=csec,
        )

        async def handle_callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                req_line = await asyncio.wait_for(reader.readline(), timeout=10.0)
                while True:
                    hdr = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if not hdr or hdr in (b"\r\n", b"\n"):
                        break
                parts = req_line.decode("utf-8", errors="replace").split(" ")
                target = parts[1] if len(parts) >= 2 else "/"
                qs = parse_qs(urlparse(target).query)
                if qs.get("error"):
                    err = str(qs["error"][0])
                    pending.error = f"Google sign-in declined: {err}"
                    pending.done = True
                    body_html = self._result_html(False, pending.error)
                else:
                    code = str((qs.get("code") or [""])[0])
                    cb_state = str((qs.get("state") or [""])[0])
                    if not code or cb_state != pending.state:
                        pending.error = "Invalid OAuth state returned from Google."
                        pending.done = True
                        body_html = self._result_html(False, pending.error)
                    else:
                        try:
                            await self._exchange_code(pending, code)
                            if on_connected is not None:
                                await on_connected(self.status())
                            body_html = self._result_html(True, "You can return to MiniMuse now.")
                        except Exception as exc:  # noqa: BLE001
                            pending.error = str(exc)
                            pending.done = True
                            body_html = self._result_html(False, str(exc))
                raw = body_html.encode("utf-8")
                headers = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(raw)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                writer.write(headers + raw)
                await writer.drain()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google OAuth callback listener error: {}", exc)
            finally:
                writer.close()

        server = await asyncio.start_server(handle_callback, "127.0.0.1", 0)
        sock = server.sockets[0]
        port = sock.getsockname()[1]
        pending.server = server
        pending.redirect_uri = f"http://127.0.0.1:{port}/oauth/google/callback"
        self._pending = pending

        async def expire() -> None:
            await asyncio.sleep(LOGIN_TIMEOUT_S)
            if self._pending is pending and not pending.done:
                pending.error = "Google sign-in timed out."
                pending.done = True
                await self._stop_pending()

        pending.timeout_task = asyncio.create_task(expire())

        params = {
            "client_id": cid,
            "redirect_uri": pending.redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent select_account",
            "include_granted_scopes": "true",
        }
        return {
            "auth_url": f"{AUTHORIZE_URL}?{urlencode(params)}",
            "redirect_uri": pending.redirect_uri,
            "state": state,
        }

    @staticmethod
    def _result_html(ok: bool, detail: str) -> str:
        title = "MiniMuse Connected" if ok else "MiniMuse Connection Error"
        color = "#10b981" if ok else "#ef4444"
        return (
            "<!doctype html><html><head><meta charset='utf-8'><title>"
            + html.escape(title)
            + "</title></head><body style='font-family:system-ui,-apple-system,sans-serif;background:#111;color:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0'>"
            "<div style='max-width:420px;padding:28px;border-radius:18px;background:#1c1c1e;border:1px solid #2e2e32;text-align:center'>"
            f"<h2 style='margin:0 0 10px;color:{color}'>{html.escape(title)}</h2>"
            f"<p style='margin:0;color:#b8b8c2;line-height:1.5'>{html.escape(detail)}</p>"
            "</div></body></html>"
        )

    async def complete_callback(self, url_or_code: str, *, on_connected: Any | None = None) -> dict[str, Any]:
        pending = self._pending
        if pending is None or pending.done:
            raise GoogleOAuthError("no_pending", "Start Google sign-in before submitting the callback URL.")
        raw = url_or_code.strip()
        if raw.startswith(("http://", "https://")):
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query)
            if qs.get("error"):
                raise GoogleOAuthError("oauth_error", f"Google error: {qs['error'][0]}")
            code = str((qs.get("code") or [""])[0]).strip()
            state = str((qs.get("state") or [""])[0]).strip()
            if state and state != pending.state:
                raise GoogleOAuthError("invalid_state", "Callback state mismatch.")
        else:
            code = raw
        if not code:
            raise GoogleOAuthError("missing_code", "No authorization code found in callback URL.")
        await self._exchange_code(pending, code)
        if on_connected is not None:
            await on_connected(self.status())
        return self.status()

    async def _exchange_code(self, pending: _PendingLogin, code: str) -> dict[str, Any]:
        payload: dict[str, str] = {
            "code": code,
            "client_id": pending.client_id,
            "redirect_uri": pending.redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": pending.code_verifier,
        }
        if pending.client_secret:
            payload["client_secret"] = pending.client_secret
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(TOKEN_URL, data=payload)
            if resp.status_code >= 400:
                raise GoogleOAuthError(
                    "token_exchange_failed",
                    f"Google token exchange failed ({resp.status_code}): {resp.text[:200]}",
                )
            tok = resp.json()
            access = str(tok.get("access_token") or "").strip()
            if not access:
                raise GoogleOAuthError("token_exchange_failed", "No access_token returned by Google.")
            info_resp = await client.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {access}"},
            )
            info = info_resp.json() if info_resp.status_code == 200 else {}

        expires_in = float(tok.get("expires_in") or 3600)
        existing = self._read_store()
        acct_email = str(info.get("email") or existing.get("email") or "").strip()
        accounts = self._accounts_map(existing)
        prev_acct = accounts.get(acct_email, {})
        acct_entry = {
            "email": acct_email,
            "name": str(info.get("name") or prev_acct.get("name") or existing.get("name") or "").strip(),
            "access_token": access,
            "refresh_token": str(tok.get("refresh_token") or prev_acct.get("refresh_token") or existing.get("refresh_token") or "").strip(),
            "expires_at": time.time() + expires_in,
            "scopes": str(tok.get("scope") or "").split(),
            "updated_at": datetime.now(UTC).isoformat(),
            "last_sync_at": prev_acct.get("last_sync_at"),
            "last_sync": prev_acct.get("last_sync"),
        }
        if acct_email:
            accounts[acct_email] = acct_entry
        saved = {
            "primary_email": existing.get("primary_email") or acct_email,
            "email": acct_email,
            "name": acct_entry["name"],
            "access_token": acct_entry["access_token"],
            "refresh_token": acct_entry["refresh_token"],
            "expires_at": acct_entry["expires_at"],
            "scopes": acct_entry["scopes"],
            "updated_at": acct_entry["updated_at"],
            "accounts": accounts,
            "last_sync_at": existing.get("last_sync_at"),
            "last_sync": existing.get("last_sync"),
        }
        self._write_store(saved)
        self._sync_vault(acct_entry, account_email=acct_email or None)
        pending.done = True
        pending.error = ""
        await self._stop_pending()
        return saved

    async def ensure_access_token(self, account_email: str | None = None) -> tuple[str, str]:
        store = self._read_store()
        accounts = self._accounts_map(store)
        target_email = (account_email or "").strip() or str(store.get("primary_email") or store.get("email") or self.vault.get(VAULT_EMAIL) or "").strip()
        acct = accounts.get(target_email, {})
        email = target_email or str(acct.get("email") or "").strip()
        access = str(
            acct.get("access_token")
            or (self.vault.get(self._acct_vault_key(email, "ACCESS_TOKEN")) if email else "")
            or store.get("access_token")
            or self.vault.get(VAULT_ACCESS_TOKEN)
            or ""
        ).strip()
        expires_at = float(
            acct.get("expires_at")
            or (self.vault.get(self._acct_vault_key(email, "EXPIRES_AT")) if email else 0.0)
            or store.get("expires_at")
            or self.vault.get(VAULT_EXPIRES_AT)
            or 0.0
        )
        if access and expires_at - REFRESH_MARGIN_S > time.time():
            return access, email

        refresh_tok = str(
            acct.get("refresh_token")
            or (self.vault.get(self._acct_vault_key(email, "REFRESH_TOKEN")) if email else "")
            or store.get("refresh_token")
            or self.vault.get(VAULT_REFRESH_TOKEN)
            or ""
        ).strip()
        # Temporarily ensure vault keys match target account if refreshing specific account
        if email and refresh_tok:
            self.vault.set(VAULT_REFRESH_TOKEN, refresh_tok)
            if access:
                self.vault.set(VAULT_ACCESS_TOKEN, access)
            self.vault.set(VAULT_EXPIRES_AT, str(expires_at))
        new_access = await asyncio.to_thread(refresh_access_token_sync, self.vault)
        exp_raw = str(self.vault.get(VAULT_EXPIRES_AT) or "")
        new_exp = float(exp_raw) if exp_raw else (time.time() + 3600)
        if email and email in accounts:
            accounts[email]["access_token"] = new_access
            accounts[email]["expires_at"] = new_exp
            store["accounts"] = accounts
            self.vault.set(self._acct_vault_key(email, "ACCESS_TOKEN"), new_access)
            self.vault.set(self._acct_vault_key(email, "EXPIRES_AT"), str(new_exp))
        if not account_email or email == store.get("email"):
            store["access_token"] = new_access
            store["expires_at"] = new_exp
            if email:
                store["email"] = email
        self._write_store(store)
        return new_access, email

    async def _sync_one_account(self, connections: Any, account_email: str | None, multi: bool) -> dict[str, Any]:
        access, email = await self.ensure_access_token(account_email=account_email)
        headers = {"Authorization": f"Bearer {access}"}
        now = datetime.now(UTC)
        time_min = (now - timedelta(days=7)).isoformat()
        time_max = (now + timedelta(days=90)).isoformat()

        cal_events = 0
        contacts_count = 0
        errors: list[str] = []
        cal_feed_name = f"Google Calendar ({email})" if (multi and email) else "Google Calendar"
        contacts_src_name = f"Google Contacts ({email})" if (multi and email) else "Google Contacts"
        safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", email or "default")

        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                cal_resp = await client.get(
                    CALENDAR_EVENTS_URL,
                    headers=headers,
                    params={
                        "timeMin": time_min,
                        "timeMax": time_max,
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": 250,
                    },
                )
                if cal_resp.status_code == 200:
                    items = list(cal_resp.json().get("items") or [])
                    cal_events = len([i for i in items if i.get("status") != "cancelled"])
                    ics_text = calendar_events_to_ics(
                        items,
                        calendar_name=f"Google Calendar ({email})" if email else "Google Calendar",
                    )
                    cal_dir = self.data_dir / "calendar"
                    cal_dir.mkdir(parents=True, exist_ok=True)
                    ics_name = f"google-calendar-{safe_slug}.ics" if multi else "google-calendar.ics"
                    ics_path = cal_dir / ics_name
                    ics_path.write_text(ics_text, encoding="utf-8")
                    keep_private(ics_path)
                    await connections.add_calendar_feed({"name": cal_feed_name, "url": str(ics_path)})
                else:
                    errors.append(f"Calendar API ({cal_resp.status_code})")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Calendar: {exc}")

            try:
                ppl_resp = await client.get(
                    PEOPLE_CONNECTIONS_URL,
                    headers=headers,
                    params={
                        "personFields": "names,emailAddresses,phoneNumbers,organizations,biographies,addresses",
                        "pageSize": 500,
                    },
                )
                if ppl_resp.status_code == 200:
                    conns = list(ppl_resp.json().get("connections") or [])
                    vcf_text = people_connections_to_vcf(conns)
                    if vcf_text.strip():
                        await connections.import_contacts(contacts_src_name, vcf_text)
                        contacts_count = vcf_text.count("BEGIN:VCARD")
                else:
                    errors.append(f"Contacts API ({ppl_resp.status_code})")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Contacts: {exc}")

        return {
            "calendar_events": cal_events,
            "contacts": contacts_count,
            "email": email,
            "errors": errors,
        }

    async def sync_all(self, connections: Any, account_email: str | None = None) -> dict[str, Any]:
        """Pull Google Calendar events and Google Contacts for one or all connected Google accounts."""
        store = self._read_store()
        accounts = self._accounts_map(store)
        targets = [account_email] if account_email else (list(accounts.keys()) or [None])
        multi = len(targets) > 1
        total_cal = 0
        total_contacts = 0
        all_errors: list[str] = []
        last_email = ""
        synced_at = datetime.now(UTC).isoformat()

        for target in targets:
            res = await self._sync_one_account(connections, target, multi=multi)
            total_cal += int(res["calendar_events"])
            total_contacts += int(res["contacts"])
            last_email = str(res["email"] or last_email)
            all_errors.extend(res["errors"])
            if target and target in accounts:
                accounts[target]["last_sync_at"] = synced_at
                accounts[target]["last_sync"] = res

        summary = {
            "calendar_events": total_cal,
            "contacts": total_contacts,
            "email": last_email,
            "accounts_synced": len(targets),
            "errors": all_errors,
        }
        store = self._read_store()
        if accounts:
            store["accounts"] = accounts
        store["last_sync_at"] = synced_at
        store["last_sync"] = summary
        self._write_store(store)
        self._last_sync_at = synced_at
        self._last_sync_summary = summary
        return {
            "ok": len(all_errors) == 0,
            **summary,
            "last_sync_at": synced_at,
        }

    async def logout(self, account_email: str | None = None) -> dict[str, Any]:
        await self._stop_pending()
        store = self._read_store()
        accounts = self._accounts_map(store)
        target = (account_email or "").strip()
        if target and target in accounts:
            accounts.pop(target, None)
            for suf in ("ACCESS_TOKEN", "REFRESH_TOKEN", "EXPIRES_AT"):
                self.vault.delete(self._acct_vault_key(target, suf))
            if accounts:
                next_primary = next(iter(accounts))
                next_info = accounts[next_primary]
                store["accounts"] = accounts
                store["primary_email"] = next_primary
                store["email"] = next_primary
                store["access_token"] = next_info.get("access_token") or ""
                store["refresh_token"] = next_info.get("refresh_token") or ""
                store["expires_at"] = next_info.get("expires_at") or 0.0
                self._write_store(store)
                self._sync_vault(next_info)
                return self.status()

        if self.store_path.is_file():
            try:
                self.store_path.unlink()
            except OSError:
                pass
        for em in list(accounts):
            for suf in ("ACCESS_TOKEN", "REFRESH_TOKEN", "EXPIRES_AT"):
                self.vault.delete(self._acct_vault_key(em, suf))
        for key in (VAULT_ACCESS_TOKEN, VAULT_REFRESH_TOKEN, VAULT_EXPIRES_AT, VAULT_EMAIL):
            self.vault.delete(key)
        return self.status()
