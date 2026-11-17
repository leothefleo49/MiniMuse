"""Pairing: the device that was scanned tells its own screen who paired."""

from __future__ import annotations

from typing import Any

from nanomuse.llm.mock import MockLLM
from nanomuse.server.service import MuseService


def test_inbound_claim_announces_the_new_device(settings: Any) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    queue = service.bus.subscribe()
    info = service.sync.issue_pair_code()
    ok, payload, status = service.sync.accept_inbound_claim(
        code=str(info["code"]),
        remote_id="phone-1",
        remote_name="Tyler's phone",
        remote_urls=[],
    )
    assert ok and status == 200 and payload["ok"] is True
    seen = []
    while not queue.empty():
        seen.append(queue.get_nowait())
    assert {"kind": "paired", "name": "Tyler's phone"} in seen


def test_a_wrong_code_announces_nothing(settings: Any) -> None:
    service = MuseService(settings, llm=MockLLM([]))
    queue = service.bus.subscribe()
    ok, _payload, status = service.sync.accept_inbound_claim(
        code="WRONG1", remote_id="x", remote_name="Nobody", remote_urls=[]
    )
    assert not ok and status in (401, 429)
    while not queue.empty():
        assert queue.get_nowait().get("kind") != "paired"
