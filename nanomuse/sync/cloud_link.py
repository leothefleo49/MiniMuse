"""Zero-config end-to-end encrypted Cloud Link for paired MiniMuse devices.

Every pair of devices automatically shares a 256-bit random ``sync_secret`` stored only in
each device's local ``CredentialVault``. From that secret both devices derive:

1. An unguessable 112-bit **rendezvous beacon topic** (``nm2b-...``) where each device
   publishes its AES-256-GCM encrypted live reachable URLs (e.g. fresh Cloudflare Quick
   Tunnel ``https://*.trycloudflare.com``, Tailscale, and LAN addresses) whenever its
   address changes or periodically while online. That lets a phone away from home
   automatically discover a PC's new tunnel URL after a reboot or network switch — with
   zero manual URL pasting.
2. An unguessable 112-bit **encrypted cloud mailbox topic** (``nm2m-...``) where either
   device drops compressed, AES-256-GCM encrypted sync deltas (conversations, messages,
   deletions, memories, goals, reminders, profile) whenever the other device is asleep or
   offline. Whichever device comes online later pulls, decrypts, and merges those changes
   even if the sender is now offline.

Every byte leaves the device authenticated & encrypted with AES-256-GCM (random 96-bit
nonce + topic AAD). The public relay never sees device names, URLs, or chat contents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import zlib
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nanomuse.logger import logger

DEFAULT_RELAY_URL = "https://ntfy.sh"
SECRET_PREFIX = "nmcloud_"
ENVELOPE_PREFIX = "v1."
CHUNK_BODY_LIMIT = 2800
MAX_CHUNKS = 16


def relay_base_url() -> str:
    raw = os.environ.get("NANOMUSE_CLOUD_LINK_RELAY", DEFAULT_RELAY_URL).strip()
    return (raw or DEFAULT_RELAY_URL).rstrip("/")


def cloud_link_disabled() -> bool:
    flag = os.environ.get("NANOMUSE_DISABLE_CLOUD_LINK", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    # Avoid real external network traffic inside unit tests unless explicitly pointed at a relay
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("NANOMUSE_CLOUD_LINK_RELAY"):
        return True
    return False


def new_sync_secret() -> str:
    """Mint a fresh 256-bit random shared secret for a paired device group."""
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def is_valid_sync_secret(secret: str | None) -> bool:
    s = str(secret or "").strip()
    return s.startswith(SECRET_PREFIX) and len(s) >= 28 and "{{vault:" not in s


def _enc_key(secret: str) -> bytes:
    return hashlib.sha256(f"nanomuse-cloudlink-enc-v1:{secret.strip()}".encode("utf-8")).digest()


def derive_beacon_topic(secret: str) -> str:
    digest = hashlib.sha256(f"nanomuse-cloudlink-beacon-v1:{secret.strip()}".encode("utf-8")).hexdigest()
    return f"nm2b-{digest[:28]}"


def derive_mailbox_topic(secret: str) -> str:
    digest = hashlib.sha256(f"nanomuse-cloudlink-mailbox-v1:{secret.strip()}".encode("utf-8")).hexdigest()
    return f"nm2m-{digest[:28]}"


def encrypt_envelope(secret: str, topic: str, payload: dict[str, Any]) -> str:
    """Compress and AES-256-GCM encrypt ``payload`` bound to ``topic`` via AAD."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    nonce = os.urandom(12)
    ct = AESGCM(_enc_key(secret)).encrypt(nonce, compressed, topic.encode("utf-8"))
    token = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
    return ENVELOPE_PREFIX + token


