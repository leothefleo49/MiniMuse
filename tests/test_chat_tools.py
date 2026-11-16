"""The react tool: the agent's emoji on the user's message (up to three)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanomuse.server.events import Timeline
from nanomuse.tools.chat_tools import React, parse_emojis


class FakeUI:
    """The slice of the web UI the tool touches: the thread, its timeline, and patch."""

    def __init__(self, timeline: Timeline):
        self._timeline = timeline
        self.patches: list[tuple[str, str, dict[str, Any]]] = []

    def thread(self) -> str:
        return self._timeline.thread_id

    def get_timeline(self, thread: str) -> Timeline:
        assert thread == self._timeline.thread_id
        return self._timeline

    def patch(self, thread: str, event_id: str, **fields: Any) -> None:
        self.patches.append((thread, event_id, fields))
        self._timeline.update(event_id, **fields)


def make_ui(tmp_path: Path) -> tuple[FakeUI, Timeline]:
    timeline = Timeline("main", tmp_path / "timeline.json")
    return FakeUI(timeline), timeline


def test_parse_splits_and_caps_at_three():
    assert parse_emojis("🔥 ✨ 💜") == ["🔥", "✨", "💜"]
    assert parse_emojis("🔥 ✨ 💜 🎉 🙌") == ["🔥", "✨", "💜"]  # at most three
    assert parse_emojis("🔥,✨") == ["🔥", "✨"]  # commas are separators
    assert parse_emojis(["🔥", "✨"]) == ["🔥", "✨"]
    assert parse_emojis("🔥 🔥") == ["🔥"]  # no duplicates


def test_parse_keeps_only_emoji():
    assert parse_emojis("lol 🔥") == ["🔥"]  # a word is not an emoji
    assert parse_emojis("great job") == []
    assert parse_emojis("123") == []
    assert parse_emojis("中文") == []
    assert parse_emojis("") == []
    # multi-codepoint emoji stay one piece (skin tone, ZWJ family, flag)
    assert parse_emojis("👍🏽") == ["👍🏽"]
    assert parse_emojis("👨‍👩‍👧‍👦") == ["👨‍👩‍👧‍👦"]
    assert parse_emojis("🇺🇸") == ["🇺🇸"]


async def test_react_marks_the_latest_user_message(tmp_path: Path):
    ui, timeline = make_ui(tmp_path)
    user = timeline.add({"type": "user", "text": "I got the job!"})
    timeline.add({"type": "assistant", "text": "Congratulations!"})
    tool = React(ui=ui)

    r = await tool.execute(emojis="🎉 ✨")

    assert r.ok, r.output
    assert timeline.get(user["id"])["reactions"] == ["🎉", "✨"]  # the user message, not the reply
    assert ui.patches == [("main", user["id"], {"reactions": ["🎉", "✨"]})]


async def test_react_replaces_what_was_there(tmp_path: Path):
    ui, timeline = make_ui(tmp_path)
    user = timeline.add({"type": "user", "text": "look at this"})
    tool = React(ui=ui)
    await tool.execute(emojis="🔥")
    await tool.execute(emojis="😂 💜", message=user["id"])
    assert timeline.get(user["id"])["reactions"] == ["😂", "💜"]  # replaced, not piled up


async def test_react_can_name_the_message(tmp_path: Path):
    ui, timeline = make_ui(tmp_path)
    first = timeline.add({"type": "user", "text": "one"})
    timeline.add({"type": "user", "text": "two"})
    r = await React(ui=ui).execute(emojis="👍", message=first["id"])
    assert r.ok
    assert timeline.get(first["id"])["reactions"] == ["👍"]
    assert "reactions" not in timeline.events[1]


async def test_react_refuses_a_non_user_event_and_no_emoji(tmp_path: Path):
    ui, timeline = make_ui(tmp_path)
    reply = timeline.add({"type": "assistant", "text": "hello"})
    tool = React(ui=ui)

    r = await tool.execute(emojis="🔥", message=reply["id"])
    assert not r.ok and "not one of their messages" in (r.error or "")
    assert not (await tool.execute(emojis="nice work")).ok
    assert not (await tool.execute()).ok


async def test_react_with_nothing_of_theirs_yet(tmp_path: Path):
    ui, timeline = make_ui(tmp_path)
    timeline.add({"type": "assistant", "text": "hello"})
    r = await React(ui=ui).execute(emojis="🔥")
    assert not r.ok and "no message" in (r.error or "")


def test_react_schema_is_offered_to_the_model():
    p = React(ui=None).to_param()["function"]
    assert p["name"] == "react"
    assert "emojis" in p["parameters"]["properties"]
    # the shared step words ride along, like every tool
    assert "step" in p["parameters"]["properties"]
