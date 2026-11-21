"""Importing a Muse archive: transcript shapes, zip classification, merging and safety."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nanomuse.schema import Message
from nanomuse.server import data_import
from nanomuse.server.data_import import (
    FLOOR_TS,
    Conversation,
    ImportOptions,
    Turn,
    apply_plan,
    import_file,
    import_text,
    looks_like_transcript,
    parse_timestamp,
    parse_transcript,
)
from nanomuse.server.events import Timeline


def test_parse_timestamp_shapes() -> None:
    # Muse section headers: "2026-10-17 13:18:45 MDT" is 19:18:45 UTC
    assert parse_timestamp("2026-10-17 13:18:45 MDT") == "2026-10-17T19:18:45+00:00"
    assert parse_timestamp("2026-11-07 19:32:44 MST") == "2026-11-08T02:32:44+00:00"
    # Muse bold headers: a human date with an abbreviation
    assert parse_timestamp("Sat Oct 10, 2026, 9:54:24 AM MDT") == "2026-10-10T15:54:24+00:00"
    # ISO stays ISO, normalized to UTC
    assert parse_timestamp("2026-10-10T15:54:24Z") == "2026-10-10T15:54:24+00:00"
    assert parse_timestamp("2026-10-10T09:54:24-06:00") == "2026-10-10T15:54:24+00:00"
    # nonsense is nothing
    assert parse_timestamp("not a time") == ""


def test_parse_transcript_section_headers() -> None:
    text = (
        "# Chat Transcript — Garment Waist Mark\n"
        "\n"
        "- Chat ID: e7ce5fac\n"
        "- Chat started: 2026-10-17 13:18:45 MDT\n"
        "\n"
        "---\n"
        "\n"
        "## User — 2026-10-17 13:18:45 MDT\n"
        "\n"
        "Hey, is this ok?\n"
        "\n"
        "## Muse — 2026-10-17 13:24:23 MDT\n"
        "\n"
        "Okay, respectfully received.\n"
    )
    turns = parse_transcript(text)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].text == "Hey, is this ok?"
    assert turns[0].ts == "2026-10-17T19:18:45+00:00"
    assert turns[1].role == "assistant"
    assert turns[1].text == "Okay, respectfully received."


def test_parse_transcript_bold_headers() -> None:
    text = (
        "**User - Sat Oct 10, 2026, 9:54:24 AM MDT**\n"
        "\n"
        "is this a true story?\n"
        "\n"
        "**Muse - Sat Oct 10, 2026, 9:54:58 AM MDT**\n"
        "\n"
        "Probably not.\n"
    )
    turns = parse_transcript(text)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].ts == "2026-10-10T15:54:24+00:00"
    assert turns[1].text == "Probably not."


def test_parse_transcript_unknown_speaker_keeps_name() -> None:
    text = "## Morgan — 2026-10-19 10:00:00 MDT\n\nThat pasta was good.\n"
    turns = parse_transcript(text)
    assert turns[0].role == "assistant"
    assert turns[0].text == "Morgan: That pasta was good."


def test_parse_transcript_plain_lines() -> None:
    turns = parse_transcript("User: hello\nMuse: hi there\nUser: thanks")
    assert [t.role for t in turns] == ["user", "assistant", "user"]
    assert turns[1].text == "hi there"


def test_parse_transcript_timestamps_never_regress() -> None:
    # a message whose time cannot be read carries the previous one's time, so the order
    # inside one transcript never breaks
    text = "## Muse — 2026-10-19 09:00:00 MDT\n\nfirst\n\n## Muse — whenever\n\nsecond\n"
    turns = parse_transcript(text)
    assert turns[0].ts == "2026-10-19T15:00:00+00:00"
    assert turns[1].ts == turns[0].ts


def test_looks_like_transcript() -> None:
    assert looks_like_transcript("## Muse — 2026-10-19 09:00:00 MDT\n\ntext")
    assert not looks_like_transcript("# A note\n\nSome prose without speakers.")


def test_safe_rel_flattens_walkups() -> None:
    assert data_import._safe_rel("../../etc/passwd").as_posix() == "etc/passwd"
    assert data_import._safe_rel("a\\b:c*d?.txt").as_posix() == "a/b-c-d-.txt"
    assert data_import._safe_rel("").as_posix() == "imported"


def test_classify_archive_layout() -> None:
    assert data_import._classify(["manifest.json"]) == "manifest"
    assert data_import._classify(["conversations", "chat.md"]) == "conversation"
    assert data_import._classify(["threads", "main.json"]) == "thread"
    assert (
        data_import._classify(
            ["workspace", "your_files", "apps_and_data", "apps", "foo", "index.html"]
        )
        == "app"
    )
    assert data_import._classify(["workspace", "mha.html"]) == "app"
    assert (
        data_import._classify(
            ["workspace", "your_files", "apps_and_data", "chat-transcripts", "x.md"]
        )
        == "transcript"
    )
    assert (
        data_import._classify(["workspace", "your_files", "apps_and_data", "memory", "MEMORY.md"])
        == "memory"
    )
    assert data_import._classify(["workspace", "your_files", "some-note.md"]) == "transcript"
    # anything unrecognized is still somebody's file, never dropped
    assert data_import._classify(["workspace", "photo.png"]) == "file"
    assert data_import._classify(["workspace", "script.py"]) == "file"
    # a document that happens to be a web page stays a document, not a junk app
    assert (
        data_import._classify(["workspace", "your_files", "notes", "documents", "page.html"])
        == "memory"
    )
    # half-written transcript pages and half-written uploads are never content
    assert (
        data_import._classify(["workspace", "your_files", ".transcript-pages", "segA.md"])
        == "skip"
    )
    assert data_import._classify(["notes", ".hatch-fileserver-tmp.x.jpg.123.tmp"]) == "skip"
    assert data_import._classify(["notes", "x.md.tmp"]) == "skip"
    assert data_import._classify(["notes", ".DS_Store"]) == "skip"
    # a Muse export can carry a whole virtualenv under notes; those leftovers are never content
    assert data_import._classify(["notes", "documents", "venv", ".f2py_f2cmap"]) == "skip"
    # a hidden file that carries something, outside a cache folder, is stored
    assert data_import._classify(["notes", "documents", ".f2py_f2cmap"]) == "file"
    # a goal note whose name says "transcript" is a note, not a chat
    assert (
        data_import._classify(["notes", "goals", "x", "call-transcript-setting-nov5.md"])
        == "memory"
    )
    # a copy next to the original is a file, not a second chat
    assert data_import._classify(["chat-transcripts", "x.md.bak"]) == "file"


def test_options_reject_unknown_values() -> None:
    with pytest.raises(ValueError):
        ImportOptions(apps="maybe")
    with pytest.raises(ValueError):
        ImportOptions(main_chat="chaos")


def test_ledger_remembers_by_digest_and_options(tmp_path: Path) -> None:
    ledger = data_import.ImportLedger(tmp_path / "imports.db")
    ledger.open()
    assert not ledger.seen("a", "app", "key1")
    ledger.mark("a", "app", "key1", "x")
    assert ledger.seen("a", "app", "key1")
    # the same bytes under different options are not the same import
    assert not ledger.seen("a", "app", "key2")
    ledger.close()


# ------------------------------------------------------------------ a fake service
class FakeAgent:
    def __init__(self) -> None:
        self.messages: list[Message] = []

    def _save_session(self) -> None:
        pass


# the size-limit test shrinks what a chat keeps, so trimming can be seen on a tiny transcript
_TINY_LIMIT: int | None = None


class FakeThread:
    def __init__(self, tid: str, title: str, threads_dir: Path) -> None:
        self.id = tid
        self.title = title
        self.created_at = "2026-11-01T00:00:00+00:00"
        self.updated_at = self.created_at
        self.timeline = Timeline(tid, threads_dir / f"{tid}.json", max_events=_TINY_LIMIT or 2000)
        self.agent = FakeAgent()

    def last_message_ts(self) -> str:
        newest = ""
        for ev in self.timeline.events:
            if ev.get("type") not in ("user", "assistant"):
                continue
            ts = str(ev.get("ts") or "")
            if ts > newest:
                newest = ts
        return newest

    def refresh_times_from_events(self) -> bool:
        """The real Thread's rule: dates come from the messages."""
        stamps = [str(ev.get("ts") or "") for ev in self.timeline.events if ev.get("ts")]
        if not stamps:
            return False
        first = min(stamps)
        changed = False
        if first < self.created_at:
            self.created_at = first
            changed = True
        last = self.last_message_ts()
        if last and last != self.updated_at:
            self.updated_at = last
            changed = True
        return changed

    def meta(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title}


