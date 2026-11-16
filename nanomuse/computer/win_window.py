"""Background window mode on Windows: capture and drive one window without stealing focus.

Mirrors :mod:`nanomuse.computer.mac_window` on Windows 10/11:
- Enumerates windows via ``EnumWindows`` and process names via ``QueryFullProcessImageNameW``.
- Captures a specific HWND via ``PrintWindow(..., PW_RENDERFULLCONTENT = 2)``, which renders
  Chromium/Electron/GPU-accelerated windows even while occluded behind other windows.
- Sends pointer, wheel, and keyboard messages via ``PostMessageW`` without calling
  ``SetForegroundWindow`` or moving the user's hardware cursor.
"""

from __future__ import annotations

import ctypes
import io
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from nanomuse.computer.mac_window import (
    WindowFrame,
    WindowInfo,
    WindowLayerBroken,
    WindowUnavailable,
    choose_window,
)

PW_RENDERFULLCONTENT = 2
SW_SHOWNOACTIVATE = 4

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

_WIN_VK = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "option": 0x12,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "spacebar": 0x20,
    "pageup": 0x21,
    "page_up": 0x21,
    "pagedown": 0x22,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}


class WinAdapter(Protocol):
    def windows(self) -> list[WindowInfo]: ...
    def capture(self, window_id: int) -> bytes | None: ...
    def click(self, hwnd: int, sx: float, sy: float, button: str = "left", clicks: int = 1) -> None: ...
    def move(self, hwnd: int, sx: float, sy: float) -> None: ...
    def drag(self, hwnd: int, sx: float, sy: float, sx2: float, sy2: float) -> None: ...
    def scroll(self, hwnd: int, sx: float, sy: float, dy: float) -> None: ...
    def type_text(self, hwnd: int, text: str) -> None: ...
    def key(self, hwnd: int, keys: list[str]) -> None: ...


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def available() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "Windows background window mode is only available on Windows"
    return True, ""


def _lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def _wparam_wheel(delta: int) -> int:
    return (delta & 0xFFFF) << 16


