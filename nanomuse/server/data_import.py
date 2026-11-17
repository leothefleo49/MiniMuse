"""Importing another assistant's data into the runtime: text, files and zip archives.

The web console's Settings → Import & export drives this. It accepts three shapes of
material:

- a Muse data export zip (``manifest.json`` turns, ``conversations/``, the
  ``workspace/your_files/apps_and_data/`` layout: chat transcripts, memory files, and
  every file of every app Muse built),
- a nanoMuse export zip (``threads/*.json`` timelines),
- loose transcripts and pasted text (Muse's ``## Speaker — 2026-10-17 13:18:45 MDT``
  and ``**Speaker - Sat Oct 10, 2026, 9:54:24 AM MDT**`` shapes, among others).

What happens to each piece is decided by :class:`ImportOptions`:

- ``restore_chats`` makes imported conversations real chats. A conversation whose title
  matches an existing chat merges into it, message by message, in time order; the rest
  become side chats. Nothing already in nanoMuse is ever deleted. The Muse main chat can
  become a side chat, take over as the main chat (the current one is set aside as a side
  chat, untouched), or merge into the current main chat interleaved by time.
- ``background_memory`` writes every imported message and memory file into the deep
  archive the agent searches with ``deep_memory`` (``personal.db`` FTS).
- ``apps`` says what happens to the code Muse built: keep the files, register them as
  interactive apps at ``/apps/<slug>``, both, or neither.

An import ledger (``data/imports.db``) remembers the hash of every entry, so importing
the same archive again adds nothing twice. Files that already exist under another name
are kept: a new copy gets ``-import-2`` (and so on) rather than overwriting anything.

Safety bounds: an upload may be up to ``IMPORT_UPLOAD_MB``, a zip up to
``MAX_ZIP_ENTRIES`` entries expanding to ``MAX_ZIP_TOTAL_BYTES``, one entry at most
``MAX_ZIP_ENTRY_BYTES``. Paths are flattened and sanitized, and nothing is ever written
outside the data directory and the workspace.

The heavy work (reading and classifying the archive, writing app and memory files) runs
in a worker thread via :func:`ingest_path`; :func:`apply_plan` then mutates the service's
threads on the event loop, where the rest of the runtime expects it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanomuse.logger import logger
from nanomuse.schema import Message

if TYPE_CHECKING:
    from nanomuse.server.service import MuseService

# ------------------------------------------------------------------ bounds
IMPORT_UPLOAD_MB = 0
"""How big one uploaded file may be, in MB. ``0`` means no limit: a Muse export is a zip of
the whole workspace and routinely passes several GB. The free disk space is the real bound
(checked up front by the endpoint)."""

MAX_ZIP_ENTRIES = 5_000_000
MAX_ZIP_ENTRY_BYTES = 64 * 1024 * 1024
"""The most of one *text* entry that is read into memory. App files are copied to disk in
chunks, so they have no size limit."""
MAX_ZIP_TOTAL_BYTES = 0
"""``0``: no limit on what an archive expands to."""
DISK_MARGIN_BYTES = 256 * 1024 * 1024
COPY_CHUNK = 1024 * 1024
MAX_TRANSCRIPT_CHARS = 20_000_000
MAX_EVENTS_PER_CONVERSATION = 20_000
MAX_TEXT_PASTE_BYTES = 32 * 1024 * 1024

# the ts a message whose time could not be read gets: before everything else, in order
FLOOR_TS = "0001-01-01T00:00:00+00:00"

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".json", ".csv", ".log", ".html", ".htm"}

# known timezone abbreviations in Muse transcripts, as fixed offsets (fine for an archive)
TZ_OFFSETS = {
    "UTC": 0,
    "GMT": 0,
    "MDT": -6,
    "MST": -7,
    "PDT": -7,
    "PST": -8,
    "EDT": -4,
    "EST": -5,
    "CDT": -5,
    "CST": -6,
    "BST": 1,
    "CET": 1,
    "CEST": 2,
    "JST": 9,
    "AEST": 10,
}

# speakers that map to the person's side of the chat; everything else is the assistant's
_USER_WORDS = ("user", "you", "me", "human", "client", "person")
_ASSISTANT_WORDS = ("muse", "assistant", "ai", "bot", "gpt", "model", "claude", "gemini")

HEADER_PATTERNS = (
    # ## User — 2026-10-17 13:18:45 MDT
    re.compile(
        r"^##\s+(?P<speaker>[^—\-\n]{1,60}?)\s*[—–-]\s*(?P<ts>\S[^\n]{0,60})$",
        re.MULTILINE,
    ),
    # **User - Sat Oct 10, 2026, 9:54:24 AM MDT**
    re.compile(
        r"^\*\*\s*(?P<speaker>[^*\n]{1,60}?)\s*[—–-]\s*(?P<ts>[^*\n]{0,60}?)\s*\*\*\s*$",
        re.MULTILINE,
    ),
    # ### User — 2026-10-17 13:18:45 MDT (the side-chat transcripts)
    re.compile(
        r"^###\s+(?P<speaker>[^—\-\n]{1,60}?)\s*[—–-]\s*(?P<ts>\S[^\n]{0,60})$",
        re.MULTILINE,
    ),
)

# fallback: "User: text" / "Muse: text" lines, no timestamp
PLAIN_LINE = re.compile(
    r"^(?P<speaker>User|You|Me|Human|Muse|Assistant|AI|Bot)\s*:\s*(?P<text>.*)$",
    re.MULTILINE,
)

DATE_IN_NAME = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")

_UNSAFE = re.compile(r"[^A-Za-z0-9_. -]+")


@dataclass(frozen=True)
class ImportOptions:
    """The switches the Import & export screen offers."""

    restore_chats: bool = True
    background_memory: bool = True
    apps: str = "both"  # "save" | "restore" | "both" | "neither"
    main_chat: str = "side"  # "side" | "replace" | "merge"

    def __post_init__(self) -> None:
        if self.apps not in ("save", "restore", "both", "neither"):
            raise ValueError(f"unknown apps option: {self.apps}")
        if self.main_chat not in ("side", "replace", "merge"):
            raise ValueError(f"unknown main chat option: {self.main_chat}")

    def key(self) -> str:
        return (
            f"{int(self.restore_chats)}|{int(self.background_memory)}|{self.apps}|{self.main_chat}"
        )


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str
    ts: str = FLOOR_TS  # ISO UTC


@dataclass
class Conversation:
    title: str
    kind: str  # "muse_main" | "transcript" | "thread" | "text"
    turns: list[Turn]
    source: str = ""
    body: str = ""  # the raw text when there were no parseable messages


@dataclass
class ImportedApp:
    slug: str
    title: str
    html_rel: str  # workspace-relative path of the page to serve, "" when none
    files: int


@dataclass
class ImportPlan:
    """What the archive contained, staged on disk; ``apply_plan`` turns it into chats."""

    conversations: list[Conversation] = field(default_factory=list)
    apps: list[ImportedApp] = field(default_factory=list)
    memory_files: int = 0
    skipped_files: int = 0
    duplicates: int = 0
    option_key: str = ""
    ledger_marks: list[tuple[str, str]] = field(default_factory=list)  # (digest, kind)
    note: str = ""
    profile_data: dict[str, Any] = field(default_factory=dict)
    ui_prefs: dict[str, Any] = field(default_factory=dict)
    avatar_files: int = 0
    avatar_face: str = ""


# ------------------------------------------------------------------ parsing
def parse_timestamp(raw: str) -> str:
    """One timestamp as ISO UTC, or ``""`` when it cannot be read.

    Muse writes two shapes: ``2026-10-17 13:18:45 MDT`` and ``Sat Oct 10, 2026, 9:54:24 AM
    MDT``. The timezone abbreviation is read from the table; anything ISO goes through
    Python's parser."""
    raw = raw.strip().strip(".,;")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(UTC).isoformat(timespec="seconds")
    except ValueError:
        pass
    # a trailing abbreviation ("MDT", "MST"): strptime's %Z accepts only UTC/GMT and the
    # machine's own names, so it is taken off and read from the table instead
    abbr = re.search(r"\b([A-Z]{2,5})\b$", raw)
    offset = 0
    if abbr is not None and abbr.group(1) in TZ_OFFSETS:
        offset = TZ_OFFSETS[abbr.group(1)]
        raw = raw[: abbr.start()].strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%a %b %d, %Y, %I:%M:%S %p",
        "%b %d, %Y, %I:%M:%S %p",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        aware = dt.replace(tzinfo=timezone(timedelta(hours=offset)))
        return aware.astimezone(UTC).isoformat(timespec="seconds")
    return ""


