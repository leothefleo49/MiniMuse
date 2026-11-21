"""v1.2.23 regressions: imported AI replies sync, Muse export shapes parse, limits are roomy."""

from __future__ import annotations

from nanomuse.server import data_import as di
from nanomuse.sync import peer


def test_peer_limits_allow_long_ai_replies() -> None:
    assert peer.LIMITS["text_bytes"] >= 256 * 1024
    assert peer.LIMITS["messages"] >= 100_000


def test_weekday_dash_time_header_parses() -> None:
    text = (
        "# Chat Transcript\n\n- **Chat ID:** 0db9d05f-1ec8-4f6a-a5d0-8b60653fa523\n\n---\n\n"
        "## Mon, Nov 2, 2026 — 1:01 PM MST — Tyler\n\nhello there friend\n\n"
        "## Mon, Nov 2, 2026 — 1:02 PM MST — Muse\n\nhi, how can I help today?\n"
    )
    turns = di.parse_transcript(text, "x.md")
    assert [t.role for t in turns] == ["user", "assistant"]
    assert di._chat_id_of(text) == "0db9d05f-1ec8-4f6a-a5d0-8b60653fa523"


def test_metadata_bullets_are_not_speakers() -> None:
    text = (
        "# Food Chat\n\n- **Chat:** Meal rotation\n- **Saved:** Nov 7\n- **Span:** Oct 5\n\n"
        "### Tyler — Mon, Oct 5, 2026, 1:18 PM MDT\n\nwhat should I eat tonight?\n\n"
        "### Muse — Mon, Oct 5, 2026, 1:20 PM MDT\n\nTry the rice bowls.\n"
    )
    turns = di.parse_transcript(text, "food.md")
    assert [t.role for t in turns] == ["user", "assistant"]


def test_backup_and_user_upload_folders_are_files_not_chats() -> None:
    assert di._classify(["workspace", "user", "files", "chat_transcript_70.txt"]) == "file"
    assert di._classify(["workspace", "your_files", "apps_source_code", "x", "a.py"]) in ("file", "app")
    assert di._classify(["workspace", "your_files", "proactive_messages", "research", "a.md"]) == "memory"
    assert di._classify(["workspace", "your_files", "asmr-watch", "venv", "lib", "x.md"]) == "skip"
