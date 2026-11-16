"""Multi-account store for Email accounts (IMAP/SMTP & Google OAuth Gmail)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanomuse.private import keep_private

if TYPE_CHECKING:
    from nanomuse.vault import CredentialVault


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _vault_key_for_email(acct_id: str) -> str:
    return f"EMAIL_ACCT_{re.sub(r'[^A-Z0-9]+', '_', acct_id.upper()).strip('_')}_PASSWORD"


class EmailAccountsStore:
    """Persistent multi-account email store backed by vault secrets."""

    FILE_NAME = "email_accounts.json"

    def __init__(self, data_dir: Path, vault: CredentialVault | None = None):
        self.data_dir = Path(data_dir)
        self.vault = vault
        self.path = self.data_dir / self.FILE_NAME
        self._items: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            rows = raw.get("accounts") if isinstance(raw, dict) else raw
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("id"):
                        self._items[str(row["id"])] = row
        except (OSError, ValueError):
            self._items = {}

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "accounts": list(self._items.values())}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        keep_private(self.path)

    def ensure_migrated_legacy(self, legacy_email: Any) -> None:
        """Import single config.connectors.email entry into multi-account store on first read."""
        addr = str(getattr(legacy_email, "address", "") or "").strip()
        if not addr or (addr.startswith("{{") and addr.endswith("}}")) or self.find_by_address(addr):
            return
        imap_host = str(getattr(legacy_email, "imap_host", "") or "").strip()
        smtp_host = str(getattr(legacy_email, "smtp_host", "") or "").strip()
        if imap_host.startswith("{{") and imap_host.endswith("}}"):
            imap_host = ""
        if smtp_host.startswith("{{") and smtp_host.endswith("}}"):
            smtp_host = ""
        acct_id = f"email_{uuid.uuid4().hex[:6]}"
        vkey = _vault_key_for_email(acct_id)
        if self.vault is not None:
            legacy_pw = self.vault.get("EMAIL_PASSWORD")
            if legacy_pw:
                self.vault.set(vkey, legacy_pw)
        self._items[acct_id] = {
            "id": acct_id,
            "label": "Primary",
            "address": addr,
            "imap_host": imap_host,
            "imap_port": int(getattr(legacy_email, "imap_port", 993) or 993),
            "smtp_host": smtp_host,
            "smtp_port": int(getattr(legacy_email, "smtp_port", 587) or 587),
            "smtp_starttls": bool(getattr(legacy_email, "smtp_starttls", True)),
            "auth_mode": str(getattr(legacy_email, "auth_mode", "password") or "password"),
            "vault_key": vkey,
            "enabled": bool(getattr(legacy_email, "enabled", True)),
            "primary": True,
            "created_at": _now_iso(),
        }
        self._save()

    def find_by_address(self, address: str) -> dict[str, Any] | None:
        needle = (address or "").strip().lower()
        if not needle:
            return None
        for row in self._items.values():
            if str(row.get("address") or "").strip().lower() == needle:
                return row
        return None

    def get(self, account_id: str) -> dict[str, Any] | None:
        if account_id in self._items:
            return self._items[account_id]
        return self.find_by_address(account_id)

    def _public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        vkey = str(row.get("vault_key") or "")
        pw_set = bool(
            row.get("auth_mode") == "oauth"
            or (self.vault is not None and vkey and self.vault.get(vkey))
        )
        return {
            "id": row["id"],
            "label": row.get("label") or row.get("address") or "Email",
            "address": row.get("address") or "",
            "imap_host": row.get("imap_host") or "",
            "imap_port": int(row.get("imap_port") or 993),
            "smtp_host": row.get("smtp_host") or "",
            "smtp_port": int(row.get("smtp_port") or 587),
            "smtp_starttls": bool(row.get("smtp_starttls", True)),
            "auth_mode": row.get("auth_mode") or "password",
            "password_set": pw_set,
            "enabled": bool(row.get("enabled", True)),
            "primary": bool(row.get("primary", False)),
        }

    def list(self) -> list[dict[str, Any]]:
        rows = sorted(
            self._items.values(),
            key=lambda r: (not bool(r.get("primary")), str(r.get("address") or "")),
        )
        return [self._public_row(r) for r in rows]

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        acct_id = str(payload.get("id") or "").strip()
        address = str(payload.get("address") or "").strip()
        if not acct_id and address:
            existing = self.find_by_address(address)
            if existing:
                acct_id = existing["id"]
        if not acct_id:
            acct_id = f"email_{uuid.uuid4().hex[:6]}"
        cur = dict(self._items.get(acct_id) or {})
        vkey = str(cur.get("vault_key") or _vault_key_for_email(acct_id))

        if payload.get("primary") or not self._items:
            for other in self._items.values():
                other["primary"] = False
            is_primary = True
        else:
            is_primary = bool(payload.get("primary", cur.get("primary", False)))

        updated = {
            "id": acct_id,
            "label": str(payload.get("label") or cur.get("label") or address or "Email").strip(),
            "address": address or cur.get("address") or "",
            "imap_host": str(payload.get("imap_host") if payload.get("imap_host") is not None else cur.get("imap_host") or ""),
            "imap_port": int(payload.get("imap_port") or cur.get("imap_port") or 993),
            "smtp_host": str(payload.get("smtp_host") if payload.get("smtp_host") is not None else cur.get("smtp_host") or ""),
            "smtp_port": int(payload.get("smtp_port") or cur.get("smtp_port") or 587),
            "smtp_starttls": bool(
                payload["smtp_starttls"]
                if "smtp_starttls" in payload and payload["smtp_starttls"] is not None
                else cur.get("smtp_starttls", True)
            ),
            "auth_mode": str(payload.get("auth_mode") or cur.get("auth_mode") or "password"),
            "vault_key": vkey,
            "enabled": bool(
                payload["enabled"]
                if "enabled" in payload and payload["enabled"] is not None
                else cur.get("enabled", True)
            ),
            "primary": is_primary,
            "created_at": cur.get("created_at") or _now_iso(),
        }
        pw = payload.get("password")
        if pw is not None and str(pw) and self.vault is not None:
            self.vault.set(vkey, str(pw))
            if is_primary:
                self.vault.set("EMAIL_PASSWORD", str(pw))
        self._items[acct_id] = updated
        self._save()
        return self._public_row(updated)

    def remove(self, account_id: str) -> bool:
        row = self.get(account_id)
        if not row:
            return False
        rid = row["id"]
        if self.vault is not None and row.get("vault_key"):
            self.vault.delete(str(row["vault_key"]))
        was_primary = bool(row.get("primary"))
        self._items.pop(rid, None)
        if was_primary and self._items:
            first_key = next(iter(self._items))
            self._items[first_key]["primary"] = True
        self._save()
        return True

    def password_for(self, account_id: str) -> str:
        row = self.get(account_id)
        if not row or self.vault is None:
            return ""
        vkey = str(row.get("vault_key") or "")
        return str((self.vault.get(vkey) if vkey else "") or "")