def _speaker_role(speaker: str) -> tuple[str, str]:
    """(role, prefix): the side of the chat, and the speaker's name to keep on the line
    when the name is nobody's known side."""
    low = speaker.strip().lower()
    words = set(re.findall(r"[a-z]+", low))
    if words & set(_USER_WORDS):
        return "user", ""
    if words & set(_ASSISTANT_WORDS):
        return "assistant", ""
    return "assistant", f"{speaker.strip()}: "


def looks_like_transcript(text: str) -> bool:
    """A text that carries speaker-and-time headers, not a plain document."""
    for pattern in HEADER_PATTERNS:
        for match in pattern.finditer(text):
            if parse_timestamp(match.group("ts")):
                return True
    return bool(PLAIN_LINE.search(text))


def parse_transcript(text: str, source: str = "") -> list[Turn]:
    """The messages of a Muse transcript, in order, with their timestamps.

    Recognizes the ``## Speaker — timestamp`` sections, the ``**Speaker - timestamp**``
    bold headers, ``### Speaker — timestamp`` blocks and bare ``User:`` lines. Text before
    the first header (the title and metadata) is left out; an unknown speaker keeps their
    name on the line so nothing is lost."""
    turns: list[Turn] = []
    last_ts = ""
    pattern = None
    for candidate in HEADER_PATTERNS:
        if candidate.search(text):
            pattern = candidate
            break
    if pattern is None:
        for line in text.splitlines():
            match = PLAIN_LINE.match(line.strip())
            if match:
                role, prefix = _speaker_role(match.group("speaker"))
                body = (prefix + match.group("text")).strip()
                if body:
                    turns.append(Turn(role=role, text=body, ts=last_ts or FLOOR_TS))
        return turns
    current: dict[str, Any] | None = None
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            ts = parse_timestamp(match.group("ts"))
            last_ts = ts or last_ts
            role, prefix = _speaker_role(match.group("speaker"))
            if current is not None and str(current["text"]).strip():
                turns.append(
                    Turn(
                        role=str(current["role"]),
                        text=str(current["text"]).strip(),
                        ts=str(current["ts"]),
                    )
                )
            current = {
                "role": role,
                "prefix": prefix,
                "text": "",
                "ts": last_ts or FLOOR_TS,
            }
            continue
        if current is not None:
            stripped = line.strip()
            if stripped in ("---", "***"):
                continue
            current["text"] = str(current["text"]) + line + "\n"
    if current is not None and str(current["text"]).strip():
        turns.append(
            Turn(
                role=str(current["role"]),
                text=(str(current["prefix"]) + str(current["text"]).strip()).strip(),
                ts=str(current["ts"]),
            )
        )
    return turns


