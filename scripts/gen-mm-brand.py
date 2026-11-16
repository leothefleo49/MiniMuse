#!/usr/bin/env python3
"""Generate unified separated bold MM brand SVGs, PNGs, ICOs, and ICNS across web, desktop, docs, and brand assets."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

# Separated bold MM geometric stroke contour in 0..100 tile coordinates:
# visual stroke bounds x=[10.25, 89.75], y=[25.75, 74.25], clear 6.50-unit center gap,
# mathematically centered at (50.00, 50.00).
MM_PATH = (
    "M14.5 70 L14.5 30 L28.5 57 L42.5 30 L42.5 70 M57.5 70 L57.5 30 L71.5 57 L85.5 30 L85.5 70"
)
STROKE_WIDTH = "8.5"

GRADIENT_DEF = (
    "<defs>"
    '<linearGradient id="mm" gradientUnits="userSpaceOnUse" x1="10.25" y1="50" x2="89.75" y2="50">'
    '<stop offset="0" stop-color="#015CFB"/><stop offset="1" stop-color="#0186FB"/>'
    "</linearGradient>"
    "</defs>"
)

_MARK_PATH_EL = (
    f'<path d="{MM_PATH}" fill="none" stroke="url(#mm)" stroke-width="{STROKE_WIDTH}" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
)

MARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    f"{GRADIENT_DEF}"
    f"{_MARK_PATH_EL}"
    "</svg>"
)

ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    f"{GRADIENT_DEF}"
    '<rect width="100" height="100" rx="27" ry="27" fill="#FFFFFF"/>'
    f"{_MARK_PATH_EL}"
    "</svg>"
)

MASKABLE_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    f"{GRADIENT_DEF}"
    '<rect width="100" height="100" fill="#FFFFFF"/>'
    '<g transform="translate(10 10) scale(0.80)">'
    f"{_MARK_PATH_EL}"
    "</g>"
    "</svg>"
)

ICON_ON_BLUE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 148 148">'
    "<defs>"
    '<linearGradient id="mm" gradientUnits="userSpaceOnUse" gradientTransform="translate(24 24)" '
    'x1="10.25" y1="50" x2="89.75" y2="50">'
    '<stop offset="0" stop-color="#015CFB"/><stop offset="1" stop-color="#0186FB"/>'
    "</linearGradient>"
    "</defs>"
    '<rect width="148" height="148" fill="#0078FD"/>'
    '<g transform="translate(24 24)">'
    '<rect width="100" height="100" rx="27" ry="27" fill="#FFFFFF"/>'
    f"{_MARK_PATH_EL}"
    "</g></svg>"
)


def render_svg_png(svg_text: str, size: int) -> Image.Image:
    """Render an SVG square string to a crisp RGBA Image via resvg_py (or cairosvg / playwright)."""
    sized_svg = re.sub(
        r"<svg\s+",
        f'<svg width="{size}" height="{size}" ',
        svg_text,
        count=1,
    )
    try:
        import resvg_py

        png_bytes = resvg_py.svg_to_bytes(svg_string=sized_svg)
        return Image.open(BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        pass
    try:
        import cairosvg

        png_bytes = cairosvg.svg2png(
            bytestring=svg_text.encode("utf-8"), output_width=size, output_height=size
        )
        return Image.open(BytesIO(png_bytes)).convert("RGBA")
    except Exception:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": size, "height": size}, device_scale_factor=1)
            page.set_content(f'<body style="margin:0;background:transparent">{sized_svg}</body>')
            data = page.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": size, "height": size})
            browser.close()
        return Image.open(BytesIO(data)).convert("RGBA")


def main() -> None:
    web_pub = ROOT / "web" / "public"
    static_dir = ROOT / "nanomuse" / "server" / "static"
    brand = ROOT / "assets" / "brand"
    desktop_res = ROOT / "harness" / "desktop" / "resources"
    desktop_icons = desktop_res / "icons"
    website_pub = ROOT / "website" / "public"
    docs_dir = ROOT / "docs"

    for path, content in (
        (web_pub / "icon.svg", ICON_SVG),
        (web_pub / "mark.svg", MARK_SVG),
        (static_dir / "icon.svg", ICON_SVG),
        (static_dir / "mark.svg", MARK_SVG),
        (brand / "nanomuse-icon.svg", ICON_SVG),
        (brand / "nanomuse-mark.svg", MARK_SVG),
        (brand / "nanomuse-icon-on-blue.svg", ICON_ON_BLUE_SVG),
        (website_pub / "nanomuse-icon.svg", ICON_SVG),
    ):
        if path.parent.exists():
            path.write_text(content, encoding="utf-8", newline="\n")

    # Master 1024px PNGs
    icon_1024 = render_svg_png(ICON_SVG, 1024)
    mark_688 = render_svg_png(MARK_SVG, 688)
    icon_1024.save(brand / "nanomuse-icon-source-1024.png")
    mark_688.save(brand / "nanomuse-mark-rgba-688.png")
    if docs_dir.exists():
        render_svg_png(ICON_SVG, 512).save(docs_dir / "app-icon.png")

    for sz in (16, 32, 48, 64, 128, 192, 256, 512, 1024):
        resized = icon_1024 if sz == 1024 else render_svg_png(ICON_SVG, sz)
        resized.save(web_pub / f"icon-{sz}.png")
        if static_dir.exists():
            resized.save(static_dir / f"icon-{sz}.png")

    # Dedicated padded opaque full-bleed maskable icons (80% safe circle compliant)
    for sz in (192, 512):
        mask_img = render_svg_png(MASKABLE_ICON_SVG, sz)
        mask_img.save(web_pub / f"icon-maskable-{sz}.png")
        if static_dir.exists():
            mask_img.save(static_dir / f"icon-maskable-{sz}.png")

    # Multi-resolution ICOs
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    for ico_path in (
        web_pub / "icon.ico",
        web_pub / "favicon.ico",
        static_dir / "icon.ico",
        static_dir / "favicon.ico",
        brand / "mm.ico",
    ):
        if ico_path.parent.exists():
            icon_1024.save(ico_path, format="ICO", sizes=ico_sizes)

    # Electron desktop shell resources
    if desktop_res.exists():
        icon_1024.save(desktop_res / "app-icon.png")
        render_svg_png(ICON_SVG, 512).save(desktop_res / "logo.png")
        desktop_icons.mkdir(parents=True, exist_ok=True)
        for sz in (16, 24, 32, 48, 64, 128, 256, 512):
            render_svg_png(ICON_SVG, sz).save(desktop_icons / f"{sz}x{sz}.png")

    print("Generated separated bold MM brand SVGs, PNGs, maskable tiles, and ICOs successfully.")


if __name__ == "__main__":
    main()
