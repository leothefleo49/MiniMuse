"""Native Spaces Router for local web apps: Habit Tracker (One For All), Meal Rotation & custom spaces."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from nanomuse.data_sync import DataSyncManager
from nanomuse.logger import logger

router = APIRouter()

SPACES_DIR = Path.home() / ".nanomuse" / "workspace" / "spaces"
HT_DB = Path.home() / ".nanomuse" / "habit-tracker.db"
MR_DB = Path.home() / ".nanomuse" / "meal-rotation.db"

_ACTIVE_WORKSPACE: Path | None = None
_ACTIVE_DATA_DIR: Path | None = None


def _resolve_spaces_dir() -> Path:
    if _ACTIVE_WORKSPACE is not None:
        cand = _ACTIVE_WORKSPACE / "spaces"
        if cand.is_dir():
            return cand
    return SPACES_DIR


def _resolve_apps_dir() -> Path | None:
    if _ACTIVE_WORKSPACE is not None:
        cand = _ACTIVE_WORKSPACE / "apps"
        if cand.is_dir():
            return cand
    fallback = Path.home() / ".nanomuse" / "workspace" / "apps"
    return fallback if fallback.is_dir() else None


def _resolve_db(filename: str, fallback: Path) -> Path:
    if _ACTIVE_DATA_DIR is not None:
        cand = _ACTIVE_DATA_DIR / filename
        if cand.is_file():
            return cand
    if _ACTIVE_WORKSPACE is not None:
        slug = filename.removesuffix(".db")
        app_db = _ACTIVE_WORKSPACE / "apps" / slug / "app.db"
        if app_db.is_file():
            return app_db
    if fallback.is_file():
        return fallback
    if _ACTIVE_DATA_DIR is not None:
        return _ACTIVE_DATA_DIR / filename
    return fallback


class ActionCall(BaseModel):
    action: str
    args: dict[str, Any] = {}
    actionCallId: str | None = None


def get_ht_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_db("habit-tracker.db", HT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def get_mr_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_resolve_db("meal-rotation.db", MR_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ==============================================================================
# Habit Tracker (One For All) Action Dispatcher
# ==============================================================================
def handle_ht_action(action: str, args: dict[str, Any]) -> Any:
    conn = get_ht_db()
    try:
        if action == "getDashboard":
            week_start = args.get("weekStart", "2026-11-02")
            habits = [
                {
                    "id": r["id"],
                    "key": r["key"],
                    "title": r["title"],
                    "category": r["category"],
                    "period": r["period"],
                    "targetMin": r["target_min"] or 1,
                    "targetMax": r["target_max"],
                    "unit": r["unit"] or "done",
                    "hint": r["hint"] or "",
                }
                for r in conn.execute("SELECT * FROM habits").fetchall()
            ]

            habit_logs = [
                {
                    "id": r["id"],
                    "habitId": r["habit_id"],
                    "date": r["date"],
                    "value": r["value"] or 1,
                    "done": bool(r["done"]),
                    "note": r["note"] or "",
                }
                for r in conn.execute("SELECT * FROM habit_logs WHERE date >= ?", (week_start,)).fetchall()
            ]

            contacts = [{"id": r["id"], "name": r["name"], "note": r["note"] or ""} for r in conn.execute("SELECT * FROM contacts").fetchall()]
            contact_logs = [{"id": r["id"], "contactId": r["contact_id"], "date": r["date"], "note": r["note"] or ""} for r in conn.execute("SELECT * FROM contact_logs").fetchall()]

            sobriety_logs = [
                {
                    "id": r["id"],
                    "date": r["date"],
                    "kind": r["kind"],
                    "urgeLevel": r["urge_level"] or 0,
                    "triggerNote": r["trigger_note"] or "",
                    "note": r["note"] or "",
                    "occurredAt": r["occurred_at"] or "",
                }
                for r in conn.execute("SELECT * FROM sobriety_logs ORDER BY date DESC, id DESC LIMIT 50").fetchall()
            ]

            sleep_logs = []
            for r in conn.execute("SELECT * FROM sleep_logs ORDER BY date DESC LIMIT 40").fetchall():
                d = dict(r)
                sleep_logs.append({
                    "id": d.get("id"),
                    "date": d.get("date"),
                    "wakeTime": d.get("wake_time") or "",
                    "bedTime": d.get("bed_time") or "",
                    "hours": d.get("hours"),
                    "quality": d.get("quality"),
                    "note": d.get("note") or "",
                    "inBedHours": d.get("in_bed_hours"),
                    "dstShift": d.get("dst_shift") or "",
                })

            journal_notes = []
            for r in conn.execute("SELECT * FROM journal_notes ORDER BY date DESC LIMIT 30").fetchall():
                d = dict(r)
                journal_notes.append({
                    "id": d.get("id"),
                    "date": d.get("date"),
                    "timeLabel": d.get("time_label") or "",
                    "title": d.get("title") or "",
                    "body": d.get("body") or "",
                    "mood": d.get("mood"),
                    "energy": d.get("energy"),
                })

            checklists = []
            for cl in conn.execute("SELECT * FROM checklists").fetchall():
                items = [
                    {"id": it["id"], "text": it["text"]}
                    for it in conn.execute("SELECT * FROM checklist_items WHERE checklist_id = ? ORDER BY sort_order, id", (cl["id"],)).fetchall()
                ]
                checklists.append({"id": cl["id"], "title": cl["title"], "description": cl["description"] or "", "items": items})

            completions = [
                {"checklistId": r["checklist_id"], "itemId": r["item_id"], "date": r["date"], "done": bool(r["done"])}
                for r in conn.execute("SELECT * FROM checklist_completions WHERE date >= ?", (week_start,)).fetchall()
            ]

            app_settings = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM app_settings").fetchall()}
            streak_start = app_settings.get("streak_start", "")
            streak_start_at = app_settings.get("streak_start_at", streak_start)
            streak_start_date = app_settings.get("streak_start_date", "")

            appearance = {
                "preset": "mealrotation",
                "vibe": "mealrotation",
                "accent": "",
                "radiusStyle": "preset",
                "headingStyle": "preset",
                "bg": "",
                "surface": "",
                "scrollbar": "",
                "density": "preset",
                "textColor": "",
                "tabOverrides": {},
            }
            if "appearance" in app_settings:
                try:
                    appearance.update(json.loads(app_settings["appearance"]))
                except Exception:
                    pass

            health_entries = [
                {"id": r["id"], "date": r["date"], "kind": r["kind"], "value": r["value"], "unit": r["unit"] or "", "note": r["note"] or ""}
                for r in conn.execute("SELECT * FROM health_entries ORDER BY date DESC LIMIT 50").fetchall()
            ]

            workout_logs = []
            for r in conn.execute("SELECT * FROM workout_logs ORDER BY date DESC LIMIT 40").fetchall():
                d = dict(r)
                workout_logs.append({
                    "id": d.get("id"),
                    "date": d.get("date"),
                    "kind": d.get("kind"),
                    "durationMin": d.get("duration_min"),
                    "distanceMiles": d.get("distance_miles"),
                    "reps": d.get("reps"),
                    "intensity": d.get("intensity"),
                    "note": d.get("note") or "",
                })

            ideas = [{"id": r["id"], "title": r["title"], "body": r["body"] or "", "category": r["category"] or "", "status": r["status"] or "active"} for r in conn.execute("SELECT * FROM ideas").fetchall()]

            tasks = [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "notes": r["notes"] or "",
                    "dueDate": r["due_date"] or "",
                    "priority": r["priority"] or "medium",
                    "status": r["status"] or "open",
                    "listName": r["list_name"] or "General",
                    "sortOrder": r["sort_order"] or 0,
                }
                for r in conn.execute("SELECT * FROM tasks ORDER BY status = 'open' DESC, id DESC").fetchall()
            ]

            trackers = [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "type": r["type"] or "number",
                    "unit": r["unit"] or "",
                    "category": r["category"] or "custom",
                    "target": r["target"],
                    "hint": r["hint"] or "",
                }
                for r in conn.execute("SELECT * FROM custom_trackers").fetchall()
            ]

            tracker_entries = [
                {
                    "id": r["id"],
                    "trackerId": r["tracker_id"],
                    "date": r["date"],
                    "timeLabel": r["time_label"] or "",
                    "valueNum": r["value_num"],
                    "valueText": r["value_text"] or "",
                    "note": r["note"] or "",
                }
                for r in conn.execute("SELECT * FROM tracker_entries ORDER BY date DESC LIMIT 60").fetchall()
            ]

            food_logs = [
                {
                    "id": r["id"],
                    "date": r["date"],
                    "kind": r["kind"] or "food",
                    "name": r["name"],
                    "calories": r["calories"] or 0,
                    "protein": r["protein"] or 0,
                    "carbs": r["carbs"] or 0,
                    "fat": r["fat"] or 0,
                    "fiber": r["fiber"] or 0,
                    "fluidOz": r["fluid_oz"] or 0,
                    "slot": r["slot"] or "snack",
                    "source": r["source"] or "user",
                    "note": r["note"] or "",
                    "createdAt": r["created_at"] or "",
                }
                for r in conn.execute("SELECT * FROM food_logs ORDER BY date DESC, id DESC LIMIT 500").fetchall()
            ]

            food_presets = [
                {
                    "id": r["id"],
                    "category": r["category"] or "",
                    "name": r["name"],
                    "calories": r["calories"] or 0,
                    "protein": r["protein"] or 0,
                    "fat": r["fat"] or 0,
                    "carbs": r["carbs"] or 0,
                    "fiber": r["fiber"] or 0,
                    "details": r["details"] or "",
                }
                for r in conn.execute("SELECT * FROM food_presets").fetchall()
            ]

            kitchen_inventory = [
                {
                    "id": r["id"],
                    "itemKey": r["item_key"],
                    "name": r["name"],
                    "quantity": r["quantity"] or 0,
                    "unit": r["unit"] or "",
                    "location": r["location"] or "pantry",
                    "usagePerWeek": r["usage_per_week"] or 0,
                    "expiresDay": r["expires_day"],
                    "notes": r["notes"] or "",
                    "ripenessStage": r["ripeness_stage"] or "not_applicable",
                    "quantityConfirmed": bool(r["quantity_confirmed"]),
                }
                for r in conn.execute("SELECT * FROM kitchen_inventory ORDER BY location, name").fetchall()
            ]

            finance_tx = [
                {
                    "id": r["id"],
                    "date": r["date"],
                    "kind": r["kind"] or "expense",
                    "amount": r["amount"] or 0,
                    "source": r["source"] or "Manual",
                    "category": r["category"] or "General",
                    "name": r["name"] or "Transaction",
                    "note": r["note"] or "",
                    "origin": r["origin"] or "plaid",
                }
                for r in conn.execute("SELECT * FROM finance_transactions ORDER BY date DESC LIMIT 250").fetchall()
            ]

            tithing_payments = [
                {"id": r["id"], "date": r["date"], "amount": r["amount"] or 0, "note": r["note"] or ""}
                for r in conn.execute("SELECT * FROM tithing_payments ORDER BY date DESC").fetchall()
            ]

            recurring_payments = []
            for r in conn.execute("SELECT * FROM recurring_payments").fetchall():
                d = dict(r)
                recurring_payments.append({
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "cadence": d.get("cadence") or "monthly",
                    "intervalMonths": d.get("interval_months") or 1,
                    "estimatedAmount": d.get("estimated_amount") or 0,
                    "category": d.get("category") or "general",
                    "status": d.get("status") or "active",
                    "nextExpectedDate": d.get("next_expected_date") or "",
                    "note": d.get("note") or "",
                    "matchTerms": d.get("match_terms") or "",
                    "effectiveEstimate": d.get("effective_estimate") or d.get("estimated_amount") or 0,
                    "monthlyEquivalent": d.get("monthly_equivalent") or d.get("estimated_amount") or 0,
                    "history": [],
                })

            sticky_notes = [
                {
                    "id": r["id"],
                    "title": r["title"] or "",
                    "body": r["body"] or "",
                    "color": r["color"] or "yellow",
                    "category": r["category"] or "general",
                    "pinned": bool(r["pinned"]),
                    "sortOrder": r["sort_order"] or 0,
                }
                for r in conn.execute("SELECT * FROM sticky_notes ORDER BY pinned DESC, sort_order, id DESC").fetchall()
            ]

            synced_daily = []
            for r in conn.execute("SELECT * FROM synced_daily ORDER BY date DESC LIMIT 45").fetchall():
                d = dict(r)
                synced_daily.append({
                    "date": d.get("date"),
                    "steps": d.get("steps"),
                    "distanceMiles": d.get("distance_miles"),
                    "activeCalories": d.get("active_calories"),
                    "totalCalories": d.get("total_calories"),
                    "restingHr": d.get("resting_hr"),
                    "avgHr": d.get("avg_hr"),
                    "sleepHours": d.get("sleep_hours"),
                    "sleepWakeTime": d.get("sleep_wake_time") or "",
                    "sleepBedTime": d.get("sleep_bed_time") or "",
                    "sleepInBedHours": d.get("sleep_in_bed_hours"),
                    "sleepDstShift": d.get("sleep_dst_shift") or "",
                    "weightLbs": d.get("weight_lbs"),
                    "source": d.get("source") or "health_connect",
                })

            agent_updates = [
                {"id": r["id"], "date": r["date"] or "", "category": r["category"] or "sync", "title": r["title"] or "", "body": r["body"] or "", "source": r["source"] or "muse"}
                for r in conn.execute("SELECT * FROM agent_updates ORDER BY id DESC LIMIT 30").fetchall()
            ]

            tell_requests = [
                {"id": r["id"], "date": r["date"] or "", "body": r["body"] or "", "status": r["status"] or "sent"}
                for r in conn.execute("SELECT * FROM tell_muse_requests ORDER BY id DESC LIMIT 20").fetchall()
            ]

            return {
                "habits": habits,
                "habitLogs": habit_logs,
                "contacts": contacts,
                "contactLogs": contact_logs,
                "sobrietyLogs": sobriety_logs,
                "sleepLogs": sleep_logs,
                "journalNotes": journal_notes,
                "checklists": checklists,
                "completions": completions,
                "streakStart": streak_start,
                "streakStartAt": streak_start_at,
                "streakStartDate": streak_start_date,
                "todaySleep": None,
                "healthEntries": health_entries,
                "workoutLogs": workout_logs,
                "ideas": ideas,
                "messageDrafts": [],
                "appearance": appearance,
                "foodLogs": food_logs,
                "foodPresets": food_presets,
                "kitchenInventory": kitchen_inventory,
                "shoppingState": {"checkedKeys": [], "customPrices": {}, "customItems": [], "removedKeys": []},
                "lastInventorySyncAt": app_settings.get("last_inventory_sync_at", ""),
                "trackers": trackers,
                "trackerEntries": tracker_entries,
                "tasks": tasks,
                "calendarEvents": [],
                "preferences": {},
                "sleepSchedule": {"targetBed": "23:00", "targetWake": "07:30"},
                "syncedDaily": synced_daily,
                "agentUpdates": agent_updates,
                "syncStatus": {
                    "lastSyncAt": app_settings.get("last_sync_at", ""),
                    "status": "idle",
                    "pendingTellCount": 0,
                    "lastFoodSyncAt": app_settings.get("last_food_sync_at", ""),
                    "foodMrToHt": 0,
                    "foodHtToMr": 0,
                    "foodSyncFailed": False,
                    "lastSobrietySyncAt": app_settings.get("last_sobriety_sync_at", ""),
                    "sobrietyStToHt": 0,
                    "sobrietyHtToSt": 0,
                    "sobrietySyncFailed": False,
                    "sobrietyOutcome": "in_sync",
                    "sobrietyStreakAlignedAt": streak_start_at,
                    "lastFullSyncAt": app_settings.get("last_full_sync_at", ""),
                    "lastFinanceSyncAt": app_settings.get("last_finance_sync_at", ""),
                    "financeNewCount": 0,
                    "financeSyncFailed": False,
                    "financeTithingOwed": float(app_settings.get("finance_tithing_owed", 0.0)),
                    "financeOutcome": "in_sync",
                },
                "tellRequests": tell_requests,
                "financeTransactions": finance_tx,
                "tithingPayments": tithing_payments,
                "financeBalances": {
                    "checking": float(app_settings.get("finance_checking_current", 0.0)),
                    "checkingAvailable": float(app_settings.get("finance_checking_available", 0.0)),
                    "savings": float(app_settings.get("finance_savings_current", 0.0)),
                    "asOf": app_settings.get("finance_balances_asof", ""),
                },
                "recurringPayments": recurring_payments,
                "recurringPeriodNotes": [],
                "stickyNotes": sticky_notes,
            }


        elif action == "getFreshness":
            today = args.get("today", "")
            cursor = conn.cursor()
            def max_id(table):
                try:
                    cursor.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
                    row = cursor.fetchone()
                    return row[0] if row else 0
                except Exception:
                    return 0

            food_max = max_id("food_logs")
            agent_max = max_id("agent_updates")
            sob_max = max_id("sobriety_logs")
            habit_max = max_id("habit_logs")
            preset_max = max_id("food_presets")

            today_count = 0
            try:
                cursor.execute("SELECT COUNT(*) FROM food_logs WHERE date = ?", (today,))
                row = cursor.fetchone()
                if row:
                    today_count = row[0]
            except Exception:
                pass

            settings = {}
            try:
                for r in cursor.execute("SELECT key, value FROM app_settings").fetchall():
                    settings[r[0]] = r[1]
            except Exception:
                pass

            return {
                "foodMaxId": food_max,
                "agentMaxId": agent_max,
                "sobrietyMaxId": sob_max,
                "habitLogMaxId": habit_max,
                "todayFoodCount": today_count,
                "lastFullSyncAt": settings.get("last_full_sync_at", ""),
                "syncStatus": settings.get("sync_status", "idle"),
                "streakStartAt": settings.get("streak_start_at", ""),
                "kitchenStamp": 1,
                "shoppingStamp": 1,
                "foodPresetMaxId": preset_max
            }

        elif action == "logHabit":
            DataSyncManager.log_habit(args.get("habitId"), args.get("date"), args.get("value", 1), args.get("done", True), args.get("note", ""))
            return {"ok": True}

        elif action == "deleteHabitLog":
            DataSyncManager.delete_habit_log(args.get("id"))
            return {"ok": True}

        elif action == "logSobriety":
            kind = args.get("kind", "urge")
            conn.execute(
                "INSERT INTO sobriety_logs (date, kind, urge_level, trigger_note, note, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
                (args.get("date"), kind, args.get("urgeLevel", 0), args.get("triggerNote", ""), args.get("note", ""), args.get("occurredAt", "")),
            )
            if kind == "slip":
                from nanomuse.data_sync import _now_iso

                now_iso = args.get("occurredAt") or args.get("timestamp") or _now_iso()
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('streak_start', ?)", (now_iso,))
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('streak_start_at', ?)", (now_iso,))
            conn.commit()
            return {"ok": True}

        elif action == "setStreakStart":
            val = args.get("timestamp") or args.get("streakStartAt") or args.get("date")
            if val:
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('streak_start', ?)", (val,))
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('streak_start_at', ?)", (val,))
                conn.commit()
            return {"ok": True}

        elif action == "logFood":
            DataSyncManager.log_food(
                date_str=args.get("date"),
                name=args.get("name", "Food"),
                calories=float(args.get("calories", 0)),
                protein=float(args.get("protein", 0)),
                carbs=float(args.get("carbs", 0)),
                fat=float(args.get("fat", 0)),
                fiber=float(args.get("fiber", 0)),
                fluid_oz=float(args.get("fluidOz", 0)),
                slot=args.get("slot", "snack"),
                note=args.get("note", ""),
                source=args.get("source", "user"),
            )
            return {"ok": True}

        elif action == "deleteFood":
            DataSyncManager.delete_food(food_id=args.get("id"))
            return {"ok": True}

        elif action == "setAppearance":
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES ('appearance', ?)", (json.dumps(args),))
            conn.commit()
            return {"ok": True}

        elif action == "toggleTask":
            DataSyncManager.toggle_task(args.get("id"))
            return {"ok": True}

        elif action == "createTask":
            DataSyncManager.upsert_task(
                title=args.get("title", ""),
                notes=args.get("notes", ""),
                due_date=args.get("dueDate", ""),
                priority=args.get("priority", "medium"),
            )
            return {"ok": True}

        elif action == "logTrackerEntry":
            conn.execute(
                "INSERT INTO tracker_entries (tracker_id, date, value_num, value_text, time_label, note) VALUES (?, ?, ?, ?, ?, ?)",
                (args.get("trackerId"), args.get("date"), args.get("valueNum"), args.get("valueText", ""), args.get("timeLabel", ""), args.get("note", "")),
            )
            conn.commit()
            return {"ok": True}

        elif action == "markTithingPaid":
            from datetime import date as _date

            amt = args.get("amount", 0)
            date_str = args.get("date") or _date.today().isoformat()
            conn.execute("INSERT INTO tithing_payments (date, amount, note) VALUES (?, ?, 'Marked paid in One For All')", (date_str, amt))
            conn.commit()
            return {"ok": True}

        elif action == "createStickyNote":
            conn.execute(
                "INSERT INTO sticky_notes (title, body, color, category, pinned) VALUES (?, ?, ?, ?, ?)",
                (args.get("title", ""), args.get("body", ""), args.get("color", "yellow"), args.get("category", "general"), 1 if args.get("pinned", True) else 0),
            )
            conn.commit()
            return {"ok": True}

        elif action == "updateStickyNote":
            sid = args.get("id")
            cur = conn.execute("SELECT * FROM sticky_notes WHERE id = ?", (sid,)).fetchone()
            if cur:
                title = args.get("title") if args.get("title") is not None else cur["title"]
                body = args.get("body") if args.get("body") is not None else cur["body"]
                color = args.get("color") if args.get("color") is not None else cur["color"]
                pinned = 1 if args.get("pinned", cur["pinned"]) else 0
                conn.execute("UPDATE sticky_notes SET title = ?, body = ?, color = ?, pinned = ? WHERE id = ?", (title, body, color, pinned, sid))
                conn.commit()
            return {"ok": True}

        elif action == "deleteStickyNote":
            conn.execute("DELETE FROM sticky_notes WHERE id = ?", (args.get("id"),))
            conn.commit()
            return {"ok": True}

        else:
            logger.info("Unimplemented HT action requested: {}", action)
            return {"ok": True}
    finally:
        conn.close()


# ==============================================================================
# Meal Rotation Action Dispatcher
# ==============================================================================
def handle_mr_action(action: str, args: dict[str, Any]) -> Any:
    conn = get_mr_db()
    try:
        if action == "getDay":
            from datetime import date as _date

            day_key = args.get("dayKey") or _date.today().isoformat()
            meal_rows = conn.execute("SELECT * FROM meal_logs WHERE day_key = ? ORDER BY created_at ASC, id ASC", (day_key,)).fetchall()
            logs = []
            for r in meal_rows:
                d = dict(r)
                d["createdAt"] = str(d.get("created_at") or "")
                logs.append(d)

            custom_presets = [dict(r) for r in conn.execute("SELECT * FROM custom_presets ORDER BY id ASC").fetchall()]
            water_rows = conn.execute("SELECT ounces FROM water_logs WHERE day_key = ?", (day_key,)).fetchall()
            water_oz = sum(r[0] for r in water_rows)
            settings_row = conn.execute("SELECT water_goal_oz FROM settings WHERE id = 1").fetchone()
            water_goal_oz = settings_row[0] if settings_row else 128

            return {
                "logs": logs,
                "customPresets": custom_presets,
                "waterOz": water_oz,
                "waterGoalOz": water_goal_oz,
            }

        elif action == "getInventory":
            rows = conn.execute("SELECT * FROM inventory_items ORDER BY location, name").fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["quantity"] = (d.get("quantity_hundredths") or 0) / 100
                d["usagePerWeek"] = (d.get("usage_per_week_hundredths") or 0) / 100
                d["updatedAt"] = str(d.get("updated_at") or "")
                items.append(d)
            return {"items": items}

        elif action == "getHistory":
            limit = args.get("limit", 500)
            rows = conn.execute("SELECT * FROM meal_logs ORDER BY day_key DESC, id DESC LIMIT ?", (limit,)).fetchall()
            history = []
            for r in rows:
                d = dict(r)
                d["createdAt"] = str(d.get("created_at") or "")
                history.append(d)
            return {"history": history}

        elif action == "logMeal":
            DataSyncManager.log_food(
                date_str=args.get("dayKey"),
                name=args.get("name", "Meal"),
                calories=float(args.get("calories", 0)),
                protein=float(args.get("protein", 0)),
                carbs=float(args.get("carbs", 0)),
                fat=float(args.get("fat", 0)),
                fiber=float(args.get("fiber", 0)),
                fluid_oz=0.0,
                slot=args.get("slot", "snack"),
                note=args.get("details", "") or args.get("notes", ""),
                source="meal_rotation",
            )
            return {"ok": True}

        elif action == "addWater":
            DataSyncManager.log_water(
                date_str=args.get("dayKey"),
                fluid_oz=float(args.get("ounces", 32)),
                source="meal_rotation",
            )
            return {"ok": True}

        else:
            logger.info("Unimplemented MR action requested: {}", action)
            return {"ok": True}
    finally:
        conn.close()


# ==============================================================================
# Routes
# ==============================================================================
def _space_root(slug: str) -> Path:
    clean = "".join(c for c in slug.strip().lower() if c.isalnum() or c in "-_")
    for base_cand in (
        _resolve_spaces_dir() / clean,
        (_resolve_apps_dir() / clean / "space-build") if _resolve_apps_dir() else None,
        (_resolve_apps_dir() / clean / "space-build-cvm") if _resolve_apps_dir() else None,
        (_resolve_apps_dir() / clean) if _resolve_apps_dir() else None,
    ):
        if base_cand is not None and (base_cand / "index.html").is_file():
            return base_cand
    return _resolve_spaces_dir() / clean


def _serve_space_file(slug: str, file_path: str) -> FileResponse:
    base = _space_root(slug).resolve()
    if file_path:
        cand = (base / file_path).resolve()
        try:
            cand.relative_to(base)
        except ValueError as exc:
            raise HTTPException(403, "path outside space") from exc
        if cand.is_file():
            return FileResponse(cand, headers={"Cache-Control": "no-cache"})
    idx = base / "index.html"
    if idx.is_file():
        return FileResponse(idx, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, f"{slug} space build not found")


def install_spaces(
    app: FastAPI, dependencies: list[Any] | None = None, svc: Any = None
) -> None:
    global _ACTIVE_WORKSPACE, _ACTIVE_DATA_DIR
    if svc is not None:
        try:
            _ACTIVE_WORKSPACE = Path(svc.workspace())
        except Exception:
            pass
        try:
            _ACTIVE_DATA_DIR = Path(svc.data_dir)
        except Exception:
            pass
    dep = dependencies or []
    first_new = len(app.router.routes)

    # 1. Habit Tracker (One For All)
    @app.post("/spaces/habit-tracker/actions", dependencies=dep)
    @app.post("/one-for-all/actions", dependencies=dep)
    async def ht_actions(call: ActionCall) -> dict[str, Any]:
        data = handle_ht_action(call.action, call.args)
        return {"data": data}

    @app.get("/spaces/habit-tracker/{file_path:path}", include_in_schema=False)
    @app.get("/spaces/habit-tracker", include_in_schema=False)
    async def serve_ht_asset(file_path: str = "") -> FileResponse:
        return _serve_space_file("habit-tracker", file_path)

    # 2. Meal Rotation
    @app.post("/spaces/meal-rotation/actions", dependencies=dep)
    @app.post("/meal-rotation/actions", dependencies=dep)
    async def mr_actions(call: ActionCall) -> dict[str, Any]:
        data = handle_mr_action(call.action, call.args)
        return {"data": data}

    @app.get("/spaces/meal-rotation/{file_path:path}", include_in_schema=False)
    @app.get("/spaces/meal-rotation", include_in_schema=False)
    async def serve_mr_asset(file_path: str = "") -> FileResponse:
        return _serve_space_file("meal-rotation", file_path)

    # 3. MHA Rankings & any other synced Space app
    @app.get("/spaces/mha-quirk-rankings/{file_path:path}", include_in_schema=False)
    @app.get("/spaces/mha-quirk-rankings", include_in_schema=False)
    async def serve_mha_asset(file_path: str = "") -> FileResponse:
        return _serve_space_file("mha-quirk-rankings", file_path)

    @app.get("/spaces/{slug}/{file_path:path}", include_in_schema=False)
    @app.get("/spaces/{slug}", include_in_schema=False)
    async def serve_generic_space(slug: str, file_path: str = "") -> FileResponse:
        return _serve_space_file(slug, file_path)

    # 4. Top-level redirects
    @app.get("/one-for-all", include_in_schema=False)
    async def ofa_redirect() -> RedirectResponse:
        return RedirectResponse("/spaces/habit-tracker/")

    @app.get("/meal-rotation", include_in_schema=False)
    async def mr_redirect() -> RedirectResponse:
        return RedirectResponse("/spaces/meal-rotation/")

    @app.get("/mha-rankings", include_in_schema=False)
    async def mha_redirect() -> RedirectResponse:
        return RedirectResponse("/spaces/mha-quirk-rankings/")

    routes = app.router.routes
    added = routes[first_new:]
    del routes[first_new:]
    routes[0:0] = added


__all__ = ["install_spaces"]