def title_from_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[-_]chat[-_]?transcript", "", stem, flags=re.IGNORECASE)
    stem = stem.replace("-", " ").replace("_", " ").strip()
    return stem[:80] or Path(name).name


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "imported"


def _safe_rel(rel: str) -> Path:
    """A relative path that cannot escape: separators normalized, walk-ups removed, and
    Windows-unfriendly characters replaced."""
    parts: list[str] = []
    for part in rel.replace("\\", "/").split("/"):
        part = _UNSAFE.sub("-", part).strip(" .")
        if not part or part in (".", ".."):
            continue
        parts.append(part[:120])
    return Path(*parts) if parts else Path("imported")


def _decode(data: bytes) -> str | None:
    if len(data) > MAX_TRANSCRIPT_CHARS * 4:
        return None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text[:1024]:
            return text
    return None


# ------------------------------------------------------------------ the ledger
class ImportLedger:
    """What has already been imported, so the same archive never lands twice."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=30)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                "digest TEXT NOT NULL, kind TEXT NOT NULL, options TEXT NOT NULL, "
                "name TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (digest, kind, options))"
            )
            self._conn.commit()

    def seen(self, digest: str, kind: str, options_key: str) -> bool:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM entries WHERE digest = ? AND kind = ? AND options = ?",
            (digest, kind, options_key),
        ).fetchone()
        return row is not None

    def mark(self, digest: str, kind: str, options_key: str, name: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT OR IGNORE INTO entries (digest, kind, options, name, at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                digest,
                kind,
                options_key,
                name[:200],
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None


# ------------------------------------------------------------------ deep memory indexing
def _open_personal(db_path: Path) -> sqlite3.Connection | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(path, title, category, content)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5("
        "turn_id, thread_id, role, created_at, content)"
    )
    conn.commit()
    return conn


def index_conversation_turns(
    db_path: Path, thread_id: str, conv: Conversation, cap: int = MAX_EVENTS_PER_CONVERSATION
) -> int:
    """Every imported message into ``messages_fts``, so ``deep_memory`` finds it."""
    conn = _open_personal(db_path)
    if conn is None:
        return 0
    try:
        indexed = 0
        for i, turn in enumerate(conv.turns):
            if turn.role not in ("user", "assistant") or not turn.text.strip():
                continue
            if indexed >= cap:
                break
            conn.execute(
                "INSERT INTO messages_fts (turn_id, thread_id, role, created_at, content) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"imp_{slugify(conv.title)}_{i}", thread_id, turn.role, turn.ts, turn.text),
            )
            indexed += 1
        conn.commit()
        return indexed
    except sqlite3.Error as exc:
        logger.warning("could not index imported messages into %s: %s", db_path, exc)
        return 0
    finally:
        conn.close()


def index_memory_text(db_path: Path, rel: str, text: str) -> bool:
    conn = _open_personal(db_path)
    if conn is None:
        return False
    try:
        conn.execute(
            "INSERT INTO memory_fts (path, title, category, content) VALUES (?, ?, 'memory', ?)",
            (rel, rel.rsplit("/", 1)[-1], text),
        )
        conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning("could not index imported memory into %s: %s", db_path, exc)
        return False
    finally:
        conn.close()


# ------------------------------------------------------------------ writing files
def _unique_target(root: Path, rel: Path) -> Path:
    """The first free name under ``root``: the plain one, else ``name-import-2`` and up.
    An existing file is never overwritten."""
    if not (root / rel).exists():
        return root / rel
    stem, suffix = rel.stem, rel.suffix
    for n in range(2, 1000):
        candidate = root / f"{stem}-import-{n}{suffix}"
        if not candidate.exists():
            return candidate
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return root / f"{stem}-import-{stamp}{suffix}"


def _store_memory_file(data_dir: Path, rel: Path, text: str) -> str | None:
    """The text as a file under ``data/muse_memory/imports``; returns the stored
    workspace-style relative name, or None when the disk refused."""
    target = _unique_target(data_dir / "muse_memory" / "imports", rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not store imported memory file: %s", exc)
        return None
    return f"memory/imports/{target.relative_to(data_dir / 'muse_memory' / 'imports').as_posix()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store_app_stream(
    workspace: Path,
    slug: str,
    rel: Path,
    archive: zipfile.ZipFile,
    member: str,
    plan: ImportPlan,
) -> tuple[str, str]:
    """One app file, streamed from the archive to ``workspace/apps/<slug>/`` in chunks (so
    a file of any size costs no memory); returns ``(workspace-relative path, sha256)``.
    The relative path is ``""`` when nothing was written (an identical file already
    exists, or the disk refused). An existing different file keeps its name and the new
    copy gets a numbered one."""
    out_rel = Path("apps") / slug / rel
    target = workspace / out_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".import-part")
    digest = hashlib.sha256()
    try:
        with archive.open(member) as src, tmp.open("wb") as out:
            for chunk in iter(lambda: src.read(COPY_CHUNK), b""):
                digest.update(chunk)
                out.write(chunk)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        logger.warning("could not store imported app file %s: %s", member, exc)
        tmp.unlink(missing_ok=True)
        return "", digest.hexdigest()
    sha = digest.hexdigest()
    if target.exists():
        try:
            same = target.stat().st_size == tmp.stat().st_size and _sha256_file(target) == sha
        except OSError:
            same = False
        if same:
            tmp.unlink(missing_ok=True)
            plan.duplicates += 1
            return "", sha
        for n in range(2, 1000):
            alt = target.with_name(f"{target.stem}-import-{n}{target.suffix}")
            if not alt.exists():
                target = alt
                out_rel = Path("apps") / slug / target.name
                break
        else:
            tmp.unlink(missing_ok=True)
            return "", sha
    try:
        tmp.replace(target)
    except OSError as exc:
        logger.warning("could not store imported app file: %s", exc)
        tmp.unlink(missing_ok=True)
        return "", sha
    return out_rel.as_posix(), sha


def free_bytes(path: Path) -> int:
    """Free disk space at ``path`` (the nearest existing parent), or a huge number when it
    cannot be read."""
    import shutil

    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return 1 << 62


def _store_app_entry(
    workspace: Path, slug: str, rel: Path, data: bytes, digest: str, plan: ImportPlan
) -> str:
    """One app file into ``workspace/apps/<slug>/``; returns its workspace-relative path.
    An existing identical file is skipped, an existing different one keeps its name and
    the new copy gets a numbered name."""
    out_rel = Path("apps") / slug / rel
    target = workspace / out_rel
    if target.exists():
        try:
            existing = target.read_bytes()
        except OSError:
            existing = b""
        if hashlib.sha256(existing).hexdigest() == digest:
            plan.duplicates += 1
            return ""
        stem, suffix = target.stem, target.suffix
        renamed = False
        for n in range(2, 1000):
            alt = target.with_name(f"{stem}-import-{n}{suffix}")
            if not alt.exists():
                target = alt
                out_rel = Path("apps") / slug / target.name
                renamed = True
                break
        if not renamed:
            return ""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_bytes(data)
    except OSError as exc:
        logger.warning("could not store imported app file: %s", exc)
        return ""
    return out_rel.as_posix()


# ------------------------------------------------------------------ archive reading
def ingest_path(svc: MuseService, path: Path, name: str, options: ImportOptions) -> ImportPlan:
    """Read and stage one uploaded file (a zip or a single text file) in a worker thread.

    Conversations are held in memory; app and memory files are written straight to the
    workspace and the data directory, and the ledger is consulted so nothing overwrites
    or duplicates. Threads are not touched here: :func:`apply_plan` does that on the loop."""
    plan = ImportPlan(option_key=options.key())
    if zipfile.is_zipfile(path):
        _ingest_zip(svc, path, name, options, plan)
    else:
        _ingest_single(path, name, options, plan, svc.data_dir)
    return plan


def _ingest_single(
    path: Path, name: str, options: ImportOptions, plan: ImportPlan, data_dir: Path
) -> None:
    try:
        if path.stat().st_size > MAX_TRANSCRIPT_CHARS * 4:
            plan.skipped_files += 1
            plan.note = f"{name} is too large to read as text; put it in a zip and try again"
            return
        data = path.read_bytes()
    except OSError:
        plan.skipped_files += 1
        return
    plan.ledger_marks.append((hashlib.sha256(data).hexdigest(), "file"))
    text = _decode(data)
    if text is None:
        plan.skipped_files += 1
        plan.note = f"{name} is not a text file; nothing was read from it"
        return
    _accept_text(data_dir, name, text, options, plan)


_PROFILE_KEYS = (
    "name",
    "user_name",
    "tagline",
    "color",
    "emoji",
    "avatar",
    "tone",
    "communication",
    "style",
)

_INLINE_PROFILE_BLOCK = re.compile(
    r"```(?:muse-profile|minimuse-profile)\s*\n(?P<body>\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _extract_profile_dict(raw: dict[str, Any], plan: ImportPlan, data_dir: Path) -> bool:
    """Extract profile fields, user_profile markdown, and ui_preferences from a JSON object."""
    matched = False
    src = raw.get("profile") if isinstance(raw.get("profile"), dict) else raw
    if isinstance(src, dict):
        for k in _PROFILE_KEYS:
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                plan.profile_data[k] = v.strip()
                matched = True
        # Alias support for Muse exports
        if not plan.profile_data.get("name") and isinstance(src.get("assistant_name"), str):
            plan.profile_data["name"] = src["assistant_name"].strip()
            matched = True
        if not plan.profile_data.get("user_name") and isinstance(src.get("user"), str):
            plan.profile_data["user_name"] = src["user"].strip()
            matched = True
    if isinstance(raw.get("ui_preferences"), dict) and raw["ui_preferences"]:
        plan.ui_prefs.update(raw["ui_preferences"])
        matched = True
    user_md = raw.get("user_md") or raw.get("user_profile")
    if isinstance(user_md, str) and user_md.strip():
        try:
            (data_dir / "USER.md").write_text(user_md.strip() + "\n", encoding="utf-8")
            matched = True
        except OSError:
            pass
    identity_md = raw.get("identity_md")
    if isinstance(identity_md, str) and identity_md.strip():
        try:
            (data_dir / "IDENTITY.md").write_text(identity_md.strip() + "\n", encoding="utf-8")
            matched = True
        except OSError:
            pass
    return matched


def _accept_text(
    data_dir: Path, name: str, text: str, options: ImportOptions, plan: ImportPlan
) -> None:
    """One piece of text: a transcript becomes a conversation; anything else becomes a
    memory file (when background memory is on) and a note in a chat."""
    text = text.strip()
    if not text:
        return
    # Check if the entire file/paste is a JSON profile or setup bundle
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed_json = json.loads(text)
            if isinstance(parsed_json, dict) and _extract_profile_dict(parsed_json, plan, data_dir):
                # If it only contained profile/setup metadata and no turns, we're done
                if not parsed_json.get("turns") and not parsed_json.get("events"):
                    return
        except json.JSONDecodeError:
            pass
    # Extract any inline ```muse-profile { ... } ``` block embedded in a pasted transfer
    for m in _INLINE_PROFILE_BLOCK.finditer(text):
        try:
            block_obj = json.loads(m.group("body"))
            if isinstance(block_obj, dict):
                _extract_profile_dict(block_obj, plan, data_dir)
        except json.JSONDecodeError:
            pass
    text_clean = _INLINE_PROFILE_BLOCK.sub("", text).strip()
    if not text_clean:
        return
    turns = parse_transcript(text_clean[:MAX_TRANSCRIPT_CHARS], name)
    if turns:
        plan.conversations.append(
            Conversation(title=title_from_name(name), kind="transcript", turns=turns, source=name)
        )
        return
    if options.background_memory:
        rel = _store_memory_file(data_dir, _safe_rel(name), text_clean)
        if rel and index_memory_text(data_dir / "personal.db", rel, text_clean):
            plan.memory_files += 1
    if options.restore_chats:
        plan.conversations.append(
            Conversation(
                title=title_from_name(name), kind="text", turns=[], source=name, body=text_clean
            )
        )


def _ingest_zip(
    svc: MuseService, path: Path, name: str, options: ImportOptions, plan: ImportPlan
) -> None:
    data_dir = svc.data_dir
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) > MAX_ZIP_ENTRIES:
            raise ValueError("the archive has too many entries to import safely")
        total = sum(info.file_size for info in archive.infolist())
        if MAX_ZIP_TOTAL_BYTES and total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("the archive expands to more than the import limit; split it first")
        apps: dict[str, dict[str, Any]] = {}
        for info in archive.infolist():
            member = info.filename
            if member.endswith("/") or info.is_dir():
                continue
            parts = [p for p in member.replace("\\", "/").split("/") if p]
            if not parts:
                continue
            kind = _classify(parts)
            # only text is read into memory, so only text has a size cap; app files stream
            if kind != "app" and info.file_size > MAX_ZIP_ENTRY_BYTES:
                plan.skipped_files += 1
                continue
            if kind == "manifest":
                _read_manifest(archive, member, name, plan, data_dir)
            elif kind == "profile":
                _read_profile_entry(archive, member, data_dir, plan)
            elif kind == "ui_prefs":
                _read_ui_prefs_entry(archive, member, plan)
            elif kind == "profile_md":
                _read_profile_md_entry(archive, member, data_dir, options, plan)
            elif kind == "avatar":
                _read_avatar_entry(svc, archive, member, parts, plan)
            elif kind in ("conversation", "transcript"):
                _read_text_entry(archive, member, data_dir, options, plan)
            elif kind == "thread":
                _read_thread_entry(archive, member, plan)
            elif kind == "memory":
                _read_memory_entry(archive, member, data_dir, options, plan)
            elif kind == "app":
                _read_app_entry(svc, archive, member, apps, options, plan)
            else:
                plan.skipped_files += 1
        _finish_apps(svc, apps, options, plan)


_AVATAR_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm", ".json"}


def _classify(parts: list[str]) -> str:
    joined = "/".join(p.lower() for p in parts)
    filename = parts[-1].lower()
    lower_parts = [p.lower() for p in parts]
    if joined == "manifest.json":
        return "manifest"
    if filename in ("profile.json", "muse-profile.json", "minimuse-profile.json", "identity.json"):
        return "profile"
    if filename == "ui-prefs.json":
        return "ui_prefs"
    if filename in ("user.md", "identity.md", "soul.md"):
        return "profile_md"
    if any(p in ("avatar", "avatars", "ai_avatar", "ai-avatar") for p in lower_parts):
        if Path(filename).suffix.lower() in _AVATAR_SUFFIXES:
            return "avatar"
    if parts[0].lower() == "conversations":
        return "conversation"
    if parts[0].lower() == "threads" and filename.endswith(".json"):
        return "thread"
    if _app_root(parts) is not None:
        return "app"
    if filename.endswith((".html", ".htm")):
        return "app"
    if "chat-transcripts" in joined or "transcript" in filename:
        return "transcript"
    if filename in ("memory.md", "muse-error-log.md", "muse-quotes.md"):
        return "memory"
    if any(p.lower() in ("memory", "goals", "projects") for p in parts):
        return "memory"
    if Path(filename).suffix.lower() in TEXT_SUFFIXES:
        return "transcript"
    return "skip"


def _app_root(parts: list[str]) -> tuple[str, ...] | None:
    """The folder of one app: the directory directly under an ``apps`` segment. A file
    directly under ``apps/`` is its own app."""
    lower = [p.lower() for p in parts]
    if "apps" not in lower:
        return None
    idx = lower.index("apps")
    if len(parts) == idx + 1:
        return None  # the apps folder itself
    return tuple(parts[: idx + 2])


def _read_manifest(
    archive: zipfile.ZipFile, member: str, name: str, plan: ImportPlan, data_dir: Path
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS * 4)
    try:
        manifest = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        plan.skipped_files += 1
        return
    if isinstance(manifest, dict):
        _extract_profile_dict(manifest, plan, data_dir)
    turns: list[Turn] = []
    for turn in manifest.get("turns", []) or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        if role not in ("user", "assistant"):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        turns.append(
            Turn(
                role=role,
                text=content,
                ts=parse_timestamp(str(turn.get("created_at") or "")) or FLOOR_TS,
            )
        )
    if turns:
        plan.conversations.append(
            Conversation(title="Muse main chat", kind="muse_main", turns=turns, source=name)
        )


def _read_profile_entry(
    archive: zipfile.ZipFile, member: str, data_dir: Path, plan: ImportPlan
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS)
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        plan.skipped_files += 1
        return
    if isinstance(obj, dict):
        _extract_profile_dict(obj, plan, data_dir)


def _read_ui_prefs_entry(
    archive: zipfile.ZipFile, member: str, plan: ImportPlan
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS)
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        plan.skipped_files += 1
        return
    if isinstance(obj, dict):
        plan.ui_prefs.update(obj)


def _read_profile_md_entry(
    archive: zipfile.ZipFile,
    member: str,
    data_dir: Path,
    options: ImportOptions,
    plan: ImportPlan,
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS)
    text = _decode(data)
    if not text or not text.strip():
        return
    fname = Path(member).name.upper()
    if fname in ("USER.MD", "IDENTITY.MD", "SOUL.MD"):
        try:
            (data_dir / fname).write_text(text.strip() + "\n", encoding="utf-8")
        except OSError:
            pass
    if options.background_memory:
        rel = _safe_rel(fname)
        stored = _store_memory_file(data_dir, rel, text.strip())
        if stored:
            index_memory_text(data_dir / "personal.db", stored, text.strip())
            plan.memory_files += 1


_MOOD_NAMES = ("idle", "working", "waiting", "happy", "error")


def _read_avatar_entry(
    svc: MuseService,
    archive: zipfile.ZipFile,
    member: str,
    parts: list[str],
    plan: ImportPlan,
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_ZIP_ENTRY_BYTES)
    if not data:
        return
    lower = [p.lower() for p in parts]
    idx = -1
    for marker in ("avatar", "avatars", "ai_avatar", "ai-avatar"):
        if marker in lower:
            idx = lower.index(marker)
            break
    rel_parts = parts[idx + 1 :] if idx >= 0 else [parts[-1]]
    if not rel_parts:
        return
    if len(rel_parts) == 1:
        face = str(plan.profile_data.get("avatar") or "imported-muse")
        file_name = _safe_rel(rel_parts[0]).name
    else:
        face = slugify(rel_parts[0])
        file_name = _safe_rel(rel_parts[-1]).name
    face_dir = svc.workspace() / "avatar" / face
    face_dir.mkdir(parents=True, exist_ok=True)
    target = face_dir / file_name
    try:
        target.write_bytes(data)
        plan.avatar_files += 1
        if not plan.avatar_face:
            plan.avatar_face = face
        # If an imported still is .png/.jpg/.gif for a standard mood and .webp doesn't exist yet,
        # also write <mood>.webp so the frontend's /api/files/avatar/<face>/<mood>.webp loads immediately.
        stem_low = Path(file_name).stem.lower()
        suf_low = Path(file_name).suffix.lower()
        if stem_low in _MOOD_NAMES and suf_low in (".png", ".jpg", ".jpeg", ".gif"):
            webp_alias = face_dir / f"{stem_low}.webp"
            if not webp_alias.exists():
                webp_alias.write_bytes(data)
    except OSError as exc:
        logger.warning("could not store imported avatar file: %s", exc)


def _read_thread_entry(archive: zipfile.ZipFile, member: str, plan: ImportPlan) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS * 4)
    try:
        thread = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        plan.skipped_files += 1
        return
    events = thread.get("events") if isinstance(thread, dict) else None
    if not isinstance(events, list):
        plan.skipped_files += 1
        return
    turns: list[Turn] = []
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") not in ("user", "assistant"):
            continue
        text = str(ev.get("text") or "").strip()
        if not text:
            continue
        turns.append(
            Turn(
                role=ev["type"], text=text, ts=parse_timestamp(str(ev.get("ts") or "")) or FLOOR_TS
            )
        )
    if turns:
        title = "Main chat" if Path(member).stem == "main" else title_from_name(member)
        plan.conversations.append(
            Conversation(title=title, kind="thread", turns=turns, source=member)
        )


def _read_text_entry(
    archive: zipfile.ZipFile, member: str, data_dir: Path, options: ImportOptions, plan: ImportPlan
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS * 4)
    text = _decode(data)
    if text is None:
        plan.skipped_files += 1
        return
    turns = parse_transcript(text, member)
    if turns:
        plan.conversations.append(
            Conversation(
                title=title_from_name(member), kind="transcript", turns=turns, source=member
            )
        )
        return
    if options.background_memory:
        rel = _memory_rel([p for p in member.replace("\\", "/").split("/") if p])
        stored = _store_memory_file(data_dir, rel, text)
        if stored and index_memory_text(data_dir / "personal.db", stored, text):
            plan.memory_files += 1
    if options.restore_chats:
        plan.conversations.append(
            Conversation(
                title=title_from_name(member), kind="text", turns=[], source=member, body=text
            )
        )


def _memory_rel(parts: list[str]) -> Path:
    """The path of a memory file relative to its own area: everything after the last
    memory/goals/projects segment, so the archive's folder structure stays readable."""
    lower = [p.lower() for p in parts]
    for marker in ("memory", "goals", "projects"):
        if marker in lower:
            idx = len(lower) - 1 - lower[::-1].index(marker)
            return _safe_rel("/".join(parts[idx + 1 :]))
    return _safe_rel(parts[-1])


