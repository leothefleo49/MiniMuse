"""PC hardware control: master volume/mute, per-app audio (who is making sound, mute one
app or tab), bluetooth, and opening files/URLs. The PowerShell backend is in
``scripts/pc_tools.ps1``. Risk is MODERATE+ so the Sentinel asks first — volume agent
driven by default, never silent."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment
from nanomuse.win_proc import hidden_subprocess_kwargs

BACKEND = Path(__file__).resolve().parents[2] / "scripts" / "pc_tools.ps1"


def powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


class PCControl(BaseTool):
    name: str = "pc_control"
    description: str = (
        "Control the PC's hardware and per-app sound. Actions: "
        "`volume` (get / set 0-100 / mute / unmute / toggle), "
        "`sessions` (list apps making sound now, with app name and volume), "
        "`session` (mute|unmute|set <0-100> for one app — match by process name like 'chrome' or the window title), "
        "`bluetooth` (status / on / off), "
        "`play` (open a file or URL). Volume and muting changes ask the user first."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["volume", "sessions", "session", "bluetooth", "play"],
            },
            "sub": {
                "type": "string",
                "description": "For volume: get|set|mute|unmute|toggle. For session: mute|unmute|set. For bluetooth: status|on|off.",
            },
            "app": {"type": "string", "description": "Process/app name or window title to match (session action)."},
            "level": {"type": "integer", "description": "Volume percent 0-100 (volume set / session set)."},
            "target": {"type": "string", "description": "File path or URL to open (play action)."},
            "step": {"type": "string", "description": "Short phrase in English saying what this call does."},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.MODERATE

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "")
        sub = str(args.get("sub") or "")
        if action == "play" and str(args.get("target") or "").startswith(("http://", "https://")):
            risk = RiskLevel.MODERATE
        elif action == "sessions" or (action == "volume" and sub == "get") or (
            action == "bluetooth" and sub == "status"
        ):
            risk = RiskLevel.SAFE
        else:
            risk = RiskLevel.MODERATE
        summary = f"pc_control: {action}" + (f" {sub}" if sub else "")
        if action == "session":
            summary += f" {args.get('app', '')}"
        if action == "play":
            summary += f" {str(args.get('target', ''))[:60]}"
        return CallAssessment(risk=risk, summary=summary)

    async def execute(
        self,
        action: str = "",
        sub: str = "",
        app: str | None = None,
        level: Any = None,
        target: str | None = None,
        **_: Any,
    ) -> ToolResult:
        ps = powershell()
        if ps is None:
            return ToolResult.fail("no PowerShell available for pc_control")
        if not BACKEND.is_file():
            return ToolResult.fail(f"pc_control backend missing: {BACKEND}")
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(BACKEND)]
        if action == "volume":
            sub = (sub or "").lower()
            if sub == "set":
                pct = _clamp_level(level)
                if pct is None:
                    return ToolResult.fail("level must be 0-100")
                cmd += ["volume", "set", str(pct)]
            elif sub in ("get", "mute", "unmute", "toggle"):
                cmd += ["volume", sub]
            else:
                return ToolResult.fail("volume sub: get|set|mute|unmute|toggle")
        elif action == "sessions":
            cmd += ["sessions"]
        elif action == "session":
            if not app:
                return ToolResult.fail("app is required")
            sub = (sub or "").lower()
            if sub in ("mute", "unmute"):
                cmd += ["session", app, sub]
            elif sub == "set":
                pct = _clamp_level(level)
                if pct is None:
                    return ToolResult.fail("level must be 0-100")
                cmd += ["session", app, "set", str(pct)]
            else:
                return ToolResult.fail("session sub: mute|unmute|set")
        elif action == "bluetooth":
            sub = (sub or "").lower()
            if sub not in ("status", "on", "off"):
                return ToolResult.fail("bluetooth sub: status|on|off")
            cmd += ["bluetooth", sub]
        elif action == "play":
            if not target:
                return ToolResult.fail("target is required")
            cmd += ["play", target]
        else:
            return ToolResult.fail(f"unknown action '{action}'")

        try:
            proc = await asyncio.wait_for(
                asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env={**os.environ},
                    **hidden_subprocess_kwargs(),
                ),
                timeout=70,
            )
        except (TimeoutError, subprocess.TimeoutExpired):
            return ToolResult.fail("pc_control timed out")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"pc_control failed: {type(exc).__name__}: {str(exc)[:200]}")
        out = (proc.stdout or "").strip()
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            tail = err[-1][:200] if err else ""
            return ToolResult.fail(f"pc_control exit {proc.returncode}: {tail}")
        try:
            data = json.loads(out) if out else {}
        except json.JSONDecodeError:
            data = {"output": out}
        if isinstance(data, dict) and "error" in data:
            return ToolResult.fail(str(data["error"]))
        return ToolResult(output=json.dumps(data, ensure_ascii=False, indent=2))


def _clamp_level(level: Any) -> int | None:
    try:
        pct = int(level)
    except (TypeError, ValueError):
        return None
    return pct if 0 <= pct <= 100 else None


__all__ = ["PCControl"]
