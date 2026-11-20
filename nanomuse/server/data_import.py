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
import time
import uuid
import zipfile
from collections.abc import Callable
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

# one timestamp as the archive writes it: "2026-11-07 7:45 PM MST", "2026-10-12 12:32",
# "2026-11-01 12:27:14 MST", "Sat 2026-10-03 14:07:48 MDT" - the clock may be 12- or
# 24-hour, with or without seconds, with or without AM/PM, with or without the zone
# abbreviation, with or without the weekday in front
_TS = (
    r"(?:[A-Za-z]{3},?\s+)?"
    r"\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AP]M)?(?:\s+[A-Z]{2,5})?"
)

HEADER_PATTERNS = (
    # ## User - 2026-10-17 13:18:45 MDT
    re.compile(
        r"^##\s+(?P<speaker>[^\-\n]{1,60}?)\s*-\s*(?P<ts>\S[^\n]{0,60})$",
        re.MULTILINE,
    ),
    # **User - Sat Oct 10, 2026, 9:54:24 AM MDT**
    re.compile(
        r"^\*\*\s*(?P<speaker>[^*\n]{1,60}?)\s*-\s*(?P<ts>[^*\n]{0,60}?)\s*\*\*\s*$",
        re.MULTILINE,
    ),
    # **Tyler is the speaker here, then Sun Oct 11, 2026, 9:15 AM MDT** (the same, em dash)
    re.compile(
        r"^\*\*\s*(?P<speaker>[^*\n]{1,60}?)\s*\u2014\s*(?P<ts>[^*\n]{0,60}?)\s*\*\*\s*$",
        re.MULTILINE,
    ),
    # ### User - 2026-10-17 13:18:45 MDT (the side-chat transcripts)
    re.compile(
        r"^###\s+(?P<speaker>[^\-\n]{1,60}?)\s*-\s*(?P<ts>\S[^\n]{0,60})$",
        re.MULTILINE,
    ),
    # ## User, em dash, 2026-10-17 1:18:45 PM MDT (the export prompt's own example)
    re.compile(
        r"^##\s+(?P<speaker>[^\u2014\n]{1,60}?)\s*\u2014\s*(?P<ts>\S[^\n]{0,60})$",
        re.MULTILINE,
    ),
    # ## 2026-11-07 7:45 PM MST, em dash, Tyler: the time first, then the speaker, which is
    # what the archive's own builder writes (em dash or hyphen, ## or ###)
    re.compile(
        rf"^#{{2,3}}\s+(?P<ts>{_TS})\s*(?:\u2014|-{{1,3}})\s+(?P<speaker>[^\n]{{1,60}})$",
        re.MULTILINE,
    ),
    # ## [2026-11-01 12:27:14 MST] Tyler: the same, bracketed, with no dash
    re.compile(
        rf"^##\s+\[(?P<ts>{_TS})\]\s+(?P<speaker>[^\n]{{1,60}})$",
        re.MULTILINE,
    ),
    # **[2026-11-01 06:37 MST] Tyler:** - bold, bracketed, time first, text on the next
    # lines: the other half of what the archive's transcripts are written in
    re.compile(
        rf"^\*\*\[(?P<ts>{_TS})\]\s*(?P<speaker>[^*\n]{{1,60}}?):\*\*\s*$",
        re.MULTILINE,
    ),
)

# fallback: "User: text" / "Muse: text" lines, no timestamp
PLAIN_LINE = re.compile(
    r"^(?P<speaker>User|You|Me|Human|Muse|Assistant|AI|Bot)\s*:\s*(?P<text>.*)$",
    re.MULTILINE,
)

# Muse's own "Conversation with Muse AI" export: "[2026-10-03 20:07:52] You: Muse", the
# message running on over the following lines until the next stamped line
BRACKET_LINE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:\s*[A-Z]{2,5})?)\]\s+"
    r"(?P<speaker>[^:\n\[\]]{1,40}?):[ \t]?(?P<text>.*)$"
)

# widget placeholders Muse leaves in its own messages: "[[hatch_widget:widget-ca26...]]"
WIDGET_TOKEN = re.compile(r"[ \t]*\[\[hatch_widget:[^\]]*\]\]")


def _parse_bracket_transcript(text: str) -> list[dict[str, str]]:
    """The stamped-line messages of a transcript, or ``[]`` when it is not written that way."""
    raw: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        match = BRACKET_LINE.match(line)
        ts = parse_timestamp(match.group("ts")) if match else ""
        if match and ts:
            if current is not None:
                raw.append(current)
            current = {
                "speaker": _clean_speaker(match.group("speaker")),
                "ts": ts,
                "text": match.group("text") + "\n",
            }
        elif current is not None:
            current["text"] += line + "\n"
    if current is not None:
        raw.append(current)
    for entry in raw:
        entry["text"] = WIDGET_TOKEN.sub("", entry["text"]).strip()
    kept = [entry for entry in raw if entry["text"]]
    return kept if len(kept) >= 2 else []


DATE_IN_NAME = re.compile(r"(?<!\d)(20\d{2})[-_ ](\d{2})[-_ ](\d{2})(?!\d)")

_UNSAFE = re.compile(r"[^A-Za-z0-9_. -]+")


@dataclass(frozen=True)
class ImportOptions:
    """The switches the Import & export screen offers."""

    restore_chats: bool = True
    background_memory: bool = True
    apps: str = "both"  # "save" | "restore" | "both" | "neither"
    main_chat: str = "side"  # "side" | "replace" | "merge"
    # Keep the uploaded file itself, byte for byte, under workspace/imports/ so the
    # original can be re-checked (by the person or the agent) after the transfer.
    keep_copy: bool = True

    def __post_init__(self) -> None:
        if self.apps not in ("save", "restore", "both", "neither"):
            raise ValueError(f"unknown apps option: {self.apps}")
        if self.main_chat not in ("side", "replace", "merge"):
            raise ValueError(f"unknown main chat option: {self.main_chat}")

    def key(self) -> str:
        # the copy switch changes no content, so it is not part of the ledger identity
        return (
            f"{int(self.restore_chats)}|{int(self.background_memory)}|{self.apps}|{self.main_chat}"
        )


@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str
    ts: str = FLOOR_TS  # ISO UTC
    source_msg_id: str = ""


@dataclass
class Conversation:
    title: str
    kind: str  # "muse_main" | "transcript" | "thread" | "text"
    turns: list[Turn]
    source: str = ""
    body: str = ""  # the raw text when there were no parseable messages
    external_id: str = ""


@dataclass
class ImportedApp:
    slug: str
    title: str
    html_rel: str  # workspace-relative path of the page to serve, "" when none
    files: int
    # true when the page was registered as an interactive app at /apps/<slug>
    registered: bool = False


@dataclass
class ImportPlan:
    """What the archive contained, staged on disk; ``apply_plan`` turns it into chats."""

    conversations: list[Conversation] = field(default_factory=list)
    apps: list[ImportedApp] = field(default_factory=list)
    memory_files: int = 0
    files_updated: int = 0
    skipped_files: int = 0
    duplicates: int = 0
    option_key: str = ""
    ledger_marks: list[tuple[str, str]] = field(default_factory=list)  # (digest, kind)
    note: str = ""
    profile_data: dict[str, Any] = field(default_factory=dict)
    ui_prefs: dict[str, Any] = field(default_factory=dict)
    avatar_files: int = 0
    avatar_face: str = ""
    avatar_faces: set[str] = field(default_factory=set)
    avatar_models: dict[str, dict[str, Any]] = field(default_factory=dict)
    manifest_transcripts: set[str] = field(default_factory=set)
    # the file this plan came from, for the verification report's name
    source_name: str = ""
    # the archive folder that carries its own counts (``minimuse-archive``), "" when the
    # export has none: only members inside it are counted against the declared totals
    archive_root: str = ""
    # counts the archive declares about itself (profile/muse-profile.json's ``counts``)
    declared_counts: dict[str, int] = field(default_factory=dict)
    # what actually landed, per declared bucket, for the same comparison
    staged_counts: dict[str, int] = field(default_factory=dict)
    # avatar-model.json: the map from animation state to file, read for the mood copies
    avatar_model: dict[str, Any] = field(default_factory=dict)
    avatar_materialized: int = 0
    # the import ledger, open for the length of this import: what has landed before, so a
    # second run of the same archive stores nothing twice
    ledger: ImportLedger | None = None
    # transcripts the archive carried twice (the canonical copy under chat-transcripts/ and
    # Muse's own working copy): one chat each, merged by the chat id written in the file
    chats_by_id: dict[str, Conversation] = field(default_factory=dict)


# ------------------------------------------------------------------ parsing
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    # the archive's own headers write the clock without a leading zero and with AM/PM:
    # "2026-11-05 5:49 PM MST"
    "%Y-%m-%d %I:%M:%S %p",
    "%Y-%m-%d %I:%M %p",
    # the bracketed bold headers carry the weekday: "Sat 2026-10-03 14:07:48"
    "%a %Y-%m-%d %H:%M:%S",
    "%a %Y-%m-%d %H:%M",
    "%a %b %d, %Y, %I:%M:%S %p",
    "%a %b %d, %Y, %I:%M %p",
    "%b %d, %Y, %I:%M:%S %p",
    "%b %d, %Y, %I:%M %p",
    "%A, %B %d, %Y, %I:%M:%S %p",
    "%A, %B %d, %Y, %I:%M %p",
    "%B %d, %Y, %I:%M:%S %p",
    "%B %d, %Y, %I:%M %p",
    "%A, %B %d, %Y at %I:%M %p",
    "%B %d, %Y at %I:%M %p",
)


def parse_timestamp(raw: str) -> str:
    """One timestamp as ISO UTC, or ``""`` when it cannot be read.

    Muse writes several shapes: ``2026-10-17 13:18:45 MDT``, ``Sat Oct 10, 2026, 9:54:24 AM
    MDT`` and the same without seconds (``Sat Oct 31, 2026, 5:35 PM MDT``), plus the long
    ``Saturday, October 31, 2026, 5:35 PM MDT``. The timezone abbreviation is read from
    the table; anything ISO goes through Python's parser."""
    raw = raw.strip().strip(".,;[]()")
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
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
    raw = re.sub(r"\s+", " ", raw)
    for fmt in _TIME_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        aware = dt.replace(tzinfo=timezone(timedelta(hours=offset)))
        return aware.astimezone(UTC).isoformat(timespec="seconds")
    return ""


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _speaker_role(speaker: str) -> tuple[str, str]:
    """(role, prefix) for one speaker on its own: the side of the chat, and the name to
    keep on the line when the name is nobody's known side."""
    words = _words(speaker)
    if words & set(_USER_WORDS):
        return "user", ""
    if words & set(_ASSISTANT_WORDS):
        return "assistant", ""
    return "assistant", f"{speaker.strip()}: "


