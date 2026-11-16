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


def _token() -> str:
    try:
        with open(os.path.join(_data_dir(), "server_token"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


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


def _app_url(port: int) -> str:
    token = _token()
    # ?shell=desktop tells the web app it is inside the native window (desktop layout at any width)
    base = f"http://127.0.0.1:{port}/?shell=desktop"
    return base if not token else f"{base}#token={token}"


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


def _run_native_window(
    url: str,
    *,
    storage_dir: object | None = None,
    on_close: object | None = None,
) -> bool:
    """Open the MiniMuse UI in a dedicated native desktop window (never a browser tab)."""
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

    try:
        win = webview.create_window(
            "MiniMuse",
            url,
            width=1220,
            height=840,
            min_size=(420, 620),
            maximized=True,
            text_select=True,
            confirm_close=False,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"webview.create_window failed: {exc}")
        return False
    if win is None:
        return False

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


def _open_the_app(port: int, wait: float = 0.0) -> bool:
    """Open MiniMuse in its standalone window or browser when native pywebview is unavailable."""
    if os.environ.get("MINIMUSE_NO_BROWSER"):
        return False
    import webbrowser

    if wait:
        _wait_for_health(port, wait=wait)
    url = _app_url(port)
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

    host, port = "127.0.0.1", settings.server.port
    _canonical_handoff_if_needed(host, port)
    _install_app_entry()
    if FROZEN:
        target = _canonical_windows_target() if sys.platform == "win32" else _canonical_linux_target()
        _stop_older_running_instance(host, port, target)

    taken = port_taken(host, port)
    storage_dir = settings.data_dir / "webview"
    if taken in (f"nanoMuse {__version__}", f"MiniMuse {__version__}"):
        # The same release is already serving on this port: open our native window on it.
        _log(f"{taken} is already running on {host}:{port}; opening native window")
        _wait_for_health(port, wait=10.0)
        url = _app_url(port)
        if not _run_native_window(url, storage_dir=storage_dir):
            _open_the_app(port, wait=2.0)
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

    if not _wait_for_health(port, wait=60.0):
        if server_err:
            _fatal(f"MiniMuse server failed to start: {server_err[0]}")
            raise SystemExit(1)

    url = _app_url(port)

    def _stop_server() -> None:
        for srv in server_ref:
            srv.should_exit = True  # type: ignore[attr-defined]

    if _run_native_window(url, storage_dir=storage_dir, on_close=_stop_server):
        srv_thread.join(timeout=3.0)
        return

    # Fallback (headless / no GUI webview engine): open chromeless app window or browser
    # and keep serving until interrupted.
    threading.Thread(target=_open_the_app, args=(port,), kwargs={"wait": 5.0}, daemon=True).start()
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
