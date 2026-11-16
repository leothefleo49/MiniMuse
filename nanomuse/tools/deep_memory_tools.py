"""Deep Memory & Archive Search tool for MiniMuse — FTS5 & semantic search across imported chats, memory files, and goals."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from nanomuse.logger import logger
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.tools.base import BaseTool, CallAssessment, int_arg
from nanomuse.tools.ofa_tools import DEFAULT_PERSONAL_DB, get_db


def _fts_query(raw: str) -> str:
    """Turn a natural-language query into a safe SQLite FTS5 OR/AND match string."""
    words = [w for w in re.findall(r"[A-Za-z0-9_]+", raw) if len(w) > 1]
    if not words:
        return '""'
    if len(words) == 1:
        return f'"{words[0]}"*'
    return " OR ".join(f'"{w}"' for w in words[:10])


class DeepMemoryTool(BaseTool):
    name: str = "deep_memory"
    description: str = (
        "FTS5 and semantic search over your imported archive (chat transcripts, memory files, "
        "people dossiers, and goals). Use when the user asks about a person, past conversation, "
        "event, quote, or project. Actions: "
        "search_all / search_memory / search_chats (query), semantic_search (query, by meaning "
        "across the embedded archive), get_person (name), read_memory_file (path), "
        "append_daily_note, update_person."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search_all",
                    "search_memory",
                    "search_chats",
                    "semantic_search",
                    "get_person",
                    "read_memory_file",
                    "append_daily_note",
                    "update_person",
                ],
            },
            "query": {"type": "string", "description": "Keywords or phrase to search for"},
            "name": {"type": "string", "description": "Person or group name (e.g. 'Alex', 'Jordan')"},
            "path": {"type": "string", "description": "Relative path such as 'memory/MEMORY.md', 'memory/2026-11-07.md', 'goal/project-launch/GOAL.md'"},
            "note": {"type": "string", "description": "Note or fact to append to daily log or person dossier"},
            "limit": {"type": "integer", "description": "Max results (default 10)"},
        },
        "required": ["action"],
    }
    risk: RiskLevel = RiskLevel.SAFE
    reads_private_data: bool = True
    db_path: Path = DEFAULT_PERSONAL_DB

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        act = str(args.get("action") or "")
        target = str(args.get("query") or args.get("name") or args.get("path") or "")
        return CallAssessment(
            risk=RiskLevel.SAFE,
            reads_private_data=True,
            summary=f"deep_memory.{act}({target[:60]})",
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip()
        limit = int_arg(kwargs.get("limit"), 10, 1, 50)
        conn = get_db(self.db_path)
        data_dir = self.db_path.parent
        try:
            if action == "search_all":
                q = str(kwargs.get("query") or "").strip()
                if not q:
                    return ToolResult.fail("Provide `query`.")
                mem_hits = self._search_memory(conn, q, limit=limit)
                chat_hits = self._search_chats(conn, q, limit=limit)
                out: dict[str, Any] = {"query": q, "memory_hits": mem_hits, "chat_hits": chat_hits}
                # keyword search found nothing: fall back to meaning, so the archive is
                # searchable the way the user phrases things rather than exact spelling
                if not mem_hits and not chat_hits:
                    sem = await self._semantic_search(conn, q, limit=min(limit, 8))
                    if sem:
                        out["semantic_hits"] = sem
                return ToolResult(output=json.dumps(out, ensure_ascii=False, indent=2))
            if action == "semantic_search":
                q = str(kwargs.get("query") or "").strip()
                if not q:
                    return ToolResult.fail("Provide `query`.")
                sem = await self._semantic_search(conn, q, limit=limit)
                return ToolResult(
                    output=json.dumps(sem if sem else [], ensure_ascii=False, indent=2)
                )
            if action == "search_memory":
                q = str(kwargs.get("query") or "").strip()
                if not q:
                    return ToolResult.fail("Provide `query`.")
                return ToolResult(
                    output=json.dumps(self._search_memory(conn, q, limit=limit), ensure_ascii=False, indent=2)
                )
            if action == "search_chats":
                q = str(kwargs.get("query") or "").strip()
                if not q:
                    return ToolResult.fail("Provide `query`.")
                return ToolResult(
                    output=json.dumps(self._search_chats(conn, q, limit=limit), ensure_ascii=False, indent=2)
                )
            if action == "get_person":
                name = str(kwargs.get("name") or kwargs.get("query") or "").strip()
                return self._get_person(data_dir, name)
            if action == "read_memory_file":
                rel = str(kwargs.get("path") or "").strip()
                return self._read_memory_file(data_dir, rel)
            if action == "append_daily_note":
                note = str(kwargs.get("note") or "").strip()
                return self._append_daily_note(conn, data_dir, note)
            if action == "update_person":
                name = str(kwargs.get("name") or "").strip()
                note = str(kwargs.get("note") or "").strip()
                return self._update_person(conn, data_dir, name, note)
            return ToolResult.fail(f"Unknown action: {action}")
        finally:
            conn.close()

    def _search_memory(self, conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
        fts_q = _fts_query(query)
        rows = conn.execute(
            """
            SELECT path, title, category, snippet(memory_fts, 3, '«', '»', ' … ', 40) AS snippet
            FROM memory_fts
            WHERE memory_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_q, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def _search_chats(self, conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict[str, Any]]:
        fts_q = _fts_query(query)
        rows = conn.execute(
            """
            SELECT turn_id, thread_id, role, created_at, snippet(messages_fts, 4, '«', '»', ' … ', 45) AS snippet
            FROM messages_fts
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_q, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    async def _semantic_search(
        self, conn: sqlite3.Connection, query: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Cosine search over the embedded archive (`deep_chunks`, built by
        scripts/import_muse_archive.py). Returns nothing when the index does not exist yet."""
        try:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='deep_chunks'"
            ).fetchone()
        except sqlite3.Error:
            return []
        if not has:
            return []
        qvec = await self._embed_query(query)
        if qvec is None:
            return []
        try:
            import numpy as np
        except ImportError:
            return []
        rows = conn.execute("SELECT id, source, title, path, text, vec FROM deep_chunks").fetchall()
        if not rows:
            return []
        q = np.asarray(qvec, dtype=np.float32)
        matrix = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
        qn = q / (np.linalg.norm(q) + 1e-9)
        mn = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9)
        scores = mn @ qn
        top = int(min(limit, len(rows)))
        idx = np.argpartition(-scores, top - 1)[:top]
        idx = idx[np.argsort(-scores[idx])]
        out = []
        for i in idx.tolist():
            r = rows[i]
            score = float(scores[i])
            if score < 0.25:
                continue
            out.append(
                {
                    "source": r["source"],
                    "title": r["title"],
                    "path": r["path"],
                    "score": round(score, 3),
                    "text": r["text"][:600],
                }
            )
        return out

    @staticmethod
    async def _embed_query(query: str) -> list[float] | None:
        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/embed",
            data=json.dumps(
                {"model": "nomic-embed-text", "input": query, "keep_alive": "5m"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            vec = data["embeddings"][0]
            return [float(x) for x in vec]
        except Exception as exc:  # noqa: BLE001 - a search must never crash a turn
            logger.warning("semantic_search: embedding failed: %s", exc)
            return None

    def _get_person(self, data_dir: Path, name: str) -> ToolResult:
        if not name:
            return ToolResult.fail("Provide `name` of the person or group.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        people_dir = data_dir / "muse_memory" / "people"
        groups_dir = data_dir / "muse_memory" / "groups"
        matches: list[Path] = []
        for folder in (people_dir, groups_dir):
            if not folder.exists():
                continue
            exact = folder / f"{slug}.md"
            if exact.exists():
                matches.insert(0, exact)
            for p in folder.glob("*.md"):
                if p not in matches and (slug in p.stem.lower() or name.lower() in p.read_text("utf-8", errors="replace").lower()):
                    matches.append(p)
        if not matches:
            return ToolResult(output=f"No person or group dossier found matching '{name}'.")
        out_parts = []
        for m in matches[:4]:
            out_parts.append(f"=== {m.relative_to(data_dir).as_posix()} ===\n{m.read_text('utf-8', errors='replace')[:4000]}")
        return ToolResult(output="\n\n".join(out_parts))

    def _read_memory_file(self, data_dir: Path, rel: str) -> ToolResult:
        if not rel:
            return ToolResult.fail("Provide `path`.")
        rel_clean = rel.lstrip("/\\")
        candidates = [
            data_dir / rel_clean,
            data_dir / "muse_memory" / rel_clean.removeprefix("memory/"),
            data_dir / "muse_goals" / rel_clean.removeprefix("goal/").removeprefix("goals/"),
            data_dir / "muse_projects" / rel_clean.removeprefix("project/").removeprefix("projects/"),
            data_dir / "muse_transcripts" / rel_clean.removeprefix("transcripts/"),
        ]
        for c in candidates:
            if c.is_file():
                return ToolResult(output=c.read_text("utf-8", errors="replace")[:15000])
        return ToolResult.fail(f"File not found: {rel}")

    def _append_daily_note(self, conn: sqlite3.Connection, data_dir: Path, note: str) -> ToolResult:
        if not note:
            return ToolResult.fail("Provide `note` to append.")
        now = datetime.now().astimezone()
        day_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%I:%M %p %Z")
        daily_file = data_dir / "muse_memory" / f"{day_str}.md"
        daily_file.parent.mkdir(parents=True, exist_ok=True)
        existing = daily_file.read_text("utf-8", errors="replace") if daily_file.exists() else f"# Daily Memory — {day_str}\n\n"
        updated = existing.rstrip() + f"\n- **[{time_str}]** {note}\n"
        daily_file.write_text(updated, "utf-8")
        conn.execute("DELETE FROM memory_fts WHERE path = ?", (f"memory/{day_str}.md",))
        conn.execute(
            "INSERT INTO memory_fts (path, title, category, content) VALUES (?, ?, 'memory', ?)",
            (f"memory/{day_str}.md", f"Daily Memory — {day_str}", updated),
        )
        conn.commit()
        return ToolResult(output=f"Appended note to memory/{day_str}.md at {time_str}.")

    def _update_person(self, conn: sqlite3.Connection, data_dir: Path, name: str, note: str) -> ToolResult:
        if not name or not note:
            return ToolResult.fail("Provide both `name` and `note`.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        pfile = data_dir / "muse_memory" / "people" / f"{slug}.md"
        pfile.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.now().astimezone().strftime("%Y-%m-%d")
        if pfile.exists():
            txt = pfile.read_text("utf-8", errors="replace")
            updated = txt.rstrip() + f"\n- ({now_str}) {note}\n"
        else:
            updated = f"---\ndisplay_name: {name}\nupdated: {now_str}\n---\n\n# {name}\n\n## Facts\n- ({now_str}) {note}\n"
        pfile.write_text(updated, "utf-8")
        rel_path = f"memory/people/{slug}.md"
        conn.execute("DELETE FROM memory_fts WHERE path = ?", (rel_path,))
        conn.execute(
            "INSERT INTO memory_fts (path, title, category, content) VALUES (?, ?, 'memory', ?)",
            (rel_path, name, updated),
        )
        conn.commit()
        return ToolResult(output=f"Updated person dossier {rel_path}.")
