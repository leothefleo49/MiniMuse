"""Notifications on a phone-only runtime: Termux:API needs no internet and no subscription."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nanomuse.server import push as push_mod
from nanomuse.server.push import PushService


@pytest.fixture()
def termux(monkeypatch: pytest.MonkeyPatch):
    exe = "/data/data/com.termux/files/usr/bin/termux-notification"
    monkeypatch.setenv("TERMUX_VERSION", "1")
    monkeypatch.setattr(
        push_mod.shutil, "which", lambda name: exe if name == "termux-notification" else None
    )
    calls: list[tuple[list[str], dict]] = []

    def popen(args, **kwargs):  # noqa: ANN001
        calls.append((list(args), kwargs))
        return object()

    # Replace the name only in the push module: replacing the process-wide Popen would
    # also reach subprocess.run inside anything else the test imports.
    monkeypatch.setattr(
        push_mod, "subprocess", SimpleNamespace(Popen=popen, DEVNULL=push_mod.subprocess.DEVNULL)
    )
    return calls


def test_the_notification_shade_works_without_web_push(settings, termux):
    svc = PushService(settings.data_dir, port=9999)
    assert svc.view()["termux"] is True
    svc.notify(
        "Approval needed", "Run the command?", tag="approve-1", url="/#chat", kind="approval"
    )
    assert len(termux) == 1
    args, kwargs = termux[0]
    assert args[0].endswith("termux-notification")
    assert args[args.index("--title") + 1] == "Approval needed"
    assert args[args.index("--content") + 1] == "Run the command?"
    assert args[args.index("--id") + 1] == "nanomuse-approve-1"
    assert args[args.index("--action") + 1] == 'termux-open-url "http://127.0.0.1:9999/#chat"'
    # an approval interrupts, so it is high priority rather than a quiet row
    assert args[args.index("--priority") + 1] == "high"
    assert kwargs["start_new_session"] is True


def test_an_ordinary_note_is_not_high_priority(settings, termux):
    svc = PushService(settings.data_dir)
    svc.notify("Checked on the goal", "Nothing new", tag="quiet", kind="feed")
    args, _ = termux[0]
    assert "--priority" not in args


def test_termux_can_be_turned_off(settings, termux, monkeypatch):
    monkeypatch.setenv("NANOMUSE_TERMUX_NOTIFY", "0")
    svc = PushService(settings.data_dir)
    svc.notify("t", "b")
    assert termux == []


def test_no_phone_no_process(settings, monkeypatch):
    monkeypatch.setattr(push_mod.shutil, "which", lambda _name: None)
    calls: list = []

    def popen(*args, **kwargs):  # noqa: ANN001, ANN002
        calls.append(args)
        return object()

    monkeypatch.setattr(
        push_mod, "subprocess", SimpleNamespace(Popen=popen, DEVNULL=push_mod.subprocess.DEVNULL)
    )
    svc = PushService(settings.data_dir)
    svc.notify("t", "b")
    assert calls == []


def test_test_notification_counts_the_phone(settings, termux):
    svc = PushService(settings.data_dir)
    out = asyncio.run(svc.test("Muse"))
    assert out["ok"] is True
    assert out["termux"] is True
    assert termux[0][0][termux[0][0].index("--title") + 1] == "Muse can reach you here"
