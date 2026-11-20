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
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from nanomuse.logger import logger
from nanomuse.sync.discovery import local_ipv4_addrs
from nanomuse.win_proc import hidden_subprocess_kwargs

_TAILSCALE_CACHE_TTL_S = 5.0
_tailscale_cache: tuple[float, int, dict[str, Any]] | None = None

_TRYCF_RE = re.compile(r"https://[A-Za-z0-9.-]+\.trycloudflare\.com\b")


def _find_bin(name: str, extra_paths: tuple[str, ...] = ()) -> str | None:
    for cand in extra_paths:
        if cand and os.path.isfile(cand):
            return cand
    found = shutil.which(name)
    if found:
        return found
    return None


def _local_cloudflared_path(data_dir: Path | None) -> Path | None:
    if data_dir is None:
        return None
    exe_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    return Path(data_dir) / "bin" / exe_name


def cloudflared_bin(data_dir: Path | None = None) -> str | None:
    """Locate ``cloudflared`` in MiniMuse's local bin folder or on Windows, macOS, or Linux."""
    local_bin = _local_cloudflared_path(data_dir)
    extra: list[str] = []
    if local_bin is not None:
        extra.append(str(local_bin))
    extra.extend(
        [
            r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
            r"C:\Program Files\cloudflared\cloudflared.exe",
            "/opt/homebrew/bin/cloudflared",
            "/usr/local/bin/cloudflared",
            "/usr/bin/cloudflared",
        ]
    )
    return _find_bin("cloudflared", tuple(extra))


def _cloudflared_download_asset() -> tuple[str, bool]:
    """Return ``(download_url, is_tgz)`` for the current OS & CPU architecture."""
    base = "https://github.com/cloudflare/cloudflared/releases/latest/download"
    mach = platform.machine().lower()
    is_arm64 = any(k in mach for k in ("arm64", "aarch64"))
    is_arm32 = mach.startswith("arm") and not is_arm64
    if os.name == "nt":
        if is_arm64:
            return f"{base}/cloudflared-windows-arm64.exe", False
        if "64" in mach or sys.maxsize > 2**32:
            return f"{base}/cloudflared-windows-amd64.exe", False
        return f"{base}/cloudflared-windows-386.exe", False
    if sys.platform == "darwin":
        arch = "arm64" if is_arm64 else "amd64"
        return f"{base}/cloudflared-darwin-{arch}.tgz", True
    # Linux / Android proot
    if is_arm64:
        return f"{base}/cloudflared-linux-arm64", False
    if is_arm32:
        return f"{base}/cloudflared-linux-arm", False
    return f"{base}/cloudflared-linux-amd64", False


async def install_cloudflared(data_dir: Path) -> str:
    """Automatically download the official Cloudflare Quick Tunnel binary into ``<data_dir>/bin``."""
    dest = _local_cloudflared_path(data_dir)
    assert dest is not None
    dest.parent.mkdir(parents=True, exist_ok=True)
    url, is_tgz = _cloudflared_download_asset()
    logger.info("Downloading cloudflared from {} to {}", url, dest)
    tmp_path = dest.with_name(dest.name + ".part")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as hc:
            async with hc.stream("GET", url) as resp:
                resp.raise_for_status()
                with tmp_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(131072):
                        fh.write(chunk)
        if is_tgz:
            with tempfile.TemporaryDirectory() as td:
                with tarfile.open(tmp_path, "r:gz") as tf:
                    tf.extractall(td)
                extracted = Path(td) / "cloudflared"
                if not extracted.is_file():
                    raise RuntimeError("cloudflared binary not found in downloaded archive")
                shutil.move(str(extracted), str(dest))
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        else:
            os.replace(tmp_path, dest)
        if os.name != "nt":
            st = dest.stat()
            dest.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return str(dest)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


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


def tailnet_search_domain(port: int = 8787) -> str:
    """Return the active Tailscale MagicDNS suffix (e.g. ``tailc1c35a.ts.net``) when detectable,
    even inside Android/Linux proot where the ``tailscale`` CLI is absent but ``/etc/resolv.conf``
    carries the tailnet search domain."""
    ts = tailscale_info(port)
    dns = str(ts.get("dns_name") or "").strip().rstrip(".")
    if dns and "." in dns and dns.endswith(".ts.net"):
        return dns.split(".", 1)[1]
    for resolv in (Path("/etc/resolv.conf"), Path("/run/systemd/resolve/resolv.conf")):
        try:
            if resolv.is_file():
                for line in resolv.read_text(encoding="utf-8", errors="ignore").splitlines():
                    s = line.strip()
                    if s.startswith(("search ", "domain ")):
                        for tok in s.split()[1:]:
                            clean = tok.strip().rstrip(".")
                            if clean.endswith(".ts.net") and clean.count(".") >= 2:
                                return clean
        except OSError:
            continue
    return ""