class FakeBus:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, message: dict[str, Any]) -> None:
        self.published.append(message)


class FakeProfile:
    """The profile the import merges into: a dict with the app's own fields."""

    def __init__(self) -> None:
        self.fields: dict[str, Any] = {
            "name": "",
            "user_name": "",
            "tagline": "",
            "color": "",
            "emoji": "",
            "avatar": "",
            "tone": "",
            "communication": "",
            "style": "",
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self.fields)


class FakeSvc:
    """The slice of MuseService the import touches, with the real Timeline and files."""

    def __init__(self, tmp_path: Path) -> None:
        self.data_dir = tmp_path / "data"
        self.threads_dir = self.data_dir / "threads"
        self.threads_dir.mkdir(parents=True)
        self.workspace_dir = tmp_path / "workspace"
        self.workspace_dir.mkdir()
        self.threads: dict[str, FakeThread] = {}
        self.bus = FakeBus()
        self.profile = FakeProfile()
        self._counter = 0

    def update_profile(self, data: dict[str, Any] | None = None, **fields: Any) -> None:
        # The real MuseService takes one dict. Older tests pass keyword fields.
        merged: dict[str, Any] = {}
        if isinstance(data, dict):
            merged.update(data)
        merged.update(fields)
        self.profile.fields.update({k: v for k, v in merged.items() if v is not None})

    def workspace(self) -> Path:
        return self.workspace_dir

    def create_thread(self, title: str) -> FakeThread:
        self._counter += 1
        tid = f"t_{self._counter:08x}"
        thread = FakeThread(tid, title, self.threads_dir)
        self.threads[tid] = thread
        return thread

    def timeline(self, thread_id: str) -> Timeline:
        """The service's own `timeline()`: make the thread if it is missing."""
        if thread_id not in self.threads:
            self.threads[thread_id] = FakeThread(thread_id, thread_id, self.threads_dir)
        return self.threads[thread_id].timeline

    def _save_index(self) -> None:
        pass

    def rename_thread(self, tid: str, title: str) -> FakeThread | None:
        thread = self.threads.get(tid)
        if thread is not None:
            thread.title = title
        return thread

    def rehome_main(self, owner: str, restore: str | None, keep: bool = True) -> FakeThread | None:
        main = self.threads.pop("main", None)
        if main is not None:
            self._counter += 1
            new_id = f"t_{self._counter:08x}"
            self.threads[new_id] = main
            main.id = new_id
        if restore is not None and restore in self.threads:
            kept = self.threads.pop(restore)
            kept.id = "main"
            self.threads["main"] = kept
        return main


def make_svc(tmp_path: Path) -> FakeSvc:
    svc = FakeSvc(tmp_path)
    return svc


def make_zip(tmp_path: Path, files: dict[str, str]) -> Path:
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


TRANSCRIPT = (
    "## User — 2026-10-17 13:18:45 MDT\n\nfirst thing\n\n"
    "## Muse — 2026-10-17 13:24:23 MDT\n\nfirst reply\n\n"
    "## User — 2026-10-17 13:30:00 MDT\n\nsecond thing\n"
)


