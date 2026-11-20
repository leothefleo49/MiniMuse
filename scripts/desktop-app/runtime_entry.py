"""The entry point of the bundled runtime (PyInstaller): ``nanomuse`` with all its
subcommands, so the desktop app can run ``nanomuse serve`` without a Python install.

Double-clicked (no arguments, frozen), it behaves like a normal desktop app instead:
no terminal window (the build is windowed), the server starts on this machine, and
the moment it answers, the default browser opens on the app — token included, so it
is just *open and use*. A small tray-less background process is all it ever shows.

Nothing here is ever silent. A windowed build has no console, so every path a user
can land on does something visible: an already-running instance wins and the browser
opens on *it*; a port held by another program moves us to the next free port; a
startup failure says so in a message box (Windows) with the details in
``~/.nanomuse/logs/minimuse.log``. And the first run installs a Start Menu entry
(Windows) / applications entry (Linux) pointing at a stable copy of the exe, so the
app is where a desktop search looks for apps — not only in the Downloads folder.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import threading
import time


def _data_dir() -> str:
    return os.environ.get("NANOMUSE_DATA_DIR", os.path.join(os.path.expanduser("~"), ".nanomuse"))


def _silence_missing_consoles() -> None:
    """A windowed (no-console) Windows build has no stdout/stderr at all — they are
    ``None``, and the first ``print``/logger write takes the app down. Point them at
    the null device instead (a log file keeps what matters)."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        try:
            log_dir = os.path.join(_data_dir(), "logs")
            os.makedirs(log_dir, exist_ok=True)
            sys.stderr = open(os.path.join(log_dir, "minimuse.log"), "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _log(message: str) -> None:
    """One line to wherever diagnostics go (the log file of a windowed build, stderr
    otherwise). Never raises."""
    try:
        print(f"MiniMuse: {message}", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — diagnostics must not take the app down
        pass


def _fatal(message: str) -> None:
    """A startup failure the user must see. A windowed Windows build has no console:
    say it in a message box (the details are in the log right above this line)."""
    _log(message)
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, message + "\n\nDetails: ~/.nanomuse/logs/minimuse.log", "MiniMuse", 0x30
            )
        except Exception:  # noqa: BLE001 — the log line above already says it
            pass


def _token(data_dir: object | None = None) -> str:
    base = str(data_dir) if data_dir is not None else _data_dir()
    try:
        with open(os.path.join(base, "server_token"), encoding="utf-8") as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    if data_dir is not None:
        try:
            with open(os.path.join(_data_dir(), "server_token"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass
    return ""


def _bind_host(settings: object) -> str:
    """The address this app's server listens on.

    The phone in your hand pairs by scanning this app's QR, so the server has to answer on
    the network and not only on this machine. A host written in config wins; otherwise the
    runtime listens on every interface, and ``NANOMUSE_LAN=0`` keeps it on loopback for
    anyone who does not want it reachable from the network. Everything the app itself opens
    (window, health checks) still goes through 127.0.0.1.
    """
    server = getattr(settings, "server", None)
    configured = str(getattr(server, "host", "") or "").strip()
    if configured and configured not in ("127.0.0.1", "localhost", "::1", ""):
        return configured
    if os.environ.get("NANOMUSE_LAN", "").strip().lower() in ("0", "false", "no", "off"):
        return "127.0.0.1"
    return "0.0.0.0"


def _health_info(host: str, port: int, timeout: float = 2.0) -> dict[str, object] | None:
    """Return parsed ``/api/health`` JSON from ``host:port`` if a MiniMuse server answers."""
    import json

    from nanomuse.local_http import local_open

    probe_host = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "localhost": "127.0.0.1", "::": "::1"}.get(
        host, host
    )
    base = f"http://[{probe_host}]:{port}" if ":" in probe_host else f"http://{probe_host}:{port}"
    try:
        with local_open(f"{base}/api/health", timeout=timeout) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode("utf-8"))
            return data if isinstance(data, dict) and data.get("ok") is True else None
    except Exception:  # noqa: BLE001 — not up (yet), not ours, not JSON
        return None


def _health_ok(port: int) -> bool:
    """True once something on ``127.0.0.1:port`` answers as this app's server."""
    return _health_info("127.0.0.1", port, timeout=2.0) is not None


def _wait_for_health(port: int, wait: float = 0.0) -> bool:
    """Wait until local MiniMuse server answers ``/api/health``."""
    deadline = time.monotonic() + max(wait, 0.0)
    while True:
        if _health_ok(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def _app_url(port: int, data_dir: object | None = None) -> str:
    token = _token(data_dir)
    # ?shell=desktop tells the web app it is inside the native window (desktop layout at any width)
    base = f"http://127.0.0.1:{port}/?shell=desktop"
    return base if not token else f"{base}#token={token}"


def _focus_existing_window() -> bool:
    """On Windows, bring an already-open MiniMuse window to the foreground instead of
    opening a second WebView2 against the same storage_dir (which throws 0x8007139F / 0x800700AA).
    Matches both 'MiniMuse' and dynamic document titles like 'Sir Diddlage · MiniMuse'."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        own_pid = os.getpid()
        found_hwnds: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindow(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title == "MiniMuse" or title.endswith("· MiniMuse") or title.endswith("- MiniMuse"):
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value != own_pid:
                    found_hwnds.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum_cb, 0)
        if found_hwnds:
            hwnd = found_hwnds[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _window_icon() -> str | None:
    """Path to the bundled MM icon for the native window, when present."""
    from pathlib import Path

    meipass = getattr(sys, "_MEIPASS", None)
    roots = [Path(meipass)] if meipass else []
    roots.append(Path(__file__).resolve().parents[2])
    rel_paths = (
        (
            "nanomuse/server/static/icon-256.png",
            "assets/brand/mm.ico",
            "nanomuse/server/static/icon.ico",
        )
        if os.name == "nt"
        else ("nanomuse/server/static/icon-256.png", "nanomuse/server/static/icon-512.png")
    )
    for root in roots:
        for rel in rel_paths:
            p = root / rel
            if p.is_file():
                return str(p)
    return None


def _open_standalone_browser_window(url: str) -> bool:
    """Fallback when pywebview has no GUI backend: launch Edge/Chrome in chromeless --app mode."""
    import shutil
    import subprocess

    arg = f"--app={url}"
    candidates: list[str] = []
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "brave-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    if os.name == "nt":
        for p in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ):
            if os.path.isfile(p):
                candidates.append(p)
    elif sys.platform == "darwin":
        for p in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            if os.path.isfile(p):
                candidates.append(p)
    for exe in candidates:
        try:
            kw: dict[str, object] = {"start_new_session": True}
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([exe, arg, "--class=MiniMuse"], **kw)  # type: ignore[call-overload]
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _boot_splash_html(port: int) -> str:
    """Instant-open native splash shown while the local FastAPI server finishes booting."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>MiniMuse</title>
<style>
  :root {{
    color-scheme: dark light;
    --bg: #111215;
    --card: #1a1c22;
    --fg: #f4f4f6;
    --muted: #9a9ca8;
    --accent: #3b82f6;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #f6f6f8;
      --card: #ffffff;
      --fg: #141518;
      --muted: #686a75;
      --accent: #0064d4;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 100%; height: 100%; overflow: hidden;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center;
  }}
  .wrap {{
    display: flex; flex-direction: column; align-items: center; gap: 18px;
    padding: 32px; text-align: center; max-width: 380px;
  }}
  .logo {{
    width: 68px; height: 68px; border-radius: 22px;
    background: linear-gradient(135deg, #0064d4, #6366f1);
    color: #fff; font-weight: 800; font-size: 26px; letter-spacing: -0.04em;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 12px 36px rgba(0, 100, 212, 0.32);
    animation: pulse 1.8s ease-in-out infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ transform: scale(1); box-shadow: 0 12px 36px rgba(0, 100, 212, 0.30); }}
    50% {{ transform: scale(1.04); box-shadow: 0 16px 44px rgba(99, 102, 241, 0.45); }}
  }}
  .title {{ font-size: 20px; font-weight: 700; letter-spacing: -0.01em; }}
  .status {{
    display: inline-flex; align-items: center; gap: 10px;
    font-size: 13.5px; color: var(--muted);
  }}
  .spinner {{
    width: 15px; height: 15px; border-radius: 50%;
    border: 2px solid color-mix(in srgb, var(--accent) 28%, transparent);
    border-top-color: var(--accent);
    animation: spin 0.75s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  .err {{
    margin-top: 8px; padding: 12px 14px; border-radius: 14px;
    background: rgba(239, 68, 68, 0.12); color: #ef4444;
    font-size: 12.5px; line-height: 1.45; display: none; word-break: break-word;
  }}
</style>
</head>
<body>
  <div class="wrap">
    <div class="logo">MM</div>
    <div class="title">MiniMuse</div>
    <div class="status" id="st"><span class="spinner"></span><span id="msg">Starting MiniMuse runtime…</span></div>
    <div class="err" id="err"></div>
  </div>
  <script>
    (function() {{
      const port = {int(port)};
      const start = Date.now();
      const msgEl = document.getElementById("msg");
      const errEl = document.getElementById("err");
      let done = false;

      async function poll() {{
        if (done) return;
        const elapsed = (Date.now() - start) / 1000;
        if (elapsed > 2.5 && msgEl) msgEl.textContent = "Loading workspace & local runtime…";
        try {{
          if (window.pywebview && window.pywebview.api && window.pywebview.api.get_boot_status) {{
            const info = await window.pywebview.api.get_boot_status();
            if (info && info.error) {{
              done = true;
              if (errEl) {{ errEl.style.display = "block"; errEl.textContent = info.error; }}
              if (msgEl) msgEl.textContent = "Startup failed";
              return;
            }}
            if (info && info.ready && info.url) {{
              done = true;
              if (msgEl) msgEl.textContent = "Opening MiniMuse…";
              window.location.replace(info.url);
              return;
            }}
          }} else {{
            const r = await fetch("http://127.0.0.1:" + port + "/api/health", {{ cache: "no-store" }});
            if (r.ok) {{
              done = true;
              window.location.replace("http://127.0.0.1:" + port + "/?shell=desktop");
              return;
            }}
          }}
        }} catch (_) {{}}
        setTimeout(poll, 120);
      }}
      window.addEventListener("pywebviewready", poll);
      setTimeout(poll, 40);
    }})();
  </script>
</body>
</html>"""


def _apply_win32_fullscreen(win: object, enabled: bool, saved: dict[str, object]) -> bool:
    """True edge-to-edge borderless fullscreen on Windows WinForms / WebView2 with zero
    frame/taskbar margin gaps on any DPI or monitor."""
    if sys.platform != "win32":
        return False
    form = getattr(win, "native", None)
    if form is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        import clr  # type: ignore[import-not-found]
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        import System  # type: ignore[import-not-found]
        import System.Windows.Forms as WinForms  # type: ignore[import-not-found]

        class _RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        class _MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        GWL_STYLE = -16
        GWL_EXSTYLE = -20
        WS_CAPTION = 0x00C00000
        WS_THICKFRAME = 0x00040000
        WS_MINIMIZEBOX = 0x00020000
        WS_MAXIMIZEBOX = 0x00010000
        WS_SYSMENU = 0x00080000
        WS_EX_DLGMODALFRAME = 0x00000001
        WS_EX_WINDOWEDGE = 0x00000100
        WS_EX_CLIENTEDGE = 0x00000200
        WS_EX_STATICEDGE = 0x00020000
        SWP_NOOWNERZORDER = 0x0200
        SWP_FRAMECHANGED = 0x0020
        SWP_SHOWWINDOW = 0x0040
        MONITOR_DEFAULTTONEAREST = 2
        HWND_TOP = 0

        def _do() -> None:
            hwnd = int(form.Handle.ToInt64())
            if enabled:
                if "style" not in saved:
                    saved["style"] = user32.GetWindowLongW(hwnd, GWL_STYLE)
                    saved["exstyle"] = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    saved["win_state"] = form.WindowState
                    saved["border_style"] = form.FormBorderStyle
                    saved["bounds"] = form.Bounds
                mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                mi = _MONITORINFO()
                mi.cbSize = ctypes.sizeof(_MONITORINFO)
                got_mi = bool(user32.GetMonitorInfoW(mon, ctypes.byref(mi)))
                screen_bounds = WinForms.Screen.FromControl(form).Bounds
                form.SuspendLayout()
                form.WindowState = WinForms.FormWindowState.Normal
                form.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                exstyle = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(
                    hwnd,
                    GWL_STYLE,
                    style & ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU),
                )
                user32.SetWindowLongW(
                    hwnd,
                    GWL_EXSTYLE,
                    exstyle & ~(WS_EX_DLGMODALFRAME | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE | WS_EX_STATICEDGE),
                )
                form.Bounds = screen_bounds
                form.ResumeLayout(True)
                if got_mi:
                    rc = mi.rcMonitor
                    user32.SetWindowPos(
                        hwnd,
                        HWND_TOP,
                        rc.left,
                        rc.top,
                        rc.right - rc.left,
                        rc.bottom - rc.top,
                        SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
                    )
                else:
                    user32.SetWindowPos(
                        hwnd,
                        HWND_TOP,
                        int(screen_bounds.Left),
                        int(screen_bounds.Top),
                        int(screen_bounds.Width),
                        int(screen_bounds.Height),
                        SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
                    )
            else:
                form.SuspendLayout()
                if "style" in saved:
                    user32.SetWindowLongW(hwnd, GWL_STYLE, int(saved["style"]))  # type: ignore[arg-type]
                if "exstyle" in saved:
                    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, int(saved["exstyle"]))  # type: ignore[arg-type]
                form.FormBorderStyle = saved.get("border_style", WinForms.FormBorderStyle.Sizable)
                if "bounds" in saved:
                    form.Bounds = saved["bounds"]
                form.WindowState = saved.get("win_state", WinForms.FormWindowState.Maximized)
                form.ResumeLayout(True)
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOP,
                    0,
                    0,
                    0,
                    0,
                    0x0001 | 0x0002 | 0x0004 | SWP_NOOWNERZORDER | SWP_FRAMECHANGED,
                )
                saved.clear()

        if getattr(form, "InvokeRequired", False):
            form.Invoke(System.Action(_do))
        else:
            _do()
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"win32 edge-to-edge fullscreen fallback: {exc}")
        return False


def _run_native_window(
    url: str,
    *,
    storage_dir: object | None = None,
    on_close: object | None = None,
    boot_port: int | None = None,
    data_dir: object | None = None,
    server_err: list[BaseException] | None = None,
) -> bool:
    """Open the MiniMuse UI in a dedicated native desktop window (never a browser tab).

    When ``boot_port`` is provided, opens the window INSTANTLY with a booting indicator while
    the local server starts in the background, then navigates directly to the app URL the
    millisecond ``/api/health`` responds.
    """
    if os.environ.get("MINIMUSE_NO_BROWSER") or os.environ.get("BROWSER"):
        return False
    if os.environ.get("NANOMUSE_OPEN_BROWSER", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        import webview

        from nanomuse.server.selfupdate import set_restart_hook
    except Exception as exc:  # noqa: BLE001
        _log(f"pywebview unavailable ({exc}); falling back")
        return False

    state_file = (storage_dir / "window-state.json") if storage_dir is not None else None
    start_fullscreen = False
    if state_file is not None and state_file.is_file():
        try:
            saved_json = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(saved_json, dict):
                start_fullscreen = bool(saved_json.get("fullscreen", False))
        except Exception:  # noqa: BLE001
            pass

    fs_state = {"fullscreen": start_fullscreen}
    win32_saved: dict[str, object] = {}
    win_holder: dict[str, object] = {}

    def _save_window_state() -> None:
        if state_file is None:
            return
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps({"fullscreen": bool(fs_state["fullscreen"])}), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass

    class _DesktopWindowApi:
        def get_boot_status(self) -> dict[str, object]:
            if server_err:
                return {"ready": False, "url": "", "error": f"MiniMuse server failed to start: {server_err[0]}"}
            if boot_port is None:
                return {"ready": True, "url": url, "error": ""}
            if _health_info("127.0.0.1", boot_port, timeout=0.35) is not None:
                return {"ready": True, "url": _app_url(boot_port, data_dir=data_dir), "error": ""}
            return {"ready": False, "url": "", "error": ""}

        def is_fullscreen(self) -> bool:
            return bool(fs_state["fullscreen"])

        def set_fullscreen(self, enabled: bool) -> bool:
            want = bool(enabled)
            w = win_holder.get("win")
            if w is not None and want != bool(fs_state["fullscreen"]):
                try:
                    if not _apply_win32_fullscreen(w, want, win32_saved):
                        w.toggle_fullscreen()
                    fs_state["fullscreen"] = want
                    _save_window_state()
                except Exception as exc:  # noqa: BLE001
                    _log(f"set_fullscreen failed: {exc}")
            return bool(fs_state["fullscreen"])

        def toggle_fullscreen(self) -> bool:
            return self.set_fullscreen(not bool(fs_state["fullscreen"]))

    use_splash = boot_port is not None and _health_info("127.0.0.1", boot_port, timeout=0.2) is None
    win_kwargs: dict[str, object] = {
        "width": 1220,
        "height": 840,
        "min_size": (420, 620),
        "maximized": not start_fullscreen,
        "fullscreen": False if sys.platform == "win32" else start_fullscreen,
        "text_select": True,
        "confirm_close": False,
        "js_api": _DesktopWindowApi(),
    }
    if use_splash and boot_port is not None:
        win_kwargs["html"] = _boot_splash_html(boot_port)
    else:
        win_kwargs["url"] = _app_url(boot_port, data_dir=data_dir) if boot_port is not None else url

    try:
        win = webview.create_window("MiniMuse", **win_kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        _log(f"webview.create_window failed: {exc}")
        return False
    if win is None:
        return False
    win_holder["win"] = win

    if sys.platform == "win32" and start_fullscreen:
        def _apply_initial_fs() -> None:
            _apply_win32_fullscreen(win, True, win32_saved)

        win.events.shown += _apply_initial_fs

    def _destroy_win() -> None:
        try:
            win.destroy()
        except Exception:  # noqa: BLE001
            pass

    set_restart_hook(_destroy_win)
    if callable(on_close):
        win.events.closed += lambda: on_close()

    kwargs: dict[str, object] = {"private_mode": False}
    if storage_dir is not None:
        storage_dir.mkdir(parents=True, exist_ok=True)
        kwargs["storage_path"] = str(storage_dir)
    icon = _window_icon()
    if icon and os.name != "nt":
        kwargs["icon"] = icon
    try:
        webview.start(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"webview.start failed: {exc}")
        return False


def _open_the_app(port: int, wait: float = 0.0, data_dir: object | None = None) -> bool:
    """Open MiniMuse in its standalone window or browser when native pywebview is unavailable."""
    if os.environ.get("MINIMUSE_NO_BROWSER"):
        return False
    import webbrowser

    if wait:
        _wait_for_health(port, wait=wait)
    url = _app_url(port, data_dir=data_dir)
    if not os.environ.get("BROWSER") and os.environ.get(
        "NANOMUSE_OPEN_BROWSER", ""
    ).strip().lower() not in ("1", "true", "yes"):
        if _open_standalone_browser_window(url):
            return True
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 — never take the app down over a browser
        return False


def _next_free_port(start: int, tries: int = 20) -> int:
    """The first port from ``start`` that nothing binds on this machine's loopback."""
    import socket

    for port in range(start, start + tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


# --- the desktop entry: a stable copy of the exe where the OS keeps its apps -----------

FROZEN = bool(getattr(sys, "frozen", False))


def _windows_shortcut(target: str, lnk_path: str) -> None:
    """Write ``lnk_path`` pointing at ``target`` through the shell's own COM
    interfaces (no pywin32, no PowerShell): IShellLinkW for the link, IPersistFile to
    save it. Any failure raises, and the caller treats the whole install as
    best-effort."""
    import ctypes
    import ctypes.wintypes as wt
    from ctypes import (
        POINTER,
        WINFUNCTYPE,
        Structure,
        byref,
        c_long,
        c_ubyte,
        c_ulong,
        c_ushort,
        c_void_p,
        c_wchar_p,
        windll,
    )

    class _GUID(Structure):
        _fields_ = [
            ("Data1", c_ulong),
            ("Data2", c_ushort),
            ("Data3", c_ushort),
            ("Data4", c_ubyte * 8),
        ]

    def guid(text: str) -> _GUID:
        d1, d2, d3, d4, d5 = text.split("-")
        return _GUID(
            int(d1, 16),
            int(d2, 16),
            int(d3, 16),
            (c_ubyte * 8).from_buffer_copy(bytes.fromhex(d4 + d5)),
        )

    clsid_shell_link = guid("00021401-0000-0000-C000-000000000046")
    iid_shell_link_w = guid("000214F9-0000-0000-C000-000000000046")
    iid_persist_file = guid("0000010B-0000-0000-C000-000000000046")

    ole32 = windll.ole32
    ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED — S_FALSE (already done) is fine
    ole32.CoCreateInstance.argtypes = [
        POINTER(_GUID),
        c_void_p,
        wt.DWORD,
        POINTER(_GUID),
        POINTER(c_void_p),
    ]
    link = c_void_p()
    if (
        ole32.CoCreateInstance(
            byref(clsid_shell_link), None, 1, byref(iid_shell_link_w), byref(link)
        )
        != 0
    ):
        raise OSError("CoCreateInstance(IShellLinkW) failed")

    def call(obj: c_void_p, index: int, *args: object) -> int:
        """Invoke vtable slot ``index`` (0-based, IUnknown included) on ``obj``. Wide
        strings pass as ``LPCWSTR``, everything else (ints, ``byref`` pointers) as
        pointer-sized words — every method here returns HRESULT."""
        table = ctypes.cast(obj, POINTER(c_void_p))[0]
        fn = ctypes.cast(table, POINTER(c_void_p))[index]
        types = [c_void_p] + [c_wchar_p if isinstance(a, str) else c_void_p for a in args]
        return WINFUNCTYPE(c_long, *types)(fn)(obj, *args)

    # IShellLinkW vtable slots, found by probe (WScript.Shell reads the file back the
    # same way): SetWorkingDirectory 9, SetPath 20
    call(link, 20, target)  # SetPath
    call(link, 9, os.path.dirname(target))  # SetWorkingDirectory
    persist = c_void_p()
    if call(link, 0, byref(iid_persist_file), byref(persist)) != 0:  # QueryInterface
        raise OSError("QueryInterface(IPersistFile) failed")
    try:
        if call(persist, 6, lnk_path, 1) != 0:  # IPersistFile::Save(path, remember)
            raise OSError(f"saving {lnk_path} failed")
    finally:
        call(link, 2)  # Release
        call(persist, 2)  # Release


def _windows_desktop_dir() -> str:
    """The real Desktop folder (OneDrive may have moved it), not a guess."""
    import ctypes
    from ctypes import Structure, byref, c_ubyte, c_ulong, c_ushort, c_wchar_p, windll

    class _GUID(Structure):
        _fields_ = [
            ("Data1", c_ulong),
            ("Data2", c_ushort),
            ("Data3", c_ushort),
            ("Data4", c_ubyte * 8),
        ]

    d1, d2, d3, d4, d5 = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641".split("-")
    fid = _GUID(
        int(d1, 16),
        int(d2, 16),
        int(d3, 16),
        (c_ubyte * 8).from_buffer_copy(bytes.fromhex(d4 + d5)),
    )
    pwstr = c_wchar_p()
    if windll.shell32.SHGetKnownFolderPath(byref(fid), 0, None, byref(pwstr)) != 0:
        raise OSError("SHGetKnownFolderPath(Desktop) failed")
    path = pwstr.value or ""
    ctypes.windll.ole32.CoTaskMemFree(pwstr)
    if not path:
        raise OSError("empty Desktop path")
    return path


def _canonical_windows_target():
    from pathlib import Path

    local_app = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local_app) / "Programs" / "MiniMuse" / "MiniMuse.exe"


def _canonical_linux_target():
    from pathlib import Path

    return Path.home() / ".local" / "bin" / "MiniMuse"


def _same_file_contents(a: object, b: object) -> bool:
    import hashlib
    from pathlib import Path

    pa, pb = Path(a), Path(b)  # type: ignore[arg-type]
    try:
        if not pa.is_file() or not pb.is_file():
            return False
        if pa.stat().st_size != pb.stat().st_size:
            return False
        ha, hb = hashlib.sha256(), hashlib.sha256()
        with pa.open("rb") as fa, pb.open("rb") as fb:
            for chunk in iter(lambda: fa.read(1 << 20), b""):
                ha.update(chunk)
            for chunk in iter(lambda: fb.read(1 << 20), b""):
                hb.update(chunk)
        return ha.digest() == hb.digest()
    except OSError:
        return False


def _windows_shortcut_target(lnk_path: str) -> str:
    """Read an existing ``.lnk`` target path via COM ``IShellLinkW::GetPath``, or ``""``."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        from ctypes import (
            POINTER,
            WINFUNCTYPE,
            Structure,
            byref,
            c_int,
            c_long,
            c_ubyte,
            c_ulong,
            c_ushort,
            c_void_p,
            c_wchar_p,
            create_unicode_buffer,
            windll,
        )

        class _GUID(Structure):
            _fields_ = [
                ("Data1", c_ulong),
                ("Data2", c_ushort),
                ("Data3", c_ushort),
                ("Data4", c_ubyte * 8),
            ]

        def guid(text: str) -> _GUID:
            d1, d2, d3, d4, d5 = text.split("-")
            return _GUID(
                int(d1, 16),
                int(d2, 16),
                int(d3, 16),
                (c_ubyte * 8).from_buffer_copy(bytes.fromhex(d4 + d5)),
            )

        ole32 = windll.ole32
        ole32.CoInitializeEx(None, 0x2)
        ole32.CoCreateInstance.argtypes = [
            POINTER(_GUID),
            c_void_p,
            wt.DWORD,
            POINTER(_GUID),
            POINTER(c_void_p),
        ]
        link = c_void_p()
        if (
            ole32.CoCreateInstance(
                byref(guid("00021401-0000-0000-C000-000000000046")),
                None,
                1,
                byref(guid("000214F9-0000-0000-C000-000000000046")),
                byref(link),
            )
            != 0
        ):
            return ""
        table = ctypes.cast(link, POINTER(c_void_p))[0]
        qi_fn = WINFUNCTYPE(c_long, c_void_p, POINTER(_GUID), POINTER(c_void_p))(
            ctypes.cast(table, POINTER(c_void_p))[0]
        )
        persist = c_void_p()
        if qi_fn(link, byref(guid("0000010B-0000-0000-C000-000000000046")), byref(persist)) != 0:
            WINFUNCTYPE(c_ulong, c_void_p)(ctypes.cast(table, POINTER(c_void_p))[2])(link)
            return ""
        ptable = ctypes.cast(persist, POINTER(c_void_p))[0]
        load_fn = WINFUNCTYPE(c_long, c_void_p, c_wchar_p, wt.DWORD)(
            ctypes.cast(ptable, POINTER(c_void_p))[5]
        )
        get_path_fn = WINFUNCTYPE(c_long, c_void_p, wt.LPWSTR, c_int, c_void_p, wt.DWORD)(
            ctypes.cast(table, POINTER(c_void_p))[3]
        )
        out = ""
        try:
            if load_fn(persist, lnk_path, 0) == 0:
                buf = create_unicode_buffer(520)
                if get_path_fn(link, buf, 520, None, 4) == 0:
                    out = buf.value or ""
        finally:
            WINFUNCTYPE(c_ulong, c_void_p)(ctypes.cast(ptable, POINTER(c_void_p))[2])(persist)
            WINFUNCTYPE(c_ulong, c_void_p)(ctypes.cast(table, POINTER(c_void_p))[2])(link)
        return out
    except Exception:  # noqa: BLE001
        return ""


def _terminate_windows_target_processes(target: object) -> bool:
    """Terminate older running MiniMuse processes whose image path equals ``target``."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        from pathlib import Path

        target_norm = os.path.normcase(os.path.abspath(str(Path(target))))  # type: ignore[arg-type]
        self_pid = os.getpid()
        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_TERMINATE = 0x0001
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", wt.WCHAR * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == wt.HANDLE(-1).value:
            return False
        killed = False
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                pid = int(entry.th32ProcessID)
                if pid > 4 and pid != self_pid:
                    proc = kernel32.OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid
                    )
                    if proc:
                        try:
                            buf = ctypes.create_unicode_buffer(1024)
                            size = wt.DWORD(1024)
                            if kernel32.QueryFullProcessImageNameW(
                                proc, 0, buf, ctypes.byref(size)
                            ):
                                img = os.path.normcase(os.path.abspath(buf.value))
                                if img == target_norm and kernel32.TerminateProcess(proc, 0):
                                    killed = True
                        finally:
                            kernel32.CloseHandle(proc)
                ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snap)
        return killed
    except Exception:  # noqa: BLE001
        return False


def _stop_older_running_instance(host: str, port: int, target: object | None = None) -> bool:
    """Ask a locally running older MiniMuse server to exit so an upgrade can replace it."""
    from nanomuse import __version__
    from nanomuse.local_http import local_open
    from nanomuse.server.update import newer_than

    info = _health_info(host, port, timeout=1.5)
    running_ver = str(info.get("version") or "") if info else ""
    if not running_ver or not newer_than(__version__, running_ver):
        return False
    probe_host = {"": "127.0.0.1", "0.0.0.0": "127.0.0.1", "localhost": "127.0.0.1", "::": "::1"}.get(
        host, host
    )
    base = f"http://[{probe_host}]:{port}" if ":" in probe_host else f"http://{probe_host}:{port}"
    tok = _token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    _log(f"stopping older running MiniMuse {running_ver} on {host}:{port} before upgrading to {__version__}")
    req_ok = False
    try:
        from urllib.request import Request

        req = Request(f"{base}/api/system/shutdown", data=b"{}", headers=headers, method="POST")
        with local_open(req, timeout=3.0) as resp:
            req_ok = 200 <= resp.status < 300
    except Exception:  # noqa: BLE001
        req_ok = False
    if req_ok:
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if _health_info(host, port, timeout=0.6) is None:
                time.sleep(0.4)
                return True
            time.sleep(0.2)
    if sys.platform == "win32" and target is not None:
        if _terminate_windows_target_processes(target):
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if _health_info(host, port, timeout=0.6) is None:
                    time.sleep(0.3)
                    return True
                time.sleep(0.2)
    return _health_info(host, port, timeout=0.6) is None


def _strip_zone_identifier(path_str: str) -> None:
    """Remove Windows Mark of the Web (Zone.Identifier ADS) so updates never re-prompt."""
    if sys.platform != "win32":
        return
    try:
        os.remove(f"{path_str}:Zone.Identifier")
    except OSError:
        pass


def _install_windows(exe: str) -> bool:
    # a stable identity for the taskbar (all instances group under the app's name)
    import ctypes
    import shutil
    from pathlib import Path

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MiniMuse")

    target = _canonical_windows_target()
    target_dir = target.parent
    exe_norm = os.path.normcase(os.path.abspath(exe))
    tgt_norm = os.path.normcase(os.path.abspath(str(target)))
    up_to_date = exe_norm == tgt_norm or _same_file_contents(exe, target)
    if not up_to_date:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp = target_dir / "MiniMuse.new"
        last_exc: OSError | None = None
        for attempt in range(30):
            if attempt in (2, 8, 16):
                _terminate_windows_target_processes(target)
            try:
                shutil.copyfile(exe, tmp)  # copyfile drops the Mark of the Web, too
                _strip_zone_identifier(str(tmp))
                os.replace(tmp, target)  # atomic; retries allow AV/exit handles to release
                up_to_date = True
                break
            except OSError as exc:
                last_exc = exc
                tmp.unlink(missing_ok=True)
                time.sleep(0.25)
        if not up_to_date:
            if not target.exists() and last_exc is not None:
                raise last_exc
            _log(f"the installed copy stayed locked ({last_exc}); keeping it for now")
    if not target.exists():
        raise OSError(f"{target} did not appear")
    _strip_zone_identifier(str(target))
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop = Path(_windows_desktop_dir())
    for where, lnk in (
        ("Start Menu", start_menu / "MiniMuse.lnk"),
        ("desktop", desktop / "MiniMuse.lnk"),
    ):
        if lnk.exists():
            existing_target = _windows_shortcut_target(str(lnk))
            if not existing_target or os.path.normcase(os.path.abspath(existing_target)) == tgt_norm:
                continue
        _windows_shortcut(str(target), str(lnk))
        _log(f"updated shortcut in the {where}: {lnk} -> {target}")
    return up_to_date


def _install_linux(exe: str) -> None:
    import shlex
    import shutil
    from pathlib import Path

    target = _canonical_linux_target()
    exe_res = Path(exe).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if exe_res != target.resolve() and not _same_file_contents(exe, target):
        tmp = target.with_name(target.name + ".new")
        shutil.copyfile(exe, tmp)
        tmp.chmod(0o755)
        os.replace(tmp, target)
    elif not target.exists():
        shutil.copyfile(exe, target)
        target.chmod(0o755)
    entry_dir = Path.home() / ".local" / "share" / "applications"
    entry_dir.mkdir(parents=True, exist_ok=True)
    entry = entry_dir / "minimuse.desktop"
    body = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MiniMuse\n"
        f"Exec={shlex.quote(str(target))}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "Comment=Your personal agent, on this machine\n"
        "StartupWMClass=MiniMuse\n"
    )
    entry.write_text(body, encoding="utf-8")
    entry.chmod(0o755)
    _log(f"added to the applications menu: {entry} -> {target}")


