"""Device-to-device sync: one runtime serves the same sync protocol the relay speaks.

nanoMuse's conversation sync normally goes through the relay (``/v1/sync/*``).  With this
module, every runtime also *serves* those endpoints, so two runtimes can pair directly:
one enters the other's address and token, and the chats sync straight between the two
machines, no relay and no account ([phone-only.md](phone-only.md), docs/sync.md).

The store is deliberately small: conversations (an id, a kind, a title, which device
started them) and messages (an id, the role, the text cut at 16 KB, attachment names and
sizes).  Never a file, never a picture.  One change counter gives every accepted change a
``seq``; a device keeps the highest seq it has seen and asks for what came after it.  The
main chat is one conversation for the pair: the first ``main`` pushed sets its id, a
second one is refused with ``main_exists`` and the id to use instead.  Presence
(``working``) is kept in memory for ten minutes: a hint for the other device's screen,
never a table.

The engine's ``SyncClient`` already speaks this protocol, so a paired device reuses it
unchanged; the store is per runtime, under ``<data_dir>/sync-peer.db``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

LIMITS = {"messages": 20000, "text_bytes": 16384}
MAX_POST_MESSAGES = 200
DEFAULT_PAGE = 500
MAX_PAGE = 1000
MAX_TAIL = 500
TOMBSTONE_DAYS = 30
DETAIL_CAP = 160
MAX_ATTACHMENTS = 20
KINDS = ("main", "side")
ROLES = ("user", "assistant")
SCOPES = ("all", "main")
WORKING_TTL_S = 10 * 60
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{3,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sync_conversations (
    cid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at REAL
);
CREATE TABLE IF NOT EXISTS sync_messages (
    mid TEXT PRIMARY KEY,
    cid TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    device TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL,
    created_at REAL NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at REAL
);
CREATE INDEX IF NOT EXISTS sync_messages_seq ON sync_messages(seq);
CREATE INDEX IF NOT EXISTS sync_messages_cid ON sync_messages(cid);
"""


class SyncStateBody(BaseModel):
    enabled: bool


class SyncConversationIn(BaseModel):
    cid: str
    kind: str
    title: str = ""
    device: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    deleted: bool = False


class SyncMessageIn(BaseModel):
    mid: str
    cid: str
    role: str
    text: str = ""
    device: str = ""
    created_at: float = 0.0
    attachments: list[Any] = Field(default_factory=list)
    deleted: bool = False


class SyncPushBody(BaseModel):
    device: str = ""
    conversations: list[SyncConversationIn] = Field(default_factory=list)
    messages: list[SyncMessageIn] = Field(default_factory=list)


class SyncWorkingBody(BaseModel):
    cid: str
    working: bool
    device: str = ""
    state: str = ""
    detail: str = ""


def _now() -> float:
    return time.time()


def _clean_attachments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:MAX_ATTACHMENTS]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "name": str(item.get("name") or "")[:200],
                "size": int(item.get("size") or 0),
            }
        )
    return out


