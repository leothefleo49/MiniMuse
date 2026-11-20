"""Meta Quest / Horizon OS & Standalone VR Headset control tool (`quest_control`).

Lets MiniMuse act as a far more capable VR assistant than built-in Meta AI:
- Detect & connect Meta Quest 2 / Quest 3 / Quest 3S / Quest Pro / Pico / Vive XR / Android XR
  over USB or Wireless ADB (including enabling wireless ADB with `enable_wifi`).
- Check battery, storage, OS version, and installed VR games/apps.
- Launch VR apps & games, open URLs / WebXR experiences in Quest Browser.
- Download files straight to Quest `/sdcard/Download/` and sideload `.apk` apps/mods.
- Capture live Quest screenshots, type text into VR inputs, tap UI, adjust volume/refresh rate.
- Enable MiniMuse's on-device Accessibility Service on Quest so MiniMuse running inside the
  headset can read and operate Quest UI even while disconnected from a PC.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.win_proc import hidden_subprocess_kwargs

VR_KEYWORDS = (
    "oculus",
    "meta",
    "quest",
    "monterey",   # Quest 1
    "hollywood",  # Quest 2
    "seacliff",   # Quest Pro
    "eureka",     # Quest 3
    "panther",    # Quest 3S
    "pico",
    "bytedance",
    "vive",
    "lynx",
    "magic leap",
    "xr",
)


def is_vr_headset_text(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in VR_KEYWORDS)


def find_adb_binary(data_dir: Path | None = None) -> str | None:
    """Locate adb on PATH, MiniMuse's local tools folder, or standard Android SDK paths."""
    found = shutil.which("adb")
    if found:
        return found
    exe = "adb.exe" if os.name == "nt" else "adb"
    candidates: list[Path] = []
    if data_dir is not None:
        candidates.append(Path(data_dir) / "tools" / "platform-tools" / exe)
    home = Path.home()
    candidates.extend(
        [
            home / ".nanomuse" / "tools" / "platform-tools" / exe,
            Path(r"C:\Android\Sdk\platform-tools\adb.exe"),
            home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
            home / "Library" / "Android" / "sdk" / "platform-tools" / "adb",
            home / "Android" / "Sdk" / "platform-tools" / "adb",
            Path("/usr/bin/adb"),
            Path("/usr/local/bin/adb"),
        ]
    )
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def probe_vr_adb_device(adb: str, dev_id: str) -> dict[str, Any]:
    """Query manufacturer, model, product, Android release, and VR headset status."""
    props: dict[str, str] = {}
    for key in (
        "ro.product.manufacturer",
        "ro.product.model",
        "ro.product.brand",
        "ro.product.device",
        "ro.product.name",
        "ro.build.version.release",
        "ro.build.characteristics",
        "ro.serialno",
    ):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            p = subprocess.run(
                [adb, "-s", dev_id, "shell", "getprop", key],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            props[key] = (p.stdout or "").strip()
    hay = " ".join(props.values()) + " " + dev_id
    is_watch = "watch" in props.get("ro.build.characteristics", "").lower()
    is_vr = (not is_watch) and is_vr_headset_text(hay)
    mfg = props.get("ro.product.manufacturer") or props.get("ro.product.brand") or ""
    model = props.get("ro.product.model") or props.get("ro.product.device") or dev_id
    display_name = f"{mfg} {model}".strip() if mfg and mfg.lower() not in model.lower() else model
    return {
        "id": dev_id,
        "manufacturer": mfg,
        "model": model,
        "name": display_name,
        "device": props.get("ro.product.device") or "",
        "android_version": props.get("ro.build.version.release") or "",
        "serial": props.get("ro.serialno") or "",
        "is_vr": is_vr,
        "is_watch": is_watch,
    }


def list_vr_adb_devices(adb: str) -> list[dict[str, Any]]:
    """Return all ADB devices annotated with ``is_vr``."""
    out: list[dict[str, Any]] = []
    try:
        proc = subprocess.run(
            [adb, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return out
    for line in (proc.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        dev_id, state = parts[0], parts[1]
        entry: dict[str, Any] = {"id": dev_id, "state": state, "is_vr": False, "name": dev_id}
        for kv in parts[2:]:
            if ":" in kv:
                k, v = kv.split(":", 1)
                if k in ("model", "product", "device"):
                    entry[k] = v
        if state == "device":
            info = probe_vr_adb_device(adb, dev_id)
            entry.update(info)
        else:
            entry["is_vr"] = is_vr_headset_text(line)
        out.append(entry)
    return out


class QuestVRTool(BaseTool):
    name: str = "quest_control"
    description: str = (
        "Control and manage Meta Quest (Quest 2, Quest 3, Quest 3S, Quest Pro / Horizon OS), "
        "Pico, HTC Vive XR, and other Android-based VR headsets over USB or Wi-Fi ADB. "
        "Capabilities: check status/battery/storage, enable wireless ADB, list installed VR games & apps, "
        "launch apps/games, open URLs or WebXR in Quest Browser, download files to Quest /sdcard/Download/, "
        "sideload .apk files onto Quest, capture VR screenshots, type text/tap/volume/keyevents, "
        "set refresh rate (72/90/120Hz), and enable MiniMuse on-device Accessibility control."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "devices",
                    "connect",
                    "enable_wifi",
                    "list_apps",
                    "launch_app",
                    "open_url",
                    "download_file",
                    "install_apk",
                    "screenshot",
                    "input_text",
                    "tap",
                    "keyevent",
                    "volume",
                    "enable_accessibility",
                    "set_refresh_rate",
                ],
                "description": "VR headset operation to perform.",
            },
            "target": {
                "type": "string",
                "description": "Optional ADB device serial or ip:port (auto-selects connected Quest/VR headset if omitted).",
            },
            "host_port": {
                "type": "string",
                "description": "For action='connect': headset Wi-Fi IP:port (e.g. '192.168.1.50:5555').",
            },
            "package": {
                "type": "string",
                "description": "For action='launch_app': Android/Quest package name or search keyword (e.g. 'com.beatgames.beatsaber' or 'browser').",
            },
            "url": {
                "type": "string",
                "description": "For action='open_url', 'download_file', or 'install_apk': target HTTP(S) URL.",
            },
            "file_path": {
                "type": "string",
                "description": "For action='download_file' or 'install_apk': local/workspace path to push or install.",
            },
            "filename": {
                "type": "string",
                "description": "Optional destination filename inside Quest /sdcard/Download/.",
            },
            "text": {
                "type": "string",
                "description": "For action='input_text': text to type into the focused Quest input field.",
            },
            "x": {"type": "integer", "description": "X coordinate for action='tap'."},
            "y": {"type": "integer", "description": "Y coordinate for action='tap'."},
            "key": {
                "type": "string",
                "description": "For action='keyevent': Android keycode name or number (e.g. HOME, BACK, MEDIA_PLAY_PAUSE, VOLUME_UP, VOLUME_DOWN).",
            },
            "direction": {
                "type": "string",
                "enum": ["up", "down", "mute"],
                "description": "For action='volume': adjust headset volume.",
            },
            "hz": {
                "type": "integer",
                "enum": [72, 80, 90, 120],
                "description": "For action='set_refresh_rate': target Quest refresh rate in Hz.",
            },
        },
        "required": ["action"],
    }

    data_dir: Path = Path(".")
    workspace: Path = Path(".")

    def __init__(self, data_dir: Path, workspace: Path, **data: Any) -> None:
        super().__init__(data_dir=Path(data_dir), workspace=Path(workspace), **data)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "status").lower()
        safe_actions = {"status", "devices", "list_apps", "screenshot"}
        sensitive_actions = {"install_apk", "enable_accessibility"}
        risk = (
            RiskLevel.SAFE
            if action in safe_actions
            else RiskLevel.SENSITIVE
            if action in sensitive_actions
            else RiskLevel.MODERATE
        )
        return CallAssessment(
            risk=risk,
            summary=f"quest_control.{action}({args.get('package') or args.get('url') or args.get('target') or ''})",
            target=str(args.get("target") or args.get("package") or args.get("url") or "quest")[:80],
        )

    def _run_adb(self, adb: str, *args: str, timeout: int = 25) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                [adb, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            return proc.returncode, (proc.stdout or proc.stderr or "").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            return 1, str(exc)

    def _resolve_target(self, adb: str, requested: str = "") -> tuple[str | None, list[dict[str, Any]]]:
        devs = list_vr_adb_devices(adb)
        ready = [d for d in devs if d.get("state") == "device"]
        if requested:
            for d in ready:
                if d["id"] == requested or d.get("serial") == requested:
                    return d["id"], devs
            return requested, devs
        vr_devs = [d for d in ready if d.get("is_vr")]
        if vr_devs:
            return vr_devs[0]["id"], devs
        # Check saved vr_target.json
        saved_path = self.data_dir / "vr_target.json"
        if saved_path.is_file():
            with contextlib.suppress(OSError, ValueError):
                saved = json.loads(saved_path.read_text(encoding="utf-8"))
                tgt = str(saved.get("target") or "")
                ser = str(saved.get("serial") or "")
                for d in ready:
                    if d["id"] == tgt or (ser and d.get("serial") == ser):
                        return d["id"], devs
        return None, devs

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "status").strip().lower()
        target_req = str(kwargs.get("target") or "").strip()
        adb = find_adb_binary(self.data_dir)
        if not adb:
            return ToolResult.fail(
                "ADB is not installed on this device yet. Open Settings → VR Headset (Meta Quest) "
                "and click 'Install & Set Up on Meta Quest' to auto-install ADB and connect your headset."
            )

        if action == "connect":
            hp = str(kwargs.get("host_port") or target_req).strip()
            if not hp:
                return ToolResult.fail("Provide `host_port` (e.g. '192.168.1.50:5555') to connect wirelessly.")
            if ":" not in hp:
                hp = f"{hp}:5555"
            rc, out = await asyncio.to_thread(self._run_adb, adb, "connect", hp, timeout=15)
            return ToolResult.ok(out or f"Connected to {hp}") if rc == 0 else ToolResult.fail(out)

        if action == "devices":
            devs = await asyncio.to_thread(list_vr_adb_devices, adb)
            vr_only = [d for d in devs if d.get("is_vr")]
            return ToolResult.ok(
                json.dumps({"vr_headsets": vr_only, "all_adb_devices": devs}, ensure_ascii=False, indent=2)
            )

        dev_id, all_devs = await asyncio.to_thread(self._resolve_target, adb, target_req)
        if not dev_id:
            unauth = [d for d in all_devs if d.get("state") == "unauthorized"]
            if unauth:
                return ToolResult.fail(
                    "Your Meta Quest / VR headset is plugged in but waiting for permission. "
                    "Put on the headset and tap 'Always allow from this computer' on the USB Debugging prompt."
                )
            return ToolResult.fail(
                "No Meta Quest or VR headset detected via USB or Wi-Fi ADB. "
                "Enable Developer Mode in the Meta Horizon mobile app, plug in via USB (or connect Wi-Fi ADB), "
                "and allow USB debugging inside the headset."
            )

        if action == "status":
            def _status() -> dict[str, Any]:
                info = probe_vr_adb_device(adb, dev_id)
                _, batt = self._run_adb(adb, "-s", dev_id, "shell", "dumpsys", "battery", timeout=8)
                lvl_m = re.search(r"level:\s*(\d+)", batt)
                status_m = re.search(r"status:\s*(\d+)", batt)
                _, df_out = self._run_adb(adb, "-s", dev_id, "shell", "df", "-h", "/sdcard", timeout=8)
                _, ip_out = self._run_adb(adb, "-s", dev_id, "shell", "ip", "route", timeout=6)
                ip_m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_out)
                _, a11y = self._run_adb(
                    adb, "-s", dev_id, "shell", "settings", "get", "secure", "enabled_accessibility_services", timeout=6
                )
                return {
                    **info,
                    "battery_percent": int(lvl_m.group(1)) if lvl_m else None,
                    "charging": status_m.group(1) in ("2", "5") if status_m else None,
                    "wifi_ip": ip_m.group(1) if ip_m else "",
                    "storage": df_out.splitlines()[-1] if df_out else "",
                    "minimuse_accessibility_enabled": "io.github.nanomuse.app" in a11y,
                }

            return ToolResult.ok(json.dumps(await asyncio.to_thread(_status), ensure_ascii=False, indent=2))

        if action == "enable_wifi":
            def _wifi() -> str:
                _, ip_out = self._run_adb(adb, "-s", dev_id, "shell", "ip", "route", timeout=6)
                ip_m = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_out)
                rc, out = self._run_adb(adb, "-s", dev_id, "tcpip", "5555", timeout=10)
                if rc != 0:
                    return f"Failed to enable wireless ADB: {out}"
                if ip_m:
                    hp = f"{ip_m.group(1)}:5555"
                    _, cout = self._run_adb(adb, "connect", hp, timeout=12)
                    return f"Wireless ADB enabled on {hp} ({cout}). You can now unplug the USB cable!"
                return f"Wireless ADB port 5555 enabled ({out})."

            return ToolResult.ok(await asyncio.to_thread(_wifi))

        if action == "list_apps":
            rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "pm", "list", "packages", "-3", timeout=15)
            if rc != 0:
                return ToolResult.fail(out)
            pkgs = sorted(line.replace("package:", "").strip() for line in out.splitlines() if line.startswith("package:"))
            return ToolResult.ok(json.dumps({"headset": dev_id, "count": len(pkgs), "packages": pkgs}, indent=2))

        if action == "launch_app":
            pkg = str(kwargs.get("package") or "").strip()
            if not pkg:
                return ToolResult.fail("Provide `package` name or keyword to launch.")
            if "." not in pkg:
                # Friendly shortcut search among installed packages
                aliases = {
                    "browser": "com.oculus.browser",
                    "meta browser": "com.oculus.browser",
                    "quest browser": "com.oculus.browser",
                    "store": "com.oculus.store",
                    "minimuse": "io.github.nanomuse.app",
                    "youtube": "com.google.android.apps.youtube.vr.oculus",
                    "settings": "com.oculus.vrshell",
                }
                if pkg.lower() in aliases:
                    pkg = aliases[pkg.lower()]
                else:
                    _, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "pm", "list", "packages", timeout=12)
                    hit = next(
                        (
                            line.replace("package:", "").strip()
                            for line in out.splitlines()
                            if pkg.lower() in line.lower()
                        ),
                        "",
                    )
                    if hit:
                        pkg = hit
            rc, out = await asyncio.to_thread(
                self._run_adb,
                adb,
                "-s",
                dev_id,
                "shell",
                "monkey",
                "-p",
                pkg,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
                timeout=15,
            )
            return ToolResult.ok(f"Launched {pkg} on {dev_id}: {out}") if rc == 0 else ToolResult.fail(out)

        if action == "open_url":
            url = str(kwargs.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                return ToolResult.fail("Provide a valid http(s) URL.")
            rc, out = await asyncio.to_thread(
                self._run_adb,
                adb,
                "-s",
                dev_id,
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                url,
                timeout=15,
            )
            return ToolResult.ok(f"Opened {url} on Quest ({out})") if rc == 0 else ToolResult.fail(out)

        if action == "download_file":
            url = str(kwargs.get("url") or "").strip()
            local_fp = str(kwargs.get("file_path") or "").strip()
            fname = str(kwargs.get("filename") or "").strip()
            tmp_file: Path | None = None
            try:
                if url:
                    if not fname:
                        fname = re.sub(r"[^A-Za-z0-9._-]", "_", url.split("?", 1)[0].rstrip("/").split("/")[-1] or "download.bin")
                    tmp_file = Path(tempfile.gettempdir()) / f"quest_dl_{fname}"
                    async with httpx.AsyncClient(follow_redirects=True, timeout=180.0) as hc:
                        async with hc.stream("GET", url) as resp:
                            resp.raise_for_status()
                            with tmp_file.open("wb") as fh:
                                async for chunk in resp.aiter_bytes(131072):
                                    fh.write(chunk)
                    src_path = tmp_file
                elif local_fp:
                    src_path = Path(local_fp) if Path(local_fp).is_absolute() else (self.workspace / local_fp)
                    if not src_path.is_file():
                        return ToolResult.fail(f"File not found: {src_path}")
                    if not fname:
                        fname = src_path.name
                else:
                    return ToolResult.fail("Provide either `url` or `file_path` to download onto the Quest.")
                remote_dest = f"/sdcard/Download/{fname}"
                rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "push", str(src_path), remote_dest, timeout=180)
                if rc != 0:
                    return ToolResult.fail(f"Push to Quest failed: {out}")
                return ToolResult.ok(f"Downloaded and saved '{fname}' to Quest at {remote_dest} ({out}).")
            finally:
                if tmp_file is not None:
                    with contextlib.suppress(OSError):
                        tmp_file.unlink()

        if action == "install_apk":
            url = str(kwargs.get("url") or "").strip()
            local_fp = str(kwargs.get("file_path") or "").strip()
            tmp_apk: Path | None = None
            try:
                if url:
                    tmp_apk = Path(tempfile.gettempdir()) / "quest_sideload.apk"
                    async with httpx.AsyncClient(follow_redirects=True, timeout=240.0) as hc:
                        async with hc.stream("GET", url) as resp:
                            resp.raise_for_status()
                            with tmp_apk.open("wb") as fh:
                                async for chunk in resp.aiter_bytes(131072):
                                    fh.write(chunk)
                    apk_path = tmp_apk
                elif local_fp:
                    apk_path = Path(local_fp) if Path(local_fp).is_absolute() else (self.workspace / local_fp)
                    if not apk_path.is_file():
                        return ToolResult.fail(f"APK not found: {apk_path}")
                else:
                    return ToolResult.fail("Provide `url` or `file_path` of the .apk to sideload onto Quest.")
                rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "install", "-r", "-g", str(apk_path), timeout=240)
                if rc != 0:
                    return ToolResult.fail(f"APK install on Quest failed: {out}")
                return ToolResult.ok(f"Installed {apk_path.name} onto Quest ({dev_id}): {out}")
            finally:
                if tmp_apk is not None:
                    with contextlib.suppress(OSError):
                        tmp_apk.unlink()

        if action == "screenshot":
            dest = self.workspace / "quest-screenshot.png"
            def _cap() -> tuple[int, str]:
                try:
                    proc = subprocess.run(
                        [adb, "-s", dev_id, "exec-out", "screencap", "-p"],
                        capture_output=True,
                        timeout=15,
                        check=False,
                        **hidden_subprocess_kwargs(),
                    )
                    if proc.returncode == 0 and proc.stdout:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(proc.stdout)
                        return 0, str(dest)
                    return 1, (proc.stderr or b"").decode("utf-8", errors="replace")
                except Exception as exc:
                    return 1, str(exc)

            rc, info = await asyncio.to_thread(_cap)
            return ToolResult.ok(f"Saved Quest screenshot to {info}") if rc == 0 else ToolResult.fail(info)

        if action == "input_text":
            text = str(kwargs.get("text") or "")
            if not text:
                return ToolResult.fail("Provide `text` to type.")
            escaped = text.replace(" ", "%s").replace("&", "\\&").replace("'", "\\'")
            rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "input", "text", escaped, timeout=10)
            return ToolResult.ok("Typed text on Quest.") if rc == 0 else ToolResult.fail(out)

        if action == "tap":
            x, y = kwargs.get("x"), kwargs.get("y")
            if x is None or y is None:
                return ToolResult.fail("Provide integer `x` and `y` coordinates.")
            rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "input", "tap", str(int(x)), str(int(y)), timeout=10)
            return ToolResult.ok(f"Tapped ({x}, {y}) on Quest.") if rc == 0 else ToolResult.fail(out)

        if action == "keyevent":
            key = str(kwargs.get("key") or "").strip().upper()
            if not key:
                return ToolResult.fail("Provide `key` (e.g. HOME, BACK, MEDIA_PLAY_PAUSE).")
            code = key if key.startswith("KEYCODE_") or key.isdigit() else f"KEYCODE_{key}"
            rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "input", "keyevent", code, timeout=10)
            return ToolResult.ok(f"Sent keyevent {code} to Quest.") if rc == 0 else ToolResult.fail(out)

        if action == "volume":
            direction = str(kwargs.get("direction") or "up").lower()
            code = {"up": "KEYCODE_VOLUME_UP", "down": "KEYCODE_VOLUME_DOWN", "mute": "KEYCODE_VOLUME_MUTE"}.get(direction, "KEYCODE_VOLUME_UP")
            rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "input", "keyevent", code, timeout=10)
            return ToolResult.ok(f"Volume {direction} sent to Quest.") if rc == 0 else ToolResult.fail(out)

        if action == "enable_accessibility":
            svc_name = "io.github.nanomuse.app/io.github.nanomuse.app.gui.MuseAccessibilityService"
            def _enable() -> tuple[int, str]:
                _, existing = self._run_adb(adb, "-s", dev_id, "shell", "settings", "get", "secure", "enabled_accessibility_services", timeout=8)
                cur = "" if existing in ("null", "None", "") else existing.strip()
                parts = [p for p in cur.split(":") if p and p != "null"]
                if svc_name not in parts:
                    parts.append(svc_name)
                combined = ":".join(parts)
                rc1, out1 = self._run_adb(adb, "-s", dev_id, "shell", "settings", "put", "secure", "enabled_accessibility_services", combined, timeout=8)
                rc2, out2 = self._run_adb(adb, "-s", dev_id, "shell", "settings", "put", "secure", "accessibility_enabled", "1", timeout=8)
                return (0 if rc1 == 0 and rc2 == 0 else 1), f"{out1} {out2}".strip()

            rc, out = await asyncio.to_thread(_enable)
            return (
                ToolResult.ok("Enabled MiniMuse on-device Accessibility Control on your Quest! MiniMuse can now read and operate your Quest screen directly.")
                if rc == 0
                else ToolResult.fail(out)
            )

        if action == "set_refresh_rate":
            hz = int(kwargs.get("hz") or 90)
            rc, out = await asyncio.to_thread(self._run_adb, adb, "-s", dev_id, "shell", "setprop", "debug.oculus.refreshRate", str(hz), timeout=8)
            return ToolResult.ok(f"Set Quest refresh rate to {hz}Hz.") if rc == 0 else ToolResult.fail(out)

        return ToolResult.fail(f"Unknown action: {action}")


__all__ = ["QuestVRTool", "find_adb_binary", "is_vr_headset_text", "list_vr_adb_devices", "probe_vr_adb_device"]
