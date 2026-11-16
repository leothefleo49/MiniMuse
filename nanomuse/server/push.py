"""Web Push: the phone buzzes when your nanoMuse needs you or has something to say.

Standard Web Push (VAPID + RFC 8291 payload encryption, via ``pywebpush``), so it works
with the browser's own push service — no account, no third party of ours. The VAPID key
pair is generated once into ``<data_dir>/push-vapid.json``; subscriptions live in
``<data_dir>/push-subscriptions.json``. Endpoints that the push service reports gone
(404/410) are dropped.

What gets pushed is decided in :mod:`nanomuse.server.service`: a card waiting for you
(approval, question), the result of a background pass that was worth surfacing, a
check-in. The service worker on the phone shows nothing when the app is already on
screen — the card is right there.

Browsers only allow push in a secure context: ``https://`` or ``localhost``. Over plain
LAN http the app still works, only this part stays off (see docs/deployment.md).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanomuse.background import keep_task
from nanomuse.logger import logger
from nanomuse.private import keep_private

VAPID_FILE = "push-vapid.json"
SUBSCRIPTIONS_FILE = "push-subscriptions.json"
MAX_SUBSCRIPTIONS = 20


def available() -> bool:
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return False
    return True


def termux_available() -> bool:
    """Whether this phone's Termux:API can post a notification (docs/phone-only.md).

    nanoMuse runs inside Termux (or in the Debian container started from it), where
    Termux:API puts ``termux-notification`` on PATH.  The desktop and the Android app have
    their own notification paths; this one is for the runtime that has no browser open.
    """
    if not (
        os.environ.get("TERMUX_VERSION")
        or Path("/data/data/com.termux/files/usr/bin").is_dir()
        or Path("/data/data/com.termux").is_dir()
    ):
        return False
    return bool(shutil.which("termux-notification"))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to a file only its owner can read: created with mode 0600 before any
    content lands in it, so there is no moment with the secrets world-readable."""
    path.touch(mode=0o600, exist_ok=True)
    try:
        keep_private(path)  # an older file written without the mode
    except OSError:
        pass
    path.write_text(text, encoding="utf-8")