def _resolve_roles(speakers: list[str]) -> dict[str, tuple[str, str]]:
    """The side of the chat of every speaker in one transcript.

    A name that says which side it is (User, You, Muse, Assistant...) is taken at its word.
    When there are exactly two people and one is known, the other is the opposite side:
    ``Tyler`` and ``Muse`` are a person and an assistant, whatever ``Tyler`` is called. Two
    unknown names: the first to speak is the person. Anyone else keeps their name on the
    line, on the assistant's side."""
    ordered: list[str] = []
    for speaker in speakers:
        if speaker not in ordered:
            ordered.append(speaker)
    roles: dict[str, tuple[str, str]] = {}
    unknown: list[str] = []
    for speaker in ordered:
        words = _words(speaker)
        if words & set(_USER_WORDS):
            roles[speaker] = ("user", "")
        elif words & set(_ASSISTANT_WORDS):
            roles[speaker] = ("assistant", "")
        else:
            unknown.append(speaker)
    if len(ordered) == 2 and len(unknown) == 1:
        other = next(s for s in ordered if s != unknown[0])
        roles[unknown[0]] = ("assistant" if roles[other][0] == "user" else "user", "")
    elif len(ordered) == 2 and len(unknown) == 2:
        roles[unknown[0]] = ("user", "")
        roles[unknown[1]] = ("assistant", "")
    else:
        for speaker in unknown:
            roles[speaker] = ("assistant", f"{speaker.strip()}: ")
    return roles


# "**Tyler:** text" / "**Tyler**: text", anywhere in the text (voice-call transcripts often
# arrive with the line breaks flattened, so the markers are found inline)
INLINE_SPEAKER = re.compile(
    r"\*\*(?P<speaker>[A-Za-z][A-Za-z0-9 _.'’-]{0,30}?)(?::\*\*|\*\*:)\s*"
)


def _clean_speaker(raw: str) -> str:
    """``Tyler (User)`` and ``**Tyler**`` are both just ``Tyler``'s side of the chat."""
    return re.sub(r"\s+", " ", raw.replace("*", "")).strip()


def looks_like_transcript(text: str) -> bool:
    """A text that carries speaker-and-time headers, not a plain document."""
    for pattern in HEADER_PATTERNS:
        for match in pattern.finditer(text):
            if parse_timestamp(match.group("ts")):
                return True
    if len(INLINE_SPEAKER.findall(text)) >= 4:
        return True
    if _parse_bracket_transcript(text):
        return True
    return bool(PLAIN_LINE.search(text))


def _spread(turns: list[Turn], source: str) -> None:
    """Turns with no time at all get one, a second apart, in order: the date in the file's
    name when there is one, else the floor. Nothing then sorts out of order."""
    stamp = DATE_IN_NAME.search(source or "")
    try:
        base = (
            datetime(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3)), tzinfo=UTC)
            if stamp
            else datetime(1, 1, 1, tzinfo=UTC)
        )
    except ValueError:
        base = datetime(1, 1, 1, tzinfo=UTC)
    for i, turn in enumerate(turns):
        if turn.ts == FLOOR_TS or not turn.ts:
            turn.ts = (base + timedelta(seconds=i)).isoformat(timespec="seconds")


def _pick_header_pattern(text: str) -> tuple[re.Pattern[str] | None, set[str]]:
    """The header shape this transcript is written in (the one with the most lines that
    carry a readable time), and every speaker any shape gave a readable time to. A file
    may mix shapes - the archive's transcripts switch halfway through - so the speakers
    are gathered from all of them, not only from the winner."""
    best: re.Pattern[str] | None = None
    best_count = 0
    speakers: set[str] = set()
    for candidate in HEADER_PATTERNS:
        count = 0
        for match in candidate.finditer(text):
            if parse_timestamp(match.group("ts")):
                count += 1
                speakers.add(_clean_speaker(match.group("speaker")).lower())
        if count > best_count:
            best, best_count = candidate, count
    if best is None:
        # headers whose times cannot be read at all: still headers when the speaker is a
        # known side of the chat ("## Muse - whenever")
        known: set[str] = set()
        for candidate in HEADER_PATTERNS:
            for match in candidate.finditer(text):
                speaker = _clean_speaker(match.group("speaker"))
                if _words(speaker) & (set(_USER_WORDS) | set(_ASSISTANT_WORDS)):
                    known.add(speaker.lower())
        if known:
            return HEADER_PATTERNS[0], known
    return best, speakers


def parse_transcript(text: str, source: str = "") -> list[Turn]:
    """The messages of a Muse transcript, in order, with their timestamps and the right
    side of the chat for each speaker.

    Recognizes the ``## Speaker — timestamp`` sections, the ``**Speaker - timestamp**``
    bold headers (the shape of Muse's own chat exports), ``### Speaker — timestamp``
    blocks, inline ``**Speaker:** text`` voice-call transcripts and bare ``User:`` lines.
    Text before the first header (the title and metadata) is left out. Who is the person
    and who the assistant is worked out for the whole transcript at once."""
    raw: list[dict[str, str]] = _parse_bracket_transcript(text)  # {speaker, ts, text}
    pattern, header_speakers = (None, set()) if raw else _pick_header_pattern(text)
    if raw:
        pass
    elif pattern is not None:
        last_ts = ""
        current: dict[str, str] | None = None
        # the file's own shape first, then the others: a transcript may switch shape
        # halfway through (the archive's do), and every message counts
        candidates = (pattern, *(p for p in HEADER_PATTERNS if p is not pattern))
        for line in text.splitlines():
            stripped = line.strip()
            match = None
            for candidate in candidates:
                found = candidate.match(stripped)
                if found is None:
                    continue
                when = parse_timestamp(found.group("ts"))
                who = _clean_speaker(found.group("speaker"))
                known_side = bool(
                    _words(who) & (set(_USER_WORDS) | set(_ASSISTANT_WORDS))
                ) or who.lower() in header_speakers
                if when or known_side:
                    match = found
                    break
            if match is not None:
                speaker = _clean_speaker(match.group("speaker"))
                ts = parse_timestamp(match.group("ts"))
                known_side = bool(
                    _words(speaker) & (set(_USER_WORDS) | set(_ASSISTANT_WORDS))
                ) or speaker.lower() in header_speakers
                if ts or known_side:
                    last_ts = ts or last_ts
                    if current is not None and current["text"].strip():
                        raw.append(current)
                    current = {"speaker": speaker, "ts": last_ts or FLOOR_TS, "text": ""}
                    continue
            if current is not None:
                if line.strip() in ("---", "***"):
                    continue
                current["text"] += line + "\n"
        if current is not None and current["text"].strip():
            raw.append(current)
    else:
        inline = list(INLINE_SPEAKER.finditer(text))
        distinct = {_clean_speaker(m.group("speaker")).lower() for m in inline}
        if len(inline) >= 3 and len(distinct) <= 4:
            for i, match in enumerate(inline):
                end = inline[i + 1].start() if i + 1 < len(inline) else len(text)
                body = text[match.end() : end].strip()
                if body:
                    raw.append(
                        {
                            "speaker": _clean_speaker(match.group("speaker")),
                            "ts": FLOOR_TS,
                            "text": body,
                        }
                    )
        else:
            plain: dict[str, str] | None = None
            for line in text.splitlines():
                stripped = line.strip()
                match = PLAIN_LINE.match(stripped)
                if match:
                    if plain is not None and plain["text"].strip():
                        raw.append(plain)
                    plain = {
                        "speaker": match.group("speaker"),
                        "ts": FLOOR_TS,
                        "text": match.group("text") + "\n",
                    }
                elif plain is not None and stripped not in ("---", "***"):
                    plain["text"] += line + "\n"
            if plain is not None and plain["text"].strip():
                raw.append(plain)
    if not raw:
        return []
    roles = _resolve_roles([entry["speaker"] for entry in raw])
    turns = [
        Turn(
            role=roles[entry["speaker"]][0],
            text=(roles[entry["speaker"]][1] + entry["text"].strip()).strip(),
            ts=entry["ts"] or FLOOR_TS,
        )
        for entry in raw
        if entry["text"].strip()
    ]
    if all(turn.ts == FLOOR_TS for turn in turns):
        _spread(turns, source)
    return turns


_GENERIC_TITLE = re.compile(r"^(?:chat\s+)?transcript$", re.IGNORECASE)


def _clean_heading(heading: str) -> str:
    """The chat's own name out of a transcript's first heading: ``Chat transcript — Begin a
    chat session`` and ``Chat Transcript — "Begin a chat session" (original main chat)``
    are both ``Begin a chat session``; ``Voice Call Chat Transcript`` is ``Voice Call``."""
    title = re.sub(r"[*_`]+", "", heading).strip()
    split = re.match(
        r"^(?P<lead>.*?)\s*(?:chat\s+)?transcript\b\s*(?:[—–:-]+\s*(?P<rest>.*))?$",
        title,
        re.IGNORECASE,
    )
    if split is not None:
        rest = (split.group("rest") or "").strip()
        lead = (split.group("lead") or "").strip()
        title = rest or lead
    title = re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()
    title = title.strip("\"'“”‘’ ")
    return "" if _GENERIC_TITLE.match(title) else title


def title_from_text(text: str, name: str) -> str:
    """A chat's title: the name inside its transcript when it has one, else the file's. A
    voice call gets its day, so two calls are never the same chat."""
    title = _title_from_text(text, name)
    if re.fullmatch(r"voice call( chat)?( transcript)?", title.strip().lower()):
        stamp = DATE_IN_NAME.search(name or "")
        if stamp is not None:
            try:
                day = datetime(int(stamp.group(1)), int(stamp.group(2)), int(stamp.group(3)))
                return f"Voice call - {day:%b} {day.day}"
            except ValueError:
                pass
        return "Voice call"
    return title


def _title_from_text(text: str, name: str) -> str:
    head = text[:4000]
    first = head.lstrip().split("\n", 1)[0].strip()
    named = re.match(r"^Conversation with\s+(.{1,60})$", first, re.IGNORECASE)
    if named is not None and not first.startswith("["):
        return f"Chat with {named.group(1).strip()}"[:80]
    heading = re.search(r"^#\s+(.+?)\s*$", head, re.MULTILINE)
    if heading is not None:
        title = _clean_heading(heading.group(1))
        if title:
            return title[:80]
    labelled = re.search(
        r"^\s*[-*]?\s*\*\*(?:Chat|Title|Conversation)\s*:\*\*\s*(.+?)\s*$",
        head,
        re.MULTILINE | re.IGNORECASE,
    )
    if labelled is not None:
        title = re.sub(r"\s*\([^)]*\)\s*$", "", labelled.group(1)).strip("\"'“”‘’ *")
        if title:
            return title[:80]
    return title_from_name(name)


_FILE_EXT = re.compile(r"\.(?:md|markdown|txt|json|csv|log|html?|bak)$", re.IGNORECASE)

# "- Chat ID: e7ce5fac-3702-4b7f-b7c3-466693085b81" in a transcript's metadata block
_CHAT_ID = re.compile(r"^\s*[-*]?\s*Chat\s+ID\s*:\s*([0-9a-fA-F][0-9a-fA-F-]{5,})\s*$", re.MULTILINE)


