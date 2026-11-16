"""Direct LAN discovery and easy device-to-device pairing without nanoMuse Cloud."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from nanomuse import __version__
from nanomuse.logger import logger
from nanomuse.private import keep_private

DISCOVERY_PORT = 48787
BEACON_INTERVAL_S = 3.5
PEER_TTL_S = 18.0
PAIR_CODE_TTL_S = 600.0
PAIR_FAIL_WINDOW_S = 300.0
PAIR_FAIL_LIMIT = 10
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PEER_TOKENS_FILE = "peer-tokens.json"


def normalize_pair_code(raw: str) -> str:
    """Upper-case alphanumeric code, tolerating spaces or hyphens a person typed."""
    return "".join(ch for ch in str(raw or "").upper() if ch.isalnum())


def normalize_peer_urls(raw: str, default_port: int = 8787) -> list[str]:
    """Turn user input (URL, IP, host:port, or QR payload) into candidate HTTP(S) base URLs."""
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                urls = payload.get("urls") or (
                    [payload["url"]] if payload.get("url") else []
                )
                return [
                    u.strip().rstrip("/")
                    for u in urls
                    if isinstance(u, str) and u.strip().startswith(("http://", "https://"))
                ]
        except ValueError:
            return []
    if text.startswith("nanomuse://"):
        parsed = urlsplit(text)
        qs = parse_qs(parsed.query)
        raw_urls = qs.get("url", []) + qs.get("urls", [])
        out: list[str] = []
        for item in raw_urls:
            for part in item.split(","):
                part = part.strip().rstrip("/")
                if part.startswith(("http://", "https://")):
                    out.append(part)
        return out
    if "://" not in text:
        host_part = text.split("/", 1)[0].strip()
        if ":" in host_part and not host_part.startswith("["):
            candidates = [f"http://{host_part}"]
        else:
            candidates = [f"http://{host_part}:{default_port}"]
        return candidates
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return []
    qs = parse_qs(parsed.query)
    if "url" in qs:
        return [u.strip().rstrip("/") for u in qs["url"] if u.strip()]
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [base.rstrip("/")]


def parse_pair_payload(raw: str) -> dict[str, Any]:
    """Extract {urls, code, name, id} from a pasted QR text, deep link, or code."""
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return {
                    "id": str(data.get("id") or ""),
                    "name": str(data.get("name") or ""),
                    "code": normalize_pair_code(str(data.get("code") or "")),
                    "urls": [
                        str(u).strip().rstrip("/")
                        for u in (data.get("urls") or ([data["url"]] if data.get("url") else []))
                        if isinstance(u, str) and u.strip().startswith(("http://", "https://"))
                    ],
                }
        except ValueError:
            return {}
    if text.startswith("nanomuse://") or ("?" in text and "code=" in text):
        parsed = urlsplit(text)
        qs = parse_qs(parsed.query)
        urls: list[str] = []
        for u in qs.get("url", []) + qs.get("urls", []):
            for part in u.split(","):
                part = part.strip().rstrip("/")
                if part.startswith(("http://", "https://")):
                    urls.append(part)
        if parsed.scheme in ("http", "https") and parsed.netloc and not urls:
            urls.append(f"{parsed.scheme}://{parsed.netloc}")
        return {
            "id": str((qs.get("id") or [""])[0]),
            "name": str((qs.get("name") or [""])[0]),
            "code": normalize_pair_code(str((qs.get("code") or [""])[0])),
            "urls": urls,
        }
    return {"urls": normalize_peer_urls(text)}


def local_ipv4_addrs() -> list[str]:
    """Private/LAN IPv4 addresses reachable from nearby devices, ordered by utility."""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    # Primary default-route interface IP (no packet leaves the machine for UDP connect)
    for target in ("192.0.2.1", "10.255.255.254", "100.64.0.1"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 80))
            ip = s.getsockname()[0]
            if ip and ip not in found:
                found.append(ip)
        except OSError:
            pass
        finally:
            s.close()
    usable: list[str] = []
    for raw_ip in found:
        try:
            addr = ipaddress.IPv4Address(raw_ip)
        except ValueError:
            continue
        if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            continue
        usable.append(str(addr))

    def _priority(ip: str) -> tuple[int, str]:
        # Prefer RFC1918 Wi-Fi / LAN first, then Tailscale CGNAT (100.64.0.0/10)
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        if ip.startswith("172."):
            return (2, ip)
        if ip.startswith("100."):
            return (3, ip)
        return (4, ip)

    usable.sort(key=_priority)
    return usable


def candidate_urls(port: int, bind_host: str = "127.0.0.1") -> list[str]:
    """HTTP URLs other devices can use to reach this runtime."""
    env = os.environ.get("NANOMUSE_ADVERTISE_URLS", "").strip()
    if env:
        out = [u.strip().rstrip("/") for u in env.split(",") if u.strip()]
        if out:
            return out
    p = int(port or 8787)
    if p <= 0:
        return []
    addrs = local_ipv4_addrs()
    if not addrs and bind_host and bind_host not in ("0.0.0.0", "::"):
        addrs = [bind_host]
    return [f"http://{ip}:{p}" for ip in addrs]


def build_pair_uri(code: str, device_id: str, name: str, urls: list[str]) -> str:
    params = {"code": code, "id": device_id, "name": name}
    if urls:
        params["url"] = urls[0]
        if len(urls) > 1:
            params["urls"] = ",".join(urls[:4])
    return "nanomuse://pair?" + urlencode(params)


class PairingManager:
    """Issues short-lived pairing codes and manages scoped inbound per-peer sync tokens."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.tokens_path = self.data_dir / PEER_TOKENS_FILE
        self._active_code: str = ""
        self._code_expires_at: float = 0.0
        self._failures: dict[str, list[float]] = {}
        self._tokens: list[dict[str, str]] = []
        self._load_tokens()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load_tokens(self) -> None:
        try:
            data = json.loads(self.tokens_path.read_text("utf-8"))
        except (OSError, ValueError):
            self._tokens = []
            return
        self._tokens = [
            {
                "hash": str(item.get("hash") or ""),
                "device_id": str(item.get("device_id") or ""),
                "name": str(item.get("name") or ""),
                "created_at": str(item.get("created_at") or ""),
            }
            for item in (data if isinstance(data, list) else [])
            if isinstance(item, dict) and item.get("hash")
        ]

    def _save_tokens(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.tokens_path.with_name(self.tokens_path.name + ".tmp")
        tmp.write_text(json.dumps(self._tokens, ensure_ascii=False, indent=1), "utf-8")
        try:
            keep_private(tmp)
        except OSError:
            pass
        tmp.replace(self.tokens_path)

    @property
    def pair_ready(self) -> bool:
        return bool(self._active_code and time.monotonic() < self._code_expires_at)

    def current_code_info(self) -> dict[str, Any] | None:
        if not self.pair_ready:
            return None
        return {
            "code": self._active_code,
            "expires_in": max(0, int(self._code_expires_at - time.monotonic())),
        }

    def issue_code(self, ttl_s: float = PAIR_CODE_TTL_S) -> dict[str, Any]:
        self._active_code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
        self._code_expires_at = time.monotonic() + max(30.0, float(ttl_s))
        return {
            "code": self._active_code,
            "expires_in": int(self._code_expires_at - time.monotonic()),
        }

    def clear_code(self) -> None:
        self._active_code = ""
        self._code_expires_at = 0.0

    def _rate_limited(self, client_ip: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self._failures.get(client_ip, []) if now - t < PAIR_FAIL_WINDOW_S]
        self._failures[client_ip] = recent
        return len(recent) >= PAIR_FAIL_LIMIT

    def _record_failure(self, client_ip: str) -> None:
        now = time.monotonic()
        recent = [t for t in self._failures.get(client_ip, []) if now - t < PAIR_FAIL_WINDOW_S]
        recent.append(now)
        self._failures[client_ip] = recent

    def claim_code(self, given_code: str, client_ip: str = "unknown") -> tuple[bool, str]:
        """Validate a 6-character pairing code (one-time consume on success)."""
        ip = (client_ip or "unknown").strip()
        if self._rate_limited(ip):
            return False, "too many attempts; wait a few minutes"
        if not self.pair_ready:
            self._record_failure(ip)
            return False, "no active pairing code on this device; open Pair on this device first"
        norm = normalize_pair_code(given_code)
        if not norm or not secrets.compare_digest(norm.encode("utf-8"), self._active_code.encode("utf-8")):
            self._record_failure(ip)
            return False, "invalid pairing code"
        self.clear_code()
        self._failures.pop(ip, None)
        return True, ""

    def mint_inbound_token(self, device_id: str = "", name: str = "") -> str:
        """Issue a scoped sync token accepted only on /v1/sync/*."""
        token = "nmpeer_" + secrets.token_urlsafe(32)
        h = self._hash_token(token)
        did = str(device_id or "").strip()[:80]
        clean_name = str(name or "").strip()[:60]
        # Replace older inbound token for the same device_id if present
        if did:
            self._tokens = [t for t in self._tokens if t.get("device_id") != did]
        self._tokens.append(
            {
                "hash": h,
                "device_id": did,
                "name": clean_name,
                "created_at": str(int(time.time())),
            }
        )
        self._save_tokens()
        return token

    def verify_inbound_token(self, token: str) -> bool:
        if not token or not token.startswith("nmpeer_"):
            return False
        candidate = self._hash_token(token).encode("utf-8")
        for row in self._tokens:
            stored = str(row.get("hash") or "").encode("utf-8")
            if stored and secrets.compare_digest(candidate, stored):
                return True
        return False

    def revoke_peer_tokens(self, device_id: str = "", name: str = "") -> None:
        before = len(self._tokens)
        self._tokens = [
            t
            for t in self._tokens
            if not (
                (device_id and t.get("device_id") == device_id)
                or (name and t.get("name") == name)
            )
        ]
        if len(self._tokens) != before:
            self._save_tokens()


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner: LanDiscovery) -> None:
        self.owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
        except ValueError:
            return
        if isinstance(payload, dict):
            self.owner.ingest_beacon(payload, addr[0])


