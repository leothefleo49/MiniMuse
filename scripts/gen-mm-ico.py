#!/usr/bin/env python3
"""Render the MiniMuse separated bold MM mark to a Windows .ico (multi-size) from assets/brand/nanomuse-icon.svg."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "brand" / "mm.ico"


def main() -> None:
    spec = importlib.util.spec_from_file_location("gen_mm_brand", ROOT / "scripts" / "gen-mm-brand.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    img = mod.render_svg_png(mod.ICON_SVG, 1024)
    img.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