def _chat_id_of(text: str) -> str:
    """The chat id a transcript carries, so two copies of one conversation are one chat."""
    match = _CHAT_ID.search(text[:4000])
    return match.group(1).lower() if match else ""


def _absorb_turns(conv: Conversation, turns: list[Turn]) -> None:
    """The messages of a second copy of a chat join the first, once each, in time order."""
    seen = {(t.role, t.ts, t.text) for t in conv.turns}
    for turn in turns:
        key = (turn.role, turn.ts, turn.text)
        if key in seen:
            continue
        seen.add(key)
        conv.turns.append(turn)
    conv.turns.sort(key=lambda t: t.ts)


def title_from_name(name: str) -> str:
    """A title from a file name: no folders, no extension, no leading date, no
    ``-chat-transcript``, dashes and underscores as spaces."""
    stem = name.replace("\\", "/").rsplit("/", 1)[-1]
    while _FILE_EXT.search(stem):
        stem = _FILE_EXT.sub("", stem)
    stem = DATE_IN_NAME.sub(" ", stem)
    stem = re.sub(r"[-_ ]chat[-_]?transcript", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"[-_ ]transcript$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem.replace("-", " ").replace("_", " ")).strip()
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


# ------------------------------------------------------------------ multi-AI chat export parsers
def _ts_from_any(raw: Any) -> str:
    """Convert Unix seconds/ms, Mongo $date dict, or ISO/human timestamp string to ISO UTC."""
    if raw is None or raw == "":
        return FLOOR_TS
    if isinstance(raw, dict):
        if "$date" in raw:
            return _ts_from_any(raw["$date"])
        if "$numberLong" in raw:
            return _ts_from_any(raw["$numberLong"])
        return FLOOR_TS
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v <= 0:
            return FLOOR_TS
        if v > 1e12:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, UTC).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return FLOOR_TS
    s = str(raw).strip()
    if not s:
        return FLOOR_TS
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return _ts_from_any(float(s))
    return parse_timestamp(s)


def _extract_content_text(content: Any) -> str:
    """Normalize string, ChatGPT content.parts, Claude content blocks, or nested dicts into text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        pieces: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    pieces.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or item.get("value")
                if isinstance(t, str) and t.strip():
                    pieces.append(t)
                elif item.get("content_type") in ("image_asset_pointer", "image") or item.get("type") == "image":
                    pieces.append("[Image attachment]")
        return "\n".join(pieces).strip()
    if isinstance(content, dict):
        if "parts" in content:
            return _extract_content_text(content["parts"])
        if "text" in content and isinstance(content["text"], str):
            return content["text"].strip()
        if "content" in content:
            return _extract_content_text(content["content"])
    return ""


def _strip_html_to_text(raw_html: str) -> str:
    import html as _html

    s = re.sub(r"<(br|p|div|li|tr|h[1-6])[^>]*>", "\n", raw_html, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    lines = [ln.rstrip() for ln in s.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _normalize_ai_role(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s in ("user", "human", "me", "person", "prompt"):
        return "user"
    if s in ("assistant", "model", "ai", "bot", "gpt", "chatgpt", "claude", "gemini", "grok", "muse", "response"):
        return "assistant"
    return ""


def _parse_chatgpt_conversation(obj: dict[str, Any], source: str) -> Conversation | None:
    mapping = obj.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        return None
    # Walk active branch from current_node back to root so edited/regenerated branches stay clean
    ordered_nodes: list[tuple[str, dict[str, Any]]] = []
    cur = obj.get("current_node")
    if isinstance(cur, str) and cur in mapping:
        seen_ids: set[str] = set()
        while isinstance(cur, str) and cur in mapping and cur not in seen_ids:
            seen_ids.add(cur)
            node = mapping[cur]
            if isinstance(node, dict):
                ordered_nodes.append((cur, node))
                cur = node.get("parent")
            else:
                break
        ordered_nodes.reverse()
    else:
        for nid, node in mapping.items():
            if isinstance(node, dict):
                ordered_nodes.append((str(nid), node))
        ordered_nodes.sort(
            key=lambda pair: float(((pair[1].get("message") or {}).get("create_time") or 0) or 0)
        )

    turns: list[Turn] = []
    for nid, node in ordered_nodes:
        msg = node.get("message")
        if not isinstance(msg, dict):
            continue
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        if meta.get("is_visually_hidden_from_conversation"):
            continue
        author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
        role = _normalize_ai_role(author.get("role"))
        if not role:
            continue
        text = _extract_content_text(msg.get("content"))
        if not text:
            continue
        ts = _ts_from_any(msg.get("create_time") or msg.get("update_time"))
        msg_id = str(msg.get("id") or nid or "").strip()
        turns.append(Turn(role=role, text=text, ts=ts, source_msg_id=msg_id))
    if not turns:
        return None
    title = str(obj.get("title") or "").strip() or title_from_name(source)
    ext_id = str(obj.get("id") or obj.get("conversation_id") or "").strip()
    return Conversation(
        title=title,
        kind="transcript",
        turns=turns,
        source=source,
        external_id=f"chatgpt:{ext_id}" if ext_id else "",
    )


def _parse_claude_conversation(obj: dict[str, Any], source: str) -> Conversation | None:
    msgs = obj.get("chat_messages")
    if not isinstance(msgs, list) or not msgs:
        return None
    turns: list[Turn] = []
    for idx, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        role = _normalize_ai_role(m.get("sender") or m.get("role"))
        if not role:
            continue
        text = _extract_content_text(m.get("text") or m.get("content"))
        if not text:
            continue
        ts = _ts_from_any(m.get("created_at") or m.get("updated_at"))
        msg_id = str(m.get("uuid") or m.get("id") or f"c_{idx}").strip()
        turns.append(Turn(role=role, text=text, ts=ts, source_msg_id=msg_id))
    if not turns:
        return None
    title = str(obj.get("name") or obj.get("title") or "").strip() or title_from_name(source)
    ext_id = str(obj.get("uuid") or obj.get("id") or "").strip()
    return Conversation(
        title=title,
        kind="transcript",
        turns=turns,
        source=source,
        external_id=f"claude:{ext_id}" if ext_id else "",
    )


def _parse_grok_conversation(entry: dict[str, Any], source: str) -> Conversation | None:
    conv_meta = entry.get("conversation") if isinstance(entry.get("conversation"), dict) else entry
    resps = entry.get("responses")
    if not isinstance(resps, list) or not resps:
        return None
    turns: list[Turn] = []
    for idx, item in enumerate(resps):
        if not isinstance(item, dict):
            continue
        resp = item.get("response") if isinstance(item.get("response"), dict) else item
        role = _normalize_ai_role(resp.get("sender") or resp.get("role"))
        if not role:
            continue
        text = _extract_content_text(resp.get("message") or resp.get("text") or resp.get("content"))
        if not text:
            continue
        ts = _ts_from_any(resp.get("create_time") or resp.get("created_at"))
        msg_id = str(resp.get("_id") or resp.get("id") or f"g_{idx}").strip()
        turns.append(Turn(role=role, text=text, ts=ts, source_msg_id=msg_id))
    if not turns:
        return None
    title = str(conv_meta.get("title") or "").strip() or title_from_name(source)
    ext_id = str(conv_meta.get("id") or conv_meta.get("_id") or "").strip()
    return Conversation(
        title=title,
        kind="transcript",
        turns=turns,
        source=source,
        external_id=f"grok:{ext_id}" if ext_id else "",
    )


def _parse_gemini_takeout_json(items: list[Any], source: str) -> list[Conversation]:
    """Parse Google Takeout Gemini Apps ``MyActivity.json`` into daily conversations."""
    by_day: dict[str, list[Turn]] = {}
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        header = str(item.get("header") or "").lower()
        title_raw = str(item.get("title") or "").strip()
        if "gemini" not in header and not title_raw.lower().startswith("prompted "):
            continue
        ts = _ts_from_any(item.get("time"))
        day_key = ts[:10] if ts != FLOOR_TS else "undated"
        prompt_text = re.sub(r"^Prompted\s+", "", title_raw, flags=re.IGNORECASE).strip()
        if prompt_text:
            by_day.setdefault(day_key, []).append(
                Turn(role="user", text=prompt_text, ts=ts, source_msg_id=f"gem_u_{day_key}_{idx}")
            )
        safe_items = item.get("safeHtmlItem")
        if isinstance(safe_items, list):
            ans_parts = [
                _strip_html_to_text(str(si.get("html") or ""))
                for si in safe_items
                if isinstance(si, dict) and si.get("html")
            ]
            ans = "\n\n".join(p for p in ans_parts if p).strip()
            if ans:
                by_day.setdefault(day_key, []).append(
                    Turn(role="assistant", text=ans, ts=ts, source_msg_id=f"gem_a_{day_key}_{idx}")
                )
    convs: list[Conversation] = []
    for day_key, turns in sorted(by_day.items()):
        turns.sort(key=lambda t: t.ts)
        convs.append(
            Conversation(
                title=f"Gemini ({day_key})" if day_key != "undated" else "Gemini history",
                kind="transcript",
                turns=turns,
                source=source,
                external_id=f"gemini:{day_key}",
            )
        )
    return convs


def _parse_generic_messages_list(
    msgs: list[Any], title: str, source: str, ext_id: str = ""
) -> Conversation | None:
    turns: list[Turn] = []
    for idx, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        role = _normalize_ai_role(
            m.get("role")
            or m.get("sender")
            or (m.get("author") if isinstance(m.get("author"), str) else (m.get("author") or {}).get("role"))
            or m.get("type")
        )
        if not role:
            continue
        text = _extract_content_text(
            m.get("content") if m.get("content") is not None else (m.get("text") or m.get("message") or m.get("parts"))
        )
        if not text:
            continue
        ts = _ts_from_any(m.get("ts") or m.get("timestamp") or m.get("created_at") or m.get("create_time"))
        msg_id = str(m.get("id") or m.get("uuid") or m.get("mid") or "").strip()
        turns.append(Turn(role=role, text=text, ts=ts, source_msg_id=msg_id))
    if not turns:
        return None
    if all(t.ts == FLOOR_TS for t in turns):
        _spread(turns, source)
    return Conversation(
        title=title or title_from_name(source),
        kind="transcript",
        turns=turns,
        source=source,
        external_id=ext_id,
    )


def parse_ai_export_json(text: str, source: str = "") -> list[Conversation]:
    """Detect and parse ChatGPT, Claude, Gemini Takeout, Grok, Perplexity, or generic AI JSON
    chat exports into structured Conversations with persistent external_id & source_msg_id."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return []

    out: list[Conversation] = []
    # Grok wrapper: {"conversations": [{"conversation": {...}, "responses": [...]}]}
    if isinstance(data, dict):
        if isinstance(data.get("mapping"), dict):
            c = _parse_chatgpt_conversation(data, source)
            if c:
                return [c]
        if isinstance(data.get("chat_messages"), list):
            c = _parse_claude_conversation(data, source)
            if c:
                return [c]
        if isinstance(data.get("responses"), list):
            c = _parse_grok_conversation(data, source)
            if c:
                return [c]
        if isinstance(data.get("conversations"), list):
            for idx, item in enumerate(data["conversations"]):
                if not isinstance(item, dict):
                    continue
                c = (
                    _parse_chatgpt_conversation(item, source)
                    or _parse_claude_conversation(item, source)
                    or _parse_grok_conversation(item, source)
                )
                if c is None:
                    msgs = item.get("messages") or item.get("turns") or item.get("history")
                    if isinstance(msgs, list):
                        cid = str(item.get("id") or item.get("uuid") or f"conv_{idx}")
                        c = _parse_generic_messages_list(
                            msgs,
                            str(item.get("title") or item.get("name") or f"Chat {idx + 1}"),
                            source,
                            ext_id=f"json:{cid}",
                        )
                if c:
                    out.append(c)
            return out
        msgs = data.get("messages") or data.get("turns") or data.get("events") or data.get("history")
        if isinstance(msgs, list):
            ext = str(data.get("id") or data.get("uuid") or data.get("chat_id") or "").strip()
            c = _parse_generic_messages_list(
                msgs,
                str(data.get("title") or data.get("name") or title_from_name(source)),
                source,
                ext_id=f"json:{ext}" if ext else "",
            )
            if c:
                return [c]
        return []

    if isinstance(data, list) and data:
        first = next((x for x in data if isinstance(x, dict)), None)
        if first is None:
            return []
        # Google Takeout Gemini MyActivity.json
        if "safeHtmlItem" in first or (
            isinstance(first.get("header"), str) and "gemini" in first["header"].lower()
        ):
            gem = _parse_gemini_takeout_json(data, source)
            if gem:
                return gem
        # ChatGPT / Claude / Grok list of conversations
        if "mapping" in first or "chat_messages" in first or "responses" in first or "messages" in first:
            for idx, item in enumerate(data):
                if not isinstance(item, dict):
                    continue
                c = (
                    _parse_chatgpt_conversation(item, source)
                    or _parse_claude_conversation(item, source)
                    or _parse_grok_conversation(item, source)
                )
                if c is None and isinstance(item.get("messages"), list):
                    cid = str(item.get("id") or item.get("uuid") or f"conv_{idx}")
                    c = _parse_generic_messages_list(
                        item["messages"],
                        str(item.get("title") or item.get("name") or f"Chat {idx + 1}"),
                        source,
                        ext_id=f"json:{cid}",
                    )
                if c:
                    out.append(c)
            if out:
                return out
        # Flat list of messages
        c = _parse_generic_messages_list(data, title_from_name(source), source)
        if c:
            return [c]
    return []