class LanDiscovery:
    """Zero-config local network peer discovery over UDP (no secrets broadcast)."""

    def __init__(
        self,
        device_id: str,
        name_fn: Any,
        port_fn: Any,
        pair_ready_fn: Any,
        kind: str = "computer",
    ) -> None:
        self.device_id = device_id
        self._name_fn = name_fn
        self._port_fn = port_fn
        self._pair_ready_fn = pair_ready_fn
        self.kind = kind
        self._seen: dict[str, dict[str, Any]] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def start(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("NANOMUSE_TEST_LAN_DISCOVERY") != "1":
            return
        if os.environ.get("MINIMUSE_NO_DISCOVERY") == "1" or os.environ.get("MINIMUSE_NO_INSTALL") == "1":
            return
        # On Windows, binding UDP INADDR_ANY from a non-canonical versioned binary (e.g. an
        # un-installed download or test binary) pops up a new Windows Defender Firewall prompt
        # per release version; only allow discovery from the single canonical MiniMuse.exe
        # unless explicitly overridden.
        if (
            sys.platform == "win32"
            and bool(getattr(sys, "frozen", False))
            and os.environ.get("NANOMUSE_FORCE_LAN_DISCOVERY") != "1"
        ):
            local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            canon = Path(local_app) / "Programs" / "MiniMuse" / "MiniMuse.exe"
            try:
                if os.path.normcase(os.path.abspath(sys.executable)) != os.path.normcase(os.path.abspath(str(canon))):
                    return
            except OSError:
                return
        port = int(self._port_fn() or 0)
        if port <= 0:
            return
        self._stopped = False
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._run(), name="lan-discovery")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _beacon_packet(self) -> bytes | None:
        port = int(self._port_fn() or 0)
        if port <= 0:
            return None
        urls = candidate_urls(port)
        if not urls:
            return None
        payload = {
            "v": 1,
            "type": "nanomuse_peer",
            "id": self.device_id,
            "name": str(self._name_fn() or socket.gethostname()),
            "kind": self.kind,
            "port": port,
            "urls": urls[:4],
            "pair_ready": bool(self._pair_ready_fn()),
            "version": __version__,
        }
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            try:
                sock.bind(("", DISCOVERY_PORT))
                self._transport, _ = await loop.create_datagram_endpoint(
                    lambda: _DiscoveryProtocol(self), sock=sock
                )
            except OSError:
                # Another local instance owns listener port, fall back to send-only
                sock.close()
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setblocking(False)
                self._transport, _ = await loop.create_datagram_endpoint(
                    lambda: _DiscoveryProtocol(self), sock=sock
                )
            while not self._stopped:
                pkt = self._beacon_packet()
                if pkt and self._transport is not None:
                    targets = {"255.255.255.255"}
                    for ip in local_ipv4_addrs():
                        parts = ip.split(".")
                        if len(parts) == 4:
                            targets.add(f"{parts[0]}.{parts[1]}.{parts[2]}.255")
                    for target in targets:
                        try:
                            self._transport.sendto(pkt, (target, DISCOVERY_PORT))
                        except OSError:
                            pass
                await asyncio.sleep(BEACON_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("lan discovery stopped: {}", exc)
        finally:
            if self._transport is not None:
                self._transport.close()
                self._transport = None
            else:
                try:
                    sock.close()
                except OSError:
                    pass

    def ingest_beacon(self, payload: dict[str, Any], sender_ip: str = "") -> None:
        if payload.get("type") != "nanomuse_peer":
            return
        did = str(payload.get("id") or "").strip()[:80]
        if not did or did == self.device_id:
            return
        port = int(payload.get("port") or 8787)
        raw_urls = [
            str(u).strip().rstrip("/")
            for u in (payload.get("urls") or [])
            if isinstance(u, str) and u.strip().startswith(("http://", "https://"))
        ]
        if sender_ip and not sender_ip.startswith("127."):
            sender_url = f"http://{sender_ip}:{port}"
            if sender_url not in raw_urls:
                raw_urls.insert(0, sender_url)
        if not raw_urls:
            return
        self._seen[did] = {
            "id": did,
            "name": str(payload.get("name") or sender_ip or did)[:60],
            "kind": str(payload.get("kind") or "computer")[:20],
            "url": raw_urls[0],
            "urls": raw_urls[:4],
            "pair_ready": bool(payload.get("pair_ready")),
            "version": str(payload.get("version") or ""),
            "last_seen": time.monotonic(),
        }

    def list_nearby(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        fresh: list[dict[str, Any]] = []
        for did, row in list(self._seen.items()):
            age = now - float(row.get("last_seen") or 0)
            if age > PEER_TTL_S:
                self._seen.pop(did, None)
                continue
            item = dict(row)
            item["age_s"] = int(age)
            item.pop("last_seen", None)
            fresh.append(item)
        fresh.sort(key=lambda r: (not r.get("pair_ready"), r.get("name", "").lower()))
        return fresh