def _canonical_handoff_if_needed(
    host: str,
    port: int,
    *,
    forward_args: list[str] | None = None,
) -> None:
    """Ensure frozen desktop execution always runs from the one non-versioned canonical binary."""
    if not FROZEN or os.environ.get("MINIMUSE_NO_INSTALL") or os.environ.get("MINIMUSE_CANONICAL_RELAUNCH"):
        return
    import subprocess
    from pathlib import Path

    extra_args = [a for a in (forward_args or []) if a]
    if sys.platform == "win32":
        target = _canonical_windows_target()
        exe_norm = os.path.normcase(os.path.abspath(sys.executable))
        tgt_norm = os.path.normcase(os.path.abspath(str(target)))
        if exe_norm == tgt_norm:
            return
        _stop_older_running_instance(host, port, target)
        updated = _install_windows(sys.executable)
        if not updated or not target.is_file():
            return
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        env = dict(os.environ)
        env["MINIMUSE_CANONICAL_RELAUNCH"] = "1"
        _log(f"handing off from {sys.executable} to canonical {target}")
        subprocess.Popen([str(target), *extra_args], close_fds=True, env=env, creationflags=flags)
        raise SystemExit(0)
    if sys.platform.startswith("linux"):
        target = _canonical_linux_target()
        if Path(sys.executable).resolve() == target.resolve():
            return
        _stop_older_running_instance(host, port, target)
        _install_linux(sys.executable)
        if target.is_file():
            env = dict(os.environ)
            env["MINIMUSE_CANONICAL_RELAUNCH"] = "1"
            _log(f"handing off from {sys.executable} to canonical {target}")
            os.execve(str(target), [str(target), *extra_args], env)