def parse_takeout_html(text: str, source: str = "") -> list[Conversation]:
    """Parse Google Takeout Gemini HTML (MyActivity.html) into structured conversations."""
    if "outer-cell" not in text or "Prompted" not in text:
        return []
    blocks = re.split(r'<div class="outer-cell\b', text)
    by_day: dict[str, list[Turn]] = {}
    for idx, blk in enumerate(blocks[1:]):
        plain = _strip_html_to_text(blk)
        m = re.search(r"Prompted\s+(.+?)(?:\n|$)", plain)
        if not m:
            continue
        prompt_text = m.group(1).strip()
        rest = plain[m.end() :].strip()
        ts = FLOOR_TS
        lines = [ln.strip() for ln in rest.splitlines() if ln.strip()]
        ans_lines: list[str] = []
        for ln in lines:
            if ts == FLOOR_TS:
                cand = parse_timestamp(ln)
                if cand != FLOOR_TS:
                    ts = cand
                    continue
            if ln.lower().startswith(("products:", "gemini apps")):
                continue
            ans_lines.append(ln)
        day_key = ts[:10] if ts != FLOOR_TS else "undated"
        by_day.setdefault(day_key, []).append(
            Turn(role="user", text=prompt_text, ts=ts, source_msg_id=f"gemh_u_{day_key}_{idx}")
        )
        ans = "\n".join(ans_lines).strip()
        if ans:
            by_day.setdefault(day_key, []).append(
                Turn(role="assistant", text=ans, ts=ts, source_msg_id=f"gemh_a_{day_key}_{idx}")
            )
    convs: list[Conversation] = []
    for day_key, turns in sorted(by_day.items()):
        turns.sort(key=lambda t: t.ts)
        convs.append(
            Conversation(
                title=f"Gemini ({day_key})" if day_key != "undated" else "Gemini history",
                kind="transcript",
                turns=turns,
                source=source,
                external_id=f"gemini:{day_key}",
            )
        )
    return convs


