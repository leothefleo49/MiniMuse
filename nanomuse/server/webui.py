"""A :class:`nanomuse.ui.UI` implementation that speaks *events* instead of printing.

One ``WebUI`` serves every thread. The thread an agent callback belongs to is
carried in a :mod:`contextvars` variable set by the worker task, so several
threads (main chat + side chats) can run at the same time and still land their
bubbles, tool chips and approval cards in the right place.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nanomuse.llm import session as llm_session
from nanomuse.logger import logger
from nanomuse.prompts import split_quiet
from nanomuse.schema import ToolCall, ToolResult
from nanomuse.server.events import MAIN_THREAD, EventBus, Timeline, new_id, now_iso
from nanomuse.tools.browser import BrowserFrame
from nanomuse.ui import ApprovalDecision, ApprovalRequest

current_thread: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nanomuse_thread", default=MAIN_THREAD
)

# browser frames kept per thread (in memory; a restart clears them)
_FRAMES_KEPT = 12

_TOOL_LABELS = {
    "web_search": "Searching the web",
    "web_fetch": "Reading a web page",
    "files": "Working with files",
    "shell": "Running a command",
    "python_execute": "Running Python",
    "read_emails": "Reading email",
    "send_email": "Sending email",
    "browser": "Browsing",
    "goals": "Updating goals",
    "remember": "Saving a memory",
    "recall": "Recalling memories",
    "forget": "Forgetting a memory",
    "ask_user": "Waiting for you",
    "terminate": "Wrapping up",
    "ofa_tracker": "Updating One For All",
    "deep_memory": "Searching deep memory & archive",
    "phone_bridge": "Connecting to phone",
    "external_connectors": "Checking external integrations",
    "app_builder": "Building custom app",
}

# Tools that cannot create files; no point scanning the workspace around them.
_READ_ONLY_TOOLS = {
    "web_search",
    "web_fetch",
    "recall",
    "remember",
    "forget",
    "goals",
    "ask_user",
    "terminate",
    "read_emails",
    "send_email",
    # looks at this computer's screen land in screenshots/; they are not the agent's work
    "computer_screen",
    "computer_act",
    "computer_task",
    "ofa_tracker",
    "deep_memory",
    "phone_bridge",
    "external_connectors",
}
# the browser's persistent profile lives in the workspace too (cookies, caches) — never an artifact
_SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".git", "browser-profile"}
_SCAN_CAP = 3000

# One For All actions, in words for the pill when the model did not write `step`
# (some models skip it; the chip then reads like a log line instead of a raw name).
_OFA_ACTION_LABELS = {
    "dashboard_summary": "Reading the One For All dashboard",
    "get_day": "Reading One For All logs",
    "log_food": "Logging food",
    "log_water": "Logging water",
    "log_preset": "Logging a preset meal",
    "list_presets": "Listing meal presets",
    "log_workout": "Logging a workout",
    "log_habit": "Logging a habit",
    "get_sobriety": "Reading sobriety logs",
    "log_sobriety": "Logging a sobriety check-in",
    "log_health": "Logging health data",
    "get_finance": "Reading finances",
    "log_finance": "Logging spending",
    "list_tasks": "Listing tasks",
    "upsert_task": "Updating a task",
    "complete_task": "Completing a task",
    "list_stickies": "Listing stickies",
    "upsert_sticky": "Updating a sticky",
    "sql_query": "Reading One For All data",
}


def _step_words(call: ToolCall) -> str:
    """Words for a step the model did not name: the tool's label, sharpened by its
    main argument ("Searching the web: nanoMuse water bottle"). Better than the
    bare summary for the pill, and never claims more than the call says."""

    args = call.arguments if isinstance(call.arguments, dict) else {}
    if call.name == "ofa_tracker":
        base = _OFA_ACTION_LABELS.get(str(args.get("action") or ""))
        if base:
            detail = str(args.get("habit_key") or args.get("name") or "").strip()
            return f"{base}: {detail}" if detail else base
    label = _TOOL_LABELS.get(call.name, "")
    for key in ("query", "name", "path", "action", "habit_key", "kind"):
        value = str(args.get(key) or "").strip()
        if value and value.lower() not in label.lower():
            return f"{label}: {value[:60]}"
    return label


class WebUI:
    """Turns agent callbacks into timeline events + live WebSocket messages."""

    def __init__(
        self,
        bus: EventBus,
        get_timeline: Callable[[str], Timeline],
        approval_timeout: float = 3600.0,
        show_thinking: bool = False,
        workspace: Path | None = None,
        exclude: tuple[Path, ...] = (),
    ):
        self.bus = bus
        self.get_timeline = get_timeline
        self.approval_timeout = approval_timeout
        self.show_thinking = show_thinking
        self.workspace = workspace
        # directories inside the workspace that are nanoMuse's own (the data dir, when
        # someone points both at the same place) — never artifacts. A data dir that
        # *contains* the workspace (~/.nanomuse and ~/.nanomuse/workspace) is not inside
        # it and must not blank the whole scan.
        ws = workspace.resolve() if workspace is not None else None
        self.exclude = tuple(
            p.resolve() for p in exclude if ws is None or ws in p.resolve().parents
        )
        # workspace snapshot taken before a tool ran, per thread; files that are new or
        # changed afterwards become artifact cards — whatever tool wrote them
        self._ws_before: dict[str, dict[str, float] | None] = {}
        # artifact events already shown in the current run: path -> event id
        self._artifacts: dict[str, dict[str, str]] = {}
        self.pending_approvals: dict[str, asyncio.Future[ApprovalDecision]] = {}
        self.pending_questions: dict[str, asyncio.Future[str]] = {}  # thread -> future
        self.status: dict[str, dict[str, Any]] = {}
        # streaming state per thread
        self._stream_ids: dict[str, str] = {}
        self._stream_buf: dict[str, str] = {}
        self._stream_reasoning: dict[str, str] = {}
        self._tool_events: dict[str, str] = {}  # tool call id -> event id
        self._tool_events_thread: dict[str, str] = {}  # tool call id -> thread
        self.last_assistant_text: dict[str, str] = {}
        self.last_assistant_event: dict[str, str] = {}  # thread -> id of that bubble
        self._step_text: dict[str, str] = {}  # text of the response currently being handled
        # threads whose final reply is already on screen (text + terminate in one response)
        self.reply_shown: set[str] = set()
        # threads currently running background work (goal passes…) → the label of that work.
        # Events emitted meanwhile are tagged so the Feed can show what happened while you
        # were away.
        self.background: dict[str, str] = {}
        # threads on *Use nanoMuse Cloud this time* → the model id; the turn's reply and a
        # failure of it carry it as `model_used`
        self.model_once: dict[str, str] = {}
        # called with every persisted event; the service decides what deserves a push
        self.on_event: Callable[[dict[str, Any]], None] | None = None
        # the browser as the user sees it: recent frames per thread (id -> jpeg) and the
        # card of the current run, which is updated in place frame after frame
        self.browser_frames: dict[str, OrderedDict[str, bytes]] = {}
        self._browser_card: dict[str, str] = {}
        # the hands on this computer: recent viewport/window frames and the active task card
        self.hands_frames: dict[str, OrderedDict[str, bytes]] = {}
        self._hands_card: dict[str, str] = {}

    # ------------------------------------------------------------------ run lifecycle
    def begin_run(self, thread: str, background: str | None = None, model_used: str = "") -> None:
        """Called by the service before each agent run in ``thread``. ``model_used`` names
        the model of a turn running on *Use nanoMuse Cloud this time*."""
        self.reply_shown.discard(thread)
        if model_used:
            self.model_once[thread] = model_used
        else:
            self.model_once.pop(thread, None)
        self.last_assistant_event.pop(thread, None)
        self._step_text.pop(thread, None)
        self._artifacts[thread] = {}
        self._browser_card.pop(thread, None)
        if background:
            self.background[thread] = background
        else:
            self.background.pop(thread, None)

    def end_run(self, thread: str) -> None:
        self.background.pop(thread, None)
        self.model_once.pop(thread, None)
        self._ws_before.pop(thread, None)
        card = self._browser_card.pop(thread, None)
        if card is not None:
            self.patch(thread, card, status="done", updated_ts=now_iso())
        # a bubble still streaming when the run ended without a message (the model call
        # failed after its first words, the person stopped it): closed here, so it does
        # not sit open in the page or take the next run's words
        sid = self._stream_ids.pop(thread, None)
        self._stream_buf.pop(thread, None)
        self._stream_reasoning.pop(thread, None)
        if sid is not None:
            self.bus.publish({"kind": "stream_end", "thread": thread, "id": sid, "discard": True})
        # a tool step still open when the run ended (interrupted mid-call, a crash, the
        # person stopped it): closed here, so its chip does not claim the run is working
        for call_id, eid in list(self._tool_events.items()):
            if self._tool_events_thread.get(call_id) == thread:
                self.patch(thread, eid, status="error", output="interrupted")
                self._tool_events.pop(call_id, None)
                self._tool_events_thread.pop(call_id, None)

    # ------------------------------------------------------------------ browser view
    def on_browser_frame(self, frame: BrowserFrame) -> None:
        """A new picture of the browser: keep it, and show/update the browser card."""
        thread = frame.thread or self.thread()
        frames = self.browser_frames.setdefault(thread, OrderedDict())
        fid = new_id("f")
        frames[fid] = frame.jpeg
        while len(frames) > _FRAMES_KEPT:
            frames.popitem(last=False)
        fields = {
            "url": frame.url,
            "title": frame.title,
            "action": frame.action,
            "frame": fid,
            "by_user": frame.by_user,
            "backend": frame.backend,
            "width": frame.width,
            "height": frame.height,
            "status": "live",
            "updated_ts": now_iso(),
        }
        card = self._browser_card.get(thread)
        if card is None and frame.by_user:
            # the user is driving between runs: refresh the last browser card if there is one
            for ev in reversed(self.get_timeline(thread).events):
                if ev.get("type") == "browser":
                    card = ev["id"]
                    break
        if card is not None:
            existing = self.get_timeline(thread).get(card)
            fields["frames"] = int((existing or {}).get("frames", 0)) + 1
            was_live = (existing or {}).get("status") == "live"
            keep_hold = (
                frame.by_user
                and frame.action != "You handed the page back"
                and (was_live or frame.action == "You have the page")
            )
            fields["status"] = "live" if (thread in self._browser_card or keep_hold) else "done"
            self.patch(thread, card, **fields)
            return
        ev = self.emit({"type": "browser", "thread": thread, "frames": 1, **fields})
        self._browser_card[thread] = ev["id"]

    def on_hands(self, body: dict[str, Any]) -> None:
        """A step of the hands on this computer (see ``ComputerLink.on_event``): one *Hands*
        card per task, updated in place, plus live screenshots and a live message for the stage overlay."""
        payload = dict(body)
        jpeg = payload.pop("jpeg", None)
        event = str(payload.get("event") or "")
        thread = self.thread()
        if event != "frame":
            self.bus.publish({"kind": "hands", "thread": thread, **payload})
        if event == "begin":
            ev = self.emit(
                {
                    "type": "hands",
                    "thread": thread,
                    "text": str(payload.get("text") or ""),
                    "status": "live",
                    "steps": 0,
                    "updated_ts": now_iso(),
                }
            )
            self._hands_card[thread] = ev["id"]
            return
        card = self._hands_card.get(thread)
        if event == "frame" and isinstance(jpeg, (bytes, bytearray)) and jpeg:
            frames = self.hands_frames.setdefault(thread, OrderedDict())
            fid = new_id("hf")
            frames[fid] = bytes(jpeg)
            while len(frames) > _FRAMES_KEPT:
                frames.popitem(last=False)
            target_card = card
            if target_card is None:
                for existing_ev in reversed(self.get_timeline(thread).events):
                    if existing_ev.get("type") == "hands":
                        target_card = existing_ev.get("id")
                        break
            if target_card is None:
                return
            fields: dict[str, Any] = {
                "frame": fid,
                "mode": str(payload.get("mode") or "screen"),
                "updated_ts": now_iso(),
            }
            if payload.get("app"):
                fields["app"] = str(payload["app"])
            if payload.get("title"):
                fields["title"] = str(payload["title"])
            if payload.get("width"):
                fields["width"] = int(payload["width"])
            if payload.get("height"):
                fields["height"] = int(payload["height"])
            self.patch(thread, target_card, **fields)
            return
        if event == "act":
            fields = {
                "last": {
                    k: payload.get(k)
                    for k in (
                        "action",
                        "label",
                        "x",
                        "y",
                        "x2",
                        "y2",
                        "fx",
                        "fy",
                        "fx2",
                        "fy2",
                        "mode",
                        "text",
                        "keys",
                    )
                    if payload.get(k) is not None
                },
                "app": payload.get("app") or "",
                "title": payload.get("title") or "",
                "updated_ts": now_iso(),
            }
            if payload.get("mode"):
                fields["mode"] = str(payload["mode"])
            if card is None:
                # a single computer_act outside a task: its own small card
                ev = self.emit(
                    {
                        "type": "hands",
                        "thread": thread,
                        "text": "",
                        "status": "done",
                        "steps": 1,
                        **fields,
                    }
                )
                return
            existing = self.get_timeline(thread).get(card) or {}
            self.patch(thread, card, steps=int(existing.get("steps", 0)) + 1, **fields)
        elif event in ("end", "stop", "notice"):
            if card is None:
                return
            status = "stopped" if event == "stop" else "done"
            fields = {"status": status, "updated_ts": now_iso()}
            if event == "notice" and payload.get("text"):
                fields["notice"] = str(payload["text"])
            self.patch(thread, card, **fields)
            if event != "notice":
                self._hands_card.pop(thread, None)

    def browser_frame(self, thread: str, fid: str) -> bytes | None:
        frames = self.browser_frames.get(thread)
        return frames.get(fid) if frames is not None else None

    def hands_frame(self, thread: str, fid: str) -> bytes | None:
        frames = self.hands_frames.get(thread)
        return frames.get(fid) if frames is not None else None

    # ------------------------------------------------------------------ holds (C1)
    def on_hold(self, event: dict[str, Any]) -> None:
        """A hold went on or off (:mod:`nanomuse.agent.holds`): one *hold* card per hold in
        the chat, updated in place when it goes off, and the live frame for every client."""
        thread = str(event.get("thread") or self.thread())
        timeline = self.get_timeline(thread)
        if timeline.get(str(event.get("id"))) is None:
            self.emit({**event, "thread": thread})
            return
        fields = {k: v for k, v in event.items() if k not in ("id", "type", "thread")}
        self.patch(thread, str(event["id"]), **fields)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def thread() -> str:
        return current_thread.get()

    def _scan_workspace(self) -> dict[str, float] | None:
        """Relative path → mtime for every visible file, or None when the workspace is
        too big to diff cheaply (then only the files tool announces artifacts)."""
        ws = self.workspace
        if ws is None or not ws.is_dir():
            return None
        out: dict[str, float] = {}
        for root, dirs, files in os.walk(ws):
            here = Path(root)
            if any(here == x or x in here.parents for x in self.exclude):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
            for name in files:
                if name.startswith("."):
                    continue
                p = Path(root) / name
                try:
                    out[p.relative_to(ws).as_posix()] = p.stat().st_mtime
                except OSError:
                    continue
                if len(out) > _SCAN_CAP:
                    return None
        return out

    def _announce_artifact(self, thread: str, rel: str, action: str) -> None:
        seen = self._artifacts.setdefault(thread, {})
        if rel in seen:
            # The same file touched again in this run: refresh the card, don't stack another.
            # The action stays what it was — a page written in parts (write, then append)
            # is still a new page, not an update of something the user had before.
            self.patch(thread, seen[rel], updated_ts=now_iso())
            return
        ev = self.emit(
            {
                "type": "artifact",
                "path": rel,
                "name": rel.rsplit("/", 1)[-1],
                "action": action,
                "thread": thread,
            }
        )
        seen[rel] = ev["id"]

    def emit(self, event: dict[str, Any], persist: bool = True) -> dict[str, Any]:
        thread = event.get("thread") or self.thread()
        event["thread"] = thread
        if (
            thread in self.background
            and "source" not in event
            # what the user typed while a background pass runs in the thread, and the look
            # card that answers it, are theirs — not the pass's, and not Feed items
            and event.get("type") not in ("user", "avatar")
        ):
            event["source"] = "background"
            event["about"] = self.background[thread]
        if persist:
            event = self.get_timeline(thread).add(event)
        else:
            event.setdefault("id", new_id())
            event.setdefault("ts", now_iso())
        self.bus.publish({"kind": "event", "event": event})
        if persist and self.on_event is not None:
            try:
                self.on_event(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning("event hook failed: {}", exc)
        return event

    def patch(self, thread: str, event_id: str, **fields: Any) -> None:
        ev = self.get_timeline(thread).update(event_id, **fields)
        if ev is not None:
            self.bus.publish({"kind": "update", "event": ev})

    def set_status(self, state: str, detail: str = "", thread: str | None = None) -> None:
        thread = thread or self.thread()
        self.status[thread] = {"thread": thread, "state": state, "detail": detail, "ts": now_iso()}
        self.bus.publish({"kind": "status", "status": self.status[thread]})

    def overall_status(self) -> dict[str, Any]:
        """What the avatar shows: the busiest thread wins, main chat breaks ties."""
        working = [s for s in self.status.values() if s["state"] != "idle"]
        if not working:
            return {"state": "idle", "detail": "", "thread": MAIN_THREAD}
        working.sort(key=lambda s: (s["thread"] != MAIN_THREAD, s["ts"]))
        return working[0]

    # ------------------------------------------------------------------ UI protocol
    def active_streams_view(self) -> dict[str, dict[str, Any]]:
        streams: dict[str, dict[str, Any]] = {}
        for thread, sid in list(self._stream_ids.items()):
            streams[thread] = {
                "id": sid,
                "text": self._stream_buf.get(thread, ""),
                "reasoning": self._stream_reasoning.get(thread, ""),
                "ended": False,
            }
        return streams

    def on_text_delta(self, text: str) -> None:
        if not text:
            return
        thread = self.thread()
        sid = self._stream_ids.get(thread)
        if sid is None:
            sid = new_id("a")
            self._stream_ids[thread] = sid
            self._stream_buf[thread] = ""
            self._stream_reasoning[thread] = ""
            self.bus.publish({"kind": "stream_start", "thread": thread, "id": sid, "ts": now_iso()})
        self._stream_buf[thread] = self._stream_buf.get(thread, "") + text
        self.bus.publish({"kind": "delta", "thread": thread, "id": sid, "text": text})

    def on_reasoning_delta(self, text: str) -> None:
        # live thinking, streamed to the bubble but never persisted on its own:
        # the final assistant event carries the full reasoning for the "Show thinking" fold
        if not text or not self.show_thinking:
            return
        thread = self.thread()
        sid = self._stream_ids.get(thread)
        if sid is None:
            sid = new_id("a")
            self._stream_ids[thread] = sid
            self._stream_buf[thread] = ""
            self._stream_reasoning[thread] = ""
            self.bus.publish({"kind": "stream_start", "thread": thread, "id": sid, "ts": now_iso()})
        self._stream_reasoning[thread] = self._stream_reasoning.get(thread, "") + text
        self.bus.publish({"kind": "reasoning_delta", "thread": thread, "id": sid, "text": text})

    def on_assistant_message(self, content: str | None, reasoning: str | None) -> None:
        thread = self.thread()
        sid = self._stream_ids.pop(thread, None)
        buf_text = self._stream_buf.pop(thread, None) or ""
        buf_reasoning = self._stream_reasoning.pop(thread, None) or ""
        # _step_text describes this response only; a terminate-only response after a
        # text reply must not look like "already said its piece".
        self._step_text.pop(thread, None)
        raw_text = content if content is not None else buf_text
        text = (raw_text or "").strip()
        raw_reasoning = reasoning if reasoning is not None else buf_reasoning
        reasoning_text = (raw_reasoning or "").strip()
        quiet = False
        if thread in self.background:
            quiet, text = split_quiet(text)
            text = text.strip()
        if sid is not None:
            end: dict[str, Any] = {"kind": "stream_end", "thread": thread, "id": sid}
            if not text and not (self.show_thinking and reasoning_text):
                # what streamed was not the reply after all — a prompt-mode tool call the
                # parser took out, or a quiet background pass — so the bubble goes now
                end["discard"] = True
            self.bus.publish(end)
        if not text and not (self.show_thinking and reasoning_text):
            return
        event: dict[str, Any] = {"id": sid or new_id("a"), "type": "assistant", "text": text}
        if quiet:
            event["quiet"] = True
        if self.show_thinking and reasoning_text:
            event["reasoning"] = reasoning_text
        if thread in self.model_once:
            event["model_used"] = self.model_once[thread]
        # the turn in the account's record this reply came from, so a thumb on the bubble
        # can name it (``turn_source`` "own" when the turn ran on the person's own key)
        turn_id, turn_source = llm_session.last_turn()
        if turn_id:
            event["turn"] = turn_id
            if turn_source == "own":
                event["turn_source"] = "own"
        self.last_assistant_text[thread] = text
        self._step_text[thread] = text
        self.last_assistant_event[thread] = self.emit(event)["id"]

    def on_tool_call(self, call: ToolCall, summary: str) -> None:
        thread = self.thread()
        if call.name in ("terminate", "ask_user"):
            # The final summary becomes the assistant bubble and questions get their own
            # card — a chip for either would only duplicate them.
            if call.name == "terminate" and self._step_text.get(thread):
                # The model already said its piece in the same response; the summary it
                # hands to terminate would be the same thing twice.
                self.reply_shown.add(thread)
            self.set_status("working", _TOOL_LABELS.get(call.name, "Working…"), thread)
            return
        self._step_text.pop(thread, None)
        args = call.arguments if isinstance(call.arguments, dict) else {}
        # the model's own words for the step ("Checking kitchen inventory") head the pill and the status
        # line; the technical summary stays underneath for the trace. When the model wrote
        # no `step` (some skip it), one is built from the tool label and its main argument.
        step = call.step or _step_words(call)
        ev = self.emit(
            {
                "type": "tool",
                "tool": call.name,
                "summary": summary,
                "title": step,
                "args": _preview_args(args),
                "status": "running",
            }
        )
        self._tool_events[call.id] = ev["id"]
        self._tool_events_thread[call.id] = thread
        if call.name not in _READ_ONLY_TOOLS:
            self._ws_before[thread] = self._scan_workspace()
        label = _TOOL_LABELS.get(call.name, f"Using {call.name}")
        self.set_status("working", step or (f"{label}: {summary}" if summary else label), thread)

    def on_tool_result(self, call: ToolCall, result: ToolResult) -> None:
        thread = self.thread()
        eid = self._tool_events.pop(call.id, None)
        self._tool_events_thread.pop(call.id, None)
        status = (
            "ok"
            if result.ok
            else ("blocked" if "Sentinel blocked" in (result.error or "") else "error")
        )
        preview = (result.output or result.error or "")[:600]
        if eid:
            self.patch(thread, eid, status=status, output=preview)
        if result.ok and call.name not in _READ_ONLY_TOOLS:
            self._announce_new_files(thread, call)
        # Let the Goals / Memory tabs refresh when the agent changed them.
        if result.ok and call.name == "goals":
            self.bus.publish({"kind": "goals"})
        elif result.ok and call.name in ("remember", "forget"):
            self.bus.publish({"kind": "memory"})
        if call.name != "terminate":
            self.set_status("working", "", thread)

    def _announce_new_files(self, thread: str, call: ToolCall) -> None:
        """Every file a tool created or changed in the workspace becomes an artifact card:
        a page from python_execute, a PDF from a shell command, a note from files."""
        before = self._ws_before.pop(thread, None)
        after = self._scan_workspace() if before is not None else None
        if before is not None and after is not None:
            for rel, mtime in after.items():
                if before.get(rel) != mtime:
                    self._announce_artifact(thread, rel, "write" if rel not in before else "update")
            return
        # no cheap diff available: fall back to what the files tool tells us
        if call.name == "files":
            args = call.arguments if isinstance(call.arguments, dict) else {}
            path = str(args.get("path") or "")
            if args.get("action") in ("write", "append") and path:
                if self.workspace is not None and Path(path).is_absolute():
                    try:
                        path = Path(path).resolve().relative_to(self.workspace.resolve()).as_posix()
                    except ValueError:
                        return
                self._announce_artifact(thread, path, str(args["action"]))

    def on_sentinel(self, decision: str, summary: str, reasons: list[str]) -> None:
        if decision == "deny":
            self.emit(
                {
                    "type": "notice",
                    "level": "warn",
                    "text": f"Sentinel blocked: {summary}"
                    + (f": {'; '.join(reasons)}" if reasons else ""),
                }
            )

    def info(self, message: str) -> None:
        self.emit({"type": "notice", "level": "info", "text": message})

    def warn(self, message: str) -> None:
        self.emit({"type": "notice", "level": "warn", "text": message})

    async def ask_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        thread = self.thread()
        approval_id = new_id("ap")
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[ApprovalDecision] = loop.create_future()
        # registered before the card goes out, so whoever reacts to the event (push badge)
        # already counts it
        self.pending_approvals[approval_id] = fut
        ev = self.emit(
            {
                "id": approval_id,
                "type": "approval",
                "tool": request.tool,
                "summary": request.summary,
                "risk": request.risk.value,
                "reasons": request.reasons,
                "warnings": request.warnings,
                "egress_target": request.egress_target,
                "purpose": request.purpose,
                "target": request.target,
                "grant_key": request.grant_key,
                "grant_options": list(request.grant_options),
                "args": _preview_args(request.args),
                "status": "pending",
            }
        )
        self.set_status("waiting", f"Needs your approval: {request.summary}", thread)
        try:
            decision = await asyncio.wait_for(fut, timeout=self.approval_timeout)
        except TimeoutError:
            decision = ApprovalDecision(approved=False, reason="no answer (timed out)")
            self.patch(thread, ev["id"], status="expired")
        finally:
            self.pending_approvals.pop(approval_id, None)
        self.set_status("working", "", thread)
        return decision

    def resolve_approval(
        self, approval_id: str, approved: bool, scope: str = "once", reason: str = ""
    ) -> bool:
        fut = self.pending_approvals.get(approval_id)
        if fut is None or fut.done():
            return False
        decision = ApprovalDecision(approved=approved, scope=scope, reason=reason)  # type: ignore[arg-type]
        for tl_thread, tl in self._all_timelines():
            if tl.get(approval_id) is not None:
                self.patch(
                    tl_thread,
                    approval_id,
                    status="approved" if approved else "denied",
                    scope=scope if approved else None,
                    decided_ts=now_iso(),
                )
                break
        fut.set_result(decision)
        return True

    async def ask_user(self, question: str) -> str:
        if not question or not str(question).strip():
            return ""
        thread = self.thread()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self.pending_questions[thread] = fut
        ev = self.emit({"type": "question", "text": question, "status": "pending"})
        self.set_status("waiting", "Waiting for your answer", thread)
        try:
            answer = await asyncio.wait_for(fut, timeout=self.approval_timeout)
        except TimeoutError:
            answer = ""
            self.patch(thread, ev["id"], status="expired")
        finally:
            self.pending_questions.pop(thread, None)
        if answer:
            self.patch(thread, ev["id"], status="answered", answer=answer)
        self.set_status("working", "", thread)
        return answer

    def answer_question(self, thread: str, text: str) -> bool:
        fut = self.pending_questions.get(thread)
        if fut is None or fut.done():
            return False
        fut.set_result(text)
        return True

    def has_pending_question(self, thread: str) -> bool:
        fut = self.pending_questions.get(thread)
        return fut is not None and not fut.done()

    # ------------------------------------------------------------------ internals
    _timelines_provider: Callable[[], list[tuple[str, Timeline]]] | None = None

    def _all_timelines(self) -> list[tuple[str, Timeline]]:
        if self._timelines_provider is None:
            return []
        try:
            return self._timelines_provider()
        except Exception as exc:  # noqa: BLE001  pragma: no cover
            logger.warning("timeline provider failed: {}", exc)
            return []


def _preview_args(args: dict[str, Any], limit: int = 400) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in args.items():
        s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        if isinstance(s, str) and len(s) > limit:
            s = s[:limit] + f"… [+{len(s) - limit} chars]"
        out[k] = s
    return out


__all__ = ["WebUI", "current_thread"]
