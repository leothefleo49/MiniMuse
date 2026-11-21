# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the one-file MiniMuse runtime: a single `MiniMuse.exe` (Windows)
or `MiniMuse` (Linux) that serves the whole app — download, double-click, open the
address it prints. No installer, no archive, no Python needed on the machine.

    python -m PyInstaller --noconfirm --clean scripts/desktop-app/minimuse_onefile.spec

One-file unpacks itself on every start (a few seconds) — the price of a single file.
The build in scripts/desktop-app/nanomuse_runtime.spec (one folder) stays the one the
Electron desktop app carries; this one is the hand-to-a-friend download, and the one
`nanomuse update --apply` swaps in place when it is what's running.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sys.path.insert(0, ROOT)

hidden = []
hidden += collect_submodules("nanomuse")
hidden += collect_submodules("uvicorn")
hidden += collect_submodules("websockets")
hidden += collect_submodules("anyio")
hidden += ["pydantic.deprecated.decorator", "tzdata", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont"]

datas = [
    (os.path.join(ROOT, "nanomuse", "server", "static"), os.path.join("nanomuse", "server", "static")),
    (os.path.join(ROOT, "nanomuse", "llm", "providers.json"), os.path.join("nanomuse", "llm")),
    (os.path.join(ROOT, "chrome-extension"), os.path.join("nanomuse", "server", "chrome-extension")),
    (os.path.join(ROOT, "nanomuse", "skills", "builtin"), os.path.join("nanomuse", "skills", "builtin")),
    (os.path.join(ROOT, "assets", "brand", "mm.ico"), os.path.join("assets", "brand")),
    (os.path.join(ROOT, "assets", "brand", "splash.png"), os.path.join("assets", "brand")),
    (os.path.join(ROOT, "config", "config.example.toml"), "nanomuse"),
    (os.path.join(ROOT, "CHANGELOG.md"), "."),
]
binaries = []

for pkg in ("certifi", "ddgs", "tzdata", "mcp", "html2text", "qrcode", "pywebpush", "py_vapid"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # noqa: BLE001 — optional on some platforms
        continue
    datas += d
    binaries += b
    hidden += h

for pkg in ("webview", "playwright", "bottle", "proxy_tools", "pythonnet", "clr_loader"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # noqa: BLE001 — platform-specific GUI/browser runtime
        continue
    datas += d
    binaries += b
    hidden += h

for pkg in ("pyautogui", "pyscreeze", "pymsgbox", "pytweening", "mouseinfo", "pyperclip", "mss"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # noqa: BLE001
        continue
    datas += d
    binaries += b
    hidden += h

excludes = ["tkinter", "matplotlib", "numpy", "scipy", "pandas", "IPython", "jupyter", "pytest"]

a = Analysis(
    [os.path.join(ROOT, "scripts", "desktop-app", "runtime_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hidden)),
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

# Native bootloader splash on Windows: opens in ~150ms BEFORE --onefile extracts to temp,
# showing the static glowing MiniMuse icon and "Booting up..." immediately on click.
splash_path = os.path.join(ROOT, "assets", "brand", "splash.png")
use_splash = sys.platform == "win32" and os.path.isfile(splash_path)
splash = (
    Splash(
        splash_path,
        binaries=a.binaries,
        datas=a.datas,
        text_pos=None,
        always_on_top=True,
    )
    if use_splash
    else None
)

exe_args = [pyz, a.scripts]
if splash is not None:
    exe_args += [splash, splash.binaries]
exe_args += [a.binaries, a.datas, []]

exe = EXE(
    *exe_args,
    name="MiniMuse",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # a normal app: on Windows no console window at all; on Linux keep stdout for a
    # terminal run (a file manager double-click opens no terminal either way).
    # Double-clicked (no args) the entry starts the server and opens the browser on
    # the app (scripts/desktop-app/runtime_entry.py)
    console=(sys.platform not in ("win32", "darwin")),
    icon=os.path.join(ROOT, "assets", "brand", "mm.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="MiniMuse.app",
        icon=os.path.join(ROOT, "assets", "brand", "mm.ico"),
        bundle_identifier="io.github.nanomuse.app",
        info_plist={
            "CFBundleName": "MiniMuse",
            "CFBundleDisplayName": "MiniMuse",
            "NSHighResolutionCapable": True,
        },
    )
