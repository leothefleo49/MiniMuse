"""Encrypted password manager store with CSV/JSON import, TOTP, and Vault sync."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import re
import struct
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanomuse.vault.vault import CredentialVault

logger = logging.getLogger(__name__)


def _extract_domain(url_or_host: str) -> str:
    raw = (url_or_host or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urllib.parse.urlparse(raw)
        host = (parsed.hostname or "").lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def compute_totp(secret_or_uri: str, now: float | None = None) -> tuple[str, int]:
    """Compute an RFC 6238 TOTP code from a base32 secret or otpauth:// URI."""
    raw = (secret_or_uri or "").strip()
    if not raw:
        raise ValueError("No TOTP secret configured for this login.")
    digits = 6
    period = 30
    algo = "SHA1"
    if raw.lower().startswith("otpauth://"):
        parsed = urllib.parse.urlparse(raw)
        qs = urllib.parse.parse_qs(parsed.query)
        raw = (qs.get("secret") or [""])[0]
        if "digits" in qs:
            try:
                digits = int(qs["digits"][0])
            except ValueError:
                digits = 6
        if "period" in qs:
            try:
                period = max(1, int(qs["period"][0]))
            except ValueError:
                period = 30
        if "algorithm" in qs:
            algo = qs["algorithm"][0].upper()

    cleaned = re.sub(r"[^A-Za-z2-7]", "", raw).upper()
    if not cleaned:
        raise ValueError("Invalid base32 TOTP secret.")
    padding = (8 - len(cleaned) % 8) % 8
    key = base64.b32decode(cleaned + ("=" * padding), casefold=True)

    ts = time.time() if now is None else now
    counter = int(ts // period)
    remaining = int(period - (int(ts) % period))
    msg = struct.pack(">Q", counter)
    digest_mod = {
        "SHA1": hashlib.sha1,
        "SHA256": hashlib.sha256,
        "SHA512": hashlib.sha512,
    }.get(algo, hashlib.sha1)
    mac = hmac.new(key, msg, digest_mod).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    code = str(binary % (10**digits)).zfill(digits)
    return code, remaining


class PasswordStore:
    """Encrypted password & login store synced with CredentialVault."""

    def __init__(self, data_dir: Path, vault: CredentialVault) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.vault = vault
        self.store_path = self.data_dir / "passwords.enc"
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    @staticmethod
    def vault_key_for(entry_id: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_]", "_", entry_id).upper()
        return f"LOGIN_{clean}"

    def _load(self) -> None:
        if not self.store_path.exists():
            self._entries = {}
            return
        try:
            encrypted = self.store_path.read_bytes()
            decrypted = self.vault.fernet.decrypt(encrypted)
            payload = json.loads(decrypted.decode("utf-8"))
            if isinstance(payload, dict):
                self._entries = payload
            else:
                self._entries = {}
            self._sync_all_to_vault()
        except Exception as exc:
            logger.error("Failed to load password store: %s", exc)
            self._entries = {}

    def _save(self) -> None:
        data = json.dumps(self._entries, ensure_ascii=False).encode("utf-8")
        encrypted = self.vault.fernet.encrypt(data)
        self.store_path.write_bytes(encrypted)

    def _sync_all_to_vault(self) -> None:
        for entry_id, item in self._entries.items():
            pw = item.get("password") or ""
            vkey = self.vault_key_for(entry_id)
            if pw and self.vault.get(vkey) != pw:
                self.vault.set(vkey, pw)

    def _sanitize(self, item: dict[str, Any]) -> dict[str, Any]:
        entry_id = str(item.get("id") or "")
        url = str(item.get("url") or "")
        domain = str(item.get("domain") or _extract_domain(url))
        title = str(item.get("title") or domain or url or "Saved Login")
        return {
            "id": entry_id,
            "title": title,
            "url": url,
            "domain": domain,
            "username": str(item.get("username") or ""),
            "has_password": bool(item.get("password")),
            "has_totp": bool(item.get("totp")),
            "notes": str(item.get("notes") or ""),
            "vault_key": self.vault_key_for(entry_id),
            "updated_at": str(item.get("updated_at") or ""),
        }

    def list_entries(self, query: str = "") -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        results: list[dict[str, Any]] = []
        for item in self._entries.values():
            s = self._sanitize(item)
            if q:
                haystack = " ".join(
                    [s["id"], s["title"], s["url"], s["domain"], s["username"], s["notes"]]
                ).lower()
                if q not in haystack:
                    continue
            results.append(s)
        results.sort(key=lambda r: (r["title"].lower(), r["username"].lower()))
        return results

    def get_raw(self, entry_id: str) -> dict[str, Any] | None:
        return self._entries.get(entry_id)

    def find_best(self, site_or_id: str, username: str = "") -> dict[str, Any] | None:
        target = (site_or_id or "").strip()
        if not target:
            return None
        if target in self._entries:
            return self._entries[target]
        target_lower = target.lower()
        target_domain = _extract_domain(target) or target_lower
        user_lower = (username or "").strip().lower()

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in self._entries.values():
            score = 0
            item_id = str(item.get("id") or "").lower()
            title = str(item.get("title") or "").lower()
            url = str(item.get("url") or "").lower()
            domain = str(item.get("domain") or _extract_domain(url)).lower()
            user = str(item.get("username") or "").lower()

            if item_id == target_lower:
                score += 100
            if domain and target_domain:
                if domain == target_domain:
                    score += 80
                elif domain.endswith("." + target_domain) or target_domain.endswith("." + domain):
                    score += 65
                elif target_domain in domain:
                    score += 45
            if title == target_lower:
                score += 70
            elif target_lower in title:
                score += 40
            if target_lower in url:
                score += 35
            if user_lower and user == user_lower:
                score += 30
            elif user_lower and user_lower in user:
                score += 15
            if score > 0:
                scored.append((score, item))

        if not scored:
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored[0][1]

    def add_or_update(
        self,
        *,
        entry_id: str = "",
        title: str = "",
        url: str = "",
        username: str = "",
        password: str = "",
        totp: str = "",
        notes: str = "",
        dedupe: bool = False,
    ) -> tuple[dict[str, Any], str]:
        """Add or update a login. Returns (sanitized_entry, 'added'|'updated'|'skipped')."""
        url = (url or "").strip()
        domain = _extract_domain(url)
        username = (username or "").strip()
        title = (title or "").strip() or domain or url or username or "Login"
        now_iso = datetime.now(timezone.utc).isoformat()

        existing_id = (entry_id or "").strip()
        if not existing_id and dedupe:
            for eid, cur in self._entries.items():
                cur_domain = str(cur.get("domain") or _extract_domain(str(cur.get("url") or "")))
                cur_title = str(cur.get("title") or "").lower()
                cur_user = str(cur.get("username") or "").lower()
                same_site = (domain and cur_domain == domain) or (
                    not domain and cur_title == title.lower()
                )
                if same_site and cur_user == username.lower():
                    existing_id = eid
                    break

        if existing_id and existing_id in self._entries:
            cur = self._entries[existing_id]
            cur["title"] = title or cur.get("title") or "Login"
            cur["url"] = url or cur.get("url") or ""
            cur["domain"] = domain or cur.get("domain") or ""
            cur["username"] = username if username else cur.get("username", "")
            if password:
                cur["password"] = password
            if totp:
                cur["totp"] = totp.strip()
            if notes:
                cur["notes"] = notes.strip()
            cur["updated_at"] = now_iso
            vkey = self.vault_key_for(existing_id)
            if cur.get("password"):
                self.vault.set(vkey, cur["password"])
            self._save()
            return self._sanitize(cur), "updated"

        if not existing_id:
            existing_id = uuid.uuid4().hex[:10]

        record = {
            "id": existing_id,
            "title": title,
            "url": url,
            "domain": domain,
            "username": username,
            "password": password,
            "totp": (totp or "").strip(),
            "notes": (notes or "").strip(),
            "updated_at": now_iso,
        }
        self._entries[existing_id] = record
        vkey = self.vault_key_for(existing_id)
        if password:
            self.vault.set(vkey, password)
        self._save()
        return self._sanitize(record), "added"

    def delete(self, entry_id: str) -> bool:
        if entry_id not in self._entries:
            return False
        del self._entries[entry_id]
        vkey = self.vault_key_for(entry_id)
        self.vault.delete(vkey)
        self._save()
        return True

    def totp_code(self, entry_id: str) -> tuple[str, int]:
        item = self._entries.get(entry_id)
        if not item:
            item = self.find_best(entry_id)
        if not item:
            raise KeyError(f"Login '{entry_id}' not found.")
        secret = str(item.get("totp") or "").strip()
        return compute_totp(secret)

    def import_data(self, text: str, filename: str = "") -> dict[str, Any]:
        """Import passwords from CSV or JSON exports (Chrome, Bitwarden, 1Password, Apple, etc.)."""
        content = (text or "").lstrip("\ufeff").strip()
        if not content:
            return {"added": 0, "updated": 0, "skipped": 0, "total": 0, "items": []}

        rows: list[dict[str, str]] = []
        if content.startswith("{") or content.startswith("[") or filename.lower().endswith(".json"):
            try:
                rows = self._parse_json_import(content)
            except Exception:
                rows = self._parse_csv_import(content)
        else:
            rows = self._parse_csv_import(content)

        added = 0
        updated = 0
        skipped = 0
        for row in rows:
            url = (row.get("url") or "").strip()
            username = (row.get("username") or "").strip()
            password = row.get("password") or ""
            title = (row.get("title") or "").strip()
            totp = (row.get("totp") or "").strip()
            notes = (row.get("notes") or "").strip()
            if not (url or title) or not (username or password):
                skipped += 1
                continue
            _, status = self.add_or_update(
                title=title,
                url=url,
                username=username,
                password=password,
                totp=totp,
                notes=notes,
                dedupe=True,
            )
            if status == "added":
                added += 1
            elif status == "updated":
                updated += 1
            else:
                skipped += 1

        all_items = self.list_entries()
        return {
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "total": len(all_items),
            "items": all_items,
        }

    def _parse_json_import(self, content: str) -> list[dict[str, str]]:
        data = json.loads(content)
        raw_items: list[Any] = []
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            for key in ("items", "logins", "entries", "passwords", "accounts"):
                if isinstance(data.get(key), list):
                    raw_items = data[key]
                    break
        parsed: list[dict[str, str]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            login_block = item.get("login") if isinstance(item.get("login"), dict) else {}
            uris = login_block.get("uris") if isinstance(login_block.get("uris"), list) else []
            first_uri = ""
            if uris and isinstance(uris[0], dict):
                first_uri = str(uris[0].get("uri") or "")
            url = str(
                item.get("url")
                or item.get("website")
                or item.get("login_uri")
                or first_uri
                or ""
            )
            username = str(
                item.get("username")
                or item.get("email")
                or item.get("login_username")
                or login_block.get("username")
                or ""
            )
            password = str(
                item.get("password")
                or item.get("login_password")
                or login_block.get("password")
                or ""
            )
            totp = str(
                item.get("totp")
                or item.get("otp")
                or item.get("otpauth")
                or login_block.get("totp")
                or ""
            )
            title = str(item.get("title") or item.get("name") or _extract_domain(url) or "")
            notes = str(item.get("notes") or item.get("note") or item.get("extra") or "")
            parsed.append(
                {
                    "title": title,
                    "url": url,
                    "username": username,
                    "password": password,
                    "totp": totp,
                    "notes": notes,
                }
            )
        return parsed

    def _parse_csv_import(self, content: str) -> list[dict[str, str]]:
        reader = csv.DictReader(io.StringIO(content))
        parsed: list[dict[str, str]] = []
        for raw_row in reader:
            if not isinstance(raw_row, dict):
                continue
            norm = {
                re.sub(r"[^a-z0-9]", "", (k or "").lower()): (v or "").strip()
                for k, v in raw_row.items()
                if k is not None
            }
            url = (
                norm.get("url")
                or norm.get("loginuri")
                or norm.get("website")
                or norm.get("site")
                or norm.get("origin")
                or norm.get("formactionorigin")
                or norm.get("domain")
                or ""
            )
            username = (
                norm.get("username")
                or norm.get("loginusername")
                or norm.get("user")
                or norm.get("email")
                or norm.get("login")
                or norm.get("account")
                or ""
            )
            password = (
                norm.get("password")
                or norm.get("loginpassword")
                or norm.get("pass")
                or norm.get("secret")
                or ""
            )
            title = (
                norm.get("title")
                or norm.get("name")
                or norm.get("servicename")
                or _extract_domain(url)
                or ""
            )
            totp = (
                norm.get("totp")
                or norm.get("logintotp")
                or norm.get("otpauth")
                or norm.get("otp")
                or norm.get("twofactor")
                or ""
            )
            notes = (
                norm.get("notes")
                or norm.get("note")
                or norm.get("extra")
                or norm.get("comment")
                or ""
            )
            parsed.append(
                {
                    "title": title,
                    "url": url,
                    "username": username,
                    "password": password,
                    "totp": totp,
                    "notes": notes,
                }
            )
        return parsed

    def export_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "url", "username", "password", "totp", "note"])
        for item in self._entries.values():
            writer.writerow(
                [
                    item.get("title") or "",
                    item.get("url") or "",
                    item.get("username") or "",
                    item.get("password") or "",
                    item.get("totp") or "",
                    item.get("notes") or "",
                ]
            )
        return buf.getvalue()