class Win32Adapter:  # pragma: no cover - Windows desktop GUI APIs
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise WindowLayerBroken("Win32 window adapter requires Windows")
        self.user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self.gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]
        self.kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        try:
            self.user32.SetProcessDPIAware()
        except Exception:
            pass

    def _proc_name(self, pid: int) -> str:
        if pid <= 0:
            return ""
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(512)
            buf = ctypes.create_unicode_buffer(512)
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return Path(buf.value).stem
        finally:
            self.kernel32.CloseHandle(handle)
        return ""

    def windows(self) -> list[WindowInfo]:
        out: list[WindowInfo] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd: int, _lparam: int) -> bool:
            if not self.user32.IsWindowVisible(hwnd):
                return True
            rect = wintypes.RECT()
            if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = float(rect.right - rect.left)
            h = float(rect.bottom - rect.top)
            if w < 80 or h < 80:
                return True
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            owner = self._proc_name(int(pid.value))
            if not owner:
                return True
            out.append(
                WindowInfo(
                    id=int(hwnd),
                    pid=int(pid.value),
                    owner=owner,
                    title=title,
                    x=float(rect.left),
                    y=float(rect.top),
                    width=w,
                    height=h,
                    layer=0,
                    alpha=1.0,
                    on_screen=not bool(self.user32.IsIconic(hwnd)),
                    bundle_id=f"{owner}.exe",
                )
            )
            return True

        self.user32.EnumWindows(enum_proc(cb), 0)
        return out

    def capture(self, window_id: int) -> bytes | None:
        hwnd = int(window_id)
        if not self.user32.IsWindow(hwnd):
            return None
        if self.user32.IsIconic(hwnd):
            # Restore softly without bringing to foreground or stealing keyboard focus.
            self.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            time.sleep(0.15)
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            return None

        hwnd_dc = self.user32.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None
        mem_dc = self.gdi32.CreateCompatibleDC(hwnd_dc)
        bmp = self.gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
        old_obj = self.gdi32.SelectObject(mem_dc, bmp)
        try:
            ok = self.user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
            if not ok:
                ok = self.user32.PrintWindow(hwnd, mem_dc, 0)
            if not ok:
                return None
            bmi = _BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0
            buf = ctypes.create_string_buffer(w * h * 4)
            lines = self.gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
            if lines != h:
                return None
            img = Image.frombuffer("RGBA", (w, h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
            out = io.BytesIO()
            img.save(out, "PNG")
            return out.getvalue()
        finally:
            self.gdi32.SelectObject(mem_dc, old_obj)
            self.gdi32.DeleteObject(bmp)
            self.gdi32.DeleteDC(mem_dc)
            self.user32.ReleaseDC(hwnd, hwnd_dc)

    def _target_child_and_client(self, hwnd: int, sx: float, sy: float) -> tuple[int, int, int]:
        pt = wintypes.POINT(int(round(sx)), int(round(sy)))
        target = hwnd
        # Walk direct child chain inside client area (important for Chrome/Electron RenderWidgetHostHWND).
        for _ in range(4):
            cpt = wintypes.POINT(pt.x, pt.y)
            self.user32.ScreenToClient(target, ctypes.byref(cpt))
            child = self.user32.ChildWindowFromPointEx(target, cpt, 0x0001 | 0x0002)
            if not child or child == target:
                break
            target = int(child)
        cpt = wintypes.POINT(pt.x, pt.y)
        self.user32.ScreenToClient(target, ctypes.byref(cpt))
        return target, int(cpt.x), int(cpt.y)

    def _input_hwnd(self, hwnd: int) -> int:
        # Prefer Chromium/Electron render widget child if present so keystrokes reach web pages.
        found: list[int] = []
        enum_child = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(child: int, _lp: int) -> bool:
            cls = ctypes.create_unicode_buffer(128)
            self.user32.GetClassNameW(child, cls, 128)
            if "RenderWidgetHostHWND" in cls.value or "Intermediate D3D Window" in cls.value:
                found.append(int(child))
                return False
            return True

        self.user32.EnumChildWindows(hwnd, enum_child(cb), 0)
        return found[0] if found else hwnd

    def move(self, hwnd: int, sx: float, sy: float) -> None:
        target, cx, cy = self._target_child_and_client(hwnd, sx, sy)
        self.user32.PostMessageW(target, WM_MOUSEMOVE, 0, _lparam(cx, cy))

    def click(self, hwnd: int, sx: float, sy: float, button: str = "left", clicks: int = 1) -> None:
        target, cx, cy = self._target_child_and_client(hwnd, sx, sy)
        lp = _lparam(cx, cy)
        btn = button.lower()
        if btn == "right":
            down_msg, up_msg, flag = WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON
        elif btn == "middle":
            down_msg, up_msg, flag = WM_MBUTTONDOWN, WM_MBUTTONUP, MK_MBUTTON
        else:
            down_msg, up_msg, flag = WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON
        self.user32.PostMessageW(target, WM_MOUSEMOVE, 0, lp)
        for idx in range(max(1, clicks)):
            if idx == 1 and btn == "left":
                self.user32.PostMessageW(target, WM_LBUTTONDBLCLK, flag, lp)
            else:
                self.user32.PostMessageW(target, down_msg, flag, lp)
            time.sleep(0.03)
            self.user32.PostMessageW(target, up_msg, 0, lp)
            time.sleep(0.03)

    def drag(self, hwnd: int, sx: float, sy: float, sx2: float, sy2: float) -> None:
        target, cx, cy = self._target_child_and_client(hwnd, sx, sy)
        _, cx2, cy2 = self._target_child_and_client(hwnd, sx2, sy2)
        self.user32.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, _lparam(cx, cy))
        steps = 8
        for i in range(1, steps + 1):
            mx = int(round(cx + (cx2 - cx) * i / steps))
            my = int(round(cy + (cy2 - cy) * i / steps))
            self.user32.PostMessageW(target, WM_MOUSEMOVE, MK_LBUTTON, _lparam(mx, my))
            time.sleep(0.015)
        self.user32.PostMessageW(target, WM_LBUTTONUP, 0, _lparam(cx2, cy2))

    def scroll(self, hwnd: int, sx: float, sy: float, dy: float) -> None:
        target, _cx, _cy = self._target_child_and_client(hwnd, sx, sy)
        # WM_MOUSEWHEEL carries screen coordinates in lParam!
        delta = int(round(dy * 120))
        if delta == 0:
            delta = 120 if dy >= 0 else -120
        lp = _lparam(int(round(sx)), int(round(sy)))
        self.user32.PostMessageW(target, WM_MOUSEWHEEL, _wparam_wheel(delta), lp)

    def type_text(self, hwnd: int, text: str) -> None:
        target = self._input_hwnd(hwnd)
        for ch in text:
            if ch in "\r\n":
                self.user32.PostMessageW(target, WM_KEYDOWN, 0x0D, 0)
                time.sleep(0.01)
                self.user32.PostMessageW(target, WM_KEYUP, 0x0D, 0xC0000001)
            else:
                self.user32.PostMessageW(target, WM_CHAR, ord(ch), 0)
            time.sleep(0.008)

    def key(self, hwnd: int, keys: list[str]) -> None:
        target = self._input_hwnd(hwnd)
        vk_list: list[int] = []
        for raw in keys:
            k = raw.strip().lower()
            if k in _WIN_VK:
                vk_list.append(_WIN_VK[k])
            elif len(k) == 1:
                vk = ord(k.upper())
                if 0x30 <= vk <= 0x5A:
                    vk_list.append(vk)
        for vk in vk_list:
            self.user32.PostMessageW(target, WM_KEYDOWN, vk, 0)
            time.sleep(0.015)
        for vk in reversed(vk_list):
            self.user32.PostMessageW(target, WM_KEYUP, vk, 0xC0000001)
            time.sleep(0.015)


class WinWindowHands:
    """Hands interface for one background window on Windows."""

    name = "win_window"

    def __init__(self, adapter: WinAdapter | None = None):
        self.adapter: WinAdapter = adapter or Win32Adapter()
        self.frame: WindowFrame | None = None

    def size(self) -> tuple[int, int]:
        if self.frame is not None:
            return int(self.frame.window.width), int(self.frame.window.height)
        return 0, 0

    def look(self, app: str, title: str = "") -> tuple[bytes, WindowFrame]:
        windows = self.adapter.windows()
        win = choose_window(windows, app, title)
        if win is None:
            # Allow matching minimized windows too so PrintWindow can render them softly without focus.
            low = app.strip().lower()
            title_low = title.strip().lower()
            cands = [
                w
                for w in windows
                if low in w.owner.lower() or low in w.bundle_id.lower() or low in w.title.lower()
            ]
            if title_low:
                titled = [w for w in cands if title_low in w.title.lower()]
                if titled:
                    cands = titled
            if cands:
                cands.sort(key=lambda w: w.area, reverse=True)
                win = cands[0]
        if win is None:
            raise WindowUnavailable(f"{app!r} has no open window right now")
        png = self.adapter.capture(win.id)
        if not png:
            raise WindowUnavailable(f"could not capture window for {win.owner or app}")
        img = Image.open(io.BytesIO(png))
        frame = WindowFrame(win, img.width, img.height)
        self.frame = frame
        return png, frame

    def _require_frame(self) -> WindowFrame:
        if self.frame is None:
            raise WindowUnavailable("no window snapshot has been captured yet")
        return self.frame

    def click(self, x: float, y: float, *, button: str = "left", clicks: int = 1) -> None:
        frame = self._require_frame()
        sx, sy = frame.to_screen(x, y)
        self.adapter.click(frame.window.id, sx, sy, button=button, clicks=clicks)

    def move(self, x: float, y: float) -> None:
        frame = self._require_frame()
        sx, sy = frame.to_screen(x, y)
        self.adapter.move(frame.window.id, sx, sy)

    def drag(self, x: float, y: float, x2: float, y2: float) -> None:
        frame = self._require_frame()
        sx, sy = frame.to_screen(x, y)
        sx2, sy2 = frame.to_screen(x2, y2)
        self.adapter.drag(frame.window.id, sx, sy, sx2, sy2)

    def scroll(self, x: float, y: float, dy: float) -> None:
        frame = self._require_frame()
        sx, sy = frame.to_screen(x, y)
        self.adapter.scroll(frame.window.id, sx, sy, dy)

    def type(self, text: str) -> None:
        frame = self._require_frame()
        self.adapter.type_text(frame.window.id, text)

    def key(self, keys: list[str]) -> None:
        frame = self._require_frame()
        self.adapter.key(frame.window.id, keys)

    def open_app(self, app: str) -> str:
        from nanomuse.computer.hands import open_application

        return open_application(app)


def list_open_windows() -> list[dict[str, Any]]:
    """Return open desktop windows for UI selectors when supported."""
    ok, _ = available()
    if ok:
        return [w.to_dict() for w in Win32Adapter().windows()]
    if sys.platform == "darwin":
        from nanomuse.computer import mac_window

        m_ok, _ = mac_window.available()
        if m_ok:
            return [w.to_dict() for w in mac_window.QuartzAdapter().windows()]
    return []


__all__ = [
    "WinAdapter",
    "Win32Adapter",
    "WinWindowHands",
    "available",
    "list_open_windows",
]
