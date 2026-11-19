"""Pairing: reachability warnings, the address a peer can be dialled back on, and the
quick answer that lets the screen show progress while the first sync runs."""

from __future__ import annotations

from typing import Any

from nanomuse.llm.mock import MockLLM
from nanomuse.server.service import MuseService
from nanomuse.sync.engine import ConversationSync


def test_pair_code_warns_when_only_this_machine_is_reachable(settings: Any) -> None:
    # the desktop app used to listen on 127.0.0.1 only, while the QR carried the LAN
    # address: the phone could never answer it, with no hint why
    settings.server.host = "127.0.0.1"
    service = MuseService(settings, llm=MockLLM([]))
    info = service.sync.issue_pair_code()
    assert info["code"]
    assert info.get("warning")
    assert "0.0.0.0" in str(info["warning"])


def test_pair_code_on_the_network_has_no_warning(settings: Any) -> None:
    settings.server.host = "0.0.0.0"
    service = MuseService(settings, llm=MockLLM([]))
    info = service.sync.issue_pair_code()
    # a machine with no network at all still gets the honest warning; with a LAN address,
    # the code is usable as it is
    if info["urls"]:
        assert not info.get("warning")


def test_peer_url_is_the_address_that_just_reached_us() -> None:
    # the claimer says it listens on :8787; the address we know works is the one it claimed
    # from, so that comes first
    urls = ConversationSync._reachable_peer_urls(["http://192.168.1.50:8787"], "192.168.1.20")
    assert urls[0] == "http://192.168.1.20:8787"
    assert "http://192.168.1.50:8787" in urls


def test_a_loopback_address_is_not_used_for_a_remote_peer() -> None:
    # a peer that only ever ran on its own loopback cannot be reached that way from here
    assert ConversationSync._reachable_peer_urls(["http://127.0.0.1:8787"], "192.168.1.20") == []
    # ...unless the claim itself came from this machine
    assert ConversationSync._reachable_peer_urls(["http://127.0.0.1:8787"], "127.0.0.1") == [
        "http://127.0.0.1:8787"
    ]
