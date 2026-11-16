"""Local writable calendar events store merged alongside external .ics & Google feeds."""

from __future__ import annotations

import builtins
import json
import re
import uuid
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any

from nanomuse.calendar.ics import Event
from nanomuse.private import keep_private

_TAG_RE = re.compile(r"#([A-Za-z0-9_-]{1,32})")


def _normalize_tags(raw: list[str] | str | None, extra_text: str = "") -> list[str]:
    tokens: list[str] = []
    if isinstance(raw, str):
        tokens.extend(re.split(r"[\s,]+", raw.strip()))
    elif isinstance(raw, list):
        tokens.extend(str(t).strip() for t in raw)
    if extra_text:
        tokens.extend(f"#{m}" for m in _TAG_RE.findall(extra_text))
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        clean = re.sub(r"[^a-z0-9_-]+", "", t.lstrip("#").lower())
        if clean:
            tag = f"#{clean}"
            if tag not in seen:
                seen.add(tag)
                out.append(tag)
    return out[:15]


def _parse_iso(value: str, tz: tzinfo) -> tuple[datetime, bool]:
    s = (value or "").strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.replace(tzinfo=tz), True
    s_norm = s[:-1] + "+00:00" if s.endswith("Z") else s
    dt = datetime.fromisoformat(s_norm)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt, False


class LocalCalendarStore:
    """Persistent store for events created/edited directly inside MiniMuse."""

    def __init__(self, path: Path | str, tz: tzinfo | None = None):
        self.path = Path(path)
        self.tz: tzinfo = tz or datetime.now().astimezone().tzinfo or UTC
        self._items: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            rows = raw.get("events") if isinstance(raw, dict) else raw
            if isinstance(rows, list):
                for r in rows:
                    if isinstance(r, dict) and r.get("uid"):
                        self._items[str(r["uid"])] = r
        except (OSError, ValueError):
            self._items = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "events": list(self._items.values())}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        keep_private(self.path)

    def list(self) -> list[dict[str, Any]]:
        return sorted(self._items.values(), key=lambda e: str(e.get("start") or ""))

    def get(self, uid: str) -> dict[str, Any] | None:
        return self._items.get(uid)

    def create(
        self,
        *,
        summary: str,
        start: str,
        end: str = "",
        all_day: bool | None = None,
        location: str = "",
        description: str = "",
        calendar: str = "Personal",
        tags: builtins.list[str] | str | None = None,
        contact_name: str = "",
        rrule: str = "",
    ) -> dict[str, Any]:
        title = (summary or "").strip()
        if not title or not (start or "").strip():
            raise ValueError("Calendar event requires a title and start time")
        start_dt, parsed_all_day = _parse_iso(start, self.tz)
        eff_all_day = parsed_all_day if all_day is None else bool(all_day)
        if end.strip():
            end_dt, end_all_day = _parse_iso(end, self.tz)
            if eff_all_day and end_all_day and end_dt <= start_dt:
                from datetime import timedelta

                end_dt = start_dt + timedelta(days=1)
        else:
            from datetime import timedelta

            end_dt = start_dt + (timedelta(days=1) if eff_all_day else timedelta(hours=1))
        if end_dt <= start_dt:
            raise ValueError("End time must be after start time")

        uid = f"local_{uuid.uuid4().hex[:10]}@minimuse"
        item = {
            "uid": uid,
            "summary": title,
            "start": start_dt.date().isoformat() if eff_all_day else start_dt.isoformat(timespec="minutes"),
            "end": end_dt.date().isoformat() if eff_all_day else end_dt.isoformat(timespec="minutes"),
            "all_day": eff_all_day,
            "location": (location or "").strip(),
            "description": (description or "").strip(),
            "calendar": (calendar or "Personal").strip(),
            "tags": _normalize_tags(tags, f"{title} {description}"),
            "contact_name": (contact_name or "").strip(),
            "rrule": (rrule or "").strip(),
            "editable": True,
        }
        self._items[uid] = item
        self._save()
        return dict(item)

    def update(self, uid: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        item = self._items.get(uid)
        if not item:
            return None
        for col in ("summary", "location", "description", "calendar", "contact_name", "rrule"):
            if col in patch and patch[col] is not None:
                item[col] = str(patch[col]).strip()
        if "all_day" in patch and patch["all_day"] is not None:
            item["all_day"] = bool(patch["all_day"])
        if "start" in patch and patch["start"] is not None:
            dt, _ = _parse_iso(str(patch["start"]), self.tz)
            item["start"] = dt.date().isoformat() if item["all_day"] else dt.isoformat(timespec="minutes")
        if "end" in patch and patch["end"] is not None:
            dt, _ = _parse_iso(str(patch["end"]), self.tz)
            item["end"] = dt.date().isoformat() if item["all_day"] else dt.isoformat(timespec="minutes")
        if "tags" in patch:
            item["tags"] = _normalize_tags(patch["tags"], f"{item['summary']} {item['description']}")
        self._save()
        return dict(item)

    def delete(self, uid: str) -> bool:
        if uid not in self._items:
            return False
        del self._items[uid]
        self._save()
        return True

    def as_events(self, tz: tzinfo | None = None) -> builtins.list[Event]:
        eff_tz = tz or self.tz
        events: list[Event] = []
        for row in self._items.values():
            try:
                start_dt, parsed_all_day = _parse_iso(str(row["start"]), eff_tz)
                end_dt, _ = _parse_iso(str(row["end"]), eff_tz)
                all_day = bool(row.get("all_day", parsed_all_day))
                if all_day and end_dt <= start_dt:
                    from datetime import timedelta

                    end_dt = start_dt + timedelta(days=1)
                desc = str(row.get("description") or "")
                tags = row.get("tags") or []
                if tags:
                    tag_line = " ".join(tags)
                    if tag_line not in desc:
                        desc = f"{desc}\n{tag_line}".strip()
                events.append(
                    Event(
                        uid=str(row["uid"]),
                        summary=str(row["summary"]),
                        start=start_dt,
                        end=end_dt,
                        all_day=all_day,
                        location=str(row.get("location") or ""),
                        description=desc,
                        rrule=str(row.get("rrule") or ""),
                        calendar=str(row.get("calendar") or "Personal"),
                    )
                )
            except Exception:
                continue
        return events