def test_import_zip_restores_a_transcript_as_a_chat(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    (tmp_path / "data" / "personal.db").write_bytes(b"")  # not a real db: indexing skipped
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/great-salmon-heist.md": TRANSCRIPT},
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["threads_created"] == 1
    assert result["threads_merged"] == 0
    assert result["events_added"] == 3
    thread = next(t for t in svc.threads.values() if t.title == "great salmon heist")
    roles = [ev["type"] for ev in thread.timeline.events]
    assert roles == ["user", "assistant", "user"]
    # the agent's transcript learned the same messages, so the chat continues
    assert [m.role.value for m in thread.agent.messages] == ["user", "assistant", "user"]
    assert thread.agent.messages[1].meta.get("imported") is True


def test_import_zip_muse_main_becomes_a_side_chat(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    svc.create_thread("Main chat")
    svc.threads["main"] = svc.threads.pop(next(iter(svc.threads)))
    svc.threads["main"].id = "main"
    zip_path = make_zip(
        tmp_path,
        {
            "manifest.json": json.dumps(
                {
                    "turns": [
                        {
                            "role": "user",
                            "content": "old message",
                            "created_at": "2026-10-01T10:00:00Z",
                        },
                        {
                            "role": "assistant",
                            "content": "old reply",
                            "created_at": "2026-10-01T10:00:05Z",
                        },
                    ]
                }
            )
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    # the current main chat is untouched, the Muse main chat is its own side chat
    assert svc.threads["main"] is not None
    assert svc.threads["main"].timeline.events == []
    side = next(t for t in svc.threads.values() if t.title == "Muse main chat")
    assert len(side.timeline.events) == 2
    assert result["threads_created"] == 1


def test_manifest_attachments_link_to_the_staged_files(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    (tmp_path / "data" / "personal.db").write_bytes(b"")  # not a real db: indexing skipped
    manifest = {
        "turns": [
            {
                "role": "user",
                "content": "what is in this picture",
                "created_at": "2026-10-01T10:00:00Z",
                "resources": [
                    {
                        "kind": "file",
                        "uri": "workspace/user/files/pic_1_ab.jpg",
                        "label": "image_0.jpg",
                        "mime_type": "image/jpeg",
                        "size_bytes": 1234,
                        "bundled_file_path": "workspace/user/files/pic_1_ab.jpg",
                    },
                    {
                        "kind": "citation",
                        "uri": "https://example.com/page",
                        "label": "a link",
                        "bundled_file_path": None,
                    },
                ],
            },
            {"role": "assistant", "content": "a cat", "created_at": "2026-10-01T10:00:05Z"},
            {
                "role": "user",
                "content": "and a note it could not bundle",
                "created_at": "2026-10-01T11:00:00Z",
                "resources": [
                    {
                        "kind": "file",
                        "uri": "workspace/user/files/never_bundled.png",
                        "label": "gone.png",
                        "mime_type": "image/png",
                        "size_bytes": 10,
                        # no such member in the zip: the attachment is skipped, not faked
                        "bundled_file_path": "workspace/user/files/never_bundled.png",
                    }
                ],
            },
        ]
    }
    zip_path = make_zip(
        tmp_path,
        {
            "manifest.json": json.dumps(manifest),
            "workspace/user/files/pic_1_ab.jpg": "jpeg-bytes",
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["attachments_linked"] == 1
    side = next(t for t in svc.threads.values() if t.title == "Muse main chat")
    user_ev = side.timeline.events[0]
    assert user_ev["files"] == [
        {
            "path": "imported/uploads/pic_1_ab.jpg",
            "name": "image_0.jpg",
            "size": 1234,
            "kind": "image",
            "mime": "image/jpeg",
        }
    ]
    # the staged file really is at that path, so the chat's thumbnail will load
    stored = svc.workspace_dir / "imported" / "uploads" / "pic_1_ab.jpg"
    assert stored.read_text() == "jpeg-bytes"
    # the turn whose file never made it into the zip gets no attachment, but keeps its text
    last_ev = side.timeline.events[-1]
    assert "files" not in last_ev
    assert last_ev["text"].startswith("and a note")


def test_import_muse_main_replace_sets_the_current_main_aside(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    main = svc.create_thread("Main chat")
    svc.threads.pop(main.id)
    main.id = "main"
    svc.threads["main"] = main
    main.timeline.add({"type": "user", "text": "existing nanoMuse message"})
    zip_path = make_zip(
        tmp_path,
        {
            "manifest.json": json.dumps(
                {
                    "turns": [
                        {
                            "role": "user",
                            "content": "muse message",
                            "created_at": "2026-10-01T10:00:00Z",
                        }
                    ]
                }
            )
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(main_chat="replace"))
    # the imported chat is now the main chat, and the old main chat survived as a side chat
    assert svc.threads["main"].timeline.events[0]["text"] == "muse message"
    archived = next(t for t in svc.threads.values() if t.title == "Previous main chat")
    assert archived.timeline.events[0]["text"] == "existing nanoMuse message"
    assert result["threads_created"] == 1


def test_import_muse_main_merge_interleaves_by_time(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    main = svc.create_thread("Main chat")
    svc.threads.pop(main.id)
    main.id = "main"
    svc.threads["main"] = main
    main.timeline.add({"type": "user", "text": "nanoMuse early", "ts": "2026-10-01T10:00:00+00:00"})
    main.timeline.add({"type": "user", "text": "nanoMuse late", "ts": "2026-10-20T10:00:00+00:00"})
    zip_path = make_zip(
        tmp_path,
        {
            "manifest.json": json.dumps(
                {
                    "turns": [
                        {
                            "role": "user",
                            "content": "muse in between",
                            "created_at": "2026-10-10T10:00:00Z",
                        }
                    ]
                }
            )
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(main_chat="merge"))
    texts = [ev["text"] for ev in svc.threads["main"].timeline.events]
    assert texts == ["nanoMuse early", "muse in between", "nanoMuse late"]
    assert result["threads_merged"] == 1
    # the agent's transcript places the imported message before the thread's own start
    assert svc.threads["main"].agent.messages[0].content == "muse in between"


def test_reimporting_the_same_zip_adds_nothing_twice(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/one.md": TRANSCRIPT},
    )
    first = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert first["events_added"] == 3
    second = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert second["events_added"] == 0
    assert second["duplicates"] == 3
    thread = next(t for t in svc.threads.values() if t.title == "one")
    assert len(thread.timeline.events) == 3
    assert len(thread.agent.messages) == 3


def test_background_memory_indexes_every_message(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/one.md": TRANSCRIPT},
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(background_memory=True))
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT role, content FROM messages_fts").fetchall()
    conn.close()
    assert len(rows) == 3
    assert ("user", "first thing") in rows
    assert result["messages_indexed"] == 3


def test_background_memory_without_restore_indexes_under_an_import_key(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    conn = sqlite3.connect(db)
    conn.commit()
    conn.close()
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/one.md": TRANSCRIPT},
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(restore_chats=False))
    assert result["threads_created"] == 0
    assert result["messages_indexed"] == 3
    conn = sqlite3.connect(db)
    thread_ids = [r[0] for r in conn.execute("SELECT DISTINCT thread_id FROM messages_fts")]
    conn.close()
    assert thread_ids == ["import:one"]


def test_memory_files_land_in_the_deep_archive(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    sqlite3.connect(db).close()
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/memory/MEMORY.md": "# Facts\n- likes cats"},
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["memory_files"] == 1
    stored = svc.data_dir / "muse_memory" / "imports" / "MEMORY.md"
    assert stored.exists()
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT title, content FROM memory_fts").fetchall()
    conn.close()
    assert ("MEMORY.md", "# Facts\n- likes cats") in rows


def test_apps_saved_and_registered(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    sqlite3.connect(db).close()
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/apps_and_data/apps/one-for-all/index.html": "<html>app</html>",
            "workspace/your_files/apps_and_data/apps/one-for-all/style.css": "body{}",
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(apps="both"))
    assert result["apps_saved"] == 1
    assert result["apps_registered"] == 1
    assert (
        svc.workspace_dir / "apps" / "one-for-all" / "index.html"
    ).read_text() == "<html>app</html>"
    assert (svc.workspace_dir / "apps" / "one-for-all" / "style.css").read_text() == "body{}"
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT slug, html_path FROM custom_apps").fetchone()
    conn.close()
    assert row == ("one-for-all", "apps/one-for-all/index.html")


def test_apps_saved_but_not_registered(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    sqlite3.connect(db).close()
    zip_path = make_zip(
        tmp_path, {"workspace/your_files/apps_and_data/apps/game/index.html": "<html>x</html>"}
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(apps="save"))
    assert result["apps_saved"] == 1
    assert result["apps_registered"] == 0
    conn = sqlite3.connect(db)
    table = conn.execute("SELECT name FROM sqlite_master WHERE name='custom_apps'").fetchone()
    count = conn.execute("SELECT count(*) FROM custom_apps").fetchone()[0] if table else 0
    conn.close()
    assert count == 0


def test_existing_files_are_never_overwritten(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    target = svc.workspace_dir / "apps" / "game" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text("mine")
    zip_path = make_zip(
        tmp_path, {"workspace/your_files/apps_and_data/apps/game/index.html": "theirs"}
    )
    import_file(svc, zip_path, "export.zip", ImportOptions(apps="save"))
    assert target.read_text() == "mine"
    alt = svc.workspace_dir / "apps" / "game" / "index-import-2.html"
    assert alt.read_text() == "theirs"


def test_pasted_transcript_becomes_a_chat(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    result = import_text(svc, TRANSCRIPT, "pasted", ImportOptions())
    assert result["events_added"] == 3
    thread = next(t for t in svc.threads.values() if t.title == "pasted")
    assert thread.timeline.events[0]["type"] == "user"


def test_pasted_prose_becomes_a_note_and_memory(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    sqlite3.connect(db).close()
    result = import_text(svc, "Just some plain notes here.", "notes", ImportOptions())
    assert result["memory_files"] == 1
    thread = next(t for t in svc.threads.values() if t.title == "notes")
    notice = thread.timeline.events[0]
    assert notice["type"] == "notice"
    assert "plain notes" in notice["text"]
    conn = sqlite3.connect(db)
    found = conn.execute("SELECT count(*) FROM memory_fts").fetchone()[0]
    conn.close()
    assert found == 1


def test_a_non_zip_binary_file_is_skipped(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    result = import_file(svc, path, "photo.png", ImportOptions())
    assert result["skipped_files"] == 1
    assert result["threads_created"] == 0


def test_import_text_refuses_an_enormous_paste(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    with pytest.raises(ValueError):
        import_text(svc, "x" * (data_import.MAX_TEXT_PASTE_BYTES + 1), "big", ImportOptions())


def test_merge_sorted_keeps_time_order(tmp_path: Path) -> None:
    timeline = Timeline("t", tmp_path / "t.json")
    timeline.add({"type": "user", "text": "early", "ts": "2026-10-01T10:00:00+00:00"})
    timeline.add({"type": "user", "text": "late", "ts": "2026-10-20T10:00:00+00:00"})
    timeline.merge_sorted(
        [
            {"type": "user", "text": "middle", "ts": "2026-10-10T10:00:00+00:00"},
            {"type": "user", "text": "unreadable", "ts": FLOOR_TS},
        ]
    )
    timeline.flush()
    texts = [ev["text"] for ev in Timeline("t", tmp_path / "t.json").events]
    assert texts == ["unreadable", "early", "middle", "late"]


def test_apply_plan_without_restore_indexes_but_creates_no_threads(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    db = svc.data_dir / "personal.db"
    sqlite3.connect(db).close()
    plan = data_import.ImportPlan(
        conversations=[Conversation(title="x", kind="transcript", turns=[Turn("user", "hi")])],
        option_key=ImportOptions().key(),
    )
    stats = apply_plan(svc, plan, ImportOptions(restore_chats=False))
    assert stats["threads_created"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0] == 1
    conn.close()


# ------------------------------------------------------------------ the real API surface
@pytest.fixture()
def api_server(settings: Any, tmp_path: Path):
    """A real service and app, so the endpoints' wiring (streaming, query switches) runs."""
    from starlette.testclient import TestClient

    from nanomuse.llm.mock import MockLLM
    from nanomuse.server.api import create_app
    from nanomuse.server.service import MuseService

    settings.server.token = "secret-token"
    settings.sentinel.always_allow_tools = ["python_execute"]
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        yield client, service


def test_importing_a_nanomuse_export_restores_the_main_chat(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    thread_json = json.dumps(
        {
            "thread": "main",
            "events": [
                {
                    "id": "e1",
                    "type": "user",
                    "text": "old main message",
                    "ts": "2026-10-01T10:00:00+00:00",
                },
                {
                    "id": "e2",
                    "type": "assistant",
                    "text": "old main reply",
                    "ts": "2026-10-01T10:00:05+00:00",
                },
            ],
        }
    )
    zip_path = make_zip(tmp_path, {"threads/main.json": thread_json})
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    # the exported main chat lands in the main chat, not in a side chat named Main chat
    assert svc.threads["main"] is not None
    assert svc.threads["main"].title == "Main chat"
    assert [ev["text"] for ev in svc.threads["main"].timeline.events] == [
        "old main message",
        "old main reply",
    ]
    assert result["threads_created"] == 1
    assert result["threads_merged"] == 0


def test_importing_a_nanomuse_main_chat_merges_into_an_existing_main(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    svc.threads["main"] = FakeThread("main", "Main chat", svc.threads_dir)
    svc.threads["main"].timeline.add(
        {"type": "user", "text": "current message", "ts": "2026-11-01T10:00:00+00:00"}
    )
    thread_json = json.dumps(
        {
            "thread": "main",
            "events": [
                {
                    "id": "e1",
                    "type": "user",
                    "text": "exported message",
                    "ts": "2026-10-01T10:00:00+00:00",
                }
            ],
        }
    )
    zip_path = make_zip(tmp_path, {"threads/main.json": thread_json})
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    texts = [ev["text"] for ev in svc.threads["main"].timeline.events]
    assert texts == ["exported message", "current message"]
    assert result["threads_merged"] == 1


def test_api_import_file_and_text(api_server: Any, tmp_path: Path) -> None:
    client, service = api_server
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/one.md": TRANSCRIPT},
    )
    reply = client.post(
        "/api/data/import?name=export.zip&main_chat=side",
        content=zip_path.read_bytes(),
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["ok"] is True
    assert body["threads_created"] == 1
    assert body["events_added"] == 3
    # the imported chat shows up in the conversation list
    threads = client.get("/api/threads").json()
    titles = [t["title"] for t in threads]
    assert "one" in titles
    # an invalid switch is refused, not guessed
    bad = client.post(
        "/api/data/import?name=export.zip&apps=maybe",
        content=zip_path.read_bytes(),
    )
    assert bad.status_code == 400
    # pasted text goes through the text endpoint
    pasted = client.post(
        "/api/data/import/text?name=pasted",
        content=TRANSCRIPT.encode("utf-8"),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    assert pasted.status_code == 200, pasted.text
    assert pasted.json()["events_added"] == 3


# ------------------------------------------------------------------ no size limit


def test_import_has_no_upload_or_expansion_limit() -> None:
    assert data_import.IMPORT_UPLOAD_MB == 0
    assert data_import.MAX_ZIP_TOTAL_BYTES == 0


def test_a_big_app_file_streams_to_disk_even_past_the_text_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the text cap (what is read into memory) must not drop an app/media file of any size
    monkeypatch.setattr(data_import, "MAX_ZIP_ENTRY_BYTES", 1024)
    monkeypatch.setattr(data_import, "COPY_CHUNK", 4096)
    svc = make_svc(tmp_path)
    sqlite3.connect(svc.data_dir / "personal.db").close()
    big = "x" * 300_000
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/apps_and_data/apps/big-app/index.html": "<html>ok</html>",
            "workspace/your_files/apps_and_data/apps/big-app/data.json": big,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(apps="both"))
    assert result["apps_saved"] == 1
    assert (svc.workspace_dir / "apps" / "big-app" / "data.json").read_text() == big
    assert not list((svc.workspace_dir / "apps").rglob("*.import-part"))
    # an identical re-import writes nothing twice
    again = import_file(svc, zip_path, "export.zip", ImportOptions(apps="both"))
    assert not list((svc.workspace_dir / "apps" / "big-app").glob("*-import-*"))
    assert again["ok"] is True


def test_free_bytes_reads_the_nearest_existing_parent(tmp_path: Path) -> None:
    assert data_import.free_bytes(tmp_path / "does" / "not" / "exist") > 0


def test_muse_bracket_export_parses_into_back_and_forth_messages() -> None:
    text = (
        "Conversation with Muse AI\n\n"
        "[2026-10-03 20:07:47] Muse AI: Hey there. [[hatch_widget:widget-1]]\n"
        "[2026-10-03 20:07:52] You: Muse\n"
        "[2026-10-03 20:08:09] Muse AI: Sorry about that.\n\nWhat should I call you?\n"
        "[2026-10-03 20:08:29] You: Tyler\n"
    )
    turns = data_import.parse_transcript(text, "Conversation with Muse AI_11-17-2026.txt")
    assert [t.role for t in turns] == ["assistant", "user", "assistant", "user"]
    assert turns[0].text == "Hey there."
    assert "What should I call you?" in turns[2].text
    assert turns[0].ts == "2026-10-03T20:07:47+00:00"
    assert data_import.looks_like_transcript(text)
    assert data_import.title_from_text(text, "x.txt") == "Chat with Muse AI"


def test_api_background_import_reports_progress_and_finishes(api_server: Any, tmp_path: Path) -> None:
    import time

    client, _service = api_server
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/one.md": TRANSCRIPT},
    )
    started = client.post(
        "/api/data/import?name=export.zip&main_chat=side&background=true",
        content=zip_path.read_bytes(),
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["job"]["id"]
    job: dict[str, Any] = {}
    for _ in range(100):
        job = client.get(f"/api/data/import/jobs/{job_id}").json()
        if job["phase"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["phase"] == "done", job
    assert job["percent"] == 100
    assert job["result"]["events_added"] == 3
    assert any(j["id"] == job_id for j in client.get("/api/data/import/jobs").json()["jobs"])
    assert client.get("/api/data/import/jobs/nope").status_code == 404
    # nothing is left in staging once the job is done
    assert not list((_service.data_dir / "imports" / "staging").glob("*.upload"))


def test_voice_call_files_get_clean_distinct_titles_and_dates() -> None:
    text = "# Voice Call Chat Transcript\n\n**Tyler:** hi **Muse:** hello **Tyler:** ok **Muse:** bye"
    one = "voice call transcript 2026 10 31.md"
    two = "voice call transcript 2026 11 02.md"
    assert data_import.title_from_text(text, one) == "Voice call - Oct 31"
    assert data_import.title_from_text(text, two) == "Voice call - Nov 2"
    assert data_import.title_from_name("zai next model 2026 11 07.md") == "zai next model"
    turns = data_import.parse_transcript(text, one)
    assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[0].ts.startswith("2026-10-31")


def test_imported_chats_are_dated_by_their_messages(api_server: Any, tmp_path: Path) -> None:
    client, _service = api_server
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/apps_and_data/chat-transcripts/dated.md": TRANSCRIPT},
    )
    assert client.post("/api/data/import?name=e.zip", content=zip_path.read_bytes()).status_code == 200
    made = [t for t in client.get("/api/threads").json() if t["title"] == "dated"][0]
    assert made["updated_at"].startswith("2026-10-17T19:30")
    assert made["created_at"].startswith("2026-10-17T19:18")


# ------------------------------------------------------------------ the archive's own formats

# The archive Muse's builder actually writes: time first, em dash, then the speaker, with
# AM/PM and the zone abbreviation. Half of one chat used the bold bracketed shape instead.
ARCHIVE_TRANSCRIPT = (
    "# Chat transcript - Update chat archive files\n"
    "\n"
    "- Chat ID: 835eec42\n"
    "- Date range: 2026-11-15 to 2026-11-16\n"
    "- Messages: 4\n"
    "\n"
    "## 2026-11-15 9:41 PM MST - Tyler\n"
    "\n"
    "Update it all again\n"
    "\n"
    "## 2026-11-15 9:42 PM MST - Muse\n"
    "\n"
    "Updating now.\n"
    "\n"
    "**[Sat 2026-11-15 21:43:02 MST] Tyler:**\n"
    "\n"
    "And the thumbnails?\n"
    "\n"
    "**[Sat 2026-11-15 21:43:30 MST] Muse:**\n"
    "\n"
    "Fixed too.\n"
)


def test_parse_transcript_timestamp_first_headers() -> None:
    turns = parse_transcript(ARCHIVE_TRANSCRIPT)
    assert [t.role for t in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[0].text == "Update it all again"
    assert turns[0].ts == "2026-11-16T04:41:00+00:00"
    assert turns[3].text == "Fixed too."


def test_one_transcript_may_switch_header_shape() -> None:
    # the same file in two shapes: every message counts, neither shape is dropped
    turns = parse_transcript(ARCHIVE_TRANSCRIPT)
    assert len(turns) == 4


def test_parse_transcript_bold_em_dash_headers() -> None:
    text = (
        "**Tyler \u2014 Sun Oct 11, 2026, 9:15 AM MDT**\n\nanime?\n\n"
        "**Muse \u2014 Sun Oct 11, 2026, 9:16 AM MDT**\n\nweekly.\n"
    )
    turns = parse_transcript(text)
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].ts == "2026-10-11T15:15:00+00:00"


def test_transcript_metadata_line_is_not_a_message() -> None:
    # "- Messages: 4" is the file's own bookkeeping, not a turn
    turns = parse_transcript(ARCHIVE_TRANSCRIPT)
    assert all("Messages:" not in t.text for t in turns)


def test_archive_root_and_declared_counts_are_read(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    profile = {
        "assistant": {"name": "Muse", "tagline": "Here for it", "emoji": "🌀"},
        "user": {"call_name": "Tyler"},
        "avatar": {"face_id": "face-1"},
        "archive_root": "workspace/your_files/minimuse-archive/",
        "counts": {"chat_transcripts_in_archive": 1, "total_transcript_messages": 4},
    }
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["profile_updated"] is True
    assert result["threads_created"] == 1
    assert result["verify"]["ok"] is True, result["verify"]
    assert any("chat transcripts" in line for line in result["verify"]["lines"])


def test_verify_names_a_gap_instead_of_claiming_success(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    profile = {
        "counts": {"chat_transcripts_in_archive": 3, "total_transcript_messages": 40},
    }
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["verify"]["ok"] is False
    assert any("GAP" in line for line in result["verify"]["lines"])


def test_a_switched_off_section_is_a_note_not_a_gap(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    profile = {"counts": {"chat_transcripts_in_archive": 1, "memory_files": 9}}
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions(background_memory=False))
    assert result["verify"]["ok"] is True
    assert any("switched off" in line for line in result["verify"]["lines"])


def test_more_than_declared_is_not_a_gap(tmp_path: Path) -> None:
    # the archive counted itself low: everything it promised is here, and more
    svc = make_svc(tmp_path)
    profile = {"counts": {"chat_transcripts_in_archive": 1, "total_transcript_messages": 3}}
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["verify"]["ok"] is True, result["verify"]
    assert any("more than declared" in line for line in result["verify"]["lines"])


def test_avatar_bundle_and_its_moods_land_in_the_workspace(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    model = {
        "face_id": "face-1",
        "active_image": "static-idle.webp",
        "animated_variants_mp4": {"idle": "idle.mp4", "working": "working.mp4"},
    }
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/avatar/face-1/avatar-model.json": json.dumps(model),
            "workspace/your_files/minimuse-archive/avatar/face-1/frames/static-idle.webp": "webp",
            "workspace/your_files/minimuse-archive/avatar/face-1/frames/static-working.webp": "webp",
            "workspace/your_files/minimuse-archive/avatar/face-1/idle.mp4": "mp4",
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["avatar_files"] == 4
    # the bundle is kept as it was, subfolders included
    assert (svc.workspace_dir / "avatar" / "face-1" / "frames" / "static-idle.webp").exists()
    assert result["avatar_moods"] and result["avatar_moods"] >= 2
    # the moods the app looks for are materialized beside the bundle
    moods = list((svc.workspace_dir / "avatar").rglob("idle.*"))
    assert moods


def test_keep_original_stores_the_zip_and_a_report(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    profile = {"counts": {"chat_transcripts_in_archive": 1, "total_transcript_messages": 4}}
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["verify"]["ok"] is True
    # a second upload of the same bytes is what gets kept, so the fixture zip is untouched
    staging = tmp_path / "staging.upload"
    staging.write_bytes(zip_path.read_bytes())
    kept = data_import.keep_original(svc, staging, "export.zip")
    stored = svc.workspace_dir / Path(kept)
    assert stored.exists() and stored.stat().st_size == zip_path.stat().st_size
    assert stored.parent == svc.workspace_dir / "imports"
    assert not staging.exists()
    reports = list((svc.workspace_dir / "imports").glob("*-verify.md"))
    assert reports
    assert "every declared count matched" in reports[0].read_text(encoding="utf-8")


def test_second_import_of_the_same_memory_file_stores_it_once(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    sqlite3.connect(svc.data_dir / "personal.db").close()
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/notes/memory/MEMORY.md": "# Facts\n- likes cats",
            "workspace/your_files/minimuse-archive/notes/goals/finish-the-app/GROALS.md": "goal text",
        },
    )
    first = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert first["memory_files"] == 2
    second = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert second["memory_files"] == 0
    assert second["duplicates"] >= 2
    stored = list((svc.data_dir / "muse_memory" / "imports").rglob("MEMORY*.md"))
    assert len(stored) == 1


def test_second_import_does_not_double_the_search_index(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    sqlite3.connect(svc.data_dir / "personal.db").close()
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT},
    )
    import_file(svc, zip_path, "export.zip", ImportOptions())
    second = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert second["messages_indexed"] == 0
    conn = sqlite3.connect(svc.data_dir / "personal.db")
    count = conn.execute("SELECT count(*) FROM messages_fts").fetchone()[0]
    conn.close()
    assert count == 4


def test_one_conversation_carried_twice_becomes_one_chat(tmp_path: Path) -> None:
    # the archive keeps the canonical copy and Muse keeps its own working copy: same chat id,
    # so one chat, and the messages that only the second copy has are kept
    head = "- Chat ID: e7ce5fac-3702-4b7f-b7c3-466693085b81\n\n"
    canonical = "# Chat transcript - Garment marks\n\n" + head + (
        "## 2026-10-17 1:18:45 PM MDT - Tyler\n\nhey\n\n"
        "## 2026-10-17 1:24:23 PM MDT - Muse\n\nhello\n"
    )
    working = "# Chat Transcript - garment-mark-weight-loss-chat-2026-10-17\n\n" + head + (
        "## 2026-10-17 1:18:45 PM MDT - Tyler\n\nhey\n\n"
        "## 2026-10-17 1:24:23 PM MDT - Muse\n\nhello\n\n"
        "## 2026-10-17 1:30:00 PM MDT - Tyler\n\none more\n"
    )
    svc = make_svc(tmp_path)
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/chat-transcripts/2026-10-17-garment-chat-transcript.md": canonical,
            "workspace/your_files/apps_and_data/chat-transcripts/garment-mark.md": working,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["conversations"] == 1
    assert result["threads_created"] == 1
    thread = next(t for t in svc.threads.values() if t.title)
    assert len(thread.timeline.events) == 3
    assert [e["text"] for e in thread.timeline.events][-1] == "one more"


def _capped_timeline(cap: int):
    """A Timeline class whose chats keep only ``cap`` messages, for the size-limit test."""
    from nanomuse.server.events import Timeline as RealTimeline

    class Capped(RealTimeline):
        def __init__(self, thread_id, path, max_events: int = 2000) -> None:
            super().__init__(thread_id, path, max_events=cap)

    return Capped


def test_reimport_does_not_refill_a_chat_past_its_size_limit(tmp_path: Path) -> None:
    # a chat keeps its newest messages only; an older message that was trimmed must not be
    # added back on every import (it would be trimmed again, forever)
    svc = make_svc(tmp_path)
    lines = ["# Chat transcript - long chat\n"]
    for i in range(6):
        role = "Tyler" if i % 2 == 0 else "Muse"
        lines.append(f"## 2026-10-17 1:{i:02d}:00 PM MDT - {role}\n\nmessage {i}\n")
    long_chat = "\n".join(lines)
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/minimuse-archive/chat-transcripts/long-chat-transcript.md": long_chat},
    )
    globals()["_TINY_LIMIT"] = 3
    try:
        first = import_file(svc, zip_path, "export.zip", ImportOptions())
        assert first["events_added"] == 6  # all six were new on the first run
        second = import_file(svc, zip_path, "export.zip", ImportOptions())
        assert second["events_added"] == 0
        thread = next(t for t in svc.threads.values() if t.title)
        assert len(thread.timeline.events) == 3
    finally:
        globals()["_TINY_LIMIT"] = None


def test_api_import_copies_list_and_delete(api_server: Any, tmp_path: Path) -> None:
    client, service = api_server
    profile = {"counts": {"chat_transcripts_in_archive": 1, "total_transcript_messages": 4}}
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT,
        },
    )
    reply = client.post(
        "/api/data/import?name=export.zip&keep_copy=true",
        content=zip_path.read_bytes(),
    )
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["kept_copy"]
    kept_name = Path(body["kept_copy"]).name
    copies = client.get("/api/data/import/copies").json()
    names = [c["name"] for c in copies]
    assert kept_name in names
    assert any(c["name"].endswith("-verify.md") and c["report"] for c in copies)
    # a name that tries to escape the folder is refused, not resolved
    assert client.delete("/api/data/import/copies/..%2F..%2Fsecrets.txt").status_code != 200
    deleted = client.delete(f"/api/data/import/copies/{kept_name}")
    assert deleted.status_code == 200
    assert kept_name not in [c["name"] for c in client.get("/api/data/import/copies").json()]


def test_api_import_without_keep_copy_leaves_no_original(api_server: Any, tmp_path: Path) -> None:
    client, _service = api_server
    zip_path = make_zip(
        tmp_path,
        {"workspace/your_files/minimuse-archive/chat-transcripts/one-chat-transcript.md": ARCHIVE_TRANSCRIPT},
    )
    reply = client.post(
        "/api/data/import?name=export.zip&keep_copy=false",
        content=zip_path.read_bytes(),
    )
    assert reply.status_code == 200, reply.text
    assert reply.json()["kept_copy"] is None
    assert client.get("/api/data/import/copies").json() == []

# ------------------------------------------------------------------ scheduled jobs
def _jobs_zip(tmp_path: Path, jobs_md: str) -> Path:
    return make_zip(
        tmp_path,
        {
            "manifest.json": json.dumps({"turns": []}),
            "workspace/your_files/minimuse-archive/system/scheduled-jobs.md": jobs_md,
        },
    )


JOBS_MD = """# Scheduled jobs — Tyler's cron jobs
Generated: Wed Nov 18, 2026 ~10:44 PM MST (auto top-up), from a live cron list.

## Enabled — recurring
| Job ID | Title | Schedule | Owner |
|---|---|---|---|
| minimuse-archive-auto-update | MiniMuse archive auto-update (every 30 min) | every 30m | goal:minimuse-complete-archive |
| daily-scripture-study-reminder | Daily scripture study reminder | daily 07:42 | goal:daily-scripture-study |
| plasma-donation-prep | Plasma donation prep | weekly Wed,Fri 06:30 | goal:plasma-donation-routine |
| miranda-reply-watch | Watch for Miranda (RISE) reply | every 1h | goal:rise-shift-follow-up |
| heartbeat | (heartbeat) | every 30m | — |

## Enabled — one-time (future)
| Job ID | Title | Fires |
|---|---|---|
| gamecube-ask | Ask Jonah & Ellie about the GameCube | {future} |

## Disabled (kept definitions, not firing)
call-josiah-reminder, call-juan-reminder, nov18-day-wrap (fired Nov 18, 2026 21:42).
"""


def test_scheduled_jobs_continue_running_here(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from nanomuse.reminders.store import ReminderStore

    svc = make_svc(tmp_path)
    (tmp_path / "data" / "personal.db").write_bytes(b"")
    svc.app = SimpleNamespace(reminders=ReminderStore(tmp_path / "data" / "reminders.db"))
    from datetime import datetime, timedelta

    soon = (datetime.now() + timedelta(days=6)).strftime("%b %d, %Y %H:%M")
    zip_path = _jobs_zip(tmp_path, JOBS_MD.format(future=soon))
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    # scripture (1) + plasma on two days (2) + hourly watch (1) + the one-time ask (1);
    # the archive auto-update and the heartbeat are Muse's own plumbing and stay out
    assert result["tasks_created"] == 5
    store = svc.app.reminders
    active = store.list("active")
    scripture = next(r for r in active if r.text == "Daily scripture study reminder")
    assert scripture.kind == "task" and scripture.repeat == "daily 07:42"
    plasma = [r for r in active if r.text == "Plasma donation prep"]
    assert sorted(r.repeat for r in plasma) == ["weekly fri 06:30", "weekly wed 06:30"]
    watch = next(r for r in active if r.text == "Watch for Miranda (RISE) reply")
    assert watch.repeat == "every 1h"
    ask = next(r for r in active if r.text == "Ask Jonah & Ellie about the GameCube")
    assert ask.kind == "remind" and ask.repeat == ""

    # the same archive again: every job is already in place, nothing is duplicated
    zip_path2 = _jobs_zip(tmp_path, JOBS_MD.format(future=soon))
    again = import_file(svc, zip_path2, "export.zip", ImportOptions())
    assert again["tasks_created"] == 0
    assert len([r for r in store.list("active") if r.text == "Daily scripture study reminder"]) == 1

    # Muse turns a job off: the next transfer cancels it here
    turned_off = JOBS_MD.format(future=soon).replace(
        "| daily-scripture-study-reminder | Daily scripture study reminder | daily 07:42 |",
        "",
    ).replace(
        "call-josiah-reminder, call-juan-reminder,",
        "call-josiah-reminder, call-juan-reminder, daily-scripture-study-reminder,",
    )
    zip_path3 = _jobs_zip(tmp_path, turned_off)
    third = import_file(svc, zip_path3, "export.zip", ImportOptions())
    assert third["tasks_cancelled"] == 1
    ids = {r.id for r in store.list(None) if r.text == "Daily scripture study reminder"}
    assert all(store.get(i).status == "cancelled" for i in ids)  # type: ignore[union-attr]

    # the switch: with continue_tasks off, the jobs are read but never made
    svc2 = make_svc(tmp_path / "off")
    (tmp_path / "off" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "off" / "data" / "personal.db").write_bytes(b"")
    svc2.app = SimpleNamespace(reminders=ReminderStore(tmp_path / "off" / "data" / "reminders.db"))
    off = import_file(svc2, _jobs_zip(tmp_path / "off", JOBS_MD.format(future=soon)), "e.zip",
                      ImportOptions(continue_tasks=False))
    assert off["tasks_created"] == 0
    assert svc2.app.reminders.list("active") == []


def test_delta_transfer_is_understood_and_reports_its_code(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    (tmp_path / "data" / "personal.db").write_bytes(b"")
    profile = {
        "scope": "delta",
        "since_code": "2026-11-18T17:42:03Z",
        "built_at": "2026-11-21T04:00:47Z",
        "archive_root": "workspace/your_files/minimuse-archive/",
        "counts": {"chat_transcripts_in_archive": 1, "total_transcript_messages": 2},
        "excluded": [{"area": "photos I sent", "count": 43, "why": "not exportable"}],
    }
    delta_transcript = (
        "## User — 2026-11-20 19:00:00 MST\n\nnew thing\n\n"
        "## Muse — 2026-11-20 19:01:00 MST\n\nnew reply\n"
    )
    zip_path = make_zip(
        tmp_path,
        {
            "workspace/your_files/minimuse-archive/profile/muse-profile.json": json.dumps(profile),
            "workspace/your_files/minimuse-archive/chat-transcripts/"
            "2026-11-20-evening-check-transcript.md": delta_transcript,
        },
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["scope"] == "delta"
    assert result["since_code"] == "2026-11-18T17:42:03Z"
    # the code the next delta pastes above the prompt is this archive's build moment
    assert result["transfer_code"] == "2026-11-21T04:00:47Z"
    assert result["excluded"] == [{"area": "photos I sent", "count": 43, "why": "not exportable"}]
    assert result["events_added"] == 2
    assert result["verify"]["ok"] is True
    joined = "\n".join(result["verify"]["lines"])
    assert "delta update built against 2026-11-18T17:42:03Z" in joined
    assert "Muse could not include photos I sent (43) - not exportable" in joined


def test_full_import_still_offers_a_transfer_code(tmp_path: Path) -> None:
    svc = make_svc(tmp_path)
    (tmp_path / "data" / "personal.db").write_bytes(b"")
    zip_path = make_zip(
        tmp_path, {"manifest.json": json.dumps({"turns": [], "exported_at": "2026-11-21T04:00:47Z"})}
    )
    result = import_file(svc, zip_path, "export.zip", ImportOptions())
    assert result["scope"] == "full"
    assert result["transfer_code"] == "2026-11-21T04:00:47Z"
    # an archive that says nothing about when it was built still gets a usable code: now
    zip_path2 = make_zip(tmp_path, {"manifest.json": json.dumps({"turns": []})})
    result2 = import_file(svc, zip_path2, "export.zip", ImportOptions())
    assert result2["transfer_code"].endswith("Z") and "T" in result2["transfer_code"]
