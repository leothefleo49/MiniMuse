"""Universal AI API Builder & Connector tool.

Lets the AI discover, build, save, test, and call ANY standard or arbitrary REST/GraphQL API.
Includes 36+ built-in presets, OpenAPI/Swagger auto-discovery, learned endpoint memory,
and Sentinel-gated HTTP execution with vault secret injection.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from nanomuse.connectors.api_registry import (
    STANDARD_API_PRESETS,
    APIRegistry,
    preset_list,
)
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment


class ApiConnectorTool(BaseTool):
    name: str = "api_connector"
    description: str = (
        "Build, configure, discover, test, and call ANY external or local REST/GraphQL API "
        "(including 36+ built-in templates: Gmail, Google Calendar/Drive/Tasks/Maps, Microsoft Graph, "
        "Slack, Discord, Telegram, Twilio, GitHub, GitLab, Linear, Jira, Notion, Todoist, Trello, "
        "Asana, Airtable, ClickUp, Obsidian, Home Assistant, Philips Hue, Spotify, Strava, Fitbit, "
        "Oura, Open-Meteo, OSRM/Nominatim, TomTom, Stripe, YNAB, Supabase, Cloudflare, OpenRouter, Webhooks). "
        "For unfamiliar APIs, run `discover_openapi` or `request` (GET root/docs) to figure out the schema, "
        "then `save_endpoint` so MiniMuse remembers how to read and write that API."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_presets",
                    "list_apis",
                    "configure_api",
                    "save_endpoint",
                    "discover_openapi",
                    "test_api",
                    "request",
                    "remove_api",
                ],
            },
            "query": {"type": "string", "description": "Search filter for list_presets (category or keyword)"},
            "api_id": {"type": "string", "description": "Configured API id or name"},
            "preset_id": {"type": "string", "description": "Standard preset id (or 'custom')"},
            "name": {"type": "string", "description": "Human-friendly name for API connection or endpoint"},
            "base_url": {"type": "string", "description": "Base URL (e.g. https://api.example.com/v1)"},
            "auth_type": {
                "type": "string",
                "enum": ["none", "bearer", "api_key_header", "api_key_query", "basic", "oauth2"],
            },
            "auth_header": {"type": "string", "description": "Header name for API key (default Authorization or X-API-Key)"},
            "auth_param": {"type": "string", "description": "Query param name for api_key_query"},
            "auth_prefix": {"type": "string", "description": "Prefix before token, e.g. 'Bearer ' or ''"},
            "secret": {"type": "string", "description": "API token/key to store securely in encrypted vault"},
            "ai_guide": {"type": "string", "description": "Usage notes or discovered schema summary for future turns"},
            "docs_url": {"type": "string"},
            "spec_url": {"type": "string", "description": "Optional OpenAPI/Swagger JSON URL for discover_openapi"},
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"]},
            "path": {"type": "string", "description": "Endpoint path relative to base_url (e.g. /tasks or /users/{id})"},
            "url": {"type": "string", "description": "Direct full URL when not calling a saved api_id"},
            "headers": {"type": "object"},
            "params": {"type": "object"},
            "body": {"type": ["object", "array", "string"]},
            "query_params": {"type": "array", "items": {"type": "string"}},
            "body_schema": {"type": "object"},
            "endpoint_description": {"type": "string"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    egress: bool = False
    accepts_secrets: bool = True
    registry: APIRegistry

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "list_apis")
        if action in ("list_presets", "list_apis"):
            return CallAssessment(risk=RiskLevel.SAFE, summary=f"api_connector.{action}")

        method = str(args.get("method") or "GET").upper()
        api_id = str(args.get("api_id") or "")
        conn = self.registry.get(api_id) if api_id else None
        raw_url = str(args.get("url") or args.get("base_url") or args.get("spec_url") or (conn.base_url if conn else ""))
        host = urlparse(raw_url).hostname or None if raw_url else None
        configured = bool(conn is not None and not args.get("url"))

        if action in ("configure_api", "save_endpoint", "remove_api"):
            risk = RiskLevel.SAFE if action == "save_endpoint" else RiskLevel.MODERATE
            return CallAssessment(
                risk=risk,
                egress=False,
                target=api_id or args.get("name") or host,
                summary=f"api_connector.{action}({args.get('name') or api_id})",
            )

        is_write = action == "request" and method not in ("GET", "HEAD")
        return CallAssessment(
            risk=RiskLevel.MODERATE if is_write else RiskLevel.SAFE,
            reads_private_data=True,
            egress=True,
            egress_target=host,
            egress_configured=configured,
            target=host or api_id,
            summary=f"api_connector.{action} {method} {args.get('path') or raw_url}",
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        try:
            if action == "list_presets":
                q = str(kwargs.get("query") or "").strip().lower()
                items = preset_list()
                if q:
                    items = [
                        p
                        for p in items
                        if q in p["id"].lower()
                        or q in p["name"].lower()
                        or q in p["category"].lower()
                        or q in p["description"].lower()
                    ]
                compact = [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "category": p["category"],
                        "base_url": p["base_url"],
                        "auth_type": p["auth_type"],
                        "ai_guide": p.get("ai_guide", ""),
                        "endpoints_count": len(p.get("endpoints") or []),
                    }
                    for p in items
                ]
                return ToolResult(output=json.dumps({"count": len(compact), "presets": compact}, indent=2))

            if action == "list_apis":
                return ToolResult(output=json.dumps({"apis": self.registry.list()}, indent=2))

            if action == "configure_api":
                payload: dict[str, Any] = {
                    k: kwargs[k]
                    for k in (
                        "id",
                        "api_id",
                        "preset_id",
                        "name",
                        "base_url",
                        "auth_type",
                        "auth_header",
                        "auth_param",
                        "auth_prefix",
                        "secret",
                        "headers",
                        "ai_guide",
                        "docs_url",
                    )
                    if k in kwargs and kwargs[k] is not None
                }
                if "api_id" in payload and "id" not in payload:
                    payload["id"] = payload.pop("api_id")
                if not payload.get("name") and not payload.get("id") and payload.get("preset_id") in STANDARD_API_PRESETS:
                    payload["name"] = STANDARD_API_PRESETS[payload["preset_id"]]["name"]
                saved = self.registry.upsert(payload)
                return ToolResult(output=json.dumps({"ok": True, "api": saved}, indent=2))

            if action == "save_endpoint":
                api_id = str(kwargs.get("api_id") or "")
                if not api_id:
                    return ToolResult.fail("Provide `api_id` to save a learned endpoint.")
                ep = {
                    "name": kwargs.get("name") or kwargs.get("path") or "Endpoint",
                    "method": kwargs.get("method") or "GET",
                    "path": kwargs.get("path") or "/",
                    "description": kwargs.get("endpoint_description") or "",
                    "query_params": kwargs.get("query_params") or [],
                    "body_schema": kwargs.get("body_schema") or {},
                }
                updated = self.registry.save_endpoint(api_id, ep, learned_notes=kwargs.get("ai_guide"))
                return ToolResult(output=json.dumps({"ok": True, "api": updated}, indent=2))

            if action == "discover_openapi":
                res = await self.registry.discover_openapi(
                    api_id=kwargs.get("api_id"),
                    spec_url=str(kwargs.get("spec_url") or ""),
                )
                return ToolResult(output=json.dumps(res, indent=2))

            if action == "test_api":
                api_id = str(kwargs.get("api_id") or "")
                if not api_id:
                    return ToolResult.fail("Provide `api_id` to test.")
                res = await self.registry.test(api_id)
                return ToolResult(output=json.dumps(res, indent=2))

            if action == "request":
                res = await self.registry.execute(
                    api_id=kwargs.get("api_id"),
                    url=kwargs.get("url"),
                    method=str(kwargs.get("method") or "GET"),
                    path=str(kwargs.get("path") or ""),
                    headers=kwargs.get("headers"),
                    params=kwargs.get("params"),
                    body=kwargs.get("body"),
                )
                return ToolResult(output=json.dumps(res, ensure_ascii=False, indent=2))

            if action == "remove_api":
                api_id = str(kwargs.get("api_id") or "")
                removed = self.registry.remove(api_id)
                if not removed:
                    return ToolResult.fail(f"No configured API matched {api_id!r}.")
                return ToolResult(output=f"Removed API integration {api_id}.")

            return ToolResult.fail(f"Unknown action: {action}")
        except (ValueError, KeyError) as exc:
            return ToolResult.fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"API call failed: {exc}")