def decrypt_envelope(secret: str, topic: str, envelope: str) -> dict[str, Any] | None:
    """Decrypt and authenticate an envelope; returns ``None`` on any tamper or format mismatch."""
    s = str(envelope or "").strip()
    if not s.startswith(ENVELOPE_PREFIX):
        return None
    try:
        blob = base64.urlsafe_b64decode(s[len(ENVELOPE_PREFIX) :].encode("ascii"))
        if len(blob) <= 28:
            return None
        nonce, ct = blob[:12], blob[12:]
        compressed = AESGCM(_enc_key(secret)).decrypt(nonce, ct, topic.encode("utf-8"))
        data = json.loads(zlib.decompress(compressed).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


async def _post_message(
    topic: str,
    body: str,
    *,
    title: str = "nm",
    relay: str | None = None,
    timeout: float = 8.0,
) -> bool:
    if cloud_link_disabled():
        return False
    base = (relay or relay_base_url()).rstrip("/")
    url = f"{base}/{topic}"
    headers = {
        "Title": title,
        "Cache": "yes",
        "Content-Type": "text/plain; charset=utf-8",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as hc:
            resp = await hc.post(url, content=body.encode("utf-8"), headers=headers)
            return 200 <= resp.status_code < 300
    except Exception as exc:  # noqa: BLE001
        logger.debug("cloud_link post failed ({}): {}", topic[:10], exc)
        return False


async def _poll_messages(
    topic: str,
    *,
    since: str = "12h",
    relay: str | None = None,
    timeout: float = 8.0,
) -> list[tuple[str, int, str]]:
    """Return ``[(ntfy_id, time_unix, message_text), ...]`` cached on the relay."""
    if cloud_link_disabled():
        return []
    base = (relay or relay_base_url()).rstrip("/")
    url = f"{base}/{topic}/json?poll=1&since={since}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as hc:
            resp = await hc.get(url)
            if resp.status_code >= 400:
                return []
            out: list[tuple[str, int, str]] = []
            for line in resp.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict) or ev.get("event") != "message":
                    continue
                msg = str(ev.get("message") or "").strip()
                if not msg:
                    continue
                out.append((str(ev.get("id") or ""), int(ev.get("time") or 0), msg))
            return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("cloud_link poll failed ({}): {}", topic[:10], exc)
        return []


async def publish_beacon(
    secret: str,
    *,
    device_id: str,
    name: str,
    kind: str,
    urls: list[str],
    relay: str | None = None,
) -> bool:
    """Publish this device's encrypted live addresses to the pair's rendezvous beacon."""
    if not is_valid_sync_secret(secret) or not device_id or not urls:
        return False
    clean_urls = [
        str(u).strip().rstrip("/")
        for u in urls
        if isinstance(u, str) and str(u).strip().startswith(("http://", "https://"))
    ][:8]
    if not clean_urls:
        return False
    topic = derive_beacon_topic(secret)
    payload = {
        "v": 1,
        "type": "beacon",
        "device_id": str(device_id).strip(),
        "name": str(name or "").strip()[:60],
        "kind": str(kind or "").strip()[:20],
        "urls": clean_urls,
        "ts": round(time.time(), 2),
    }
    envelope = encrypt_envelope(secret, topic, payload)
    return await _post_message(topic, envelope, title="b", relay=relay)


async def fetch_beacons(
    secret: str,
    *,
    exclude_device_id: str = "",
    since: str = "12h",
    relay: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Fetch and decrypt the latest beacon per remote device_id."""
    if not is_valid_sync_secret(secret):
        return {}
    topic = derive_beacon_topic(secret)
    rows = await _poll_messages(topic, since=since, relay=relay)
    latest: dict[str, dict[str, Any]] = {}
    for _, srv_time, msg in rows:
        data = decrypt_envelope(secret, topic, msg)
        if not data or data.get("type") != "beacon":
            continue
        did = str(data.get("device_id") or "").strip()
        if not did or (exclude_device_id and did == exclude_device_id):
            continue
        urls = [
            str(u).strip().rstrip("/")
            for u in (data.get("urls") or [])
            if isinstance(u, str) and str(u).strip().startswith(("http://", "https://"))
        ]
        if not urls:
            continue
        ts = float(data.get("ts") or srv_time or 0.0)
        prev = latest.get(did)
        if prev is None or ts >= float(prev.get("ts") or 0.0):
            latest[did] = {
                "device_id": did,
                "name": str(data.get("name") or "").strip(),
                "kind": str(data.get("kind") or "").strip(),
                "urls": urls,
                "ts": ts,
            }
    return latest


async def publish_mailbox(
    secret: str,
    packet: dict[str, Any],
    *,
    relay: str | None = None,
) -> bool:
    """Encrypt and drop an asynchronous sync delta packet into the shared Cloud Link mailbox."""
    if not is_valid_sync_secret(secret) or not isinstance(packet, dict):
        return False
    topic = derive_mailbox_topic(secret)
    payload = dict(packet)
    payload.setdefault("v", 1)
    payload.setdefault("packet_id", secrets.token_hex(8))
    payload.setdefault("ts", round(time.time(), 2))
    envelope = encrypt_envelope(secret, topic, payload)
    if len(envelope) <= CHUNK_BODY_LIMIT:
        return await _post_message(topic, envelope, title="m", relay=relay)
    # Split large encrypted envelope into authenticated chunk frames
    gid = str(payload["packet_id"])
    chunks = [
        envelope[i : i + CHUNK_BODY_LIMIT - 200]
        for i in range(0, len(envelope), CHUNK_BODY_LIMIT - 200)
    ]
    if len(chunks) > MAX_CHUNKS:
        logger.debug("cloud_link mailbox packet too large ({} chunks), skipping", len(chunks))
        return False
    ok_all = True
    for idx, piece in enumerate(chunks):
        frame = encrypt_envelope(
            secret,
            topic,
            {
                "v": 1,
                "type": "chunk",
                "g": gid,
                "i": idx,
                "n": len(chunks),
                "d": piece,
                "device_id": str(payload.get("device_id") or ""),
            },
        )
        ok = await _post_message(topic, frame, title="c", relay=relay)
        if not ok:
            ok_all = False
            break
    return ok_all


async def fetch_mailbox(
    secret: str,
    *,
    exclude_device_id: str = "",
    seen_ids: set[str] | None = None,
    since: str = "12h",
    relay: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch, decrypt, reassemble, and deduplicate pending mailbox packets from the peer."""
    if not is_valid_sync_secret(secret):
        return [], []
    topic = derive_mailbox_topic(secret)
    rows = await _poll_messages(topic, since=since, relay=relay)
    seen = set(seen_ids or set())
    packets: list[dict[str, Any]] = []
    new_ids: list[str] = []
    chunk_groups: dict[str, tuple[int, dict[int, str]]] = {}

    def _accept(data: dict[str, Any]) -> None:
        did = str(data.get("device_id") or "").strip()
        if exclude_device_id and did == exclude_device_id:
            return
        pid = str(data.get("packet_id") or "").strip()
        if not pid or pid in seen:
            return
        seen.add(pid)
        new_ids.append(pid)
        packets.append(data)

    for _, _, msg in rows:
        data = decrypt_envelope(secret, topic, msg)
        if not data:
            continue
        if data.get("type") == "chunk":
            gid = str(data.get("g") or "")
            idx = int(data.get("i") or 0)
            total = int(data.get("n") or 0)
            piece = str(data.get("d") or "")
            if not gid or total <= 0 or total > MAX_CHUNKS or idx < 0 or idx >= total:
                continue
            grp_total, parts = chunk_groups.setdefault(gid, (total, {}))
            if grp_total == total:
                parts[idx] = piece
            continue
        _accept(data)

    for gid, (total, parts) in chunk_groups.items():
        if len(parts) != total or gid in seen:
            continue
        joined = "".join(parts[i] for i in range(total))
        inner = decrypt_envelope(secret, topic, joined)
        if inner:
            _accept(inner)

    packets.sort(key=lambda p: float(p.get("ts") or 0.0))
    return packets, new_ids


__all__ = [
    "DEFAULT_RELAY_URL",
    "SECRET_PREFIX",
    "cloud_link_disabled",
    "decrypt_envelope",
    "derive_beacon_topic",
    "derive_mailbox_topic",
    "encrypt_envelope",
    "fetch_beacons",
    "fetch_mailbox",
    "is_valid_sync_secret",
    "new_sync_secret",
    "publish_beacon",
    "publish_mailbox",
    "relay_base_url",
]
