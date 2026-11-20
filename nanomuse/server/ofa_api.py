"""FastAPI endpoints & app routes for Personal Tracker (One For All), Meal Rotation, Health Connect & Custom Apps."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from nanomuse.data_sync import DataSyncManager
from nanomuse.logger import logger
from nanomuse.server.events import MAIN_THREAD
from nanomuse.tools.ofa_tools import DEFAULT_PERSONAL_DB, _now_iso, _waking_date, get_db

if TYPE_CHECKING:
    from nanomuse.server.service import MuseService


class OFAActionBody(BaseModel):
    action: str
    payload: dict[str, Any] = {}


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        d = dict(r)
        if "raw_json" in d and d["raw_json"]:
            try:
                raw = json.loads(d.pop("raw_json"))
                if isinstance(raw, dict):
                    raw.update({k: v for k, v in d.items() if v is not None})
                    d = raw
            except Exception:
                d.pop("raw_json", None)
        else:
            d.pop("raw_json", None)
        out.append(d)
    return out


def build_dashboard_payload(db_path: Path, target_date: str | None = None) -> dict[str, Any]:
    day = target_date or _waking_date()
    conn = get_db(db_path)
    try:
        # 1. App state key-values
        state_rows = conn.execute("SELECT key, value_json FROM app_state").fetchall()
        app_state: dict[str, Any] = {}
        for sr in state_rows:
            try:
                app_state[sr["key"]] = json.loads(sr["value_json"])
            except Exception:
                app_state[sr["key"]] = sr["value_json"]

        # 2. Food logs
        food_logs = _rows_to_list(
            conn.execute(
                "SELECT * FROM food_logs ORDER BY date DESC, id DESC LIMIT 350"
            ).fetchall()
        )
        day_food = [f for f in food_logs if f.get("date") == day]
        if not day_food:
            day_food = _rows_to_list(
                conn.execute(
                    "SELECT * FROM food_logs WHERE date = ? ORDER BY id ASC", (day,)
                ).fetchall()
            )

        # 3. Meal presets & Kitchen inventory
        meal_presets = [
            dict(r)
            for r in conn.execute("SELECT * FROM meal_presets ORDER BY slot, id").fetchall()
        ]
        for p in meal_presets:
            try:
                p["tags"] = json.loads(p.get("tags_json") or "[]")
            except Exception:
                p["tags"] = []
            p.setdefault("ingredients", p.get("ingredients_note", ""))
            p.setdefault("prep_time", f"{p.get('cook_minutes', 15)} min")

        kitchen_inventory = _rows_to_list(
            conn.execute(
                "SELECT * FROM kitchen_inventory ORDER BY location, name"
            ).fetchall()
        )

        # 4. Workouts & Weekly spread
        workout_logs = _rows_to_list(
            conn.execute("SELECT * FROM workout_logs ORDER BY date DESC, id DESC").fetchall()
        )
        for w in workout_logs:
            w.setdefault("title", w.get("kind", "Workout").title())
            w.setdefault("notes", w.get("note", ""))

        dt = date.fromisoformat(day)
        week_start = (dt - timedelta(days=dt.weekday())).isoformat()
        week_end = (dt + timedelta(days=6 - dt.weekday())).isoformat()
        week_workouts = [
            w for w in workout_logs if week_start <= str(w.get("date") or "") <= week_end
        ]
        run_miles = round(
            sum(
                float(w.get("distance_miles") or 0)
                for w in week_workouts
                if w.get("kind") in ("run", "treadmill")
            ),
            2,
        )
        climbs = sum(
            1 for w in week_workouts if w.get("kind") in ("climb", "bouldering", "climbing")
        )
        pushups_week = sum(
            int(w.get("reps") or 0) for w in week_workouts if w.get("kind") == "pushups"
        )
        pushups_today = sum(
            int(w.get("reps") or 0)
            for w in week_workouts
            if w.get("kind") == "pushups" and w.get("date") == day
        )

        # 5. Health Connect & Sleep & Health entries
        synced_daily = _rows_to_list(
            conn.execute("SELECT * FROM synced_daily ORDER BY date DESC LIMIT 45").fetchall()
        )
        sleep_logs = _rows_to_list(
            conn.execute("SELECT * FROM sleep_logs ORDER BY date DESC LIMIT 30").fetchall()
        )
        health_entries = _rows_to_list(
            conn.execute("SELECT * FROM health_entries ORDER BY date DESC, id DESC LIMIT 40").fetchall()
        )

        # 6. Habits & Habit logs
        habits = _rows_to_list(conn.execute("SELECT * FROM habits ORDER BY id").fetchall())
        for h in habits:
            h.setdefault("name", h.get("title") or h.get("key"))
        habit_Map = {h["id"]: h["key"] for h in habits}

        habit_logs = _rows_to_list(
            conn.execute(
                "SELECT * FROM habit_logs WHERE date >= ? ORDER BY date DESC", (week_start,)
            ).fetchall()
        )
        for hl in habit_logs:
            hl["habit_key"] = habit_Map.get(hl.get("habit_id"), str(hl.get("habit_id")))
            hl["completed"] = bool(hl.get("done", 1))

        # 7. Sobriety
        sobriety_logs = _rows_to_list(
            conn.execute("SELECT * FROM sobriety_logs ORDER BY date DESC, id DESC").fetchall()
        )
        for sl in sobriety_logs:
            sl.setdefault("notes", sl.get("note", ""))
            occ = str(sl.get("occurred_at") or "")
            sl.setdefault("time", occ[11:16] if len(occ) >= 16 else "")

        streak_start = (
            app_state.get("streakStart")
            or app_state.get("sobriety", {}).get("streakStart")
            or ""
        )

        # 8. Finance
        finance_tx = _rows_to_list(
            conn.execute(
                "SELECT * FROM finance_transactions ORDER BY date DESC, id DESC LIMIT 220"
            ).fetchall()
        )
        for ftx in finance_tx:
            ftx.setdefault("title", ftx.get("name", "Transaction"))
            ftx.setdefault("account", ftx.get("source") or ftx.get("origin") or "Checking")

        recurring_payments = _rows_to_list(
            conn.execute("SELECT * FROM recurring_payments ORDER BY id").fetchall()
        )
        for rp in recurring_payments:
            rp.setdefault("amount", rp.get("effective_estimate") or rp.get("estimated_amount") or rp.get("monthly_equivalent") or 0)
            rp.setdefault("due_day", rp.get("next_expected_date") or "--")

        tithing_payments = _rows_to_list(
            conn.execute("SELECT * FROM tithing_payments ORDER BY date DESC").fetchall()
        )

        # 9. Tasks, Checklists, Contacts, Stickies, Journal, Ideas, Trackers, Custom Apps
        tasks = _rows_to_list(
            conn.execute("SELECT * FROM tasks ORDER BY status = 'open' DESC, id DESC").fetchall()
        )
        checklists = _rows_to_list(conn.execute("SELECT * FROM checklists ORDER BY id").fetchall())
        contacts = _rows_to_list(conn.execute("SELECT * FROM contacts ORDER BY id").fetchall())
        sticky_notes = _rows_to_list(
            conn.execute("SELECT * FROM sticky_notes ORDER BY pinned DESC, id DESC").fetchall()
        )
        for sn in sticky_notes:
            sn.setdefault("content", sn.get("body", ""))

        journal_notes = _rows_to_list(
            conn.execute("SELECT * FROM journal_notes ORDER BY date DESC, id DESC").fetchall()
        )
        for jn in journal_notes:
            jn.setdefault("content", jn.get("body", ""))

        ideas = _rows_to_list(conn.execute("SELECT * FROM ideas ORDER BY id DESC").fetchall())
        trackers = _rows_to_list(conn.execute("SELECT * FROM trackers ORDER BY id").fetchall())
        tracker_entries = _rows_to_list(
            conn.execute("SELECT * FROM tracker_entries ORDER BY date DESC, id DESC").fetchall()
        )
        agent_updates = _rows_to_list(
            conn.execute("SELECT * FROM agent_updates ORDER BY id DESC LIMIT 25").fetchall()
        )
        tell_requests = _rows_to_list(
            conn.execute("SELECT * FROM tell_requests ORDER BY id DESC LIMIT 25").fetchall()
        )
        custom_apps = [
            dict(r)
            for r in conn.execute("SELECT * FROM custom_apps ORDER BY updated_at DESC").fetchall()
        ]

        # 10. Today's summary totals
        totals = {
            "calories": round(sum(float(f.get("calories") or 0) for f in day_food), 1),
            "protein": round(sum(float(f.get("protein") or 0) for f in day_food), 1),
            "carbs": round(sum(float(f.get("carbs") or 0) for f in day_food), 1),
            "fat": round(sum(float(f.get("fat") or 0) for f in day_food), 1),
            "fiber": round(sum(float(f.get("fiber") or 0) for f in day_food), 1),
            "fluid_oz": round(sum(float(f.get("fluid_oz") or 0) for f in day_food), 1),
        }

        if contacts:
            picked = contacts[dt.toordinal() % len(contacts)]
            today_connection = {
                "name": str(picked.get("name") or "Friend"),
                "role": str(picked.get("role") or picked.get("relationship") or "Daily Connection"),
                "tip": str(picked.get("notes") or "Reach out with a quick text or voice memo today."),
            }
        else:
            today_connection = {
                "name": "Family & Friends",
                "role": "Daily Check-in",
                "tip": "Pick someone close to check in on today.",
            }

        return {
            "ok": True,
            "wakingDate": day,
            "weekStart": week_start,
            "weekEnd": week_end,
            "todayTotals": totals,
            "goals": {
                "calories": 2250,
                "protein": 175,
                "carbs": 230,
                "fat": 70,
                "fiber": 30,
                "waterOz": 192,
                "runMilesWeek": 7.0,
                "climbsWeek": 2,
                "pushupsDay": 15,
                "pushupsWeek": 75,
            },
            "weeklySpread": {
                "runMiles": run_miles,
                "climbs": climbs,
                "pushupsWeek": pushups_week,
                "pushupsToday": pushups_today,
            },
            "todayConnection": today_connection,
            "streakStart": streak_start,
            "foodLogs": food_logs,
            "dayFood": day_food,
            "mealPresets": meal_presets,
            "kitchenInventory": kitchen_inventory,
            "workoutLogs": workout_logs,
            "syncedDaily": synced_daily,
            "sleepLogs": sleep_logs,
            "healthEntries": health_entries,
            "habits": habits,
            "habitLogs": habit_logs,
            "sobrietyLogs": sobriety_logs,
            "financeTransactions": finance_tx,
            "recurringPayments": recurring_payments,
            "tithingPayments": tithing_payments,
            "tasks": tasks,
            "checklists": checklists,
            "contacts": contacts,
            "stickyNotes": sticky_notes,
            "journalNotes": journal_notes,
            "ideas": ideas,
            "trackers": trackers,
            "trackerEntries": tracker_entries,
            "agentUpdates": agent_updates,
            "tellRequests": tell_requests,
            "customApps": custom_apps,
            "appState": app_state,
        }
    finally:
        conn.close()


def install_ofa(app: FastAPI, svc: MuseService, dependencies: list[Any] | None = None) -> None:
    dep = dependencies or []
    """Register `/api/ofa/*`, `/one-for-all`, `/mha-rankings`, and `/apps/{slug}` ahead of the SPA catch-all."""
    first_new = len(app.router.routes)
    db_path = svc.settings.data_dir / "personal.db"
    if not db_path.exists():
        db_path = DEFAULT_PERSONAL_DB
    workspace = svc.settings.agent.workspace

    def _broadcast(event: str, extra: dict[str, Any] | None = None) -> None:
        try:
            svc.bus.publish({"kind": "ofa_update", "event": event, **(extra or {})})
        except Exception:
            pass

    @app.get("/api/ofa/dashboard", dependencies=dep)
    async def ofa_dashboard(date: str | None = None) -> dict[str, Any]:
        return build_dashboard_payload(db_path, date)

    @app.post("/api/ofa/action", dependencies=dep)
    async def ofa_action(body: OFAActionBody) -> dict[str, Any]:
        act = body.action.strip()
        p = body.payload or {}
        day = str(p.get("date") or "").strip() or _waking_date()
        now = _now_iso()
        conn = get_db(db_path)
        try:
            if act == "log_food":
                name = str(p.get("name") or "Food item").strip()
                slot = str(p.get("slot") or "snack").strip()
                cal = float(p.get("calories") or 0)
                pro = float(p.get("protein") or 0)
                carbs = float(p.get("carbs") or 0)
                fat = float(p.get("fat") or 0)
                fiber = float(p.get("fiber") or 0)
                fl_oz = float(p.get("fluid_oz") or 0)
                fid = DataSyncManager.log_food(
                    date_str=day,
                    name=name,
                    calories=cal,
                    protein=pro,
                    carbs=carbs,
                    fat=fat,
                    fiber=fiber,
                    fluid_oz=fl_oz,
                    slot=slot,
                    note=str(p.get("note") or ""),
                    source="ofa_ui",
                )
                _broadcast("log_food", {"id": fid, "date": day, "name": name})

            elif act == "delete_food":
                fid = int(p.get("id") or 0)
                DataSyncManager.delete_food(food_id=fid)
                _broadcast("delete_food", {"id": fid, "date": day})

            elif act == "log_water":
                oz = float(p.get("fluid_oz") or 32)
                wid = DataSyncManager.log_water(date_str=day, fluid_oz=oz, source="ofa_ui")
                _broadcast("log_water", {"id": wid, "date": day, "fluid_oz": oz})

            elif act == "log_preset":
                pid = str(p.get("preset_id") or "").strip()
                row = conn.execute("SELECT * FROM meal_presets WHERE id = ?", (pid,)).fetchone()
                if not row:
                    raise HTTPException(404, f"Preset {pid} not found")
                extra_fruit = str(p.get("fruit") or "").strip()
                name = row["name"] + (f" + {extra_fruit}" if extra_fruit else "")
                cal = float(row["calories"] or 0) + (80 if extra_fruit else 0)
                carbs = float(row["carbs"] or 0) + (20 if extra_fruit else 0)
                fiber = float(row["fiber"] or 0) + (3 if extra_fruit else 0)
                fid = DataSyncManager.log_food(
                    date_str=day,
                    name=name,
                    calories=cal,
                    protein=row["protein"],
                    carbs=carbs,
                    fat=row["fat"],
                    fiber=fiber,
                    fluid_oz=0,
                    slot=row["slot"],
                    note=f"Preset {pid}",
                    source="preset_ui",
                )
                _broadcast("log_preset", {"id": fid, "date": day, "preset_id": pid, "name": name})

            elif act == "update_inventory":
                key = str(p.get("item_key") or p.get("name") or "").strip().lower().replace(" ", "_")
                name = str(p.get("name") or key).strip()
                loc = str(p.get("location") or "pantry").strip().lower()
                qty = float(p.get("quantity") if p.get("quantity") is not None else 1)
                unit = str(p.get("unit") or "count").strip()
                ripe = str(p.get("ripeness_stage") or "not_applicable").strip()
                notes = str(p.get("notes") or "").strip()
                DataSyncManager.update_inventory(
                    item_key=key,
                    name=name,
                    location=loc,
                    quantity=qty,
                    unit=unit,
                    ripeness_stage=ripe,
                    notes=notes,
                )
                _broadcast("update_inventory", {"item_key": key, "quantity": qty})

            elif act == "log_workout":
                kind = str(p.get("kind") or "workout").strip().lower()
                dur = float(p.get("duration_min") or 0)
                dist = float(p.get("distance_miles") or 0)
                reps = int(p.get("reps") or 0)
                note = str(p.get("note") or p.get("name") or "").strip()
                conn.execute(
                    """
                    INSERT INTO workout_logs (date, kind, duration_min, distance_miles, reps, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (day, kind, dur, dist, reps, note),
                )
                conn.commit()
                _broadcast("log_workout", {"date": day, "kind": kind})

            elif act == "toggle_habit":
                hkey = str(p.get("habit_key") or "").strip()
                hrow = conn.execute("SELECT id FROM habits WHERE key = ?", (hkey,)).fetchone()
                if hrow:
                    hid = hrow["id"]
                    existing = conn.execute(
                        "SELECT id, done FROM habit_logs WHERE habit_id = ? AND date = ?",
                        (hid, day),
                    ).fetchone()
                    if existing:
                        new_val = 0 if existing["done"] else 1
                        conn.execute("UPDATE habit_logs SET done = ? WHERE id = ?", (new_val, existing["id"]))
                    else:
                        conn.execute(
                            "INSERT INTO habit_logs (habit_id, date, done, note) VALUES (?, ?, 1, '')",
                            (hid, day),
                        )
                    conn.commit()
                    _broadcast("toggle_habit", {"habit_key": hkey, "date": day})

            elif act == "log_sobriety":
                kind = str(p.get("kind") or "checkin").strip().lower()
                urge = int(p.get("urge_level") or 0)
                trig = str(p.get("trigger_note") or "").strip()
                note = str(p.get("note") or "").strip()
                conn.execute(
                    """
                    INSERT INTO sobriety_logs (date, occurred_at, kind, urge_level, trigger_note, note)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (day, now, kind, urge, trig, note),
                )
                if kind == "slip":
                    for k, val in [("streakStart", now), ("streakStartAt", now), ("streakStartDate", day)]:
                        conn.execute(
                            "INSERT OR REPLACE INTO app_state (key, value_json, updated_at) VALUES (?, ?, ?)",
                            (k, json.dumps(val), now),
                        )
                conn.commit()
                _broadcast("log_sobriety", {"kind": kind, "date": day})

            elif act == "log_health":
                kind = str(p.get("kind") or "weight").strip().lower()
                val = float(p.get("value") or 0)
                unit = str(p.get("unit") or ("lb" if kind == "weight" else "")).strip()
                note = str(p.get("note") or "").strip()
                conn.execute(
                    "INSERT INTO health_entries (date, kind, value, unit, note) VALUES (?, ?, ?, ?, ?)",
                    (day, kind, val, unit, note),
                )
                conn.commit()
                _broadcast("log_health", {"kind": kind, "value": val})

            elif act == "log_finance":
                name = str(p.get("name") or "Transaction").strip()
                amt = float(p.get("amount") or 0)
                kind = str(p.get("kind") or ("expense" if amt < 0 else "income")).strip()
                cat = str(p.get("category") or "General").strip()
                note = str(p.get("note") or "").strip()
                conn.execute(
                    """
                    INSERT INTO finance_transactions (date, name, amount, kind, category, origin, source, note)
                    VALUES (?, ?, ?, ?, ?, 'ofa_ui', 'Manual', ?)
                    """,
                    (day, name, amt, kind, cat, note),
                )
                conn.commit()
                _broadcast("log_finance", {"title": name, "amount": amt})

            elif act == "upsert_task":
                tid = p.get("id")
                title = str(p.get("title") or p.get("name") or "").strip()
                prio = str(p.get("priority") or "med").strip()
                status = str(p.get("status") or "open").strip()
                due = str(p.get("due_date") or "").strip()
                notes = str(p.get("notes") or "").strip()
                if tid:
                    conn.execute(
                        "UPDATE tasks SET status = ?, title = COALESCE(NULLIF(?, ''), title) WHERE id = ?",
                        (status, title, int(tid)),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO tasks (title, status, priority, due_date, list_name, notes)
                        VALUES (?, ?, ?, ?, 'General', ?)
                        """,
                        (title, status, prio, due, notes),
                    )
                conn.commit()
                _broadcast("upsert_task", {"title": title})

            elif act == "toggle_task":
                tid = int(p.get("id") or 0)
                row = conn.execute("SELECT status FROM tasks WHERE id = ?", (tid,)).fetchone()
                if row:
                    new_status = "open" if row["status"] == "done" else "done"
                    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, tid))
                    conn.commit()
                    _broadcast("toggle_task", {"id": tid, "status": new_status})

            elif act == "upsert_sticky":
                sid = p.get("id")
                title = str(p.get("title") or "Sticky Note").strip()
                body_txt = str(p.get("content") or p.get("body") or "").strip()
                color = str(p.get("color") or "gold").strip()
                if sid:
                    conn.execute(
                        "UPDATE sticky_notes SET title = ?, body = ?, color = ? WHERE id = ?",
                        (title, body_txt, color, int(sid)),
                    )
                else:
                    conn.execute(
                        "INSERT INTO sticky_notes (title, body, color, pinned) VALUES (?, ?, ?, 1)",
                        (title, body_txt, color),
                    )
                conn.commit()
                _broadcast("upsert_sticky", {"title": title})

            elif act == "add_journal":
                title = str(p.get("title") or f"Note — {day}").strip()
                body_txt = str(p.get("content") or p.get("body") or "").strip()
                conn.execute(
                    "INSERT INTO journal_notes (date, title, body) VALUES (?, ?, ?)",
                    (day, title, body_txt),
                )
                conn.commit()
                _broadcast("add_journal", {"title": title})

            elif act == "set_appearance":
                conn.execute(
                    "INSERT OR REPLACE INTO app_state (key, value_json, updated_at) VALUES ('appearance', ?, ?)",
                    (json.dumps(p), now),
                )
                conn.commit()
                _broadcast("set_appearance", p)

            elif act == "log_tracker":
                tid = int(p.get("tracker_id") or p.get("trackerId") or 1)
                val_num = p.get("value_num") if p.get("value_num") is not None else p.get("valueNum")
                val_num = float(val_num) if val_num is not None else 0.0
                val_txt = str(p.get("value_text") or p.get("valueText") or "").strip()
                t_label = str(p.get("time_label") or p.get("timeLabel") or "").strip()
                note = str(p.get("note") or "").strip()
                raw = json.dumps({
                    "trackerId": tid,
                    "date": day,
                    "valueNum": val_num,
                    "valueText": val_txt,
                    "timeLabel": t_label,
                    "note": note,
                })
                # Upsert if step work (tracker 6) already logged for that date
                if tid == 6:
                    conn.execute("DELETE FROM tracker_entries WHERE tracker_id = 6 AND date = ?", (day,))
                conn.execute(
                    """
                    INSERT INTO tracker_entries (tracker_id, date, value_num, value_text, time_label, note, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tid, day, val_num, val_txt, t_label, note, raw),
                )
                conn.commit()
                _broadcast("log_tracker", {"tracker_id": tid, "date": day, "value_num": val_num})

            elif act == "delete_tracker_entry":
                eid = int(p.get("id") or 0)
                conn.execute("DELETE FROM tracker_entries WHERE id = ?", (eid,))
                conn.commit()
                _broadcast("delete_tracker_entry", {"id": eid})

            elif act == "pay_tithing":
                amt = float(p.get("amount") or 0)
                note = str(p.get("note") or "Tithing paid via One For All").strip()
                conn.execute(
                    "INSERT INTO tithing_payments (date, amount, note) VALUES (?, ?, ?)",
                    (day, amt, note),
                )
                conn.commit()
                _broadcast("pay_tithing", {"date": day, "amount": amt})

            elif act == "delete_sticky":
                sid = int(p.get("id") or 0)
                conn.execute("DELETE FROM sticky_notes WHERE id = ?", (sid,))
                conn.commit()
                _broadcast("delete_sticky", {"id": sid})

            elif act == "toggle_pin_sticky":
                sid = int(p.get("id") or 0)
                row = conn.execute("SELECT pinned FROM sticky_notes WHERE id = ?", (sid,)).fetchone()
                if row:
                    new_p = 0 if row["pinned"] else 1
                    conn.execute("UPDATE sticky_notes SET pinned = ? WHERE id = ?", (new_p, sid))
                    conn.commit()
                    _broadcast("toggle_pin_sticky", {"id": sid, "pinned": new_p})

            elif act == "tell_muse":
                msg = str(p.get("message") or "").strip()
                send_to_chat = bool(p.get("send_to_chat", True))
                if not msg:
                    raise HTTPException(400, "Message is empty")
                conn.execute(
                    "INSERT INTO tell_requests (date, body, status) VALUES (?, ?, 'sent')",
                    (day, msg),
                )
                conn.commit()
                if send_to_chat:
                    try:
                        svc.send(MAIN_THREAD, f"[From One For All Dashboard] {msg}")
                    except Exception as exc:
                        logger.warning("tell_muse send failed: {}", exc)
                _broadcast("tell_muse", {"message": msg})

            else:
                raise HTTPException(400, f"Unknown OFA action: {act}")

            return build_dashboard_payload(db_path, day)
        finally:
            conn.close()

    @app.post("/api/ofa/health-connect")
    async def health_connect_webhook(request: Request) -> dict[str, Any]:
        """Webhook endpoint for Android Health Connect / Sleep as Android."""
        data = await request.json()
        day = str(data.get("date") or "").strip() or _waking_date()
        conn = get_db(db_path)
        try:
            existing = conn.execute("SELECT * FROM synced_daily WHERE date = ?", (day,)).fetchone()
            steps = int(data.get("steps") or (existing["steps"] if existing else 0) or 0)
            dist = float(data.get("distance_miles") or (existing["distance_miles"] if existing else 0) or 0)
            act_cal = float(data.get("active_calories") or (existing["active_calories"] if existing else 0) or 0)
            tot_cal = float(data.get("total_calories") or (existing["total_calories"] if existing else 0) or 0)
            rhr = float(data.get("resting_hr") or (existing["resting_hr"] if existing else 0) or 0)
            avg_hr = float(data.get("avg_hr") or (existing["avg_hr"] if existing else 0) or 0)
            sleep_h = float(data.get("sleep_hours") or (existing["sleep_hours"] if existing else 0) or 0)
            weight = float(data.get("weight_lbs") or (existing["weight_lbs"] if existing else 0) or 0)

            conn.execute(
                """
                INSERT INTO synced_daily (date, steps, distance_miles, active_calories, total_calories, avg_hr, resting_hr, sleep_hours, weight_lbs, source, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'health_connect_webhook', ?)
                ON CONFLICT(date) DO UPDATE SET
                    steps = excluded.steps,
                    distance_miles = excluded.distance_miles,
                    active_calories = excluded.active_calories,
                    total_calories = excluded.total_calories,
                    avg_hr = excluded.avg_hr,
                    resting_hr = excluded.resting_hr,
                    sleep_hours = excluded.sleep_hours,
                    weight_lbs = excluded.weight_lbs,
                    raw_json = excluded.raw_json
                """,
                (day, steps, dist, act_cal, tot_cal, avg_hr, rhr, sleep_h, weight, json.dumps(data)),
            )
            if data.get("sleep_hours"):
                conn.execute(
                    """
                    INSERT INTO sleep_logs (date, bed_time, wake_time, hours, quality, note, raw_json)
                    VALUES (?, ?, ?, ?, ?, 'Health Connect Webhook', ?)
                    """,
                    (
                        day,
                        str(data.get("bed_time") or data.get("bedtime") or ""),
                        str(data.get("wake_time") or ""),
                        float(data["sleep_hours"]),
                        int(data.get("quality") or 0),
                        json.dumps(data),
                    ),
                )
            conn.commit()
            _broadcast("health_connect_sync", {"date": day, "steps": steps})
            return {"ok": True, "date": day, "steps": steps, "sleep_hours": sleep_h}
        finally:
            conn.close()

    @app.get("/one-for-all", include_in_schema=False)
    async def serve_ofa() -> FileResponse:
        p = workspace / "one-for-all.html"
        if not p.is_file():
            raise HTTPException(404, "one-for-all.html not found in workspace")
        return FileResponse(p, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/mha-rankings", include_in_schema=False)
    async def serve_mha() -> FileResponse:
        p = workspace / "mha-quirk-rankings.html"
        if not p.is_file():
            raise HTTPException(404, "mha-quirk-rankings.html not found in workspace")
        return FileResponse(p, media_type="text/html", headers={"Cache-Control": "no-cache"})

    def _resolve_custom_app_root(slug: str) -> tuple[Path | None, Path | None]:
        clean = "".join(c for c in slug.strip().lower().removesuffix(".html") if c.isalnum() or c in "-_")
        if not clean:
            return None, None
        folder_candidates = [
            workspace / "apps" / clean / "space-build",
            workspace / "apps" / clean / "space-build-cvm",
            workspace / "spaces" / clean,
            workspace / "apps" / clean,
        ]
        for folder in folder_candidates:
            if folder.is_dir():
                for entry_name in ("index.html", f"{clean}.html"):
                    entry = folder / entry_name
                    if entry.is_file():
                        return folder.resolve(), entry.resolve()
                htmls = sorted(folder.glob("*.html"))
                if htmls:
                    return folder.resolve(), htmls[0].resolve()
        for single in (workspace / f"{clean}.html", workspace / "apps" / f"{clean}.html"):
            if single.is_file():
                return single.parent.resolve(), single.resolve()
        return None, None

    @app.get("/apps/{slug}", include_in_schema=False)
    async def serve_custom_app(slug: str) -> FileResponse:
        _root, entry = _resolve_custom_app_root(slug)
        if entry is not None and entry.is_file():
            return FileResponse(entry, media_type="text/html", headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, f"Custom app '{slug}' not found")

    @app.get("/apps/{slug}/{asset_path:path}", include_in_schema=False)
    async def serve_custom_app_asset(slug: str, asset_path: str) -> FileResponse:
        root, entry = _resolve_custom_app_root(slug)
        if root is None:
            raise HTTPException(404, f"Custom app '{slug}' not found")
        clean_asset = asset_path.lstrip("/")
        if clean_asset:
            cand = (root / clean_asset).resolve()
            try:
                cand.relative_to(root)
            except ValueError as exc:
                raise HTTPException(403, "path outside app") from exc
            if cand.is_file():
                return FileResponse(cand, headers={"Cache-Control": "no-cache"})
            # Also try parent app folder (e.g. icon.webp alongside space-build/)
            app_dir = (workspace / "apps" / "".join(c for c in slug.strip().lower() if c.isalnum() or c in "-_")).resolve()
            if app_dir.is_dir():
                cand2 = (app_dir / clean_asset).resolve()
                try:
                    cand2.relative_to(app_dir)
                except ValueError:
                    pass
                else:
                    if cand2.is_file():
                        return FileResponse(cand2, headers={"Cache-Control": "no-cache"})
        if entry is not None and entry.is_file():
            return FileResponse(entry, media_type="text/html", headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, f"Asset '{asset_path}' not found in app '{slug}'")

    # Move our routes ahead of the SPA catch-all (`/{full_path:path}`)
    routes = app.router.routes
    added = routes[first_new:]
    del routes[first_new:]
    routes[0:0] = added


__all__ = ["install_ofa"]
