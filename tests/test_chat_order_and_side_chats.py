"""How the conversation list is ordered, and why imported chats belong in it."""

from __future__ import annotations

import json
from typing import Any

from nanomuse.llm.mock import MockLLM
from nanomuse.server.service import MuseService


def test_an_imported_chat_is_dated_by_its_messages(settings: Any) -> None:
    # an archive imported today still starts where its first message was, and sorts by its
    # newest one: the dates a chat is shown by are the chat's, not the transfer's
    service = MuseService(settings, llm=MockLLM([]))
    thread = service.create_thread("Imported")
    thread.created_at = "2026-11-19T00:00:00+00:00"
    thread.updated_at = "2026-11-19T00:00:00+00:00"
    thread.timeline.add({"type": "user", "text": "old", "ts": "2026-10-01T10:00:00+00:00"})
    thread.timeline.add({"type": "assistant", "text": "older", "ts": "2026-10-01T10:01:00+00:00"})

    assert thread.refresh_times_from_events() is True
    assert thread.created_at == "2026-10-01T10:00:00+00:00"
    assert thread.updated_at == "2026-10-01T10:01:00+00:00"


def test_a_restart_repairs_the_order_of_chats_imported_before_this_rule(settings: Any) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    older = service.create_thread("Older chat")
    older.timeline.add({"type": "user", "text": "a", "ts": "2026-10-01T10:00:00+00:00"})
    newer = service.create_thread("Newer chat")
    newer.timeline.add({"type": "user", "text": "b", "ts": "2026-11-10T10:00:00+00:00"})
    # what an older import left behind: both stamped with the import time
    for thread in (older, newer):
        thread.updated_at = "2026-11-19T00:00:00+00:00"
        thread.created_at = "2026-11-19T00:00:00+00:00"
        thread.timeline.flush()
    service._save_index()

    # a restart reads the index and repairs each chat from its messages
    service._load_threads()
    titles = [t.title for t in service.visible_threads()]
    assert titles.index("Newer chat") < titles.index("Older chat")


def test_a_shell_with_only_notices_sorts_below_real_chats(settings: Any) -> None:
    # an older import turned a document into an empty chat and stamped it with the import
    # time; it must not sit above conversations people actually spoke in
    service = MuseService(settings, llm=MockLLM([]))
    real = service.create_thread("Real chat")
    real.timeline.add({"type": "user", "text": "hi", "ts": "2026-11-01T10:00:00+00:00"})
    real.timeline.add({"type": "assistant", "text": "hello", "ts": "2026-11-01T10:01:00+00:00"})
    real.refresh_times_from_events()
    shell = service.create_thread("v9")
    shell.timeline.add(
        {
            "type": "notice",
            "text": "Imported from workspace/your_files/notes/v9.md",
            "ts": "2026-11-19T02:33:18+00:00",
        }
    )
    shell.updated_at = "2026-11-19T02:33:18+00:00"
    titles = [t.title for t in service.visible_threads()]
    assert titles.index("Real chat") < titles.index("v9")


def test_the_list_is_newest_activity_first(settings: Any) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    a = service.create_thread("A")
    a.timeline.add({"type": "user", "text": "x", "ts": "2026-10-01T10:00:00+00:00"})
    a.refresh_times_from_events()
    b = service.create_thread("B")
    b.timeline.add({"type": "assistant", "text": "y", "ts": "2026-11-11T10:00:00+00:00"})
    b.refresh_times_from_events()
    titles = [t.title for t in service.visible_threads()]
    assert titles.index("B") < titles.index("A")


def test_a_chat_with_no_messages_keeps_its_dates(settings: Any) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    thread = service.create_thread("Empty")
    before = thread.created_at
    assert thread.refresh_times_from_events() is False
    assert thread.created_at == before


def test_side_chats_sync_by_default_and_honour_an_explicit_off(settings: Any) -> None:
    (settings.data_dir / "sync.json").write_text(
        json.dumps({"side_chats": False, "peer_cursors": {"peer": {"in": 9, "out": 4}}}),
        encoding="utf-8",
    )
    service = MuseService(settings, llm=MockLLM([]))
    # never chosen: your own paired devices get your side chats, and the cursor that had
    # skipped them is rewound once so the missed chats arrive
    assert service.sync.side_chats is True
    assert service.sync.state["peer_cursors"]["peer"] == {"in": 0, "out": 0}
    assert service.sync.state["side_chats_backfilled"] is True


def test_turning_side_chats_off_is_remembered(tmp_path: Any, settings: Any) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    service.sync.set_side_chats(False)
    assert service.sync.side_chats is False
    state = json.loads((settings.data_dir / "sync.json").read_text(encoding="utf-8"))
    assert state["side_chats_set"] is True
    assert state["side_chats"] is False
    # a fresh engine reading the same file keeps the choice
    again = MuseService(settings, llm=MockLLM([]))
    assert again.sync.side_chats is False
