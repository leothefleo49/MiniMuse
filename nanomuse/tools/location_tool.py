"""AI tool for locations, frequent places, contact addresses, travel history, speeds, and maps."""

from __future__ import annotations

import json
from typing import Any

from nanomuse.location.store import (
    LocationStore,
    build_map_links,
    estimate_route_and_traffic,
)
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment


class LocationTool(BaseTool):
    name: str = "location"
    description: str = (
        "Search and manage places, common addresses (Home, Work, favorites), friends/contacts addresses, "
        "travel history, driving/walking speeds, live traffic/route estimates, and map app launch links "
        "(Google Maps, Apple Maps, Waze, OpenStreetMap). Search by name, address, contact, #hashtag, or context."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "summary",
                    "search",
                    "where_is",
                    "save_place",
                    "update_place",
                    "delete_place",
                    "log_trip",
                    "travel_history",
                    "check_traffic",
                    "map_link",
                ],
            },
            "query": {"type": "string", "description": "Place name, contact name, address, #hashtag, or context keyword"},
            "tag": {"type": "string", "description": "Filter hashtag e.g. #work or #friend"},
            "place_id": {"type": "string"},
            "name": {"type": "string"},
            "address": {"type": "string"},
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "category": {
                "type": "string",
                "enum": ["home", "work", "friend", "frequent", "food", "errand", "travel", "other"],
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "context": {"type": "string", "description": "Helpful context note (gate code, parking tip, who lives here)"},
            "contact_name": {"type": "string"},
            "favorite": {"type": "boolean"},
            "origin": {"type": "string", "description": "Starting place/address/contact for traffic check or trip"},
            "destination": {"type": "string", "description": "Destination place/address/contact for traffic check"},
            "duration_min": {"type": "number"},
            "distance_mi": {"type": "number"},
            "current_speed_mph": {"type": "number"},
            "mode": {"type": "string", "enum": ["driving", "walking", "cycling", "transit", "stationary"]},
            "traffic_level": {"type": "string", "enum": ["clear", "light", "moderate", "heavy", "unknown"]},
            "notes": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    egress: bool = False
    store: LocationStore
    contacts: Any = None

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        action = str(args.get("action") or "summary")
        if action == "check_traffic":
            return CallAssessment(
                risk=RiskLevel.SAFE,
                reads_private_data=True,
                egress=True,
                egress_target="router.project-osrm.org",
                egress_configured=True,
                summary=f"location.check_traffic({args.get('destination') or args.get('query') or ''})",
            )
        return CallAssessment(
            risk=RiskLevel.SAFE,
            reads_private_data=True,
            summary=f"location.{action}({args.get('query') or args.get('name') or ''})",
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        contact_list = self.contacts.contacts if self.contacts is not None else []
        try:
            if action == "summary":
                return ToolResult(output=json.dumps(self.store.summary(contact_list), ensure_ascii=False, indent=2))

            if action in ("search", "where_is"):
                q = str(kwargs.get("query") or kwargs.get("contact_name") or kwargs.get("name") or "")
                res = self.store.search(
                    q,
                    contacts=contact_list,
                    tag=str(kwargs.get("tag") or ""),
                    category=str(kwargs.get("category") or ""),
                )
                return ToolResult(output=json.dumps(res, ensure_ascii=False, indent=2))

            if action == "save_place":
                place = self.store.add_place(
                    name=str(kwargs.get("name") or ""),
                    address=str(kwargs.get("address") or ""),
                    lat=kwargs.get("lat"),
                    lng=kwargs.get("lng"),
                    category=str(kwargs.get("category") or "other"),
                    tags=kwargs.get("tags"),
                    context=str(kwargs.get("context") or ""),
                    contact_name=str(kwargs.get("contact_name") or ""),
                    favorite=bool(kwargs.get("favorite", False)),
                )
                return ToolResult(output=json.dumps({"ok": True, "place": place}, ensure_ascii=False, indent=2))

            if action == "update_place":
                pid = str(kwargs.get("place_id") or "")
                if not pid:
                    return ToolResult.fail("Provide `place_id`.")
                updated = self.store.update_place(pid, kwargs)
                if not updated:
                    return ToolResult.fail(f"No place found with id {pid}.")
                return ToolResult(output=json.dumps({"ok": True, "place": updated}, ensure_ascii=False, indent=2))

            if action == "delete_place":
                pid = str(kwargs.get("place_id") or "")
                if not pid or not self.store.delete_place(pid):
                    return ToolResult.fail(f"No place found with id {pid}.")
                return ToolResult(output=f"Deleted place {pid}.")

            if action == "log_trip":
                trip = self.store.log_trip(
                    place_id=str(kwargs.get("place_id") or ""),
                    place_name=str(kwargs.get("name") or kwargs.get("destination") or ""),
                    address=str(kwargs.get("address") or ""),
                    lat=kwargs.get("lat"),
                    lng=kwargs.get("lng"),
                    origin_name=str(kwargs.get("origin") or ""),
                    duration_min=float(kwargs.get("duration_min") or 0),
                    distance_mi=float(kwargs.get("distance_mi") or 0),
                    current_speed_mph=float(kwargs.get("current_speed_mph") or 0),
                    mode=str(kwargs.get("mode") or ""),
                    traffic_level=str(kwargs.get("traffic_level") or "unknown"),
                    tags=kwargs.get("tags"),
                    notes=str(kwargs.get("notes") or ""),
                    source="ai",
                )
                return ToolResult(output=json.dumps({"ok": True, "trip": trip}, ensure_ascii=False, indent=2))

            if action == "travel_history":
                lim = int(kwargs.get("limit") or 30)
                return ToolResult(
                    output=json.dumps(
                        {
                            "stats": self.store.speed_stats(),
                            "trips": self.store.list_trips(limit=lim),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )

            if action == "check_traffic":
                dest = str(kwargs.get("destination") or kwargs.get("query") or kwargs.get("address") or "")
                if not dest:
                    return ToolResult.fail("Provide `destination` or `query`.")
                res = await estimate_route_and_traffic(
                    dest,
                    str(kwargs.get("origin") or ""),
                    store=self.store,
                    contacts=contact_list,
                )
                return ToolResult(output=json.dumps(res, ensure_ascii=False, indent=2))

            if action == "map_link":
                q = str(kwargs.get("query") or kwargs.get("address") or kwargs.get("name") or "")
                lat = kwargs.get("lat")
                lng = kwargs.get("lng")
                if not q and lat is None and lng is None:
                    return ToolResult.fail("Provide `query`, `address`, or coordinates.")
                links = build_map_links(q, lat=lat, lng=lng, label=str(kwargs.get("name") or q))
                return ToolResult(output=json.dumps(links, indent=2))

            return ToolResult.fail(f"Unknown action: {action}")
        except ValueError as exc:
            return ToolResult.fail(str(exc))
