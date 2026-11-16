"""Authoritative Data Synchronization Engine for nanoMuse.

Ensures personal.db remains the single authoritative master source of truth,
while maintaining real-time bidirectional synchronization with Spaces databases
(habit-tracker.db and meal-rotation.db) so React apps and the AI agent never drift.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nanomuse.logger import logger

DATA_DIR = Path.home() / ".nanomuse"
PERSONAL_DB = DATA_DIR / "personal.db"
HT_DB = DATA_DIR / "habit-tracker.db"
MR_DB = DATA_DIR / "meal-rotation.db"


def configure_data_dir(path: Path | str) -> None:
    """Point the sync engine at a data directory (defaults above, overridden by the
    runtime's configured ``data_dir`` so tests and alternate installs never write
    through to the default location)."""
    global DATA_DIR, PERSONAL_DB, HT_DB, MR_DB  # noqa: PLW0603
    DATA_DIR = Path(path)
    PERSONAL_DB = DATA_DIR / "personal.db"
    HT_DB = DATA_DIR / "habit-tracker.db"
    MR_DB = DATA_DIR / "meal-rotation.db"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def get_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_personal_db() -> sqlite3.Connection:
    return get_db(PERSONAL_DB)


def get_ht_db() -> sqlite3.Connection:
    return get_db(HT_DB)


def get_mr_db() -> sqlite3.Connection:
    return get_db(MR_DB)


class DataSyncManager:
    """Manages transactional write-through synchronization across databases."""

    @staticmethod
    def log_food(
        date_str: str,
        name: str,
        calories: float,
        protein: float,
        carbs: float = 0.0,
        fat: float = 0.0,
        fiber: float = 0.0,
        fluid_oz: float = 0.0,
        slot: str = "snack",
        note: str = "",
        source: str = "ofa_ui",
    ) -> int:
        now = _now_iso()
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        mr_conn = get_mr_db()
        try:
            # 1. Authoritative write to personal.db
            cur = p_conn.execute(
                """
                INSERT INTO food_logs (date, created_at, kind, name, calories, protein, carbs, fat, fiber, fluid_oz, slot, note, source)
                VALUES (?, ?, 'food', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (date_str, now, name, calories, protein, carbs, fat, fiber, fluid_oz, slot, note, source),
            )
            p_conn.commit()
            food_id = cur.lastrowid or 0

            # 2. Sync to habit-tracker.db
            try:
                ht_conn.execute(
                    """
                    INSERT INTO food_logs (date, kind, name, calories, protein, carbs, fat, fiber, fluid_oz, slot, source, note)
                    VALUES (?, 'food', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (date_str, name, calories, protein, carbs, fat, fiber, fluid_oz, slot, source, note),
                )
                ht_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync food to habit-tracker.db: {e}")

            # 3. Sync to meal-rotation.db
            try:
                mr_conn.execute(
                    """
                    INSERT INTO meal_logs (day_key, category, meal_key, name, calories, protein, details, created_at, fiber, fluid_ounces, fat, carbs, notes, source)
                    VALUES (?, ?, 'custom', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (date_str, slot, name, calories, protein, note, now, fiber, fluid_oz, fat, carbs, note, source),
                )
                mr_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync meal to meal-rotation.db: {e}")

            return food_id
        finally:
            p_conn.close()
            ht_conn.close()
            mr_conn.close()

    @staticmethod
    def delete_food(food_id: int | None = None, date_str: str | None = None, name: str | None = None) -> None:
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        mr_conn = get_mr_db()
        try:
            # Look up item details in personal.db if id provided
            target_name = name
            target_date = date_str
            if food_id:
                row = p_conn.execute("SELECT date, name FROM food_logs WHERE id = ?", (food_id,)).fetchone()
                if row:
                    target_date = row["date"]
                    target_name = row["name"]
                p_conn.execute("DELETE FROM food_logs WHERE id = ?", (food_id,))
                p_conn.commit()

            # Sync deletion to habit-tracker.db
            if target_name and target_date:
                try:
                    ht_conn.execute("DELETE FROM food_logs WHERE date = ? AND name = ?", (target_date, target_name))
                    ht_conn.commit()
                except Exception as e:
                    logger.warning(f"DataSync: Failed to delete from habit-tracker.db: {e}")

            # Sync deletion to meal-rotation.db
            if target_name and target_date:
                try:
                    mr_conn.execute("DELETE FROM meal_logs WHERE day_key = ? AND name = ?", (target_date, target_name))
                    mr_conn.commit()
                except Exception as e:
                    logger.warning(f"DataSync: Failed to delete from meal-rotation.db: {e}")
        finally:
            p_conn.close()
            ht_conn.close()
            mr_conn.close()

    @staticmethod
    def log_water(date_str: str, fluid_oz: float, source: str = "ofa_ui") -> int:
        now = _now_iso()
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        mr_conn = get_mr_db()
        try:
            # 1. Authoritative write to personal.db
            cur = p_conn.execute(
                """
                INSERT INTO food_logs (date, created_at, kind, name, calories, protein, carbs, fat, fiber, fluid_oz, slot, source)
                VALUES (?, ?, 'water', 'Water', 0, 0, 0, 0, 0, ?, 'drink', ?)
                """,
                (date_str, now, fluid_oz, source),
            )
            p_conn.commit()
            water_id = cur.lastrowid or 0

            # 2. Sync to habit-tracker.db
            try:
                ht_conn.execute(
                    """
                    INSERT INTO food_logs (date, kind, name, calories, protein, carbs, fat, fiber, fluid_oz, slot, source, note)
                    VALUES (?, 'water', 'Water', 0, 0, 0, 0, 0, ?, 'drink', ?, '')
                    """,
                    (date_str, fluid_oz, source),
                )
                ht_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync water to habit-tracker.db: {e}")

            # 3. Write-through to meal-rotation.db
            try:
                mr_conn.execute(
                    """
                    INSERT INTO water_logs (day_key, ounces, created_at, source)
                    VALUES (?, ?, ?, ?)
                    """,
                    (date_str, int(fluid_oz), now, source),
                )
                mr_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync water to meal-rotation.db: {e}")

            return water_id
        finally:
            p_conn.close()
            ht_conn.close()
            mr_conn.close()

    @staticmethod
    def update_inventory(
        item_key: str,
        name: str,
        location: str,
        quantity: float,
        unit: str,
        ripeness_stage: str = "not_applicable",
        notes: str = "",
    ) -> None:
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        mr_conn = get_mr_db()
        try:
            # 1. Authoritative update in personal.db
            existing_p = p_conn.execute(
                "SELECT id FROM kitchen_inventory WHERE LOWER(item_key) = ? OR LOWER(name) = ?",
                (item_key.lower(), name.lower()),
            ).fetchone()
            if existing_p:
                p_conn.execute(
                    """
                    UPDATE kitchen_inventory
                    SET quantity = ?, unit = ?, location = ?, ripeness_stage = ?, quantity_confirmed = 1, notes = ?
                    WHERE id = ?
                    """,
                    (quantity, unit, location, ripeness_stage, notes, existing_p["id"]),
                )
            else:
                p_conn.execute(
                    """
                    INSERT INTO kitchen_inventory (item_key, name, location, quantity, unit, quantity_confirmed, ripeness_stage, notes)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (item_key, name, location, quantity, unit, ripeness_stage, notes),
                )
            p_conn.commit()

            # 2. Write-through to habit-tracker.db
            try:
                existing_ht = ht_conn.execute(
                    "SELECT id FROM kitchen_inventory WHERE LOWER(item_key) = ? OR LOWER(name) = ?",
                    (item_key.lower(), name.lower()),
                ).fetchone()
                if existing_ht:
                    ht_conn.execute(
                        """
                        UPDATE kitchen_inventory
                        SET quantity = ?, unit = ?, location = ?, ripeness_stage = ?, quantity_confirmed = 1, notes = ?
                        WHERE id = ?
                        """,
                        (quantity, unit, location, ripeness_stage, notes, existing_ht["id"]),
                    )
                else:
                    ht_conn.execute(
                        """
                        INSERT INTO kitchen_inventory (item_key, name, location, quantity, unit, quantity_confirmed, ripeness_stage, notes)
                        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (item_key, name, location, quantity, unit, ripeness_stage, notes),
                    )
                ht_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync inventory to habit-tracker.db: {e}")

            # 3. Write-through to meal-rotation.db
            try:
                qty_hundredths = int(round(quantity * 100))
                existing_mr = mr_conn.execute(
                    "SELECT id FROM inventory_items WHERE LOWER(item_key) = ? OR LOWER(name) = ?",
                    (item_key.lower(), name.lower()),
                ).fetchone()
                if existing_mr:
                    mr_conn.execute(
                        """
                        UPDATE inventory_items
                        SET quantity_hundredths = ?, unit = ?, location = ?, ripeness_stage = ?, quantity_confirmed = 1, notes = ?
                        WHERE id = ?
                        """,
                        (qty_hundredths, unit, location, ripeness_stage, notes, existing_mr["id"]),
                    )
                else:
                    mr_conn.execute(
                        """
                        INSERT INTO inventory_items (item_key, name, location, quantity_hundredths, unit, quantity_confirmed, ripeness_stage, notes)
                        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (item_key, name, location, qty_hundredths, unit, ripeness_stage, notes),
                    )
                mr_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync inventory to meal-rotation.db: {e}")
        finally:
            p_conn.close()
            ht_conn.close()
            mr_conn.close()

    @staticmethod
    def log_habit(habit_id_or_key: Any, date_str: str, value: float = 1.0, done: bool = True, note: str = "") -> None:
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        try:
            # 1. Resolve habit in personal.db
            h_p = p_conn.execute(
                "SELECT id, key FROM habits WHERE id = ? OR key = ?",
                (habit_id_or_key, str(habit_id_or_key)),
            ).fetchone()
            if h_p:
                p_conn.execute(
                    "INSERT INTO habit_logs (habit_id, date, value, done, note) VALUES (?, ?, ?, ?, ?)",
                    (h_p["id"], date_str, value, 1 if done else 0, note),
                )
                p_conn.commit()

            # 2. Write-through to habit-tracker.db
            try:
                h_ht = ht_conn.execute(
                    "SELECT id, key FROM habits WHERE id = ? OR key = ?",
                    (habit_id_or_key, str(habit_id_or_key)),
                ).fetchone()
                if h_ht:
                    ht_conn.execute(
                        "INSERT INTO habit_logs (habit_id, date, value, done, note) VALUES (?, ?, ?, ?, ?)",
                        (h_ht["id"], date_str, value, 1 if done else 0, note),
                    )
                    ht_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync habit log to habit-tracker.db: {e}")
        finally:
            p_conn.close()
            ht_conn.close()

    @staticmethod
    def delete_habit_log(log_id: int) -> None:
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        try:
            row = ht_conn.execute("SELECT habit_id, date FROM habit_logs WHERE id = ?", (log_id,)).fetchone()
            ht_conn.execute("DELETE FROM habit_logs WHERE id = ?", (log_id,))
            ht_conn.commit()
            if row:
                p_conn.execute("DELETE FROM habit_logs WHERE habit_id = ? AND date = ?", (row["habit_id"], row["date"]))
                p_conn.commit()
        finally:
            p_conn.close()
            ht_conn.close()

    @staticmethod
    def upsert_task(
        title: str,
        notes: str = "",
        due_date: str = "",
        priority: str = "medium",
        status: str = "open",
        list_name: str = "General",
    ) -> int:
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        try:
            # 1. Authoritative write to personal.db
            existing_p = p_conn.execute(
                "SELECT id FROM tasks WHERE LOWER(title) = ?",
                (title.strip().lower(),),
            ).fetchone()
            if existing_p:
                p_conn.execute(
                    "UPDATE tasks SET notes = ?, due_date = ?, priority = ?, status = ?, list_name = ? WHERE id = ?",
                    (notes, due_date, priority, status, list_name, existing_p["id"]),
                )
                task_id = existing_p["id"]
            else:
                cur = p_conn.execute(
                    "INSERT INTO tasks (title, notes, due_date, priority, status, list_name) VALUES (?, ?, ?, ?, ?, ?)",
                    (title.strip(), notes, due_date, priority, status, list_name),
                )
                task_id = cur.lastrowid or 0
            p_conn.commit()

            # 2. Write-through to habit-tracker.db
            try:
                existing_ht = ht_conn.execute(
                    "SELECT id FROM tasks WHERE LOWER(title) = ?",
                    (title.strip().lower(),),
                ).fetchone()
                if existing_ht:
                    ht_conn.execute(
                        "UPDATE tasks SET notes = ?, due_date = ?, priority = ?, status = ?, list_name = ? WHERE id = ?",
                        (notes, due_date, priority, status, list_name, existing_ht["id"]),
                    )
                else:
                    ht_conn.execute(
                        "INSERT INTO tasks (title, notes, due_date, priority, status, list_name) VALUES (?, ?, ?, ?, ?, ?)",
                        (title.strip(), notes, due_date, priority, status, list_name),
                    )
                ht_conn.commit()
            except Exception as e:
                logger.warning(f"DataSync: Failed to sync task to habit-tracker.db: {e}")

            return task_id
        finally:
            p_conn.close()
            ht_conn.close()

    @staticmethod
    def toggle_task(task_id: int) -> str:
        p_conn = get_personal_db()
        ht_conn = get_ht_db()
        try:
            cur = ht_conn.execute("SELECT id, title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not cur:
                cur = p_conn.execute("SELECT id, title, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not cur:
                return "unknown"
            new_st = "open" if cur["status"] == "done" else "done"
            title = cur["title"]

            p_conn.execute("UPDATE tasks SET status = ? WHERE LOWER(title) = ?", (new_st, title.lower()))
            p_conn.commit()

            ht_conn.execute("UPDATE tasks SET status = ? WHERE id = ? OR LOWER(title) = ?", (new_st, task_id, title.lower()))
            ht_conn.commit()
            return new_st
        finally:
            p_conn.close()
            ht_conn.close()
