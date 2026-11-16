"""Tracker tools for MiniMuse (food, water, inventory, workouts, habits, recovery, finance, tasks)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from nanomuse.data_sync import DataSyncManager, configure_data_dir
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment, int_arg, number_arg

DEFAULT_PERSONAL_DB = Path.home() / ".nanomuse" / "personal.db"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _waking_date() -> str:
    """Waking-day boundary rule: if still awake past midnight (before 4:30 AM local),
    logs belong to the previous calendar day."""
    now = datetime.now().astimezone()
    if now.hour < 4 or (now.hour == 4 and now.minute < 30):
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


def get_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_PERSONAL_DB
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


class OFATrackerTool(BaseTool):
    name: str = "ofa_tracker"
    description: str = (
        "Personal tracker (food, water, inventory, workouts, habits, recovery, finance, tasks). "
        "Use for kitchen inventory questions and daily logging. Actions: "
        "list_inventory (filter by location/query), update_inventory, dashboard_summary, get_day (date), "
        "log_food (name+calories/protein/carbs/fat/fiber, slot, fluid_oz), log_water (fluid_oz), "
        "log_preset/list_presets (meal presets), log_workout (kind run|walk|treadmill|climb|pushups|strength, duration_min, distance_miles, reps), "
        "log_habit (habit_key), get_sobriety, log_sobriety (kind slip|urge|checkin|win), log_health, "
        "get_finance, log_finance, list_tasks/upsert_task/complete_task, list_stickies/upsert_sticky, "
        "sql_query (read-only SELECT)."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "dashboard_summary",
                    "get_day",
                    "log_food",
                    "log_water",
                    "log_preset",
                    "list_presets",
                    "list_inventory",
                    "update_inventory",
                    "log_workout",
                    "log_habit",
                    "get_sobriety",
                    "log_sobriety",
                    "log_health",
                    "get_finance",
                    "log_finance",
                    "list_tasks",
                    "upsert_task",
                    "complete_task",
                    "list_stickies",
                    "upsert_sticky",
                    "sql_query",
                ],
            },
            "date": {"type": "string", "description": "YYYY-MM-DD (defaults to current waking day)"},
            "name": {"type": "string", "description": "Food name, item name, transaction name, or task/sticky title"},
            "kind": {"type": "string", "description": "Entry kind (e.g. food|water, run|climb|pushups|strength, slip|urge|checkin, expense|income)"},
            "slot": {"type": "string", "description": "breakfast | lunch | dinner | snack | side"},
            "calories": {"type": "number"},
            "protein": {"type": "number"},
            "carbs": {"type": "number"},
            "fat": {"type": "number"},
            "fiber": {"type": "number"},
            "fluid_oz": {"type": "number", "description": "Fluid ounces (e.g. 32 for a 32 oz water bottle)"},
            "preset_id": {"type": "string", "description": "Preset ID"},
            "item_key": {"type": "string"},
            "location": {"type": "string", "description": "fridge | freezer | pantry | counter"},
            "quantity": {"type": "number"},
            "unit": {"type": "string"},
            "ripeness_stage": {"type": "string"},
            "duration_min": {"type": "number"},
            "distance_miles": {"type": "number"},
            "reps": {"type": "integer"},
            "habit_key": {"type": "string", "description": "meds | reading | journal | exercise | pushups | climb | run"},
            "urge_level": {"type": "integer", "description": "0-10 urge intensity"},
            "trigger_note": {"type": "string"},
            "value": {"type": "number"},
            "amount": {"type": "number"},
            "category": {"type": "string"},
            "priority": {"type": "string", "description": "low | med | high"},
            "status": {"type": "string", "description": "open | done"},
            "id": {"type": "integer", "description": "Row ID to update/complete"},
            "note": {"type": "string"},
            "query": {"type": "string", "description": "Search filter or read-only SQL SELECT query"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    db_path: Path = DEFAULT_PERSONAL_DB
    bus: Any = None  # Optional EventBus to notify live UI clients

    def model_post_init(self, __context: Any) -> None:
        # The sync engine writes through to the same data directory this tool reads:
        # follow the configured db_path instead of the module's default directory.
        configure_data_dir(self.db_path.parent)

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        act = str(args.get("action") or "")
        name = str(args.get("name") or args.get("preset_id") or args.get("habit_key") or "")
        return CallAssessment(
            risk=RiskLevel.SAFE,
            reads_private_data=True,
            summary=f"ofa_tracker.{act}" + (f"({name[:50]})" if name else ""),
        )

    def _notify_ui(self, event_kind: str, payload: dict[str, Any] | None = None) -> None:
        if self.bus is not None:
            try:
                self.bus.publish({"kind": "ofa_update", "event": event_kind, **(payload or {})})
            except Exception:
                pass

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        day = str(kwargs.get("date") or "").strip() or _waking_date()
        conn = get_db(self.db_path)
        try:
            if action == "dashboard_summary":
                return self._dashboard_summary(conn, day)
            if action == "get_day":
                return self._get_day(conn, day)
            if action == "log_food":
                return self._log_food(conn, day, kwargs)
            if action == "log_water":
                return self._log_water(conn, day, kwargs)
            if action == "log_preset":
                return self._log_preset(conn, day, kwargs)
            if action == "list_presets":
                return self._list_presets(conn, kwargs)
            if action == "list_inventory":
                return self._list_inventory(conn, kwargs)
            if action == "update_inventory":
                return self._update_inventory(conn, kwargs)
            if action == "log_workout":
                return self._log_workout(conn, day, kwargs)
            if action == "log_habit":
                return self._log_habit(conn, day, kwargs)
            if action == "get_sobriety":
                return self._get_sobriety(conn)
            if action == "log_sobriety":
                return self._log_sobriety(conn, day, kwargs)
            if action == "log_health":
                return self._log_health(conn, day, kwargs)
            if action == "get_finance":
                return self._get_finance(conn)
            if action == "log_finance":
                return self._log_finance(conn, day, kwargs)
            if action == "list_tasks":
                return self._list_tasks(conn, kwargs)
            if action == "upsert_task":
                return self._upsert_task(conn, kwargs)
            if action == "complete_task":
                return self._complete_task(conn, kwargs)
            if action == "list_stickies":
                return self._list_stickies(conn)
            if action == "upsert_sticky":
                return self._upsert_sticky(conn, kwargs)
            if action == "sql_query":
                return self._sql_query(conn, str(kwargs.get("query") or ""))
            return ToolResult.fail(f"Unknown action: {action}")
        finally:
            conn.close()

    def _dashboard_summary(self, conn: sqlite3.Connection, day: str) -> ToolResult:
        # Food & water totals for `day`
        f_rows = conn.execute("SELECT * FROM food_logs WHERE date = ? ORDER BY id", (day,)).fetchall()
        cal = sum(r["calories"] for r in f_rows if r["kind"] != "water")
        prot = sum(r["protein"] for r in f_rows if r["kind"] != "water")
        carbs = sum(r["carbs"] for r in f_rows if r["kind"] != "water")
        fat = sum(r["fat"] for r in f_rows if r["kind"] != "water")
        fib = sum(r["fiber"] for r in f_rows if r["kind"] != "water")
        water_oz = sum(r["fluid_oz"] for r in f_rows if r["kind"] == "water")
        total_fluid_oz = sum(r["fluid_oz"] for r in f_rows)

        # Weekly workout totals (last 7 days inclusive)
        d_obj = date.fromisoformat(day[:10])
        week_start = (d_obj - timedelta(days=d_obj.weekday())).isoformat()  # Monday start
        w_rows = conn.execute("SELECT * FROM workout_logs WHERE date >= ? ORDER BY date DESC", (week_start,)).fetchall()
        run_miles = sum(float(r["distance_miles"] or 0) for r in w_rows if r["kind"] in ("run", "treadmill"))
        climbs = sum(1 for r in w_rows if r["kind"] == "climb")
        week_pushups = sum(int(r["reps"] or 0) for r in w_rows if r["kind"] == "pushups")
        today_pushups = sum(int(r["reps"] or 0) for r in w_rows if r["kind"] == "pushups" and r["date"] == day)

        # Recovery streak
        st_row = conn.execute("SELECT value_json FROM app_state WHERE key = 'streakStart'").fetchone()
        streak_iso = json.loads(st_row["value_json"]) if st_row else ""
        days_clean, hours_clean = 0, 0.0
        if streak_iso:
            try:
                st_dt = datetime.fromisoformat(streak_iso.replace("Z", "+00:00")).astimezone()
                days_clean = (datetime.now().astimezone().date() - st_dt.date()).days
                hours_clean = round((datetime.now(UTC) - st_dt.astimezone(UTC)).total_seconds() / 3600, 1)
            except Exception:
                days_clean, hours_clean = 0, 0.0

        # Latest sleep & synced daily
        sd = conn.execute("SELECT * FROM synced_daily ORDER BY date DESC LIMIT 1").fetchone()
        bal_row = conn.execute("SELECT value_json FROM app_state WHERE key = 'financeBalances'").fetchone()
        balances = json.loads(bal_row["value_json"]) if bal_row else {}
        sync_row = conn.execute("SELECT value_json FROM app_state WHERE key = 'syncStatus'").fetchone()
        sync_st = json.loads(sync_row["value_json"]) if sync_row else {}
        open_tasks = conn.execute("SELECT id, title, priority, due_date FROM tasks WHERE status != 'done' ORDER BY id").fetchall()

        out = {
            "waking_date": day,
            "week_start_monday": week_start,
            "nutrition_today": {
                "calories": round(cal),
                "target_calories": 2250,
                "protein_g": round(prot),
                "target_protein_g": 175,
                "carbs_g": round(carbs),
                "fat_g": round(fat),
                "fiber_g": round(fib),
                "water_oz": round(water_oz),
                "target_water_oz": 192,
                "total_fluid_oz": round(total_fluid_oz),
                "entries_count": len(f_rows),
            },
            "workouts_this_week": {
                "run_miles": round(run_miles, 2),
                "target_run_miles": 7.0,
                "climbs_count": climbs,
                "target_climbs": 2,
                "pushups_week": week_pushups,
                "target_pushups_week": 75,
                "pushups_today": today_pushups,
                "target_pushups_today": 15,
            },
            "sobriety": {
                "streak_start": streak_iso,
                "days_count": days_clean,
                "hours_elapsed": hours_clean,
            },
            "latest_health_connect": dict(sd) if sd else None,
            "finance": {
                "balances": balances,
                "tithing_owed": sync_st.get("financeTithingOwed", 0),
            },
            "open_tasks_count": len(open_tasks),
            "open_tasks": [dict(r) for r in open_tasks[:10]],
        }
        return ToolResult(output=json.dumps(out, ensure_ascii=False, indent=2))

    def _get_day(self, conn: sqlite3.Connection, day: str) -> ToolResult:
        foods = [dict(r) for r in conn.execute("SELECT id, kind, slot, name, calories, protein, carbs, fat, fiber, fluid_oz, note, created_at FROM food_logs WHERE date = ? ORDER BY id", (day,)).fetchall()]
        workouts = [dict(r) for r in conn.execute("SELECT id, kind, duration_min, distance_miles, reps, note FROM workout_logs WHERE date = ? ORDER BY id", (day,)).fetchall()]
        habits = [dict(r) for r in conn.execute("SELECT hl.id, h.key, h.title, hl.done, hl.value, hl.note FROM habit_logs hl JOIN habits h ON h.id = hl.habit_id WHERE hl.date = ?", (day,)).fetchall()]
        sleep = [dict(r) for r in conn.execute("SELECT * FROM sleep_logs WHERE date = ?", (day,)).fetchall()]
        synced = conn.execute("SELECT * FROM synced_daily WHERE date = ?", (day,)).fetchone()
        return ToolResult(
            output=json.dumps(
                {
                    "date": day,
                    "food_logs": foods,
                    "workout_logs": workouts,
                    "habit_logs": habits,
                    "sleep_logs": sleep,
                    "synced_daily": dict(synced) if synced else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def _log_food(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        name = str(kwargs.get("name") or "").strip()
        if not name:
            return ToolResult.fail("`log_food` requires `name`.")
        cal = number_arg(kwargs.get("calories"), 0, 0, 10000)
        prot = number_arg(kwargs.get("protein"), 0, 0, 1000)
        carbs = number_arg(kwargs.get("carbs"), 0, 0, 1000)
        fat = number_arg(kwargs.get("fat"), 0, 0, 1000)
        fib = number_arg(kwargs.get("fiber"), 0, 0, 500)
        fluid = number_arg(kwargs.get("fluid_oz"), 0, 0, 500)
        slot = str(kwargs.get("slot") or "snack").strip().lower()
        note = str(kwargs.get("note") or "").strip()
        fid = DataSyncManager.log_food(
            date_str=day,
            name=name,
            calories=round(cal),
            protein=round(prot),
            carbs=round(carbs),
            fat=round(fat),
            fiber=round(fib),
            fluid_oz=round(fluid),
            slot=slot,
            note=note,
            source="muse",
        )
        self._notify_ui("food_logged", {"date": day, "id": fid})
        return self._get_day(conn, day)

    def _log_water(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        oz = number_arg(kwargs.get("fluid_oz"), 32, 1, 256)
        name = str(kwargs.get("name") or f"{int(oz)} oz water").strip()
        note = str(kwargs.get("note") or "").strip()
        wid = DataSyncManager.log_water(date_str=day, fluid_oz=round(oz), source="muse")
        self._notify_ui("water_logged", {"date": day, "id": wid, "fluid_oz": oz})
        rows = conn.execute("SELECT SUM(fluid_oz) as w FROM food_logs WHERE date = ? AND kind = 'water'", (day,)).fetchone()
        total_w = rows["w"] or 0
        return ToolResult(output=f"Logged {int(oz)} oz water ({name}) for {day}. Today's water total: {int(total_w)} / 192 oz (1.5 gal).")

    def _log_preset(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        pid = str(kwargs.get("preset_id") or "").strip()
        qname = str(kwargs.get("name") or "").strip()
        row = None
        if pid:
            row = conn.execute("SELECT * FROM meal_presets WHERE id = ?", (pid,)).fetchone()
        if not row and qname:
            row = conn.execute("SELECT * FROM meal_presets WHERE LOWER(name) LIKE ?", (f"%{qname.lower()}%",)).fetchone()
        if not row:
            return ToolResult.fail(f"No meal preset found matching id={pid!r} name={qname!r}. Call `list_presets` first.")
        fluid = 32 if row["id"] == "s10" else (16 if "shake" in row["name"].lower() else 0)
        kind = "water" if row["id"] == "s10" else "food"
        fid = DataSyncManager.log_food(
            date_str=day,
            name=row["name"],
            calories=row["calories"],
            protein=row["protein"],
            carbs=row["carbs"],
            fat=row["fat"],
            fiber=row["fiber"],
            fluid_oz=fluid,
            slot=row["slot"],
            note=str(kwargs.get("note") or f"Preset {row['id']}"),
            source="preset",
        )
        self._notify_ui("food_logged", {"date": day, "id": fid})
        return self._get_day(conn, day)

    def _list_presets(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        slot = str(kwargs.get("slot") or "").strip().lower()
        if slot:
            rows = conn.execute("SELECT * FROM meal_presets WHERE slot = ? ORDER BY id", (slot,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM meal_presets ORDER BY id").fetchall()
        return ToolResult(output=json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

    def _list_inventory(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        loc = str(kwargs.get("location") or "").strip().lower()
        q = str(kwargs.get("query") or "").strip().lower()
        sql = "SELECT id, item_key, name, location, quantity, unit, ripeness_stage, expires_day, notes FROM kitchen_inventory"
        params: list[Any] = []
        conds: list[str] = []
        if loc:
            conds.append("LOWER(location) = ?")
            params.append(loc)
        if q:
            conds.append("(LOWER(name) LIKE ? OR LOWER(item_key) LIKE ? OR LOWER(notes) LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY location, name"
        rows = conn.execute(sql, params).fetchall()
        return ToolResult(output=json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

    def _update_inventory(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        key = str(kwargs.get("item_key") or kwargs.get("name") or "").strip().lower().replace(" ", "-")
        name = str(kwargs.get("name") or key).strip()
        if not key:
            return ToolResult.fail("Provide `item_key` or `name` to update inventory.")
        existing = conn.execute(
            "SELECT * FROM kitchen_inventory WHERE LOWER(item_key) = ? OR LOWER(name) = ?",
            (key, name.lower()),
        ).fetchone()
        if existing:
            qty = kwargs.get("quantity") if kwargs.get("quantity") is not None else existing["quantity"]
            unit = str(kwargs.get("unit") or existing["unit"])
            loc = str(kwargs.get("location") or existing["location"])
            ripe = str(kwargs.get("ripeness_stage") or existing["ripeness_stage"])
            note = str(kwargs.get("note") if kwargs.get("note") is not None else existing["notes"])
            conn.execute(
                """
                UPDATE kitchen_inventory
                SET quantity = ?, unit = ?, location = ?, ripeness_stage = ?, quantity_confirmed = 1, notes = ?
                WHERE id = ?
                """,
                (float(qty), unit, loc, ripe, note, existing["id"]),
            )
            conn.commit()
            self._notify_ui("inventory_updated", {"id": existing["id"]})
            return ToolResult(output=f"Updated inventory '{existing['name']}': {qty} {unit} ({loc}, {ripe}).")
        else:
            qty = float(kwargs.get("quantity") or 1)
            unit = str(kwargs.get("unit") or "count")
            loc = str(kwargs.get("location") or "pantry")
            ripe = str(kwargs.get("ripeness_stage") or "not_applicable")
            note = str(kwargs.get("note") or "")
            cur = conn.execute(
                """
                INSERT INTO kitchen_inventory (item_key, name, location, quantity, unit, quantity_confirmed, ripeness_stage, notes)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (key, name, loc, qty, unit, ripe, note),
            )
            conn.commit()
            self._notify_ui("inventory_updated", {"id": cur.lastrowid})
            return ToolResult(output=f"Added inventory '{name}': {qty} {unit} ({loc}).")

    def _log_workout(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        kind = str(kwargs.get("kind") or "workout").strip().lower()
        dur = kwargs.get("duration_min")
        dist = kwargs.get("distance_miles")
        reps = kwargs.get("reps")
        note = str(kwargs.get("note") or "").strip()
        cur = conn.execute(
            """
            INSERT INTO workout_logs (date, kind, duration_min, distance_miles, reps, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (day, kind, dur, dist, reps, note),
        )
        conn.commit()
        self._notify_ui("workout_logged", {"date": day, "id": cur.lastrowid})
        return ToolResult(output=f"Logged workout [{kind}] on {day} (duration={dur}m, miles={dist}, reps={reps}). Note: {note}")

    def _log_habit(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        hkey = str(kwargs.get("habit_key") or kwargs.get("name") or "").strip().lower()
        hid = kwargs.get("id")
        hrow = None
        if hid:
            hrow = conn.execute("SELECT * FROM habits WHERE id = ?", (hid,)).fetchone()
        if not hrow and hkey:
            hrow = conn.execute("SELECT * FROM habits WHERE LOWER(key) LIKE ? OR LOWER(title) LIKE ?", (f"%{hkey}%", f"%{hkey}%")).fetchone()
        if not hrow:
            all_h = [f"{r['id']}:{r['key']} ({r['title']})" for r in conn.execute("SELECT * FROM habits").fetchall()]
            return ToolResult.fail(f"Habit not found for {hkey!r}. Available habits: {', '.join(all_h)}")
        val = kwargs.get("value")
        note = str(kwargs.get("note") or "").strip()
        DataSyncManager.log_habit(hrow["id"], day, val or 1.0, True, note)
        self._notify_ui("habit_logged", {"date": day, "habit_id": hrow["id"]})
        return ToolResult(output=f"Marked habit '{hrow['title']}' ({hrow['key']}) DONE for {day}.")

    def _get_sobriety(self, conn: sqlite3.Connection) -> ToolResult:
        st_row = conn.execute("SELECT value_json FROM app_state WHERE key = 'streakStart'").fetchone()
        streak_iso = json.loads(st_row["value_json"]) if st_row else ""
        days_clean, hours_clean = 0, 0.0
        streak_local = ""
        if streak_iso:
            st_dt = datetime.fromisoformat(streak_iso.replace("Z", "+00:00")).astimezone()
            now_local = datetime.now().astimezone()
            days_clean = (now_local.date() - st_dt.date()).days
            hours_clean = round((datetime.now(UTC) - st_dt.astimezone(UTC)).total_seconds() / 3600, 1)
            streak_local = st_dt.strftime("%Y-%m-%d %I:%M %p %Z")
        logs = [dict(r) for r in conn.execute("SELECT id, date, occurred_at, kind, urge_level, trigger_note, note FROM sobriety_logs ORDER BY id DESC LIMIT 15").fetchall()]
        return ToolResult(
            output=json.dumps(
                {
                    "streak_start_iso": streak_iso,
                    "streak_start_local": streak_local,
                    "current_day_count": days_clean,
                    "hours_clean": hours_clean,
                    "recent_logs": logs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def _log_sobriety(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        kind = str(kwargs.get("kind") or "checkin").strip().lower()
        urge = int_arg(kwargs.get("urge_level"), 0, 0, 10)
        trig = str(kwargs.get("trigger_note") or "").strip()
        note = str(kwargs.get("note") or "").strip()
        now = _now_iso()
        cur = conn.execute(
            """
            INSERT INTO sobriety_logs (date, occurred_at, kind, urge_level, trigger_note, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (day, now, kind, urge, trig, note),
        )
        if kind in ("slip", "relapse", "reset"):
            for k, val in [("streakStart", now), ("streakStartAt", now), ("streakStartDate", day)]:
                conn.execute(
                    "INSERT OR REPLACE INTO app_state (key, value_json, updated_at) VALUES (?, ?, ?)",
                    (k, json.dumps(val), now),
                )
            # Also append to sobriety_log.md
            sob_md = self.db_path.parent / "muse_memory" / "personal" / "sobriety_log.md"
            if sob_md.exists():
                try:
                    txt = sob_md.read_text("utf-8")
                    local_str = datetime.now().astimezone().strftime("%b %d, %Y (~%I:%M %p %Z)")
                    txt = re.sub(
                        r"- \*\*Current streak:\*\* .*",
                        f"- **Current streak:** restarted {local_str}. (Day 0 = {day}.)\n- {local_str}: slip logged — {note or 'Counter reset, zero judgment.'}",
                        txt,
                        count=1,
                    )
                    sob_md.write_text(txt, "utf-8")
                except OSError:
                    pass
        conn.commit()
        self._notify_ui("sobriety_logged", {"kind": kind, "id": cur.lastrowid})
        return self._get_sobriety(conn)

    def _log_health(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        kind = str(kwargs.get("kind") or "symptom").strip().lower()
        val = kwargs.get("value")
        unit = str(kwargs.get("unit") or ("lb" if kind == "weight" else "")).strip()
        note = str(kwargs.get("note") or "").strip()
        cur = conn.execute(
            "INSERT INTO health_entries (date, kind, value, unit, note) VALUES (?, ?, ?, ?, ?)",
            (day, kind, val, unit, note),
        )
        conn.commit()
        self._notify_ui("health_logged", {"date": day, "id": cur.lastrowid})
        return ToolResult(output=f"Logged health entry [{kind}] on {day}: value={val} {unit}, note={note}")

    def _get_finance(self, conn: sqlite3.Connection) -> ToolResult:
        bal_row = conn.execute("SELECT value_json FROM app_state WHERE key = 'financeBalances'").fetchone()
        balances = json.loads(bal_row["value_json"]) if bal_row else {}
        sync_row = conn.execute("SELECT value_json FROM app_state WHERE key = 'syncStatus'").fetchone()
        sync_st = json.loads(sync_row["value_json"]) if sync_row else {}
        txs = [dict(r) for r in conn.execute("SELECT id, date, name, amount, kind, category, note FROM finance_transactions ORDER BY date DESC, id DESC LIMIT 30").fetchall()]
        rec = [dict(r) for r in conn.execute("SELECT id, name, category, cadence, effective_estimate, monthly_equivalent, note FROM recurring_payments WHERE status = 'active'").fetchall()]
        tith = [dict(r) for r in conn.execute("SELECT * FROM tithing_payments ORDER BY date DESC").fetchall()]
        return ToolResult(
            output=json.dumps(
                {
                    "balances": balances,
                    "tithing_owed": sync_st.get("financeTithingOwed", 0),
                    "tithing_payments": tith,
                    "recurring_payments": rec,
                    "recent_transactions": txs,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def _log_finance(self, conn: sqlite3.Connection, day: str, kwargs: dict[str, Any]) -> ToolResult:
        kind = str(kwargs.get("kind") or "expense").strip().lower()
        name = str(kwargs.get("name") or "Transaction").strip()
        amt = number_arg(kwargs.get("amount"), 0, 0, 1_000_000)
        cat = str(kwargs.get("category") or "General").strip()
        note = str(kwargs.get("note") or "").strip()
        if kind == "tithing":
            cur = conn.execute(
                "INSERT INTO tithing_payments (date, amount, note) VALUES (?, ?, ?)",
                (day, amt, note or name),
            )
        else:
            cur = conn.execute(
                "INSERT INTO finance_transactions (date, name, amount, kind, category, origin, note) VALUES (?, ?, ?, ?, ?, 'muse', ?)",
                (day, name, amt, kind, cat, note),
            )
        conn.commit()
        self._notify_ui("finance_logged", {"date": day, "id": cur.lastrowid})
        return ToolResult(output=f"Logged finance [{kind}] {name}: ${amt:.2f} on {day}.")

    def _list_tasks(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        st = str(kwargs.get("status") or "").strip().lower()
        if st:
            rows = conn.execute("SELECT id, title, status, priority, due_date, list_name, notes FROM tasks WHERE status = ? ORDER BY id", (st,)).fetchall()
        else:
            rows = conn.execute("SELECT id, title, status, priority, due_date, list_name, notes FROM tasks ORDER BY status = 'open' DESC, id DESC").fetchall()
        return ToolResult(output=json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

    def _upsert_task(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        tid = kwargs.get("id")
        title = str(kwargs.get("name") or "").strip()
        prio = str(kwargs.get("priority") or "med").strip()
        due = str(kwargs.get("date") or "").strip()
        note = str(kwargs.get("note") or "").strip()
        if tid:
            conn.execute(
                "UPDATE tasks SET title = COALESCE(NULLIF(?, ''), title), priority = ?, due_date = ?, notes = ? WHERE id = ?",
                (title, prio, due, note, tid),
            )
            conn.commit()
            self._notify_ui("task_updated", {"id": tid})
            return ToolResult(output=f"Updated task #{tid}.")
        if not title:
            return ToolResult.fail("Provide `name` (task title) to create a task.")
        cur = conn.execute(
            "INSERT INTO tasks (title, status, priority, due_date, list_name, notes) VALUES (?, 'open', ?, ?, 'General', ?)",
            (title, prio, due, note),
        )
        conn.commit()
        self._notify_ui("task_created", {"id": cur.lastrowid})
        return ToolResult(output=f"Created task #{cur.lastrowid}: {title}")

    def _complete_task(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        tid = kwargs.get("id")
        if not tid:
            return ToolResult.fail("Provide task `id` to mark done.")
        conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (tid,))
        conn.commit()
        self._notify_ui("task_completed", {"id": tid})
        return ToolResult(output=f"Marked task #{tid} as done.")

    def _list_stickies(self, conn: sqlite3.Connection) -> ToolResult:
        rows = conn.execute("SELECT id, title, body, category, color, pinned FROM sticky_notes ORDER BY pinned DESC, id").fetchall()
        return ToolResult(output=json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))

    def _upsert_sticky(self, conn: sqlite3.Connection, kwargs: dict[str, Any]) -> ToolResult:
        sid = kwargs.get("id")
        title = str(kwargs.get("name") or "Note").strip()
        body = str(kwargs.get("note") or "").strip()
        cat = str(kwargs.get("category") or "general").strip()
        if sid:
            conn.execute(
                "UPDATE sticky_notes SET title = ?, body = ?, category = ? WHERE id = ?",
                (title, body, cat, sid),
            )
            conn.commit()
            self._notify_ui("sticky_updated", {"id": sid})
            return ToolResult(output=f"Updated sticky note #{sid}: {title}")
        cur = conn.execute(
            "INSERT INTO sticky_notes (title, body, category, color, pinned) VALUES (?, ?, ?, 'yellow', 1)",
            (title, body, cat),
        )
        conn.commit()
        self._notify_ui("sticky_created", {"id": cur.lastrowid})
        return ToolResult(output=f"Created sticky note #{cur.lastrowid}: {title}")

    def _sql_query(self, conn: sqlite3.Connection, sql: str) -> ToolResult:
        sql = sql.strip()
        if not sql.upper().startswith(("SELECT", "PRAGMA", "WITH")):
            return ToolResult.fail("`sql_query` only accepts read-only SELECT / PRAGMA / WITH statements.")
        try:
            rows = conn.execute(sql).fetchall()
        except Exception as exc:  # noqa: BLE001 - a bad guess must fail gracefully
            cols = ""
            try:
                tables = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' "
                    "AND name NOT LIKE '%_data' AND name NOT LIKE '%_idx' "
                    "AND name NOT LIKE '%_content' AND name NOT LIKE '%_config' AND name NOT LIKE '%_docsize'"
                ).fetchall()
                cols = " Tables: " + ", ".join(r["name"] for r in tables) + "."
            except Exception:  # noqa: BLE001
                pass
            return ToolResult.fail(
                f"SQL error: {type(exc).__name__}: {exc}. Check the table and column "
                "names before retrying (common ones: food_logs, workout_logs, habit_logs, "
                "habits, sleep_logs, sobriety_logs, finance_transactions, meal_presets, "
                f"kitchen_inventory, tasks).{cols}"
            )
        return ToolResult(output=json.dumps([dict(r) for r in rows[:200]], ensure_ascii=False, indent=2))