def _install_app_entry() -> None:
    """Put a stable, searchable copy of the app on the machine (best effort, once per
    place): Windows gets %LOCALAPPDATA%\\Programs\\MiniMuse + Start Menu and Desktop
    links, Linux a ~/.local/share/applications entry. ``MINIMUSE_NO_INSTALL`` opts out
    (tests, portable use)."""
    if not FROZEN or os.environ.get("MINIMUSE_NO_INSTALL"):
        return
    try:
        if sys.platform == "win32":
            _install_windows(sys.executable)
        elif sys.platform == "linux":
            _install_linux(sys.executable)
    except Exception as exc:  # noqa: BLE001 — the app runs fine without the shortcuts
        _log(f"could not add the Start Menu entry ({exc}); the app still runs")


def _ensure_stdio() -> None:
    """GUI-mode binaries on Windows start with sys.stdout/stderr=None; redirect to log/devnull."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from pathlib import Path

    try:
        log_path = Path.home() / ".nanomuse" / "desktop.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    except Exception:  # noqa: BLE001
        fh = open(os.devnull, "a", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def _app_mode() -> None:
    """Double-clicked: serve locally and open MiniMuse in its native desktop window."""
    _ensure_stdio()
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MiniMuse")
        except Exception:  # noqa: BLE001
            pass
    from nanomuse.cli import _settings
    from nanomuse.server import port_taken, serve

    try:
        settings = _settings(None)
    except SystemExit as exc:  # a config.toml the runtime refuses: say it, don't just die
        _fatal(f"MiniMuse could not read its settings (exit {exc.code}).")
        raise
    from nanomuse import __version__

    # Listen on the network too: a phone pairs by scanning this app's QR, and that QR carries
    # the LAN address, so a loopback-only server could never answer it ("pairing does not
    # work"). The window, the health probe and every self-check still use 127.0.0.1, and
    # every request carries the access token, so this only widens reach, not access.
    host, port = _bind_host(settings), settings.server.port
    _canonical_handoff_if_needed(host, port)
    _install_app_entry()
    if FROZEN:
        target = _canonical_windows_target() if sys.platform == "win32" else _canonical_linux_target()
        _stop_older_running_instance(host, port, target)

    taken = port_taken(host, port)
    storage_dir = settings.data_dir / "webview"
    # Clean up any stale secondary WebView2 session folders from earlier launches
    try:
        import shutil

        for old_dir in settings.data_dir.glob("webview-session-*"):
            if old_dir.is_dir():
                shutil.rmtree(old_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass

    if taken in (f"nanoMuse {__version__}", f"MiniMuse {__version__}"):
        # The same release is already serving on this port: focus its window if open,
        # otherwise open a native window with an unlocked per-PID storage path so WebView2
        # never hits 0x800700AA (resource in use) and shows a blank white window.
        _log(f"{taken} is already running on {host}:{port}; activating window")
        if _focus_existing_window():
            raise SystemExit(0)
        _wait_for_health(port, wait=10.0)
        url = _app_url(port, data_dir=settings.data_dir)
        unlocked_dir = settings.data_dir / f"webview-session-{os.getpid()}"
        if not _run_native_window(url, storage_dir=unlocked_dir):
            _open_the_app(port, wait=2.0, data_dir=settings.data_dir)
        raise SystemExit(0)
    if taken:
        port = _next_free_port(port + 1)
        _log(
            f"port {settings.server.port} is held by {taken}; serving {__version__} on {port} instead"
        )

    server_ref: list[object] = []
    server_err: list[BaseException] = []

    def _serve_bg() -> None:
        try:
            serve(
                settings,
                host=host,
                port=port,
                print_qr=False,
                on_server=server_ref.append,
            )
        except BaseException as exc:  # noqa: BLE001
            server_err.append(exc)

    srv_thread = threading.Thread(target=_serve_bg, name="minimuse-server", daemon=True)
    srv_thread.start()

    url = _app_url(port, data_dir=settings.data_dir)

    def _stop_server() -> None:
        for srv in server_ref:
            srv.should_exit = True  # type: ignore[attr-defined]

    # Open the native desktop window IMMEDIATELY with a booting splash instead of blocking ~5s
    if _run_native_window(
        url,
        storage_dir=storage_dir,
        on_close=_stop_server,
        boot_port=port,
        data_dir=settings.data_dir,
        server_err=server_err,
    ):
        srv_thread.join(timeout=3.0)
        return

    if not _wait_for_health(port, wait=60.0):
        if server_err:
            _fatal(f"MiniMuse server failed to start: {server_err[0]}")
            raise SystemExit(1)

    # Fallback (headless / no GUI webview engine): open chromeless app window or browser
    # and keep serving until interrupted.
    threading.Thread(
        target=_open_the_app,
        args=(port,),
        kwargs={"wait": 5.0, "data_dir": settings.data_dir},
        daemon=True,
    ).start()
    try:
        while srv_thread.is_alive():
            srv_thread.join(timeout=0.5)
    except KeyboardInterrupt:
        _stop_server()


def main() -> None:
    multiprocessing.freeze_support()
    _silence_missing_consoles()
    # the bundle's OpenSSL knows the build machine's certificate paths, not this one's
    from nanomuse.certs import ensure_ca_bundle

    ensure_ca_bundle()
    if FROZEN and len(sys.argv) == 1:
        # opened like a normal app: no console, no arguments — just be the app
        try:
            _app_mode()
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 — a windowed build must not die silently
            import traceback

            _log("startup failed:\n" + "".join(traceback.format_exception(exc)))
            _fatal(f"MiniMuse could not start ({exc}).")
            raise
        return
    # the bridge commands (console scripts in a pip install) are subcommands of this one
    # executable: `nanomuse device …`, `nanomuse browser …`, `nanomuse open …`
    if len(sys.argv) > 1 and sys.argv[1] in ("device", "browser", "open"):
        from nanomuse.bridge import cli as bridge

        which = sys.argv.pop(1)
        {"device": bridge.device_main, "browser": bridge.browser_main, "open": bridge.open_main}[
            which
        ]()
        return
    if FROZEN and len(sys.argv) > 1 and sys.argv[1] in ("serve", "app"):
        try:
            _canonical_handoff_if_needed("127.0.0.1", 8787, forward_args=sys.argv[1:])
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 — fall back to direct execution on error
            _log(f"canonical CLI handoff skipped ({exc})")
    from nanomuse.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
