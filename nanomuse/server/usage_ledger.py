"""The usage ledger: one line per turn, aggregated for the Usage screen.

``<data_dir>/usage.jsonl`` records what every turn spent and did — the model, its
tokens (input, cached, reasoning, output), the cost at the provider's rate card, how
long it took, what asked for it (a person, a routine, another device), and what the
turn did: tool calls grouped into activities (reading, commands, APIs, coding, files,
tracking), pictures and clips written or updated, and pictures and clips the model
looked at. The file is local, and nothing in it needs an account: the Usage screen
reads it signed-out, which is the point (the relay's own counts stay in the account).
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nanomuse.usage import estimate_turn_cost, tool_activity_counts

IMAGE_EXTS = ("png", "jpg", "jpeg", "gif", "webp")
VIDEO_EXTS = ("mp4", "mov", "webm", "avi", "mkv", "m4v")
AUDIO_EXTS = ("mp3", "wav", "m4a", "ogg", "flac", "aac")
PDF_EXTS = ("pdf",)
DOC_EXTS = ("doc", "docx", "rtf", "odt", "md", "txt")
SHEET_EXTS = ("csv", "tsv", "xls", "xlsx", "ods")
APP_EXTS = ("html", "htm", "jsx", "tsx", "vue", "svelte")
CODE_EXTS = ("py", "js", "ts", "kt", "java", "go", "rs", "c", "cpp", "cs", "sh", "ps1", "sql", "json", "yaml", "yml", "toml", "css")


def media_kind(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return ""


def file_category(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in PDF_EXTS:
        return "pdf"
    if ext in SHEET_EXTS:
        return "spreadsheet"
    if ext in APP_EXTS:
        return "app"
    if ext in CODE_EXTS:
        return "code"
    if ext in DOC_EXTS:
        return "document"
    if ext in AUDIO_EXTS:
        return "audio"
    return ""


class UsageLedger:
    """Append-only JSONL of turn records plus the aggregates the screen shows."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ recording
    def record_turn(
        self,
        *,
        ts: str,
        thread: str,
        model: str,
        source: str,
        trace: dict[str, Any],
        tool_actions: dict[str, str] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """One turn's record: tokens, cost, activities, media. Returns what was written."""
        prompt = int(trace.get("prompt_tokens") or 0)
        cache = min(int(trace.get("cache_tokens") or 0), prompt)
        completion = int(trace.get("completion_tokens") or 0)
        reasoning = int(trace.get("reasoning_tokens") or 0)
        act_counts = tool_activity_counts(trace.get("tool_names") or [], tool_actions)
        if str(source or "").lower() in ("call", "live_call", "voice"):
            act_counts["live_calls"] = act_counts.get("live_calls", 0) + 1
        media_counts = self._media_counts(artifacts, attachments)
        entry: dict[str, Any] = {
            "ts": ts,
            "thread": thread,
            "model": model,
            "source": source,
            "prompt_tokens": prompt,
            "cache_tokens": cache,
            "reasoning_tokens": min(reasoning, completion),
            "completion_tokens": completion,
            "cost_usd": round(estimate_turn_cost(model, prompt, cache, completion), 6),
            "duration_s": round(float(trace.get("duration_s") or 0), 2),
            "tool_calls": int(trace.get("tool_calls") or 0),
            "model_calls": int(trace.get("model_calls") or 0),
            "activities": act_counts,
            "media": media_counts,
        }
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # a crash mid-write can leave a line without its newline: start the next
            # record on a fresh line, so the fragment (skipped by the screen) does not
            # swallow this record too
            prefix = ""
            if self.path.is_file() and self.path.stat().st_size > 0:
                with self.path.open("rb") as f:
                    f.seek(-1, 2)
                    if f.read(1) != b"\n":
                        prefix = "\n"
            with self.path.open("a", encoding="utf-8") as f:
                f.write(prefix + json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    @staticmethod
    def _media_counts(
        artifacts: list[dict[str, Any]] | None, attachments: list[dict[str, Any]] | None
    ) -> dict[str, int]:
        counts = {
            "image_generation": 0,
            "image_editing": 0,
            "video_generation": 0,
            "video_editing": 0,
            "photo_analysis": 0,
            "video_analysis": 0,
            "pdf_read": 0,
            "document_read": 0,
            "spreadsheet_read": 0,
            "code_read": 0,
            "audio_read": 0,
            "app_built": 0,
            "pdf_built": 0,
            "document_built": 0,
            "spreadsheet_built": 0,
            "code_built": 0,
        }
        for a in artifacts or []:
            fname = str(a.get("path") or a.get("name") or "")
            kind = media_kind(fname)
            action = str(a.get("action") or "")
            if kind:
                key = f"{kind}_{'editing' if action == 'update' else 'generation'}"
                counts[key] = counts.get(key, 0) + 1
                continue
            fcat = file_category(fname)
            if fcat:
                bkey = f"{fcat}_built"
                if bkey in counts:
                    counts[bkey] += 1
        for a in attachments or []:
            fname = str(a.get("name") or a.get("path") or "")
            kind = media_kind(fname)
            if kind == "image":
                counts["photo_analysis"] += 1
                continue
            if kind == "video":
                counts["video_analysis"] += 1
                continue
            fcat = file_category(fname)
            if fcat:
                rkey = f"{fcat}_read"
                if rkey in counts:
                    counts[rkey] += 1
        return counts

    # ------------------------------------------------------------------ reading
    def _entries(self, days: int) -> list[dict[str, Any]]:
        """The ledger lines newer than ``days``, oldest first. An unreadable line is
        skipped, not fatal: a half-written file (a crash mid-write) still aggregates."""
        if not self.path.is_file():
            return []
        cutoff = (datetime.now(UTC) - timedelta(days=max(days, 1))).isoformat(timespec="seconds")
        out: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or '"ts"' not in line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(entry.get("ts", "")) >= cutoff:
                        out.append(entry)
        except OSError:
            return []
        return out

    def summary(self, days: int = 30, model: str = "") -> dict[str, Any]:
        """What the Usage screen shows: totals, per day, per model, per activity, media.
        ``model`` (an exact model id) narrows every figure to that one AI's turns."""
        entries = self._entries(days)
        if model:
            entries = [e for e in entries if str(e.get("model") or "") == model]
        per_day: dict[str, dict[str, Any]] = {}
        per_model: dict[str, dict[str, Any]] = {}
        activities: dict[str, int] = defaultdict(int)
        media: dict[str, int] = defaultdict(int)
        total_cost = 0.0
        total_prompt = total_cached = total_reasoning = total_completion = 0
        total_duration = 0.0
        total_tools = 0
        recent: list[dict[str, Any]] = []
        for e in entries:
            day = str(e.get("ts", ""))[:10]
            d = per_day.setdefault(
                day,
                {
                    "day": day,
                    "turns": 0,
                    "prompt_tokens": 0,
                    "cached_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            d["turns"] += 1
            p = int(e.get("prompt_tokens") or 0)
            c = int(e.get("cache_tokens") or 0)
            o = int(e.get("completion_tokens") or 0)
            d["prompt_tokens"] += p
            d["cached_tokens"] += c
            d["completion_tokens"] += o
            d["cost_usd"] = round(d["cost_usd"] + float(e.get("cost_usd") or 0), 4)
            total_prompt += p
            total_cached += c
            total_reasoning += int(e.get("reasoning_tokens") or 0)
            total_completion += o
            total_cost += float(e.get("cost_usd") or 0)
            total_duration += float(e.get("duration_s") or 0)
            total_tools += int(e.get("tool_calls") or 0)
            m = per_model.setdefault(
                str(e.get("model") or "unknown"),
                {
                    "model": str(e.get("model") or "unknown"),
                    "turns": 0,
                    "prompt_tokens": 0,
                    "cached_tokens": 0,
                    "reasoning_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                },
            )
            m["turns"] += 1
            m["prompt_tokens"] += p
            m["cached_tokens"] += c
            m["reasoning_tokens"] += int(e.get("reasoning_tokens") or 0)
            m["completion_tokens"] += o
            m["cost_usd"] = round(m["cost_usd"] + float(e.get("cost_usd") or 0), 4)
            for k, v in (e.get("activities") or {}).items():
                if v:
                    activities[k] += v
            for k, v in (e.get("media") or {}).items():
                if v:
                    media[k] += v
            if len(recent) < 12:
                recent.append(
                    {
                        "ts": e.get("ts"),
                        "thread": e.get("thread"),
                        "model": e.get("model"),
                        "source": e.get("source"),
                        "cost_usd": e.get("cost_usd"),
                        "duration_s": e.get("duration_s"),
                        "completion_tokens": o,
                    }
                )
        turns = len(entries)
        return {
            "days": days,
            "model": model,
            "turns": turns,
            "prompt_tokens": total_prompt,
            "cached_tokens": total_cached,
            "reasoning_tokens": total_reasoning,
            "completion_tokens": total_completion,
            "cost_usd": round(total_cost, 4),
            "avg_duration_s": round(total_duration / turns, 1) if turns else 0.0,
            "tool_calls": total_tools,
            "per_day": [per_day[k] for k in sorted(per_day)],
            "per_model": sorted(per_model.values(), key=lambda m: -m["cost_usd"]),
            "activities": dict(sorted(activities.items(), key=lambda kv: -kv[1])),
            "media": dict(sorted(media.items(), key=lambda kv: -kv[1])),
            "recent": list(reversed(recent)),
        }


__all__ = ["UsageLedger", "media_kind"]
