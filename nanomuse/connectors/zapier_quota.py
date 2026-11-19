"""Zapier usage quota tracker & Direct API recommendation engine.

Zapier MCP / Webhooks have a strict monthly task quota (e.g. 100 tasks/month on
the free tier), making Zapier best suited as a temporary bridge or for niche apps.
This module tracks every Zapier tool execution, reports remaining quota in the UI
and to the AI, and recommends switching high-usage services to direct APIs via
``api_connector``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanomuse.connectors.api_registry import STANDARD_API_PRESETS
from nanomuse.logger import logger
from nanomuse.private import keep_private

DEFAULT_ZAPIER_MONTHLY_LIMIT = 100

# Keywords in Zapier tool names mapped to built-in direct API presets
_TOOL_TO_PRESET_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("gmail", "gmail"),
    ("google_mail", "gmail"),
    ("calendar", "google_calendar"),
    ("gcal", "google_calendar"),
    ("drive", "google_drive"),
    ("gtasks", "google_tasks"),
    ("google_tasks", "google_tasks"),
    ("maps", "google_maps_places"),
    ("outlook", "microsoft_graph"),
    ("office365", "microsoft_graph"),
    ("microsoft", "microsoft_graph"),
    ("teams", "microsoft_graph"),
    ("slack", "slack"),
    ("discord", "discord"),
    ("telegram", "telegram"),
    ("twilio", "twilio"),
    ("sms", "twilio"),
    ("github", "github"),
    ("gitlab", "gitlab"),
    ("linear", "linear"),
    ("jira", "jira"),
    ("notion", "notion"),
    ("todoist", "todoist"),
    ("trello", "trello"),
    ("asana", "asana"),
    ("airtable", "airtable"),
    ("clickup", "clickup"),
    ("spotify", "spotify"),
    ("strava", "strava"),
    ("fitbit", "fitbit"),
    ("oura", "oura"),
    ("whoop", "whoop"),
    ("withings", "withings"),
    ("garmin", "garmin_connect"),
    ("weather", "open_meteo"),
    ("stripe", "stripe"),
    ("ynab", "ynab"),
    ("supabase", "supabase"),
    ("cloudflare", "cloudflare"),
    ("home_assistant", "home_assistant"),
    ("hue", "philips_hue"),
)


def _current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_zapier_server(server_name: str, url: str = "") -> bool:
    s = (server_name or "").strip().lower()
    u = (url or "").strip().lower()
    return "zapier" in s or "zapier.com" in u


def match_direct_preset(tool_or_service: str) -> dict[str, Any] | None:
    text = (tool_or_service or "").strip().lower()
    if not text:
        return None
    if text in STANDARD_API_PRESETS:
        return STANDARD_API_PRESETS[text]
    for kw, pid in _TOOL_TO_PRESET_KEYWORDS:
        if kw in text and pid in STANDARD_API_PRESETS:
            return STANDARD_API_PRESETS[pid]
    for pid, preset in STANDARD_API_PRESETS.items():
        if pid in text or str(preset.get("name") or "").lower() in text:
            return preset
    return None


class ZapierQuotaTracker:
    """Persistent monthly usage counter and direct-API advisor for Zapier."""

    FILE_NAME = "zapier_quota.json"

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / self.FILE_NAME

    def _load(self) -> dict[str, Any]:
        month = _current_month()
        default_data: dict[str, Any] = {
            "month": month,
            "used": 0,
            "limit": DEFAULT_ZAPIER_MONTHLY_LIMIT,
            "by_tool": {},
            "last_used_at": "",
        }
        if not self.path.is_file():
            return default_data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return default_data
            limit = int(raw.get("limit") or DEFAULT_ZAPIER_MONTHLY_LIMIT)
            if limit <= 0:
                limit = DEFAULT_ZAPIER_MONTHLY_LIMIT
            if raw.get("month") != month:
                return {
                    "month": month,
                    "used": 0,
                    "limit": limit,
                    "by_tool": {},
                    "last_used_at": str(raw.get("last_used_at") or ""),
                }
            by_tool = raw.get("by_tool")
            return {
                "month": month,
                "used": max(0, int(raw.get("used") or 0)),
                "limit": limit,
                "by_tool": {str(k): int(v) for k, v in by_tool.items() if k}
                if isinstance(by_tool, dict)
                else {},
                "last_used_at": str(raw.get("last_used_at") or ""),
            }
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("zapier_quota.json unreadable: {}", exc)
            return default_data

    def _save(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        keep_private(self.path)

    def update_config(
        self,
        *,
        limit: int | None = None,
        used: int | None = None,
        reset: bool = False,
    ) -> dict[str, Any]:
        data = self._load()
        if limit is not None and int(limit) > 0:
            data["limit"] = max(1, min(1_000_000, int(limit)))
        if reset:
            data["used"] = 0
            data["by_tool"] = {}
        elif used is not None and int(used) >= 0:
            data["used"] = int(used)
        self._save(data)
        return self.status()

    def record_call(self, server_name: str, tool_name: str) -> dict[str, Any]:
        data = self._load()
        data["used"] = int(data.get("used") or 0) + 1
        by_tool: dict[str, int] = dict(data.get("by_tool") or {})
        key = f"{server_name}.{tool_name}" if server_name else tool_name
        by_tool[key] = int(by_tool.get(key) or 0) + 1
        data["by_tool"] = by_tool
        data["last_used_at"] = _now_iso()
        self._save(data)

        used = data["used"]
        limit = data["limit"]
        remaining = max(0, limit - used)
        pct = round((used / limit) * 100, 1) if limit > 0 else 0.0
        tool_calls = by_tool[key]
        preset = match_direct_preset(tool_name)
        recommend = pct >= 50.0 or tool_calls >= 3
        preset_hint = (
            f" Direct preset available: `{preset['id']}` ({preset['name']}) via `api_connector`."
            if preset
            else " You can replace frequent Zapier actions with a direct API via `api_connector`."
        )
        if recommend:
            notice = (
                f"\n\n[Zapier Quota Notice: {used}/{limit} monthly tasks used ({pct}%, {remaining} left). "
                f"`{tool_name}` has been called {tool_calls}x this month. Zapier quotas are temporary/limited — "
                f"recommend switching to a direct API connection so the user doesn't run out of Zapier tasks.{preset_hint}]"
            )
        else:
            notice = f"\n\n[Zapier Quota: {used}/{limit} monthly tasks used ({remaining} remaining this month).]"
        return {
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "percent": pct,
            "tool_calls": tool_calls,
            "recommend_direct": recommend,
            "matched_preset": preset["id"] if preset else None,
            "notice": notice,
        }

    def status(
        self,
        mcp_servers: list[Any] | None = None,
        configured_apis: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        data = self._load()
        used = int(data["used"])
        limit = int(data["limit"])
        remaining = max(0, limit - used)
        pct = round((used / limit) * 100, 1) if limit > 0 else 0.0
        if pct >= 100:
            level = "exhausted"
        elif pct >= 80:
            level = "high"
        elif pct >= 50:
            level = "moderate"
        else:
            level = "ok"

        zapier_configured = False
        zapier_servers: list[str] = []
        for srv in mcp_servers or []:
            name = str(getattr(srv, "name", "") or (srv.get("name") if isinstance(srv, dict) else "") or "")
            url = str(getattr(srv, "url", "") or (srv.get("url") if isinstance(srv, dict) else "") or "")
            if is_zapier_server(name, url):
                zapier_configured = True
                zapier_servers.append(name)

        direct_api_ids = {
            str(a.get("preset_id") or a.get("id") or "").lower()
            for a in (configured_apis or [])
            if isinstance(a, dict)
        }

        by_tool: dict[str, int] = dict(data.get("by_tool") or {})
        sorted_tools = sorted(by_tool.items(), key=lambda kv: kv[1], reverse=True)
        recommendations: list[dict[str, Any]] = []
        seen_presets: set[str] = set()

        for tool_key, count in sorted_tools:
            preset = match_direct_preset(tool_key)
            if preset and preset["id"] not in seen_presets:
                seen_presets.add(preset["id"])
                recommendations.append({
                    "tool": tool_key,
                    "calls_this_month": count,
                    "preset_id": preset["id"],
                    "preset_name": preset["name"],
                    "base_url": preset["base_url"],
                    "already_connected_direct": preset["id"].lower() in direct_api_ids,
                    "reason": (
                        f"Used {count}x via Zapier this month. Connecting {preset['name']} directly "
                        "uses 0 Zapier quota, runs faster, and never expires."
                    ),
                })

        # Always provide top recommended direct alternatives even before heavy usage
        if not recommendations:
            for pid in ("gmail", "google_calendar", "slack", "notion", "github", "todoist"):
                preset = STANDARD_API_PRESETS.get(pid)
                if preset and pid not in direct_api_ids:
                    recommendations.append({
                        "tool": f"zapier.{pid}",
                        "calls_this_month": 0,
                        "preset_id": pid,
                        "preset_name": preset["name"],
                        "base_url": preset["base_url"],
                        "already_connected_direct": False,
                        "reason": (
                            f"Save Zapier tasks by connecting {preset['name']} directly via Direct API."
                        ),
                    })
                if len(recommendations) >= 4:
                    break

        should_recommend_direct = level in ("moderate", "high", "exhausted") or any(
            c >= 3 for _, c in sorted_tools
        )
        summary_message = (
            f"{used} / {limit} Zapier tasks used in {data['month']} ({remaining} remaining, {pct}%)."
        )
        if level == "exhausted":
            summary_message += " Monthly Zapier quota reached! Switch frequent integrations to Direct APIs."
        elif level == "high":
            summary_message += " Zapier quota is running low — we recommend moving frequent services to Direct APIs."
        elif should_recommend_direct:
            summary_message += " Tip: Zapier has a monthly task cap; use Direct APIs for heavy/permanent workflows."

        return {
            "configured": zapier_configured or used > 0,
            "servers": zapier_servers,
            "month": data["month"],
            "used": used,
            "limit": limit,
            "remaining": remaining,
            "percent": pct,
            "level": level,
            "last_used_at": data.get("last_used_at") or "",
            "by_tool": dict(sorted_tools[:15]),
            "should_recommend_direct": should_recommend_direct,
            "summary": summary_message,
            "recommendations": recommendations,
        }

    def compare(self, service: str = "") -> dict[str, Any]:
        st = self.status()
        preset = match_direct_preset(service) if service else None
        return {
            "service": service or "General Integration",
            "zapier_quota": {
                "used": st["used"],
                "limit": st["limit"],
                "remaining": st["remaining"],
                "percent": st["percent"],
                "level": st["level"],
            },
            "comparison": {
                "zapier": {
                    "pros": [
                        "Single MCP URL connects thousands of SaaS apps quickly",
                        "Great for quick prototypes or apps without public REST documentation",
                    ],
                    "cons": [
                        f"Strict monthly task quota ({st['used']}/{st['limit']} used this month) — temporary for heavy use",
                        "Higher latency (extra proxy hop through Zapier servers)",
                        "Stops working once monthly task allowance runs out",
                    ],
                },
                "direct_api": {
                    "pros": [
                        "Unlimited direct calls — burns 0 Zapier monthly tasks",
                        "Fast direct HTTPS connection with local CredentialVault encryption",
                        "Full REST/GraphQL & OpenAPI schema discovery via `api_connector`",
                        "Permanent setup that won't pause mid-month",
                    ],
                    "cons": [
                        "Requires a one-time API key or OAuth sign-in per service",
                    ],
                },
            },
            "direct_preset": {
                "available": preset is not None,
                "preset_id": preset["id"] if preset else "custom",
                "name": preset["name"] if preset else f"Custom Direct API ({service or 'REST'})",
                "base_url": preset["base_url"] if preset else "",
                "auth_type": preset["auth_type"] if preset else "bearer",
                "ai_guide": preset.get("ai_guide", "") if preset else (
                    "Use `api_connector(action='configure_api', ...)` or `request_setup` to ask the user for the API key."
                ),
            },
            "recommendation": (
                f"Use Direct API (`{preset['id']}`) via `api_connector` for ongoing or frequent {preset['name']} tasks, "
                "and reserve Zapier for low-volume or one-off automations."
                if preset
                else "For any service you call more than a few times a week, configure a Direct API via `api_connector` "
                "so you don't exhaust your temporary Zapier task quota."
            ),
        }
