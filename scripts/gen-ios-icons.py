#!/usr/bin/env python3
"""Generate the nanoMuse app icon for android/src/ios from assets/brand/.

Idempotent; re-run after every upstream pull (CONTRIBUTING.md). Writes 1024x1024 PNGs:

- Assets.xcassets/AppIcon.appiconset/Icon-1024.png       the one-stroke N in the brand gradient
                                                          on a white tile (the default icon)
- Assets.xcassets/AppIcon.appiconset/Icon-1024-Dark.png  the same mark on the dark tile (the
                                                          automatic dark appearance)
- Resources/AlternateIcons/AppIcon-{Light,Dark,LegacyLight,LegacyDark}.png  the four skins the
  in-app icon picker offers (Info.plist CFBundleAlternateIcons): light / dark with the gradient
  mark, "legacy" light with the flat brand blue, "legacy" dark with a white mark (the same
  pairing as the Android classic skins).

iOS applies its own corner mask, so the tiles are full squares. The mark sits in the same frame
as the Android legacy launcher icon (assets/brand/nanomuse-mark.svg is drawn on a 100-unit tile).
Needs Playwright with Chromium (`pip install playwright && playwright install chromium`).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "android" / "src" / "ios"
ICONSET = IOS / "Assets.xcassets" / "AppIcon.appiconset"
ALTERNATE = IOS / "Resources" / "AlternateIcons"
SIZE = 1024


def android_icons():  # noqa: ANN201  (module name has a hyphen)
    spec = importlib.util.spec_from_file_location(
        "gen_android_icons", ROOT / "scripts" / "gen-android-icons.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tile(
    d: str,
    background: str | None,
    stroke_start: str,
    stroke_end: str | None = None,
    stroke_w: float | None = None,
) -> str:
    """A full-square tile (or transparent mark if background is None); gradient when two colors are given."""
    grad = (
        f'<linearGradient id="g" gradientUnits="userSpaceOnUse" x1="10.25" y1="50" x2="89.75" y2="50">'
        f'<stop offset="0" stop-color="{stroke_start}"/><stop offset="1" stop-color="{stroke_end}"/></linearGradient>'
        if stroke_end
        else ""
    )
    fill = "url(#g)" if stroke_end else stroke_start
    rect = f'<rect width="100" height="100" fill="{background}"/>' if background else ""
    path_el = (
        f'<path d="{d}" fill="none" stroke="{fill}" stroke-width="{stroke_w:.2f}" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        if stroke_w is not None
        else f'<path d="{d}" fill="{fill}"/>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><defs>{grad}</defs>'
        f'{rect}{path_el}</svg>'
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    if args:
        print(
            f"usage: {sys.argv[0]}  (takes no arguments; unknown: {' '.join(args)})",
            file=sys.stderr,
        )
        return 2
    if not ICONSET.is_dir():
        sys.exit(f"{ICONSET} not found (restore the iOS tree first)")
    use_resvg = False
    try:
        import resvg_py
        use_resvg = True
    except ImportError:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            sys.exit("neither resvg_py nor playwright installed")
    icons = android_icons()
    d, stroke_w = icons.mark_spec()
    light = tile(d, icons.TILE_LIGHT, icons.STROKE_START, icons.STROKE_END, stroke_w)
    dark = tile(d, icons.TILE_DARK, icons.STROKE_START, icons.STROKE_END, stroke_w)
    launch = tile(d, None, icons.STROKE_START, icons.STROKE_END, stroke_w)
    launch_dir = IOS / "NanoMuse" / "NanoMuseLaunch.xcassets" / "NanoMuseLaunchMark.imageset"
    jobs: dict[Path, tuple[str, int, bool]] = {
        ICONSET / "Icon-1024.png": (light, SIZE, False),
        ICONSET / "Icon-1024-Dark.png": (dark, SIZE, False),
        ALTERNATE / "AppIcon-Light.png": (light, SIZE, False),
        ALTERNATE / "AppIcon-Dark.png": (dark, SIZE, False),
        ALTERNATE / "AppIcon-LegacyLight.png": (tile(d, icons.TILE_LIGHT, icons.STROKE_START, None, stroke_w), SIZE, False),
        ALTERNATE / "AppIcon-LegacyDark.png": (tile(d, icons.TILE_DARK, "#FFFFFF", None, stroke_w), SIZE, False),
        launch_dir / "mark@2x.png": (launch, 240, True),
        launch_dir / "mark@3x.png": (launch, 360, True),
    }
    if use_resvg:
        from io import BytesIO

        from PIL import Image

        for target, (svg, px, transparent) in jobs.items():
            if not target.parent.is_dir():
                continue
            sized = svg.replace("<svg ", f'<svg width="{px}" height="{px}" ', 1)
            raw = resvg_py.svg_to_bytes(svg_string=sized)
            if not transparent:
                # App Store icons reject alpha channels on Icon-1024.png; flatten cleanly to RGB.
                img = Image.open(BytesIO(raw)).convert("RGB")
                img.save(target, format="PNG")
            else:
                target.write_bytes(raw)
            print(f"  wrote {target.relative_to(ROOT)}")
    else:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            for target, (svg, px, transparent) in jobs.items():
                if not target.parent.is_dir():
                    continue
                page = browser.new_page(viewport={"width": px, "height": px}, device_scale_factor=1)
                page.set_content(f'<body style="margin:0;background:transparent">{svg}</body>')
                page.locator("svg").evaluate(
                    "(e, s) => { e.setAttribute('width', s); e.setAttribute('height', s); }", str(px)
                )
                page.screenshot(
                    path=str(target),
                    omit_background=transparent,
                    clip={"x": 0, "y": 0, "width": px, "height": px},
                )
                page.close()
                print(f"  wrote {target.relative_to(ROOT)}")
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