def _read_memory_entry(
    archive: zipfile.ZipFile, member: str, data_dir: Path, options: ImportOptions, plan: ImportPlan
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS * 4)
    plan.ledger_marks.append((hashlib.sha256(data).hexdigest(), "memory"))
    if not options.background_memory:
        return
    text = _decode(data)
    if text is None:
        plan.skipped_files += 1
        return
    rel = _memory_rel([p for p in member.replace("\\", "/").split("/") if p])
    stored = _store_memory_file(data_dir, rel, text)
    if stored and index_memory_text(data_dir / "personal.db", stored, text):
        plan.memory_files += 1


def _read_app_entry(
    svc: MuseService,
    archive: zipfile.ZipFile,
    member: str,
    apps: dict[str, dict[str, Any]],
    options: ImportOptions,
    plan: ImportPlan,
) -> None:
    if options.apps == "neither":
        plan.skipped_files += 1
        return
    root = _app_root([p for p in member.replace("\\", "/").split("/") if p])
    slug = slugify(root[-1] if root is not None else Path(member).stem)
    app = apps.setdefault(
        slug,
        {"slug": slug, "title": slug.replace("-", " ").title(), "html_rel": "", "files": 0},
    )
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    lower = [p.lower() for p in parts]
    if "apps" in lower:
        idx = lower.index("apps")
        rel_parts = parts[idx + 1 :]
        if rel_parts and rel_parts[0].lower() == slug:
            rel_parts = rel_parts[1:]
    else:
        rel_parts = [parts[-1]]
    if not rel_parts:
        rel_parts = [Path(member).name]
    rel = _safe_rel("/".join(rel_parts))
    out, sha = _store_app_stream(svc.workspace(), slug, rel, archive, member, plan)
    plan.ledger_marks.append((sha, "app"))
    if out:
        app["files"] += 1
        low = rel.name.lower()
        if not app["html_rel"] and low in (
            "index.html",
            "index.htm",
            f"{slug}.html",
            f"{slug}.htm",
        ):
            app["html_rel"] = out
        elif not app["html_rel"] and low.endswith((".html", ".htm")):
            app["html_rel"] = out