# ------------------------------------------------------------------ the ledger
class ImportLedger:
    """What has already been imported, so the same archive never lands twice and updated
    re-exports merge & update previously-imported threads and files cleanly in place."""

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
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS imported_threads ("
                "external_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
                "title TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS imported_files ("
                "rel_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, updated_at TEXT NOT NULL)"
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

    def get_thread(self, external_id: str) -> str | None:
        if not external_id or self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT thread_id FROM imported_threads WHERE external_id = ?",
            (external_id,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def remember_thread(self, external_id: str, thread_id: str, title: str) -> None:
        if not external_id or not thread_id or self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO imported_threads (external_id, thread_id, title, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (external_id, thread_id, title[:200], datetime.now(UTC).isoformat(timespec="seconds")),
        )

    def get_file_hash(self, rel_path: str) -> str | None:
        if not rel_path or self._conn is None:
            return None
        row = self._conn.execute(
            "SELECT sha256 FROM imported_files WHERE rel_path = ?",
            (rel_path,),
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def remember_file_hash(self, rel_path: str, sha256: str) -> None:
        if not rel_path or not sha256 or self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO imported_files (rel_path, sha256, updated_at) VALUES (?, ?, ?)",
            (rel_path, sha256, datetime.now(UTC).isoformat(timespec="seconds")),
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
    """Every imported message into ``messages_fts``, so ``deep_memory`` finds it.

    Unchanged messages already indexed are skipped; edited messages matching the same
    ``source_msg_id`` or exact timestamp+role are replaced cleanly in place."""
    conn = _open_personal(db_path)
    if conn is None:
        return 0
    try:
        indexed = 0
        existing_by_id: dict[str, str] = {}
        existing_by_ts_role: dict[tuple[str, str], tuple[str, str]] = {}
        for row in conn.execute(
            "SELECT turn_id, role, created_at, content FROM messages_fts WHERE thread_id = ?",
            (thread_id,),
        ):
            tid_s = str(row[0])
            r_s = str(row[1] or "")
            ts_s = str(row[2] or "")
            c_s = str(row[3] or "")
            existing_by_id[tid_s] = c_s
            if ts_s and ts_s != FLOOR_TS:
                existing_by_ts_role[(ts_s, r_s)] = (tid_s, c_s)
        for i, turn in enumerate(conv.turns):
            if turn.role not in ("user", "assistant") or not turn.text.strip():
                continue
            if indexed >= cap:
                break
            turn_id = (
                f"imp_{slugify(conv.external_id or conv.title)}_{turn.source_msg_id}"
                if turn.source_msg_id
                else f"imp_{slugify(conv.title)}_{i}"
            )
            if turn_id in existing_by_id:
                if existing_by_id[turn_id] == turn.text:
                    continue
                # Edited message with same deterministic turn_id: replace in place!
                conn.execute(
                    "DELETE FROM messages_fts WHERE thread_id = ? AND turn_id = ?",
                    (thread_id, turn_id),
                )
            elif turn.ts != FLOOR_TS and (turn.ts, turn.role) in existing_by_ts_role:
                old_id, old_text = existing_by_ts_role[(turn.ts, turn.role)]
                if old_text == turn.text:
                    continue
                conn.execute(
                    "DELETE FROM messages_fts WHERE thread_id = ? AND turn_id = ?",
                    (thread_id, old_id),
                )
                turn_id = old_id
            conn.execute(
                "INSERT INTO messages_fts (turn_id, thread_id, role, created_at, content) "
                "VALUES (?, ?, ?, ?, ?)",
                (turn_id, thread_id, turn.role, turn.ts, turn.text),
            )
            existing_by_id[turn_id] = turn.text
            if turn.ts != FLOOR_TS:
                existing_by_ts_role[(turn.ts, turn.role)] = (turn_id, turn.text)
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
        # Replace any prior FTS row for this exact path so updated re-exports never duplicate hits
        conn.execute("DELETE FROM memory_fts WHERE path = ?", (rel,))
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


def _store_memory_file(
    data_dir: Path,
    rel: Path,
    text: str,
    ledger: ImportLedger | None = None,
    plan: ImportPlan | None = None,
) -> str | None:
    """The text as a file under ``data/muse_memory/imports``; returns the stored
    workspace-style relative name, or None when the disk refused.
    If the exact path was previously imported (recorded in the ledger) and updated in a
    newer export, updates it cleanly in place instead of spawning ``-import-N`` duplicates."""
    root = data_dir / "muse_memory" / "imports"
    base_target = root / rel
    new_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    ledger_key = f"memory/imports/{rel.as_posix()}"
    updated_in_place = False
    target = base_target
    if base_target.exists():
        try:
            cur_sha = _sha256_file(base_target)
        except OSError:
            cur_sha = ""
        if cur_sha == new_sha:
            if plan is not None:
                plan.duplicates += 1
            return None
        prev_imported = ledger.get_file_hash(ledger_key) if ledger is not None else None
        if prev_imported and cur_sha == prev_imported:
            # Previously imported file updated in a newer export -> overwrite cleanly in place
            target = base_target
            updated_in_place = True
        else:
            target = _unique_target(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(text, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not store imported memory file: %s", exc)
        return None
    stored_rel = f"memory/imports/{target.relative_to(root).as_posix()}"
    if ledger is not None:
        ledger.remember_file_hash(stored_rel, new_sha)
    if updated_in_place and plan is not None:
        plan.files_updated += 1
    return stored_rel


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _store_app_stream(
    workspace: Path,
    root: Path,
    rel: Path,
    archive: zipfile.ZipFile,
    member: str,
    plan: ImportPlan,
) -> tuple[str, str, str]:
    """One file from the archive, streamed to ``workspace/<root>/<rel>`` in chunks (so a
    file of any size costs no memory); returns ``(workspace-relative path, sha256, status)``.
    If a previously imported file is updated in a newer re-export, overwrites it cleanly in
    place (no ``-import-2..-20`` clutter) while never overwriting locally user-modified files."""
    out_rel = root / rel
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
        return "", digest.hexdigest(), "failed"
    sha = digest.hexdigest()
    ledger_key = out_rel.as_posix()
    updated_in_place = False
    if target.exists():
        try:
            cur_sha = _sha256_file(target)
            same = target.stat().st_size == tmp.stat().st_size and cur_sha == sha
        except OSError:
            cur_sha = ""
            same = False
        if same:
            tmp.unlink(missing_ok=True)
            if plan.ledger is not None:
                plan.ledger.remember_file_hash(ledger_key, sha)
            plan.duplicates += 1
            return "", sha, "duplicate"
        prev_imported = plan.ledger.get_file_hash(ledger_key) if plan.ledger is not None else None
        if prev_imported and cur_sha == prev_imported:
            # File was previously placed by an earlier import and has now been updated in a new export
            updated_in_place = True
        else:
            for n in range(2, 1000):
                alt = target.with_name(f"{target.stem}-import-{n}{target.suffix}")
                if not alt.exists():
                    target = alt
                    out_rel = root / target.relative_to(workspace / root)
                    ledger_key = out_rel.as_posix()
                    break
            else:
                tmp.unlink(missing_ok=True)
                return "", sha, "failed"
    try:
        tmp.replace(target)
    except OSError as exc:
        logger.warning("could not store imported app file: %s", exc)
        tmp.unlink(missing_ok=True)
        return "", sha, "failed"
    if plan.ledger is not None:
        plan.ledger.remember_file_hash(ledger_key, sha)
    if updated_in_place:
        plan.files_updated += 1
    return out_rel.as_posix(), sha, "updated" if updated_in_place else "stored"


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
@dataclass
class ImportJob:
    """One import running in the background: what it is doing and how far along it is."""

    id: str
    name: str
    phase: str = "queued"  # queued, reading, applying, done, error
    done: int = 0
    total: int = 0
    result: dict[str, Any] | None = None
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def view(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {
            "id": self.id,
            "name": self.name,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "percent": (
                100
                if self.phase == "done"
                else min(99, int(self.done * 100 / self.total))
                if self.total
                else 0
            ),
            "elapsed_s": round(end - self.started, 1),
            "result": self.result,
            "error": self.error,
        }

    def progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total


IMPORT_JOBS: dict[str, ImportJob] = {}
MAX_KEPT_JOBS = 20


def new_job(name: str) -> ImportJob:
    job = ImportJob(id=uuid.uuid4().hex[:12], name=name)
    IMPORT_JOBS[job.id] = job
    finished = [j for j in IMPORT_JOBS.values() if j.finished]
    for old in sorted(finished, key=lambda j: j.finished)[: max(0, len(finished) - MAX_KEPT_JOBS)]:
        IMPORT_JOBS.pop(old.id, None)
    return job


def ingest_path(
    svc: MuseService,
    path: Path,
    name: str,
    options: ImportOptions,
    progress: Callable[[int, int], None] | None = None,
) -> ImportPlan:
    """Read and stage one uploaded file (a zip or a single text file) in a worker thread.

    Conversations are held in memory; app and memory files are written straight to the
    workspace and the data directory, and the ledger is consulted so nothing overwrites
    or duplicates. Threads are not touched here: :func:`apply_plan` does that on the loop."""
    plan = ImportPlan(option_key=options.key(), source_name=name or "")
    ledger = ImportLedger(svc.data_dir / "imports.db")
    ledger.open()
    plan.ledger = ledger
    try:
        if zipfile.is_zipfile(path):
            _ingest_zip(svc, path, name, options, plan, progress)
        else:
            _ingest_single(path, name, options, plan, svc.data_dir)
    finally:
        # the connection belongs to this worker thread; apply_plan opens its own to mark
        ledger.close()
        plan.ledger = None
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
    """Extract profile fields, user_profile markdown, and ui_preferences from a JSON object.

    Accepts the flat shape the export prompt asks for and the nested one Muse's own
    archive builder writes (``assistant.name``, ``user.call_name``, ``avatar.face_id``,
    ``counts``); nulls are skipped, never invented."""
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
    # the nested shape: {"assistant": {...}, "user": {...}, "avatar": {...}, "counts": {...}}
    assistant = raw.get("assistant")
    if isinstance(assistant, dict):
        if not plan.profile_data.get("name") and isinstance(assistant.get("name"), str):
            plan.profile_data["name"] = assistant["name"].strip()
            matched = True
        if not plan.profile_data.get("style"):
            for key in ("persona_note", "vibe", "character", "style"):
                v = assistant.get(key)
                if isinstance(v, str) and v.strip():
                    plan.profile_data["style"] = v.strip()
                    matched = True
                    break
        if not plan.profile_data.get("emoji") and isinstance(assistant.get("emoji"), str):
            plan.profile_data["emoji"] = assistant["emoji"].strip()
            matched = True
    user = raw.get("user")
    if isinstance(user, dict) and not plan.profile_data.get("user_name"):
        for key in ("call_name", "full_name", "name"):
            v = user.get(key)
            if isinstance(v, str) and v.strip():
                plan.profile_data["user_name"] = v.strip()
                matched = True
                break
    avatar = raw.get("avatar")
    if isinstance(avatar, dict) and isinstance(avatar.get("face_id"), str):
        face = avatar["face_id"].strip()
        if face:
            plan.avatar_face = plan.avatar_face or face
            plan.profile_data["avatar"] = face
            matched = True
    counts = raw.get("counts")
    if isinstance(counts, dict):
        for k, v in counts.items():
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                plan.declared_counts[str(k)] = v
    archive_root = raw.get("archive_root")
    if isinstance(archive_root, str) and archive_root.strip():
        # "workspace/your_files/minimuse-archive/" -> "minimuse-archive": the folder whose
        # members are counted against the declared totals
        segments = [p for p in archive_root.replace("\\", "/").split("/") if p]
        if segments:
            plan.archive_root = segments[-1].lower()
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
    # Multi-AI JSON chat exports (ChatGPT, Claude, Gemini Takeout, Grok, Perplexity, generic JSON)
    ai_convs = parse_ai_export_json(text_clean, name)
    if not ai_convs and name.lower().endswith((".html", ".htm")):
        ai_convs = parse_takeout_html(text_clean, name)
    if ai_convs:
        plan.conversations.extend(ai_convs)
        return
    turns = parse_transcript(text_clean[:MAX_TRANSCRIPT_CHARS], name)
    if turns:
        chat_id = _chat_id_of(text_clean)
        plan.conversations.append(
            Conversation(
                title=title_from_text(text_clean, name),
                kind="transcript",
                turns=turns,
                source=name,
                external_id=f"muse:{chat_id}" if chat_id else "",
            )
        )
        return
    if options.background_memory:
        rel = _store_memory_file(data_dir, _safe_rel(name), text_clean, ledger=plan.ledger, plan=plan)
        if rel and index_memory_text(data_dir / "personal.db", rel, text_clean):
            plan.memory_files += 1
    if options.restore_chats:
        plan.conversations.append(
            Conversation(
                title=title_from_name(name), kind="text", turns=[], source=name, body=text_clean
            )
        )


def _ingest_zip(
    svc: MuseService,
    path: Path,
    name: str,
    options: ImportOptions,
    plan: ImportPlan,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    data_dir = svc.data_dir
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) > MAX_ZIP_ENTRIES:
            raise ValueError("the archive has too many entries to import safely")
        if not plan.archive_root:
            # the folder that carries its own counts, when the profile has not said so yet
            for candidate in names:
                segments = [p.lower() for p in candidate.replace("\\", "/").split("/")]
                if "minimuse-archive" in segments:
                    plan.archive_root = "minimuse-archive"
                    break
        total = sum(info.file_size for info in archive.infolist())
        if MAX_ZIP_TOTAL_BYTES and total > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("the archive expands to more than the import limit; split it first")
        apps: dict[str, dict[str, Any]] = {}
        entry_total = len(names)
        for entry_no, info in enumerate(archive.infolist(), start=1):
            if progress is not None and (entry_no % 100 == 0 or entry_no == entry_total):
                progress(entry_no, entry_total)
            member = info.filename
            if member.endswith("/") or info.is_dir():
                continue
            parts = [p for p in member.replace("\\", "/").split("/") if p]
            if not parts:
                continue
            kind = _classify(parts)
            # only text is read into memory, so only text has a size cap; app and loose
            # files stream to disk and may be as large as the disk allows
            if kind not in ("app", "file") and info.file_size > MAX_ZIP_ENTRY_BYTES:
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
            elif kind == "file":
                _read_file_entry(svc, archive, member, parts, plan)
            elif kind == "app":
                _read_app_entry(svc, archive, member, apps, options, plan)
            else:
                plan.skipped_files += 1
        _finish_apps(svc, apps, options, plan)
    _materialize_avatar_moods(svc, plan)
    if plan.manifest_transcripts:
        # the manifest already carries these chats with exact times and sides; the text copy of
        # the same chat would show up a second time
        plan.conversations = [
            c
            for c in plan.conversations
            if not (c.kind == "transcript" and c.source.rsplit("/", 1)[-1] in plan.manifest_transcripts)
        ]


# the bundle keeps two of its images base64-encoded beside the real ones; they are avatar
# files too, so the archive's own avatar count adds up
_AVATAR_SUFFIXES = {".webp", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".webm", ".json", ".b64"}

# files that are the file they are - a picture, a clip, a recording, a document, a
# database - and belong in the workspace as themselves, not parsed as text
_MEDIA_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".avif",
    ".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv",
    ".mp3", ".m4a", ".wav", ".ogg", ".oga", ".flac",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".xlsx", ".xls", ".docx", ".pptx", ".epub",
    ".glb", ".gltf", ".fbx", ".vrm", ".obj",
    ".db", ".sqlite", ".sqlite3",
}

# text that is not chat text: configs, skills and scripts kept as memory files
_TEXT_EXTRA = {".yaml", ".yml", ".toml", ".ini", ".cfg", ".py", ".sh", ".js", ".ts"}


# Muse keeps a folder of half-rendered transcript pages while it writes them; the finished
# transcripts live elsewhere, so the fragments are never content
_CACHE_FOLDERS = {".transcript-pages"}

# copies of a file next to the file itself: kept as files, never parsed as the file
_BACKUP_SUFFIXES = (".bak", ".old", ".orig", ".swp", ".copy", "~")


# names that are never content: half-written uploads, editor swap files, desktop clutter
_NOISE_NAMES = {".ds_store", "thumbs.db", ".gitkeep"}
_NOISE_PREFIXES = (".hatch-fileserver-tmp", ".~lock")
_NOISE_SUFFIXES = (".tmp", ".part", ".crdownload", ".download")


def _classify(parts: list[str]) -> str:
    joined = "/".join(p.lower() for p in parts)
    filename = parts[-1].lower()
    lower_parts = [p.lower() for p in parts]
    suffix = Path(filename).suffix.lower()
    # half-written uploads and editor leftovers are never content; a hidden file that does
    # carry something (a fixture, a dotfile config) is still somebody's file
    if (
        filename in _NOISE_NAMES
        or filename.startswith(_NOISE_PREFIXES)
        or filename.endswith(_NOISE_SUFFIXES)
    ):
        return "skip"
    if _CACHE_FOLDERS & set(lower_parts[:-1]):
        return "skip"
    if filename.endswith(_BACKUP_SUFFIXES):
        # "chat-transcript.md.bak" is a copy, not another chat
        return "file"
    if joined == "manifest.json":
        return "manifest"
    if filename == "manifest.md":
        # the archive's own MANIFEST: a document about the archive, not one of its chats
        return "memory"
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
    if "chat-transcripts" in joined:
        return "transcript"
    if any(p in ("memory", "goals", "projects", "notes", "system", "profile") for p in lower_parts):
        # notes/, system/ and profile/: documents, skills, preferences - text into the
        # deep archive, anything else (pictures, clips, code, databases) kept as the file
        # it is. Checked before the transcript and html rules so a goal note whose name
        # says "transcript", or a document that happens to be a web page, stays what it is.
        return "memory" if (suffix in TEXT_SUFFIXES or suffix in _TEXT_EXTRA) else "file"
    if suffix in _MEDIA_SUFFIXES:
        return "file"
    if filename in ("conversations.json", "prod-grok-backend.json", "myactivity.json", "myactivity.html"):
        return "transcript"
    if filename == "chat.html" and any(p in ("chatgpt", "openai", "takeout") for p in lower_parts):
        return "transcript"
    if filename in ("memory.md", "muse-error-log.md", "muse-quotes.md"):
        return "memory"
    if "transcript" in filename:
        return "transcript"
    if filename.endswith((".html", ".htm")):
        return "app"
    if suffix in TEXT_SUFFIXES:
        return "transcript"
    # catch-all: anything else is still somebody's file - a script, a database, a build
    # output - and goes into the workspace as itself rather than being dropped
    return "file"


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
        content = WIDGET_TOKEN.sub("", str(turn.get("content") or "")).strip()
        if not content:
            continue
        transcript_file = str(turn.get("transcript_file") or "").strip()
        if transcript_file:
            plan.manifest_transcripts.add(transcript_file.replace("\\", "/").rsplit("/", 1)[-1])
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
        digest = hashlib.sha256(data).hexdigest()
        plan.ledger_marks.append((digest, "memory"))
        if plan.ledger and plan.ledger.seen(digest, "memory", plan.option_key):
            # the same file landed in an earlier run: one copy, not a numbered one
            plan.duplicates += 1
            return
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
        m_face = re.search(r"avatar-([A-Za-z0-9]{6,})", rel_parts[0], re.IGNORECASE)
        face = slugify(m_face.group(1)) if m_face else slugify(str(plan.profile_data.get("avatar") or "imported-muse"))
        sub = _safe_rel(rel_parts[0])
    else:
        face = slugify(rel_parts[0])
        # frames/, options/, thumbnails/ keep their folder: the bundle stays browsable
        sub = _safe_rel("/".join(rel_parts[1:]))
    face_dir = svc.workspace() / "avatar" / face
    target = face_dir / sub
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # streamed, because an avatar clip can be far larger than the text cap
        with archive.open(member) as source, target.open("wb") as out:
            while True:
                chunk = source.read(COPY_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        plan.avatar_files += 1
        plan.avatar_faces.add(face)
        if not plan.avatar_face:
            plan.avatar_face = face
        if target.name.lower() == "avatar-model.json":
            try:
                parsed = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
            if isinstance(parsed, dict):
                plan.avatar_models[face] = parsed
                if not plan.avatar_model:
                    plan.avatar_model = parsed
        # If an imported still is .png/.jpg/.gif for a standard mood and .webp doesn't exist yet,
        # also write <mood>.webp so the frontend's /api/files/avatar/<face>/<mood>.webp loads immediately.
        stem_low = Path(target.name).stem.lower()
        suf_low = Path(target.name).suffix.lower()
        if stem_low in _MOOD_NAMES and suf_low in (".png", ".jpg", ".jpeg", ".gif"):
            webp_alias = face_dir / f"{stem_low}.webp"
            if not webp_alias.exists():
                webp_alias.write_bytes(target.read_bytes())
    except OSError as exc:
        logger.warning("could not store imported avatar file: %s", exc)


def _materialize_avatar_moods(svc: MuseService, plan: ImportPlan) -> None:
    """The stills and clips the app asks for (``<mood>.webp`` / ``<mood>.mp4``),
    copied out of the bundle's own files using ``avatar-model.json``'s state map (or
    filename heuristics when importing standalone model zips), so every imported face
    changes pose across situations. Safe to re-run when re-importing."""
    faces = list(plan.avatar_faces) or ([plan.avatar_face] if plan.avatar_face else [])
    if not faces:
        return
    total_made = 0
    for raw_face in faces:
        model = plan.avatar_models.get(raw_face) or (plan.avatar_model if raw_face == plan.avatar_face else {}) or {}
        face = slugify(str(model.get("face_id") or raw_face))
        face_dir = svc.workspace() / "avatar" / face
        if not face_dir.is_dir():
            continue
        if not model and (face_dir / "avatar-model.json").is_file():
            try:
                loaded = json.loads((face_dir / "avatar-model.json").read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    model = loaded
            except Exception:
                model = {}
        webps = sorted(p for p in face_dir.rglob("*.webp") if p.is_file())
        mp4s = sorted(p for p in face_dir.rglob("*.mp4") if p.is_file())

        def pick_webp(*hints: str) -> Path | None:
            for path in webps:
                name = path.name.lower()
                if "darkmode" not in name and all(hint in name for hint in hints):
                    return path
            for path in webps:
                name = path.name.lower()
                if all(hint in name for hint in hints):
                    return path
            return None

        def pick_mp4(*hints: str) -> Path | None:
            for path in mp4s:
                name = path.name.lower()
                if "darkmode" not in name and "attempt" not in name and all(hint in name for hint in hints):
                    return path
            for path in mp4s:
                name = path.name.lower()
                if all(hint in name for hint in hints):
                    return path
            return None

        active_name = str(model.get("active_image") or "")
        active = face_dir / active_name if active_name else None
        if active is not None and not active.is_file():
            active = None
        if active is None:
            active = pick_webp("-1") or (webps[0] if webps else None)

        stills: dict[str, Path | None] = {
            "idle": pick_webp("static", "idle") or pick_webp("idle") or active,
            "working": pick_webp("static", "working") or pick_webp("working") or active,
            "waiting": pick_webp("static", "making_something") or pick_webp("making_something") or pick_webp("waiting") or active,
            "happy": pick_webp("static", "milestone_level_up") or pick_webp("milestone") or pick_webp("happy") or active,
            "error": next((p for p in webps if p.name.lower().endswith("-darkmode.webp")), None) or active,
        }
        variants = model.get("animated_variants_mp4")
        variants = variants if isinstance(variants, dict) else {}
        call_anims = model.get("call_idle_animations_mp4")
        call_anims = call_anims if isinstance(call_anims, dict) else {}
        clips: dict[str, Any] = {
            "idle": variants.get("idle") or (pick_mp4("-1.mp4").name if pick_mp4("-1.mp4") else None),
            "working": variants.get("working") or (pick_mp4("working").name if pick_mp4("working") else None),
            "waiting": (
                variants.get("making_something")
                or variants.get("waiting_for_subagents")
                or variants.get("waiting")
                or (pick_mp4("making_something").name if pick_mp4("making_something") else None)
            ),
            "happy": (
                variants.get("milestone_level_up")
                or variants.get("happy")
                or (pick_mp4("milestone").name if pick_mp4("milestone") else None)
            ),
            "call": (
                call_anims.get("light")
                or call_anims.get("dark")
                or (pick_mp4("call", "light", "idle").name if pick_mp4("call", "light", "idle") else None)
            ),
        }
        for mood, source in stills.items():
            if source is None or not source.is_file():
                continue
            target = face_dir / f"{mood}.webp"
            if target.resolve() == source.resolve():
                continue
            try:
                target.write_bytes(source.read_bytes())
                total_made += 1
            except OSError as exc:
                logger.warning("could not materialize avatar mood %s: %s", mood, exc)
        for mood, source_name in clips.items():
            if not isinstance(source_name, str) or not source_name:
                continue
            wanted = Path(source_name).name.lower()
            source = next(
                (p for p in face_dir.rglob("*") if p.is_file() and p.name.lower() == wanted), None
            )
            target = face_dir / f"{mood}.mp4"
            if source is None or target.resolve() == source.resolve():
                continue
            try:
                target.write_bytes(source.read_bytes())
                total_made += 1
            except OSError as exc:
                logger.warning("could not materialize avatar clip %s: %s", mood, exc)
    plan.avatar_materialized += total_made


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
    # Multi-AI JSON & Takeout HTML exports (ChatGPT conversations.json, Claude, Gemini, Grok)
    low_member = member.lower()
    ai_convs = parse_ai_export_json(text, member) if low_member.endswith(".json") else []
    if not ai_convs and low_member.endswith((".html", ".htm")):
        ai_convs = parse_takeout_html(text, member)
    if ai_convs:
        for conv in ai_convs:
            existing = plan.chats_by_id.get(conv.external_id) if conv.external_id else None
            if existing is not None:
                _absorb_turns(existing, conv.turns)
            else:
                if conv.external_id:
                    plan.chats_by_id[conv.external_id] = conv
                plan.conversations.append(conv)
        return
    turns = parse_transcript(text, member)
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    if _in_archive(plan, member) and any(p.lower() == "chat-transcripts" for p in parts):
        # counted against the archive's own transcript totals in the verification report
        plan.staged_counts["chat_transcripts_in_archive"] = (
            plan.staged_counts.get("chat_transcripts_in_archive", 0) + 1
        )
        plan.staged_counts["total_transcript_messages"] = (
            plan.staged_counts.get("total_transcript_messages", 0) + len(turns)
        )
    if turns:
        chat_id = _chat_id_of(text)
        ext_key = f"muse:{chat_id}" if chat_id else ""
        existing = plan.chats_by_id.get(chat_id) if chat_id else None
        if existing is not None:
            # the same conversation, carried a second time: the messages join the one chat
            _absorb_turns(existing, turns)
            return
        conv = Conversation(
            title=title_from_text(text, member),
            kind="transcript",
            turns=turns,
            source=member,
            external_id=ext_key,
        )
        if chat_id:
            plan.chats_by_id[chat_id] = conv
        plan.conversations.append(conv)
        return
    if options.background_memory:
        # no messages in it: it is a document, and it goes into the deep archive
        digest = hashlib.sha256(data).hexdigest()
        plan.ledger_marks.append((digest, "memory"))
        if plan.ledger and plan.ledger.seen(digest, "memory", plan.option_key):
            plan.duplicates += 1
        else:
            rel = _memory_rel(parts)
            stored = _store_memory_file(data_dir, rel, text, ledger=plan.ledger, plan=plan)
            if stored and index_memory_text(data_dir / "personal.db", stored, text):
                plan.memory_files += 1
    lowered = member.replace("\\", "/").lower()
    is_chat_file = lowered.startswith("conversations/") or "transcript" in lowered
    if options.restore_chats and is_chat_file:
        plan.conversations.append(
            Conversation(
                title=title_from_name(member), kind="text", turns=[], source=member, body=text
            )
        )


def _memory_rel(parts: list[str]) -> Path:
    """The path of a memory file relative to its own area: everything after the last
    memory/goals/projects/notes/system segment, so the archive's folder structure stays
    readable (``notes/documents/asmr-watch/v1.txt`` lands as ``documents/asmr-watch/v1.txt``).
    A file with no such folder keeps its own folders too, so two ``note.md`` files from
    different places do not land on top of each other."""
    lower = [p.lower() for p in parts]
    for marker in ("memory", "goals", "projects", "notes", "system", "chat-transcripts", "profile"):
        if marker in lower:
            idx = len(lower) - 1 - lower[::-1].index(marker)
            rest = parts[idx + 1 :]
            if rest:
                return _safe_rel("/".join(rest))
    return _safe_rel("/".join(parts[-3:]))


def _in_archive(plan: ImportPlan, member: str) -> bool:
    """The member is inside the archive folder that declares its own counts, so it counts
    against them (files outside it are still imported, just not tallied)."""
    if not plan.archive_root:
        return False
    parts = [p.lower() for p in member.replace("\\", "/").split("/") if p]
    return plan.archive_root in parts


def _section_of(parts: list[str]) -> str:
    """Which declared count bucket a member belongs to, or "" when it has none."""
    lower = [p.lower() for p in parts]
    if "documents" in lower:
        return "document_files"
    if "goals" in lower:
        return "goal_files"
    if "memory" in lower:
        return "memory_files"
    return ""


def _file_rel(parts: list[str]) -> Path:
    """Where a loose file lands under ``workspace/imported/``: the archive's own folder
    kept (``notes/uploads/image/15/x.jpg`` -> ``uploads/image/15/x.jpg``). Muse's raw
    stores are the same files as the archive's uploads, so their prefixes are folded onto
    one place (``user/files/x.jpg`` and ``notes/uploads/files/x.jpg`` are one picture, not
    two) and identical bytes are stored once."""
    lower = [p.lower() for p in parts]
    for marker in ("uploads", "attachments", "documents", "goals", "memory", "projects", "user"):
        if marker in lower:
            idx = len(lower) - 1 - lower[::-1].index(marker)
            rest = parts[idx + 1 :]
            if rest and rest[0].lower() in ("files", "media_library"):
                rest = rest[1:]
            if rest:
                base = "uploads" if marker == "user" else marker
                return _safe_rel("/".join([base] + rest))
    return _safe_rel("/".join(parts[-3:]))


def _read_file_entry(
    svc: MuseService, archive: zipfile.ZipFile, member: str, parts: list[str], plan: ImportPlan
) -> None:
    """A file the archive carried as itself - a picture, a clip, a recording, a document
    - into ``workspace/imported/`` as the file it is, streamed and deduplicated."""
    rel = _file_rel(parts)
    out, sha, status = _store_app_stream(svc.workspace(), Path("imported"), rel, archive, member, plan)
    plan.ledger_marks.append((sha, "file"))
    if status != "failed" and _in_archive(plan, member):
        key = _section_of(parts)
        if key:
            plan.staged_counts[key] = plan.staged_counts.get(key, 0) + 1
    if not out and status == "failed":
        plan.skipped_files += 1


def _read_memory_entry(
    archive: zipfile.ZipFile, member: str, data_dir: Path, options: ImportOptions, plan: ImportPlan
) -> None:
    with archive.open(member) as handle:
        data = handle.read(MAX_TRANSCRIPT_CHARS * 4)
    digest = hashlib.sha256(data).hexdigest()
    plan.ledger_marks.append((digest, "memory"))
    if not options.background_memory:
        return
    text = _decode(data)
    if text is None:
        plan.skipped_files += 1
        return
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    already = bool(plan.ledger and plan.ledger.seen(digest, "memory", plan.option_key))
    stored: str | None = None
    if already:
        # the same bytes landed in an earlier run: they are already in the deep archive
        plan.duplicates += 1
    else:
        rel = _memory_rel(parts)
        stored = _store_memory_file(data_dir, rel, text, ledger=plan.ledger, plan=plan)
        if stored:
            plan.memory_files += 1
            # the search index is best-effort: a missing database must not hide the file
            index_memory_text(data_dir / "personal.db", stored, text)
    if (stored or already) and _in_archive(plan, member):
        key = _section_of(parts)
        if key:
            plan.staged_counts[key] = plan.staged_counts.get(key, 0) + 1


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
    parts = [p for p in member.replace("\\", "/").split("/") if p]
    root = _app_root(parts)
    slug = slugify(root[-1] if root is not None else Path(member).stem)
    app = apps.setdefault(
        slug,
        {"slug": slug, "title": slug.replace("-", " ").title(), "html_rel": "", "files": 0},
    )
    lower = [p.lower() for p in parts]
    if root is not None:
        # everything after the app's own folder is the app's file
        rel_parts = parts[len(root) :]
    elif "apps" in lower:
        idx = lower.index("apps")
        rel_parts = parts[idx + 1 :]
        if rel_parts and rel_parts[0].lower() == slug:
            rel_parts = rel_parts[1:]
    else:
        rel_parts = [parts[-1]]
    if not rel_parts:
        rel_parts = [Path(member).name]
    rel = _safe_rel("/".join(rel_parts))
    out, sha, _status = _store_app_stream(
        svc.workspace(), Path("apps") / slug, rel, archive, member, plan
    )
    plan.ledger_marks.append((sha, "app"))
    if root is not None and plan.archive_root and plan.archive_root in lower:
        # a folder inside the archive's own apps/ - one of the folders it counts
        app["from_archive"] = True
    if root is not None:
        app["rooted"] = True
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
    if plan.archive_root:
        plan.staged_counts["app_folders_in_archive"] = sum(
            1 for a in apps.values() if a.get("from_archive")
        )
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
            entry = ImportedApp(
                slug=app["slug"],
                title=app["title"],
                html_rel=app["html_rel"],
                files=app["files"],
            )
            plan.apps.append(entry)
            if register and conn is not None and app["html_rel"]:
                # an index.html with no folder of its own is a page inside something else
                # (a build output, a mirror of another app), not an app of its own
                page = Path(app["html_rel"]).name.lower()
                if not app.get("rooted") and page.startswith("index"):
                    continue
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
                entry.registered = True
        if conn is not None:
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("could not register imported apps: %s", exc)
    finally:
        if conn is not None:
            conn.close()


# ------------------------------------------------------------------ applying to the service
_VERIFY_BUCKETS = (
    ("chat_transcripts_in_archive", "chat transcripts"),
    ("total_transcript_messages", "transcript messages"),
    ("avatar_files", "avatar files"),
    ("app_folders_in_archive", "app folders"),
    ("memory_files", "memory files"),
    ("goal_files", "goal files"),
    ("document_files", "document files"),
)


def _verify_import(plan: ImportPlan, options: ImportOptions) -> dict[str, Any] | None:
    """The archive's own counts against what actually landed - the honest check behind
    the report file. A gap stays a gap: nothing is claimed complete when it is not."""
    if not plan.declared_counts:
        return None
    # buckets a switched-off option was never asked to fill: reported as notes, not gaps
    off: set[str] = set()
    if not options.background_memory:
        off |= {"memory_files", "goal_files", "document_files"}
    if options.apps == "neither":
        off.add("app_folders_in_archive")
    lines: list[str] = []
    ok = True
    for key, label in _VERIFY_BUCKETS:
        if key not in plan.declared_counts or key in off:
            continue
        declared = plan.declared_counts[key]
        staged = plan.avatar_files if key == "avatar_files" else plan.staged_counts.get(key, 0)
        if staged == declared:
            lines.append(f"{label}: the archive declares {declared}, this import staged {staged}")
        elif staged > declared:
            # more than the archive counted itself: nothing is missing, its own total was low
            lines.append(
                f"{label}: the archive declares {declared}, this import staged {staged}"
                "  (more than declared, so nothing is missing)"
            )
        elif plan.duplicates > 0 and (staged + plan.duplicates) >= declared:
            lines.append(
                f"{label}: the archive declares {declared}, this import staged {staged}"
                f"  ({declared - staged} already present from an earlier import)"
            )
        else:
            ok = False
            lines.append(
                f"{label}: the archive declares {declared}, this import staged {staged}  (GAP)"
            )
    if not lines:
        return None
    if not options.background_memory:
        lines.append(
            "note: background memory was switched off, so memory, goal and document text was not stored"
        )
    if options.apps == "neither":
        lines.append("note: apps were switched off, so no app folders were stored")
    if plan.skipped_files:
        lines.append(f"files the importer skipped (unreadable, unsupported, or too large): {plan.skipped_files}")
    if plan.duplicates:
        lines.append(f"identical files already present, not stored twice: {plan.duplicates}")
    return {"ok": ok, "lines": lines}


def _write_verify_report(svc: MuseService, plan: ImportPlan, verify: dict[str, Any]) -> None:
    """The verification as a markdown file under ``workspace/imports/``, where the agent
    can read it later next to the kept copy of the export."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    slug = slugify(Path(plan.source_name or "import").stem) or "import"
    body = [
        "# Import verification",
        "",
        f"- Source: {plan.source_name or 'unknown'}",
        f"- Built: {stamp}",
        f"- Result: {'every declared count matched' if verify['ok'] else 'GAPS: some declared counts did not match'}",
        "",
        "## Counts",
        "",
        *[f"- {line}" for line in verify["lines"]],
        "",
        "The original export (when a copy was kept) sits next to this file under"
        " `workspace/imports/`; this report compares the archive's own declared totals"
        " with what the import staged. Nothing here deletes anything.",
        "",
    ]
    try:
        root = svc.workspace() / "imports"
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{slug}-verify.md").write_text("\n".join(body), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write the import verification report: %s", exc)


def _avatar_to_wear(svc: MuseService, plan: ImportPlan, cur_prof: dict[str, Any]) -> str:
    """Which face folder the profile should name after this import."""
    if not plan.avatar_face:
        return str(cur_prof.get("avatar") or "")
    root = svc.workspace() / "avatar"
    imported = root / plan.avatar_face
    chosen = str(cur_prof.get("avatar") or "")
    chosen_dir = root / chosen if chosen else None
    has_webp = bool(chosen_dir and (chosen_dir / "idle.webp").is_file())
    imported_has_clip = (imported / "idle.mp4").is_file()
    imported_has_webp = (imported / "idle.webp").is_file()
    if imported.is_dir() and (imported_has_clip or imported_has_webp or not has_webp):
        return plan.avatar_face
    return chosen or plan.avatar_face


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
        # the face the profile names must be the folder that holds the imported animation
        cur_prof["avatar"] = _avatar_to_wear(svc, plan, cur_prof)
        try:
            svc.update_profile(cur_prof)
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
        "events_updated": 0,
        "threads_created": 0,
        "threads_merged": 0,
        "messages_indexed": 0,
        "memory_files": plan.memory_files,
        "files_updated": plan.files_updated,
        "apps_saved": sum(1 for a in plan.apps),
        "apps_registered": (
            sum(1 for a in plan.apps if a.registered) if options.apps in ("restore", "both") else 0
        ),
        "profile_updated": profile_updated,
        "avatar_files": plan.avatar_files,
        "avatar_moods": plan.avatar_materialized,
        "ui_preferences": plan.ui_prefs if plan.ui_prefs else None,
        "skipped_files": plan.skipped_files,
        "duplicates": plan.duplicates,
        "note": plan.note,
        # filled in by the caller when the uploaded file is kept under workspace/imports/
        "kept_copy": None,
    }
    ledger = plan.ledger or ImportLedger(svc.data_dir / "imports.db")
    if plan.ledger is None:
        ledger.open()
    plan.ledger = None
    titles = {normalize_title(t.title): t.id for t in svc.threads.values() if t.title.strip()}
    personal_db = svc.data_dir / "personal.db"
    try:
        for conv in plan.conversations:
            if options.restore_chats:
                thread_id = _apply_conversation(svc, conv, options, titles, stats, ledger=ledger)
                if thread_id:
                    # the next same-titled file merges into the chat this one just made
                    titles[normalize_title(conv.title)] = thread_id
                    if conv.external_id:
                        ledger.remember_thread(conv.external_id, thread_id, conv.title)
            else:
                thread_id = f"import:{slugify(conv.external_id or conv.title)}"
            if options.background_memory and thread_id:
                stats["messages_indexed"] += index_conversation_turns(personal_db, thread_id, conv)
        for digest, kind in plan.ledger_marks:
            ledger.mark(digest, kind, plan.option_key, "import")
        ledger.commit()
    finally:
        ledger.close()
    verify = _verify_import(plan, options)
    if verify is not None:
        stats["verify"] = verify
        _write_verify_report(svc, plan, verify)
    if getattr(svc, "sync", None) is not None and any(
        stats.get(k)
        for k in ("events_added", "events_updated", "apps_saved", "files_updated", "memory_files", "profile_updated")
    ):
        try:
            svc.sync.mirror_local_now()
            svc.sync.peer_sync_soon(delay=1.0)
        except Exception:
            pass
    return stats


def _event_hash(ts: str, kind: str, text: str) -> str:
    return hashlib.sha256(f"{ts}|{kind}|{text}".encode()).hexdigest()[:16]


def _apply_conversation(
    svc: MuseService,
    conv: Conversation,
    options: ImportOptions,
    titles: dict[str, str],
    stats: dict[str, Any],
    ledger: ImportLedger | None = None,
) -> str:
    """One conversation becomes (or merges into) a chat; the thread id is returned.
    Handles overlapping, edited, and newly added messages cleanly in place."""
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
        existing_by_ext = ledger.get_thread(conv.external_id) if (ledger and conv.external_id) else None
        existing = (
            existing_by_ext
            if existing_by_ext and svc.threads.get(existing_by_ext) is not None
            else titles.get(normalize_title(conv.title))
        )
        if existing is not None and svc.threads.get(existing) is not None:
            tid = existing
            stats["threads_merged"] += 1
            # If the conversation was renamed in the updated export, keep the fresher title
            existing_th = svc.threads.get(existing)
            if (
                existing_by_ext == existing
                and existing_th is not None
                and conv.title.strip()
                and normalize_title(existing_th.title) != normalize_title(conv.title)
                and tid != "main"
            ):
                svc.rename_thread(tid, conv.title.strip())
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
    by_source_id: dict[str, dict[str, Any]] = {}
    by_ts_role: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in thread.timeline.events:
        if not ev.get("imported"):
            continue
        smid = str(ev.get("source_msg_id") or "").strip()
        if smid:
            by_source_id[smid] = ev
        ev_ts = str(ev.get("ts") or "")
        ev_type = str(ev.get("type") or "")
        if ev_ts and ev_ts != FLOOR_TS and ev_type in ("user", "assistant"):
            by_ts_role.setdefault((ev_ts, ev_type), ev)
    # a chat view keeps the newest `max_events` messages: anything older than the oldest
    # kept one would be trimmed the moment it is added, so a second import of the same
    # archive must not re-add it (it is in the search index either way)
    events_cap = int(getattr(thread.timeline, "max_events", 0) or 0)
    oldest_kept = min(
        (str(ev.get("ts") or "") for ev in thread.timeline.events), default=""
    )
    full = bool(events_cap) and len(thread.timeline.events) >= events_cap
    events: list[dict[str, Any]] = []
    timeline_modified = False
    if conv.kind == "text" and not turns:
        # plain prose with no messages in it: shown in the chat as a note, kept in memory
        body = (conv.body or conv.source).strip()[:20_000]
        note_text = f"Imported from {conv.source}:\n\n{body}"
        if not any(ev.get("type") == "notice" and ev.get("text") == note_text for ev in thread.timeline.events):
            events.append(
                {
                    "type": "notice",
                    "level": "info",
                    "text": note_text,
                    "imported": True,
                }
            )
    for turn in turns:
        if _event_hash(turn.ts, turn.role, turn.text) in known:
            stats["duplicates"] += 1
            continue
        # Check if this turn edits an existing imported message in place
        target_ev: dict[str, Any] | None = None
        if turn.source_msg_id and turn.source_msg_id in by_source_id:
            target_ev = by_source_id[turn.source_msg_id]
        elif not turn.source_msg_id and turn.ts != FLOOR_TS and (turn.ts, turn.role) in by_ts_role:
            target_ev = by_ts_role[(turn.ts, turn.role)]
        if target_ev is not None:
            if str(target_ev.get("text") or "") != turn.text:
                target_ev["text"] = turn.text
                if turn.source_msg_id:
                    target_ev["source_msg_id"] = turn.source_msg_id
                known.add(_event_hash(str(target_ev.get("ts") or turn.ts), turn.role, turn.text))
                stats["events_updated"] += 1
                timeline_modified = True
            else:
                stats["duplicates"] += 1
            continue
        if full and oldest_kept and turn.ts <= oldest_kept:
            # already trimmed by the chat's own size limit: do not add it again
            stats["duplicates"] += 1
            continue
        ev_obj: dict[str, Any] = {"type": turn.role, "text": turn.text, "ts": turn.ts, "imported": True}
        if turn.source_msg_id:
            ev_obj["source_msg_id"] = turn.source_msg_id
        events.append(ev_obj)
    if timeline_modified:
        thread.timeline.save()
    if events:
        stats["events_added"] += thread.timeline.merge_sorted(events)
    # where the chat sits in the list comes from its messages, like every chat written
    # here: a merged import keeps the newest message's time, a new one gets its own span
    thread.refresh_times_from_events()
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
    line up around them by time (the thread's start is the boundary). Edited messages
    update cleanly in place."""
    boundary = parse_timestamp(str(getattr(thread, "created_at", "") or "")) or datetime.now(
        UTC
    ).isoformat(timespec="seconds")
    seen: set[tuple[str, str]] = set()
    by_smid: dict[str, Message] = {}
    by_ts_role: dict[tuple[str, str], Message] = {}
    for m in thread.agent.messages:
        meta = m.meta or {}
        if not meta.get("imported"):
            continue
        ts_m = str(meta.get("imported_ts") or "")
        seen.add((ts_m, m.content or ""))
        smid = str(meta.get("source_msg_id") or "").strip()
        if smid:
            by_smid[smid] = m
        r_m = str(getattr(m.role, "value", m.role) or "")
        if ts_m and ts_m != FLOOR_TS and r_m in ("user", "assistant"):
            by_ts_role.setdefault((ts_m, r_m), m)
    before: list[tuple[str, Message]] = []
    after: list[tuple[str, Message]] = []
    updated = 0
    for turn in turns:
        if not turn.text.strip():
            continue
        if (turn.ts, turn.text) in seen:
            continue
        existing_msg: Message | None = None
        if turn.source_msg_id and turn.source_msg_id in by_smid:
            existing_msg = by_smid[turn.source_msg_id]
        elif not turn.source_msg_id and turn.ts != FLOOR_TS and (turn.ts, turn.role) in by_ts_role:
            existing_msg = by_ts_role[(turn.ts, turn.role)]
        if existing_msg is not None:
            if (existing_msg.content or "") != turn.text:
                existing_msg.content = turn.text
                updated += 1
            continue
        message = Message.user(turn.text) if turn.role == "user" else Message.assistant(turn.text)
        message.meta["imported"] = True
        message.meta["imported_ts"] = turn.ts
        if turn.source_msg_id:
            message.meta["source_msg_id"] = turn.source_msg_id
        if turn.ts <= boundary:
            before.append((turn.ts, message))
        else:
            after.append((turn.ts, message))
    before.sort(key=lambda pair: pair[0])
    after.sort(key=lambda pair: pair[0])
    agent = thread.agent
    if before or after or updated:
        agent.messages = [m for _, m in before] + list(agent.messages) + [m for _, m in after]
        agent._save_session()
    return len(before) + len(after) + updated


# ------------------------------------------------------------------ public entry points
def ingest_text(svc: MuseService, text: str, name: str, options: ImportOptions) -> ImportPlan:
    """Stage pasted text in a worker thread (the file side); :func:`apply_plan` turns it
    into chats on the loop."""
    if len(text.encode("utf-8", errors="replace")) > MAX_TEXT_PASTE_BYTES:
        raise ValueError("the pasted text is larger than 32 MB; import it as a file")
    plan = ImportPlan(option_key=options.key())
    ledger = ImportLedger(svc.data_dir / "imports.db")
    ledger.open()
    plan.ledger = ledger
    try:
        _accept_text(svc.data_dir, name or "Imported text", text, options, plan)
    finally:
        # the connection belongs to this worker thread; apply_plan opens its own to mark
        ledger.close()
        plan.ledger = None
    return plan


def import_text(svc: MuseService, text: str, name: str, options: ImportOptions) -> dict[str, Any]:
    """Import pasted text in one call (Settings → Import & export → Paste text, scripts)."""
    plan = ingest_text(svc, text, name, options)
    return apply_plan(svc, plan, options)


def import_file(svc: MuseService, path: Path, name: str, options: ImportOptions) -> dict[str, Any]:
    """Import one uploaded file (Settings → Import & export → Import a file)."""
    plan = ingest_path(svc, path, name, options)
    return apply_plan(svc, plan, options)


def keep_original(svc: MuseService, staging: Path, name: str) -> str:
    """Move the uploaded file, byte for byte, under ``workspace/imports/`` so the
    original survives the transfer and can be re-checked later (by the person or the
    agent); returns its workspace-relative path. The staging file is consumed."""
    import shutil

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_. -]+", "_", Path(name).name).strip() or "import.zip"
    root = svc.workspace() / "imports"
    root.mkdir(parents=True, exist_ok=True)
    dest = root / f"{stamp}-{safe}"
    counter = 2
    while dest.exists():
        dest = root / f"{stamp}-{safe}-{counter}"
        counter += 1
    try:
        staging.rename(dest)
    except OSError:
        # staging and the workspace may sit on different volumes: a copy-move then
        shutil.move(str(staging), str(dest))
    return dest.relative_to(svc.workspace()).as_posix()


__all__ = [
    "IMPORT_UPLOAD_MB",
    "ImportLedger",
    "ImportOptions",
    "apply_plan",
    "import_file",
    "import_text",
    "ingest_path",
    "ingest_text",
    "keep_original",
    "looks_like_transcript",
    "parse_timestamp",
    "parse_transcript",
]
