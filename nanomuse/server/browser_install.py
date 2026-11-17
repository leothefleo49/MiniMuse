"""One-tap install of the computer's browser control (Playwright and its Chromium).

The Connections card used to print a ``pip install`` line for the person to run themselves.
This runs the same two steps in the background and reports where it is, so the card can show
a button, a progress line and then switch itself on.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

STEPS: tuple[tuple[str, list[str]], ...] = (
    ("Installing the browser library", ["-m", "pip", "install", "--disable-pip-version-check", "playwright"]),
    ("Downloading the browser (about 150 MB)", ["-m", "playwright", "install", "chromium"]),
)

_lock = threading.Lock()
_state: dict[str, Any] = {"status": "idle", "step": "", "message": "", "log": [], "started": 0.0}


def _frozen() -> bool:
    """A packaged app has no pip of its own to install into."""
    return bool(getattr(sys, "frozen", False))


def status() -> dict[str, Any]:
    with _lock:
        out = dict(_state)
        out["log"] = list(_state["log"])[-6:]
        return out


def _set(**changes: Any) -> None:
    with _lock:
        _state.update(changes)


def _run(steps: tuple[tuple[str, list[str]], ...], runner: Callable[[list[str]], tuple[int, str]]) -> None:
    for title, args in steps:
        _set(step=title)
        code, output = runner([sys.executable, *args])
        lines = [line for line in output.splitlines() if line.strip()]
        with _lock:
            _state["log"] = (list(_state["log"]) + lines)[-40:]
        if code != 0:
            tail = lines[-1] if lines else f"exit code {code}"
            _set(status="error", message=f"{title} failed: {tail}")
            return
    _set(status="done", step="", message="Browser control is installed.")


def _subprocess_runner(command: list[str]) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603 - fixed argument lists, no user input
        command,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def start(runner: Callable[[list[str]], tuple[int, str]] | None = None, *, wait: bool = False) -> dict[str, Any]:
    """Begin the install unless one is running. ``wait`` is for tests."""
    with _lock:
        if _state["status"] == "running":
            return dict(_state)
        if _frozen():
            _state.update(
                status="error",
                step="",
                log=[],
                message=(
                    "This packaged app cannot install the browser into itself. Use the phone's "
                    "own browser, or run MiniMuse from Python (pip install 'minimuse[browser]')."
                ),
            )
            return dict(_state)
        _state.update(status="running", step=STEPS[0][0], message="", log=[], started=time.time())
    worker = threading.Thread(target=_run, args=(STEPS, runner or _subprocess_runner), daemon=True)
    worker.start()
    if wait:
        worker.join()
    return status()
