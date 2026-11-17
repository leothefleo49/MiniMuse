"""Auto (adaptive) proactivity: check-ins follow whether the person answers them."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.schema import LLMResponse
from nanomuse.server import create_app
from nanomuse.server.proactivity import (
    ANSWER_WINDOW_S,
    MIN_EVIDENCE,
    AutoProactivity,
    factor_for,
    level_for,
)
from nanomuse.server.service import MuseService, Profile

NOW = 1_000_000.0
H = 3600.0


def engine(tmp_path: Path) -> AutoProactivity:
    return AutoProactivity(tmp_path)


def sent_and_resolved(e: AutoProactivity, answered: int, ignored: int) -> None:
    """History old enough that every check-in has an outcome: `answered` replied to, `ignored` not."""
    t = NOW - 100 * H
    for _ in range(answered):
        e.record_sent(t)
        e.record_reply(t + 60)
        t += H
    for _ in range(ignored):
        e.record_sent(t)
        t += H


def test_no_evidence_behaves_like_the_default(tmp_path: Path) -> None:
    e = engine(tmp_path)
    assert e.factor(NOW) == 1.0
    sent_and_resolved(e, answered=0, ignored=MIN_EVIDENCE - 1)
    assert e.factor(NOW) == 1.0  # too few to say anything


@pytest.mark.parametrize(
    ("answered", "ignored", "factor", "level"),
    [
        (6, 0, 0.5, "high"),  # always answered: reach out more
        (5, 3, 0.5, "high"),  # 62%
        (3, 5, 1.0, "default"),  # 38%
        (2, 8, 2.0, "low"),  # 20%
        (0, 8, 4.0, "low"),  # ignored: back right off
    ],
)
def test_the_answer_rate_sets_the_interval_and_how_readily_it_speaks(
    tmp_path: Path, answered: int, ignored: int, factor: float, level: str
) -> None:
    e = engine(tmp_path)
    sent_and_resolved(e, answered, ignored)
    assert e.factor(NOW) == factor
    assert level_for(e.factor(NOW)) == level


def test_a_check_in_still_inside_the_window_is_not_held_against_the_person(tmp_path: Path) -> None:
    e = engine(tmp_path)
    sent_and_resolved(e, answered=5, ignored=0)
    for _ in range(4):
        e.record_sent(NOW - 1 * H)  # sent an hour ago: they may still reply
    assert e.factor(NOW) == 0.5
    assert e.stats(NOW)["waiting"] == 4
    # once the window has passed without a reply they count as ignored: 5 answered of 9 is 56%
    assert e.factor(NOW + ANSWER_WINDOW_S + H) == 1.0


def test_only_a_reply_inside_the_window_answers_the_latest_check_in(tmp_path: Path) -> None:
    e = engine(tmp_path)
    e.record_sent(NOW)
    assert e.record_reply(NOW + ANSWER_WINDOW_S + 1) is False  # too late
    e.record_sent(NOW + 10 * H)
    assert e.record_reply(NOW + 10 * H + 600) is True
    assert e.record_reply(NOW + 10 * H + 700) is False  # already answered; nothing else is waiting
    assert [c.answered for c in e.items] == [False, True]


def test_the_history_survives_a_restart(tmp_path: Path) -> None:
    e = engine(tmp_path)
    sent_and_resolved(e, answered=1, ignored=5)
    again = engine(tmp_path)
    assert [(c.at, c.answered) for c in again.items] == [(c.at, c.answered) for c in e.items]
    assert again.factor(NOW) == e.factor(NOW) == 2.0  # 1 answered of 6 is 17%


def test_a_broken_history_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "proactivity_auto.json").write_text("{not json", encoding="utf-8")
    assert engine(tmp_path).factor(NOW) == 1.0


def test_the_factor_table() -> None:
    assert [factor_for(r) for r in (1.0, 0.6, 0.59, 0.3, 0.29, 0.1, 0.09, 0.0)] == [0.5, 0.5, 1.0, 1.0, 2.0, 2.0, 4.0, 4.0]


# ----------------------------------------------------------------------------- inside the service
def make(tmp_path: Path) -> MuseService:
    return MuseService(Settings(data_dir=tmp_path / "data", workspace=tmp_path / "ws"), llm=MockLLM([]))


def test_auto_scales_the_interval_by_what_was_learned(tmp_path: Path) -> None:
    svc = make(tmp_path)
    svc.update_profile({"proactivity": "auto", "goal_interval_minutes": 60})
    assert svc.profile.proactivity == "auto"
    assert svc.profile.interval_seconds == 3600  # no evidence yet: the default pace
    sent_and_resolved(svc.auto_proactivity, answered=0, ignored=8)
    svc._refresh_auto()
    assert svc.profile.interval_seconds == 4 * 3600
    assert svc.profile.effective_level == "low"


def test_the_learned_factor_survives_a_profile_edit(tmp_path: Path) -> None:
    """update_profile builds a brand new Profile every time; the factor must not reset to 1."""
    svc = make(tmp_path)
    sent_and_resolved(svc.auto_proactivity, answered=0, ignored=8)
    svc.update_profile({"proactivity": "auto"})
    assert svc.profile.auto_factor == 4.0
    svc.update_profile({"tagline": "Your day, sorted."})
    assert svc.profile.auto_factor == 4.0


def test_the_learned_factor_survives_a_restart(tmp_path: Path) -> None:
    svc = make(tmp_path)
    svc.update_profile({"proactivity": "auto"})
    sent_and_resolved(svc.auto_proactivity, answered=7, ignored=0)
    again = make(tmp_path)
    assert again.profile.proactivity == "auto"
    assert again.profile.auto_factor == 0.5


def test_other_levels_are_unchanged(tmp_path: Path) -> None:
    svc = make(tmp_path)
    sent_and_resolved(svc.auto_proactivity, answered=0, ignored=8)
    svc._refresh_auto()
    for level, mult in (("low", 2), ("default", 1), ("high", 0.5)):
        svc.update_profile({"proactivity": level, "goal_interval_minutes": 60})
        assert svc.profile.interval_seconds == int(3600 * mult)
        assert svc.profile.effective_level == level  # the learned factor is ignored off "auto"


def test_the_effective_level_in_the_saved_profile_does_not_break_loading() -> None:
    p = Profile.from_dict({**Profile().to_dict(), "proactivity": "auto"})
    assert p.proactivity == "auto"
    assert Profile.from_dict(p.to_dict()).proactivity == "auto"
    assert Profile.from_dict({"proactivity": "nonsense"}).proactivity == "default"


def test_a_person_writing_answers_the_latest_check_in(tmp_path: Path) -> None:
    svc = make(tmp_path)
    svc.auto_proactivity.record_sent()  # a check-in just reached them
    assert svc.auto_proactivity.items[-1].answered is False
    svc._note_reply()
    assert svc.auto_proactivity.items[-1].answered is True


# ----------------------------------------------------------------------------- through a real goal pass

@pytest.fixture()
def live(settings: Settings) -> Iterator[tuple[TestClient, MuseService, MockLLM]]:
    settings.server.token = "secret-token"
    llm = MockLLM([])
    service = MuseService(settings, llm=llm)
    with TestClient(create_app(settings, service)) as client:
        client.headers["Authorization"] = "Bearer secret-token"
        yield client, service, llm


def _idle(service: MuseService) -> None:
    thread = service.threads["main"]
    deadline = time.time() + 10
    while time.time() < deadline:
        if not thread.busy and thread.inbox.empty():
            return
        time.sleep(0.02)
    raise AssertionError("the thread did not go idle")


def test_only_a_check_in_that_reaches_the_person_counts_and_a_reply_answers_it(live) -> None:  # noqa: ANN001
    client, service, llm = live
    client.put("/api/settings", json={"profile": {"proactivity": "auto"}})
    goal = client.post("/api/goals", json={"title": "Water the plants", "steps": ["Check soil"]}).json()

    llm.script.append(LLMResponse(content="[quiet] Soil is still damp; nothing to do today."))
    client.post(f"/api/goals/{goal['id']}/advance")
    _idle(service)
    assert service.auto_proactivity.items == []  # a quiet pass reached nobody

    llm.script.append(LLMResponse(content="Checked the soil: it is dry, so I scheduled watering."))
    client.post(f"/api/goals/{goal['id']}/advance")
    _idle(service)
    (checkin,) = service.auto_proactivity.items
    assert checkin.answered is False

    llm.script.append(LLMResponse(content="You're welcome."))
    client.post("/api/threads/main/send", json={"text": "thanks, that is great"})
    _idle(service)
    assert checkin.answered is True


def test_a_reminder_or_a_chat_is_never_counted_as_a_check_in(live) -> None:  # noqa: ANN001
    client, service, llm = live
    client.put("/api/settings", json={"profile": {"proactivity": "auto"}})
    llm.script.append(LLMResponse(content="Hello!"))
    client.post("/api/threads/main/send", json={"text": "hi"})
    _idle(service)
    assert service.auto_proactivity.items == []


def test_auto_changes_what_a_pass_is_told_about_reaching_out(live) -> None:  # noqa: ANN001
    client, service, llm = live
    client.put("/api/settings", json={"profile": {"proactivity": "auto"}})
    sent_and_resolved(service.auto_proactivity, answered=0, ignored=8)  # ignored: back right off
    service._refresh_auto()
    goal = client.post("/api/goals", json={"title": "Water the plants", "steps": ["Check soil"]}).json()
    llm.script.append(LLMResponse(content="[quiet] nothing"))
    client.post(f"/api/goals/{goal['id']}/advance")
    _idle(service)
    told = llm.calls[-1]["messages"][-1].content
    assert "Reach out only if a step got finished or you need the user" in told  # the "low" wording