class PushService:
    def __init__(
        self,
        data_dir: Path,
        contact: str = "mailto:nanomuse@localhost",
        port: int = 8787,
    ):
        self.data_dir = data_dir
        self.contact = contact
        self.port = port
        self._vapid = data_dir / VAPID_FILE
        self._subs_file = data_dir / SUBSCRIPTIONS_FILE
        self.private_key_pem: str = ""
        self.public_key: str = ""
        self.subscriptions: list[dict[str, Any]] = []
        self._load()

    # ------------------------------------------------------------------ keys / storage
    def _load(self) -> None:
        if self._vapid.is_file():
            try:
                data = json.loads(self._vapid.read_text(encoding="utf-8"))
                self.private_key_pem = data["private_key_pem"]
                self.public_key = data["public_key"]
            except (ValueError, KeyError, TypeError, OSError):
                logger.warning("push: unreadable {}; generating a new key pair", self._vapid)
        if not self.public_key and available():
            self._generate_keys()
        if self._subs_file.is_file():
            try:
                self.subscriptions = [
                    s
                    for s in json.loads(self._subs_file.read_text(encoding="utf-8"))
                    if isinstance(s, dict) and s.get("endpoint")
                ]
            except (ValueError, TypeError, OSError):
                logger.warning(
                    "push: unreadable {}; starting without subscriptions", self._subs_file
                )
                self.subscriptions = []

    def _generate_keys(self) -> None:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        from py_vapid import Vapid

        v = Vapid()
        v.generate_keys()
        pem = v.private_pem()
        self.private_key_pem = pem.decode() if isinstance(pem, bytes) else str(pem)
        self.public_key = _b64url(
            v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_private(
            self._vapid,
            json.dumps({"private_key_pem": self.private_key_pem, "public_key": self.public_key}),
        )

    def _save_subscriptions(self) -> None:
        # a subscription carries the browser's p256dh and auth secrets: whoever holds them
        # and the VAPID key can send that browser notifications, so the file is ours only
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_private(self._subs_file, json.dumps(self.subscriptions, indent=1))

    # ------------------------------------------------------------------ subscriptions
    @property
    def enabled(self) -> bool:
        return bool(self.public_key) and available()

    def view(self) -> dict[str, Any]:
        return {
            "available": available(),
            "termux": termux_available(),
            "public_key": self.public_key,
            "subscriptions": len(self.subscriptions),
            "devices": [
                {
                    "endpoint": s["endpoint"],
                    "created_at": s.get("created_at"),
                    "ua": s.get("ua", ""),
                }
                for s in self.subscriptions
            ],
        }

    def subscribe(self, subscription: dict[str, Any], ua: str = "") -> int:
        endpoint = str(subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys") or {}
        if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("a push subscription needs an https endpoint and p256dh/auth keys")
        self.subscriptions = [s for s in self.subscriptions if s["endpoint"] != endpoint]
        self.subscriptions.append(
            {
                "endpoint": endpoint,
                "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])},
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "ua": ua[:120],
            }
        )
        # the oldest devices fall off; nobody has twenty phones
        self.subscriptions = self.subscriptions[-MAX_SUBSCRIPTIONS:]
        self._save_subscriptions()
        return len(self.subscriptions)

    def unsubscribe(self, endpoint: str) -> bool:
        before = len(self.subscriptions)
        self.subscriptions = [s for s in self.subscriptions if s["endpoint"] != endpoint]
        if len(self.subscriptions) != before:
            self._save_subscriptions()
            return True
        return False

    # ------------------------------------------------------------------ sending
    def notify(
        self,
        title: str,
        body: str,
        *,
        tag: str = "",
        url: str = "/",
        badge: int | None = None,
        kind: str = "",
    ) -> None:
        """Queue a notification to every device. Fire-and-forget; failures are logged."""
        payload = {
            "title": title[:120],
            "body": body[:240],
            "tag": tag,
            "url": url,
            "badge": badge,
            "kind": kind,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        # The phone's own notification shade first: a phone-only runtime has no Web Push
        # service to reach (that needs the internet), and this path works with none.
        self._termux(payload)
        if not self.enabled or not self.subscriptions:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._forget(self._send_all(payload)[0])
            return

        async def deliver() -> None:
            # the network goes to a worker thread; the list and its file are only ever
            # touched on the loop, where subscribe() and unsubscribe() run too
            self._forget((await asyncio.to_thread(self._send_all, payload))[0])

        keep_task(loop.create_task(deliver()))

    def _termux(self, payload: dict[str, Any]) -> bool:
        """Post the notification with Termux:API, when this is a phone that has it.

        ``termux-notification`` is a command, not a network: it needs no push service and no
        account.  A tap opens the app on the local address.  Failures are the phone's (no
        Termux:API app, notifications refused) and stay out of the turn.
        """
        if os.environ.get("NANOMUSE_TERMUX_NOTIFY", "").strip().lower() in (
            "0",
            "off",
            "false",
            "no",
        ):
            return False
        if not termux_available():
            return False
        exe = shutil.which("termux-notification")
        if not exe:  # pragma: no cover - termux_available just checked
            return False
        url = str(payload.get("url") or "/")
        if url.startswith("/"):
            url = f"http://127.0.0.1:{self.port}{url}"
        opener = shutil.which("termux-open-url") or "termux-open-url"
        args = [
            exe,
            "--title",
            str(payload.get("title") or "MiniMuse"),
            "--content",
            str(payload.get("body") or ""),
            "--id",
            "nanomuse-" + (str(payload.get("tag") or "note")),
            "--action",
            f"{opener} {json.dumps(url)}",
        ]
        if str(payload.get("kind") or "") in ("approval", "question", "reminder"):
            args += ["--priority", "high"]
        try:
            subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:  # pragma: no cover - the phone's own failure
            logger.warning("termux notification failed: {}", exc)
            return False
        return True

    def _forget(self, gone: list[str]) -> None:
        """Drop the endpoints a push service reported gone (404/410)."""
        if not gone:
            return
        self.subscriptions = [s for s in self.subscriptions if s["endpoint"] not in gone]
        self._save_subscriptions()

    def _send_all(self, payload: dict[str, Any]) -> tuple[list[str], int, int, str]:
        """Send ``payload`` to a snapshot of the devices: (endpoints gone, how many got
        it, how many failed for another reason, and the first failure's words). Reads the
        list, never writes it: it may run in a worker thread."""
        from pywebpush import WebPushException, webpush

        data = json.dumps(payload)
        gone: list[str] = []
        delivered = 0
        failed = 0
        first_error = ""
        subscribers = list(self.subscriptions)
        for sub in subscribers:
            try:
                webpush(
                    subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                    data=data,
                    vapid_private_key=self.private_key_pem,
                    vapid_claims={"sub": self.contact},
                    ttl=6 * 3600,
                    timeout=10,
                )
                delivered += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    gone.append(sub["endpoint"])
                else:
                    failed += 1
                    if not first_error:
                        first_error = f"{status or 'error'}: {str(exc)[:160]}"
            except Exception as exc:  # noqa: BLE001
                failed += 1
                if not first_error:
                    first_error = str(exc)[:160]
        return gone, delivered, failed, first_error

    async def test(self, name: str = "MiniMuse") -> dict[str, Any]:
        payload = {
            "title": f"{name} can reach you here",
            "body": f"This is what a notification from {name} looks like.",
            "tag": "test",
            "url": "/",
            "kind": "test",
        }
        local = self._termux(payload)
        if not self.enabled and not local:
            return {"ok": False, "error": "push is not available on this server"}
        if not self.subscriptions:
            if local:
                return {"ok": True, "sent": 0, "devices": 0, "termux": True}
            return {"ok": False, "error": "no device has turned notifications on"}
        gone, delivered, failed, first_error = await asyncio.to_thread(self._send_all, payload)
        self._forget(gone)
        out: dict[str, Any] = {
            "ok": delivered > 0 or local,
            "sent": delivered,
            "devices": len(self.subscriptions) + len(gone),
            "termux": local,
        }
        if failed:
            out["failed"] = failed
            out["error"] = first_error
        return out


__all__ = ["PushService", "available", "termux_available"]
