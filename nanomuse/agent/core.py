"""The agent loop: think (LLM) → act (tools through the Sentinel) → repeat."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from nanomuse import prompts
from nanomuse.agent.context import fit_context
from nanomuse.agent.intent import should_skip_tools
from nanomuse.calendar import CalendarFeeds
from nanomuse.config import Settings
from nanomuse.contacts import ContactBook
from nanomuse.goals import GoalStore
from nanomuse.instrumentation import InstrumentationRegistry
from nanomuse.llm import budget as task_budget
from nanomuse.llm import session as llm_session
from nanomuse.llm.base import BaseLLM
from nanomuse.llm.budget import estimate_tokens
from nanomuse.llm.openai_chat import PartialStreamError
from nanomuse.llm.vision import keep_newest_images
from nanomuse.logger import logger
from nanomuse.memory import MemoryItem, MemoryStore
from nanomuse.runtime import device
from nanomuse.schema import AgentState, Attachment, Message, Role, ToolResult, new_id
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.skills import SkillLibrary
from nanomuse.tools.base import ToolCollection
from nanomuse.ui import UI

# How much of one turn's tool result survives when arrivals alone would not fit the
# budget (request-only; the saved history keeps everything).

_ARRIVAL_SLICE = 1000

# The slack the frozen packing may run over the token budget before a refit. The
# estimate overshoots the wire size by about a twelfth at this scale (Nov 9: an
# estimate of 31.8k for a request of 28.4k on the wire), so holding the frozen prefix
# to the letter of the budget refits requests the wire never needed, and a refit moves
# every byte behind the new tool results, which throws away the prompt cache and buys
# a whole CPU prefill again (~95 seconds for 28k tokens).

_ARRIVAL_HEADROOM = 1200

# Phrases that mark an outright correction ("listen when I count sets", "I didn't have
# a grilled wrap", "from now on …"). A sentence with one of these becomes a memory in
# the person's own words, without waiting on the model to call `remember` (it forgets
# to, or logs a paraphrase). Kept short and literal on purpose: a near-twin is skipped
# by the store's similarity check, so re-saying a rule does not pile up rows.

_CORRECTION_CUES = (
    "listen when",
    "i told you",
    "i didn't have",
    "i did not have",
    "i never",
    "i always",
    "you forgot",
    "you keep",
    "you always",
    "you never",
    "from now on",
    "from next time",
    "stop assuming",
    "pay attention",
    "don't ever",
    "do not ever",
)


class InterruptedByUserInput(Exception):
    """Raised when an in-flight LLM generation is interrupted by a new user message."""


# A short message that means STOP: the turn must end now, not fold the message into the
# running turn and carry on (2026-11-11: "stop" typed mid-task was folded in as pile-on,
# and the agent kept driving the phone for ten more minutes).
_STOP_WORDS = re.compile(
    r"^\s*(please\s+)?(emergency\s+)?(stop|stopp|quit|halt|abort|cancel|freeze|cease"
    r"|pause|停止|停|停下|停下来|住手|别动|先停|马上停|暂停|紧急停止)\b",
    re.IGNORECASE,
)


class Incoming:
    """A user message on its way to the agent: the text, what was attached to it, the

    language of the client's screens (a BCP-47 tag, "" when the client did not say), and,

    for *Use nanoMuse Cloud this time*, the model client this one turn runs on (``llm``)

    and its id (``model``); None and "" for a turn on the configured model. ``run`` is what

    set the turn going, in the relay's word (``person``, ``schedule``, ``feed``, ``goal``,

    ``hub``; docs/cloud.md "Provenance")."""

    __slots__ = ("delivery", "files", "language", "llm", "model", "run", "text")

    def __init__(
        self,
        text: str,
        files: list[Attachment] | None = None,
        language: str = "",
        llm: BaseLLM | None = None,
        model: str = "",
        delivery: str = "",
        run: str = "person",
    ):

        self.text = text

        self.files = files or []

        self.language = language

        self.llm = llm

        self.model = model

        self.delivery = delivery

        self.run = run


class MuseAgent:
    def __init__(
        self,
        settings: Settings,
        llm: BaseLLM,
        tools: ToolCollection,
        sentinel: Sentinel,
        ui: UI,
        audit: AuditLog,
        memory: MemoryStore | None = None,
        goals: GoalStore | None = None,
        calendar: CalendarFeeds | None = None,
        contacts: ContactBook | None = None,
        skills: SkillLibrary | None = None,
        session_file: Path | None = None,
        conversation_id: str | None = None,
    ):

        self.settings = settings

        self.llm = llm

        self.tools = tools

        self.sentinel = sentinel

        self.ui = ui

        self.audit = audit

        self.memory = memory

        self.goals = goals

        self.calendar = calendar

        self.contacts = contacts

        self.skills = skills

        self.session_file = session_file

        # The conversation (thread) this agent is; approvals "for this conversation" are

        # bound to it and last as long as it does. Without one, each run stands alone.

        self.conversation_id = conversation_id

        # The id model calls carry for a vendor that wants one per conversation (OpenCode Go):

        # the thread's, else one of this agent's own for as long as it lives.

        self.session_id = conversation_id or llm_session.new_fallback()

        # the id the record keeps the latest model call under and where it was kept

        # (``cloud`` or ``own``; ""/"" before the first), for the notes about the turn

        self.last_call: tuple[str, str] = ("", "")

        self.messages: list[Message] = []

        self.state = AgentState.IDLE

        self.turns = 0

        # Optional queue of user messages that arrive *while* a run is in progress (the app

        # lets you interrupt or pile on requests). They are folded into the conversation

        # before the next model call instead of waiting for the current run to finish.

        self.inbox: asyncio.Queue[str | Incoming] | None = None

        self._told_no_vision = False

        self.last_budget: dict[str, Any] = {}

        self._task_start = 0

        # The request's packing starts with the turn: a frozen prefix per turn so the
        # model's prompt cache survives every call within the same task (see
        # `_request_context`).

        self._turn_pack: list[Message] | None = None

        self._turn_base = 0

        # The language of the screens the person reads this conversation on, from the latest

        # message that said (a BCP-47 tag); the language rule names it ahead of the script

        # of one message. "" means no client has said, and the script decides.

        self.ui_language = ""

        # Words for this conversation alone, read when the system prompt is built (the first

        # conversation's phase, nanomuse/server/firstrun.py); None or "" adds nothing.

        self.prompt_addendum: Callable[[], str] | None = None

    @staticmethod
    def _foldable(incoming: Incoming) -> bool:
        """Whether an inbox item can fold into the running turn. A one-turn client
        (``llm``), a one-turn model pick (``model``) and a scheduled delivery (``delivery``)
        run as turns of their own and must wait for the worker: the drain refuses them, so
        the stream checks must not abort for them either, or the run restarts on the first
        token of every attempt forever while the head never moves (the Nov 11 freeze)."""
        return incoming.llm is None and not incoming.model and not incoming.delivery

    def _stop_requested(self) -> bool:
        """Whether the last folded user message is an explicit stop: the turn ends now,
        whatever it was doing. Only a short message that IS a stop word counts, so
        "don't stop" or a long sentence never trips it."""
        if not self.messages:
            return False
        last = self.messages[-1]
        if last.role != Role.USER:
            return False
        text = " ".join((last.content or "").strip().split())
        if not text or len(text) > 80 or re.search(r"don'?t\s+stop|dont\s+stop|不要停|别停|不停", text, re.IGNORECASE):
            return False
        return bool(_STOP_WORDS.match(text))

    def _drain_inbox(self) -> int:

        if self.inbox is None:
            return 0

        count = 0

        while not self.inbox.empty():
            # a look at the head before taking it: what must run as a turn of its own stays

            item = self.inbox._queue[0]  # type: ignore[attr-defined]

            incoming = item if isinstance(item, Incoming) else Incoming(item)

            if not self._foldable(incoming):
                # A one-turn client belongs to the queued turn, not to the run that happens

                # to be in progress, and scheduled work (a reminder, a routine, a trigger)

                # is a background run with a purpose of its own that acknowledges its

                # source when it ends. Both wait for the worker to run them alone.

                break

            try:
                self.inbox.get_nowait()

            except asyncio.QueueEmpty:  # pragma: no cover
                break

            if incoming.language:
                self.ui_language = incoming.language

            self.messages.append(self.user_message(incoming.text, incoming.files))

            if incoming.run == "person":
                self._capture_corrections(incoming.text)

            self.audit.record("user_message", content=incoming.text, interjected=True)

            count += 1

        return count

    def _capture_corrections(self, text: str) -> None:
        """A correction the person states outright ("listen when I count sets", "I
        didn't have a grilled wrap") becomes a memory in his own words, verbatim.

        Only his own typed turns; the model calling `remember` remains the usual path
        and this is the net under it (it forgets to, or paraphrases what he said).
        Exact and near repeats are skipped, so re-stating a rule adds no second row."""

        if self.memory is None or not text or not self.settings.memory.enabled:
            return
        for raw in re.split(r"(?<=[.!?…])\s+|\n+", text):
            sentence = " ".join(raw.split())
            if len(sentence) < 12 or len(sentence) > 400:
                continue
            lowered = sentence.lower()
            if not any(cue in lowered for cue in _CORRECTION_CUES):
                continue
            try:
                if self.memory.similar(sentence, threshold=0.8, limit=1):
                    continue
                item = self.memory.add(sentence, category="preference", source="user")
                logger.info("correction saved as memory {} from his own words", item.id)
            except Exception as exc:  # noqa: BLE001 - capture must never break a turn
                logger.warning("correction capture failed: %s", exc)

    def user_message(self, text: str, files: list[Attachment] | None = None) -> Message:
        """The user's message as the model gets it: the text, a line per attached file

        (path and what it is), and the pictures as images when the model takes them."""

        if not files:
            return Message(
                role=Role.USER,
                content=text,
                meta={"user_request": True, "request_id": new_id("message")},
            )

        lines = [f"- {a.path} ({a.describe()})" for a in files]

        note = "[Attached files]\n" + "\n".join(lines)

        if any(a.kind != "image" for a in files):
            note += "\nRead them with `files` action=read."

        content = f"{text}\n\n{note}" if text.strip() else note

        workspace = Path(self.settings.agent.workspace).expanduser()

        images = [str(workspace / a.path) for a in files if a.kind == "image"]

        return Message(
            role=Role.USER,
            content=content,
            images=images or None,
            meta={"user_request": True, "request_id": new_id("message")},
        )

    # ------------------------------------------------------------------ prompt

    def sandbox_note(self) -> str:
        """How commands run, from the shell tool's sandbox (the tools own it)."""

        shell = self.tools.get("shell")

        box = getattr(shell, "sandbox", None)

        if box is not None and box.active:
            note = box.describe().replace("Commands run", "Commands (shell, python_execute) run")

            if box.blocks_network:
                note += (
                    " Commands that need the network say so by what they run (curl, pip, git, a "
                    "URL, a script that imports requests …); otherwise pass network=true."
                )

            return note

        return "Commands (shell, python_execute) run in the workspace with a scrubbed environment."

    def skills_section(self) -> str:
        """The index of skills — names and descriptions; the tool has the instructions."""

        lib = self.skills

        if lib is None or "skills" not in self.tools:
            return ""

        index = lib.index()

        if not index:
            return ""

        return prompts.SKILLS_SECTION.format(items=index)

    def phone_section(self) -> str:
        """The phone, when GUI operation is on: connected or not, and what is on it."""

        task = self.tools.get("phone_task")

        link = getattr(task, "link", None)

        if task is None or link is None:
            return ""

        device = link.device

        if device is None:
            status = (
                "- GUI operation is on, but no phone is connected right now: the `phone_*` tools "
                "will fail until the user opens the nanoMuse app on the phone (or the MobileGym "
                "module). Say so if a step needs the phone."
            )

        else:
            apps = device.app_list()

            status = f"- The user's phone is connected: {device.name} ({device.platform})."

            if apps:
                status += f" Apps on it: {apps}."

        return prompts.PHONE_SECTION.format(status=status)

    def device_section(self) -> str:
        """The phone this runs on, when its capabilities are there as `device__*` tools."""

        from nanomuse.runtime import DEVICE_PREFIX

        names = sorted(
            t.name[len(DEVICE_PREFIX) :] for t in self.tools if t.name.startswith(DEVICE_PREFIX)
        )

        if not names:
            return ""

        return prompts.DEVICE_SECTION.format(tools=", ".join(names))

    def computer_section(self) -> str:
        """This computer's screen, when the hands are on: available or not, and what is in front.

        On a computer with the hands off, one line saying where the switch is — so a request

        for a desktop application gets the way to it, not a shrug."""

        task = self.tools.get("computer_task")

        link = getattr(task, "link", None)

        if task is None or link is None:
            if device() is None and not self.settings.hands.enabled:
                return prompts.COMPUTER_OFF_SECTION

            return ""

        status = link.status() if hasattr(link, "status") else {}

        if not status.get("available", True):
            reason = status.get("reason") or "no backend"

            status_line = (
                "- The hands are turned on, but they cannot drive this computer right now "
                f"({reason}): the `computer_*` tools will fail. Say so if a step needs the screen."
            )

        else:
            last = link.last_screen

            status_line = (
                f"- The hands can drive this computer ({status.get('backend') or 'hands'} on "
                f"{link.device.platform}, screen {link.device.width}×{link.device.height})."
            )

            if last is not None and last.title:
                status_line += f" In front last time: {last.title}."

        return prompts.COMPUTER_SECTION.format(status=status_line)

    def devices_section(self) -> str:
        """The user's other devices, when this computer is on the hub (the `devices` tool)."""

        tool = self.tools.get("devices")

        hub = getattr(tool, "hub", None)

        if tool is None or hub is None:
            return ""

        others = hub.others()

        if not getattr(hub, "client", None) or not hub.client.connected.is_set():
            status = (
                "- This computer is not connected to the nanoMuse hub right now, so the other "
                "devices cannot be reached; say so if a step needs one."
            )

        elif not others:
            status = (
                f"- This computer is on the hub as “{hub.device_name}”; no other device of the "
                "user's has joined yet."
            )

        else:
            listed = ", ".join(
                f"{d.get('name')} ({d.get('kind') or 'device'}, "
                f"{'online' if d.get('online') else 'offline'})"
                for d in others
            )

            status = f"- This computer is “{hub.device_name}”. Other devices: {listed}."

        return prompts.DEVICES_SECTION.format(status=status)

    def contacts_note(self) -> str:
        """One line on the address book, when there is one (the tool does the looking up)."""

        book = self.contacts

        if book is None or "contacts" not in self.tools or not book.configured:
            return ""

        n = len(book)

        return (
            f"- Address book: {n} {'person' if n == 1 else 'people'}; look someone up with "
            "`contacts` before writing to them; never guess an address\n"
        )

    async def recall_for(self, user_input: str) -> list[MemoryItem] | None:
        """The memories to put in the system prompt for this message — by meaning too,

        when an embedding endpoint is set up (that is the awaited part)."""

        if self.memory is None or not self.settings.memory.enabled:
            return None

        try:
            return await self.memory.relevant_async(
                user_input, limit=self.settings.memory.max_inject
            )

        except Exception as exc:  # noqa: BLE001 — recall must never stop a turn
            logger.warning("recall failed ({}); using the keyword ranking", exc)

            return self.memory.relevant(user_input, limit=self.settings.memory.max_inject)

    def language_rule(self, user_input: str) -> str:
        """The `## Language` paragraph of the system prompt. A reply language set under

        Settings wins; else the language of the client's screens when a client has said

        (``ui_language``); else the script of the message."""

        fixed = self.settings.agent.language

        if fixed not in ("", "auto"):
            return prompts.LANGUAGE_FIXED.format(language=fixed)

        detected = prompts.detect_language(user_input)

        ui = prompts.language_name(self.ui_language)

        if ui:
            return prompts.LANGUAGE_UI.format(language=ui, detected=detected)

        return prompts.LANGUAGE_AUTO.format(detected=detected)

    def build_system_prompt(self, user_input: str = "", *args: Any, **kwargs: Any) -> str:
        """Invariant system prompt: static identity, persona, rules and capabilities.
        Preserves 100% prefix KV cache hit across all turns."""
        a = self.settings.agent
        language_rule = self.language_rule(user_input)
        profile = (
            prompts.USER_PROFILE_SECTION.format(profile=a.user_profile.strip())
            if a.user_profile.strip()
            else ""
        )
        extra = (
            f"\n## Additional instructions\n{a.instructions.strip()}\n"
            if a.instructions.strip()
            else ""
        )
        addendum = (self.prompt_addendum() if self.prompt_addendum is not None else "").strip()
        if addendum:
            extra += f"\n## This conversation\n{addendum}\n"

        return prompts.SYSTEM_PROMPT.format(
            name=a.name,
            language_rule=language_rule,
            workspace=str(a.workspace.resolve()),
            sentinel_mode=self.settings.sentinel.mode,
            sandbox=self.sandbox_note(),
            contacts=self.contacts_note(),
            tool_names=", ".join(t.name for t in self.tools),
            user_profile=profile,
            extra=extra,
            house_rules=self.house_rules(),
        )

    def house_rules(self) -> str:
        """A compact, imperative rule block that always rides at the end of the system
        prompt: the fixed bans plus the user's own corrections, newest first. User
        preference memories are treated as standing law rather than context to weigh."""
        fixed = [
            'Never open a reply with "Got it" or any canned opener.',
            'Never end a reply with "Is there anything else I can help you with?", "How\'s your day going?", "Anything fun on the horizon?" or any canned check-in or sign-off.',
            "Never tell the user to chill, relax, chillax, or calm down.",
            "Use 12-hour AM/PM time by default unless the user asks for 24-hour time.",
            "Reply once per turn, in a single message. Never split one thought into multiple messages.",
            "When corrected, own the mistake directly and never repeat it.",
            "When the user corrects you mid-turn, save it with `remember` in the same turn, in their own words.",
            "Never ask the user for information your tools already hold: check contacts, tracker, files and memory first, then ask only what is truly missing.",
            "Once a task needs tools, run its whole plan to the end. Do not stop after one step and hand the rest back as a talk-through.",
            "Say a log or result once, briefly. Never recite past logs or repeat confirmations.",
            "When a fact is checkable (nutrition, prices, events, what someone said), check it with a tool or the internet before answering. Never guess.",
            "About pictures: name only what a picture actually shows. If it is unclear or you could not look, say so plainly. Never guess what it contains.",
            "`step` text is always English. Never write Chinese unless asked.",
        ]
        if self.settings.agent.allow_self_improve or self.settings.sentinel.mode == "auto":
            fixed.append(
                "Self-improve freely: when a clear capability gap appears, add the connector, "
                "API, or tool yourself (Zapier MCP, a custom REST API via api_connector or "
                "external_connectors, an MCP server) and report what you added and how it works. "
                "If a permission, API key, OAuth login, or device setup is required from the user, "
                "call `request_setup` to open the interactive setup popup instead of stopping."
            )
        else:
            fixed.append(
                "Never change connection, permission, model or Sentinel settings on your own. "
                "When a task needs an integration, API key, permission, or device setup that is "
                "not yet connected, call `request_setup` to prompt the user with an interactive "
                "setup popup instead of stopping and saying you cannot do it."
            )
        if self.settings.sentinel.mode == "auto":
            fixed.append(
                "Hands-off mode (sentinel_mode=auto) is active: execute requested actions, tool "
                "calls, file builds, and connector setups directly end-to-end without stopping to "
                "ask 'May I do this?' or asking for permission first."
            )
        lines = [f"- {r}" for r in fixed]
        if self.memory is not None and self.settings.memory.enabled:
            try:
                prefs = [
                    m
                    for m in self.memory.all(limit=400)
                    if m.category == "preference" and m.source == "user"
                ]
                prefs.sort(key=lambda m: m.created_at or "", reverse=True)
                for m in prefs[:8]:
                    text = " ".join(m.content.split())
                    if len(text) > 220:
                        text = text[:217].rstrip() + "..."
                    if text:
                        lines.append(f"- (your rule) {text}")
            except Exception as exc:  # noqa: BLE001 - house rules must never break a turn
                logger.warning("house rules: preference memories unavailable: %s", exc)
        return "\n".join(lines) + "\n"

    def model_note(self) -> str:
        """One live-context line naming the model this turn runs on, so the model never
        claims to be another one (the worker swaps ``self.llm`` for a one-turn switch).
        Empty when the LLM carries no settings (MockLLM in tests)."""
        settings = getattr(self.llm, "settings", None)
        model = str(getattr(settings, "model", "") or "")
        if not model:
            return ""
        base = str(getattr(settings, "base_url", "") or "")
        where = "served locally"
        if base:
            try:
                netloc = urlparse(base).netloc.split("@")[-1]
                if netloc and netloc.split(":")[0] not in ("localhost", "127.0.0.1"):
                    where = f"served by {netloc.split(':')[0]}"
            except ValueError:  # pragma: no cover - a malformed base_url just stays local
                pass
        return prompts.MODEL_NOTE.format(model=model, where=where)

    def build_live_context(self, user_input: str, memories_: list[MemoryItem] | None = None) -> str:
        """Dynamic turn-specific context injected in the turn envelope (not system prefix)."""
        now_str = datetime.now().astimezone().strftime("%A, %B %d, %Y at %I:%M %p (%Z)")
        memories = ""
        if self.memory is not None and self.settings.memory.enabled:
            items = (
                memories_
                if memories_ is not None
                else self.memory.relevant(user_input, limit=self.settings.memory.max_inject)
            )
            if items:
                rendered: list[str] = []
                budget = 3200  # chars ≈ 800 tokens for all injected memories combined
                for m in items:
                    text = " ".join(m.content.split())
                    if len(text) > 300:
                        text = text[:297].rstrip() + "..."
                    line = f"- [{m.id}] ({m.category}) {text}"
                    if rendered and sum(len(r) for r in rendered) + len(line) > budget:
                        break
                    rendered.append(line)
                memories = prompts.MEMORY_SECTION.format(items="\n".join(rendered))
                if self.turns <= 1:
                    memories += prompts.MEMORY_OPENING_NOTE

        goals = ""
        if self.goals is not None:
            active = self.goals.list("active")[:5]
            if active:
                lines = []
                for g in active:
                    nxt = g.next_step
                    bits = [f"progress {g.progress}"]
                    if g.category:
                        bits.append(g.category)
                    if g.due:
                        bits.append(f"due {g.due}" + (", overdue" if g.overdue else ""))
                    if nxt:
                        bits.append(f"next: {nxt.idx}. {nxt.title}")
                    if g.proposal:
                        bits.append("a plan change is awaiting the user's answer")
                    lines.append(f"- {g.id}: {g.title} ({', '.join(bits)})")
                goals = prompts.GOALS_SECTION.format(items="\n".join(lines))

        calendar = ""
        if self.calendar is not None and self.calendar.configured:
            today = datetime.now(self.calendar.tz).date()
            events = self.calendar.agenda(today, 2)
            calendar = prompts.CALENDAR_SECTION.format(
                items=self.calendar.render(events, today)
                if events
                else "(nothing on the calendar today or tomorrow)"
            )

        goals_block = (
            goals
            + calendar
            + self.device_section()
            + self.phone_section()
            + self.computer_section()
            + self.devices_section()
            + self.skills_section()
        )

        return prompts.LIVE_CONTEXT_TEMPLATE.format(
            now=now_str,
            model=self.model_note(),
            memories=memories,
            goals=goals_block,
        ).strip()

    # ------------------------------------------------------------------ context window

    def context_messages(self) -> list[Message]:
        """The window the model sees: the newest ``max_context_messages`` (never starting

        inside a tool exchange), with pictures only on the newest ``max_context_images``

        image-bearing messages — a hands session would otherwise send every screenshot of

        the run on every step (C9). Copies where a picture was dropped; the history itself

        keeps them."""

        limit = self.settings.agent.max_context_messages

        msgs = self.messages

        if len(msgs) <= limit:
            window = list(msgs)

        else:
            cutoff = len(msgs) - limit

            # Never start in the middle of a tool exchange: advance to the next user message.

            while cutoff < len(msgs) and msgs[cutoff].role != Role.USER:
                cutoff += 1

            if cutoff >= len(msgs):
                # A single long turn can fill the window. Keep the leading tool

                # exchange whole, even if that takes a few messages over the limit.

                cutoff = slice(-limit, None).indices(len(msgs))[0]

                while 0 < cutoff < len(msgs) and msgs[cutoff].role == Role.TOOL:
                    cutoff -= 1

            window = list(msgs[cutoff:])

        return keep_newest_images(window, self.settings.agent.max_context_images)

    # ------------------------------------------------------------------ stuck detection

    def _is_stuck(self) -> bool:
        assistants = [m for m in self.messages[self._task_start :] if m.role == Role.ASSISTANT]
        if len(assistants) < 2:
            return False

        def sig(m: Message) -> str:
            content = (m.content or "").strip()
            calls = [(tc.name, tc.arguments) for tc in (m.tool_calls or [])]
            return json.dumps([content, calls], ensure_ascii=False, sort_keys=True)

        # the same words AND the same calls twice in a row: the model is talking to itself.
        # Two wordless turns that repeat the same call are judged by their answers instead
        # (the check below): a changing answer is an additive log's running total, which is
        # progress and not a loop. Killing those turns on the bare signature is what ended
        # the Nov 9 water logs two steps short (identical tool-only turns, totals 32, 64).

        if (assistants[-1].content or "").strip() and sig(assistants[-1]) == sig(assistants[-2]):
            return True

        last_calls = [(tc.name, tc.arguments) for tc in (assistants[-1].tool_calls or [])]
        if last_calls:
            # A repeat of an earlier call is only a loop when the tool's earlier answers
            # said nothing new: the words came back identical every time or the call never
            # reported a result. When the answers keep moving (an additive log's running
            # total: "Logged 32 oz … total: 32/192", then "… 64/192"), the same call again
            # is the next item of a plan and driving it away kills the plan (the Nov 9
            # water logs died exactly here).
            results = {
                m.tool_call_id: (m.content or "").strip()
                for m in self.messages[self._task_start :]
                if m.role == Role.TOOL and m.tool_call_id
            }
            prior: dict[tuple[str, str], list[str]] = {}
            for prev_m in assistants[:-1]:
                for tc in prev_m.tool_calls or []:
                    key = (tc.name, json.dumps(tc.arguments, ensure_ascii=False, sort_keys=True))
                    prior.setdefault(key, []).append(results.get(tc.id, ""))
            for name, args in last_calls:
                texts = prior.get((name, json.dumps(args, ensure_ascii=False, sort_keys=True)))
                if texts is None:
                    continue
                spoken = [t for t in texts if t]
                if not spoken:
                    return True  # the earlier runs never reported anything: repeating gains none
                if len(spoken) >= 2 and not any(t != spoken[0] for t in spoken):
                    # the tool answered with the same words at least twice while the
                    # model asks a third time: nothing movable left in those answers
                    return True
                # one answer so far, or answers that keep changing (a running total is
                # progress): the repeat is the plan's next item, let it run
        return False

    def _tool_loop_detected(self) -> bool:
        """Four or more calls to the same tool in this turn whose results were identical:
        the model is asking the same question and getting the same answer (the Nov 8
        calendar loop called it seven times that way). Results that differ are progress,
        not a loop, so a long turn that logs many items stays untouched."""
        same_results: dict[str, int] = {}
        last_result: dict[str, str] = {}
        msgs = self.messages[self._task_start :]
        assistant_calls: dict[str, str] = {}  # tool_call_id -> tool name
        for m in msgs:
            if m.role == Role.ASSISTANT:
                for tc in m.tool_calls or []:
                    assistant_calls[tc.id] = tc.name
            elif m.role == Role.TOOL and m.tool_call_id:
                name = m.name or assistant_calls.get(m.tool_call_id) or "?"
                content = (m.content or "").strip()
                if last_result.get(name) == content:
                    same_results[name] = same_results.get(name, 0) + 1
                else:
                    last_result[name] = content
                    same_results[name] = 0
        return any(c >= 4 for c in same_results.values())

    # ------------------------------------------------------------------ main loop

    async def run(
        self,
        user_input: str,
        purpose: str | None = None,
        files: list[Attachment] | None = None,
        language: str = "",
        run: str = "person",
    ) -> str:

        if self.state == AgentState.RUNNING:
            raise RuntimeError("agent is already running")

        token = task_budget.begin(self.settings.agent.budget, inherit=False)

        budget = task_budget.current()

        assert budget is not None

        self._task_start = len(self.messages)

        # a new turn: the packing starts over from the fresh history

        self._turn_pack = None

        self._turn_base = self._task_start

        deadline = asyncio.timeout(self.settings.agent.budget.max_seconds or None)

        try:
            async with deadline:
                return await self._run(
                    user_input, purpose=purpose, files=files, language=language, run=run
                )

        except (task_budget.BudgetExceeded, TimeoutError) as exc:
            if isinstance(exc, TimeoutError) and not deadline.expired():
                raise

            reason = exc.reason if isinstance(exc, task_budget.BudgetExceeded) else "time"

            budget.stopped = reason

            reply_language = language or self.ui_language or self.settings.agent.language

            if reply_language in ("", "auto"):
                reply_language = "zh" if prompts.detect_language(user_input) == "Chinese" else "en"

            final = task_budget.BudgetExceeded(reason).message(reply_language)

            self._repair_dangling_tool_calls(reason=final)

            self.messages.append(Message.assistant(final))

            self.ui.on_assistant_message(final, None)

            self.state = AgentState.FINISHED

            self._save_session()

            return final

        finally:
            try:
                self.last_budget = budget.snapshot()

                # a line in the audit log when a budget is in force or was reached; a task

                # with no limits set leaves the log as it was

                if budget.limited or budget.stopped:
                    self.audit.record("task_budget", **self.last_budget)

            finally:
                task_budget.end(token)

    def _shrunk(self, message: Message) -> Message:
        """A request-only copy of a turn arrival with its tool result cut short.

        Only used when the frozen prefix plus the new arrivals would not fit: the
        copies keep the saved history full while the newest tool results give up
        their tail, so the request fits without moving anything behind it."""

        if message.role != Role.TOOL or not message.content:
            return message
        text = message.content.strip()
        if len(text) <= _ARRIVAL_SLICE:
            return message
        return message.model_copy(
            update={"content": text[:_ARRIVAL_SLICE].rstrip() + " … (cut to protect the context)"}
        )

    def _request_context(
        self, system_prompt: str, tools: list[dict[str, Any]] | None
    ) -> list[Message]:
        """The request copies, packed against the token budget once per turn.

        The packing is frozen: the fit runs once at the turn's first ask, and every
        later call in the same turn reuses that exact prefix and only appends what
        came in since. Ollama keeps a prompt cache keyed on the prompt's opening
        tokens, so a history rebuilt for every call costs a full CPU prefill of 25k
        tokens again each time (whole minutes per step); a frozen prefix keeps the
        cache and the app snappy."""

        limit = self.settings.agent.max_context_tokens

        if not limit:
            # no budget configured: send the history as it is, nothing ever fits or moves

            return [Message.system(system_prompt), *self.context_messages()]

        if self._turn_pack is not None:
            arrivals = self.messages[self._turn_base :]

            head: list[Message]

            if self._turn_pack[0].content == system_prompt:
                head = [self._turn_pack[0]]

            else:
                # an interjection rebuilt the system prompt: the header changes once,

                # the history underneath keeps its shape (and the cache behind it)

                head = [Message.system(system_prompt)]

            pack = [*head, *self._turn_pack[1:], *arrivals]

            if estimate_tokens(pack, tools) <= limit + _ARRIVAL_HEADROOM:
                return pack

            # the turn's own tool results are what grew it: shrink those in place,

            # the history behind never moves inside a turn so the cache survives

            pack = [*head, *self._turn_pack[1:], *(self._shrunk(m) for m in arrivals)]

            if estimate_tokens(pack, tools) <= limit + _ARRIVAL_HEADROOM:
                return pack

            # still over: one honest refit (a cache miss, but rare)

        window = self.context_messages()

        protected = [m for m in self.messages[self._task_start :] if m.meta.get("user_request")]

        if self.settings.agent.max_context_tokens:
            present = {m.meta.get("request_id") for m in window}

            window = [m for m in protected if m.meta.get("request_id") not in present] + window

        packed = fit_context(
            [Message.system(system_prompt), *window],
            tools,
            self.settings.agent.max_context_tokens,
            protected,
        )

        self._turn_pack = packed

        self._turn_base = len(self.messages)

        return packed

    async def _run(
        self,
        user_input: str,
        purpose: str | None = None,
        files: list[Attachment] | None = None,
        language: str = "",
        run: str = "person",
    ) -> str:
        """One task: a user message (or a background prompt) worked to completion.



        ``purpose`` is what approval cards show as the reason for an action; it defaults

        to the message itself. ``files`` are the attachments that came with the message.

        ``language`` is the BCP-47 tag of the client's screens when the client sent one;

        it is remembered for the conversation and names the reply language ahead of the

        script of the message. ``run`` is what set the turn going (``person`` by default;

        ``schedule``, ``feed``, ``goal``, ``hub`` for the runs nobody typed), told to the

        relay with every call of the turn. Task-scoped approvals end when this call returns.

        """

        if self.state == AgentState.RUNNING:
            raise RuntimeError("agent is already running")

        self.state = AgentState.RUNNING

        self.turns += 1

        # his typed words are the chance to net corrections before anything rewrites them

        if run == "person":
            self._capture_corrections(user_input)

        if language:
            self.ui_language = language

        if self.skills is not None and "skills" in self.tools:
            # "/weekly-review …" — the skill's instructions ride along with the message

            user_input = self.skills.expand(user_input)

        trace = InstrumentationRegistry.get().start_trace(user_input, self.conversation_id)
        recalled = await self.recall_for(user_input)
        live_ctx = self.build_live_context(user_input, recalled)
        envelope_text = f"{user_input}\n\n[Live Context]\n{live_ctx}" if live_ctx else user_input
        self.messages.append(self.user_message(envelope_text, files))
        attached = {"files": [a.path for a in files]} if files else {}
        self.audit.record("user_message", content=user_input, trace_id=trace.trace_id, **attached)

        system_prompt = self.build_system_prompt(user_input)
        last_sentinel_mode = self.settings.sentinel.mode

        # Always send the full tool set. The old fast path (tools=None for casual chat)
        # flipped the rendered prompt prefix between turn types, which broke Ollama's
        # KV prefix cache: qwen3.5 is a hybrid-attention model whose cache is
        # checkpoint-based, so a prefix divergence forces a recompute from an earlier
        # checkpoint — measured: a casual turn after a tool turn reprocessed 13,900
        # tokens (~24s prefill) instead of hitting a ~12.7k warm prefix (~2s).
        # A stable prefix makes casual turns nearly free; the persona rules already
        # keep the model from calling tools on banter.
        if should_skip_tools(user_input):
            logger.info(
                "conversational or stop intent detected; keeping tool schemas for prefix cache stability"
            )
        tool_params = self.tools.to_params()

        final: str | None = None

        step = 0

        # auto-continue batches used this turn (reset every run)
        self._continues = 0

        # stuck nudges given this turn (reset every run)
        self._stuck_nudges = 0

        empty_replies = 0

        task_token = self.sentinel.begin_task(
            purpose or user_input, conversation=self.conversation_id
        )

        session_token = llm_session.enter(self.session_id)

        turn_token = llm_session.begin_turn(run, self.turns)

        try:
            while True:
                if step >= self.settings.agent.max_steps:
                    # Step budget reached. The default is to stop and wrap up; when
                    # auto-continue is on, a fresh batch of steps starts without a
                    # summary, so a long build does not stop mid-task just because a
                    # number was hit. Only when the safety bound runs out does the
                    # wrap-up summary step in.
                    continues = self._continues

                    if (
                        self.settings.agent.auto_continue
                        and continues < self.settings.agent.max_continues
                    ):
                        self._continues = continues + 1

                        logger.info(
                            "step cap reached; continuing automatically (batch {})", continues + 1
                        )

                        step = 0

                    else:
                        self._continues = 0
                        self.messages.append(Message.user(prompts.MAX_STEPS_PROMPT))

                        context = self._request_context(system_prompt, None)

                        response = await task_budget.ask(
                            self.llm,
                            context,
                            tools=None,
                            on_delta=lambda c: (
                                trace.record_first_token(),
                                self.ui.on_text_delta(c),
                            ),
                            on_reasoning_delta=lambda c: (
                                trace.record_first_token(),
                                self.ui.on_reasoning_delta(c),
                            ),
                        )

                        self.messages.append(response.to_message())

                        self.ui.on_assistant_message(response.content, response.reasoning)

                        final = response.content or "(step limit reached)"

                        break

                step += 1

                if self.settings.sentinel.mode != last_sentinel_mode:
                    last_sentinel_mode = self.settings.sentinel.mode
                    system_prompt = self.build_system_prompt(user_input)

                if self._drain_inbox():
                    logger.debug("folded queued user message(s) into the running turn")

                    # the language rule follows the latest message; the recall list does
                    # not. A recall here loaded the embeddings model mid-turn, which
                    # evicted the chat model and its slot cache: every later step repaid
                    # a full prefill for a memory list the system prompt does not take.

                    latest = self.messages[-1].content or ""

                    system_prompt = self.build_system_prompt(latest)

                    if self._stop_requested():
                        # an explicit stop mid-task ends the turn here, before another
                        # step runs; the rest of the inbox waits for the next turn
                        self.ui.on_assistant_message("Stopped. What next?")
                        self.messages.append(Message.assistant("Stopped. What next?"))
                        final = "Stopped."
                        break

                context = self._request_context(system_prompt, tool_params)

                streamed_tokens: list[str] = []

                def _check_and_delta(c: str) -> None:
                    if self.inbox is not None and not self.inbox.empty():
                        try:
                            next_item = self.inbox._queue[0]
                            inc = (
                                next_item
                                if isinstance(next_item, Incoming)
                                else Incoming(next_item)
                            )
                            if getattr(inc, "run", "") == "person" and self._foldable(inc):
                                raise InterruptedByUserInput()
                        except (IndexError, AttributeError):
                            pass
                    streamed_tokens.append(c)  # noqa: B023 - the closure reads this iteration only
                    trace.record_first_token()
                    self.ui.on_text_delta(c)

                def _check_and_reasoning_delta(c: str) -> None:
                    if self.inbox is not None and not self.inbox.empty():
                        try:
                            next_item = self.inbox._queue[0]
                            inc = (
                                next_item
                                if isinstance(next_item, Incoming)
                                else Incoming(next_item)
                            )
                            if getattr(inc, "run", "") == "person" and self._foldable(inc):
                                raise InterruptedByUserInput()
                        except (IndexError, AttributeError):
                            pass
                    trace.record_first_token()
                    self.ui.on_reasoning_delta(c)

                try:
                    response = await task_budget.ask(
                        self.llm,
                        context,
                        tools=tool_params,
                        on_delta=_check_and_delta,
                        on_reasoning_delta=_check_and_reasoning_delta,
                    )
                except (InterruptedByUserInput, PartialStreamError) as exc:
                    if isinstance(exc, PartialStreamError) and not (
                        isinstance(getattr(exc, "__cause__", None), InterruptedByUserInput)
                        or getattr(getattr(exc, "__cause__", None), "__class__", None).__name__
                        == "InterruptedByUserInput"
                    ):
                        raise
                    logger.info(
                        "In-flight generation interrupted by incoming user message; adapting immediately"
                    )
                    partial_text = "".join(streamed_tokens).strip()
                    if partial_text:
                        self.ui.on_assistant_message(partial_text, None)
                        self.messages.append(Message.assistant(partial_text))
                    else:
                        self.ui.on_assistant_message(None, None)
                    if self._drain_inbox():
                        # no fresh recall inside a fold either: the frozen head and the
                        # cache behind it stay byte-identical
                        latest = self.messages[-1].content or ""
                        system_prompt = self.build_system_prompt(latest)
                        if self._stop_requested():
                            # the interruption was a stop: the turn ends now
                            self.ui.on_assistant_message("Stopped.")
                            self.messages.append(Message.assistant("Stopped."))
                            final = "Stopped."
                            break
                    continue

                if (
                    response.finish_reason == "length"
                    and not response.content
                    and not response.tool_calls
                ):
                    # the model thought until the budget ran out (reasoning models do on a

                    # hard step): the same call once more, with room to think and answer

                    logger.info("reply cut off before it began; asking again with more room")

                    response = await task_budget.ask(
                        self.llm,
                        context,
                        tools=tool_params,
                        on_delta=self.ui.on_text_delta,
                        on_reasoning_delta=self.ui.on_reasoning_delta,
                        max_tokens=self.llm.roomier_max_tokens(),
                    )

                if response.request_id:
                    self.last_call = (response.request_id, response.turn_source)

                if self.llm.vision_available is False and not self._told_no_vision:
                    # the turn's own pictures: one note per session, on a message that
                    # actually carried one (a picture left in context from an older turn
                    # is none of this turn's business)

                    last_user = next(
                        (m for m in reversed(self.messages) if m.role == Role.USER), None
                    )

                    if last_user is not None and last_user.images:
                        self._told_no_vision = True

                        self.ui.warn(
                            "This model does not take images, so the picture was described to "
                            "it by name only. It stays in the workspace; a model with vision "
                            "(Settings → Connections) would see it."
                        )

                trace.record_llm_step(response.usage)

                assistant = response.to_message()

                assistant.meta.update({"step": step, "usage": response.usage})

                self.messages.append(assistant)

                self.ui.on_assistant_message(response.content, response.reasoning)

                self.audit.record(
                    "assistant_message",
                    step=step,
                    content=response.content or "",
                    tool_calls=[tc.function.name for tc in response.tool_calls],
                    usage=response.usage,
                )

                if not response.tool_calls:
                    if response.content:
                        final = response.content

                        break

                    empty_replies += 1

                    if empty_replies >= 2:
                        final = ""

                        break

                    response = await task_budget.ask(
                        self.llm,
                        self._request_context(system_prompt, None),
                        tools=None,
                        on_delta=self.ui.on_text_delta,
                        on_reasoning_delta=self.ui.on_reasoning_delta,
                    )

                    if response.content:
                        self.messages[-1] = response.to_message()

                        self.ui.on_assistant_message(response.content, response.reasoning)

                        final = response.content

                        break

                    continue

                if (
                    step == 1
                    and len(response.tool_calls) == 1
                    and not response.tool_calls[0].arguments
                ):
                    tc0 = response.tool_calls[0]

                    t0 = self.tools.get(tc0.name)

                    req_params = (t0.parameters or {}).get("required") if t0 else None

                    if req_params:
                        logger.info(
                            "tool {} called with empty arguments on step 1; retrying with tools=None",
                            tc0.name,
                        )

                        self.messages.pop()

                        response = await task_budget.ask(
                            self.llm,
                            self._request_context(system_prompt, None),
                            tools=None,
                            on_delta=self.ui.on_text_delta,
                            on_reasoning_delta=self.ui.on_reasoning_delta,
                        )

                        assistant = response.to_message()

                        assistant.meta.update({"step": step, "usage": response.usage})

                        self.messages.append(assistant)

                        self.ui.on_assistant_message(response.content, response.reasoning)

                        final = response.content or ""

                        break

                if self._tool_loop_detected():
                    logger.warning(
                        "agent repeated identical tool call; forcing text response with tools=None"
                    )
                    self.messages.pop()
                    self.messages.append(
                        Message.user(
                            f"Based on the tool results above, directly answer the user's request: '{user_input}' in plain text."
                        )
                    )
                    response = await task_budget.ask(
                        self.llm,
                        self._request_context(system_prompt, None),
                        tools=None,
                        on_delta=self.ui.on_text_delta,
                        on_reasoning_delta=self.ui.on_reasoning_delta,
                    )
                    assistant = response.to_message()
                    assistant.meta.update({"step": step, "usage": response.usage})
                    self.messages.append(assistant)
                    self.ui.on_assistant_message(response.content, response.reasoning)
                    final = response.content or ""
                    break

                stop = False

                pictures: list[str] = []

                for call in response.tool_calls:
                    budget = task_budget.current()

                    if budget is not None:
                        budget.check()

                    tool = self.tools.get(call.name)

                    if tool is None:
                        summary = f"{call.name}(?)"

                    elif "__raw__" in call.arguments:
                        raw = str(call.arguments["__raw__"])

                        summary = f"{call.name}: arguments cut off ({len(raw)} chars)"

                    else:
                        try:
                            summary = tool.assess(call.arguments).summary

                        except Exception as exc:  # noqa: BLE001
                            # the gate's assess will fail the same way, as a tool result

                            summary = f"{call.name}: bad arguments ({type(exc).__name__})"

                    self.ui.on_tool_call(call, summary)

                    if tool is None:
                        result = ToolResult.fail(
                            f"unknown tool '{call.name}'. Available tools: {', '.join(t.name for t in self.tools)}"
                        )

                    else:
                        trace.record_tool_call(call.name)

                    result = await self.sentinel.guard(call, tool)

                    self.ui.on_tool_result(call, result)

                    self.messages.append(Message.tool(result.for_model(), call.id, call.name))

                    if result.images:
                        pictures.extend(result.images)

                    if result.stop:
                        final = result.output

                        stop = True

                if stop:
                    break

                if (
                    pictures
                    and self.llm.vision_available is not False
                    and self.settings.llm.vision != "off"
                ):
                    # A tool result cannot carry an image, so the picture (the phone's screen)

                    # follows as a user message; models without vision get the text alone.

                    self.messages.append(
                        Message.user(
                            "[The screenshot that goes with the tool result above.]",
                            images=pictures[-2:],
                        )
                    )

                if self._is_stuck():
                    if self._stuck_nudges < 2:
                        # one more chance to change strategy instead of ending the turn:
                        # a nudge the model can act on, then the loop runs again with
                        # tools. Only when two nudges change nothing does the wrap-up
                        # step in (keep-going rule: stop for a roadblock, not a repeat).
                        self._stuck_nudges += 1

                        logger.warning("agent seems stuck; nudging it to change strategy")

                        self.messages.append(Message.user(prompts.STUCK_PROMPT))

                        continue

                    logger.warning(
                        "agent stuck after {} nudges; forcing text wrap-up without tools",
                        self._stuck_nudges,
                    )

                    self.messages.append(
                        Message.user(
                            "Stop calling tools immediately. Answer the user directly with text now."
                        )
                    )

                    context = self._request_context(system_prompt, None)

                    response = await task_budget.ask(
                        self.llm,
                        context,
                        tools=None,
                        on_delta=self.ui.on_text_delta,
                        on_reasoning_delta=self.ui.on_reasoning_delta,
                    )

                    self.messages.append(response.to_message())

                    self.ui.on_assistant_message(response.content, response.reasoning)

                    final = response.content or ""

                    break

            self.state = AgentState.FINISHED

        except Exception:
            self.state = AgentState.ERROR

            raise

        finally:
            perf_summary = InstrumentationRegistry.get().finish_trace(
                trace, status=self.state.value
            )

            self.audit.record("task_performance", **perf_summary)

            llm_session.end_turn(turn_token)

            llm_session.leave(session_token)

            self.sentinel.end_task(task_token)

            self._save_session()

        return final or ""

    # ------------------------------------------------------------------ session persistence

    def reset(self) -> None:

        self.messages.clear()

        # only this conversation: the taint of the chats running beside this one is theirs

        self.sentinel.untaint(self.conversation_id or "")

        self.state = AgentState.IDLE

    def forget(self, role: str, text: str, attachments_count: int = 0) -> int:

        """History lines that read like a deleted bubble go with it.

        The newest match leaves when the session says the same words twice; the count
        answers how many went. Saved at once, so a restart cannot resurrect what the
        person asked to see gone."""

        target = Role.USER if role == "user" else Role.ASSISTANT
        needle = (text or "").strip()

        def _msg_text(msg: Message) -> str:
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(str(part.get("text") or ""))
                return "\n".join(parts)
            return ""

        for i in range(len(self.messages) - 1, -1, -1):

            message = self.messages[i]

            if message.role != target:
                continue

            body = _msg_text(message)
            if needle:
                if body == text or body.strip() == needle:
                    self.messages.pop(i)
                    self._save_session()
                    return 1
            elif attachments_count > 0 and (
                not body.strip() or isinstance(message.content, list)
            ):
                self.messages.pop(i)
                self._save_session()
                return 1

        return 0

    def _save_session(self) -> None:

        if not self.session_file:
            return

        temporary: Path | None = None

        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)

            data: dict[str, Any] = {
                "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "turns": self.turns,
                "messages": [m.model_dump(mode="json") for m in self.messages],
            }

            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.session_file.parent,
                prefix=f".{self.session_file.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)

                json.dump(data, handle, ensure_ascii=False, indent=1)

                handle.flush()

                os.fsync(handle.fileno())

            os.replace(temporary, self.session_file)

            temporary = None

        except OSError as exc:  # pragma: no cover
            logger.warning("could not save session: {}", exc)

        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load_session(self, path: Path) -> int:

        data = json.loads(Path(path).read_text("utf-8"))

        self.messages = [Message.model_validate(m) for m in data.get("messages", [])]

        self.turns = int(data.get("turns", 0))

        self._repair_dangling_tool_calls()

        return len(self.messages)

    def _repair_dangling_tool_calls(self, reason: str = "") -> None:
        """A restart in the middle of a tool call (say, while an approval was pending)

        leaves an assistant message whose tool calls have no results. Providers reject

        that history outright, so give every orphan a result that says what happened."""

        repaired: list[Message] = []

        i = 0

        while i < len(self.messages):
            msg = self.messages[i]

            repaired.append(msg)

            i += 1

            if msg.role != Role.ASSISTANT or not msg.tool_calls:
                continue

            answered: set[str] = set()

            while i < len(self.messages) and self.messages[i].role == Role.TOOL:
                repaired.append(self.messages[i])

                answered.add(self.messages[i].tool_call_id or "")

                i += 1

            for call in msg.tool_calls:
                if call.id not in answered:
                    repaired.append(
                        Message.tool(
                            reason
                            or "(no result: the app was restarted before this call finished)",
                            call.id,
                            call.function.name,
                        )
                    )

                    if reason:
                        self.ui.on_tool_result(call, ToolResult.fail(reason))

        if len(repaired) != len(self.messages):
            logger.info("repaired {} unanswered tool call(s)", len(repaired) - len(self.messages))

            self.messages = repaired


__all__ = ["MuseAgent"]