def _finish_apps(
    svc: MuseService, apps: dict[str, dict[str, Any]], options: ImportOptions, plan: ImportPlan
) -> None:
    register = options.apps in ("restore", "both")
    conn: sqlite3.Connection | None = None
    try:
        if register:
            conn = sqlite3.connect(svc.data_dir / "personal.db", timeout=30)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS custom_apps ("
                "slug TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', "
                "html_path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
        for app in apps.values():
            if app["files"] == 0:
                continue
            plan.apps.append(
                ImportedApp(
                    slug=app["slug"],
                    title=app["title"],
                    html_rel=app["html_rel"],
                    files=app["files"],
                )
            )
            if register and conn is not None and app["html_rel"]:
                now = datetime.now(UTC).isoformat(timespec="seconds")
                conn.execute(
                    "INSERT INTO custom_apps (slug, title, description, html_path, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(slug) DO UPDATE SET "
                    "title=excluded.title, description=excluded.description, "
                    "html_path=excluded.html_path, updated_at=excluded.updated_at",
                    (
                        app["slug"],
                        app["title"],
                        "Imported from a Muse data export",
                        app["html_rel"],
                        now,
                        now,
                    ),
                )
        if conn is not None:
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("could not register imported apps: %s", exc)
    finally:
        if conn is not None:
            conn.close()


# ------------------------------------------------------------------ applying to the service
def apply_plan(svc: MuseService, plan: ImportPlan, options: ImportOptions) -> dict[str, Any]:
    """Turn the staged plan into chats, on the event loop: threads are created or merged
    in time order, the agent's transcript learns the imported messages, the deep archive
    is indexed, and the ledger is committed. Nothing existing is deleted."""
    profile_updated = False
    if plan.profile_data or plan.avatar_face:
        cur_prof = svc.profile.to_dict()
        for k, v in plan.profile_data.items():
            if k in cur_prof and isinstance(v, str) and v.strip():
                cur_prof[k] = v.strip()
        if plan.avatar_face and not plan.profile_data.get("avatar"):
            cur_prof["avatar"] = plan.avatar_face
        try:
            svc.update_profile(**cur_prof)
            profile_updated = True
        except Exception as exc:
            logger.warning("could not apply imported profile: %s", exc)
    if plan.ui_prefs:
        try:
            (svc.data_dir / "ui-prefs.json").write_text(
                json.dumps(plan.ui_prefs, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
    stats: dict[str, Any] = {
        "ok": True,
        "conversations": len(plan.conversations),
        "events_added": 0,
        "threads_created": 0,
        "threads_merged": 0,
        "messages_indexed": 0,
        "memory_files": plan.memory_files,
        "apps_saved": sum(1 for a in plan.apps),
        "apps_registered": (
            sum(1 for a in plan.apps if a.html_rel) if options.apps in ("restore", "both") else 0
        ),
        "profile_updated": profile_updated,
        "avatar_files": plan.avatar_files,
        "ui_preferences": plan.ui_prefs if plan.ui_prefs else None,
        "skipped_files": plan.skipped_files,
        "duplicates": plan.duplicates,
        "note": plan.note,
    }
    titles = {normalize_title(t.title): t.id for t in svc.threads.values() if t.title.strip()}
    personal_db = svc.data_dir / "personal.db"
    for conv in plan.conversations:
        if options.restore_chats:
            thread_id = _apply_conversation(svc, conv, options, titles, stats)
        else:
            thread_id = f"import:{slugify(conv.title)}"
        if options.background_memory and thread_id:
            stats["messages_indexed"] += index_conversation_turns(personal_db, thread_id, conv)
    ledger = ImportLedger(svc.data_dir / "imports.db")
    ledger.open()
    try:
        for digest, kind in plan.ledger_marks:
            ledger.mark(digest, kind, plan.option_key, "import")
        ledger.commit()
    finally:
        ledger.close()
    return stats


def _event_hash(ts: str, kind: str, text: str) -> str:
    return hashlib.sha256(f"{ts}|{kind}|{text}".encode()).hexdigest()[:16]


def _apply_conversation(
    svc: MuseService,
    conv: Conversation,
    options: ImportOptions,
    titles: dict[str, str],
    stats: dict[str, Any],
) -> str:
    """One conversation becomes (or merges into) a chat; the thread id is returned."""
    turns = [t for t in conv.turns if t.role in ("user", "assistant") and t.text.strip()][
        :MAX_EVENTS_PER_CONVERSATION
    ]
    if conv.kind == "muse_main" and options.main_chat == "replace":
        made = svc.create_thread("Main chat")
        stats["threads_created"] += 1
        tid = made.id
    elif conv.kind == "muse_main" and options.main_chat == "merge" and "main" in svc.threads:
        tid = "main"
        stats["threads_merged"] += 1
    elif conv.kind == "thread" and normalize_title(conv.title) == "main chat":
        # a nanoMuse export's own main chat restores into the main chat here, whatever
        # the main_chat switch says: two main chats would be worse than one
        existed = "main" in svc.threads
        svc.timeline("main")
        main_thread = svc.threads.get("main")
        if main_thread is not None and normalize_title(main_thread.title) == "main":
            svc.rename_thread("main", "Main chat")
        tid = "main"
        if existed:
            stats["threads_merged"] += 1
        else:
            stats["threads_created"] += 1
    else:
        existing = titles.get(normalize_title(conv.title))
        if existing is not None and svc.threads.get(existing) is not None:
            tid = existing
            stats["threads_merged"] += 1
        else:
            made = svc.create_thread(conv.title)
            stats["threads_created"] += 1
            tid = made.id
    thread = svc.threads.get(tid)
    if thread is None:
        return ""
    known = {
        _event_hash(str(ev.get("ts") or ""), str(ev.get("type") or ""), str(ev.get("text") or ""))
        for ev in thread.timeline.events
    }
    events: list[dict[str, Any]] = []
    if conv.kind == "text" and not turns:
        # plain prose with no messages in it: shown in the chat as a note, kept in memory
        body = (conv.body or conv.source).strip()[:20_000]
        events.append(
            {
                "type": "notice",
                "level": "info",
                "text": f"Imported from {conv.source}:\n\n{body}",
                "imported": True,
            }
        )
    for turn in turns:
        if _event_hash(turn.ts, turn.role, turn.text) in known:
            stats["duplicates"] += 1
            continue
        events.append({"type": turn.role, "text": turn.text, "ts": turn.ts, "imported": True})
    if events:
        stats["events_added"] += thread.timeline.merge_sorted(events)
    if turns:
        _insert_agent_messages(thread, turns)
    svc._save_index()
    svc.bus.publish({"kind": "thread", "thread": thread.meta()})
    if conv.kind == "muse_main" and options.main_chat == "replace":
        if "main" not in svc.threads:
            # a main chat must exist for the swap; an empty one is fine, it is set aside
            svc.timeline("main")
        archived = svc.rehome_main("muse-import", restore=tid, keep=True)
        if archived is not None:
            svc.rename_thread(archived.id, "Previous main chat")
            return "main"
        return tid
    return tid


def _insert_agent_messages(thread: Any, turns: list[Turn]) -> int:
    """The agent's own transcript learns the imported messages so a new message in that
    chat continues where it left off. Existing messages stay in the middle; imported ones
    line up around them by time (the thread's start is the boundary)."""
    boundary = parse_timestamp(str(getattr(thread, "created_at", "") or "")) or datetime.now(
        UTC
    ).isoformat(timespec="seconds")
    seen = {
        (str(m.meta.get("imported_ts") or ""), m.content or "")
        for m in thread.agent.messages
        if (m.meta or {}).get("imported")
    }
    before: list[tuple[str, Message]] = []
    after: list[tuple[str, Message]] = []
    for turn in turns:
        if not turn.text.strip():
            continue
        if (turn.ts, turn.text) in seen:
            continue
        message = Message.user(turn.text) if turn.role == "user" else Message.assistant(turn.text)
        message.meta["imported"] = True
        message.meta["imported_ts"] = turn.ts
        if turn.ts <= boundary:
            before.append((turn.ts, message))
        else:
            after.append((turn.ts, message))
    before.sort(key=lambda pair: pair[0])
    after.sort(key=lambda pair: pair[0])
    agent = thread.agent
    agent.messages = [m for _, m in before] + list(agent.messages) + [m for _, m in after]
    agent._save_session()
    return len(before) + len(after)


# ------------------------------------------------------------------ public entry points
def ingest_text(svc: MuseService, text: str, name: str, options: ImportOptions) -> ImportPlan:
    """Stage pasted text in a worker thread (the file side); :func:`apply_plan` turns it
    into chats on the loop."""
    if len(text.encode("utf-8", errors="replace")) > MAX_TEXT_PASTE_BYTES:
        raise ValueError("the pasted text is larger than 32 MB; import it as a file")
    plan = ImportPlan(option_key=options.key())
    _accept_text(svc.data_dir, name or "Imported text", text, options, plan)
    return plan


def import_text(svc: MuseService, text: str, name: str, options: ImportOptions) -> dict[str, Any]:
    """Import pasted text in one call (Settings → Import & export → Paste text, scripts)."""
    plan = ingest_text(svc, text, name, options)
    return apply_plan(svc, plan, options)


def import_file(svc: MuseService, path: Path, name: str, options: ImportOptions) -> dict[str, Any]:
    """Import one uploaded file (Settings → Import & export → Import a file)."""
    plan = ingest_path(svc, path, name, options)
    return apply_plan(svc, plan, options)


__all__ = [
    "IMPORT_UPLOAD_MB",
    "ImportLedger",
    "ImportOptions",
    "apply_plan",
    "import_file",
    "import_text",
    "ingest_path",
    "ingest_text",
    "looks_like_transcript",
    "parse_timestamp",
    "parse_transcript",
]
