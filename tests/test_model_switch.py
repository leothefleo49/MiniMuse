"""The chat model switch: one turn on a named model (*use flash / use pro / use local*),
the wording rule, the DeepSeek builders, the rate card and the per-turn cost line."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from nanomuse import usage as usage_pricing
from nanomuse.config import Settings
from nanomuse.instrumentation import InstrumentationRegistry, TaskTrace
from nanomuse.llm import MockLLM
from nanomuse.server.events import MAIN_THREAD
from nanomuse.server.service import MuseService, _model_alias_in_text
from nanomuse.usage import estimate_turn_cost, usage_note

WEDNESDAY_NOON = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
WEDNESDAY_PEAK = datetime(2026, 11, 11, 7, 0, tzinfo=UTC)
SATURDAY_PEAK_HOUR = datetime(2026, 11, 14, 7, 0, tzinfo=UTC)


@pytest.fixture()
def service(settings: Settings) -> MuseService:
    InstrumentationRegistry.configure(settings.data_dir)
    svc = MuseService(settings, llm=MockLLM([]))
    svc.threads[MAIN_THREAD] = svc._make_thread(MAIN_THREAD, "Main chat")
    return svc


# ------------------------------------------------------------------ the wording


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("use pro for this", "pro"),
        ("let's use the new flash", "flash"),
        ("switch to local", "local"),
        ("can you use deepseek", "deepseek"),
        ("hop on qwen", "qwen"),
    ],
)
def test_alias_matches_the_saying(text: str, word: str) -> None:
    assert _model_alias_in_text(text, word)


@pytest.mark.parametrize(
    "text",
    [
        "don't use pro for this",
        "do not use flash for this",
        "never use qwen again",
        "the flash of lightning scared me",
        "use a flash drive for the backup",
        "use the flashlight",
        "Flash, what's the plan",
    ],
)
def test_alias_rejects_the_sayings_that_are_not_a_switch(text: str) -> None:
    assert not any(_model_alias_in_text(text, word) for word in ("flash", "pro", "local"))


def test_gemini_keeps_its_direct_address() -> None:
    assert _model_alias_in_text("Gemini, what's the plan", "gemini", direct_address=True)
    assert not _model_alias_in_text("Gemini, what's the plan", "flash")


# ------------------------------------------------------------------ the builders


def test_deepseek_once_builds_the_client(service: MuseService) -> None:
    service.app.vault.set("DEEPSEEK_API_KEY", "sk-test")
    llm, model = service.deepseek_once("deepseek-v4-pro", thinking="enabled")
    assert model == "deepseek-v4-pro"
    assert llm is not None
    assert llm.settings.model == "deepseek-v4-pro"
    assert llm.settings.base_url == "https://api.deepseek.com/v1"
    assert llm.settings.pass_reasoning is True
    assert llm.settings.extra_body == {"thinking": {"type": "enabled"}}


def test_flash_once_thinks_off(service: MuseService) -> None:
    service.app.vault.set("DEEPSEEK_API_KEY", "sk-test")
    llm, model = service.alias_once("flash")
    assert model == "deepseek-flash"
    assert llm is not None
    assert llm.settings.extra_body == {"thinking": {"type": "disabled"}}


def test_alias_once_without_a_key_keeps_the_configured_model(service: MuseService) -> None:
    llm, model = service.alias_once("flash")
    assert llm is None
    assert model == service.settings.llm.model


def test_local_once_points_at_ollama(service: MuseService) -> None:
    llm, model = service.alias_once("local")
    assert model == "qwen3.5:9b-32k"
    assert llm is not None
    assert llm.settings.base_url == "http://127.0.0.1:11434/v1"
    assert llm.settings.api_key == "ollama"
    assert llm.settings.extra_body == {"options": {"num_ctx": 32768}, "keep_alive": "24h"}


# ------------------------------------------------- the composer pill's explicit pick


def test_send_takes_an_explicit_model(
    service: MuseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.app.vault.set("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(service, "_ensure_worker", lambda thread: None)  # no loop here
    service.send(MAIN_THREAD, "hello", model="flash")
    item = service.threads[MAIN_THREAD].inbox.get_nowait()
    # the pick rides the send call: the turn runs on Flash and the text stays as written
    assert item.model == "deepseek-flash"
    assert item.llm is not None and item.llm.settings.model == "deepseek-flash"
    assert item.text == "hello"


def test_the_pill_wins_over_the_wording(
    service: MuseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.app.vault.set("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(service, "_ensure_worker", lambda thread: None)  # no loop here
    service.send(MAIN_THREAD, "use local for this", model="pro")
    item = service.threads[MAIN_THREAD].inbox.get_nowait()
    assert item.model == "deepseek-v4-pro"


def test_an_unknown_pill_pick_keeps_the_configured_model(
    service: MuseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "_ensure_worker", lambda thread: None)  # no loop here
    service.send(MAIN_THREAD, "hello", model="nonsense")
    queued = service.threads[MAIN_THREAD].inbox.get_nowait()
    assert queued == "hello"  # a plain turn on the configured model, no one-turn client


# ------------------------------------------------------------------ the cost note


def test_usage_note_skips_local_turns(
    service: MuseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = service._make_thread("quiet", "Quiet")
    service.threads["quiet"] = thread
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(service.ui, "emit", lambda event: emitted.append(event))
    service._record_usage(thread, "qwen3.5:9b-32k", "person")
    service._record_usage(thread, None, "person")
    assert not emitted


def test_usage_note_after_a_deepseek_turn(
    service: MuseService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # pin the off-peak price: the note's dollar figure would otherwise swing with the
    # hour the test happens to run at
    monkeypatch.setattr(usage_pricing, "_peak_hour", lambda now: False)
    emitted: list[dict[str, Any]] = []
    monkeypatch.setattr(service.ui, "emit", lambda event: emitted.append(event))
    registry = InstrumentationRegistry.get()
    trace = registry.start_trace("hi", conversation_id="main")
    trace.record_llm_step(
        {"prompt_tokens": 24167, "completion_tokens": 2, "prompt_cache_hit_tokens": 23500}
    )
    registry.finish_trace(trace)
    service._record_usage(service.threads["main"], "deepseek-flash", "person")
    assert len(emitted) == 1
    note = emitted[0]
    assert note["type"] == "notice" and note["thread"] == "main"
    assert note["text"].startswith("deepseek-flash: 24.2k prompt (23.5k cached)")
    assert "~$0.0002" in note["text"]


def test_trace_records_the_cache_tokens() -> None:
    trace = TaskTrace("t", "p", "c")
    trace.record_llm_step(
        {"prompt_tokens": 100, "completion_tokens": 5, "prompt_cache_hit_tokens": 90}
    )
    trace.record_llm_step(
        {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    )
    assert trace.cache_tokens == 130
    assert trace.to_dict()["cache_tokens"] == 130


# ------------------------------------------------------------------ the rate card


def test_flash_fully_cached_off_peak_is_sub_mill_dollar() -> None:
    cost = estimate_turn_cost("deepseek-flash", 23000, 23000, 20, now=WEDNESDAY_NOON)
    assert cost == pytest.approx((23000 * 0.003 + 20 * 0.60) / 1e6)
    assert cost < 0.001


def test_peak_hours_double_the_price() -> None:
    peak = estimate_turn_cost("deepseek-flash", 1000, 0, 0, now=WEDNESDAY_PEAK)
    off = estimate_turn_cost("deepseek-flash", 1000, 0, 0, now=WEDNESDAY_NOON)
    assert peak == pytest.approx(2 * off)
    assert off == pytest.approx(1000 * 0.15 / 1e6)


def test_the_weekend_never_bills_peak() -> None:
    cost = estimate_turn_cost("deepseek-flash", 1000, 0, 0, now=SATURDAY_PEAK_HOUR)
    assert cost == pytest.approx(1000 * 0.15 / 1e6)


def test_pro_rates() -> None:
    cost = estimate_turn_cost("deepseek-v4-pro", 1_000_000, 0, 1_000_000, now=WEDNESDAY_NOON)
    assert cost == pytest.approx(0.66 + 1.98)


def test_unknown_model_costs_zero() -> None:
    assert estimate_turn_cost("qwen3.5:9b-32k", 1000, 0, 10) == 0.0


def test_the_cache_never_bills_above_the_prompt() -> None:
    cost = estimate_turn_cost("deepseek-flash", 100, 500, 0, now=WEDNESDAY_NOON)
    assert cost == pytest.approx(100 * 0.003 / 1e6)


def test_usage_note_names_the_model_and_the_cache() -> None:
    # a Saturday keeps the weekend's off-peak price, whatever wall-clock time CI runs at
    note = usage_note(
        "deepseek-flash",
        {"prompt_tokens": 24167, "cache_tokens": 23500, "completion_tokens": 2},
        now=SATURDAY_PEAK_HOUR,
    )
    assert note.startswith("deepseek-flash: 24.2k prompt (23.5k cached), 2 reply")
    assert "~$0.0002 this turn" in note
