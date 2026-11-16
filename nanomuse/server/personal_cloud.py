"""Free Personal MiniMuse Cloud: your own self-owned remote access without fees.

Distinct from upstream nanoMuse Cloud (hosted subscription relay), Personal MiniMuse Cloud
exposes your own running MiniMuse instance across your devices using:

1. Direct P2P Sync + LAN discovery (UDP 48787) on the same Wi-Fi.
2. Tailscale mesh (``tailscale status --json`` / ``100.x`` IP or MagicDNS HTTPS) when installed.
3. Free Cloudflare Quick Tunnel (``cloudflared tunnel --no-autoupdate --url ...``) which mints
   a free ``https://*.trycloudflare.com`` endpoint with zero Cloudflare account or credit card.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from typing import Any

from nanomuse.logger import logger
from nanomuse.sync.discovery import local_ipv4_addrs
from nanomuse.win_proc import hidden_subprocess_kwargs

_TAILSCALE_CACHE_TTL_S = 5.0
_tailscale_cache: tuple[float, int, dict[str, Any]] | None = None

_TRYCF_RE = re.compile(r"https://[A-Za-z0-9.-]+\.trycloudflare\.com\b")


def _find_bin(name: str, extra_paths: tuple[str, ...] = ()) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for cand in extra_paths:
        if cand and os.path.isfile(cand):
            return cand
    return None


def cloudflared_bin() -> str | None:
    """Locate ``cloudflared`` if installed on Windows, macOS, or Linux."""
    return _find_bin(
        "cloudflared",
        (
            r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
            r"C:\Program Files\cloudflared\cloudflared.exe",
            "/opt/homebrew/bin/cloudflared",
            "/usr/local/bin/cloudflared",
            "/usr/bin/cloudflared",
        ),
    )


def tailscale_info(port: int) -> dict[str, Any]:
    """Detect active Tailscale IPv4 / MagicDNS host on this machine (non-blocking best-effort)."""
    import time

    global _tailscale_cache
    now = time.monotonic()
    if (
        _tailscale_cache is not None
        and _tailscale_cache[1] == port
        and (now - _tailscale_cache[0]) < _TAILSCALE_CACHE_TTL_S
    ):
        return dict(_tailscale_cache[2])

    ts = _find_bin(
        "tailscale",
        (
            r"C:\Program Files\Tailscale\tailscale.exe",
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/usr/bin/tailscale",
        ),
    )
    info: dict[str, Any] = {
        "installed": ts is not None,
        "running": False,
        "ip": "",
        "dns_name": "",
        "url": "",
    }
    if ts is not None:
        try:
            proc = subprocess.run(
                [ts, "status", "--json"],
                capture_output=True,
                text=True,
                timeout=1.8,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                data = json.loads(proc.stdout)
                self_node = data.get("Self") or {}
                online = bool(self_node.get("Online"))
                ips = self_node.get("TailscaleIPs") or []
                ipv4 = next((ip for ip in ips if ":" not in str(ip)), "")
                dns = str(self_node.get("DNSName") or "").rstrip(".")
                info["running"] = online or bool(ipv4)
                info["ip"] = str(ipv4)
                info["dns_name"] = dns
                if dns:
                    info["url"] = f"http://{dns}:{port}" if port != 80 else f"http://{dns}"
                elif ipv4:
                    info["url"] = f"http://{ipv4}:{port}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("tailscale probe failed: {}", exc)
    if not info["ip"]:
        for ip in local_ipv4_addrs():
            if ip.startswith("100."):
                info["running"] = True
                info["ip"] = ip
                if not info["url"]:
                    info["url"] = f"http://{ip}:{port}"
                break
    _tailscale_cache = (time.monotonic(), port, dict(info))
    return info


@dataclass
class PersonalCloudManager:
    """Manages free Personal MiniMuse Cloud connectivity (LAN + Tailscale + Cloudflare Quick Tunnel)."""

    port: int = 8787
    _proc: asyncio.subprocess.Process | None = None
    _reader_task: asyncio.Task[None] | None = None
    _tunnel_url: str = ""
    _tunnel_error: str = ""
    _tunnel_starting: bool = False
    _ready_event: asyncio.Event = field(default_factory=asyncio.Event)

    def lan_urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}" for ip in local_ipv4_addrs()]

    def best_url(self, ts: dict[str, Any] | None = None) -> str:
        if self._tunnel_url:
            return self._tunnel_url
        tail = ts if ts is not None else tailscale_info(self.port)
        if tail.get("url"):
            return str(tail["url"])
        lans = self.lan_urls()
        return lans[0] if lans else f"http://127.0.0.1:{self.port}"

    def status(self, *, probe_tailscale: bool = True) -> dict[str, Any]:
        ts = (
            tailscale_info(self.port)
            if probe_tailscale
            else {
                "installed": False,
                "running": False,
                "ip": "",
                "dns_name": "",
                "url": "",
            }
        )
        running = self._proc is not None and self._proc.returncode is None
        return {
            "host": socket.gethostname(),
            "port": self.port,
            "lan_urls": self.lan_urls(),
            "tailscale": ts,
            "cloudflared_installed": cloudflared_bin() is not None,
            "tunnel": {
                "running": running,
                "starting": bool(self._tunnel_starting and running),
                "url": self._tunnel_url if running else "",
                "error": self._tunnel_error,
            },
            "best_url": self.best_url(ts),
        }

    async def start_tunnel(self, timeout_s: float = 18.0) -> dict[str, Any]:
        if self._proc is not None and self._proc.returncode is None and self._tunnel_url:
            return self.status(probe_tailscale=True)
        binary = cloudflared_bin()
        if not binary:
            self._tunnel_error = (
                "cloudflared is not installed. Install free cloudflared from Cloudflare Downloads "
                "(winget install Cloudflare.cloudflared on Windows, brew install cloudflared on macOS) "
                "or use Tailscale / Direct P2P Sync below."
            )
            return self.status(probe_tailscale=True)

        await self.stop_tunnel()
        self._tunnel_error = ""
        self._tunnel_url = ""
        self._tunnel_starting = True
        self._ready_event = asyncio.Event()
        target = f"http://127.0.0.1:{self.port}"
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "tunnel",
                "--no-autoupdate",
                "--url",
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **hidden_subprocess_kwargs(),
            )
        except OSError as exc:
            self._tunnel_starting = False
            self._tunnel_error = f"could not launch cloudflared: {exc}"
            return self.status(probe_tailscale=True)

        self._proc = proc
        self._reader_task = asyncio.create_task(self._read_tunnel_output(proc))
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout_s)
        except TimeoutError:
            if not self._tunnel_url:
                self._tunnel_error = (
                    "cloudflared started but did not return a trycloudflare.com URL in time"
                )
        finally:
            self._tunnel_starting = False
        return self.status(probe_tailscale=True)

    async def _read_tunnel_output(self, proc: asyncio.subprocess.Process) -> None:
        stream = proc.stdout
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                m = _TRYCF_RE.search(text)
                if m and not self._tunnel_url:
                    self._tunnel_url = m.group(0)
                    self._ready_event.set()
                elif "failed" in text.lower() or "error" in text.lower():
                    if not self._tunnel_url:
                        self._tunnel_error = text[:200]
        except Exception as exc:  # noqa: BLE001
            logger.debug("cloudflared output reader: {}", exc)
        finally:
            self._ready_event.set()

    async def stop_tunnel(self) -> dict[str, Any]:
        task = self._reader_task
        self._reader_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=4.0)
            except TimeoutError:
                proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
        self._tunnel_url = ""
        self._tunnel_starting = False
        return self.status(probe_tailscale=True)


__all__ = ["PersonalCloudManager", "cloudflared_bin", "tailscale_info"]
