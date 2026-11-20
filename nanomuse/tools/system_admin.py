"""Chat-side administration of the AI lineup: any model can answer "what AIs do I have",
add a new one with its API key, make another one the default (an approval card in ask
mode, a done deal in auto), set usage limits and warnings, and restart the server after
a build.

Everything here goes through the same stores the web console uses (app-settings.json,
the vault, the endpoint list), so a change made in chat shows up in the UI and survives
a restart.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment


class SystemAdminTool(BaseTool):
    name: str = "system_admin"
    description: str = (
        "Administer this MiniMuse itself: inspect or update ANY MiniMuse app setting when "
        "explicitly requested by the user (`get_settings`, `update_settings`), control the "
        "Personal Cloud Free Tunnel (`personal_cloud`), list configured AIs (`list`), add a "
        "new AI endpoint with its API key (`add_model`), switch the default AI (`set_default_model`), "
        "manage usage limits (`list_usage_limits`, `set_usage_limit`, `remove_usage_limit`), "
        "and restart the server (`restart_server`). Never change settings unless the user "
        "explicitly asked."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "get_settings",
                    "update_settings",
                    "personal_cloud",
                    "add_model",
                    "set_default_model",
                    "list_usage_limits",
                    "set_usage_limit",
                    "remove_usage_limit",
                    "restart_server",
                ],
            },
            "settings": {
                "type": "object",
                "description": (
                    "update_settings: dict of MiniMuse settings to update immediately. Supported keys: "
                    "profile ({name, tagline, avatar, emoji, color, avatar_bg_mode ['theme'|'default'|'color'|'image'], "
                    "avatar_bg_color, avatar_bg_image, avatar_border_mode ['theme'|'color'|'none'], "
                    "avatar_border_color, avatar_border_glow [0-100], avatar_animated [bool], "
                    "tone [''|'formal'|'casual'|'playful'|'concise'], communication [''|'short'|'detailed'|'bullets'], "
                    "style, user_name, proactivity ['off'|'low'|'default'|'high'], goal_interval_minutes, quiet_hours}), "
                    "ui ({theme ['light'|'dark'], bg, accent, bubble, scale, show_calendar ['true'|'false'], "
                    "show_location ['true'|'false'], map_app}), "
                    "sentinel_mode ('strict'|'ask'|'task'|'auto'), permissions ({device: {cap: 'always'|'ask'|'never'}}), "
                    "show_thinking (bool), allow_self_improve (bool), language (str, e.g. 'auto'|'English'|'中文'), "
                    "model_choice (str), background_model (str), cloud_mode ('local'|'hybrid'|'cloud'), "
                    "cloud_slots ({chat, media, sync}), cloud_prompts (bool), auto_update (bool), "
                    "trash_retention_days (int), max_steps (int), auto_continue (bool), max_continues (int), "
                    "max_context_messages (int), max_context_tokens (int), max_context_images (int), "
                    "sync_enabled (bool), sync_side_chats (bool), personal_cloud_auto_tunnel (bool)."
                ),
            },
            "tunnel_action": {
                "type": "string",
                "enum": ["status", "start", "stop", "auto_on", "auto_off"],
                "description": "personal_cloud: check status, start/stop Free Cloudflare Quick Tunnel, or toggle auto-start.",
            },
            "label": {"type": "string", "description": "Display name for add_model"},
            "base_url": {
                "type": "string",
                "description": "add_model: OpenAI-compatible base URL (e.g. https://api.x.ai/v1)",
            },
            "api_key": {
                "type": "string",
                "description": "add_model: the key (stored in the vault, never logged)",
            },
            "model": {
                "type": "string",
                "description": "set_default_model: the model id; add_model: a model id to pin on the endpoint",
            },
            "endpoint_id": {
                "type": "string",
                "description": "set_default_model: an endpoint id from `list`",
            },
            "name": {"type": "string", "description": "set_usage_limit: display name"},
            "scope": {
                "type": "string",
                "enum": ["global", "model"],
                "description": "set_usage_limit: one AI or all of them together",
            },
            "limit_model": {
                "type": "string",
                "description": "set_usage_limit: the model id when scope=model",
            },
            "unit": {
                "type": "string",
                "enum": ["cost", "tokens", "requests"],
                "description": "set_usage_limit: what is counted",
            },
            "window": {
                "type": "string",
                "enum": ["day", "week", "month"],
                "description": "set_usage_limit: reset window",
            },
            "amount": {"type": "number", "description": "set_usage_limit: the cap itself"},
            "warning_pct": {
                "type": "number",
                "description": "set_usage_limit: warn at this percent (default 80)",
            },
            "on_full": {
                "type": "string",
                "enum": ["warn", "stop", "confirm"],
                "description": "set_usage_limit: what happens at 100% (default stop; confirm asks once per turn)",
            },
            "limit_id": {
                "type": "string",
                "description": "remove_usage_limit: the id from list_usage_limits",
            },
        },
        "required": ["action"],
    }
    # switching the brain or restarting the server is the person's call in ask mode;
    # auto mode runs without prompting in hands-off configurations
    risk: RiskLevel = RiskLevel.SENSITIVE

    # wired by MuseService after the app is built (the tool list exists first)
    svc: Any = None

    def attach(self, svc: Any) -> None:
        self.svc = svc

    def assess(self, arguments: dict[str, Any]) -> CallAssessment:
        act = str(arguments.get("action") or "list").strip()
        if act in ("list", "get_settings", "list_usage_limits") or (
            act == "personal_cloud" and str(arguments.get("tunnel_action") or "status") == "status"
        ):
            return CallAssessment(
                risk=RiskLevel.SAFE,
                summary=f"Inspect MiniMuse {act.replace('_', ' ')}",
            )
        if act == "update_settings":
            s = arguments.get("settings")
            keys = sorted(s.keys()) if isinstance(s, dict) else [k for k in arguments if k not in ("action", "step")]
            return CallAssessment(
                risk=RiskLevel.SENSITIVE,
                summary=f"Update MiniMuse settings ({', '.join(keys[:6]) or 'profile/ui'})",
            )
        return CallAssessment(
            risk=RiskLevel.SENSITIVE,
            summary=f"system_admin {act}",
        )

    # ------------------------------------------------------------------ actions
    async def execute(self, action: str = "list", **kwargs: Any) -> ToolResult:
        try:
            if action == "list":
                return ToolResult(output=self._list())
            if action == "get_settings":
                return ToolResult(output=self._get_settings())
            if action == "update_settings":
                return await self._update_settings(kwargs.get("settings") or kwargs)
            if action == "personal_cloud":
                return await self._personal_cloud(str(kwargs.get("tunnel_action") or "status"))
            if action == "add_model":
                return await self._add_model(kwargs)
            if action == "set_default_model":
                return self._set_default_model(kwargs)
            if action == "list_usage_limits":
                return ToolResult(output=self._usage_json(self._limits().all()))
            if action == "set_usage_limit":
                return self._set_usage_limit(kwargs)
            if action == "remove_usage_limit":
                return self._remove_usage_limit(str(kwargs.get("limit_id") or ""))
            if action == "restart_server":
                return self._restart()
            return ToolResult.fail(f"unknown system_admin action {action!r}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("system_admin {} failed", action)
            return ToolResult.fail(f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ app settings
    def _get_settings(self) -> str:
        svc = self._svc()
        sv = svc.settings_view()
        prof = svc.profile.to_dict()
        sync_v = svc.sync.view() if getattr(svc, "sync", None) is not None else {}
        pc = getattr(svc, "personal_cloud", None)
        pc_status = pc.status(probe_tailscale=False) if pc is not None else None
        snapshot = {
            "profile": prof,
            "ui": sv.get("ui") or {},
            "agent": sv.get("agent") or {},
            "llm": {
                "provider": (sv.get("llm") or {}).get("provider"),
                "model": (sv.get("llm") or {}).get("model"),
                "model_choice": (sv.get("llm") or {}).get("model_choice"),
                "background_model": (sv.get("llm") or {}).get("background_model"),
            },
            "sentinel": sv.get("sentinel") or {},
            "permissions": sv.get("permissions") or {},
            "cloud_mode": sv.get("cloud_mode"),
            "cloud_slots": sv.get("cloud_slots"),
            "cloud_prompts": sv.get("cloud_prompts"),
            "auto_update": sv.get("auto_update"),
            "trash_retention_days": sv.get("trash_retention_days"),
            "sync": {
                "enabled": sync_v.get("enabled"),
                "side_chats": sync_v.get("side_chats"),
                "peers_count": len(sync_v.get("peers") or []),
                "pending_offline": sync_v.get("pending_offline"),
            },
            "personal_cloud": {
                "best_url": pc_status.get("best_url"),
                "tunnel_running": (pc_status.get("tunnel") or {}).get("running"),
                "tunnel_url": (pc_status.get("tunnel") or {}).get("url"),
                "auto_tunnel": pc_status.get("auto_tunnel"),
                "cloudflared_installed": pc_status.get("cloudflared_installed"),
            }
            if isinstance(pc_status, dict)
            else None,
        }
        return json.dumps(snapshot, ensure_ascii=False, indent=2)

    async def _update_settings(self, raw: Any) -> ToolResult:
        svc = self._svc()
        if not isinstance(raw, dict):
            return ToolResult.fail("update_settings requires a settings object")
        payload = dict(raw)
        payload.pop("action", None)
        payload.pop("step", None)
        if not payload:
            return ToolResult.fail("No settings fields provided to update")

        applied: list[str] = []

        # 1. Sync toggles
        if "sync_enabled" in payload and getattr(svc, "sync", None) is not None:
            val = bool(payload.pop("sync_enabled"))
            await svc.sync.set_enabled(val)
            applied.append(f"sync_enabled={val}")
        if "sync_side_chats" in payload and getattr(svc, "sync", None) is not None:
            val = bool(payload.pop("sync_side_chats"))
            svc.sync.set_side_chats(val)
            applied.append(f"sync_side_chats={val}")

        # 2. Personal cloud auto-tunnel toggle
        if "personal_cloud_auto_tunnel" in payload:
            val = bool(payload.pop("personal_cloud_auto_tunnel"))
            pc = getattr(svc, "personal_cloud", None)
            if pc is not None:
                pc.set_auto_tunnel(val)
                applied.append(f"personal_cloud_auto_tunnel={val}")

        # 3. Allow top-level profile keys convenience (e.g. avatar_bg_mode="default")
        profile_keys = {
            "name",
            "tagline",
            "avatar",
            "emoji",
            "color",
            "avatar_bg_mode",
            "avatar_bg_color",
            "avatar_bg_image",
            "avatar_border_mode",
            "avatar_border_color",
            "avatar_border_glow",
            "avatar_animated",
            "avatar_situation_clips",
            "tone",
            "communication",
            "style",
            "user_name",
            "proactivity",
            "goal_interval_minutes",
            "quiet_hours",
        }
        prof_patch = dict(payload.get("profile") or {}) if isinstance(payload.get("profile"), dict) else {}
        for k in list(payload.keys()):
            if k in profile_keys:
                prof_patch[k] = payload.pop(k)
        if prof_patch:
            payload["profile"] = prof_patch

        # 4. Allow top-level UI keys convenience (e.g. theme="dark", accent="#...")
        ui_keys = {"theme", "bg", "accent", "bubble", "scale", "show_calendar", "show_location", "map_app"}
        ui_patch = dict(payload.get("ui") or {}) if isinstance(payload.get("ui"), dict) else {}
        for k in list(payload.keys()):
            if k in ui_keys:
                val = payload.pop(k)
                ui_patch[k] = "true" if val is True else "false" if val is False else str(val)
        if ui_patch:
            payload["ui"] = ui_patch

        # 5. Apply all standard MiniMuse settings immediately (persists to profile.json / app-settings.json & broadcasts WS)
        allowed_top = {
            "profile",
            "sentinel_mode",
            "show_thinking",
            "allow_self_improve",
            "language",
            "max_steps",
            "auto_continue",
            "max_continues",
            "max_context_messages",
            "max_context_tokens",
            "max_context_images",
            "always_ask_tools",
            "always_allow_tools",
            "model_choice",
            "background_model",
            "cloud_mode",
            "cloud_slots",
            "cloud_prompts",
            "ui",
            "permissions",
            "auto_update",
            "trash_retention_days",
        }
        filtered = {k: v for k, v in payload.items() if k in allowed_top}
        unknown = [k for k in payload.keys() if k not in allowed_top]
        if filtered:
            svc.update_settings(filtered)
            for k, v in filtered.items():
                if isinstance(v, dict):
                    applied.append(f"{k}={json.dumps(v, ensure_ascii=False)}")
                else:
                    applied.append(f"{k}={v!r}")
        if not applied:
            return ToolResult.fail(
                f"No supported settings keys found (ignored: {', '.join(unknown)}). "
                "Call get_settings first to inspect valid keys."
            )
        note = f" (ignored unsupported keys: {', '.join(unknown)})" if unknown else ""
        return ToolResult(
            output=f"Settings saved immediately and synced: {'; '.join(applied)}.{note}"
        )

    async def _personal_cloud(self, tunnel_action: str) -> ToolResult:
        svc = self._svc()
        pc = getattr(svc, "personal_cloud", None)
        if pc is None:
            return ToolResult.fail("Personal Cloud manager is not attached on this runtime.")
        act = tunnel_action.strip().lower()
        if act == "start":
            st = await pc.start_tunnel(auto_install=False)
            if getattr(svc, "sync", None) is not None:
                svc.sync.peer_sync_soon(delay=0.5)
        elif act == "stop":
            st = await pc.stop_tunnel()
        elif act == "auto_on":
            pc.set_auto_tunnel(True)
            st = pc.status(probe_tailscale=True)
        elif act == "auto_off":
            pc.set_auto_tunnel(False)
            st = pc.status(probe_tailscale=True)
        else:
            st = pc.status(probe_tailscale=True)
        return ToolResult(output=json.dumps(st, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------ models
    def _list(self) -> str:
        svc = self._svc()
        s = svc.app.settings
        lines = [
            "Default AI (the one every chat uses):",
            f"- {s.llm.model} @ {s.llm.base_url or '(provider default)'} "
            f"[provider: {s.llm.provider}]",
            "Endpoints you can switch to (id | label | base_url | models):",
        ]
        rows = svc.connections.endpoints.rows()
        if not rows:
            lines.append("- (none added yet; add one with action=add_model)")
        for row in rows:
            models = ", ".join(str(m) for m in row.get("models") or []) or "(no models listed)"
            lines.append(
                f"- {row['id']} | {row.get('label') or ''} | {row.get('base_url') or ''} | {models}"
            )
        return "\n".join(lines)

    async def _add_model(self, args: dict[str, Any]) -> ToolResult:
        svc = self._svc()
        body = {
            "base_url": str(args.get("base_url") or ""),
            "label": str(args.get("label") or ""),
            "api_key": str(args.get("api_key") or ""),
        }
        if args.get("model"):
            body["models"] = [str(args["model"])]
        row = await svc.connections.endpoints.add(body)
        return ToolResult(
            output=(
                f"Endpoint added: id={row['id']} label={row.get('label')} "
                f"models={row.get('models')}. It is now in the dropdown. To make it the "
                f"default, call set_default_model with endpoint_id={row['id']} and "
                f"model=<id>."
            )
        )

    def _set_default_model(self, args: dict[str, Any]) -> ToolResult:
        svc = self._svc()
        prev = dict(svc.connections.view().get("llm") or {})
        body: dict[str, Any] = {}
        if args.get("endpoint_id"):
            body["endpoint"] = str(args["endpoint_id"])
        if args.get("model"):
            body["model"] = str(args["model"])
        if not body:
            return ToolResult.fail("set_default_model needs a model id and/or an endpoint_id")
        view = svc.connections.set_llm(body)
        # the person gets a card to keep the switch or put the previous default back
        # (in ask mode this runs after the approval; in hands-off it just lands)
        try:
            svc.ui.emit(
                {
                    "type": "model_switch",
                    "model": str(view.get("model") or body.get("model") or ""),
                    "previous_model": str(prev.get("model") or ""),
                    "previous_provider": str(prev.get("provider") or ""),
                    "previous_base_url": str(prev.get("base_url") or ""),
                    "status": "applied",
                }
            )
        except Exception:  # noqa: BLE001 - the switch itself already happened
            logger.warning("model_switch card could not be emitted")
        return ToolResult(
            output=(
                f"Default AI switched to {view.get('model')} "
                f"(endpoint {view.get('endpoint_id') or 'n/a'})."
            )
        )

    # ------------------------------------------------------------------ usage limits
    def _limits(self) -> Any:
        return self._svc().usage_limits

    @staticmethod
    def _usage_json(rows: list[dict[str, Any]]) -> str:
        return json.dumps(rows, ensure_ascii=False, indent=1) if rows else "(no usage limits set)"

    def _set_usage_limit(self, args: dict[str, Any]) -> ToolResult:
        from nanomuse.server.usage_limits import UNITS, WINDOWS, new_id

        store = self._limits()
        amount = float(args.get("amount") or 0)
        if amount <= 0:
            return ToolResult.fail("set_usage_limit needs a positive amount")
        unit = str(args.get("unit") or "cost")
        window = str(args.get("window") or "month")
        scope = str(args.get("scope") or "global")
        if unit not in UNITS:
            return ToolResult.fail(f"unit must be one of {UNITS}")
        if window not in WINDOWS:
            return ToolResult.fail(f"window must be one of {WINDOWS}")
        if scope == "model" and not args.get("limit_model"):
            return ToolResult.fail("scope=model needs limit_model=<model id>")
        on_full = str(args.get("on_full") or "stop")
        if on_full not in ("warn", "stop", "confirm"):
            on_full = "stop"
        limit_id = str(args.get("limit_id") or "") or new_id()
        row = {
            "id": limit_id,
            "name": str(args.get("name") or f"{scope} {unit} per {window}"),
            "scope": scope,
            "model": str(args.get("limit_model") or "") if scope == "model" else "",
            "unit": unit,
            "window": window,
            "amount": amount,
            "warning_pct": int(args.get("warning_pct") or 80),
            "on_full": on_full,
            "action": on_full,
        }
        rows = [r for r in store.all() if r.get("id") != limit_id]
        rows.append(row)
        store.save(rows)
        # live: the service checks these before every turn
        return ToolResult(
            output=(
                f"Usage limit saved: id={limit_id}, {row['name']}: {amount} {unit} per "
                f"{window}, warns at {row['warning_pct']}%, at 100% it {on_full}s."
            )
        )

    def _remove_usage_limit(self, limit_id: str) -> ToolResult:
        store = self._limits()
        rows = [r for r in store.all() if r.get("id") != limit_id]
        if len(rows) == len(store.all()):
            return ToolResult.fail(f"no usage limit {limit_id!r}")
        store.save(rows)
        return ToolResult(output=f"Usage limit {limit_id} removed.")

    # ------------------------------------------------------------------ restart
    def _restart(self) -> ToolResult:
        svc = self._svc()
        # answer first; the actual restart happens on a short timer so this tool result
        # reaches the chat before the server goes down (it comes back on its own)
        loop = asyncio.get_running_loop()
        loop.call_later(1.0, svc.schedule_restart)
        return ToolResult(
            output=(
                "Restarting the server now. It will be back in a few seconds; give the "
                "app a moment, then reload the page if it does not reconnect by itself."
            )
        )

    def _svc(self) -> Any:
        if self.svc is None:
            raise RuntimeError("system_admin is not attached to the server yet")
        return self.svc
