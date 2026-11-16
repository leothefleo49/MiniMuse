"""Usage limits the person (or any AI on their behalf) can set: per model or global,
in tokens, requests or dollars, over a day, a week or a month.

A limit warns before it bites (a notice at ``warning_pct`` of the window's spend) and
stops the next turn at 100% (``action = "stop"``) or asks for one-turn permission
(``action = "confirm"``). The ledger (``usage.jsonl``) is the source of truth, so limits
survive restarts and count every run, background ones included.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nanomuse.config import load_app_settings, save_app_settings

DATA_KEY = "usage_limits"
UNITS = ("tokens", "requests", "cost")
WINDOWS = ("day", "week", "month")
ACTIONS = ("warn", "stop", "confirm")


class UsageLimitStore:
    """The list behind the Usage screen and the ``system_admin`` tool."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    # ------------------------------------------------------------------ storage
    def all(self) -> list[dict[str, Any]]:
        data = load_app_settings(self.data_dir)
        rows = data.get(DATA_KEY)
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict) and r.get("id")]

    def save(self, rows: list[dict[str, Any]]) -> None:
        stored = load_app_settings(self.data_dir)
        stored[DATA_KEY] = rows
        save_app_settings(self.data_dir, stored)

    def get(self, limit_id: str) -> dict[str, Any] | None:
        for row in self.all():
            if row.get("id") == limit_id:
                return row
        return None

    # ------------------------------------------------------------------ usage math
    def _window_start(self, window: str) -> datetime:
        now = datetime.now(UTC)
        if window == "week":
            start = now - timedelta(days=now.weekday())
            return start.replace(hour=0, minute=0, second=0, microsecond=0)
        if window == "month":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return start
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _used(self, scope: str, model: str, window: str) -> dict[str, float]:
        """What the ledger says this window already spent, for a model or everything."""
        path = self.data_dir / "usage.jsonl"
        start = self._window_start(window).timestamp()
        used = {"tokens": 0.0, "requests": 0.0, "cost": 0.0}
        try:
            raw = path.read_text("utf-8")
        except OSError:
            return used
        for line in raw.splitlines():
            line = line.strip().lstrip("\x00")
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = str(row.get("ts") or "")
            try:
                when = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                continue
            if when < start:
                continue
            if scope == "model" and str(row.get("model") or "") != model:
                continue
            used["requests"] += 1
            used["tokens"] += float(
                (row.get("prompt_tokens") or 0) + (row.get("completion_tokens") or 0)
            )
            used["cost"] += float(row.get("cost_usd") or 0)
        return used

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, model: str) -> dict[str, Any] | None:
        """The binding limit for the next turn, or None.

        Returns ``{"limit": <row>, "level": "warn"|"stop", "pct": <int>, "used": ...,
        "text": <human sentence>}``. ``stop`` also covers ``confirm``: the caller decides
        whether to ask or refuse. The tightest binding limit wins when two bite at once.
        """
        binding: dict[str, Any] | None = None
        for row in self.all():
            scope = str(row.get("scope") or "global")
            if scope == "model" and str(row.get("model") or "") != model:
                continue
            unit = str(row.get("unit") or "cost")
            if unit not in UNITS:
                continue
            window = str(row.get("window") or "month")
            if window not in WINDOWS:
                continue
            amount = float(row.get("amount") or 0)
            if amount <= 0:
                continue
            used = self._used(scope, model, window)
            spent = float(used.get(unit, 0.0))
            pct = int(round(spent * 100 / amount)) if amount else 0
            warning_pct = int(row.get("warning_pct") or 80)
            action = str(row.get("on_full") or row.get("action") or "stop")
            if action not in ACTIONS:
                action = "stop"
            level = ""
            if pct >= 100:
                level = "stop" if action in ("stop", "confirm") else "warn"
            elif pct >= warning_pct:
                level = "warn"
            if not level:
                continue
            candidate = {
                "limit": row,
                "level": level,
                "pct": pct,
                "used": round(spent, 4),
                "amount": amount,
                "unit": unit,
                "window": window,
                "action": action,
            }
            # the first hard stop wins; otherwise the highest percentage
            if binding is None or (binding["level"] != "stop" and pct > binding["pct"]):
                binding = candidate
        if binding is None:
            return None
        name = str(binding["limit"].get("name") or "usage limit")
        if binding["level"] == "stop":
            binding["text"] = (
                f"{name} reached: {binding['pct']}% of the {binding['window']} "
                f"{binding['unit']} limit."
            )
        else:
            binding["text"] = (
                f"{name} at {binding['pct']}% of the {binding['window']} {binding['unit']} limit."
            )
        return binding


def new_id() -> str:
    import secrets

    return "ul-" + secrets.token_hex(4)
