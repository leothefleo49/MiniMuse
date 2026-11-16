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
    assert data_import._classify(["workspace", "photo.png"]) == "skip"


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


class FakeThread:
    def __init__(self, tid: str, title: str, threads_dir: Path) -> None:
        self.id = tid
        self.title = title
        self.created_at = "2026-11-01T00:00:00+00:00"
        self.timeline = Timeline(tid, threads_dir / f"{tid}.json")
        self.agent = FakeAgent()

    def meta(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title}


class FakeBus:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def publish(self, message: dict[str, Any]) -> None:
        self.published.append(message)


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
        self._counter = 0

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