class SyncPeerStore:
    """The conversation store one runtime keeps for the devices paired with it."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._working: dict[str, dict[str, Any]] = {}
        with self._tx() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(sync_messages)").fetchall()}
            if "device" not in cols:
                c.execute("ALTER TABLE sync_messages ADD COLUMN device TEXT NOT NULL DEFAULT ''")

    def _tx(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _cursor(self, c: sqlite3.Connection) -> int:
        row = c.execute(
            "SELECT COALESCE(MAX(seq), 0) AS n FROM ("
            " SELECT seq FROM sync_messages UNION ALL SELECT seq FROM sync_conversations)"
        ).fetchone()
        return int(row["n"] or 0)

    def store_id(self) -> str:
        """A name for this store's sequence space. A device that pairs again - or that is
        pointed at a different data folder - gets a new id, so the other side knows its
        saved cursor belongs to a different store and reads from the beginning instead of
        waiting forever for seq numbers that will never come."""
        with self._tx() as c:
            row = c.execute("SELECT v FROM sync_meta WHERE k='store_id'").fetchone()
            if row and row["v"]:
                return str(row["v"])
            sid = uuid.uuid4().hex
            c.execute("INSERT OR REPLACE INTO sync_meta(k, v) VALUES('store_id', ?)", (sid,))
            c.commit()
            return sid

    # ------------------------------------------------------------------ state
    def enabled(self) -> bool:
        with self._tx() as c:
            row = c.execute("SELECT v FROM sync_meta WHERE k='enabled'").fetchone()
        return bool(row and row["v"] == "1")

    def state(self) -> dict[str, Any]:
        with self._tx() as c:
            convs = int(
                c.execute("SELECT COUNT(*) FROM sync_conversations WHERE deleted=0").fetchone()[0]
            )
            msgs = int(
                c.execute("SELECT COUNT(*) FROM sync_messages WHERE deleted=0").fetchone()[0]
            )
            cursor = self._cursor(c)
        self._sweep_working()
        return {
            "enabled": self.enabled(),
            "cursor": cursor,
            "store": self.store_id(),
            "counts": {"conversations": convs, "messages": msgs},
            "limits": dict(LIMITS),
            "working": [
                {
                    "cid": cid,
                    "device": w["device"],
                    "at": w["at"],
                    "state": w["state"],
                    "detail": w["detail"],
                }
                for cid, w in sorted(self._working.items())
            ],
        }

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        if not enabled:
            self.wipe()
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO sync_meta(k, v) VALUES('enabled', ?)",
                ("1" if enabled else "0",),
            )
            c.commit()
        return self.state()

    # ------------------------------------------------------------------ changes
    def changes(self, since: int, limit: int, scope: str = "all", tail: int = 0) -> dict[str, Any]:
        if scope not in SCOPES:
            raise HTTPException(400, "scope must be all or main")
        limit = max(1, min(int(limit), MAX_PAGE))
        tail = max(0, min(int(tail), MAX_TAIL))
        since = max(0, int(since))
        with self._tx() as c:
            conversations = [
                {
                    "cid": r["cid"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "device": r["device"],
                    "seq": r["seq"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "deleted": bool(r["deleted"]),
                }
                for r in c.execute(
                    "SELECT cid, kind, title, device, seq, created_at, updated_at, deleted "
                    "FROM sync_conversations WHERE seq > ? ORDER BY seq LIMIT ?",
                    (since, limit),
                )
            ]
            messages: list[dict[str, Any]] = []
            skipped = 0
            if tail:
                rows = c.execute(
                    "SELECT mid, cid, role, text, attachments, device, seq, created_at, deleted "
                    "FROM sync_messages WHERE deleted=0 ORDER BY seq DESC LIMIT ?",
                    (tail,),
                ).fetchall()
                skipped = max(
                    0,
                    int(
                        c.execute("SELECT COUNT(*) FROM sync_messages WHERE deleted=0").fetchone()[
                            0
                        ]
                    )
                    - len(rows),
                )
                messages = [
                    {
                        "mid": r["mid"],
                        "cid": r["cid"],
                        "role": r["role"],
                        "text": r["text"],
                        "attachments": json.loads(r["attachments"] or "[]"),
                        "device": r["device"],
                        "seq": r["seq"],
                        "created_at": r["created_at"],
                        "deleted": False,
                    }
                    for r in reversed(rows)
                ]
            else:
                where = "seq > ?"
                params: list[Any] = [since]
                if scope == "main":
                    where += " AND cid IN (SELECT cid FROM sync_conversations WHERE kind='main')"
                rows = c.execute(
                    f"SELECT mid, cid, role, text, attachments, device, seq, created_at, deleted "
                    f"FROM sync_messages WHERE {where} ORDER BY seq LIMIT ?",
                    (*params, limit),
                ).fetchall()
                for r in rows:
                    messages.append(
                        {
                            "mid": r["mid"],
                            "cid": r["cid"],
                            "role": r["role"],
                            "text": r["text"],
                            "attachments": json.loads(r["attachments"] or "[]"),
                            "device": r["device"],
                            "seq": r["seq"],
                            "created_at": r["created_at"],
                            "deleted": bool(r["deleted"]),
                        }
                    )
            cursor = self._cursor(c)
            more = len(conversations) == limit or len(messages) == limit
        out: dict[str, Any] = {
            "cursor": cursor,
            "store": self.store_id(),
            "more": more,
            "conversations": conversations,
            "messages": messages,
        }
        if tail:
            out["skipped"] = skipped
        return out

    # ------------------------------------------------------------------ push
    def push(
        self, device: str, conversations: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        now = _now()
        if not isinstance(conversations, list) or not isinstance(messages, list):
            raise HTTPException(400, "conversations and messages must be lists")
        if len(messages) > MAX_POST_MESSAGES:
            raise HTTPException(400, "at most 200 messages per request")
        accepted = 0
        rejected: list[dict[str, Any]] = []
        with self._tx() as c:
            c.execute("BEGIN IMMEDIATE")
            known: set[str] = set()
            for conv in conversations:
                if not isinstance(conv, dict):
                    continue
                cid = str(conv.get("cid") or "")
                kind = str(conv.get("kind") or "")
                if not _ID.match(cid):
                    rejected.append({"cid": cid, "reason": "bad_id"})
                    continue
                if kind not in KINDS:
                    rejected.append({"cid": cid, "reason": "bad_kind"})
                    continue
                main = c.execute(
                    "SELECT cid FROM sync_conversations WHERE kind='main' AND deleted=0"
                ).fetchone()
                if kind == "main" and main is not None and main["cid"] != cid:
                    rejected.append({"cid": cid, "reason": "main_exists", "cid_main": main["cid"]})
                    continue
                row = c.execute(
                    "SELECT seq, created_at, kind, title, deleted, deleted_at FROM sync_conversations WHERE cid=?",
                    (cid,),
                ).fetchone()
                conv_title = str(conv.get("title") or "")[:200]
                is_deleted = bool(conv.get("deleted"))
                in_updated = float(conv.get("updated_at") or 0.0)
                if (
                    row is not None
                    and bool(row["deleted"])
                    and not is_deleted
                    and in_updated <= float(row["deleted_at"] or 0.0) + 1.0
                ):
                    # Tombstone wins: do not resurrect a deleted conversation from a stale peer
                    continue
                if (
                    row is not None
                    and row["kind"] == kind
                    and row["title"] == conv_title
                    and bool(row["deleted"]) == is_deleted
                ):
                    if not is_deleted:
                        known.add(cid)
                    continue
                # a rename or delete bumps the conversation's seq so every device sees it
                seq = self._next_seq(c)
                created = float(conv.get("created_at") or (row["created_at"] if row else now))
                conv_device = str(conv.get("device") or device)[:80]
                c.execute(
                    "INSERT OR REPLACE INTO sync_conversations "
                    "(cid, kind, title, device, seq, created_at, updated_at, deleted, deleted_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        kind,
                        conv_title,
                        conv_device,
                        seq,
                        created,
                        float(conv.get("updated_at") or now),
                        1 if is_deleted else 0,
                        now if is_deleted else None,
                    ),
                )
                if not is_deleted:
                    known.add(cid)
                else:
                    c.execute(
                        "UPDATE sync_messages SET deleted=1, deleted_at=? WHERE cid=?",
                        (now, cid),
                    )
                accepted += 1
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                mid = str(msg.get("mid") or "")
                cid = str(msg.get("cid") or "")
                role = str(msg.get("role") or "")
                if not _ID.match(mid):
                    rejected.append({"mid": mid, "reason": "bad_id"})
                    continue
                if role not in ROLES:
                    rejected.append({"mid": mid, "reason": "bad_role"})
                    continue
                if msg.get("deleted"):
                    existing = c.execute(
                        "SELECT seq, deleted FROM sync_messages WHERE mid=?", (mid,)
                    ).fetchone()
                    if existing is not None and bool(existing["deleted"]):
                        continue
                    seq = self._next_seq(c)
                    if existing is not None:
                        c.execute(
                            "UPDATE sync_messages SET deleted=1, deleted_at=?, seq=? WHERE mid=?",
                            (now, seq, mid),
                        )
                    else:
                        msg_device = str(msg.get("device") or device)[:80]
                        c.execute(
                            "INSERT INTO sync_messages (mid, cid, role, text, attachments, device, seq, created_at, deleted, deleted_at) "
                            "VALUES (?,?,?,?,?,?,?,?,1,?)",
                            (mid, cid, role, "", "[]", msg_device, seq, float(msg.get("created_at") or now), now),
                        )
                    accepted += 1
                    continue
                row = c.execute(
                    "SELECT cid FROM sync_conversations WHERE cid=? AND deleted=0", (cid,)
                ).fetchone()
                if row is None and cid not in known:
                    rejected.append({"mid": mid, "reason": "no_conversation"})
                    continue
                text = str(msg.get("text") or "")
                if len(text.encode("utf-8", errors="replace")) > LIMITS["text_bytes"]:
                    rejected.append({"mid": mid, "reason": "text_too_long"})
                    continue
                existing = c.execute("SELECT seq FROM sync_messages WHERE mid=?", (mid,)).fetchone()
                if existing is None:
                    seq = self._next_seq(c)
                    msg_device = str(msg.get("device") or device)[:80]
                    c.execute(
                        "INSERT INTO sync_messages (mid, cid, role, text, attachments, device, seq, created_at, deleted) "
                        "VALUES (?,?,?,?,?,?,?,?,0)",
                        (
                            mid,
                            cid,
                            role,
                            text,
                            json.dumps(
                                _clean_attachments(msg.get("attachments")), ensure_ascii=False
                            ),
                            msg_device,
                            seq,
                            float(msg.get("created_at") or now),
                        ),
                    )
                    accepted += 1
            c.execute(
                "DELETE FROM sync_messages WHERE deleted=0 AND rowid IN ("
                " SELECT rowid FROM sync_messages WHERE deleted=0 ORDER BY seq LIMIT ?)",
                (
                    max(
                        0,
                        int(
                            c.execute(
                                "SELECT COUNT(*) FROM sync_messages WHERE deleted=0"
                            ).fetchone()[0]
                        )
                        - LIMITS["messages"],
                    ),
                ),
            )
            cursor = self._cursor(c)
            c.commit()
        return {"cursor": cursor, "accepted": accepted, "rejected": rejected}

    def _next_seq(self, c: sqlite3.Connection) -> int:
        return self._cursor(c) + 1

    # ------------------------------------------------------------------ working
    def set_working(
        self, cid: str, device: str, working: bool, state: str = "", detail: str = ""
    ) -> dict[str, Any]:
        if not cid or not _ID.match(cid):
            raise HTTPException(400, "Say cid and working: true or false")
        if working:
            self._working[cid] = {
                "device": device[:80],
                "at": _now(),
                "state": state[:20],
                "detail": detail[:DETAIL_CAP],
            }
        else:
            self._working.pop(cid, None)
        return {"cid": cid, "working": working, "device": device[:80]}

    def _sweep_working(self) -> None:
        cutoff = _now() - WORKING_TTL_S
        for cid in [cid for cid, w in self._working.items() if w["at"] < cutoff]:
            self._working.pop(cid, None)

    # ------------------------------------------------------------------ delete
    def delete_conversation(
        self, cid: str, *, create_if_missing: bool = False, kind: str = "side"
    ) -> dict[str, Any]:
        with self._tx() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT cid, deleted FROM sync_conversations WHERE cid=?", (cid,)).fetchone()
            now = _now()
            if row is None:
                if not create_if_missing:
                    c.commit()
                    raise HTTPException(404, "no_conversation")
                seq = self._next_seq(c)
                c.execute(
                    "INSERT INTO sync_conversations "
                    "(cid, kind, title, device, seq, created_at, updated_at, deleted, deleted_at) "
                    "VALUES (?, ?, '', '', ?, ?, ?, 1, ?)",
                    (cid, kind if kind in KINDS else "side", seq, now, now, now),
                )
            elif not bool(row["deleted"]):
                seq = self._next_seq(c)
                c.execute(
                    "UPDATE sync_conversations SET deleted=1, deleted_at=?, seq=? WHERE cid=?",
                    (now, seq, cid),
                )
                c.execute(
                    "UPDATE sync_messages SET deleted=1, deleted_at=? WHERE cid=?", (now, cid)
                )
            cursor = self._cursor(c)
            c.commit()
        return {"cursor": cursor, "deleted": True}

    def delete_message(
        self, cid: str, mid: str, *, create_if_missing: bool = False, role: str = "user"
    ) -> dict[str, Any]:
        with self._tx() as c:
            c.execute("BEGIN IMMEDIATE")
            now = _now()
            existing = c.execute(
                "SELECT mid, deleted FROM sync_messages WHERE mid=?", (mid,)
            ).fetchone()
            if existing is not None:
                if bool(existing["deleted"]):
                    cursor = self._cursor(c)
                    c.commit()
                    return {"cursor": cursor, "deleted": False}
                seq = self._next_seq(c)
                cur = c.execute(
                    "UPDATE sync_messages SET deleted=1, deleted_at=?, seq=? WHERE mid=? AND cid=?",
                    (now, seq, mid, cid),
                )
                deleted = cur.rowcount > 0
            elif create_if_missing and _ID.match(mid) and _ID.match(cid):
                seq = self._next_seq(c)
                c.execute(
                    "INSERT INTO sync_messages "
                    "(mid, cid, role, text, attachments, device, seq, created_at, deleted, deleted_at) "
                    "VALUES (?, ?, ?, '', '[]', '', ?, ?, 1, ?)",
                    (mid, cid, role if role in ROLES else "user", seq, now, now),
                )
                deleted = True
            else:
                deleted = False
            cursor = self._cursor(c)
            c.commit()
        return {"cursor": cursor, "deleted": deleted}

    def wipe(self) -> dict[str, Any]:
        with self._tx() as c:
            c.execute("DELETE FROM sync_messages")
            c.execute("DELETE FROM sync_conversations")
            c.commit()
        return self.state()


def make_peer_router(
    db_path: str | Path,
    store: SyncPeerStore | None = None,
    on_push: Callable[[dict[str, Any]], Any] | None = None,
    before_pull: Callable[[], Any] | None = None,
) -> APIRouter:
    """The ``/v1/sync/*`` endpoints on a runtime; mount it with the server's token check.

    The mount point is ``/v1/sync`` so a paired device's :class:`nanomuse.sync.client.SyncClient`
    works unchanged, with the peer's address as its base URL and the peer's server token
    or scoped pairing token as its key.
    """
    store = store or SyncPeerStore(db_path)
    router = APIRouter()

    @router.get("/state")
    async def sync_state() -> dict[str, Any]:
        if before_pull is not None:
            before_pull()
        return store.state()

    @router.put("/state")
    async def sync_set_state(body: SyncStateBody) -> dict[str, Any]:
        return store.set_enabled(body.enabled)

    @router.get("/changes")
    async def sync_changes(
        since: int = 0, limit: int = DEFAULT_PAGE, scope: str = "all", tail: int = 0
    ) -> dict[str, Any]:
        if before_pull is not None:
            before_pull()
        return store.changes(since, limit, scope=scope, tail=tail)

    @router.post("/changes")
    async def sync_push(body: SyncPushBody) -> dict[str, Any]:
        convs = [dict(c.model_dump()) for c in body.conversations]
        msgs = [dict(m.model_dump()) for m in body.messages]
        device = str(body.device or "")
        payload: dict[str, Any] = {
            "device": device,
            "conversations": convs,
            "messages": msgs,
        }
        out = store.push(device, convs, msgs)
        if on_push is not None and out.get("accepted"):
            try:
                on_push(payload)
            except Exception:
                pass
        return out

    @router.post("/working", status_code=204)
    async def sync_working(body: SyncWorkingBody) -> Response:
        store.set_working(body.cid, body.device, body.working, state=body.state, detail=body.detail)
        return Response(status_code=204)

    @router.delete("/changes")
    async def sync_wipe() -> dict[str, Any]:
        return store.wipe()

    @router.delete("/conversations/{cid}")
    async def sync_delete_conversation(cid: str) -> dict[str, Any]:
        out = store.delete_conversation(cid)
        if on_push is not None and out.get("deleted"):
            try:
                on_push({"device": "", "conversations": [{"cid": cid, "deleted": True}], "messages": []})
            except Exception:
                pass
        return out

    return router


# ---------------------------------------------------------------------------- pairing
PAIRS_FILE = "pairs.json"
"""The peers this runtime syncs with, in ``<data_dir>/pairs.json``: ``[{name, url, key}]``
where ``key`` is a vault reference (``{{vault:PAIR_<NAME>}}``); the token itself lives in
the encrypted vault, never in this file.  ``nanomuse pair`` writes and reads it."""


def _pair_slot(name: str) -> str:
    keep = "".join(c for c in name.upper() if c.isalnum() or c == "_")
    return "PAIR_" + (keep or "PEER")[:40]


def _pair_url(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{url!r} is not an http(s) address")
    return url.strip().rstrip("/")


def load_pairs(data_dir: str | Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / PAIRS_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    out: list[dict[str, Any]] = []
    for p in data if isinstance(data, list) else []:
        if isinstance(p, dict) and p.get("url") and p.get("key"):
            entry: dict[str, Any] = {
                "name": str(p.get("name") or ""),
                "url": str(p.get("url") or ""),
                "key": str(p.get("key") or ""),
            }
            if p.get("id"):
                entry["id"] = str(p["id"])
            if p.get("kind"):
                entry["kind"] = str(p["kind"])
            if p.get("cloud_key"):
                entry["cloud_key"] = str(p["cloud_key"])
            raw_urls = p.get("urls")
            if isinstance(raw_urls, list):
                clean_urls = [
                    str(u).strip().rstrip("/")
                    for u in raw_urls
                    if isinstance(u, str) and str(u).strip().startswith(("http://", "https://"))
                ]
                if clean_urls:
                    entry["urls"] = list(dict.fromkeys(clean_urls))
            out.append(entry)
    return out


def save_pairs(data_dir: str | Path, pairs: list[dict[str, Any]]) -> None:
    from nanomuse.private import keep_private

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / PAIRS_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(pairs, ensure_ascii=False, indent=1), "utf-8")
    try:
        keep_private(tmp)
    except OSError:  # pragma: no cover
        pass
    tmp.replace(path)


def add_pair(
    data_dir: str | Path,
    url: str,
    token: str,
    name: str = "",
    device_id: str = "",
    kind: str = "",
    urls: list[str] | None = None,
    cloud_secret: str = "",
) -> dict[str, Any]:
    """Record a peer: its token into the vault, its address and name into ``pairs.json``.

    Run the same command (or claim a pairing code) between two runtimes and they sync
    straight to each other: no relay, no account.
    """
    from urllib.parse import urlsplit

    from nanomuse.vault.vault import CredentialVault

    url = _pair_url(url)
    clean_name = str(name or "").strip()
    if clean_name.lower() == "localhost":
        clean_name = ""
    name = (clean_name or urlsplit(url).hostname or "peer").strip()[:60]
    did = str(device_id or "").strip()[:80]
    slot = _pair_slot(did or name)
    vault = CredentialVault(Path(data_dir) / "vault.enc", Path(data_dir) / "vault.key")
    vault.set(slot, token.strip())
    existing_pairs = load_pairs(data_dir)
    merged_urls: list[str] = [url]
    for u in urls or []:
        cu = str(u or "").strip().rstrip("/")
        if cu.startswith(("http://", "https://")) and cu not in merged_urls:
            merged_urls.append(cu)
    preserved_cloud_key = ""
    for old in existing_pairs:
        if (did and old.get("id") == did) or old["name"] == name or old["url"] == url:
            if old.get("cloud_key") and not preserved_cloud_key:
                preserved_cloud_key = str(old["cloud_key"])
            for u in [old.get("url"), *(old.get("urls") or [])]:
                cu = str(u or "").strip().rstrip("/")
                if cu.startswith(("http://", "https://")) and cu not in merged_urls:
                    merged_urls.append(cu)
    cloud_key_ref = preserved_cloud_key
    clean_secret = str(cloud_secret or "").strip()
    if clean_secret and "{{vault:" not in clean_secret:
        cslot = f"CLOUD_{slot}"[:48]
        vault.set(cslot, clean_secret)
        cloud_key_ref = f"{{{{vault:{cslot}}}}}"
    pairs = [
        p
        for p in existing_pairs
        if not (
            (did and p.get("id") == did)
            or p["name"] == name
            or p["url"] == url
        )
    ]
    entry: dict[str, Any] = {
        "name": name,
        "url": url,
        "key": f"{{{{vault:{slot}}}}}",
        "urls": merged_urls[:8],
    }
    if did:
        entry["id"] = did
    if kind:
        entry["kind"] = str(kind).strip()[:20]
    if cloud_key_ref:
        entry["cloud_key"] = cloud_key_ref
    pairs.append(entry)
    save_pairs(data_dir, pairs)
    return entry


def remove_pair(data_dir: str | Path, identifier: str) -> bool:
    pairs = load_pairs(data_dir)
    before = len(pairs)
    target = str(identifier or "").strip()
    pairs = [
        p
        for p in pairs
        if p.get("name") != target and p.get("id") != target and p.get("url") != target
    ]
    if len(pairs) != before:
        save_pairs(data_dir, pairs)
        return True
    return False


__all__ = [
    "LIMITS",
    "PAIRS_FILE",
    "SyncPeerStore",
    "add_pair",
    "load_pairs",
    "make_peer_router",
    "remove_pair",
]
