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
from nanomuse.tools.base import BaseTool


class SystemAdminTool(BaseTool):
    name: str = "system_admin"
    description: str = (
        "Administer this nanoMuse itself: list the configured AIs (models), add a new AI "
        "endpoint with its API key, switch the default AI (the person gets a card to "
        "accept), set or remove usage limits and warnings, and restart the server after "
        "a build. Use `list` first to see ids."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "add_model",
                    "set_default_model",
                    "list_usage_limits",
                    "set_usage_limit",
                    "remove_usage_limit",
                    "restart_server",
                ],
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

    # ------------------------------------------------------------------ actions
    async def execute(self, action: str = "list", **kwargs: Any) -> ToolResult:
        try:
            if action == "list":
                return ToolResult(output=self._list())
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
