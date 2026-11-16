"""Bridge between the MiniMuse Chrome extension and the agent browser tool.

The extension connects over ``/ws/chrome``, tracks at most one user-shared Chrome tab via
``chrome.debugger`` CDP calls in the background (without focusing Chrome or letting content-script
extensions break page automation), and serves the extension bundle at ``/api/chrome/extension.zip``.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanomuse.logger import logger

DEFAULT_RPC_TIMEOUT_S = 20.0


def find_extension_dir() -> Path | None:
    """Locate the unpacked ``chrome-extension`` directory beside the package or repo root."""
    here = Path(__file__).resolve()
    candidates = (
        here.parents[2] / "chrome-extension",
        here.parent / "chrome-extension",
        Path.cwd() / "chrome-extension",
    )
    for cand in candidates:
        if (cand / "manifest.json").is_file():
            return cand
    return None


def build_extension_zip(ext_dir: Path | None = None) -> bytes | None:
    """Build an in-memory ZIP archive of the unpacked MV3 MiniMuse Chrome extension."""
    root = ext_dir or find_extension_dir()
    if root is None or not (root / "manifest.json").is_file():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(root.rglob("*")):
            if item.is_file() and "__pycache__" not in item.parts:
                arcname = f"MiniMuse-Chrome-Extension/{item.relative_to(root).as_posix()}"
                zf.write(item, arcname)
    return buf.getvalue()


@dataclass
class ChromeSession:
    """State of a connected MiniMuse Chrome extension instance."""

    session_id: str
    send: Callable[[dict[str, Any]], Awaitable[None]]
    version: str = ""
    browser: str = "Chrome"
    tab_id: int | None = None
    url: str = ""
    title: str = ""
    width: int = 1280
    height: int = 800
    attached: bool = False
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": True,
            "attached": bool(self.attached and self.tab_id is not None),
            "version": self.version,
            "browser": self.browser,
            "tab_id": self.tab_id,
            "url": self.url,
            "title": self.title,
            "width": self.width,
            "height": self.height,
            "connected_at": self.connected_at,
            "last_seen": self.last_seen,
        }


class ChromeBridge:
    """Tracks the connected Chrome extension WebSocket and dispatches CDP RPC calls."""

    def __init__(self, on_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._session: ChromeSession | None = None
        self.on_change = on_change

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def ready(self) -> bool:
        s = self._session
        return bool(s is not None and s.attached and s.tab_id is not None)

    def status(self) -> dict[str, Any]:
        ext_dir = find_extension_dir()
        s = self._session
        base = {
            "extension_packaged": ext_dir is not None,
            "connected": s is not None,
            "attached": bool(s is not None and s.attached and s.tab_id is not None),
            "version": s.version if s else "",
            "browser": s.browser if s else "Chrome",
            "tab_id": s.tab_id if s else None,
            "url": s.url if s else "",
            "title": s.title if s else "",
            "width": s.width if s else 1280,
            "height": s.height if s else 800,
        }
        return base

    def _notify(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(self.status())
        except Exception as exc:  # noqa: BLE001
            logger.debug("chrome bridge notify: {}", exc)

    def attach_socket(
        self,
        send: Callable[[dict[str, Any]], Awaitable[None]],
        version: str = "",
        browser: str = "Chrome",
    ) -> ChromeSession:
        old = self._session
        if old is not None:
            self._fail_pending(old, ConnectionError("replaced by a newer extension connection"))
        session = ChromeSession(
            session_id=uuid.uuid4().hex[:12],
            send=send,
            version=version,
            browser=browser or "Chrome",
        )
        self._session = session
        self._notify()
        return session

    def detach_socket(self, session: ChromeSession | None) -> None:
        if session is None or self._session is not session:
            return
        self._fail_pending(session, ConnectionError("Chrome extension disconnected"))
        self._session = None
        self._notify()

    @staticmethod
    def _fail_pending(session: ChromeSession, exc: BaseException) -> None:
        for fut in list(session.pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        session.pending.clear()

    def handle_message(self, session: ChromeSession, msg: dict[str, Any]) -> None:
        if self._session is not session:
            return
        session.last_seen = time.time()
        kind = str(msg.get("kind") or "")
        if kind == "chrome_response":
            rid = str(msg.get("id") or "")
            fut = session.pending.pop(rid, None)
            if fut is not None and not fut.done():
                if msg.get("ok", True):
                    fut.set_result(dict(msg.get("result") or {}))
                else:
                    fut.set_exception(RuntimeError(str(msg.get("error") or "Chrome action failed")))
            return
        if kind == "chrome_state":
            tab = msg.get("tab_id")
            session.tab_id = int(tab) if isinstance(tab, int | float) and tab is not None else None
            session.attached = bool(msg.get("attached") and session.tab_id is not None)
            session.url = str(msg.get("url") or "")[:2000]
            session.title = str(msg.get("title") or "")[:300]
            if isinstance(msg.get("width"), int | float) and int(msg["width"]) > 0:
                session.width = int(msg["width"])
            if isinstance(msg.get("height"), int | float) and int(msg["height"]) > 0:
                session.height = int(msg["height"])
            if msg.get("version"):
                session.version = str(msg["version"])[:40]
            self._notify()

    async def request(
        self, action: str, *, timeout: float = DEFAULT_RPC_TIMEOUT_S, **params: Any
    ) -> dict[str, Any]:
        s = self._session
        if s is None:
            raise RuntimeError("MiniMuse Chrome extension is not connected")
        if not s.attached or s.tab_id is None:
            if action not in ("attach_active", "tabs", "ping", "detach"):
                raise RuntimeError(
                    "No Chrome tab is shared yet. Click Share active tab in the MiniMuse Chrome panel"
                )
        rid = f"chr-{uuid.uuid4().hex[:10]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        s.pending[rid] = fut
        try:
            await s.send({"kind": "chrome_request", "id": rid, "action": action, **params})
            res = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            s.pending.pop(rid, None)
        if "url" in res:
            s.url = str(res["url"] or "")[:2000]
        if "title" in res:
            s.title = str(res["title"] or "")[:300]
        return res

    async def screenshot_bytes(self, quality: int = 55) -> bytes:
        res = await self.request("screenshot", quality=max(20, min(95, int(quality))))
        b64 = str(res.get("jpeg") or "")
        return base64.b64decode(b64) if b64 else b""


__all__ = [
    "ChromeBridge",
    "ChromeSession",
    "build_extension_zip",
    "find_extension_dir",
]
