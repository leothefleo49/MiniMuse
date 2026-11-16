"""The usage ledger and the words the Usage screen shows: what a turn costs, what it
did (tool calls by activity, pictures and clips), and the replies that decide an
approval card straight from the chat."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanomuse import usage
from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.server.api import create_app
from nanomuse.server.service import MuseService, _chat_approval_verdict
from nanomuse.server.usage_ledger import UsageLedger, media_kind
from nanomuse.usage import estimate_turn_cost, set_user_rates, tool_activity_counts


def test_media_kind_images_and_clips() -> None:
    assert media_kind("photo.png") == "image"
    assert media_kind("shot.JPG") == "image"
    assert media_kind("clip.mp4") == "video"
    assert media_kind("recording.webm") == "video"
    assert media_kind("notes.txt") == ""
    assert media_kind("no-extension") == ""


def test_record_and_summarize(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    trace = {
        "prompt_tokens": 20000,
        "cache_tokens": 18000,
        "reasoning_tokens": 700,
        "completion_tokens": 900,
        "duration_s": 4.5,
        "tool_calls": 3,
        "model_calls": 1,
        "tool_names": ["files", "web_search", "shell"],
    }
    entry = ledger.record_turn(
        ts="2026-11-10T12:00:00+00:00",
        thread="main",
        model="deepseek-flash",
        source="person",
        trace=trace,
        tool_actions={"0:files": "write"},
        artifacts=[{"path": "poster.png", "action": "write"}],
        attachments=[{"name": "clip.mp4"}],
    )
    assert entry["activities"]["files_written"] == 1
    assert entry["activities"]["files_read"] == 0
    assert entry["activities"]["reading"] == 1
    assert entry["activities"]["commands"] == 1
    assert entry["media"] == {
        "image_generation": 1,
        "image_editing": 0,
        "video_generation": 0,
        "video_editing": 0,
        "photo_analysis": 0,
        "video_analysis": 1,
    }
    # a priced model records an estimate; the thinking tokens fold into the reply count
    assert entry["cost_usd"] > 0
    assert entry["reasoning_tokens"] == 700

    summary = ledger.summary(days=30)
    assert summary["turns"] == 1
    assert summary["prompt_tokens"] == 20000
    assert summary["cached_tokens"] == 18000
    assert summary["completion_tokens"] == 900
    assert summary["reasoning_tokens"] == 700
    assert abs(summary["cost_usd"] - entry["cost_usd"]) < 0.0001
    assert summary["cost_usd"] > 0
    assert summary["tool_calls"] == 3
    assert summary["per_day"][0]["day"] == "2026-11-10"
    assert summary["per_model"][0]["model"] == "deepseek-flash"
    assert summary["activities"]["files_written"] == 1
    assert summary["media"]["video_analysis"] == 1
    assert summary["recent"][0]["model"] == "deepseek-flash"


def test_local_models_cost_nothing() -> None:
    assert estimate_turn_cost("qwen3.5:9b-32k", 20_000, 18_000, 900) == 0.0


def test_deepseek_peak_hours_double() -> None:
    # a Sunday evening is off-peak all day; a Tuesday 02:00 UTC is peak (deepseek)
    sunday = datetime(2026, 11, 8, 20, 0, tzinfo=UTC)
    peak = datetime(2026, 11, 10, 2, 30, tzinfo=UTC)
    off = estimate_turn_cost("deepseek-flash", 1000, 0, 1000, now=sunday)
    double = estimate_turn_cost("deepseek-flash", 1000, 0, 1000, now=peak)
    assert off > 0
    assert double == off * 2
    # the other vendors have no peak hour: their rate is the rate
    gemini_sunday = estimate_turn_cost("gemini-3.1-flash-lite", 1000, 0, 1000, now=sunday)
    gemini_tuesday = estimate_turn_cost("gemini-3.1-flash-lite", 1000, 0, 1000, now=peak)
    assert gemini_sunday == gemini_tuesday > 0


def test_owner_rate_card_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage, "_USER_RATES", {})
    set_user_rates("my-own-model", 1.0, 2.0)
    cost = estimate_turn_cost("my-own-model", 1_000_000, 0, 1_000_000)
    assert cost == 3.0
    # a zero/zero card is not a card: the one set before it stays
    set_user_rates("my-own-model", 0, 0)
    assert usage.rates_for("my-own-model") == {"in": 1.0, "hit": 1.0, "out": 2.0}


def test_tool_activity_counts() -> None:
    counts = tool_activity_counts(
        ["files", "files", "web_search", "coding", "send_email", "unknown_tool"],
        {"0:files": "read", "1:files": "write"},
    )
    assert counts["files_read"] == 1
    assert counts["files_written"] == 1
    assert counts["reading"] == 1
    assert counts["coding"] == 1
    assert counts["apis"] == 1
    assert counts["commands"] == 0


def test_summary_survives_a_torn_line(tmp_path: Path) -> None:
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    ledger.record_turn(
        ts=datetime.now(UTC).isoformat(timespec="seconds"),
        thread="main",
        model="qwen3.5:9b-32k",
        source="person",
        trace={"prompt_tokens": 100, "completion_tokens": 10},
    )
    with ledger.path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-11-10T00:00:0')  # a crash mid-write
    ledger.record_turn(
        ts=datetime.now(UTC).isoformat(timespec="seconds"),
        thread="main",
        model="qwen3.5:9b-32k",
        source="background",
        trace={"prompt_tokens": 50, "completion_tokens": 5},
    )
    summary = ledger.summary(days=30)
    assert summary["turns"] == 2
    assert summary["cost_usd"] == 0.0  # the local model is free, always


def test_chat_approval_verdict() -> None:
    for word in ("yes", "yep", "ok", "go ahead", "do it", "sounds good", "please"):
        assert _chat_approval_verdict(word) is True, word
    for word in ("no", "nope", "cancel it", "do not", "stop", "never mind", "deny"):
        assert _chat_approval_verdict(word) is False, word
    # the same in Chinese: the whole message is one short phrase
    for word in ("好", "好的", "可以", "行", "批准", "同意", "没问题", "去做吧"):
        assert _chat_approval_verdict(word) is True, word
    for word in ("不", "不行", "取消", "停下", "算了", "不同意"):
        assert _chat_approval_verdict(word) is False, word
    # a Chinese instruction that merely contains a decision word stays a message
    assert _chat_approval_verdict("不要用pro回答") is None
    assert _chat_approval_verdict("好的，再加一段") is None
    # anything longer or mixed is a message for the agent, not a decision
    assert _chat_approval_verdict("") is None
    assert _chat_approval_verdict("Yes, and also add a paragraph about the plan") is None
    assert _chat_approval_verdict("sure thing") is None
    assert _chat_approval_verdict("yesss that would be great") is None


def test_usage_endpoint_works_signed_out(settings: Settings) -> None:
    settings.server.token = ""
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    with TestClient(app) as client:
        r = client.get("/api/usage?days=7")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["days"] == 7 and body["turns"] == 0
        assert body["per_model"] == [] and body["activities"] == {}
