"""Location, frequent places, contact addresses, travel history, speed & traffic store."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

_TAG_RE = re.compile(r"#([A-Za-z0-9_-]{1,32})")
VALID_PLACE_CATEGORIES = (
    "home",
    "work",
    "friend",
    "frequent",
    "food",
    "errand",
    "travel",
    "other",
)
VALID_MODES = ("driving", "walking", "cycling", "transit", "stationary")
VALID_TRAFFIC = ("clear", "light", "moderate", "heavy", "unknown")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_tags(raw: list[str] | str | None, text_hint: str = "") -> list[str]:
    """Normalize hashtags into lowercase ``#tag`` tokens, also extracting inline hashtags."""
    items: list[str] = []
    if isinstance(raw, str):
        items.extend(re.split(r"[\s,]+", raw.strip()))
    elif isinstance(raw, list):
        items.extend(str(x).strip() for x in raw)
    if text_hint:
        items.extend(f"#{m}" for m in _TAG_RE.findall(text_hint))
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        clean = item.strip().lstrip("#").lower()
        clean = re.sub(r"[^a-z0-9_-]+", "", clean)
        if not clean:
            continue
        tag = f"#{clean}"
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out[:20]


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two coordinates in statute miles."""
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def build_map_links(
    query: str = "",
    *,
    lat: float | None = None,
    lng: float | None = None,
    label: str = "",
) -> dict[str, str]:
    """Generate launch URLs across Google Maps, Apple Maps, Waze, OSM, and geo: URI."""
    has_coords = lat is not None and lng is not None
    coord_str = f"{lat:.6f},{lng:.6f}" if has_coords else ""
    dest_q = coord_str if has_coords and not query.strip() else (query.strip() or coord_str)
    q_enc = quote_plus(dest_q)
    label_enc = quote_plus(label.strip() or dest_q)

    if has_coords:
        geo_uri = f"geo:{lat:.6f},{lng:.6f}?q={lat:.6f},{lng:.6f}({label_enc})"
        waze_url = f"https://waze.com/ul?ll={lat:.6f},{lng:.6f}&navigate=yes"
        apple_url = f"https://maps.apple.com/?ll={lat:.6f},{lng:.6f}&q={label_enc}"
        osm_url = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lng:.6f}#map=16/{lat:.6f}/{lng:.6f}"
    else:
        geo_uri = f"geo:0,0?q={q_enc}"
        waze_url = f"https://waze.com/ul?q={q_enc}&navigate=yes"
        apple_url = f"https://maps.apple.com/?q={q_enc}"
        osm_url = f"https://www.openstreetmap.org/search?query={q_enc}"

    return {
        "google_maps": f"https://www.google.com/maps/search/?api=1&query={q_enc}",
        "google_directions": f"https://www.google.com/maps/dir/?api=1&destination={q_enc}",
        "apple_maps": apple_url,
        "waze": waze_url,
        "openstreetmap": osm_url,
        "geo": geo_uri,
    }


def _infer_mode(speed_mph: float) -> str:
    if speed_mph <= 0.8:
        return "stationary"
    if speed_mph <= 5.0:
        return "walking"
    if speed_mph <= 17.0:
        return "cycling"
    return "driving"


class LocationStore:
    """SQLite store for saved places, frequent addresses, travel history, and speed logs."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS places (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                address TEXT NOT NULL DEFAULT '',
                lat REAL,
                lng REAL,
                category TEXT NOT NULL DEFAULT 'other',
                tags_json TEXT NOT NULL DEFAULT '[]',
                context TEXT NOT NULL DEFAULT '',
                contact_id TEXT NOT NULL DEFAULT '',
                contact_name TEXT NOT NULL DEFAULT '',
                visit_count INTEGER NOT NULL DEFAULT 1,
                last_visited_at TEXT NOT NULL DEFAULT '',
                favorite INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                place_id TEXT NOT NULL DEFAULT '',
                place_name TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                lat REAL,
                lng REAL,
                origin_name TEXT NOT NULL DEFAULT '',
                origin_address TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL DEFAULT '',
                duration_min REAL NOT NULL DEFAULT 0,
                distance_mi REAL NOT NULL DEFAULT 0,
                avg_speed_mph REAL NOT NULL DEFAULT 0,
                max_speed_mph REAL NOT NULL DEFAULT 0,
                current_speed_mph REAL NOT NULL DEFAULT 0,
                mode TEXT NOT NULL DEFAULT 'driving',
                traffic_level TEXT NOT NULL DEFAULT 'unknown',
                traffic_note TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                created_at TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ row mappers
    @staticmethod
    def _row_to_place(row: sqlite3.Row) -> dict[str, Any]:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except ValueError:
            tags = []
        lat = row["lat"]
        lng = row["lng"]
        address = row["address"]
        name = row["name"]
        return {
            "id": row["id"],
            "name": name,
            "address": address,
            "lat": lat,
            "lng": lng,
            "category": row["category"],
            "tags": tags,
            "context": row["context"],
            "contact_id": row["contact_id"],
            "contact_name": row["contact_name"],
            "visit_count": int(row["visit_count"] or 0),
            "last_visited_at": row["last_visited_at"],
            "favorite": bool(row["favorite"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "map_links": build_map_links(address or name, lat=lat, lng=lng, label=name),
        }

    @staticmethod
    def _row_to_trip(row: sqlite3.Row) -> dict[str, Any]:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except ValueError:
            tags = []
        lat = row["lat"]
        lng = row["lng"]
        address = row["address"]
        place_name = row["place_name"]
        return {
            "id": row["id"],
            "place_id": row["place_id"],
            "place_name": place_name,
            "address": address,
            "lat": lat,
            "lng": lng,
            "origin_name": row["origin_name"],
            "origin_address": row["origin_address"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "duration_min": round(float(row["duration_min"] or 0.0), 1),
            "distance_mi": round(float(row["distance_mi"] or 0.0), 2),
            "avg_speed_mph": round(float(row["avg_speed_mph"] or 0.0), 1),
            "max_speed_mph": round(float(row["max_speed_mph"] or 0.0), 1),
            "current_speed_mph": round(float(row["current_speed_mph"] or 0.0), 1),
            "mode": row["mode"],
            "traffic_level": row["traffic_level"],
            "traffic_note": row["traffic_note"],
            "tags": tags,
            "notes": row["notes"],
            "source": row["source"],
            "created_at": row["created_at"],
            "map_links": build_map_links(address or place_name, lat=lat, lng=lng, label=place_name or address),
        }

    # ------------------------------------------------------------------ places
    def add_place(
        self,
        *,
        name: str,
        address: str = "",
        lat: float | None = None,
        lng: float | None = None,
        category: str = "other",
        tags: list[str] | str | None = None,
        context: str = "",
        contact_id: str = "",
        contact_name: str = "",
        favorite: bool = False,
        visit_count: int = 1,
    ) -> dict[str, Any]:
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValueError("Place name is required")
        cat = category if category in VALID_PLACE_CATEGORIES else "other"
        now = _now_iso()
        pid = "plc_" + uuid.uuid4().hex[:8]
        norm_tags = normalize_tags(tags, f"{cleaned_name} {context}")
        self._conn.execute(
            """
            INSERT INTO places (
                id, name, address, lat, lng, category, tags_json, context,
                contact_id, contact_name, visit_count, last_visited_at, favorite,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                cleaned_name,
                (address or "").strip(),
                float(lat) if lat is not None else None,
                float(lng) if lng is not None else None,
                cat,
                json.dumps(norm_tags),
                (context or "").strip(),
                (contact_id or "").strip(),
                (contact_name or "").strip(),
                max(0, int(visit_count)),
                now if visit_count > 0 else "",
                1 if favorite else 0,
                now,
                now,
            ),
        )
        self._conn.commit()
        return self.get_place(pid)  # type: ignore[return-value]

    def get_place(self, place_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM places WHERE id = ?", (place_id,)).fetchone()
        return self._row_to_place(row) if row else None

    def update_place(self, place_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        existing = self.get_place(place_id)
        if not existing:
            return None
        updates: dict[str, Any] = {"updated_at": _now_iso()}
        for col in ("name", "address", "context", "contact_id", "contact_name"):
            if col in patch and patch[col] is not None:
                updates[col] = str(patch[col]).strip()
        if "category" in patch and patch["category"] in VALID_PLACE_CATEGORIES:
            updates["category"] = patch["category"]
        if "lat" in patch:
            updates["lat"] = float(patch["lat"]) if patch["lat"] is not None else None
        if "lng" in patch:
            updates["lng"] = float(patch["lng"]) if patch["lng"] is not None else None
        if "favorite" in patch and patch["favorite"] is not None:
            updates["favorite"] = 1 if bool(patch["favorite"]) else 0
        if "tags" in patch:
            updates["tags_json"] = json.dumps(normalize_tags(patch["tags"], str(updates.get("context", existing["context"]))))
        if "visit_count" in patch and patch["visit_count"] is not None:
            updates["visit_count"] = max(0, int(patch["visit_count"]))
        assign = ", ".join(f"{k} = ?" for k in updates)
        vals = [*updates.values(), place_id]
        self._conn.execute(f"UPDATE places SET {assign} WHERE id = ?", vals)
        self._conn.commit()
        return self.get_place(place_id)

    def record_visit(self, place_id: str) -> dict[str, Any] | None:
        existing = self.get_place(place_id)
        if not existing:
            return None
        now = _now_iso()
        self._conn.execute(
            "UPDATE places SET visit_count = visit_count + 1, last_visited_at = ?, updated_at = ? WHERE id = ?",
            (now, now, place_id),
        )
        self._conn.commit()
        return self.get_place(place_id)

    def delete_place(self, place_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM places WHERE id = ?", (place_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_places(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM places ORDER BY favorite DESC, visit_count DESC, updated_at DESC"
        ).fetchall()
        return [self._row_to_place(r) for r in rows]

    # ------------------------------------------------------------------ trips & pings
    def log_trip(
        self,
        *,
        place_id: str = "",
        place_name: str = "",
        address: str = "",
        lat: float | None = None,
        lng: float | None = None,
        origin_name: str = "",
        origin_address: str = "",
        started_at: str = "",
        ended_at: str = "",
        duration_min: float = 0.0,
        distance_mi: float = 0.0,
        avg_speed_mph: float | None = None,
        max_speed_mph: float | None = None,
        current_speed_mph: float = 0.0,
        mode: str = "",
        traffic_level: str = "unknown",
        traffic_note: str = "",
        tags: list[str] | str | None = None,
        notes: str = "",
        source: str = "manual",
    ) -> dict[str, Any]:
        now = _now_iso()
        matched_place: dict[str, Any] | None = self.get_place(place_id) if place_id else None
        if matched_place is None and lat is not None and lng is not None:
            for p in self.list_places():
                if p["lat"] is not None and p["lng"] is not None:
                    if haversine_miles(lat, lng, p["lat"], p["lng"]) <= 0.12:
                        matched_place = p
                        break
        if matched_place is not None:
            place_id = matched_place["id"]
            place_name = place_name or matched_place["name"]
            address = address or matched_place["address"]
            self.record_visit(place_id)

        dur = max(0.0, float(duration_min or 0.0))
        dist = max(0.0, float(distance_mi or 0.0))
        cur_spd = max(0.0, float(current_speed_mph or 0.0))
        if avg_speed_mph is None:
            avg_spd = round((dist / (dur / 60.0)), 1) if dur > 0 and dist > 0 else cur_spd
        else:
            avg_spd = max(0.0, float(avg_speed_mph))
        max_spd = max(avg_spd, cur_spd, float(max_speed_mph or 0.0))
        eff_mode = mode if mode in VALID_MODES else _infer_mode(cur_spd or avg_spd)
        eff_traffic = traffic_level if traffic_level in VALID_TRAFFIC else "unknown"
        tid = "trp_" + uuid.uuid4().hex[:8]
        norm_tags = normalize_tags(tags, f"{place_name} {notes}")

        self._conn.execute(
            """
            INSERT INTO trips (
                id, place_id, place_name, address, lat, lng,
                origin_name, origin_address, started_at, ended_at,
                duration_min, distance_mi, avg_speed_mph, max_speed_mph, current_speed_mph,
                mode, traffic_level, traffic_note, tags_json, notes, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tid,
                place_id,
                (place_name or address or "Pinned location").strip(),
                (address or "").strip(),
                float(lat) if lat is not None else None,
                float(lng) if lng is not None else None,
                (origin_name or "").strip(),
                (origin_address or "").strip(),
                (started_at or now).strip(),
                (ended_at or "").strip(),
                dur,
                dist,
                avg_spd,
                max_spd,
                cur_spd,
                eff_mode,
                eff_traffic,
                (traffic_note or "").strip(),
                json.dumps(norm_tags),
                (notes or "").strip(),
                source,
                now,
            ),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM trips WHERE id = ?", (tid,)).fetchone()
        return self._row_to_trip(row)

    def delete_trip(self, trip_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_trips(self, limit: int = 80) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM trips ORDER BY started_at DESC, created_at DESC LIMIT ?",
            (max(1, min(500, limit)),),
        ).fetchall()
        return [self._row_to_trip(r) for r in rows]

    # ------------------------------------------------------------------ contacts + summary + search
    def contact_locations(self, contacts: list[Any] | None = None) -> list[dict[str, Any]]:
        """Project ContactBook records with addresses into unified location cards."""
        out: list[dict[str, Any]] = []
        if not contacts:
            return out
        saved_by_contact = {p["contact_id"]: p for p in self.list_places() if p.get("contact_id")}
        for c in contacts:
            addresses = list(getattr(c, "addresses", None) or [])
            if not addresses:
                continue
            cid = str(getattr(c, "id", "") or "")
            name = str(getattr(c, "name", "") or "Contact")
            note = str(getattr(c, "note", "") or "")
            org = str(getattr(c, "org", "") or "")
            pinned = saved_by_contact.get(cid)
            tags = list(pinned["tags"]) if pinned else normalize_tags(["#friend"], note)
            if org and not any(t == f"#{re.sub(r'[^a-z0-9]+', '', org.lower())}" for t in tags):
                pass
            primary_addr = addresses[0]
            out.append(
                {
                    "contact_id": cid,
                    "name": name,
                    "addresses": addresses,
                    "primary_address": primary_addr,
                    "org": org,
                    "phones": list(getattr(c, "phones", None) or []),
                    "emails": list(getattr(c, "emails", None) or []),
                    "note": note,
                    "source": str(getattr(c, "source", "") or ""),
                    "tags": tags,
                    "pinned_place_id": pinned["id"] if pinned else None,
                    "lat": pinned["lat"] if pinned else None,
                    "lng": pinned["lng"] if pinned else None,
                    "map_links": build_map_links(
                        primary_addr,
                        lat=pinned["lat"] if pinned else None,
                        lng=pinned["lng"] if pinned else None,
                        label=f"{name} - {primary_addr}",
                    ),
                }
            )
        return out

    def speed_stats(self) -> dict[str, Any]:
        trips = self.list_trips(limit=200)
        driving = [t for t in trips if t["avg_speed_mph"] > 0 or t["current_speed_mph"] > 0]
        latest = trips[0] if trips else None
        cutoff_7d = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        recent = [t for t in trips if (t["started_at"] or t["created_at"]) >= cutoff_7d]
        total_miles = round(sum(t["distance_mi"] for t in trips), 1)
        week_miles = round(sum(t["distance_mi"] for t in recent), 1)
        avg_spd = round(
            sum(t["avg_speed_mph"] for t in driving) / len(driving), 1
        ) if driving else 0.0
        max_spd = round(max((t["max_speed_mph"] for t in driving), default=0.0), 1)
        return {
            "total_trips": len(trips),
            "total_miles": total_miles,
            "week_miles": week_miles,
            "avg_speed_mph": avg_spd,
            "max_speed_mph": max_spd,
            "current_speed_mph": latest["current_speed_mph"] if latest else 0.0,
            "current_mode": latest["mode"] if latest else "stationary",
            "latest_traffic_level": latest["traffic_level"] if latest else "unknown",
            "latest_place": latest["place_name"] if latest else "",
            "latest_at": latest["started_at"] if latest else "",
        }

    def summary(self, contacts: list[Any] | None = None) -> dict[str, Any]:
        places = self.list_places()
        trips = self.list_trips(limit=60)
        contact_locs = self.contact_locations(contacts)
        frequent = sorted(
            [p for p in places if p["visit_count"] > 0 or p["favorite"] or p["category"] in ("home", "work", "frequent")],
            key=lambda p: (not p["favorite"], -p["visit_count"]),
        )
        all_tags: set[str] = set()
        for p in places:
            all_tags.update(p["tags"])
        for t in trips:
            all_tags.update(t["tags"])
        for c in contact_locs:
            all_tags.update(c["tags"])
        return {
            "places": places,
            "frequent_places": frequent[:12],
            "trips": trips,
            "contacts": contact_locs,
            "stats": self.speed_stats(),
            "tags": sorted(all_tags),
        }

    def search(
        self,
        query: str = "",
        *,
        contacts: list[Any] | None = None,
        tag: str = "",
        category: str = "",
    ) -> dict[str, Any]:
        q = (query or "").strip().lower()
        t_filter = ("#" + tag.strip().lstrip("#").lower()) if tag.strip() else ""
        c_filter = category.strip().lower()

        def _matches(text_parts: list[str], item_tags: list[str], item_cat: str = "") -> bool:
            if t_filter and t_filter not in item_tags:
                return False
            if c_filter and item_cat.lower() != c_filter:
                return False
            if not q:
                return True
            hay = " ".join([*text_parts, *item_tags, item_cat]).lower()
            if q.startswith("#") and q in [t.lower() for t in item_tags]:
                return True
            return q in hay

        places = [
            p
            for p in self.list_places()
            if _matches([p["name"], p["address"], p["context"], p["contact_name"]], p["tags"], p["category"])
        ]
        trips = [
            tr
            for tr in self.list_trips(limit=120)
            if _matches(
                [tr["place_name"], tr["address"], tr["origin_name"], tr["origin_address"], tr["notes"], tr["traffic_note"]],
                tr["tags"],
                tr["mode"],
            )
        ]
        contact_hits = [
            cl
            for cl in self.contact_locations(contacts)
            if _matches(
                [cl["name"], *cl["addresses"], cl["org"], cl["note"], *cl["phones"], *cl["emails"]],
                cl["tags"],
                "friend",
            )
        ]
        direct_query = (query or tag or "").strip()
        return {
            "query": direct_query,
            "places": places,
            "contacts": contact_hits,
            "trips": trips,
            "direct_map_links": build_map_links(direct_query) if direct_query else None,
        }


async def estimate_route_and_traffic(
    destination: str,
    origin: str = "",
    *,
    store: LocationStore | None = None,
    contacts: list[Any] | None = None,
) -> dict[str, Any]:
    """Resolve origin/destination via saved places, contacts, or OSM Nominatim + OSRM routing."""
    def _resolve_local(text: str) -> tuple[str, float | None, float | None, str]:
        q = (text or "").strip()
        if not q:
            return ("", None, None, "")
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$", q)
        if m:
            return (q, float(m.group(1)), float(m.group(2)), q)
        if store is not None:
            hits = store.search(q, contacts=contacts)
            if hits["places"]:
                p = hits["places"][0]
                return (p["address"] or p["name"], p["lat"], p["lng"], p["name"])
            if hits["contacts"]:
                c = hits["contacts"][0]
                return (c["primary_address"], c["lat"], c["lng"], c["name"])
        return (q, None, None, q)

    dest_text, d_lat, d_lng, dest_label = _resolve_local(destination)
    orig_text, o_lat, o_lng, orig_label = _resolve_local(origin)
    if not orig_text and store is not None:
        trips = store.list_trips(limit=1)
        if trips and trips[0]["lat"] is not None and trips[0]["lng"] is not None:
            o_lat, o_lng = trips[0]["lat"], trips[0]["lng"]
            orig_text = trips[0]["address"] or trips[0]["place_name"]
            orig_label = orig_text
        else:
            homes = [p for p in store.list_places() if p["category"] == "home"]
            if homes:
                orig_text = homes[0]["address"] or homes[0]["name"]
                o_lat, o_lng = homes[0]["lat"], homes[0]["lng"]
                orig_label = homes[0]["name"]

    headers = {"User-Agent": "MiniMuse/1.0"}
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        async def _geocode(query_str: str) -> tuple[float | None, float | None, str]:
            if not query_str:
                return (None, None, "")
            try:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": query_str, "format": "json", "limit": 1},
                )
                if r.status_code == 200 and isinstance(r.json(), list) and r.json():
                    item = r.json()[0]
                    return (float(item["lat"]), float(item["lon"]), str(item.get("display_name") or query_str))
            except Exception:
                pass
            return (None, None, query_str)

        if d_lat is None and dest_text:
            d_lat, d_lng, resolved_d = await _geocode(dest_text)
            if resolved_d:
                dest_text = resolved_d
        if o_lat is None and orig_text:
            o_lat, o_lng, _ = await _geocode(orig_text)

        distance_mi: float | None = None
        duration_min: float | None = None
        est_speed_mph: float | None = None
        traffic_note = "Free-flow road estimate (open Google Maps or Waze for live incident overlay)."
        if o_lat is not None and o_lng is not None and d_lat is not None and d_lng is not None:
            try:
                route_url = (
                    f"https://router.project-osrm.org/route/v1/driving/"
                    f"{o_lng:.6f},{o_lat:.6f};{d_lng:.6f},{d_lat:.6f}"
                )
                rr = await client.get(route_url, params={"overview": "false"})
                if rr.status_code == 200:
                    routes = rr.json().get("routes") or []
                    if routes:
                        dist_m = float(routes[0].get("distance") or 0.0)
                        dur_s = float(routes[0].get("duration") or 0.0)
                        distance_mi = round(dist_m / 1609.344, 2)
                        duration_min = round(dur_s / 60.0, 1)
                        if dur_s > 0:
                            est_speed_mph = round(distance_mi / (dur_s / 3600.0), 1)
            except Exception:
                pass
            if distance_mi is None:
                distance_mi = round(haversine_miles(o_lat, o_lng, d_lat, d_lng), 2)
                duration_min = round((distance_mi / 30.0) * 60.0, 1)
                est_speed_mph = 30.0

    map_links = build_map_links(dest_text or destination, lat=d_lat, lng=d_lng, label=dest_label)
    if orig_text and dest_text:
        map_links["google_directions"] = (
            f"https://www.google.com/maps/dir/?api=1&origin={quote_plus(orig_text)}&destination={quote_plus(dest_text)}"
        )

    traffic_level = "unknown"
    if est_speed_mph is not None:
        if est_speed_mph >= 42:
            traffic_level = "clear"
        elif est_speed_mph >= 25:
            traffic_level = "light"
        elif est_speed_mph >= 15:
            traffic_level = "moderate"
        else:
            traffic_level = "heavy"

    return {
        "ok": True,
        "origin": {"label": orig_label, "address": orig_text, "lat": o_lat, "lng": o_lng},
        "destination": {"label": dest_label, "address": dest_text, "lat": d_lat, "lng": d_lng},
        "distance_mi": distance_mi,
        "duration_min": duration_min,
        "estimated_speed_mph": est_speed_mph,
        "traffic_level": traffic_level,
        "traffic_note": traffic_note,
        "map_links": map_links,
    }