@dataclass
class PersonalCloudManager:
    """Manages free Personal MiniMuse Cloud connectivity (LAN + Tailscale + Cloudflare Quick Tunnel)."""

    port: int = 8787
    data_dir: Path | None = None
    _proc: asyncio.subprocess.Process | None = None
    _reader_task: asyncio.Task[None] | None = None
    _tunnel_url: str = ""
    _tunnel_error: str = ""
    _tunnel_starting: bool = False
    _installing: bool = False
    _ready_event: asyncio.Event = field(default_factory=asyncio.Event)

    def _prefs_path(self) -> Path | None:
        return (Path(self.data_dir) / "personal-cloud.json") if self.data_dir is not None else None

    def get_auto_tunnel(self) -> bool:
        p = self._prefs_path()
        if p is None or not p.is_file():
            return False
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return bool(raw.get("auto_tunnel", False))
        except Exception:  # noqa: BLE001
            pass
        return False

    def set_auto_tunnel(self, enabled: bool) -> bool:
        p = self._prefs_path()
        if p is None:
            return bool(enabled)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if p.is_file():
                with contextlib.suppress(Exception):
                    loaded = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
            data["auto_tunnel"] = bool(enabled)
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
        return bool(enabled)

    async def maybe_auto_start(self) -> None:
        """Start Cloudflare Quick Tunnel automatically at boot if the user opted in and binary exists."""
        if not self.get_auto_tunnel():
            return
        if cloudflared_bin(self.data_dir) is None:
            return
        try:
            await self.start_tunnel(timeout_s=20.0, auto_install=False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("personal cloud auto_tunnel start skipped: {}", exc)

    def lan_urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}" for ip in local_ipv4_addrs()]

    def all_urls(self, ts: dict[str, Any] | None = None) -> list[str]:
        """All reachable URLs ordered from best remote (Tunnel -> Tailscale) to Home Wi-Fi LAN."""
        out: list[str] = []
        if self._tunnel_url:
            out.append(self._tunnel_url.rstrip("/"))
        tail = ts if ts is not None else tailscale_info(self.port)
        if tail.get("url"):
            u = str(tail["url"]).rstrip("/")
            if u and u not in out:
                out.append(u)
        for u in self.lan_urls():
            cu = u.rstrip("/")
            if cu and cu not in out:
                out.append(cu)
        return out

    def best_url(self, ts: dict[str, Any] | None = None) -> str:
        urls = self.all_urls(ts)
        return urls[0] if urls else f"http://127.0.0.1:{self.port}"

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
        installed = cloudflared_bin(self.data_dir) is not None
        return {
            "host": socket.gethostname(),
            "port": self.port,
            "lan_urls": self.lan_urls(),
            "all_urls": self.all_urls(ts),
            "tailscale": ts,
            "cloudflared_installed": installed,
            "auto_tunnel": self.get_auto_tunnel(),
            "tunnel": {
                "running": running,
                "starting": bool(self._tunnel_starting and running),
                "installing": bool(self._installing),
                "needs_install": not installed,
                "url": self._tunnel_url if running else "",
                "error": self._tunnel_error,
            },
            "best_url": self.best_url(ts),
        }

    async def start_tunnel(self, timeout_s: float = 20.0, *, auto_install: bool = False) -> dict[str, Any]:
        if self._proc is not None and self._proc.returncode is None and self._tunnel_url:
            return self.status(probe_tailscale=True)
        binary = cloudflared_bin(self.data_dir)
        if not binary:
            if not auto_install:
                self._tunnel_error = ""
                return self.status(probe_tailscale=True)
            if self.data_dir is None:
                self._tunnel_error = "No data directory configured to install cloudflared."
                return self.status(probe_tailscale=True)
            self._installing = True
            self._tunnel_error = ""
            try:
                binary = await install_cloudflared(self.data_dir)
            except Exception as exc:  # noqa: BLE001
                self._tunnel_error = f"Could not automatically install cloudflared: {exc}"
                return self.status(probe_tailscale=True)
            finally:
                self._installing = False

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


__all__ = [
    "PersonalCloudManager",
    "cloudflared_bin",
    "install_cloudflared",
    "tailnet_search_domain",
    "tailscale_info",
]
