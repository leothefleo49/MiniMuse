"""Windows subprocess hiding helpers.

A windowed frozen build on Windows has no attached console, so every child console
process (``powershell``, ``cmd``, ``tasklist``, ``icacls``, ``nvidia-smi``, ``adb``,
``.cmd`` shims) flashes a visible terminal window unless ``CREATE_NO_WINDOW`` and a
hidden ``STARTUPINFO`` are passed. On POSIX these helpers return empty mappings so
cross-platform callers can unpack ``**hidden_subprocess_kwargs()`` uniformly.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def hidden_creationflags(extra: int = 0) -> int:
    """Return ``CREATE_NO_WINDOW`` combined with ``extra`` on Windows, else ``extra``."""
    if sys.platform != "win32":
        return extra
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | extra


def hidden_startupinfo() -> Any:
    """Return a hidden ``STARTUPINFO`` on Windows, or ``None`` elsewhere."""
    if sys.platform != "win32":
        return None
    si_cls = getattr(subprocess, "STARTUPINFO", None)
    if si_cls is None:
        return None
    si = si_cls()
    si.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
    si.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    return si


def hidden_subprocess_kwargs(*, hide_window: bool = True, creationflags: int = 0) -> dict[str, Any]:
    """Keyword arguments preventing console window flashes on Windows.

    Pass ``hide_window=False`` when launching a visible GUI program via ``cmd /c start``
    so the console wrapper stays invisible without hiding the target GUI window.
    """
    if sys.platform != "win32":
        return {}
    kw: dict[str, Any] = {"creationflags": hidden_creationflags(creationflags)}
    if hide_window:
        si = hidden_startupinfo()
        if si is not None:
            kw["startupinfo"] = si
    return kw


__all__ = ["hidden_creationflags", "hidden_startupinfo", "hidden_subprocess_kwargs"]
