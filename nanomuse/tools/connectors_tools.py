"""Connectors, Phone Bridge (ADB/Tailscale/Termux), Health Connect, GroupMe, Last.fm & App Builder tools for nanoMuse."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any, ClassVar

import httpx

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment, int_arg
from nanomuse.tools.ofa_tools import DEFAULT_PERSONAL_DB, _now_iso, get_db
from nanomuse.win_proc import hidden_subprocess_kwargs


class PhoneBridgeTool(BaseTool):
    name: str = "phone_bridge"
    description: str = (
        "ADB bridge to a connected Android phone or Wear OS watch. Look up unknown numbers in "
        "`contacts`/`deep_memory` before asking the user; check real SMS here before characterizing a thread. "
        "Actions: status, read_sms (query/phone filter), send_sms (phone, text), call_logs, "
        "call_transcripts, notifications, set_alarm (hour, minute, message), phone_settings "
        "(brightness/volume/dnd/wifi/bluetooth), adb_shell (diagnostic)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "read_sms",
                    "send_sms",
                    "call_logs",
                    "call_transcripts",
                    "notifications",
                    "set_alarm",
                    "phone_settings",
                    "adb_shell",
                ],
            },
            "query": {"type": "string", "description": "Search filter for SMS, call logs, or transcripts"},
            "phone": {"type": "string", "description": "Phone number (e.g. +15550101234)"},
            "text": {"type": "string", "description": "SMS body or alarm label"},
            "hour": {"type": "integer", "description": "Alarm hour (0-23)"},
            "minute": {"type": "integer", "description": "Alarm minute (0-59)"},
            "setting": {"type": "string", "description": "brightness | volume | dnd | battery"},
            "value": {"type": "string"},
            "command": {"type": "string", "description": "ADB shell command"},
            "limit": {"type": "integer", "description": "Max items (default 20)"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.MODERATE
    reads_private_data: bool = True
    db_path: Path = DEFAULT_PERSONAL_DB
    workspace: Path = Path.home() / ".nanomuse" / "workspace"

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        act = str(args.get("action") or "")
        risk = RiskLevel.SENSITIVE if act in ("send_sms", "adb_shell") else RiskLevel.SAFE
        return CallAssessment(
            risk=risk,
            reads_private_data=True,
            egress=(act == "send_sms"),
            # a send approval is bound to its recipient: approving one text never
            # covers texting anyone else
            egress_target=str(args.get("phone") or "") if act == "send_sms" else None,
            # texting is reaching a person: the Sentinel asks every single time
            reaches_people=(act == "send_sms"),
            summary=f"phone_bridge.{act}({args.get('phone') or args.get('query') or args.get('setting') or ''})",
        )

    async def _adb(self, *args: str, timeout: float = 15.0) -> tuple[int, str, str]:
        adb_bin = shutil.which("adb")
        if not adb_bin:
            # Check common Windows/macOS Android SDK locations
            for candidate in [
                self.db_path.parent / "platform-tools" / "platform-tools" / "adb.exe",
                self.db_path.parent / "platform-tools" / "adb.exe",
                Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe",
                Path(r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe"),
            ]:
                if candidate.exists():
                    adb_bin = str(candidate)
                    break
        if not adb_bin:
            return 127, "", "ADB binary not found on PATH. Pair your Android phone via the MiniMuse Android app or install Android platform-tools."

        cmd_args = list(args)
        # If targeting a device command and -s not provided, auto-select active online device
        if cmd_args and cmd_args[0] not in ("devices", "disconnect", "connect", "kill-server", "start-server"):
            if "-s" not in cmd_args:
                try:
                    dev_proc = await asyncio.create_subprocess_exec(
                        adb_bin, "devices", "-l",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        **hidden_subprocess_kwargs(),
                    )
                    dev_out, _ = await asyncio.wait_for(dev_proc.communicate(), timeout=3.0)
                    for line in dev_out.decode("utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if not line or line.startswith("List of devices"):
                            continue
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] == "device":
                            cmd_args = ["-s", parts[0]] + cmd_args
                            break
                except Exception:
                    pass

        try:
            proc = await asyncio.create_subprocess_exec(
                adb_bin,
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **hidden_subprocess_kwargs(),
            )
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode or 0, out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace")
        except Exception as exc:
            return 1, "", str(exc)

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        limit = int_arg(kwargs.get("limit"), 20, 1, 100)

        if action == "status":
            code, out, err = await self._adb("devices", "-l")
            conn = get_db(self.db_path)
            try:
                sd = conn.execute("SELECT * FROM synced_daily ORDER BY date DESC LIMIT 3").fetchall()
            finally:
                conn.close()
            return ToolResult(
                output=json.dumps(
                    {
                        "adb_code": code,
                        "adb_devices": out.strip() or err.strip(),
                        "recent_health_connect_days": [dict(r) for r in sd],
                        "health_connect_webhook": "POST http://<PC-IP>:8787/api/ofa/health-connect",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        if action == "read_sms":
            q = str(kwargs.get("query") or kwargs.get("phone") or "").strip().lower()
            code, out, err = await self._adb(
                "shell",
                f"content query --uri content://sms --projection _id:address:date:type:body --sort 'date DESC' | head -n {max(limit * 3, 50)}",
            )
            if code != 0 or not out.strip():
                return ToolResult(output=f"ADB SMS query unavailable ({err.strip() or out.strip()}). Ensure your phone is connected via USB/Wireless ADB or use the MiniMuse Android app.")
            lines = out.splitlines()
            filtered = [ln for ln in lines if not q or q in ln.lower()][:limit]
            return ToolResult(output="\n".join(filtered) if filtered else "No matching SMS messages.")

        if action == "send_sms":
            phone = str(kwargs.get("phone") or "").strip()
            text = str(kwargs.get("text") or "").strip()
            if not phone or not text:
                return ToolResult.fail("Provide `phone` and `text`.")
            # Call the default SMS app's own send service with the body, and check the
            # outbox to confirm it went; fall back to composer only when no app exposes one.
            sent = await self._send_sms_directly(phone, text)
            if sent is not None:
                return sent
            code, out, err = await self._adb(
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.SENDTO",
                "-d",
                f"smsto:{phone}",
                "--es",
                "sms_body",
                text,
            )
            if code != 0:
                return ToolResult.fail(f"Failed to stage SMS on phone: {err or out}")
            return ToolResult(
                output=(
                    f"No SMS app on the phone exposes a send service, so the "
                    f"composer was opened to {phone} with the message staged: {text!r}. "
                    "It has not been sent yet — press send on the phone."
                )
            )

        if action == "call_logs":
            q = str(kwargs.get("query") or kwargs.get("phone") or "").strip().lower()
            code, out, err = await self._adb(
                "shell",
                "content query --uri content://call_log/calls --projection number:date:duration:type:name --sort 'date DESC'",
            )
            if code != 0 or not out.strip():
                return ToolResult(output=f"ADB call log query unavailable ({err.strip() or out.strip()}).")
            lines = [ln for ln in out.splitlines() if not q or q in ln.lower()][:limit]
            return ToolResult(output="\n".join(lines) if lines else "No matching call logs.")

        if action == "call_transcripts":
            q = str(kwargs.get("query") or "").strip().lower()
            t_dir = self.workspace / "call_transcripts"
            t_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(t_dir.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True)
            # Also search migrated voice-call transcripts
            mig_dir = self.db_path.parent / "muse_transcripts"
            if mig_dir.exists():
                files.extend(p for p in mig_dir.rglob("*call*transcript*.md") if p.is_file())
            results = []
            for f in files:
                if not f.is_file():
                    continue
                txt = f.read_text("utf-8", errors="replace")
                if not q or q in f.name.lower() or q in txt.lower():
                    results.append({"file": str(f), "preview": txt[:1500]})
                if len(results) >= limit:
                    break
            return ToolResult(output=json.dumps(results, ensure_ascii=False, indent=2))

        if action == "notifications":
            code, out, err = await self._adb("shell", "dumpsys notification --noredact")
            if code != 0:
                return ToolResult.fail(f"ADB notifications check failed: {err or out}")
            titles = re.findall(r"android\.(?:title|text)=String\s*\(([^)]+)\)", out)
            return ToolResult(output="\n".join(titles[:limit]) if titles else out[:3000])

        if action == "set_alarm":
            h = int_arg(kwargs.get("hour"), 7, 0, 23)
            m = int_arg(kwargs.get("minute"), 30, 0, 59)
            msg = str(kwargs.get("text") or "nanoMuse Alarm").strip()
            code, out, err = await self._adb(
                "shell",
                "am",
                "start",
                "-a",
                "android.intent.action.SET_ALARM",
                "--ei",
                "android.intent.extra.alarm.HOUR",
                str(h),
                "--ei",
                "android.intent.extra.alarm.MINUTES",
                str(m),
                "--es",
                "android.intent.extra.alarm.MESSAGE",
                msg,
                "--ez",
                "android.intent.extra.alarm.SKIP_UI",
                "true",
            )
            if code != 0:
                return ToolResult.fail(f"Could not set alarm via ADB: {err or out}")
            return ToolResult(output=f"Set alarm on Android phone for {h:02d}:{m:02d} ({msg}).")

        if action == "phone_settings":
            setting = str(kwargs.get("setting") or "battery").strip().lower()
            val = str(kwargs.get("value") or "").strip()
            if setting == "battery":
                _, out, err = await self._adb("shell", "dumpsys battery")
                return ToolResult(output=out or err)
            if setting == "brightness" and val:
                _, out, err = await self._adb("shell", "settings", "put", "system", "screen_brightness", val)
                return ToolResult(output=f"Set brightness to {val}. {out or err}")
            if setting == "dnd" and val:
                _, out, err = await self._adb("shell", "cmd", "notification", "set_dnd", val)
                return ToolResult(output=f"Set DND to {val}. {out or err}")
            return ToolResult.fail("Supported settings: battery, brightness (0-255), dnd (on|off).")

        if action == "adb_shell":
            cmd = str(kwargs.get("command") or "").strip()
            if not cmd:
                return ToolResult.fail("Provide `command`.")
            code, out, err = await self._adb("shell", cmd)
            return ToolResult(output=f"exit={code}\n{out}\n{err}".strip())

        return ToolResult.fail(f"Unknown action: {action}")

    # Sending a text from this PC. Android gives no shell-level "send an SMS" command: the
    # only service that sends without a human tapping send (the SMS app's
    # RESPOND_VIA_MESSAGE) is guarded by SEND_RESPOND_VIA_MESSAGE, which the ADB shell does
    # not hold on this phone (verified 2026-11-11: "Requires permission"). So the message
    # is typed into the SMS app's own composer with the keyboard, and the send button is
    # pressed — the phone sends it, and the sent box is read back to prove it left.
    SMS_SEND_BUTTONS: ClassVar[tuple[str, ...]] = ("Send", "SEND", "Send message", "Send SMS")

    async def _send_sms_directly(self, phone: str, text: str) -> ToolResult | None:
        """Send the message through the phone's own SMS app, then prove it left.

        Returns a ToolResult when the message went (or when the attempt is worth
        reporting), or None when the app could not be driven — the caller then falls
        back to opening the composer with the text staged.
        """
        # 1) open the conversation with this number
        code, out, err = await self._adb(
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.SENDTO",
            "-d",
            f"smsto:{phone}",
            "--es",
            "sms_body",
            text,
        )
        if code != 0:
            return None
        await asyncio.sleep(2.0)
        # 2) find the message field and the send button in the view tree
        code, out, _ = await self._adb("shell", "uiautomator dump /sdcard/nm_ui.xml >/dev/null 2>&1; cat /sdcard/nm_ui.xml")
        xml = out or ""
        if not xml.strip():
            return None
        field = self._ui_node(xml, ("Message text", "Enter message", "Text message", "Message"))
        send = self._ui_node(xml, self.SMS_SEND_BUTTONS)
        if field is None or send is None:
            return None
        await self._adb("shell", "input", "tap", str(field[0]), str(field[1]))
        await asyncio.sleep(0.6)
        # the body is typed (spaces as %s, the way `input text` wants them), then the
        # send button is pressed
        escaped = text.replace(" ", "%s")
        await self._adb("shell", "input", "text", escaped)
        await asyncio.sleep(0.8)
        await self._adb("shell", "input", "tap", str(send[0]), str(send[1]))
        # 3) proof: a row for this number with this text in the sent box
        needle = re.sub(r"\D", "", phone)[-10:]
        for _ in range(8):
            await asyncio.sleep(1.0)
            code, out, _ = await self._adb(
                "shell",
                "content query --uri content://sms/sent --projection address:body:date "
                "--sort 'date DESC' | head -n 3",
            )
            rows = out or ""
            if needle and needle in re.sub(r"\D", "", rows) and text[:20].lower() in rows.lower():
                return ToolResult(
                    output=f"Sent the SMS to {phone} from your Android phone: {text!r}"
                )
        return ToolResult(
            output=(
                f"Typed the SMS to {phone} into the messaging app and pressed send ({text!r}); "
                "the sent box has not confirmed it yet — worth a glance at the phone."
            )
        )

    @staticmethod
    def _ui_node(xml: str, labels: tuple[str, ...]) -> tuple[int, int] | None:
        """The centre of the first node whose text or description matches a label."""
        for label in labels:
            for m in re.finditer(r"<node[^>]*>", xml):
                node = m.group(0)
                if f'text="{label}"' not in node and f'content-desc="{label}"' not in node:
                    continue
                b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
                if b:
                    x1, y1, x2, y2 = map(int, b.groups())
                    return ((x1 + x2) // 2, (y1 + y2) // 2)
        return None


class ExternalConnectorsTool(BaseTool):
    name: str = "external_connectors"
    description: str = (
        "Live API connectors & Custom API Integrations for external services: "
        "1) Custom APIs & Webhooks (`custom_api`): call third-party REST APIs or webhooks with GET/POST/PUT/DELETE/PATCH, custom headers, JSON body, and vault substitution (`{{vault:KEY}}`). "
        "2) Credential Vault Management (`save_credential`, `list_credentials`, `delete_credential`): store and manage encrypted API keys and bearer tokens in the local vault. "
        "3) Last.fm (`lastfm_recent`, `lastfm_top`): pull listening history and top tracks using `LASTFM_API_KEY` and `LASTFM_USER` from the vault. "
        "4) GroupMe (read-only: `groupme_groups`, `groupme_messages`): list groups and read recent messages using `GROUPME_TOKEN`."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "service": {
                "type": "string",
                "enum": [
                    "custom_api",
                    "save_credential",
                    "list_credentials",
                    "delete_credential",
                    "lastfm_recent",
                    "lastfm_top",
                    "groupme_groups",
                    "groupme_messages",
                ],
            },
            "query": {"type": "string", "description": "Search query, GroupMe group ID, Last.fm period (7day|1month|overall), or Last.fm username"},
            "url": {"type": "string", "description": "Target endpoint URL for custom_api (supports {{vault:KEY}} placeholders)"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"], "description": "HTTP method for custom_api (default GET)"},
            "headers": {"type": "object", "description": "Headers dict for custom_api (e.g. {'Authorization': 'Bearer {{vault:MY_TOKEN}}'})"},
            "body": {"type": "object", "description": "JSON body dict or string for custom_api POST/PUT"},
            "params": {"type": "object", "description": "Query parameters dict for custom_api"},
            "key_name": {"type": "string", "description": "Name of vault credential (e.g. SPOTIFY_API_KEY, NOTION_TOKEN)"},
            "key_value": {"type": "string", "description": "Secret value to encrypt in the vault (only for save_credential)"},
            "limit": {"type": "integer", "description": "Number of items (default 15)"},
        },
        "required": ["service"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    egress: bool = True
    accepts_secrets: bool = True
    vault: Any = None
    db_path: Path = DEFAULT_PERSONAL_DB
    data_dir: Path = DEFAULT_PERSONAL_DB.parent
    workspace: Path = DEFAULT_PERSONAL_DB.parent / "workspace"

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        svc = str(args.get("service") or "")
        risk = RiskLevel.SAFE
        if svc in ("save_credential", "delete_credential"):
            risk = RiskLevel.MODERATE
        elif svc == "custom_api":
            method = str(args.get("method") or "GET").upper()
            risk = RiskLevel.SAFE if method == "GET" else RiskLevel.MODERATE
        return CallAssessment(
            risk=risk,
            reads_private_data=True,
            egress=svc.startswith(("lastfm", "groupme")) or svc == "custom_api",
            egress_configured=True,
            summary=f"external_connectors.{svc}({args.get('query') or args.get('url') or args.get('key_name') or ''})",
        )

    def _secret(self, name: str) -> str:
        import os

        if self.vault is not None:
            val = self.vault.get(name)
            if val:
                return val
        return os.environ.get(name, "")

    async def execute(self, **kwargs: Any) -> ToolResult:
        svc = str(kwargs.get("service") or "").strip()
        q = str(kwargs.get("query") or "").strip()
        limit = int_arg(kwargs.get("limit"), 15, 1, 50)

        if svc == "save_credential":
            key_name = str(kwargs.get("key_name") or q).strip().upper()
            key_value = str(kwargs.get("key_value") or "").strip()
            if not key_name or not key_value:
                return ToolResult.fail("Both `key_name` and `key_value` are required to save a credential.")
            if self.vault is None:
                return ToolResult.fail("Credential vault is not configured on this instance.")
            self.vault.set(key_name, key_value)
            return ToolResult(
                output=f"Successfully encrypted and saved credential '{key_name}' in nanoMuse vault. You can now use it in custom_api or connectors via '{{{{vault:{key_name}}}}}'."
            )

        if svc == "list_credentials":
            if self.vault is None:
                return ToolResult(output="Vault not configured.")
            names = sorted(list(self.vault.names()))
            return ToolResult(
                output=json.dumps({"vault_credentials": names, "count": len(names)}, indent=2)
            )

        if svc == "delete_credential":
            key_name = str(kwargs.get("key_name") or q).strip().upper()
            if not key_name:
                return ToolResult.fail("Provide `key_name` to delete.")
            if self.vault is None:
                return ToolResult.fail("Vault not configured.")
            self.vault.delete(key_name)
            return ToolResult(output=f"Credential '{key_name}' deleted from vault.")

        if svc == "custom_api":
            url = str(kwargs.get("url") or "").strip()
            if not url:
                return ToolResult.fail("A valid `url` is required for custom_api.")
            method = str(kwargs.get("method") or "GET").upper()
            raw_headers = kwargs.get("headers") or {}
            if isinstance(raw_headers, str):
                try:
                    raw_headers = json.loads(raw_headers)
                except Exception:
                    raw_headers = {}
            raw_body = kwargs.get("body")
            raw_params = kwargs.get("params") or {}
            if isinstance(raw_params, str):
                try:
                    raw_params = json.loads(raw_params)
                except Exception:
                    raw_params = {}

            resolved_headers: dict[str, str] = {}
            for k, v in raw_headers.items():
                if isinstance(v, str) and self.vault is not None:
                    resolved_headers[str(k)] = self.vault.resolve(v)
                else:
                    resolved_headers[str(k)] = str(v)

            resolved_url = self.vault.resolve(url) if self.vault else url

            content_body = None
            json_body = None
            if raw_body is not None:
                if isinstance(raw_body, dict):
                    if self.vault is not None:
                        body_str = json.dumps(raw_body)
                        resolved_body_str = self.vault.resolve(body_str)
                        json_body = json.loads(resolved_body_str)
                    else:
                        json_body = raw_body
                elif isinstance(raw_body, str):
                    resolved_str = self.vault.resolve(raw_body) if self.vault else raw_body
                    try:
                        json_body = json.loads(resolved_str)
                    except Exception:
                        content_body = resolved_str

            try:
                async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                    r = await client.request(
                        method=method,
                        url=resolved_url,
                        headers=resolved_headers,
                        params=raw_params,
                        json=json_body,
                        content=content_body,
                    )
                    output_text = r.text
                    if self.vault is not None:
                        output_text = self.vault.redact(output_text)
                    return ToolResult(
                        output=json.dumps(
                            {
                                "status_code": r.status_code,
                                "headers": dict(r.headers),
                                "body": output_text[:8000],
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
            except Exception as e:
                return ToolResult.fail(f"HTTP request failed: {e}")

        if svc in ("lastfm_recent", "lastfm_top"):
            api_key = self._secret("LASTFM_API_KEY")
            user = (self._secret("LASTFM_USER") or "").strip()
            if not api_key or not user:
                return ToolResult(
                    output=(
                        "Last.fm credentials not yet stored in vault (`LASTFM_API_KEY` and `LASTFM_USER`). "
                        "Store them via Settings -> Connections or `save_credential` to pull live listening history."
                    )
                )
            method = "user.getrecenttracks" if svc == "lastfm_recent" else "user.gettoptracks"
            params = {
                "method": method,
                "user": user,
                "api_key": api_key,
                "format": "json",
                "limit": str(limit),
            }
            if svc == "lastfm_top":
                params["period"] = q if q in ("7day", "1month", "3month", "6month", "12month", "overall") else "7day"
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get("https://ws.audioscrobbler.com/2.0/", params=params)
                return ToolResult(output=r.text[:6000])

        if svc in ("groupme_groups", "groupme_messages"):
            token = self._secret("GROUPME_TOKEN")
            if not token:
                # Fallback to reading group summaries from muse_memory/groups/
                gdir = self.data_dir / "muse_memory" / "groups"
                summaries = []
                if gdir.exists():
                    for p in sorted(gdir.glob("*.md")):
                        summaries.append(f"=== {p.name} ===\n{p.read_text('utf-8', errors='replace')[:800]}")
                return ToolResult(
                    output=(
                        "GroupMe live token (`GROUPME_TOKEN`) not yet stored in vault; showing local GroupMe circle dossiers (read-only rule enforced):\n\n"
                        + "\n\n".join(summaries)
                    )
                )
            async with httpx.AsyncClient(timeout=15.0) as client:
                if svc == "groupme_groups":
                    r = await client.get("https://api.groupme.com/v3/groups", params={"token": token, "per_page": 20})
                    data = r.json().get("response", [])
                    out = [{"id": g.get("id"), "name": g.get("name"), "updated_at": g.get("updated_at")} for g in data]
                    return ToolResult(output=json.dumps(out, ensure_ascii=False, indent=2))
                else:
                    if not q:
                        return ToolResult.fail("Provide GroupMe group ID in `query`.")
                    r = await client.get(f"https://api.groupme.com/v3/groups/{q}/messages", params={"token": token, "limit": limit})
                    msgs = r.json().get("response", {}).get("messages", [])
                    out = [{"name": m.get("name"), "text": m.get("text"), "created_at": m.get("created_at")} for m in msgs]
                    return ToolResult(output=json.dumps(out, ensure_ascii=False, indent=2))

        return ToolResult.fail(f"Unknown service: {svc}")


class AppBuilderTool(BaseTool):
    name: str = "app_builder"
    description: str = (
        "Create, update, or list custom interactive web apps hosted directly inside MiniMuse (`/apps/<slug>`) "
        "and connected to the local SQLite database (`/api/ofa/*`). "
        "Every app created or updated here is ALSO written into the workspace so it appears as an interactive card in chat."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "create_or_update", "get"]},
            "slug": {"type": "string", "description": "URL-safe app slug, e.g. 'one-for-all', 'habit-tracker', 'meal-planner'"},
            "title": {"type": "string", "description": "Display title of the app"},
            "description": {"type": "string", "description": "Short description"},
            "html": {"type": "string", "description": "Complete self-contained HTML5 document (inline CSS + JS)"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    db_path: Path = DEFAULT_PERSONAL_DB
    workspace: Path = DEFAULT_PERSONAL_DB.parent / "workspace"

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        act = str(args.get("action") or "")
        slug = str(args.get("slug") or "")
        return CallAssessment(
            risk=RiskLevel.SAFE,
            summary=f"app_builder.{act}({slug})",
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        conn = get_db(self.db_path)
        try:
            if action == "list":
                rows = conn.execute("SELECT * FROM custom_apps ORDER BY updated_at DESC").fetchall()
                return ToolResult(output=json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
            if action == "get":
                slug = str(kwargs.get("slug") or "").strip().lower()
                row = conn.execute("SELECT * FROM custom_apps WHERE slug = ?", (slug,)).fetchone()
                if not row:
                    return ToolResult.fail(f"No app found with slug {slug!r}")
                p = self.workspace / row["html_path"]
                html = p.read_text("utf-8", errors="replace") if p.exists() else ""
                return ToolResult(output=json.dumps({**dict(row), "html_length": len(html), "html_preview": html[:2000]}, ensure_ascii=False, indent=2))
            if action == "create_or_update":
                slug = re.sub(r"[^a-z0-9-]+", "-", str(kwargs.get("slug") or "").strip().lower()).strip("-")
                title = str(kwargs.get("title") or slug).strip()
                desc = str(kwargs.get("description") or "").strip()
                html = str(kwargs.get("html") or "").strip()
                if not slug or not html:
                    return ToolResult.fail("Provide `slug` and complete `html`.")
                rel_path = f"{slug}.html"
                full_path = self.workspace / rel_path
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(html, encoding="utf-8")
                now = _now_iso()
                conn.execute(
                    """
                    INSERT INTO custom_apps (slug, title, description, html_path, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        html_path = excluded.html_path,
                        updated_at = excluded.updated_at
                    """,
                    (slug, title, desc, rel_path, now, now),
                )
                conn.commit()
                return ToolResult(output=f"App '{title}' saved to {full_path} and live at /apps/{slug}")
            return ToolResult.fail(f"Unknown action: {action}")
        finally:
            conn.close()
