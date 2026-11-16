#!/usr/bin/env python3
"""Generate MiniMuse's separated bold MM launcher and status-bar icons from assets/brand/nanomuse-mark.svg.

Delegates directly to scripts/gen-android-icons.py (defaulting to android-classic/app/src/main/res)
so all launcher skins, monochrome silhouettes, and 24dp notification badges use the single centered
separated bold MM mark.

Usage: gen-mm-icons.py [--res <res dir>]   (default: android-classic/app/src/main/res)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--res" not in args:
        args = ["--res", "android-classic/app/src/main/res", *args]
    sys.argv = [sys.argv[0], *args]
    spec = importlib.util.spec_from_file_location(
        "gen_android_icons", ROOT / "scripts" / "gen-android-icons.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
