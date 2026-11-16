"""A file kept to the one person it belongs to.

POSIX takes a mode: ``0600``, the owner reads and writes and nobody else does. Windows
has no mode, and ``chmod`` there clears a read-only flag and nothing else, so every call
to it around this codebase left the calendars, address books, token and key files it
meant to protect open to the machine's other local accounts. The ACL is set instead:
inheritance off, the one account in full control.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from nanomuse.win_proc import hidden_subprocess_kwargs


def keep_private(path: Path | str) -> None:
    """Make ``path`` readable by its owner and no other local account.

    On POSIX this is ``chmod 0600`` and raises as that does. On Windows the inherited
    access rules come off and the current account alone keeps full control; a command
    that cannot run (no ``USERNAME``, an ``icacls`` that refused) is left alone rather
    than unwriting the file, the way a mode no platform honours would be. The machine's
    own service account keeps access too: the runtime may be a scheduled task serving
    before anyone logs in, and it has to read the vault, tokens and caches the
    interactive session wrote (and the other way round), which a bare owner-only ACL
    denies it.
    """
    if sys.platform == "win32":
        import subprocess

        user = os.environ.get("USERNAME")
        if user:  # a service without one keeps the file's default rules
            win_kw = hidden_subprocess_kwargs()
            subprocess.run(
                ["icacls", os.fspath(path), "/inheritance:r", "/grant:r", f"{user}:(F)"],
                capture_output=True,
                check=False,
                **win_kw,
            )
            # by well-known SID rather than a name, so a Windows localised into another
            # language is covered too; SYSTEM is the machine's own authority, never one
            # of the other local accounts the ACL exists to keep out
            subprocess.run(
                ["icacls", os.fspath(path), "/grant", "*S-1-5-18:(F)"],
                capture_output=True,
                check=False,
                **win_kw,
            )
    else:
        os.chmod(path, 0o600)
