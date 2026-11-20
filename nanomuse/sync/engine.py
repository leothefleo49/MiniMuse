"""Conversation sync (contracts C7 and C8): the runtime's chats are the same on every device
of the account.

The relay (``/v1/sync/*``, relay 0.19) keeps the text of the account's conversations —
user and final assistant messages, attachment names and sizes — and a change counter per
account. This engine:

* **pushes** the person's message the moment it is sent, the assistant's final text when
  the turn ends (debounced two seconds), a chat's title when it is created or renamed, a
  tombstone when it is deleted — and the **whole eligible history** of this runtime,
  oldest first in pages of 200, on sign-in and when sync is turned on again (every event
  is unmarked, so the first conversation goes up along with the rest);
* **pulls** on start, on sign-in, on the hub's ``sync`` frame and every 60 seconds, and
  applies what came in ``seq`` order: new conversations become threads (title, the device
  that started them), new messages become timeline events sorted into time order and
  history for the agent, tombstones remove messages and threads.

Every synced thread has a ``cid`` (kept in ``data_dir/sync.json`` next to the cursor);
every synced event carries a ``mid`` and ``synced: true`` in the timeline. The main chat
is ``kind: main`` and **one conversation for the whole account** (C8): the first device to
push it names its cid, every other device adopts that cid on its first pull (``main_exists``
says which when two race), and the local main chat shows the union of its own turns and the
other devices' — merged by ``created_at``, local first on a tie, deduplicated by ``mid``.
A pulled message this device wrote (the echo of its own push) is never added twice. Chats
addressed to another device (``Thread.device``) and chats another device opened here
(``Thread.remote_from``) are not synced — the other device has the same conversation as
its own.

**Main first** (C9, relay 0.20). By default only the main conversation syncs: pushes send
``kind: main`` alone and pulls ask for ``scope=main``. *Settings → Data controls → Also sync
side chats* (``side_chats`` here, default from ``[sync] side_chats``) turns the side chats
on for this device: they are pushed too, pulls ask for ``scope=all``, and the moment it is
turned on one pull from ``since=0&scope=all&tail=300`` brings the other devices' side chats
(idempotent by ``mid`` / ``cid``). Off again stops pushing and pulling them; what was synced
stays where it is. A fresh device's first pull asks for the **tail** — the newest 300
messages and their conversations — so the chat is on screen in seconds; older history stays
on the devices that wrote it. **Presence**: a turn that starts on a synced conversation
posts ``working: true`` to the relay right after the person's message went up, and
``working: false`` after the assistant's text; the hub's ``working`` frames from the other
devices land in a small map (:meth:`working_view`) the web app reads, and leave it when the
reply arrives, when the device says done, or ten minutes after ``at``. Presence is best
effort: never retried, never awaited on the turn's path, errors at debug.

**Whose conversations** (C10). Every local conversation that was ever pushed to, or pulled
from, an account carries that account's ``id`` (``GET /v1/me`` → ``account.id``) as its
``owner`` in ``sync.json``; one created while signed out and never synced has none. Signed
in as B, the list shows B's and the ownerless ones; A's stay on disk, hidden, and are never
pushed into B's account — an ownerless one becomes B's on its first push. Signed out,
everything local is shown. When the signed-in account changes, the account-scoped state
starts over: the cursor goes to 0 (a fresh ``tail`` pull), the presence map and the hub's
device list are cleared, and the main chat — one conversation per account on the relay — is
re-homed: the old account's main chat is kept as a hidden thread (``main_of``) and the new
account's own comes back when it has one, else a fresh one begins. Mappings are kept, so
switching back shows everything again. Sign-out alone clears nothing but the key.

Nothing here reaches the network when the account is signed out; ``sync_off`` from the
relay flips the local switch, ``bad_key`` pauses until the next sign-in."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from nanomuse.cloud import CloudClient, CloudError
from nanomuse.logger import logger
from nanomuse.schema import Message
from nanomuse.sync.client import SyncClient
from nanomuse.sync.discovery import (
    LanDiscovery,
    PairingManager,
    build_pair_uri,
    candidate_urls,
    normalize_pair_code,
    parse_pair_payload,
)
from nanomuse.sync.peer import SyncPeerStore, add_pair, load_pairs, remove_pair

if TYPE_CHECKING:
    from nanomuse.server.service import MuseService, Thread


def _main_thread() -> str:
    """The main thread's id.  Imported here, not at module level: a module-level import of
    ``nanomuse.server.events`` pulls in the whole server (api -> service -> this engine),
    which breaks when something imports the sync package first, as ``nanomuse pair`` does."""
    from nanomuse.server.events import MAIN_THREAD as _main

    return _main


def _now_iso() -> str:
    from nanomuse.server.events import now_iso

    return now_iso()


PUSH_DELAY_S = 2.0
PULL_EVERY_S = 60.0
PEER_POLL_EVERY_S = 10.0
BATCH = 200
PAGE = 500
# the first pull of a fresh device: the newest messages of the scope, not the whole store (C9)
TAIL = 300
# a `working: true` without a `false` stands this long (the device may have lost its network)
WORKING_TTL_S = 10 * 60
# how many POSTs one push may make before it lets go (a brand-new device with a long history)
MAX_ROUNDS = 50
# the quietest gap between two presence posts of one run (0.31): a step that changes
# every second still reads on the other devices as one moving line, not a stream
PRESENCE_INTERVAL_S = 2.0
# how often the status watcher checks it was not dropped for slowness (a full queue in
# EventBus.publish discards the subscriber; the watcher notices and subscribes again)
WATCH_POLL_S = 30.0


def new_cid() -> str:
    return str(uuid.uuid4())


def _unix(ts: str | None) -> int:
    """A timeline ``ts`` (ISO) → Unix seconds; now when unreadable."""
    if ts:
        try:
            return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return int(time.time())


def _iso(unix: int | float | None) -> str:
    try:
        return datetime.fromtimestamp(float(unix or 0), UTC).isoformat(timespec="milliseconds")
    except (OverflowError, OSError, ValueError):
        return _now_iso()


# Keys inside app-settings.json that belong strictly to a single physical device and must
# NEVER be copied to or overwritten by a paired peer (copying `hub` gave the phone the PC's
# own device_id, causing peer sync to mistake PC messages for local echoes).
DEVICE_LOCAL_SETTINGS_KEYS = frozenset(
    {"hub", "app_lock", "server", "hands", "gui", "device_nicknames", "watch"}
)

# Canonical avatar files that every MiniMuse UI (desktop, phone, watch) actually loads first.
# Exporting these first ensures the live animated avatar is never crowded out by raw source files.
_CANONICAL_AVATAR_FILES = (
    "idle.webp",
    "idle.mp4",
    "working.webp",
    "working.mp4",
    "happy.webp",
    "happy.mp4",
    "waiting.webp",
    "waiting.mp4",
    "error.webp",
    "error.mp4",
    "dark-idle.webp",
    "dark-idle.mp4",
    "dark-working.webp",
    "dark-working.mp4",
    "dark-happy.webp",
    "dark-happy.mp4",
    "dark-waiting.webp",
    "dark-waiting.mp4",
    "avatar-model.json",
)


def _infer_peer_kind(peer: dict[str, Any]) -> str:
    explicit = str(peer.get("kind") or "").strip().lower()
    if explicit in ("phone", "computer", "tablet", "watch"):
        return explicit
    pid = str(peer.get("id") or "").strip().lower()
    name = str(peer.get("name") or "").strip().lower()
    if pid.startswith("phone-") or any(w in name for w in ("phone", "android", "pixel", "galaxy", "samsung", "iphone", "oneplus", "xiaomi")):
        return "phone"
    if pid.startswith("pc-") or any(w in name for w in ("desktop", "laptop", "macbook", "pc", "windows")):
        return "computer"
    return ""


def _clean_peer_name(name: str, url: str, kind: str = "") -> str:
    clean = str(name or "").strip()
    if clean and clean.lower() != "localhost":
        return clean
    if kind == "phone":
        return "Paired phone"
    if kind == "computer":
        return "Paired computer"
    host = str(url or "").replace("http://", "").replace("https://", "").rstrip("/")
    return host or "Peer"


class ConversationSync:
    def __init__(self, svc: MuseService):
        self.svc = svc
        self.path = svc.data_dir / "sync.json"
        self.local_store = SyncPeerStore(svc.data_dir / "sync-peer.db")
        self.pairing = PairingManager(svc.data_dir)
        self.discovery = LanDiscovery(
            device_id=svc.hub.device_id,
            name_fn=lambda: svc.hub.device_name,
            port_fn=lambda: int(getattr(svc.settings.server, "port", 0) or 0),
            pair_ready_fn=lambda: self.pairing.pair_ready,
            kind="phone" if svc.app.device is not None else "computer",
        )
        self.state: dict[str, Any] = {
            "account_id": "",
            "cursor": 0,
            "local_cursor": 0,
            "enabled": bool(svc.settings.cloud.sync),
            # C9: this device's side chats too (and the other devices'); off = main only
            "side_chats": bool(svc.settings.sync.side_chats),
            "cids": {},  # thread id -> cid
            "titles": {},  # thread id -> the title last pushed
            "local_titles": {},  # thread id -> title mirrored into local_store
            # C10: thread id -> the account (`account.id`) it was synced with; absent = none
            "owners": {},
            # C10: account id -> the thread that holds that account's main chat while another
            # account is signed in (hidden; restored as `main` when the account comes back)
            "mains": {},
            # direct peers: peer key -> {"in": int, "out": int, "last_sync_at": str}
            "peer_cursors": {},
            # direct peers: peer key -> the id of the store their cursor counts in
            "peer_stores": {},
            "deleted_cids": [],
            "deleted_mids": [],
            "device_overrides": {
                "enabled": False,
                "keep_local_appearance": False,
                "keep_local_sentinel": False,
                "keep_local_notifications": False,
                "default_model": "",
            },
        }
        self._load()
        self.client = SyncClient(svc.hub.cloud)
        self.peers: list[dict[str, str]] = self._load_peers()
        self.peer: dict[str, str] | None = self.peers[0] if self.peers else None
        self._peer_key = self.peer["token"] if self.peer else ""
        if self.peers:
            self.state["enabled"] = True  # a pair is the switch; no account needed
        self._peer_status: dict[str, dict[str, Any]] = {}
        self._push_task: asyncio.Task[None] | None = None
        self._pull_task: asyncio.Task[int] | None = None
        self._peer_task: asyncio.Task[None] | None = None
        self._local_pull_task: asyncio.Task[int] | None = None
        self._timer: asyncio.Task[None] | None = None
        self._peer_timer: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._peer_lock = asyncio.Lock()
        self._save_lock = threading.Lock()
        # after the lock: a state file from before side chats synced by default needs one
        # rewind, which writes
        self._migrate_side_chats()
        self._tasks: set[asyncio.Task[Any]] = set()
        # set by stop(): nothing of this engine talks to the relay after that
        self._stopped = False
        # 0.31: what this device last announced per thread — (state, detail) of the run
        # under way — so a step change re-posts only when there is something new to say
        self._announced: dict[str, tuple[str, str]] = {}
        self._presence_scheduled: set[str] = set()
        self._status_queue: asyncio.Queue[dict[str, Any]] | None = None
        # presence (C9): cid -> {thread, device, device_name, state, detail, at} for the other
        # devices' turns under way; a frame says so, a reply or ten minutes clears it
        self.working: dict[str, dict[str, Any]] = {}
        # a one-off pull from zero waiting its turn (side chats just turned on)
        self._pull_from_zero = False
        # a 401 from the relay: nothing more until the next sign-in
        self._paused = False
        self._applying = False
        # the scheduled push is still waiting out its delay (it may be rescheduled); once it
        # is on the wire a new request queues a second round behind it instead
        self._push_waiting = False
        self._push_again: float | None = None
        self.last_pull_at: str | None = None
        self.last_push_at: str | None = None
        self.last_error = ""
        self._personal_cloud_urls_fn: Any = None

    def set_personal_cloud_urls_provider(self, fn: Any) -> None:
        """Hook from the API server returning [tunnel_url, tailscale_url, *lan_urls]."""
        self._personal_cloud_urls_fn = fn

    def _my_reachable_urls(self) -> list[str]:
        """All URLs other devices can use to reach this MiniMuse (Tunnel -> Tailscale -> LAN)."""
        port = int(getattr(self.svc.settings.server, "port", 8787) or 8787)
        host = str(getattr(self.svc.settings.server, "host", "127.0.0.1") or "127.0.0.1")
        out: list[str] = []
        if callable(self._personal_cloud_urls_fn):
            try:
                for u in self._personal_cloud_urls_fn() or []:
                    cu = str(u or "").strip().rstrip("/")
                    if cu.startswith(("http://", "https://")) and cu not in out:
                        out.append(cu)
            except Exception:
                pass
        for u in candidate_urls(port, host):
            cu = str(u or "").strip().rstrip("/")
            if cu.startswith(("http://", "https://")) and cu not in out:
                out.append(cu)
        return out

    def _get_or_mint_return_token(self, peer_id: str, peer_name: str = "") -> str:
        """Return a stable, valid inbound nmpeer_ token for a known paired peer so we can
        auto-heal stale or missing tokens over an already-authenticated reverse sync link."""
        from nanomuse.sync.peer import _pair_slot
        from nanomuse.vault.vault import CredentialVault

        clean_id = str(peer_id or "").strip()
        if not clean_id:
            return ""
        slot = "RET_" + _pair_slot(clean_id)
        try:
            vault = CredentialVault(self.svc.data_dir / "vault.enc", self.svc.data_dir / "vault.key")
            existing = str(vault.get(slot) or "").strip()
            if existing and self.pairing.verify_inbound_token(existing):
                return existing
            tok = self.pairing.mint_inbound_token(clean_id, peer_name)
            vault.set(slot, tok)
            return tok
        except Exception:
            return self.pairing.mint_inbound_token(clean_id, peer_name)

    # ------------------------------------------------------------------ what this device keeps
    def _migrate_side_chats(self) -> None:
        """Side chats used to be opt-in for syncing, and the change cursor moved past the
        rows a pull did not ask for: a phone that paired received the main chat and nothing
        else, for good. One rewind, once per device, reads the skipped chats again. A device
        that explicitly turned the switch off keeps its choice."""
        if self.state.get("side_chats_backfilled") or self.state.get("side_chats_set"):
            return
        for pcur in (self.state.get("peer_cursors") or {}).values():
            if isinstance(pcur, dict):
                pcur["in"] = 0
                pcur["out"] = 0
        self.state["side_chats_backfilled"] = True
        self._save()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            for key in (
                "account_id",
                "cursor",
                "local_cursor",
                "enabled",
                "side_chats",
                # without these two, "side chats off" was forgotten on every restart and
                # the one-time rewind below ran again
                "side_chats_set",
                "side_chats_backfilled",
            ):
                if key in data:
                    self.state[key] = data[key]
            for key in ("cids", "titles", "local_titles", "owners", "mains"):
                if isinstance(data.get(key), dict):
                    self.state[key] = {str(k): str(v) for k, v in data[key].items()}
            if isinstance(data.get("peer_cursors"), dict):
                self.state["peer_cursors"] = {
                    str(k): dict(v)
                    for k, v in data["peer_cursors"].items()
                    if isinstance(v, dict)
                }
            if isinstance(data.get("peer_stores"), dict):
                self.state["peer_stores"] = {
                    str(k): str(v) for k, v in data["peer_stores"].items() if v
                }
            if isinstance(data.get("deleted_cids"), list):
                self.state["deleted_cids"] = [str(c) for c in data["deleted_cids"] if c]
            if isinstance(data.get("deleted_mids"), list):
                self.state["deleted_mids"] = [
                    dict(m) for m in data["deleted_mids"] if isinstance(m, dict) and m.get("mid")
                ]
            if isinstance(data.get("device_overrides"), dict):
                ov = data["device_overrides"]
                self.state["device_overrides"] = {
                    "enabled": bool(ov.get("enabled", False)),
                    "keep_local_appearance": bool(ov.get("keep_local_appearance", False)),
                    "keep_local_sentinel": bool(ov.get("keep_local_sentinel", False)),
                    "keep_local_notifications": bool(ov.get("keep_local_notifications", False)),
                    "default_model": str(ov.get("default_model") or ""),
                }

    def _save(self) -> None:
        with self._save_lock:
            try:
                # written aside and moved on, like every other state file here: a device losing
                # power mid-write would otherwise leave half a JSON file, and `_load` reads that
                # as no state at all — the cursor, the thread↔cid map and the hidden main chats
                # gone, so the next pull starts from nothing
                payload = json.dumps(self.state, ensure_ascii=False, indent=1)
                tmp = self.path.with_name(f"{self.path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
                tmp.write_text(payload, "utf-8")
                tmp.replace(self.path)
            except OSError as exc:  # pragma: no cover
                logger.warning("could not save sync state: {}", exc)

    @property
    def enabled(self) -> bool:
        return bool(self.state.get("enabled", True))

    @property
    def side_chats(self) -> bool:
        """Your own paired devices get your side chats too, unless you turned that off.

        It used to be opt-in, so a phone that paired received the main chat and nothing
        else, and every side chat stayed on the computer that wrote it. A device that never
        touched the switch now syncs them (and the cursors are rewound once, below)."""
        if not self.state.get("side_chats_set"):
            return True
        return bool(self.state.get("side_chats", False))

    @property
    def scope(self) -> str:
        return "all" if self.side_chats else "main"

    @property
    def cursor(self) -> int:
        return int(self.state.get("cursor") or 0)

    @property
    def active(self) -> bool:
        """Signed in (or paired device-to-device), switched on, not paused by a refused key."""
        return (
            self.enabled and (self.svc.hub.signed_in or bool(self.peers)) and not self._paused
        )

    @property
    def cloud_active(self) -> bool:
        return self.enabled and self.svc.hub.signed_in and not self._paused

    @property
    def peer_active(self) -> bool:
        return self.enabled and bool(self.peers)

    def _load_peers(self) -> list[dict[str, str]]:
        """All paired devices from ``pairs.json``, with their tokens out of the vault."""
        from nanomuse.vault.vault import CredentialVault

        pairs = load_pairs(self.svc.data_dir)
        if not pairs:
            return []
        vault = CredentialVault(self.svc.data_dir / "vault.enc", self.svc.data_dir / "vault.key")
        loaded: list[dict[str, str]] = []
        for raw in pairs:
            peer = dict(raw)
            try:
                peer["token"] = str(vault.resolve(peer.get("key") or "", strict=False) or "")
            except Exception:
                peer["token"] = ""
            loaded.append(peer)
        return loaded

    def reload_peers(self) -> list[dict[str, str]]:
        self.peers = self._load_peers()
        self.peer = self.peers[0] if self.peers else None
        self._peer_key = self.peer.get("token", "") if self.peer else ""
        return self.peers

    def _load_peer(self) -> dict[str, str] | None:
        """The first paired device from ``pairs.json``, with its token out of the vault."""
        peers = self._load_peers()
        if not peers:
            return None
        self._peer_key = peers[0].get("token", "")
        return peers[0]

    def _sync_key(self) -> str:
        """The key the Cloud sync client sends when Cloud is active."""
        return self.svc.hub._key() if self.svc.hub.signed_in else self._peer_key

    def cid_of(self, thread_id: str) -> str | None:
        v = self.state["cids"].get(thread_id)
        return str(v) if v else None

    def thread_of(self, cid: str) -> Thread | None:
        for tid, c in self.state["cids"].items():
            if c == cid:
                return self.svc.threads.get(tid)
        return None

    # ------------------------------------------------------------------ whose (C10)
    @property
    def account_id(self) -> str:
        """The account the last sign-in named (``account.id``); "" before any."""
        return str(self.state.get("account_id") or "")

    def owner_of(self, thread_id: str) -> str:
        """The account a conversation was synced with, "" when none (never synced)."""
        return str(self.state["owners"].get(thread_id) or "")

    def visible(self, thread: Thread) -> bool:
        """Shown in the list (C10): signed in, the current account's and the ownerless ones;
        signed out, everything. Another account's main chat (``main_of``) is that account's."""
        if not self.svc.hub.signed_in or not self.account_id:
            return True
        if thread.main_of:
            return thread.main_of == self.account_id
        owner = self.owner_of(thread.id)
        return not owner or owner == self.account_id

    def _mine(self, thread: Thread) -> bool:
        """May move for the signed-in account: its own, or not yet anyone's (C10 rule 3)."""
        if thread.main_of and thread.main_of != self.account_id:
            return False
        owner = self.owner_of(thread.id)
        return not owner or not self.account_id or owner == self.account_id

    def _adopt(self, thread_id: str) -> None:
        """The conversation is the signed-in account's from now on (first push or pull)."""
        if self.account_id and self.state["owners"].get(thread_id) != self.account_id:
            self.state["owners"][thread_id] = self.account_id
            self._save()

    def _eligible(self, thread: Thread) -> bool:
        """Synced from here: not a chat for or from another device, the signed-in account's
        or nobody's yet (C10), and — with side chats off (C9) — the main chat only."""
        if thread.device or thread.remote_from or thread.main_of:
            return False
        if not self._mine(thread):
            return False
        return self.side_chats or thread.id == _main_thread()

    def _cid_for(self, thread: Thread) -> str:
        cid = self.cid_of(thread.id)
        if cid is None:
            cid = new_cid()
            self.state["cids"][thread.id] = cid
            self._save()
        self._adopt(thread.id)
        return cid

    @staticmethod
    def _peer_key_of(peer: dict[str, str]) -> str:
        return str(peer.get("id") or peer.get("url") or peer.get("name") or "")

    def peers_view(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        local_cursor = self.local_store.state().get("cursor", 0)
        for p in self.peers:
            pkey = self._peer_key_of(p)
            st = self._peer_status.get(pkey, {})
            cur = self.state.get("peer_cursors", {}).get(pkey, {})
            out_seq = int(cur.get("out") or 0)
            kind = _infer_peer_kind(p)
            out.append(
                {
                    "id": p.get("id") or "",
                    "name": _clean_peer_name(p.get("name") or "", p.get("url") or "", kind),
                    "kind": kind,
                    "url": p.get("url") or "",
                    "online": bool(st.get("online", False)),
                    "last_sync_at": st.get("last_sync_at") or cur.get("last_sync_at") or None,
                    "last_error": st.get("last_error") or "",
                    "pending": max(0, int(local_cursor) - out_seq),
                }
            )
        return out

    def pending_offline_count(self) -> int:
        count = 0
        for thread in self.svc.threads.values():
            if not self._eligible_for_local_sync(thread):
                continue
            for ev in thread.timeline.events:
                if ev.get("type") in ("user", "assistant") and not ev.get("synced") and not ev.get("via_device"):
                    if ev.get("type") == "assistant" and not ev.get("final"):
                        continue
                    count += 1
        return count

    def view(self) -> dict[str, Any]:
        """What the Data controls & Devices page shows: Cloud or peer sync state."""
        self.reload_peers()
        return {
            "enabled": self.enabled,
            "side_chats": self.side_chats,
            "available": self.svc.hub.signed_in or bool(self.peers),
            "cloud_available": self.svc.hub.signed_in,
            "peer_mode": bool(self.peers),
            "peers": self.peers_view(),
            "nearby": self.discovery.list_nearby(),
            "pending_offline": self.pending_offline_count(),
            "device_id": self.svc.hub.device_id,
            "device_name": self.svc.hub.device_name,
            "urls": candidate_urls(
                int(getattr(self.svc.settings.server, "port", 8787) or 8787),
                str(getattr(self.svc.settings.server, "host", "127.0.0.1") or "127.0.0.1"),
            ),
            "paused": self._paused,
            "cursor": self.cursor,
            "last_pull_at": self.last_pull_at,
            "last_push_at": self.last_push_at,
            "error": self.last_error,
            "working": self.working_view(),
            "device_overrides": dict(
                self.state.get("device_overrides")
                or {"enabled": False, "keep_local_appearance": False, "default_model": ""}
            ),
        }

    def working_view(self) -> list[dict[str, Any]]:
        """The other devices' turns under way (C9): ``[{thread, cid, device, device_name,
        state, detail, at}]``, ``at`` in Unix seconds, entries older than ten minutes
        dropped. ``state``/``detail`` (0.31) say what the run is doing right now."""
        cutoff = int(time.time()) - WORKING_TTL_S
        for cid in [c for c, w in self.working.items() if int(w.get("at") or 0) < cutoff]:
            del self.working[cid]
        return [dict(w) for w in self.working.values()]

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """After the hub is up: a first pull, a push of what is new, and background sync."""
        self._stopped = False
        self.reload_peers()
        if self._timer is None or self._timer.done():
            self._timer = asyncio.create_task(self._tick(), name="sync-timer")
        if self._peer_timer is None or self._peer_timer.done():
            self._peer_timer = asyncio.create_task(self._peer_tick(), name="sync-peer-timer")
        if self._status_queue is None:
            self._status_queue = self.svc.bus.subscribe()
            self._keep(asyncio.create_task(self._watch_status(), name="sync-status"))
        self.discovery.start()
        if self.cloud_active:
            self.pull_soon()
            self.push_soon(delay=PUSH_DELAY_S)
        if self.peer_active:
            self.mirror_local_now()
            self.peer_sync_soon(delay=0.2)

    async def stop(self) -> None:
        """Cancel every task of the engine and wait cleanly before closing clients."""
        self._stopped = True
        await self.discovery.stop()
        tasks = [
            t
            for t in (
                self._timer,
                self._peer_timer,
                self._push_task,
                self._pull_task,
                self._peer_task,
                self._local_pull_task,
                *self._tasks,
            )
            if t is not None and not t.done()
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks)

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(PULL_EVERY_S)
            if self.cloud_active:
                self.pull_soon()

    async def _peer_tick(self) -> None:
        while True:
            await asyncio.sleep(PEER_POLL_EVERY_S)
            self.reload_peers()
            if self.peer_active and not self._stopped:
                self.peer_sync_soon(delay=0.0)

    def account_changed(self, account_id: str) -> None:
        """Signed in (again). A different account (C10 rule 4) starts from cursor 0 — a fresh
        tail pull — with the presence map and the hub's device list cleared and the main chat
        re-homed; the mappings of the account that was here before are kept, so switching
        back shows its conversations again."""
        self._paused = False
        self.last_error = ""
        previous = self.account_id
        if account_id and account_id != previous:
            self.state.update(account_id=account_id, cursor=0)
            self.working.clear()
            self._announced.clear()
            self._presence_scheduled.clear()
            self._save()
            self._rehome_main(previous, account_id)
            if previous:
                logger.info("sync: a different account signed in; its conversations are shown")
        if self.active:
            self.pull_soon()
            self.push_soon()

    def _rehome_main(self, previous: str, account_id: str) -> None:
        """The main chat is one conversation per account (C8), so a new account cannot keep
        the old one's: the old main chat becomes a hidden thread marked ``main_of`` and the
        new account's own comes back when it has one, else a fresh one begins. A main chat
        nobody has synced yet stays — it is nobody's and joins the account on its first push."""
        main = self.svc.threads.get(_main_thread())
        if main is None:
            return
        owner = self.owner_of(_main_thread())
        restore = str(self.state["mains"].get(account_id) or "")
        if restore not in self.svc.threads:
            restore = ""
        if not restore and (not owner or owner == account_id):
            # the main chat is this account's, or nobody's yet (it joins on its first push)
            return
        if not owner:
            # text written under the previous account and never synced: it stays with that
            # account rather than landing in the new one's main chat
            owner = previous
        has_text = any(ev.get("type") in ("user", "assistant") for ev in main.timeline.events)
        archived = self.svc.rehome_main(owner, restore or None, keep=bool(owner and has_text))
        if archived is not None:
            # the mappings follow the threads: the old main chat keeps its cid and owner
            # under its new id, the restored one takes `main` back
            for key in ("cids", "titles", "owners"):
                value = self.state[key].pop(_main_thread(), None)
                if value is not None:
                    self.state[key][archived.id] = value
            self.state["owners"][archived.id] = owner
            self.state["mains"][owner] = archived.id
        else:
            for key in ("cids", "titles", "owners"):
                self.state[key].pop(_main_thread(), None)
        if restore:
            self.state["mains"].pop(account_id, None)
            for key in ("cids", "titles", "owners"):
                value = self.state[key].pop(restore, None)
                if value is not None:
                    self.state[key][_main_thread()] = value
            self.state["owners"][_main_thread()] = account_id
        self._save()

    def signed_out(self) -> None:
        """The key is gone: nothing more until the next sign-in; what is local stays."""
        for t in (self._push_task, self._pull_task):
            if t is not None and not t.done():
                t.cancel()
        self._paused = False

    def _clear_marks(self) -> None:
        """Every synced event is unsynced again: the next push sends it all (a new account, the
        switch turned back on, the main chat re-homed). Another account's rows are left as
        they are (C10): they are not going anywhere from here."""
        for thread in self.svc.threads.values():
            if self._mine(thread):
                self._clear_thread_marks(thread)

    @staticmethod
    def _clear_thread_marks(thread: Thread) -> None:
        changed = False
        for ev in thread.timeline.events:
            # another device's row is that device's to send again, not ours (C8)
            if ev.get("synced") and not ev.get("via_device"):
                ev["synced"] = False
                changed = True
        if changed:
            thread.timeline.save()

    # ------------------------------------------------------------------ the switch
    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """Data controls: works for Cloud relay AND Cloud-free paired devices."""
        enabled = bool(enabled)
        relay: dict[str, Any] | None = None
        if self.svc.hub.signed_in:
            self.client.cloud.api_key = self._sync_key()
            relay = await self.client.set_enabled(enabled)
        self.state["enabled"] = enabled
        self._save()
        if enabled:
            self._paused = False
            self.state["titles"] = {}
            self._clear_marks()
            if self.svc.hub.signed_in:
                self.pull_soon()
                self.push_soon()
            if self.peers:
                self.mirror_local_now()
                self.peer_sync_soon(delay=0.0)
        else:
            for t in (self._push_task, self._pull_task, self._peer_task):
                if t is not None and not t.done():
                    t.cancel()
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
        return {**self.view(), "relay": relay}

    def set_side_chats(self, on: bool) -> dict[str, Any]:
        """Data controls -> Also sync side chats (C9)."""
        on = bool(on)
        if on == self.side_chats:
            return self.view()
        self.state["side_chats"] = on
        self.state["side_chats_set"] = True
        self._save()
        if on and self.cloud_active:
            self._pull_from_zero = True
            self.pull_soon()
            self.push_soon(delay=0.5)
        if on and self.peer_active:
            for pcur in self.state.get("peer_cursors", {}).values():
                if isinstance(pcur, dict):
                    pcur["in"] = 0
            self.mirror_local_now()
            self.peer_sync_soon(delay=0.1)
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
        return self.view()

    async def relay_state(self) -> dict[str, Any] | None:
        """The relay's own view (or local peer store counts when syncing direct)."""
        if not self.svc.hub.signed_in:
            if self.peers:
                st = self.local_store.state()
                return {**st, "peer_mode": True}
            return None
        self.client.cloud.api_key = self._sync_key()
        try:
            return await self.client.state()
        except CloudError as exc:
            self._note_error(exc)
            return None

    async def delete_remote(self) -> dict[str, Any] | None:
        """Delete synced conversations from relay/local peer log without removing local chats."""
        out: dict[str, Any] | None = None
        if self.svc.hub.signed_in:
            self.client.cloud.api_key = self._sync_key()
            out = await self.client.wipe()
            self.state["cursor"] = int(out.get("cursor") or self.cursor)
        if self.peers:
            out = self.local_store.wipe()
            self.state["local_cursor"] = 0
            self.state["peer_cursors"] = {}
        self._save()
        return out

    # ------------------------------------------------------------------ hooks from the service
    def _eligible_for_local_sync(self, thread: Thread) -> bool:
        if thread.device or thread.remote_from or thread.main_of:
            return False
        if not self._mine(thread):
            return False
        return True

    def _cid_for_local(self, thread: Thread) -> str:
        cid = self.cid_of(thread.id)
        if cid is None:
            cid = new_cid()
            self.state["cids"][thread.id] = cid
            self._save()
        return cid

    def message_sent(self, thread: Thread) -> None:
        """The person's message is on the timeline: persist locally & push if online."""
        if self.peers or (self.enabled and not self.svc.hub.signed_in):
            self.mirror_local_now()
        if self.cloud_active and self._eligible(thread):
            self.push_soon(delay=0.0)
            self._working_soon(thread, True)
        if self.peer_active and (self.side_chats or thread.id == _main_thread()):
            self.peer_sync_soon(delay=0.1)

    def turn_finished(self, thread: Thread) -> None:
        if self.peers or (self.enabled and not self.svc.hub.signed_in):
            self.mirror_local_now()
        if self.cloud_active and self._eligible(thread):
            self.push_soon(delay=PUSH_DELAY_S)
            self._working_soon(thread, False)
        if self.peer_active and (self.side_chats or thread.id == _main_thread()):
            self.peer_sync_soon(delay=0.5)

    def _working_soon(self, thread: Thread, working: bool) -> None:
        """Presence to the relay once the push it belongs to is through — so the
        conversation exists there and carries the id it ended up with. Off the turn's path,
        never retried, errors at debug."""
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._keep(loop.create_task(self._send_working(thread, working), name="sync-working"))

    async def _send_working(self, thread: Thread, working: bool) -> None:
        for _ in range(3):  # a push rescheduled under us: wait for the one that replaced it
            push = self._push_task
            if push is None or push.done():
                break
            # wait for the push without taking on its fate: a cancelled push (rescheduled, or
            # stop() on the way) must not read as this task being cancelled — and when this
            # task is the one cancelled, that ends it here rather than letting it go on to
            # open a connection to the relay while the engine is stopping
            await asyncio.wait({push})
        cid = self.cid_of(thread.id)
        if not cid or not self.active or self._stopped:
            return
        status = self.svc.ui.status.get(thread.id) or {}
        state = str(status.get("state") or "working")
        detail = str(status.get("detail") or "")
        if working:
            self._announced[thread.id] = (state, detail)
        else:
            self._announced.pop(thread.id, None)
        self.client.cloud.api_key = self._sync_key()
        try:
            await self.client.working(
                cid, working, self.svc.hub.device_id, state=state, detail=detail
            )
        except Exception as exc:  # noqa: BLE001 — presence is a hint, never worth a retry
            logger.debug("sync: working={} not delivered: {}", working, exc)

    def status_changed(self, thread_id: str, state: str, detail: str) -> None:
        """The run's step moved on (0.31): presence again with the new state and step.
        Throttled to one post per interval — the task sleeps, then reads the newest status
        and sends only what is still true and still new."""
        if self._stopped or state not in ("working", "waiting"):
            return
        thread = self.svc.threads.get(thread_id)
        if thread is None or not self.active or not self._eligible(thread):
            return
        if self._announced.get(thread_id) == (state, detail):
            return
        if thread_id in self._presence_scheduled:
            return
        self._presence_scheduled.add(thread_id)
        self._keep(asyncio.create_task(self._presence_later(thread), name="sync-presence"))

    async def _presence_later(self, thread: Thread) -> None:
        """One post per interval, until what is announced is what the run is doing: a step
        that lands while a post is under way is caught by the next round, not lost."""
        try:
            while not self._stopped and self.active:
                await asyncio.sleep(PRESENCE_INTERVAL_S)
                status = self.svc.ui.status.get(thread.id) or {}
                state = str(status.get("state") or "")
                if state not in ("working", "waiting"):
                    return  # the run ended while we waited; turn_finished owns the goodbye
                detail = str(status.get("detail") or "")
                if self._announced.get(thread.id) == (state, detail):
                    return
                await self._send_working(thread, True)
        finally:
            self._presence_scheduled.discard(thread.id)

    async def _watch_status(self) -> None:
        """The bus's status frames (the local run's steps), feeding presence (0.31). The
        bus drops a subscriber whose queue filled (a burst of stream frames while this
        task waited its turn), so the wait is bounded: a dropped watcher subscribes
        itself again rather than hanging on a queue nobody fills."""
        try:
            while not self._stopped:
                q = self._status_queue
                if q is None or not self.svc.bus.subscribed(q):
                    if q is not None:
                        self.svc.bus.unsubscribe(q)  # a discard is idempotent either way
                    q = self.svc.bus.subscribe()
                    self._status_queue = q
                    # the frames the drop ate are gone; the runs' own status is the
                    # catch-up, so a step that landed while this healed is not lost
                    for tid, st in (getattr(self.svc.ui, "status", {}) or {}).items():
                        self.status_changed(
                            str(tid), str(st.get("state") or ""), str(st.get("detail") or "")
                        )
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=WATCH_POLL_S)
                except TimeoutError:
                    continue
                if msg.get("kind") != "status":
                    continue
                status = msg.get("status") or {}
                self.status_changed(
                    str(status.get("thread") or ""),
                    str(status.get("state") or ""),
                    str(status.get("detail") or ""),
                )
        finally:
            q = self._status_queue
            if q is not None:
                self.svc.bus.unsubscribe(q)
                if self._status_queue is q:
                    self._status_queue = None

    def thread_changed(self, thread: Thread) -> None:
        """Created, renamed, or restored: mirror locally and push now."""
        if self._applying:
            return
        if self.peers or (self.enabled and not self.svc.hub.signed_in):
            cid = self.cid_of(thread.id) or self._cid_for_local(thread)
            deleted = self.state.get("deleted_cids", [])
            if cid in deleted:
                self.state["deleted_cids"] = [c for c in deleted if c != cid]
                self._save()
            self.mirror_local_now()
        if self.cloud_active and self._eligible(thread):
            self.push_soon(delay=0.2)
        if self.peer_active and (self.side_chats or thread.id == _main_thread()):
            self.peer_sync_soon(delay=0.1)

    def thread_deleted(self, thread_id: str) -> None:
        cid = self.state["cids"].pop(thread_id, None)
        self.state["titles"].pop(thread_id, None)
        self.state.get("local_titles", {}).pop(thread_id, None)
        owner = self.state["owners"].pop(thread_id, None)
        if owner and self.state["mains"].get(owner) == thread_id:
            del self.state["mains"][owner]
        for c in [c for c, w in self.working.items() if w.get("thread") == thread_id]:
            del self.working[c]
        self._announced.pop(thread_id, None)
        if cid and not self._applying and (self.side_chats or thread_id == _main_thread()):
            # Record tombstone locally so offline/disconnected peers receive it on reconnect
            try:
                self.local_store.delete_conversation(cid)
            except Exception:
                pass
            if cid not in self.state.setdefault("deleted_cids", []):
                self.state["deleted_cids"].append(cid)
        self._save()
        if (
            cid
            and self.cloud_active
            and not self._stopped
            and not self._applying
            and (self.side_chats or thread_id == _main_thread())
        ):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            self._keep(loop.create_task(self._delete_remote_conversation(cid), name="sync-delete"))
        if cid and self.peer_active and not self._stopped and not self._applying:
            self.peer_sync_soon(delay=0.1)

    def event_deleted(self, thread: Thread, ev: dict[str, Any]) -> None:
        """One message removed from timeline: tombstone locally and push to peers."""
        if self._applying:
            return
        cid = self.cid_of(thread.id)
        if not cid:
            return
        mid = str(ev.get("mid") or "")
        if not mid:
            return
        self.local_store.delete_message(cid, mid)
        pending = self.state.setdefault("deleted_mids", [])
        if not any(m.get("mid") == mid for m in pending):
            pending.append(
                {
                    "mid": mid,
                    "cid": cid,
                    "role": str(ev.get("type") or "user"),
                    "created_at": _unix(ev.get("ts")),
                }
            )
        self._save()
        if self.cloud_active and self._eligible(thread):
            self.push_soon(delay=0.1)
        if self.peer_active and (self.side_chats or thread.id == _main_thread()):
            self.peer_sync_soon(delay=0.1)

    def on_frame(self, frame: dict[str, Any]) -> None:
        """The hub says another device pushed: pull when our cursor is behind. Or (C9) that
        a turn started or ended there: the presence map and the web app hear it."""
        if str(frame.get("from") or "") == self.svc.hub.device_id:
            return
        if frame.get("type") == "working":
            self._apply_working(frame)
            return
        if int(frame.get("cursor") or 0) > self.cursor and self.active:
            self.pull_soon()

    def _apply_working(self, frame: dict[str, Any]) -> None:
        cid = str(frame.get("cid") or "")
        if not cid:
            return
        thread = self.thread_of(cid)
        if thread is None or not self._eligible(thread):
            return
        device = str(frame.get("from") or "")
        at = int(frame.get("at") or time.time())
        if frame.get("working"):
            self.working[cid] = {
                "thread": thread.id,
                "cid": cid,
                "device": device,
                "device_name": str(frame.get("device_name") or ""),
                "state": str(frame.get("state") or "working"),
                "detail": str(frame.get("detail") or ""),
                "at": at,
            }
        else:
            current = self.working.get(cid)
            # another device's `true` stands: the one that finished is not the one working
            if current is None or (device and current.get("device") != device):
                return
            del self.working[cid]
        self.svc.bus.publish(
            {
                "kind": "working",
                "thread": thread.id,
                "cid": cid,
                "device": device,
                "device_name": str(frame.get("device_name") or ""),
                "state": str(frame.get("state") or "working"),
                "detail": str(frame.get("detail") or ""),
                "working": bool(frame.get("working")),
                "at": at,
            }
        )

    def _clear_working(self, cid: str, device: str) -> None:
        """The reply from that device arrived: its line goes."""
        current = self.working.get(cid)
        if current is None or current.get("device") != device:
            return
        self._apply_working({"cid": cid, "from": device, "working": False, "at": time.time()})

    def _keep(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _delete_remote_conversation(self, cid: str) -> None:
        self.client.cloud.api_key = self._sync_key()
        try:
            await self.client.delete_conversation(cid)
        except CloudError as exc:
            if exc.code != "no_conversation":
                self._note_error(exc)

    # ------------------------------------------------------------------ scheduling
    def push_soon(self, delay: float | None = None) -> None:
        """Schedule a push ``delay`` seconds from now (``PUSH_DELAY_S`` by default, read when
        the push is scheduled so a test can shorten it)."""
        if delay is None:
            delay = PUSH_DELAY_S
        if not self.active or self._stopped:
            return
        if not self.cloud_active:
            self.peer_sync_soon(delay=delay)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._push_task is not None and not self._push_task.done():
            if not self._push_waiting:
                self._push_again = (
                    min(self._push_again, delay) if self._push_again is not None else delay
                )
                return
            self._push_task.cancel()
        self._push_task = loop.create_task(self._push_later(delay), name="sync-push")

    async def _push_later(self, delay: float) -> None:
        while True:
            self._push_waiting = True
            try:
                await asyncio.sleep(delay)
            finally:
                self._push_waiting = False
            try:
                await self.push()
            except CloudError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("sync push")
            if self._push_again is None:
                return
            delay, self._push_again = self._push_again, None

    def pull_soon(self) -> None:
        if not self.active or self._stopped:
            return
        if not self.cloud_active:
            self.peer_sync_soon(delay=0.0)
            return
        if self._pull_task is not None and not self._pull_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._pull_task = loop.create_task(self._pull_quietly(), name="sync-pull")

    async def _pull_quietly(self) -> int:
        try:
            return await self.pull()
        except CloudError:
            pass  # noted by pull()
        except Exception:  # noqa: BLE001
            logger.exception("sync pull")
        return 0

    def _note_error(self, exc: CloudError) -> None:
        """Rule 7: network errors are silent (the next trigger tries again); ``sync_off`` flips
        the switch here; a refused key pauses until the next sign-in."""
        if exc.code == "sync_off":
            self.state["enabled"] = False
            self._save()
            self.last_error = ""
            logger.info("sync: turned off on another device; off here too")
            return
        if exc.status == 401:
            self._paused = True
            self.last_error = "signed_out"
            return
        self.last_error = exc.code
        logger.debug("sync: {}", exc.describe())

    # ------------------------------------------------------------------ push
    def _attachments(self, ev: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for f in ev.get("files") or []:
            if isinstance(f, dict):
                out.append(
                    {
                        "name": str(f.get("name") or "")[:200],
                        "mime": str(f.get("mime") or "")[:100],
                        "size": int(f.get("size") or 0),
                    }
                )
        return out

    def _unsynced(self, thread: Thread, cid: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """(event, wire message) for every user / final assistant text not yet on the relay."""
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for ev in thread.timeline.events:
            kind = ev.get("type")
            if kind not in ("user", "assistant") or ev.get("synced") or ev.get("quiet"):
                continue
            if ev.get("via_device"):
                # written on another device: shown here, never pushed as ours
                continue
            if kind == "assistant" and not ev.get("final"):
                continue
            text = str(ev.get("text") or "")
            attachments = self._attachments(ev)
            if not text.strip() and not attachments:
                continue
            mid = str(ev.get("mid") or "")
            if not mid:
                mid = new_cid()
                ev["mid"] = mid
            wire = {
                "mid": mid,
                "cid": cid,
                "role": kind,
                "text": text,
                "created_at": _unix(ev.get("ts")),
            }
            if attachments:
                wire["attachments"] = attachments
            out.append((ev, wire))
        return out

    async def push(self) -> dict[str, Any]:
        """Everything new on this device to the relay (or paired peers), in batches of 200 messages."""
        if not self.active:
            return {"pushed": 0}
        if not self.cloud_active:
            self.mirror_local_now()
            out = await self.sync_peers()
            return {"pushed": int(out.get("pushed") or 0), "cursor": self.cursor}
        async with self._lock:
            try:
                return await self._push_locked()
            except CloudError as exc:
                self._note_error(exc)
                raise

    async def _push_locked(self) -> dict[str, Any]:
        self.client.cloud.api_key = self._sync_key()
        device = self.svc.hub.device_id
        pushed = 0
        sent = False
        for _round in range(MAX_ROUNDS):
            conversations: list[dict[str, Any]] = []
            messages: list[dict[str, Any]] = []
            pending: list[tuple[Thread, dict[str, Any]]] = []
            for thread in list(self.svc.threads.values()):
                if not self._eligible(thread):
                    continue
                cid = self._cid_for(thread)
                if self.state["titles"].get(thread.id) != thread.title:
                    conversations.append(
                        {
                            "cid": cid,
                            "kind": "main" if thread.id == _main_thread() else "side",
                            "title": thread.title,
                            "created_at": _unix(thread.created_at),
                            "updated_at": _unix(thread.updated_at),
                        }
                    )
                for ev, wire in self._unsynced(thread, cid):
                    if len(messages) >= BATCH:
                        break
                    messages.append(wire)
                    pending.append((thread, ev))
            if not conversations and not messages:
                break
            sent = True
            out = await self.client.push(device, conversations, messages)
            # the cursor only moves on a pull: the relay's cursor after our POST would skip
            # whatever the other devices pushed in between
            rejected = [r for r in out.get("rejected") or [] if isinstance(r, dict)]
            redirect: dict[str, str] = {}
            for r in rejected:
                if r.get("reason") == "main_exists" and r.get("cid") and r.get("cid_main"):
                    redirect[str(r["cid"])] = str(r["cid_main"])
            if redirect:
                # the account's main chat already has an id: ours takes it, and its messages go
                # again under it on the next round
                for tid, cid in list(self.state["cids"].items()):
                    if cid in redirect:
                        self.state["cids"][tid] = redirect[cid]
                        self.state["titles"].pop(tid, None)
                        logger.info("sync: the main chat adopts the account's conversation id")
            refused_mids = {
                str(r.get("mid")): str(r.get("reason") or "")
                for r in rejected
                if r.get("mid") and r.get("reason") != "main_exists"
            }
            for thread, ev in pending:
                mid = str(ev.get("mid") or "")
                if mid in refused_mids:
                    # a bad row stays bad: do not send it again and again
                    ev["synced"] = True
                    ev["sync_refused"] = refused_mids[mid]
                elif not any(
                    r.get("mid") == mid and r.get("reason") == "main_exists" for r in rejected
                ):
                    ev["synced"] = True
                    pushed += 1
                thread.timeline.save()
            for c in conversations:
                if not any(r.get("cid") == c["cid"] for r in rejected):
                    for tid, cid in self.state["cids"].items():
                        if cid == c["cid"]:
                            self.state["titles"][tid] = c["title"]
            self._save()
            if len(messages) < BATCH and not redirect:
                break
        if pushed:
            self.last_push_at = _now_iso()
            logger.debug("sync: {} message(s) pushed", pushed)
        if sent:
            # our rows come back on the next pull (known ids, no change) and the cursor catches up
            self.pull_soon()
        return {"pushed": pushed, "cursor": self.cursor}

    # ------------------------------------------------------------------ pull
    async def pull(self) -> int:
        """What the other devices pushed since our cursor, applied in order. Returns how many
        rows were applied."""
        if not self.active:
            return 0
        if not self.cloud_active:
            res = await self.sync_peers()
            return int(res.get("applied") or 0)
        async with self._lock:
            try:
                return await self._pull_locked()
            except CloudError as exc:
                self._note_error(exc)
                raise

    async def _pull_locked(self) -> int:
        self.client.cloud.api_key = self._sync_key()
        applied = 0
        # C9: a fresh device (cursor 0) asks for the tail of its scope, not the whole store;
        # side chats just turned on ask once more from zero, for everything, as a tail too
        from_zero = self._pull_from_zero or self.cursor == 0
        self._pull_from_zero = False
        for _page in range(MAX_ROUNDS):
            if from_zero:
                out = await self.client.changes(0, PAGE, scope=self.scope, tail=TAIL)
                from_zero = False
                skipped = int(out.get("skipped") or 0)
                if skipped:
                    logger.info(
                        "sync: the newest {} messages pulled; {} older stay on the relay",
                        TAIL,
                        skipped,
                    )
            else:
                out = await self.client.changes(self.cursor, PAGE, scope=self.scope)
            # The page's conversations first, then its messages, each in seq order: a rename
            # puts a conversation's seq above its messages, and the relay sends every
            # message's conversation along with the page so none of them is an orphan.
            convs = [c for c in out.get("conversations") or [] if isinstance(c, dict)]
            msgs = [m for m in out.get("messages") or [] if isinstance(m, dict)]
            convs.sort(key=lambda c: int(c.get("seq") or 0))
            msgs.sort(key=lambda m: int(m.get("seq") or 0))
            self._applying = True
            try:
                for row in convs:
                    self._apply_conversation(row)
                    applied += 1
                for row in msgs:
                    self._apply_message(row)
                    applied += 1
            finally:
                self._applying = False
            self.state["cursor"] = max(self.cursor, int(out.get("cursor") or 0))
            self._save()
            if not out.get("more"):
                break
        self.last_pull_at = _now_iso()
        self.last_error = ""
        if applied:
            logger.info("sync: {} change(s) from the account's other devices", applied)
            # what adoption or a tombstone left unsynced goes up now
            self.push_soon(delay=0.5)
        return applied

    def _apply_conversation(self, row: dict[str, Any]) -> None:
        cid = str(row.get("cid") or "")
        if not cid:
            return
        kind = str(row.get("kind") or "side")
        if kind != "main" and not self.side_chats:
            # C9: side chats stay where they were written; a row that still arrives (an
            # older relay without `scope`) is left alone, as is a copy already here
            return
        thread = self.thread_of(cid)
        if thread is not None and not self._mine(thread):
            # another account's conversation happens to carry this id: not ours to touch
            return
        if row.get("deleted"):
            if thread is None:
                return
            if thread.id == _main_thread():
                # the main chat is never deleted here; it starts over on the relay with a new id
                self.state["cids"].pop(thread.id, None)
                self.state["titles"].pop(thread.id, None)
                self._clear_thread_marks(thread)
                self._save()
                return
            self.svc.delete_thread(thread.id)
            return
        title = str(row.get("title") or "").strip()
        if kind == "main":
            main = self.svc.threads.get(_main_thread())
            if main is None:
                return
            if thread is None or thread.id != _main_thread():
                if thread is not None:
                    self.state["cids"].pop(thread.id, None)
                self.state["cids"][_main_thread()] = cid
                self.state["titles"][_main_thread()] = main.title
                self._clear_thread_marks(main)
                self._save()
                self._adopt(_main_thread())
            if title and title != main.title and title != "Main chat":
                remote_updated = _iso(row.get("updated_at"))
                if not main.updated_at or remote_updated >= main.updated_at:
                    self.state["titles"][_main_thread()] = title
                    self.state.setdefault("local_titles", {})[_main_thread()] = title
                    self.svc.rename_thread(_main_thread(), title)
            return
        if thread is not None:
            if title and title != thread.title:
                self.state["titles"][thread.id] = title
                self.svc.rename_thread(thread.id, title)
            return
        device = str(row.get("device") or "")
        if device and device == self.svc.hub.device_id:
            # ours, but the mapping was lost (a reset): no second copy
            return
        created = self.svc.create_thread(title or "Side chat")
        created.origin_device = device
        created.origin_device_name = str(row.get("device_name") or "")
        created.created_at = _iso(row.get("created_at"))
        created.updated_at = _iso(row.get("updated_at"))
        self.state["cids"][created.id] = cid
        self.state["titles"][created.id] = created.title
        self._save()
        self._adopt(created.id)
        self.svc._save_index()
        self.svc.bus.publish({"kind": "thread", "thread": created.meta()})

    def _find_event(self, thread: Thread, mid: str) -> dict[str, Any] | None:
        for ev in thread.timeline.events:
            if ev.get("mid") == mid:
                return ev
        return None

    def _apply_message(self, row: dict[str, Any]) -> None:
        cid = str(row.get("cid") or "")
        mid = str(row.get("mid") or "")
        if not cid or not mid:
            return
        thread = self.thread_of(cid)
        if thread is None or not self._eligible(thread):
            return
        existing = self._find_event(thread, mid)
        if row.get("deleted"):
            if existing is not None:
                thread.timeline.remove(existing["id"])
                thread.timeline.flush()
                role_ev = str(existing.get("type") or "")
                if role_ev in ("user", "assistant"):
                    thread.agent.forget(
                        role_ev,
                        str(existing.get("text") or ""),
                        attachments_count=len(existing.get("files") or []),
                    )
                    if hasattr(self.svc, "_unindex_event"):
                        self.svc._unindex_event(thread.id, existing)
                thread.updated_at = _now_iso()
                self.svc._save_index()
                self.svc.bus.publish(
                    {"kind": "event_removed", "thread": thread.id, "id": existing["id"]}
                )
                self.svc.bus.publish({"kind": "thread", "thread": thread.meta()})
            return
        if existing is not None:
            return
        device = str(row.get("device") or "")
        if device and device == self.svc.hub.device_id:
            # the echo of our own push (or a row of ours the timeline no longer holds):
            # never a second bubble (C8)
            return
        role = str(row.get("role") or "")
        if role not in ("user", "assistant"):
            return
        text = str(row.get("text") or "")
        event: dict[str, Any] = {
            "type": role,
            "text": text,
            "mid": mid,
            "synced": True,
            "ts": _iso(row.get("created_at")),
        }
        if device:
            event["via_device"] = device
            event["via_device_name"] = str(row.get("device_name") or "")
        if role == "assistant":
            event["final"] = True
        if row.get("truncated"):
            event["truncated"] = True
        attachments = row.get("attachments")
        if isinstance(attachments, list) and attachments:
            # names and sizes only: the files stayed on the device they were made on
            event["files"] = [
                {
                    "name": str(a.get("name") or ""),
                    "size": int(a.get("size") or 0),
                    "mime": str(a.get("mime") or ""),
                    "path": "",
                    "kind": "remote",
                }
                for a in attachments
                if isinstance(a, dict)
            ]
        ev = thread.timeline.add(event)
        # in time order with what is here already (add() appends; the sort is stable, so a
        # local event with the same second stays in front - C8: ties, local first)
        thread.timeline.events.sort(key=lambda e: str(e.get("ts") or ""))
        thread.timeline.save()
        thread.timeline.flush()
        thread.updated_at = max(thread.updated_at, ev["ts"])
        if role == "assistant" and device:
            # the reply is here: that device's "working..." line goes (C9)
            self._clear_working(cid, device)
        if not thread.busy:
            # the agent reads it as history on its next turn here
            msg = Message.user(text) if role == "user" else Message.assistant(text)
            msg.meta["mid"] = mid
            thread.agent.messages.append(msg)
            thread.agent._save_session()
        self.svc.bus.publish({"kind": "event", "event": ev})
        self.svc.bus.publish({"kind": "thread", "thread": thread.meta()})

    # ------------------------------------------------------------------ Cloud-free direct peer sync & offline merge
    def mirror_local_now(self) -> int:
        """Persist local eligible threads, messages, renames, and deletions into local_store
        so paired devices can pull and merge them cleanly once reconnected."""
        device = self.svc.hub.device_id
        self.local_store.set_enabled(True)
        existing_mids: set[str] = set()
        existing_cids: dict[str, tuple[str, int]] = {}
        try:
            with self.local_store._tx() as c:
                for r in c.execute("SELECT mid FROM sync_messages").fetchall():
                    existing_mids.add(str(r["mid"]))
                for r in c.execute("SELECT cid, title, deleted FROM sync_conversations").fetchall():
                    existing_cids[str(r["cid"])] = (str(r["title"] or ""), int(r["deleted"] or 0))
        except sqlite3.Error:
            pass
        convs: list[dict[str, Any]] = []
        msgs: list[dict[str, Any]] = []
        changed_threads: list[Thread] = []
        for thread in list(self.svc.threads.values()):
            if not self._eligible_for_local_sync(thread):
                continue
            cid = self._cid_for_local(thread)
            prev_row = existing_cids.get(cid)
            if prev_row is None or prev_row[0] != thread.title or prev_row[1] == 1:
                convs.append(
                    {
                        "cid": cid,
                        "kind": "main" if thread.id == _main_thread() else "side",
                        "title": thread.title,
                        "device": thread.origin_device or device,
                        "created_at": _unix(thread.created_at),
                        "updated_at": _unix(thread.updated_at),
                        "deleted": False,
                    }
                )
            t_dirty = False
            for ev in thread.timeline.events:
                kind = ev.get("type")
                if kind not in ("user", "assistant") or ev.get("quiet"):
                    continue
                if kind == "assistant" and not ev.get("final"):
                    continue
                text = str(ev.get("text") or "")
                attachments = self._attachments(ev)
                if not text.strip() and not attachments:
                    continue
                mid = str(ev.get("mid") or "")
                if not mid:
                    mid = new_cid()
                    ev["mid"] = mid
                    t_dirty = True
                if mid in existing_mids:
                    continue
                existing_mids.add(mid)
                msgs.append(
                    {
                        "mid": mid,
                        "cid": cid,
                        "role": kind,
                        "text": text,
                        "device": str(ev.get("via_device") or device),
                        "created_at": _unix(ev.get("ts")),
                        "attachments": attachments,
                        "deleted": False,
                    }
                )
            if t_dirty:
                changed_threads.append(thread)
        for thread in changed_threads:
            thread.timeline.save()
        count = 0
        if convs or msgs:
            for i in range(0, max(1, len(msgs)), 180):
                batch_msgs = msgs[i : i + 180]
                batch_convs = convs if i == 0 else []
                res = self.local_store.push(device, batch_convs, batch_msgs)
                count += int(res.get("accepted") or 0)
            # Advance local ingest cursor past our own local push so we do not re-apply our own writes
            cur_after = int(self.local_store.state().get("cursor") or 0)
            self.state["local_cursor"] = max(int(self.state.get("local_cursor") or 0), cur_after)
            self._save()
        return count

    def _adopt_main_cid(self, old_cid: str, new_cid_val: str) -> None:
        if not old_cid or not new_cid_val or old_cid == new_cid_val:
            return
        self.state["cids"][_main_thread()] = new_cid_val
        try:
            with self.local_store._tx() as c:
                c.execute("BEGIN IMMEDIATE")
                old_row = c.execute(
                    "SELECT * FROM sync_conversations WHERE cid=?", (old_cid,)
                ).fetchone()
                has_new = c.execute(
                    "SELECT cid FROM sync_conversations WHERE cid=?", (new_cid_val,)
                ).fetchone()
                if old_row is not None and has_new is None:
                    c.execute(
                        "UPDATE sync_conversations SET cid=?, seq=? WHERE cid=?",
                        (new_cid_val, self.local_store._next_seq(c), old_cid),
                    )
                elif old_row is not None:
                    c.execute("DELETE FROM sync_conversations WHERE cid=?", (old_cid,))
                c.execute("UPDATE sync_messages SET cid=? WHERE cid=?", (new_cid_val, old_cid))
                # Bump seq of moved local main messages so peer receives them under new cid
                rows = c.execute("SELECT mid FROM sync_messages WHERE cid=?", (new_cid_val,)).fetchall()
                for r in rows:
                    c.execute(
                        "UPDATE sync_messages SET seq=? WHERE mid=?",
                        (self.local_store._next_seq(c), r["mid"]),
                    )
                c.commit()
        except sqlite3.Error as exc:
            logger.debug("adopt main cid failed: {}", exc)
        self._save()

    def _mirror_pulled_into_local_store(
        self, convs: list[dict[str, Any]], msgs: list[dict[str, Any]]
    ) -> None:
        if not convs and not msgs:
            return
        clean_convs = [
            {
                "cid": c.get("cid"),
                "kind": c.get("kind") or "side",
                "title": c.get("title") or "",
                "device": c.get("device") or "",
                "created_at": c.get("created_at") or 0.0,
                "updated_at": c.get("updated_at") or 0.0,
                "deleted": bool(c.get("deleted")),
            }
            for c in convs
        ]
        clean_msgs = [
            {
                "mid": m.get("mid"),
                "cid": m.get("cid"),
                "role": m.get("role") or "user",
                "text": m.get("text") or "",
                "device": m.get("device") or "",
                "created_at": m.get("created_at") or 0.0,
                "attachments": m.get("attachments") or [],
                "deleted": bool(m.get("deleted")),
            }
            for m in msgs
        ]
        for i in range(0, max(1, len(clean_msgs)), 180):
            self.local_store.push(
                self.svc.hub.device_id,
                clean_convs if i == 0 else [],
                clean_msgs[i : i + 180],
            )
        cur_after = int(self.local_store.state().get("cursor") or 0)
        self.state["local_cursor"] = max(int(self.state.get("local_cursor") or 0), cur_after)
        self._save()

    def on_inbound_peer_push(self) -> None:
        """Called when a remote peer pushes directly into /v1/sync/changes here."""
        if self._stopped or not self.enabled:
            return
        if self._local_pull_task is not None and not self._local_pull_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._local_pull_task = loop.create_task(self.pull_local_store(), name="sync-local-pull")

    async def pull_local_store(self) -> int:
        """Apply rows pushed by direct peers into our local_store to live threads."""
        applied = 0
        since = int(self.state.get("local_cursor") or 0)
        async with self._lock:
            for _ in range(MAX_ROUNDS):
                out = self.local_store.changes(since, PAGE, scope=self.scope)
                convs = [c for c in out.get("conversations") or [] if isinstance(c, dict)]
                msgs = [m for m in out.get("messages") or [] if isinstance(m, dict)]
                convs.sort(key=lambda c: int(c.get("seq") or 0))
                msgs.sort(key=lambda m: int(m.get("seq") or 0))
                self._applying = True
                try:
                    for row in convs:
                        cid = str(row.get("cid") or "")
                        missing_locally = bool(cid) and self.thread_of(cid) is None
                        if (
                            str(row.get("device") or "") != self.svc.hub.device_id
                            or row.get("deleted")
                            or missing_locally
                        ):
                            self._apply_conversation(row)
                            applied += 1
                    for row in msgs:
                        mid = str(row.get("mid") or "")
                        cid = str(row.get("cid") or "")
                        th = self.thread_of(cid) if cid else None
                        missing_locally = bool(mid) and (
                            th is None or not any(ev.get("mid") == mid for ev in th.timeline.events)
                        )
                        if (
                            str(row.get("device") or "") != self.svc.hub.device_id
                            or row.get("deleted")
                            or missing_locally
                        ):
                            self._apply_message(row)
                            applied += 1
                finally:
                    self._applying = False
                new_cursor = int(out.get("cursor") or since)
                since = max(since, new_cursor)
                self.state["local_cursor"] = since
                self._save()
                if not out.get("more"):
                    break
        if applied and getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
        return applied

    def peer_sync_soon(self, delay: float = 0.2) -> None:
        if not self.peer_active or self._stopped:
            return
        if self._peer_task is not None and not self._peer_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _run() -> None:
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self.sync_peers()
            except Exception as exc:  # noqa: BLE001
                logger.debug("peer sync pass: {}", exc)

        self._peer_task = loop.create_task(_run(), name="sync-peers")

    def _refresh_peer_urls(self) -> bool:
        """A phone's or laptop's Wi-Fi address changes when moving networks; its beacon or
        active scan keeps the same device id. Update ``pairs.json`` while preserving the
        vault key reference so a paired device on a new Wi-Fi IP reconnects automatically."""
        nearby = self.discovery.list_nearby()
        if not nearby:
            return False
        by_id = {str(n.get("id") or ""): n for n in nearby if n.get("id")}
        by_name = {
            str(n.get("name") or "").strip().lower(): n
            for n in nearby
            if n.get("name") and str(n.get("name")).strip().lower() != "localhost"
        }
        from nanomuse.sync.peer import save_pairs

        stored = load_pairs(self.svc.data_dir)
        changed = False
        for item in stored:
            pid = str(item.get("id") or "")
            pname = str(item.get("name") or "").strip().lower()
            found = by_id.get(pid) or (by_name.get(pname) if pname else None)
            if not found:
                continue
            urls = found.get("urls") or []
            new = str((urls[0] if urls else found.get("url")) or "").strip().rstrip("/")
            old = str(item.get("url") or "").strip().rstrip("/")
            if new and new != old:
                item["url"] = new
                changed = True
            if not pid and found.get("id"):
                item["id"] = str(found["id"])
                changed = True
            if found.get("kind") in ("phone", "computer", "tablet", "watch") and item.get("kind") != found.get("kind"):
                item["kind"] = str(found["kind"])
                changed = True
        if changed:
            save_pairs(self.svc.data_dir, stored)
            self.reload_peers()
        else:
            for item in self.peers:
                pid = str(item.get("id") or "")
                pname = str(item.get("name") or "").strip().lower()
                found = by_id.get(pid) or (by_name.get(pname) if pname else None)
                if not found:
                    continue
                urls = found.get("urls") or []
                new = str((urls[0] if urls else found.get("url")) or "").strip().rstrip("/")
                old = str(item.get("url") or "").strip().rstrip("/")
                if new and new != old:
                    item["url"] = new
                    changed = True
        return changed

    def note_inbound_peer_contact(
        self,
        device_id: str,
        sender_ip: str,
        port: int = 8787,
        urls: list[str] | None = None,
        name: str = "",
        kind: str = "",
    ) -> bool:
        """Record a peer's live IP when it contacts us over HTTP, healing stale IPs on any Wi-Fi."""
        did = str(device_id or "").strip()
        ip = str(sender_ip or "").strip()
        if not did or did == self.svc.hub.device_id:
            return False
        p = int(port or 8787)
        cand_urls = [u.strip().rstrip("/") for u in (urls or []) if u and u.strip().startswith(("http://", "https://"))]
        if ip and not ip.startswith("127.") and ip != "testclient":
            direct = f"http://{ip}:{p}"
            if direct not in cand_urls:
                cand_urls.insert(0, direct)
        if not cand_urls:
            return False
        self.discovery.ingest_beacon(
            {
                "type": "nanomuse_peer",
                "id": did,
                "name": name or ip or did,
                "kind": kind or "phone",
                "port": p,
                "urls": cand_urls,
                "pair_ready": False,
            },
            sender_ip=ip if not ip.startswith("127.") and ip != "testclient" else "",
        )
        return self._refresh_peer_urls()

    async def sync_peers(self) -> dict[str, Any]:
        """Bidirectionally pull, merge, and push with every paired device in pairs.json."""
        self._refresh_peer_urls()
        self.reload_peers()
        self.mirror_local_now()
        if not self.enabled or not self.peers:
            return {"applied": 0, "pushed": 0, "peers": self.peers_view()}
        total_applied = 0
        total_pushed = 0
        async with self._peer_lock:
            for peer in list(self.peers):
                try:
                    a, p = await self._sync_one_peer(peer)
                    total_applied += a
                    total_pushed += p
                except Exception as exc:
                    pkey = self._peer_key_of(peer)
                    prev = self._peer_status.get(pkey, {})
                    self._peer_status[pkey] = {
                        "online": False,
                        "last_sync_at": prev.get("last_sync_at"),
                        "last_error": str(exc)[:120],
                    }
        if total_applied:
            self.last_pull_at = _now_iso()
        if total_pushed:
            self.last_push_at = _now_iso()
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
        return {"applied": total_applied, "pushed": total_pushed, "peers": self.peers_view()}

    async def _refresh_peer_identity(self, peer: dict[str, Any]) -> None:
        """Ask the peer's /v1/sync/hello for its real name, kind, device_id, and latest
        Personal Cloud / Tailscale / Tunnel / LAN URLs so stale names, IPs, or away-from-home
        addresses heal automatically across any Wi-Fi or cellular network."""
        import httpx

        base = str(peer.get("url") or "").strip().rstrip("/")
        candidates: list[str] = []
        for u in [base, *(peer.get("urls") or [])]:
            cu = str(u or "").strip().rstrip("/")
            if cu.startswith(("http://", "https://")) and cu not in candidates:
                candidates.append(cu)
        if not candidates:
            return
        my_port = int(getattr(self.svc.settings.server, "port", 8787) or 8787)
        my_kind = self._local_kind()
        my_urls = self._my_reachable_urls()
        headers = {
            "X-Nanomuse-Device-Id": self.svc.hub.device_id,
            "X-Nanomuse-Device-Name": self.svc.hub.device_name,
            "X-Nanomuse-Device-Kind": my_kind,
            "X-Nanomuse-Device-Port": str(my_port),
            "X-Nanomuse-Device-Urls": ",".join(my_urls[:6]),
        }
        info: dict[str, Any] | None = None
        working_url = ""
        async with httpx.AsyncClient(timeout=3.0) as hc:
            for cand in candidates:
                try:
                    r = await hc.get(cand + "/v1/sync/hello", headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        if isinstance(data, dict):
                            info = data
                            working_url = cand
                            break
                except Exception:
                    continue
        if info is None:
            # Peer unreachable at all stored URLs: scan current Wi-Fi / hotspot subnet
            await self.discovery.active_wifi_scan(port=my_port)
            if self._refresh_peer_urls():
                pid = str(peer.get("id") or "")
                for updated_peer in self.peers:
                    if pid and updated_peer.get("id") == pid and updated_peer.get("url"):
                        peer["url"] = updated_peer["url"]
                        base = str(peer["url"]).rstrip("/")
                        break
                try:
                    async with httpx.AsyncClient(timeout=3.0) as hc:
                        r = await hc.get(base + "/v1/sync/hello", headers=headers)
                        if r.status_code == 200:
                            data = r.json()
                            if isinstance(data, dict):
                                info = data
                                working_url = base
                except Exception:
                    info = None
        if not isinstance(info, dict):
            return
        remote_name = str(info.get("name") or "").strip()
        remote_kind = str(info.get("kind") or "").strip().lower()
        remote_id = str(info.get("id") or "").strip()
        remote_urls = [
            str(u).strip().rstrip("/")
            for u in (info.get("urls") or [])
            if isinstance(u, str) and str(u).strip().startswith(("http://", "https://"))
        ]
        changed = False
        if working_url and working_url != base:
            peer["url"] = working_url
            base = working_url
            changed = True
        if remote_name and remote_name.lower() != "localhost" and peer.get("name") != remote_name:
            peer["name"] = remote_name[:60]
            changed = True
        if remote_kind in ("phone", "computer", "tablet", "watch") and peer.get("kind") != remote_kind:
            peer["kind"] = remote_kind
            changed = True
        if remote_id and remote_id != self.svc.hub.device_id and peer.get("id") != remote_id:
            peer["id"] = remote_id
            changed = True
        merged_urls: list[str] = []
        for u in [working_url or base, *remote_urls, *(peer.get("urls") or [])]:
            cu = str(u or "").strip().rstrip("/")
            if cu.startswith(("http://", "https://")) and cu not in merged_urls:
                merged_urls.append(cu)
        merged_urls = merged_urls[:8]
        if merged_urls and merged_urls != (peer.get("urls") or []):
            peer["urls"] = merged_urls
            changed = True
        if changed:
            from nanomuse.sync.peer import save_pairs

            stored = load_pairs(self.svc.data_dir)
            pid = str(peer.get("id") or "")
            for item in stored:
                if (pid and item.get("id") == pid) or item.get("url", "").rstrip("/") in candidates:
                    if peer.get("url"):
                        item["url"] = peer["url"]
                    if peer.get("urls"):
                        item["urls"] = list(peer["urls"])
                    if peer.get("name"):
                        item["name"] = peer["name"]
                    if peer.get("kind"):
                        item["kind"] = peer["kind"]
                    if peer.get("id"):
                        item["id"] = peer["id"]
            save_pairs(self.svc.data_dir, stored)

    async def _sync_one_peer(self, peer: dict[str, str]) -> tuple[int, int]:
        await self._refresh_peer_identity(peer)
        pkey = self._peer_key_of(peer)
        peer_cursors = self.state.setdefault("peer_cursors", {})
        cur = peer_cursors.setdefault(pkey, {"in": 0, "out": 0})
        in_seq = int(cur.get("in") or 0)
        out_seq = int(cur.get("out") or 0)
        client = SyncClient(CloudClient(peer["url"]))
        client.cloud.api_key = peer.get("token") or ""
        applied = 0
        pushed = 0
        peer_name = _clean_peer_name(
            str(peer.get("name") or ""),
            str(peer.get("url") or ""),
            _infer_peer_kind(peer),
        )
        # Ensure peer's sync store is turned on
        try:
            await client.set_enabled(True)
        except Exception:
            pass
        # 1. Pull missed changes from peer in seq order. The first pass of a device that
        # has never synced (or has just been re-paired) asks for the tail first, so the
        # newest chats and messages land before the older history: a list that only ever
        # grew from the oldest entry was never readable. A tail pass returns cursor 0 for
        # the next round, which then walks the whole history.
        first_round = True
        for _round in range(MAX_ROUNDS):
            tail = TAIL if (first_round and in_seq == 0 and not cur.get("tail_done")) else 0
            first_round = False
            out = await client.changes(in_seq, PAGE, scope=self.scope, tail=tail)
            # a different store behind the same peer (paired again, or the other app now
            # uses another data folder) has a new sequence space: a saved cursor that
            # points into the old one would silently skip everything
            store = str(out.get("store") or "")
            if store:
                known = self.state.setdefault("peer_stores", {})
                previous = known.get(pkey)
                known[pkey] = store
                if previous and previous != store:
                    in_seq = 0
                    out_seq = 0
                    cur["in"] = 0
                    cur["out"] = 0
                    cur["tail_done"] = False
                    self._save()
            convs = [c for c in out.get("conversations") or [] if isinstance(c, dict)]
            msgs = [m for m in out.get("messages") or [] if isinstance(m, dict)]
            convs.sort(key=lambda c: int(c.get("seq") or 0))
            msgs.sort(key=lambda m: int(m.get("seq") or 0))
            self._applying = True
            try:
                for row in convs:
                    self._apply_conversation(row)
                    applied += 1
                for row in msgs:
                    self._apply_message(row)
                    applied += 1
            finally:
                self._applying = False
            self._mirror_pulled_into_local_store(convs, msgs)
            if tail:
                cur["tail_done"] = True
                in_seq = 0
            else:
                in_seq = max(in_seq, int(out.get("cursor") or 0))
            cur["in"] = in_seq
            self._save()
            if getattr(self.svc, "bus", None):
                self.svc.bus.publish(
                    {
                        "kind": "sync_progress",
                        "peer": peer_name,
                        "applied": applied,
                        "done": False,
                    }
                )
            if not out.get("more"):
                if tail:
                    continue
                break
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish(
                {"kind": "sync_progress", "peer": peer_name, "applied": applied, "done": True}
            )

        # 2. Push local deltas from local_store to peer (including retries on main_exists)
        self.mirror_local_now()
        device_id = self.svc.hub.device_id
        for _ in range(MAX_ROUNDS):
            delta = self.local_store.changes(out_seq, BATCH, scope=self.scope)
            convs_all = [c for c in delta.get("conversations") or [] if isinstance(c, dict)]
            msgs_all = [m for m in delta.get("messages") or [] if isinstance(m, dict)]
            # Only push items above out_seq (local_store.changes already returns seq > out_seq for msgs;
            # conversations may also include parent rows when messages reference them)
            batch_convs = [
                {
                    "cid": c["cid"],
                    "kind": c["kind"],
                    "title": c.get("title") or "",
                    "device": c.get("device") or device_id,
                    "created_at": c.get("created_at") or 0.0,
                    "updated_at": c.get("updated_at") or 0.0,
                    "deleted": bool(c.get("deleted")),
                }
                for c in convs_all
            ]
            batch_msgs = [
                {
                    "mid": m["mid"],
                    "cid": m["cid"],
                    "role": m["role"],
                    "text": m.get("text") or "",
                    "device": m.get("device") or device_id,
                    "created_at": m.get("created_at") or 0.0,
                    "attachments": m.get("attachments") or [],
                    "deleted": bool(m.get("deleted")),
                }
                for m in msgs_all
            ]
            if not batch_convs and not batch_msgs:
                break
            res = await client.push(device_id, batch_convs, batch_msgs)
            rejected = [r for r in res.get("rejected") or [] if isinstance(r, dict)]
            redirect = {
                str(r["cid"]): str(r["cid_main"])
                for r in rejected
                if r.get("reason") == "main_exists" and r.get("cid") and r.get("cid_main")
            }
            if redirect:
                for old_c, new_c in redirect.items():
                    self._adopt_main_cid(old_c, new_c)
                out_seq = 0
                cur["out"] = 0
                self._save()
                continue
            pushed += int(res.get("accepted") or len(batch_msgs))
            max_seq = max(
                [out_seq]
                + [int(c.get("seq") or 0) for c in convs_all]
                + [int(m.get("seq") or 0) for m in msgs_all]
            )
            out_seq = max_seq
            cur["out"] = out_seq
            self._save()
            if not delta.get("more"):
                break
        # Mark pushed local events synced
        for thread in list(self.svc.threads.values()):
            dirty = False
            for ev in thread.timeline.events:
                if ev.get("mid") and not ev.get("synced") and not ev.get("via_device"):
                    ev["synced"] = True
                    dirty = True
            if dirty:
                thread.timeline.save()

        # 3. Sync Muse data (profile, avatar/frames, memories, goals, reminders, apps, portable settings)
        try:
            await self._sync_muse_data_with_peer(peer)
        except Exception as exc:  # noqa: BLE001
            logger.debug("peer muse-data sync skipped/failed for {}: {}", peer_name, exc)

        now_str = _now_iso()
        cur["last_sync_at"] = now_str
        self._peer_status[pkey] = {
            "online": True,
            "last_sync_at": now_str,
            "last_error": "",
        }
        self._save()
        return applied, pushed

    # ------------------------------------------------------------------ Muse data & avatar/frames sync
    def list_avatar_files(self, face: str | None = None) -> list[dict[str, Any]]:
        """List files (stills, animated clips, and frames) for the active avatar face."""
        target_face = str(face if face is not None else getattr(self.svc.profile, "avatar", "") or "").strip()
        if not target_face or target_face in ("none", "dragon", "emoji"):
            return []
        face_dir = self.svc.workspace() / "avatar" / target_face
        if not face_dir.is_dir():
            return []
        allowed_ext = (".webp", ".png", ".jpg", ".jpeg", ".mp4", ".webm", ".json")
        rank = {name: i for i, name in enumerate(_CANONICAL_AVATAR_FILES)}
        items: list[tuple[int, str, int, float]] = []
        try:
            for f in face_dir.iterdir():
                if f.is_file() and f.suffix.lower() in allowed_ext:
                    st = f.stat()
                    items.append((rank.get(f.name, 999), f.name, int(st.st_size), float(st.st_mtime)))
            frames_dir = face_dir / "frames"
            if frames_dir.is_dir():
                for f in sorted(frames_dir.iterdir()):
                    if f.is_file() and f.suffix.lower() in allowed_ext:
                        st = f.stat()
                        items.append((500, f"frames/{f.name}", int(st.st_size), float(st.st_mtime)))
        except OSError:
            pass
        items.sort(key=lambda t: (t[0], t[1]))
        return [
            {"face": target_face, "rel": rel, "size": size, "mtime": round(mtime, 2)}
            for _, rel, size, mtime in items
        ]

    def export_muse_data(self) -> dict[str, Any]:
        """Export portable Muse state (profile, avatar manifest, memories, goals, reminders,
        USER.md, UI prefs, portable settings, and small HTML apps) for peer sync."""
        memories: list[dict[str, Any]] = []
        goals: list[dict[str, Any]] = []
        goal_steps: list[dict[str, Any]] = []
        reminders: list[dict[str, Any]] = []

        # Memories
        mem_db = self.svc.data_dir / "memory.db"
        if mem_db.is_file():
            try:
                conn = sqlite3.connect(str(mem_db))
                conn.row_factory = sqlite3.Row
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
                sel = ["id", "content", "category", "created_at", "source"]
                if "updated_at" in cols:
                    sel.append("updated_at")
                rows = conn.execute(f"SELECT {', '.join(sel)} FROM memories").fetchall()
                memories = [dict(r) for r in rows]
                conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("export_muse_data memories: {}", exc)

        # Goals + steps
        goals_db = self.svc.data_dir / "goals.db"
        if goals_db.is_file():
            try:
                conn = sqlite3.connect(str(goals_db))
                conn.row_factory = sqlite3.Row
                goals = [dict(r) for r in conn.execute("SELECT * FROM goals").fetchall()]
                goal_steps = [dict(r) for r in conn.execute("SELECT * FROM steps").fetchall()]
                conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("export_muse_data goals: {}", exc)

        # Reminders
        rem_db = self.svc.data_dir / "reminders.db"
        if rem_db.is_file():
            try:
                conn = sqlite3.connect(str(rem_db))
                conn.row_factory = sqlite3.Row
                reminders = [dict(r) for r in conn.execute("SELECT * FROM reminders").fetchall()]
                conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("export_muse_data reminders: {}", exc)

        # Small standalone HTML apps in workspace/ or workspace/apps/
        workspace_apps: list[dict[str, Any]] = []
        try:
            ws = self.svc.workspace()
            app_candidates: list[tuple[str, Any]] = []
            for f in sorted(ws.glob("*.html")):
                app_candidates.append((f.name, f))
            apps_dir = ws / "apps"
            if apps_dir.is_dir():
                for f in sorted(apps_dir.glob("*.html")):
                    app_candidates.append((f"apps/{f.name}", f))
                for sub in sorted(apps_dir.iterdir()):
                    idx = sub / "index.html"
                    if sub.is_dir() and idx.is_file():
                        app_candidates.append((f"apps/{sub.name}/index.html", idx))
            total_app_bytes = 0
            for rel, fpath in app_candidates[:30]:
                sz = fpath.stat().st_size
                if sz <= 512 * 1024 and total_app_bytes + sz <= 4 * 1024 * 1024:
                    workspace_apps.append(
                        {
                            "rel": rel,
                            "mtime": round(float(fpath.stat().st_mtime), 2),
                            "content": fpath.read_text(encoding="utf-8", errors="replace"),
                        }
                    )
                    total_app_bytes += sz
        except Exception as exc:  # noqa: BLE001
            logger.debug("export_muse_data apps: {}", exc)

        profile_text = ""
        profile_path = self.svc.data_dir / "USER.md"
        if profile_path.exists():
            try:
                profile_text = profile_path.read_text(encoding="utf-8")
            except Exception:
                pass

        portable_settings = {
            k: v
            for k, v in dict(self.svc.connections.data).items()
            if k not in DEVICE_LOCAL_SETTINGS_KEYS
        }

        return {
            "device_id": self.svc.hub.device_id,
            "device_name": self.svc.hub.device_name,
            "device_kind": self._local_kind(),
            "reachable_urls": self._my_reachable_urls(),
            "profile": self.svc.profile.to_dict(),
            "avatar_manifest": self.list_avatar_files(),
            "memories": memories,
            "goals": goals,
            "goal_steps": goal_steps,
            "reminders": reminders,
            "user_profile": profile_text,
            "ui_preferences": self._load_ui_prefs(),
            "app_settings": portable_settings,
            "workspace_apps": workspace_apps,
        }

    def _apply_peer_sync_info(self, payload: dict[str, Any]) -> bool:
        """Auto-heal a paired peer's Personal Cloud / Tunnel / Tailscale / LAN URLs and
        return token received over an already-authenticated sync exchange."""
        info = payload.get("peer_sync_info")
        if not isinstance(info, dict):
            # Still pick up reachable_urls if provided directly on muse-data
            did_top = str(payload.get("device_id") or "").strip()
            urls_top = payload.get("reachable_urls")
            if not did_top or not isinstance(urls_top, list):
                return False
            info = {
                "device_id": did_top,
                "name": str(payload.get("device_name") or ""),
                "kind": str(payload.get("device_kind") or ""),
                "urls": urls_top,
                "return_token": "",
            }
        remote_id = str(info.get("device_id") or "").strip()
        if not remote_id or remote_id == self.svc.hub.device_id:
            return False
        raw_urls = [
            str(u).strip().rstrip("/")
            for u in (info.get("urls") or [])
            if isinstance(u, str) and str(u).strip().startswith(("http://", "https://"))
        ]
        return_tok = str(info.get("return_token") or "").strip()
        remote_name = str(info.get("name") or "").strip()
        remote_kind = str(info.get("kind") or "").strip().lower()
        from nanomuse.sync.peer import _pair_slot, save_pairs
        from nanomuse.vault.vault import CredentialVault

        stored = load_pairs(self.svc.data_dir)
        matched = None
        for item in stored:
            if item.get("id") == remote_id:
                matched = item
                break
        if matched is None and remote_name and remote_name.lower() != "localhost":
            for item in stored:
                if str(item.get("name") or "").strip().lower() == remote_name.lower():
                    matched = item
                    break
        if matched is None and len(stored) == 1 and remote_kind and stored[0].get("kind") == remote_kind:
            matched = stored[0]
        changed = False
        if matched is not None:
            if matched.get("id") != remote_id:
                matched["id"] = remote_id
                changed = True
            if remote_name and remote_name.lower() != "localhost" and matched.get("name") != remote_name:
                matched["name"] = remote_name[:60]
                changed = True
            if remote_kind in ("phone", "computer", "tablet", "watch") and matched.get("kind") != remote_kind:
                matched["kind"] = remote_kind
                changed = True
            merged_urls: list[str] = []
            for u in [*raw_urls, matched.get("url"), *(matched.get("urls") or [])]:
                cu = str(u or "").strip().rstrip("/")
                if cu.startswith(("http://", "https://")) and cu not in merged_urls:
                    merged_urls.append(cu)
            merged_urls = merged_urls[:8]
            if merged_urls and merged_urls != (matched.get("urls") or []):
                matched["urls"] = merged_urls
                changed = True
            if return_tok and return_tok.startswith("nmpeer_"):
                # Auto-heal stored peer token so 401 "invalid or missing token" never sticks!
                key_ref = str(matched.get("key") or "")
                slot = ""
                if key_ref.startswith("{{vault:") and key_ref.endswith("}}"):
                    slot = key_ref[8:-2]
                if not slot:
                    slot = _pair_slot(remote_id or str(matched.get("name") or "peer"))
                    matched["key"] = f"{{{{vault:{slot}}}}}"
                    changed = True
                try:
                    vault = CredentialVault(self.svc.data_dir / "vault.enc", self.svc.data_dir / "vault.key")
                    if str(vault.get(slot) or "").strip() != return_tok:
                        vault.set(slot, return_tok)
                        changed = True
                except Exception:
                    pass
            if changed:
                save_pairs(self.svc.data_dir, stored)
                self.reload_peers()
                # Clear stale auth error if we just healed the token
                if return_tok:
                    pkey = self._peer_key_of(matched)
                    prev = self._peer_status.get(pkey)
                    if prev and "token" in str(prev.get("last_error") or "").lower():
                        prev["last_error"] = ""
        elif raw_urls and return_tok and return_tok.startswith("nmpeer_"):
            try:
                add_pair(
                    self.svc.data_dir,
                    raw_urls[0],
                    return_tok,
                    name=remote_name,
                    device_id=remote_id,
                    kind=remote_kind,
                    urls=raw_urls,
                )
                self.reload_peers()
                changed = True
            except ValueError:
                pass
        return changed

    def apply_muse_data(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Idempotently merge Muse data (profile, memories, goals, reminders, apps, USER.md)
        from a paired peer without overwriting device-local settings."""
        if not isinstance(payload, dict):
            return {"applied": False}
        counts = {"memories": 0, "goals": 0, "reminders": 0, "apps": 0, "profile": False}
        try:
            self._apply_peer_sync_info(payload)
        except Exception as exc:  # noqa: BLE001
            logger.debug("apply_muse_data peer_sync_info: {}", exc)
        overrides = dict(
            self.state.get("device_overrides")
            or {
                "enabled": False,
                "keep_local_appearance": False,
                "keep_local_sentinel": False,
                "keep_local_notifications": False,
                "default_model": "",
            }
        )
        ov_on = bool(overrides.get("enabled", False))
        keep_appearance = ov_on and bool(overrides.get("keep_local_appearance", False))
        keep_sentinel = ov_on and bool(overrides.get("keep_local_sentinel", False))
        keep_notifications = ov_on and bool(overrides.get("keep_local_notifications", False))

        # 1. Profile — sync newer profile updates and prefer custom non-default identity/avatar
        incoming_prof = payload.get("profile")
        if isinstance(incoming_prof, dict) and incoming_prof:
            cur_prof = self.svc.profile.to_dict()
            changed = False
            defaults = {"name": ("MiniMuse", "nanoMuse", ""), "avatar": ("dragon", "none", "")}
            in_updated = float(incoming_prof.get("updated_at") or 0.0)
            cur_updated = float(cur_prof.get("updated_at") or 0.0)
            incoming_is_newer = in_updated > cur_updated and in_updated > 0.0
            appearance_keys = {
                "color",
                "emoji",
                "avatar",
                "avatar_bg_mode",
                "avatar_bg_color",
                "avatar_bg_image",
                "avatar_border_mode",
                "avatar_border_color",
                "avatar_border_glow",
                "avatar_animated",
                "avatar_situation_clips",
            }
            for k, v in incoming_prof.items():
                if k not in cur_prof:
                    continue
                if keep_appearance and k in appearance_keys:
                    continue
                if keep_notifications and k in ("quiet_hours", "proactivity", "goal_interval_minutes"):
                    continue
                if incoming_is_newer and v is not None and cur_prof.get(k) != v:
                    if k in defaults and str(v).strip() in defaults[k] and str(cur_prof.get(k) or "").strip() not in defaults[k]:
                        continue
                    cur_prof[k] = v
                    changed = True
                    continue
                if isinstance(v, str):
                    vs = v.strip()
                    if not vs:
                        continue
                    cur_val = str(cur_prof.get(k) or "").strip()
                    # Prefer incoming if local is empty/default or incoming is richer/custom
                    if k in defaults:
                        if cur_val in defaults[k] and vs not in defaults[k]:
                            cur_prof[k] = vs
                            changed = True
                        elif vs not in defaults[k] and k == "avatar" and cur_val != vs:
                            in_manifest = payload.get("avatar_manifest") or []
                            if any(
                                isinstance(m, dict) and m.get("face") == vs and str(m.get("rel", "")).endswith(".mp4")
                                for m in in_manifest
                            ):
                                cur_prof[k] = vs
                                changed = True
                    elif not cur_val or (k == "style" and len(vs) > len(cur_val)):
                        cur_prof[k] = vs
                        changed = True
                elif isinstance(v, (bool, int, dict)) and not cur_prof.get(k):
                    cur_prof[k] = v
                    changed = True
            if changed:
                try:
                    self.svc.update_profile(cur_prof)
                    counts["profile"] = True
                except Exception as exc:  # noqa: BLE001
                    logger.debug("apply_muse_data profile: {}", exc)

        # 2. Memories (idempotent upsert by id + normalized content dedup)
        incoming_mems = payload.get("memories")
        if isinstance(incoming_mems, list) and incoming_mems:
            mem_store = getattr(self.svc.app, "memory", None)
            mem_db = self.svc.data_dir / "memory.db"
            if mem_store is not None or mem_db.is_file():
                try:
                    conn = sqlite3.connect(str(mem_db))
                    conn.row_factory = sqlite3.Row
                    cols = {r["name"] for r in conn.execute("PRAGMA table_info(memories)")}
                    existing_by_id = {
                        str(r["id"]): str(r["updated_at"] if "updated_at" in cols else r["created_at"])
                        for r in conn.execute(
                            f"SELECT id, created_at{', updated_at' if 'updated_at' in cols else ''} FROM memories"
                        ).fetchall()
                    }
                    existing_contents = {
                        str(r["content"]).strip().lower()
                        for r in conn.execute("SELECT content FROM memories").fetchall()
                    }
                    for m in incoming_mems:
                        if not isinstance(m, dict):
                            continue
                        mid = str(m.get("id") or "").strip()
                        content = str(m.get("content") or "").strip()
                        if not mid or not content:
                            continue
                        category = str(m.get("category") or "general")
                        created_at = str(m.get("created_at") or _now_iso())
                        source = str(m.get("source") or "sync")
                        updated_at = str(m.get("updated_at") or created_at)
                        if mid in existing_by_id:
                            if updated_at > existing_by_id[mid]:
                                if "updated_at" in cols:
                                    conn.execute(
                                        "UPDATE memories SET content=?, category=?, updated_at=? WHERE id=?",
                                        (content, category, updated_at, mid),
                                    )
                                else:
                                    conn.execute(
                                        "UPDATE memories SET content=?, category=? WHERE id=?",
                                        (content, category, mid),
                                    )
                                counts["memories"] += 1
                        elif content.lower() not in existing_contents:
                            if "updated_at" in cols:
                                conn.execute(
                                    "INSERT OR IGNORE INTO memories (id, content, category, created_at, source, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                                    (mid, content, category, created_at, source, updated_at),
                                )
                            else:
                                conn.execute(
                                    "INSERT OR IGNORE INTO memories (id, content, category, created_at, source) VALUES (?, ?, ?, ?, ?)",
                                    (mid, content, category, created_at, source),
                                )
                            existing_contents.add(content.lower())
                            counts["memories"] += 1
                    conn.commit()
                    conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("apply_muse_data memories: {}", exc)

        # 3. Goals + steps (idempotent upsert by id, keeping newer updated_at)
        incoming_goals = payload.get("goals")
        incoming_steps = payload.get("goal_steps")
        if isinstance(incoming_goals, list) and incoming_goals:
            goals_db = self.svc.data_dir / "goals.db"
            if goals_db.is_file():
                try:
                    conn = sqlite3.connect(str(goals_db))
                    conn.row_factory = sqlite3.Row
                    g_cols = [r["name"] for r in conn.execute("PRAGMA table_info(goals)")]
                    g_col_set = set(g_cols)
                    existing_goals = {
                        str(r["id"]): str(r["updated_at"] or "")
                        for r in conn.execute("SELECT id, updated_at FROM goals").fetchall()
                    }
                    for g in incoming_goals:
                        if not isinstance(g, dict):
                            continue
                        gid = str(g.get("id") or "").strip()
                        title = str(g.get("title") or "").strip()
                        if not gid or not title:
                            continue
                        in_upd = str(g.get("updated_at") or g.get("created_at") or "")
                        if gid not in existing_goals or in_upd > existing_goals[gid]:
                            use_cols = [c for c in g_cols if c in g]
                            placeholders = ", ".join("?" for _ in use_cols)
                            conn.execute(
                                f"INSERT OR REPLACE INTO goals ({', '.join(use_cols)}) VALUES ({placeholders})",
                                [g[c] for c in use_cols],
                            )
                            counts["goals"] += 1
                    if isinstance(incoming_steps, list) and incoming_steps:
                        s_cols = [r["name"] for r in conn.execute("PRAGMA table_info(steps)")]
                        existing_steps = {
                            str(r["id"]): str(r["updated_at"] or "")
                            for r in conn.execute("SELECT id, updated_at FROM steps").fetchall()
                        }
                        for st in incoming_steps:
                            if not isinstance(st, dict):
                                continue
                            sid = str(st.get("id") or "").strip()
                            if not sid:
                                continue
                            in_upd = str(st.get("updated_at") or "")
                            if sid not in existing_steps or in_upd > existing_steps[sid]:
                                use_cols = [c for c in s_cols if c in st]
                                placeholders = ", ".join("?" for _ in use_cols)
                                conn.execute(
                                    f"INSERT OR REPLACE INTO steps ({', '.join(use_cols)}) VALUES ({placeholders})",
                                    [st[c] for c in use_cols],
                                )
                    conn.commit()
                    conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("apply_muse_data goals: {}", exc)

        # 4. Reminders (idempotent upsert by id)
        incoming_rems = payload.get("reminders")
        if isinstance(incoming_rems, list) and incoming_rems:
            rem_db = self.svc.data_dir / "reminders.db"
            if rem_db.is_file():
                try:
                    conn = sqlite3.connect(str(rem_db))
                    conn.row_factory = sqlite3.Row
                    r_cols = [r["name"] for r in conn.execute("PRAGMA table_info(reminders)")]
                    existing_rems = {
                        str(r["id"]): (str(r["last_fired_at"] or ""), str(r["status"] or ""))
                        for r in conn.execute("SELECT id, last_fired_at, status FROM reminders").fetchall()
                    }
                    for rm in incoming_rems:
                        if not isinstance(rm, dict):
                            continue
                        rid = str(rm.get("id") or "").strip()
                        if not rid or not str(rm.get("text") or "").strip():
                            continue
                        in_fired = str(rm.get("last_fired_at") or "")
                        if rid not in existing_rems or in_fired > existing_rems[rid][0]:
                            use_cols = [c for c in r_cols if c in rm]
                            placeholders = ", ".join("?" for _ in use_cols)
                            conn.execute(
                                f"INSERT OR REPLACE INTO reminders ({', '.join(use_cols)}) VALUES ({placeholders})",
                                [rm[c] for c in use_cols],
                            )
                            counts["reminders"] += 1
                    conn.commit()
                    conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("apply_muse_data reminders: {}", exc)

        # 5. User profile (USER.md) — keep richer copy
        user_prof = payload.get("user_profile")
        if isinstance(user_prof, str) and user_prof.strip():
            profile_path = self.svc.data_dir / "USER.md"
            cur_txt = ""
            if profile_path.exists():
                try:
                    cur_txt = profile_path.read_text(encoding="utf-8")
                except Exception:
                    pass
            if len(user_prof.strip()) > len(cur_txt.strip()):
                try:
                    profile_path.write_text(user_prof, encoding="utf-8")
                except Exception:
                    pass

        # 6. Workspace HTML apps (keep newer mtime)
        incoming_apps = payload.get("workspace_apps")
        if isinstance(incoming_apps, list) and incoming_apps:
            ws = self.svc.workspace()
            for app_item in incoming_apps:
                if not isinstance(app_item, dict):
                    continue
                rel = str(app_item.get("rel") or "").replace("\\", "/").strip("/")
                content = app_item.get("content")
                if not rel or not rel.endswith(".html") or not isinstance(content, str):
                    continue
                parts = [p for p in rel.split("/") if p and p not in (".", "..")]
                if not parts:
                    continue
                dest = ws.joinpath(*parts)
                try:
                    in_mtime = float(app_item.get("mtime") or 0.0)
                    if not dest.exists() or in_mtime > dest.stat().st_mtime + 1.0:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_text(content, encoding="utf-8")
                        counts["apps"] += 1
                except Exception:
                    pass

        # Notify live UI if anything changed
        if any(counts.values()) and getattr(self.svc, "bus", None):
            try:
                self.svc.bus.publish({"kind": "profile", "profile": self.svc.profile.to_dict()})
                self.svc.bus.publish({"kind": "sync", "sync": self.view()})
            except Exception:
                pass
        return {"applied": True, "counts": counts}

    async def _sync_muse_data_with_peer(self, peer: dict[str, str]) -> None:
        """Bidirectionally sync Muse data (memories, goals, reminders, profile, avatar/frames,
        HTML apps) with a paired peer."""
        import hashlib
        import httpx

        base = str(peer.get("url") or "").strip().rstrip("/")
        tok = str(peer.get("token") or "").strip()
        if not base or not tok:
            return
        headers = {"Authorization": f"Bearer {tok}"}
        async with httpx.AsyncClient(timeout=15.0) as hc:
            # 1. Pull peer's Muse data
            r = await hc.get(base + "/v1/sync/muse-data", headers=headers)
            if r.status_code == 200:
                remote_data = r.json()
                if isinstance(remote_data, dict):
                    # Download missing/updated avatar stills, clips, and frames first so
                    # the profile switch renders immediately with zero broken images
                    manifest = remote_data.get("avatar_manifest") or []
                    ws_avatar = self.svc.workspace() / "avatar"
                    for item in manifest:
                        if not isinstance(item, dict):
                            continue
                        face = str(item.get("face") or "").strip()
                        rel = str(item.get("rel") or "").replace("\\", "/").strip("/")
                        expected_sz = int(item.get("size") or 0)
                        if not face or not rel or ".." in face or ".." in rel:
                            continue
                        parts = [p for p in rel.split("/") if p and p not in (".", "..")]
                        if not parts:
                            continue
                        dest = (ws_avatar / face).joinpath(*parts)
                        if dest.is_file() and expected_sz > 0 and dest.stat().st_size == expected_sz:
                            continue
                        try:
                            fr = await hc.get(
                                f"{base}/v1/sync/avatar/{face}/{rel}",
                                headers=headers,
                                timeout=20.0,
                            )
                            if fr.status_code == 200 and fr.content:
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                dest.write_bytes(fr.content)
                        except Exception:
                            pass
                    self.apply_muse_data(remote_data)

            # 2. Push our Muse data + latest Personal Cloud URLs + valid return token to the peer
            local_data = self.export_muse_data()
            peer_id = str(peer.get("id") or "").strip()
            peer_name = str(peer.get("name") or "").strip()
            my_urls = self._my_reachable_urls()
            return_tok = self._get_or_mint_return_token(peer_id, peer_name) if peer_id else ""
            local_data["peer_sync_info"] = {
                "device_id": self.svc.hub.device_id,
                "name": self.svc.hub.device_name,
                "kind": self._local_kind(),
                "urls": my_urls,
                "return_token": return_tok,
            }
            digest_src = json.dumps(
                {
                    "profile": local_data.get("profile"),
                    "urls": my_urls,
                    "ret_sig": return_tok[-8:] if return_tok else "",
                    "m": len(local_data.get("memories") or []),
                    "g": len(local_data.get("goals") or []),
                    "r": len(local_data.get("reminders") or []),
                    "a": len(local_data.get("avatar_manifest") or []),
                    "apps": [(x.get("rel"), x.get("mtime")) for x in (local_data.get("workspace_apps") or [])],
                },
                sort_keys=True,
            ).encode("utf-8")
            digest = hashlib.sha256(digest_src).hexdigest()[:16]
            pkey = self._peer_key_of(peer)
            pushed_digests = self.state.setdefault("peer_muse_digests", {})
            if pushed_digests.get(pkey) != digest:
                pr = await hc.post(base + "/v1/sync/muse-data", headers=headers, json=local_data)
                if pr.status_code == 200:
                    pushed_digests[pkey] = digest
                    self._save()

    def set_device_overrides(self, body: dict[str, Any]) -> dict[str, Any]:
        """Configure optional device-specific settings (disabled by default)."""
        cur = dict(
            self.state.get("device_overrides")
            or {
                "enabled": False,
                "keep_local_appearance": False,
                "keep_local_sentinel": False,
                "keep_local_notifications": False,
                "default_model": "",
            }
        )
        if body.get("enabled") is not None:
            cur["enabled"] = bool(body["enabled"])
        if body.get("keep_local_appearance") is not None:
            cur["keep_local_appearance"] = bool(body["keep_local_appearance"])
        if body.get("keep_local_sentinel") is not None:
            cur["keep_local_sentinel"] = bool(body["keep_local_sentinel"])
        if body.get("keep_local_notifications") is not None:
            cur["keep_local_notifications"] = bool(body["keep_local_notifications"])
        if body.get("default_model") is not None:
            cur["default_model"] = str(body["default_model"]).strip()
        self.state["device_overrides"] = cur
        self._save()
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
        return self.view()

    def export_setup_bundle(self) -> dict[str, Any]:
        """Export this device's portable setup (models, vault keys, routing, connections,
        appearance, profile, avatar, and Muse data) for a paired peer — never exporting
        device-local keys such as hub.device_id or app_lock."""
        from nanomuse.llm.smart_router import load_routing_config

        vault = self.svc.app.vault
        vault_items: dict[str, str] = {}
        try:
            for name in vault.names():
                if name.startswith("PAIR_"):
                    continue
                val = vault.get(name)
                if val:
                    vault_items[name] = val
        except Exception:
            pass
        app_data = {
            k: v
            for k, v in dict(self.svc.connections.data).items()
            if k not in DEVICE_LOCAL_SETTINGS_KEYS
        }
        profile_text = ""
        profile_path = self.svc.data_dir / "USER.md"
        if profile_path.exists():
            try:
                profile_text = profile_path.read_text(encoding="utf-8")
            except Exception:
                pass
        avatar_files: dict[str, str] = {}
        try:
            import base64

            face = str(getattr(self.svc.profile, "avatar", "") or "").strip()
            if face and face != "none":
                face_dir = self.svc.workspace() / "avatar" / face
                if face_dir.is_dir():
                    rank = {name: i for i, name in enumerate(_CANONICAL_AVATAR_FILES)}
                    candidates = [
                        f
                        for f in face_dir.iterdir()
                        if f.is_file()
                        and f.suffix.lower() in (".webp", ".png", ".jpg", ".mp4", ".webm", ".json")
                    ]
                    candidates.sort(key=lambda f: (rank.get(f.name, 999), f.name))
                    total_bytes = 0
                    for f in candidates:
                        sz = f.stat().st_size
                        if total_bytes + sz <= 25 * 1024 * 1024:
                            avatar_files[f"{face}/{f.name}"] = base64.b64encode(f.read_bytes()).decode("ascii")
                            total_bytes += sz
        except Exception:
            pass
        muse_data = self.export_muse_data()
        return {
            "app_settings": app_data,
            "profile": self.svc.profile.to_dict(),
            "avatar_files": avatar_files,
            "llm_routing": load_routing_config(self.svc.data_dir),
            "vault_entries": vault_items,
            "user_profile": profile_text,
            "ui_preferences": self._load_ui_prefs(),
            "memories": muse_data.get("memories") or [],
            "goals": muse_data.get("goals") or [],
            "goal_steps": muse_data.get("goal_steps") or [],
            "reminders": muse_data.get("reminders") or [],
            "workspace_apps": muse_data.get("workspace_apps") or [],
        }

    def _load_ui_prefs(self) -> dict[str, Any]:
        p = self.svc.data_dir / "ui-prefs.json"
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    def apply_setup_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Apply a setup bundle received from a paired device, respecting optional device_overrides
        and strictly preserving device-local identity/security keys."""
        if not isinstance(bundle, dict):
            return {"applied": False}
        from nanomuse.config import apply_app_settings
        from nanomuse.llm.smart_router import save_routing_config

        overrides = dict(
            self.state.get("device_overrides")
            or {
                "enabled": False,
                "keep_local_appearance": False,
                "keep_local_sentinel": False,
                "keep_local_notifications": False,
                "default_model": "",
            }
        )
        ov_enabled = bool(overrides.get("enabled", False))
        keep_appearance = ov_enabled and bool(overrides.get("keep_local_appearance", False))
        keep_sentinel = ov_enabled and bool(overrides.get("keep_local_sentinel", False))
        override_model = str(overrides.get("default_model") or "").strip() if ov_enabled else ""

        # 1. Vault keys
        vault_entries = bundle.get("vault_entries")
        if isinstance(vault_entries, dict):
            for k, v in vault_entries.items():
                if k and isinstance(v, str) and v and not k.startswith("PAIR_"):
                    try:
                        self.svc.app.vault.set(str(k), v)
                    except Exception:
                        pass

        # 2. LLM routing
        routing = bundle.get("llm_routing")
        if isinstance(routing, dict):
            try:
                save_routing_config(self.svc.data_dir, routing)
            except Exception:
                pass

        # 3. App settings (models, connections, onboarding) — NEVER overwrite device-local keys
        incoming = bundle.get("app_settings")
        if isinstance(incoming, dict):
            merged = dict(self.svc.connections.data)
            local_agent = dict(merged.get("agent") or {})
            local_llm = dict(merged.get("llm") or {})
            for k, v in incoming.items():
                if k in DEVICE_LOCAL_SETTINGS_KEYS:
                    continue
                if keep_appearance and k == "ui":
                    continue
                if keep_sentinel and k in ("sentinel", "permissions"):
                    continue
                merged[k] = v
            if keep_appearance and local_agent:
                # Keep device-local avatar/theme appearance if override enabled
                cur_agent = dict(merged.get("agent") or {})
                if "avatar" in local_agent:
                    cur_agent["avatar"] = local_agent["avatar"]
                merged["agent"] = cur_agent
            if override_model:
                cur_llm = dict(merged.get("llm") or {})
                cur_llm["model"] = override_model
                if local_llm.get("base_url"):
                    cur_llm["base_url"] = local_llm["base_url"]
                merged["llm"] = cur_llm
            merged["onboarded"] = True
            self.svc.connections.data = merged
            self.svc.connections._save()
            try:
                apply_app_settings(self.svc.settings, merged)
                self.svc.connections._swap_llm()
                self.svc.connections._publish()
            except Exception as exc:
                logger.debug("apply_setup_bundle settings apply: {}", exc)

        # 4. User profile
        user_prof = bundle.get("user_profile")
        if isinstance(user_prof, str) and user_prof.strip():
            profile_path = self.svc.data_dir / "USER.md"
            if not profile_path.exists() or not profile_path.read_text(encoding="utf-8").strip():
                try:
                    profile_path.write_text(user_prof, encoding="utf-8")
                except Exception:
                    pass

        # 5. Profile & custom AI avatar stills/animations
        avatar_files = bundle.get("avatar_files")
        if isinstance(avatar_files, dict) and avatar_files and not keep_appearance:
            import base64

            for rel_key, b64_str in avatar_files.items():
                if not isinstance(rel_key, str) or not isinstance(b64_str, str):
                    continue
                parts = [p for p in rel_key.replace("\\", "/").split("/") if p and p not in (".", "..")]
                if len(parts) >= 2:
                    try:
                        dest = (self.svc.workspace() / "avatar").joinpath(*parts)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(base64.b64decode(b64_str))
                    except Exception:
                        pass

        incoming_prof = bundle.get("profile")
        if isinstance(incoming_prof, dict) and incoming_prof:
            cur_prof = self.svc.profile.to_dict()
            for k, v in incoming_prof.items():
                if k in cur_prof and isinstance(v, str) and v.strip():
                    if keep_appearance and k in ("color", "emoji", "avatar"):
                        continue
                    cur_prof[k] = v.strip()
            try:
                self.svc.update_profile(cur_prof)
            except Exception as exc:
                logger.debug("apply_setup_bundle profile apply: {}", exc)

        # 6. UI preferences (unless keep_local_appearance is enabled)
        ui_prefs = bundle.get("ui_preferences")
        if isinstance(ui_prefs, dict) and ui_prefs and not keep_appearance:
            try:
                (self.svc.data_dir / "ui-prefs.json").write_text(
                    json.dumps(ui_prefs, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass

        # 7. Memories, goals, reminders, workspace HTML apps
        try:
            self.apply_muse_data(
                {
                    "memories": bundle.get("memories") or [],
                    "goals": bundle.get("goals") or [],
                    "goal_steps": bundle.get("goal_steps") or [],
                    "reminders": bundle.get("reminders") or [],
                    "workspace_apps": bundle.get("workspace_apps") or [],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("apply_setup_bundle muse data: {}", exc)

        return {
            "applied": True,
            "ui_preferences": None if keep_appearance else (ui_prefs or {}),
            "profile": self.svc.profile.to_dict(),
            "onboarded": True,
        }

    # ------------------------------------------------------------------ Easy pairing
    def issue_pair_code(self) -> dict[str, Any]:
        info = self.pairing.issue_code()
        host = str(getattr(self.svc.settings.server, "host", "127.0.0.1") or "127.0.0.1")
        urls = self._my_reachable_urls()
        did = self.svc.hub.device_id
        name = self.svc.hub.device_name
        payload: dict[str, Any] = {
            **info,
            "device_id": did,
            "name": name,
            "urls": urls,
            "pair_uri": build_pair_uri(info["code"], did, name, urls),
        }
        if not urls:
            # every address is this machine only: a phone that scans this code cannot reach it
            payload["warning"] = (
                "This app is only listening on this machine, so another device cannot reach "
                "it. Restart it with network access (server.host = 0.0.0.0) and scan again."
            )
        elif host in ("127.0.0.1", "localhost", "::1"):
            payload["warning"] = (
                "This app is listening on this machine only; the address in the code may not "
                "answer from your phone. Restart it with server.host = 0.0.0.0."
            )
        return payload

    @staticmethod
    def _reachable_peer_urls(remote_urls: list[str], client_ip: str) -> list[str]:
        """The addresses this runtime can actually dial back to reach a device that just
        claimed a pairing code.

        The claiming device lists its own addresses, and those can be wrong for us: an app
        that advertised a default port, a phone that only ran on its own loopback, a machine
        behind another interface. The one address we know reached us is the claim's own
        client address, so the same host on the port it says it listens on comes first; the
        claimed URLs follow, minus the ones that could never work from here (loopback,
        unless the claim itself came from loopback)."""
        claimed = [str(u).strip().rstrip("/") for u in remote_urls if str(u).strip()]
        usable = [u for u in claimed if u.startswith(("http://", "https://"))]
        from_loopback = client_ip in ("", "127.0.0.1", "::1", "localhost")
        if not from_loopback:
            usable = [u for u in usable if "127.0.0.1" not in u and "localhost" not in u]
        port = ""
        for url in usable:
            hostport = url.split("://", 1)[-1].split("/", 1)[0]
            if ":" in hostport and not hostport.endswith("]"):
                port = hostport.rsplit(":", 1)[1]
                break
        out: list[str] = []
        if client_ip and not from_loopback and port.isdigit():
            out.append(f"http://{client_ip}:{port}")
        for url in usable:
            if url not in out:
                out.append(url)
        return out

    def _local_kind(self) -> str:
        if os.environ.get("NANOMUSE_DEVICE", "").strip().lower() == "android":
            return "phone"
        return "phone" if getattr(self.svc.app, "device", None) is not None else "computer"

    def accept_inbound_claim(
        self,
        *,
        code: str,
        remote_id: str,
        remote_name: str,
        remote_urls: list[str],
        return_token: str = "",
        client_ip: str = "",
        remote_kind: str = "",
    ) -> tuple[bool, dict[str, Any], int]:
        ok, err = self.pairing.claim_code(code, client_ip=client_ip)
        if not ok:
            status = 429 if "many attempts" in err else 401
            return False, {"ok": False, "detail": err}, status
        clean_kind = str(remote_kind or "").strip().lower() or _infer_peer_kind(
            {"id": remote_id, "name": remote_name}
        )
        clean_name = _clean_peer_name(remote_name, client_ip or remote_id, clean_kind)
        peer_tok = self.pairing.mint_inbound_token(remote_id, clean_name)
        my_urls = self._my_reachable_urls()
        # If the caller also exposed URLs + a return token, record mutual peer automatically!
        if remote_urls and return_token:
            reachable = self._reachable_peer_urls(remote_urls, client_ip)
            for rurl in reachable:
                try:
                    add_pair(
                        self.svc.data_dir,
                        rurl,
                        return_token,
                        name=clean_name,
                        device_id=remote_id,
                        kind=clean_kind,
                        urls=reachable,
                    )
                    break
                except ValueError:
                    continue
            self.state["enabled"] = True
            if not self.state.get("side_chats_set"):
                self.state["side_chats"] = True
            self._save()
            self.reload_peers()
            self.mirror_local_now()
            self.peer_sync_soon(delay=0.2)
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
            self.svc.bus.publish({"kind": "paired", "name": clean_name or "A device"})
        return (
            True,
            {
                "ok": True,
                "device_id": self.svc.hub.device_id,
                "name": self.svc.hub.device_name,
                "kind": self._local_kind(),
                "peer_token": peer_tok,
                "urls": my_urls,
                "setup_bundle": self.export_setup_bundle(),
            },
            200,
        )

    async def pair_with_remote(
        self,
        *,
        target: str = "",
        code: str = "",
        token: str = "",
        name: str = "",
        sync_side_chats: bool = True,
    ) -> dict[str, Any]:
        """Pair with another runtime either by 6-char code (+ URL/IP/QR) or direct token."""
        import httpx

        parsed = parse_pair_payload(target)
        urls = list(parsed.get("urls") or [])
        norm_code = normalize_pair_code(code or str(parsed.get("code") or ""))
        if not urls and target:
            raise ValueError("Enter the other device's address, QR link, or pick a nearby device")
        my_urls = self._my_reachable_urls()
        if norm_code:
            return_token = self.pairing.mint_inbound_token(
                str(parsed.get("id") or ""), name or str(parsed.get("name") or "")
            )
            payload = {
                "code": norm_code,
                "device_id": self.svc.hub.device_id,
                "name": self.svc.hub.device_name,
                "kind": self._local_kind(),
                "urls": my_urls,
                "peer_token": return_token,
            }
            last_err = "no answer"
            tried = ", ".join(u.rstrip("/") for u in urls)
            async with httpx.AsyncClient(timeout=20.0) as hc:
                for base in urls:
                    try:
                        r = await hc.post(base.rstrip("/") + "/v1/sync/pair/claim", json=payload)
                        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                        if r.status_code == 200 and data.get("ok"):
                            peer_tok = str(data.get("peer_token") or "")
                            r_id = str(data.get("device_id") or parsed.get("id") or "")
                            r_kind = str(data.get("kind") or "").strip().lower() or _infer_peer_kind(
                                {"id": r_id, "name": data.get("name") or name or parsed.get("name") or ""}
                            )
                            r_name = _clean_peer_name(
                                str(data.get("name") or name or parsed.get("name") or ""),
                                base,
                                r_kind,
                            )
                            r_urls = [
                                str(u).strip().rstrip("/")
                                for u in (data.get("urls") or [])
                                if isinstance(u, str) and str(u).strip().startswith(("http://", "https://"))
                            ]
                            if not peer_tok:
                                raise ValueError("Peer did not return a pairing token")
                            add_pair(
                                self.svc.data_dir,
                                base,
                                peer_tok,
                                name=r_name,
                                device_id=r_id,
                                kind=r_kind,
                                urls=[base, *r_urls, *urls],
                            )
                            self.state["enabled"] = True
                            if sync_side_chats and not self.state.get("side_chats_set"):
                                self.state["side_chats"] = True
                            self._save()
                            bundle_res: dict[str, Any] = {}
                            if isinstance(data.get("setup_bundle"), dict):
                                bundle_res = self.apply_setup_bundle(data["setup_bundle"])
                            self.reload_peers()
                            # the first pass can move a whole history: let the screen show its
                            # progress (poll /api/sync/state) instead of holding the request open
                            self.peer_sync_soon(delay=0.05)
                            return {
                                "ok": True,
                                "peer": r_name,
                                "syncing": True,
                                "sync": self.view(),
                                "result": {"applied": 0, "pushed": 0, "peers": self.peers_view()},
                                "setup_synced": bundle_res,
                            }
                        last_err = str(data.get("detail") or f"HTTP {r.status_code}")
                        if r.status_code in (401, 429):
                            # the code itself was refused: the address was fine
                            raise ValueError(last_err)
                    except httpx.HTTPError as exc:
                        last_err = str(exc) or exc.__class__.__name__
            raise ValueError(
                f"Could not reach {tried} ({last_err}). Both devices have to be on the same "
                "Wi-Fi or Tailscale, and the other app has to be running and allowed through "
                "its firewall."
            )
        if token.strip():
            base = urls[0]
            async with httpx.AsyncClient(timeout=5.0) as hc:
                r = await hc.get(
                    base.rstrip("/") + "/v1/sync/state",
                    headers={"Authorization": f"Bearer {token.strip()}"},
                )
                if r.status_code >= 400:
                    raise ValueError(f"Peer rejected token (HTTP {r.status_code})")
            add_pair(self.svc.data_dir, base, token.strip(), name=name or str(parsed.get("name") or ""))
            self.state["enabled"] = True
            if sync_side_chats and not self.state.get("side_chats_set"):
                self.state["side_chats"] = True
            self._save()
            self.reload_peers()
            # first pass in the background: the screen follows /api/sync/state for progress
            self.peer_sync_soon(delay=0.05)
            return {
                "ok": True,
                "peer": name or base,
                "syncing": True,
                "sync": self.view(),
                "result": {"applied": 0, "pushed": 0, "peers": self.peers_view()},
            }
        raise ValueError("Provide the 6-character pairing code or the device's token")

    def unpair_peer(self, identifier: str) -> bool:
        self.reload_peers()
        match = next(
            (
                p
                for p in self.peers
                if p.get("name") == identifier or p.get("id") == identifier or p.get("url") == identifier
            ),
            None,
        )
        removed = remove_pair(self.svc.data_dir, identifier)
        if match:
            self.pairing.revoke_peer_tokens(
                device_id=str(match.get("id") or ""), name=str(match.get("name") or "")
            )
            pkey = self._peer_key_of(match)
            self.state.get("peer_cursors", {}).pop(pkey, None)
            self._peer_status.pop(pkey, None)
            self._save()
        self.reload_peers()
        if getattr(self.svc, "bus", None):
            self.svc.bus.publish({"kind": "sync", "sync": self.view()})
        return removed
