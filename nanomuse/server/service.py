"""The always-on nanoMuse: threads, background workers, goals scheduler, ideas, profile.

This is the piece that keeps working after you close the app. It owns one
:class:`~nanomuse.app.NanoMuseApp` (LLM, tools, Sentinel, vault, memory, goals)
and runs one agent per *thread* — the main chat plus any side chats — so the
user can fire off several requests without waiting for the last one to finish.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
import time
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from datetime import time as time_of_day
from pathlib import Path
from typing import Any

from nanomuse import __version__, prompts
from nanomuse.agent import Incoming, MuseAgent
from nanomuse.app import NanoMuseApp
from nanomuse.avatar import AvatarStudio
from nanomuse.background import spawn
from nanomuse.bridge.server import Bridge
from nanomuse.cloud import CLOUD_KEY, DEFAULT_CHAT_MODEL, CloudError, model_url
from nanomuse.coding.service import CodingService
from nanomuse.config import LLMSettings, Settings
from nanomuse.goals import Goal
from nanomuse.hub.service import HubService
from nanomuse.instrumentation import InstrumentationRegistry
from nanomuse.llm import BaseLLM
from nanomuse.llm import session as llm_session
from nanomuse.llm.factory import create_llm, llm_ready
from nanomuse.logger import logger
from nanomuse.memory.consolidate import TidyReport, tidy
from nanomuse.nudges import NudgesPolicy
from nanomuse.phone import PhoneLink
from nanomuse.private import keep_private
from nanomuse.reminders import Reminder
from nanomuse.schema import Attachment, Message, Role
from nanomuse.sentinel.grants import normalize_scope
from nanomuse.server import firstrun
from nanomuse.server.app_lock import AppLockStore
from nanomuse.server.chrome_bridge import ChromeBridge
from nanomuse.server.connections import Connections
from nanomuse.server.events import (
    MAIN_THREAD,
    STOPPED_NOTICE,
    EventBus,
    Timeline,
    new_id,
    now_iso,
)
from nanomuse.server.failures import failure_notice
from nanomuse.server.outbox import Delivery, DeliveryOutbox
from nanomuse.server.proactivity import AutoProactivity, level_for
from nanomuse.server.providers import ChatGPTSignIn, Providers
from nanomuse.server.push import PushService
from nanomuse.server.usage_ledger import UsageLedger
from nanomuse.server.usage_limits import UsageLimitStore
from nanomuse.server.webui import WebUI, current_thread
from nanomuse.sync import ConversationSync, parse_mention, system_note
from nanomuse.telemetry import telemetry
from nanomuse.tools.browser import Browser
from nanomuse.tools.reminder_tools import Reminders
from nanomuse.tools.trigger_tools import Triggers
from nanomuse.triggers import MailWatcher, Trigger, matches
from nanomuse.usage import known_cost_model, set_user_rates, usage_note

# The chat model switch (2026-11-10): "use flash", "switch to pro", "use local" route one
# turn to a named model, the same one-turn shape as Gemini. The verbs that introduce a
# model and the negation window are the Gemini rule, generalised to every alias.
_SWITCH_VERBS = r"(?:use|using|with|switch to|ask|try|hop on|move to)"
_SWITCH_NEGATION = r"\b(?:don'?t|do not|never|stop|no(?:t)? need|without)\s+(?:\w+\s+){0,2}?"
# The words the user can say to switch models this turn, and the turn each word runs on:
# flash/deepseek → Flash with thinking off (the fast default), pro → V4 Pro thinking on
# (the careful one, text only), local/qwen → the local Qwen fallback on Ollama.
SWITCH_WORDS: tuple[str, ...] = ("flash", "deepseek", "pro", "local", "qwen")

# A reply that IS the decision on a card waiting in the chat: "yes", "go ahead",
# "no", "cancel". Short, all-in-one-word-set messages only, so ordinary sentences
# still reach the agent.
_APPROVE_WORDS: frozenset[str] = frozenset(
    {
        "yes",
        "yep",
        "yeah",
        "yea",
        "yup",
        "sure",
        "ok",
        "okay",
        "k",
        "kk",
        "go",
        "ahead",
        "do",
        "it",
        "approve",
        "approved",
        "allow",
        "allowed",
        "accept",
        "accepted",
        "proceed",
        "sounds",
        "good",
        "fine",
        "please",
        "run",
        "affirmative",
        "for",
        "alright",
        "bet",
        "cool",
        "copy",
        "green",
        "light",
        "confirmed",
        "y",
    }
)
_DENY_WORDS: frozenset[str] = frozenset(
    {
        "no",
        "nope",
        "nah",
        "n",
        "deny",
        "denied",
        "reject",
        "rejected",
        "cancel",
        "canceled",
        "cancelled",
        "stop",
        "dont",
        "do",
        "not",
        "never",
        "mind",
        "nevermind",
        "nvm",
        "block",
        "blocked",
        "negative",
        "halt",
        "abort",
        "scrap",
        "pass",
        "skip",
        "way",
    }
)
# The same decisions in Chinese: the whole message is one short phrase, nothing else,
# so a bare "不" decides and "不要用pro" (a normal instruction) stays a message.
_APPROVE_PHRASES_ZH: frozenset[str] = frozenset(
    {
        "好",
        "好的",
        "好啊",
        "好呀",
        "行",
        "行的",
        "可以",
        "可以的",
        "嗯",
        "嗯嗯",
        "对",
        "是的",
        "批准",
        "批准了",
        "同意",
        "允许",
        "继续",
        "去做",
        "去做吧",
        "没问题",
    }
)
_DENY_PHRASES_ZH: frozenset[str] = frozenset(
    {
        "不",
        "不行",
        "不可以",
        "不要",
        "别",
        "不做",
        "取消",
        "停下",
        "停止",
        "算了",
        "拒绝",
        "不同意",
    }
)


def _chat_approval_verdict(text: str) -> bool | None:
    """True = approve, False = deny, None = not a decision (a normal message)."""
    words = re.sub(r"[^a-z\s]", " ", text.lower()).split()
    if not words or len(words) > 4:
        # a Chinese reply is one short phrase: the whole message, punctuation off
        bare = re.sub(r"[\s，。、！!？?～~·…]+", "", text)
        if bare and len(bare) <= 6:
            if bare in _DENY_PHRASES_ZH:
                return False
            if bare in _APPROVE_PHRASES_ZH:
                return True
        return None
    if any(w in ("stop", "cancel", "deny", "abort", "dont", "nevermind", "nvm") for w in words):
        return False
    if all(w in _APPROVE_WORDS for w in words):
        return True
    if all(w in _DENY_WORDS for w in words):
        return False
    return None


def _usage_source(run: str, purpose: str | None) -> str:
    """The ledger's word for who started a turn: the person typed it, another device
    asked over the hub, or a background pass (goal, routine, reminder, feed) ran it."""
    if run == "person" and not purpose:
        return "person"
    return "device" if run == "hub" else "background"


def _model_alias_in_text(text: str, alias: str, direct_address: bool = False) -> bool:
    """Detect the user asking FOR a named model, not away from it. Gemini alone also answers to its
    name ("Gemini, what's the plan"); "flash," and "pro," are words, not a chatbot being
    addressed."""
    positive = re.search(
        rf"\b(?<!not ){_SWITCH_VERBS}\s+(?:the\s+)?(?:new\s+)?{alias}\b", text, re.I
    )
    if not positive and direct_address:
        positive = re.search(rf"\b{alias}[,:]\s*\S", text, re.I)
    if not positive:
        return False
    return not re.search(_SWITCH_NEGATION + rf"{alias}", text, re.I)


IDEAS_PROMPT = """You are {name}, the user's personal agent. Based on what you know about them, propose {n} concrete, genuinely useful things you could do for them right now. Prefer tasks you can actually complete with your tools (research, comparisons, planning, drafting, tracking, reminders, organising files, advancing their goals).

What you know:
{context}

Answer with a JSON array only, no prose. Each item: {{"title": "<short title, max 8 words>", "detail": "<one sentence on what you would do and why it helps>", "prompt": "<the exact request the user could send you to start>", "area": "<one of: planning, research, goals, money, health, home, learning, people, files, fun>", "kind": "<chat for a one-off conversation; routine for something to do every day at a time; goal for something long-term to track>", "time": "<HH:MM, routines only>", "category": "<goals only; one of: health, finance, career, learning, relationships, family, home, travel, creative, other>"}}. Mostly chat; at most one routine and one goal. Write in the user's language ({language})."""

# what tapping an idea does on every client: a message in the main chat, a daily routine
# (a repeating reminder with the prompt), or a goal conversation (the phone's IdeaKind)
IDEA_KINDS = ("chat", "routine", "goal")
IDEA_GOAL_CATEGORIES = (
    "health",
    "finance",
    "career",
    "learning",
    "relationships",
    "family",
    "home",
    "travel",
    "creative",
    "other",
)

IDEA_AREAS = (
    "planning",
    "research",
    "goals",
    "money",
    "health",
    "home",
    "learning",
    "people",
    "files",
    "fun",
)

FEED_PROMPT = """You are {name}, the user's personal agent, writing their personal feed: a few short posts made just for them, to read when they open the app. Think of a thoughtful friend who knows what they care about: a nudge on a goal, something worth knowing about a topic they follow, a small plan for the day, a question worth thinking about, a summary of something you noticed. Be specific to this person; never generic filler.

The user's feed instructions (what they want to read here, how often, in what tone):
{instructions}

What you know about them:
{context}

Write {n} posts. Answer with a JSON array only, no prose. Each item: {{"title": "<max 10 words>", "body": "<60-160 words of Markdown; short paragraphs or a list; no heading>", "area": "<one of: planning, research, goals, money, health, home, learning, people, files, fun>", "prompt": "<a request the user could send you to follow up, or empty>"}}. Write in the user's language ({language})."""

FEED_DEFAULT_TIME = "08:00"  # the daily routine, local time (contract C5)
FEED_MIN_GAP_HOURS = 4  # a batch asked for by hand shortly before the hour counts as the day's
FEED_RETRY_S = 3600  # after a failed batch (the model down), the next try an hour later
FEED_KEEP = 200
_FEED_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _feed_time(value: Any) -> str:
    """``"8:00"`` → ``"08:00"``; anything that is not ``HH:MM`` → ``""``."""
    m = _FEED_TIME.match(str(value or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


STARTER_IDEAS = [
    {
        "title": "Plan my week",
        "detail": "Tell me what's on your plate and I'll turn it into a realistic plan with the important things first.",
        "prompt": "Help me plan my week. Ask me what I need to get done, then propose a schedule.",
        "area": "planning",
        "kind": "chat",
    },
    {
        "title": "A morning brief",
        "detail": "Before you leave each morning: the weather, the first thing on your plate and anything new worth knowing.",
        "prompt": "Morning brief: check today's weather, read my calendar and reminders for today, and tell me in five lines what matters.",
        "area": "planning",
        "kind": "routine",
        "time": "07:30",
    },
    {
        "title": "Research & compare options",
        "detail": "Laptops, flights, insurance, a new phone plan: I'll gather the facts and compare them for you.",
        "prompt": "I need to make a purchase decision. Ask me what I'm choosing between, then research and compare the options.",
        "area": "research",
        "kind": "chat",
    },
    {
        "title": "Set up a long-term goal",
        "detail": "Share a goal (learn a language, run a 10k, save for a trip) and I'll break it into steps and keep track.",
        "prompt": "I want to set up a long-term goal. Ask me about it, then create a plan with concrete steps and track it.",
        "area": "goals",
        "kind": "goal",
        "category": "other",
    },
    {
        "title": "Tell me about yourself",
        "detail": "The more I know about your preferences, routines and constraints, the more useful I get. I'll remember what matters.",
        "prompt": "Ask me a few questions about myself so you can help me better, and remember the answers.",
        "area": "people",
        "kind": "chat",
    },
    {
        "title": "Build a quick tracker",
        "detail": "Spending, habits, workouts, reading: I can write a small script or document to track it for you.",
        "prompt": "Build me a simple tracker. Ask me what I want to track and how, then create it in the workspace.",
        "area": "files",
        "kind": "chat",
    },
]


PROACTIVITY = ("off", "low", "default", "high", "auto")

# identity limits and vocabularies (the app's forms and the identity tool share them)
NAME_MAX = 20
TAGLINE_MAX = 60
TONES: dict[str, str] = {
    "formal": "Tone: formal. Polite and precise, no slang, no exclamation marks.",
    "casual": "Tone: casual. Relaxed and friendly, the way a capable friend talks.",
    "playful": "Tone: playful. Light and a little witty, never at the expense of being clear.",
    "concise": "Tone: concise. Say what matters, then stop; no preamble, no recap.",
}
COMMUNICATION: dict[str, str] = {
    "short": "Length: keep replies short, a few sentences, one or two paragraphs at most, unless asked for more.",
    "detailed": "Length: be thorough; give the reasoning, the alternatives and what you ruled out.",
    "bullets": "Shape: prefer bullet points and short headings over running prose; one idea per line.",
}
# how the configured interval stretches or shrinks per level
_INTERVAL_FACTOR = {"low": 2.0, "default": 1.0, "high": 0.5}
_QUIET_HOURS_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)$")
# A thread id arrives over the WebSocket and names a file in the threads folder, so it
# carries no separator: the same rule as channels' thread_id_for, whose longest id is
# "channel-" plus an 80-character chat id.
_THREAD_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")
# how long `stop()` lets a cancelled worker take to settle before closing over its work
_SHUTDOWN_GRACE_S = 5.0


@dataclass
class Profile:
    name: str = "MiniMuse"
    # the face: "dragon" (the one the Android app wears; stills per mood), a face drawn in
    # the avatar studio (its id names files under the workspace), or "" for the emoji; the
    # red panda and the dolls of 0.1.22 are gone — a profile naming one wears the dragon
    avatar: str = "dragon"
    emoji: str = "✨"
    color: str = "#0064d4"
    # avatar background: "theme" (matches active light/dark/custom theme), "default" (#f1efeb disc),
    # "color" (custom hex/gradient in avatar_bg_color), or "image" (workspace path / URL in avatar_bg_image)
    avatar_bg_mode: str = "theme"
    avatar_bg_color: str = ""
    avatar_bg_image: str = ""
    # avatar border & glow: separate from background ("theme" | "color" | "none")
    avatar_border_mode: str = "theme"
    avatar_border_color: str = ""
    avatar_border_glow: int = 55
    # custom situation -> clip/image filename map for the active avatar face
    avatar_situation_clips: dict[str, str] = field(default_factory=dict)
    updated_at: float = 0.0
    # one line under the name, the way Muse shows it ("Your day, sorted.")
    tagline: str = ""
    # how it talks: a tone (formal · casual · playful · concise), a length or shape of reply
    # (short · detailed · bullets) and free text; each becomes its own paragraph of the prompt
    tone: str = ""
    communication: str = ""
    style: str = ""
    # what the user wants to be called
    user_name: str = ""
    # how eagerly background work runs and reaches out: off · low · default · high
    proactivity: str = "default"
    goal_interval_minutes: int = 60
    # "22:00-08:00" (server local time): no background passes in this window
    quiet_hours: str = ""
    # what "auto" has learned from how the check-ins were answered (set by the service from
    # proactivity.AutoProactivity; never saved with the profile)
    auto_factor: float = 1.0

    @property
    def proactive(self) -> bool:
        return self.proactivity != "off"

    @property
    def effective_level(self) -> str:
        """The level a pass behaves at: the chosen one, or what "auto" has settled on."""
        if self.proactivity == "auto":
            return level_for(self.auto_factor)
        return self.proactivity

    @property
    def interval_seconds(self) -> int:
        factor = self.auto_factor if self.proactivity == "auto" else _INTERVAL_FACTOR.get(self.proactivity, 1.0)
        return max(60, int(self.goal_interval_minutes * 60 * factor))

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        window = _parse_quiet_hours(self.quiet_hours)
        if window is None:
            return False
        start, end = window
        local = (now or datetime.now()).astimezone()
        minute = local.hour * 60 + local.minute
        if start <= end:
            return start <= minute < end
        return minute >= start or minute < end  # wraps midnight

    def quiet_hours_end(self, now: datetime | None = None) -> datetime | None:
        """When the current quiet window ends, or None if we are not in one."""
        if not self.in_quiet_hours(now):
            return None
        _, end = _parse_quiet_hours(self.quiet_hours)  # type: ignore[misc]
        now = (now or datetime.now()).astimezone()
        candidate = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "avatar": self.avatar,
            "emoji": self.emoji,
            "color": self.color,
            "avatar_bg_mode": self.avatar_bg_mode,
            "avatar_bg_color": self.avatar_bg_color,
            "avatar_bg_image": self.avatar_bg_image,
            "avatar_border_mode": self.avatar_border_mode,
            "avatar_border_color": self.avatar_border_color,
            "avatar_border_glow": self.avatar_border_glow,
            "avatar_situation_clips": dict(self.avatar_situation_clips or {}),
            "updated_at": self.updated_at,
            "tagline": self.tagline,
            "tone": self.tone,
            "communication": self.communication,
            "style": self.style,
            "user_name": self.user_name,
            "proactivity": self.proactivity,
            "effective_level": self.effective_level,
            "proactive": self.proactive,
            "goal_interval_minutes": self.goal_interval_minutes,
            "quiet_hours": self.quiet_hours,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        p = cls()
        for k, v in data.items():
            if k not in ("proactive", "effective_level") and hasattr(p, k) and v is not None:
                setattr(p, k, v)
        if "proactivity" not in data and "proactive" in data:
            # profiles written before the dial existed
            p.proactivity = "default" if data["proactive"] else "off"
        if p.proactivity not in PROACTIVITY:
            p.proactivity = "default"
        raw_name = str(p.name).strip()
        # Legacy <= v1.0.15 profiles persisted the default "nanoMuse" name on disk;
        # migrate that untouched default to "MiniMuse" while preserving custom names.
        if raw_name.lower() == "nanomuse":
            raw_name = "MiniMuse"
        p.name = (raw_name or "MiniMuse")[:NAME_MAX]
        if "avatar" not in data:
            # a profile from before the dolls: keep the emoji it has
            p.avatar = ""
        p.avatar = re.sub(r"[^a-z0-9-]", "", str(p.avatar).lower())[:32]
        p.emoji = str(p.emoji)[:8] or "✨"
        p.color = str(p.color)[:16] or "#0064d4"
        p.avatar_bg_mode = str(p.avatar_bg_mode or "theme").strip().lower()
        if p.avatar_bg_mode not in ("theme", "default", "color", "image"):
            p.avatar_bg_mode = "theme"
        p.avatar_bg_color = str(p.avatar_bg_color or "").strip()[:32]
        p.avatar_bg_image = str(p.avatar_bg_image or "").strip()[:500]
        p.avatar_border_mode = str(p.avatar_border_mode or "theme").strip().lower()
        if p.avatar_border_mode not in ("theme", "color", "none"):
            p.avatar_border_mode = "theme"
        p.avatar_border_color = str(p.avatar_border_color or "").strip()[:32]
        try:
            p.avatar_border_glow = max(0, min(100, int(p.avatar_border_glow)))
        except (TypeError, ValueError):
            p.avatar_border_glow = 55
        raw_sit = data.get("avatar_situation_clips")
        if isinstance(raw_sit, dict):
            p.avatar_situation_clips = {
                str(k).strip().lower()[:40]: str(v).strip()[:200]
                for k, v in raw_sit.items()
                if k and v
            }
        else:
            p.avatar_situation_clips = {}
        try:
            p.updated_at = float(data.get("updated_at") or p.updated_at or 0.0)
        except (TypeError, ValueError):
            p.updated_at = 0.0
        p.tagline = " ".join(str(p.tagline).split())[:TAGLINE_MAX]
        p.tone = str(p.tone).strip().lower()
        if p.tone not in TONES:
            p.tone = ""
        p.communication = str(p.communication).strip().lower()
        if p.communication not in COMMUNICATION:
            p.communication = ""
        p.style = str(p.style)[:1000]
        p.user_name = str(p.user_name).strip()[:60]
        p.goal_interval_minutes = max(5, min(int(p.goal_interval_minutes), 24 * 60))
        p.quiet_hours = str(p.quiet_hours).strip()
        if p.quiet_hours and _parse_quiet_hours(p.quiet_hours) is None:
            p.quiet_hours = ""
        return p


def _parse_quiet_hours(value: str) -> tuple[int, int] | None:
    m = _QUIET_HOURS_RE.match(value.strip()) if value else None
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    return (start, end) if start != end else None


@dataclass
class Thread:
    id: str
    title: str
    created_at: str
    updated_at: str
    timeline: Timeline
    agent: MuseAgent
    inbox: asyncio.Queue[str | Incoming] = field(default_factory=asyncio.Queue)
    worker: asyncio.Task[None] | None = None
    stopping: bool = False
    busy: bool = False
    # background prompts (goal work, ideas) → the short label shown as the approval purpose
    purposes: dict[str, str] = field(default_factory=dict)
    # a chat addressed to another device of the account: what is typed here runs there
    device: str | None = None
    device_name: str = ""
    # a chat another device opened here with a `task` over the hub: who asked
    remote_from: dict[str, Any] | None = None
    # a chat that came in through conversation sync (contract C7): the device that started
    # it, for the "From Pixel 8" badge; "" for chats started here
    origin_device: str = ""
    origin_device_name: str = ""
    # the main chat of another account of this device (contract C10): kept while someone
    # else is signed in, hidden from them, back as `main` when that account returns
    main_of: str = ""

    def last_message_ts(self) -> str:
        """The newest message either person actually wrote, "" when the conversation only
        holds notices (a document imported by an older build, a system line). Notices carry
        the time they were imported, which says nothing about when the chat was last used."""
        newest = ""
        for ev in self.timeline.events:
            if ev.get("type") not in ("user", "assistant"):
                continue
            ts = str(ev.get("ts") or "")
            if ts > "1971-01-01" and ts > newest:
                newest = ts
        return newest

    def refresh_times_from_events(self) -> bool:
        """The dates a conversation is shown and sorted by are its messages' dates, not
        when a file happened to be imported or when the chat was last renamed. An archive
        imported today still starts where its first message was, and sits in the list by
        its newest message like every chat written here. Returns True when something moved."""
        stamps = [
            str(ev.get("ts") or "")
            for ev in self.timeline.events
            if str(ev.get("ts") or "") > "1971-01-01"
        ]
        if not stamps:
            return False
        first = min(stamps)
        changed = False
        if not self.created_at or first < self.created_at:
            self.created_at = first
            changed = True
        last = self.last_message_ts()
        if last and last != self.updated_at:
            self.updated_at = last
            changed = True
        return changed

    def meta(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            # the newest real message: the list is ordered by this, so a shell that only
            # holds an imported notice sorts below every chat somebody actually spoke in
            "last_message_at": self.last_message_ts(),
            "busy": self.busy,
            "queued": self.inbox.qsize(),
            "events": self.timeline.total,
        }
        if self.device:
            meta["device"] = self.device
            meta["device_name"] = self.device_name
        if self.remote_from:
            meta["remote_from"] = self.remote_from
        if self.origin_device:
            meta["origin_device"] = self.origin_device
            meta["origin_device_name"] = self.origin_device_name
        if self.main_of:
            meta["main_of"] = self.main_of
        return meta


def _parse_when(value: str | None) -> datetime | None:
    """An ISO timestamp from a store → aware datetime (naive ones are local), or None."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when.astimezone()  # a naive stamp is local time


class MuseService:
    def data_export_sources(self) -> dict[str, sqlite3.Connection]:
        """The active stores' main databases, never newly opened export paths."""
        sources = {
            "goals.db": self.app.goals._conn,
            "reminders.db": self.app.reminders._conn,
            "triggers.db": self.app.triggers._conn,
            "scheduled-deliveries.db": self.deliveries._conn,
        }
        if self.app.memory is not None:
            sources["memory.db"] = self.app.memory._conn
        return sources

    def __init__(self, settings: Settings, llm: BaseLLM | None = None):
        self.settings = settings
        settings.ensure_dirs()
        self.data_dir: Path = settings.data_dir
        self.threads_dir = self.data_dir / "threads"
        self.threads_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir = self.threads_dir / "trash"
        self.trash: list[dict[str, Any]] = []
        self.deliveries = DeliveryOutbox(self.data_dir / "scheduled-deliveries.db")
        self.bus = EventBus()
        self.threads: dict[str, Thread] = {}
        # chats waiting on a model-made name (so a burst of turns asks once)
        self._title_pending: set[str] = set()
        self._base_instructions = settings.agent.instructions
        self.profile = self._load_profile()
        self.auto_proactivity = AutoProactivity(self.settings.data_dir)
        self.profile.auto_factor = self.auto_proactivity.factor()
        self._apply_profile()
        self.ui = WebUI(
            self.bus,
            self.timeline,
            approval_timeout=settings.server.approval_timeout,
            show_thinking=settings.agent.show_thinking,
            workspace=settings.agent.workspace.resolve(),
            exclude=(settings.data_dir,),
        )
        self.ui._timelines_provider = lambda: [(t.id, t.timeline) for t in self.threads.values()]
        self.push = PushService(settings.data_dir, port=settings.server.port)
        self.usage_ledger = UsageLedger(settings.data_dir / "usage.jsonl")
        set_user_rates(
            settings.llm.model,
            settings.llm.input_usd_per_million,
            settings.llm.output_usd_per_million,
        )
        self.ui.on_event = self._on_event
        # The phone: whichever device announces itself on the WebSocket (see nanomuse.phone).
        self.phone = PhoneLink(
            timeout_s=settings.gui.device_timeout_s,
            shots_dir=settings.agent.workspace.resolve() / "screenshots",
        )
        self.phone.on_change = self.publish_phone
        self.chrome_bridge = ChromeBridge(
            on_change=lambda st: self.bus.publish({"kind": "chrome_state", **st})
        )
        self.app = NanoMuseApp(settings, ui=self.ui, llm=llm, session_id="app", phone=self.phone)
        # a hold belongs to the chat whose run is acting (docs/browser.md, contract C1)
        self.app.holds.thread_of = current_thread.get
        # The CLI bridge: `nanomuse-device` and friends, from inside a shell command. It
        # learns where the server listens in `serve()`; until then no token is minted.
        self.bridge = Bridge(self.app.tools, self.app.sentinel, self.ui)
        for name in ("shell", "python_execute"):
            tool = self.app.tools.get(name)
            if tool is not None:
                tool.bridge = self.bridge  # type: ignore[attr-defined]
        self.watch_browser()
        self._watch_reminders()
        self._mail_polled_at: float | None = None
        self._fired_pruned_at: float | None = None
        self.mail_checked_at: str | None = None
        self.mail_watch_error = ""
        self._watch_triggers()
        self.connections = Connections(self)
        # usage limits (per model or global) checked before every turn (Usage screen +
        # the system_admin tool write the same store)
        self.usage_limits = UsageLimitStore(self.data_dir)
        # the lock screen's PIN (hashed in app settings) and its biometric credential
        self.app_lock = AppLockStore(self.data_dir)
        # the browsers on the live socket right now, keyed by user agent: the Devices
        # screen's local view (no account needed)
        self._live_clients: dict[str, dict[str, Any]] = {}
        # the sentinel's configured lists, before any capability policy is applied
        # (a capability on "ask" returns its tools to these)
        self._sentinel_baseline = {
            "always_ask": list(self.settings.sentinel.always_ask_tools),
            "always_allow": list(self.settings.sentinel.always_allow_tools),
            "deny": list(self.settings.sentinel.deny_tools),
        }
        # a policy saved earlier is in force from the first moment, not only after it is edited
        self._apply_permission_policy()
        admin_tool = self.app.tools.get("system_admin")
        if admin_tool is not None:
            admin_tool.attach(self)
        # this computer on the hub: the Cloud account, the other devices, their side chats
        self.hub = HubService(self)
        # the same conversations on every device of the account (contract C7; docs/every-device.md)
        self.sync = ConversationSync(self)
        # when the app may ask for a star on GitHub: the relay's policy, a day at a time
        # (contract C1; GET /api/nudges hands it to the web app)
        self.nudges = NudgesPolicy(self.data_dir, settings.cloud.base_url)
        # the first conversation (contract C4): bound to the main chat by Start, the model
        # hears its phase there and nowhere else (docs/web.md)
        self.firstrun = firstrun.FirstRunStore(self.data_dir)
        # a new face from a description, drawn on the chat model's host (docs/avatar.md)
        self.avatar = AvatarStudio(self)
        # the catalogue with what is configured and what that covers (contract C11), and the
        # ChatGPT sign-in run from the app (GET /api/providers, /api/chatgpt/*)
        self.providers = Providers(self)
        self.chatgpt = ChatGPTSignIn(self)
        self.coding = CodingService(self)
        # self.app.tools.add(CodingAgents(coding=self.coding))
        self.token = self._load_token()
        self._scheduler: asyncio.Task[None] | None = None
        # webhook deliveries accepted, by trigger id (monotonic seconds): the rate limit's
        # word while the first run is still going (deliver_hook)
        self._hook_accepted: dict[str, float] = {}
        # what start() is busy with, for /api/health and the log while the app comes up
        self.starting: str = "not started"
        self._tidying = False
        self._writing_feed = False
        self._feed_failed_at: float | None = None
        self._started = False
        self.started_at = now_iso()
        self.next_goal_pass_at: datetime | None = None
        # the scheduler naps on this: `wake()` (POST /api/tick — the phone's alarm) ends the
        # nap early so what is due runs now, not at the end of the nap
        self._wake = asyncio.Event()
        self._announced_wake: str | None = None
        self._load_threads()
        self._load_trash()

    def phone_view(self) -> dict[str, Any]:
        return {**self.phone.status(), "gui_enabled": self.settings.gui.enabled}

    def publish_phone(self) -> None:
        self.bus.publish({"kind": "phone", "phone": self.phone_view()})

    def hands_view(self) -> dict[str, Any]:
        return self.connections.hands_view()

    def publish_hands(self) -> None:
        self.bus.publish({"kind": "hands_state", "hands": self.hands_view()})

    def stop_hands(self) -> bool:
        """The Stop button of the Hands card: the task on this computer's screen ends at its
        next step, like the phone's Stop pill."""
        computer = self.app.computer
        return computer.stop() if computer is not None else False

    # ------------------------------------------------------------------ local devices
    @staticmethod
    def _friendly_device_model(model: str, ua: str, width: int = 0, height: int = 0) -> str:
        raw = (model or "").strip().strip('"')
        lower_ua = (ua or "").lower()
        if not raw or raw.upper() == "K":
            # Extract from UA if not frozen
            import re as _re

            m = _re.search(r"android\s+[\d.]+;\s*([^;)]+?)(?:\s+build/|\))", ua or "", _re.I)
            if m:
                cand = m.group(1).strip()
                if cand.upper() != "K":
                    raw = cand
        upper = raw.upper()
        if "SM-F766" in upper or "Z FLIP 7" in upper or "Z FLIP7" in upper:
            return "Galaxy Z Flip 7"
        if "SM-F741" in upper or "Z FLIP 6" in upper or "Z FLIP6" in upper:
            return "Galaxy Z Flip 6"
        if "SM-F731" in upper or "Z FLIP 5" in upper or "Z FLIP5" in upper:
            return "Galaxy Z Flip 5"
        if "SM-F721" in upper or "Z FLIP 4" in upper or "Z FLIP4" in upper:
            return "Galaxy Z Flip 4"
        if "SM-F7" in upper or "sm-f7" in lower_ua:
            return "Galaxy Z Flip"
        if "SM-F9" in upper:
            return "Galaxy Z Fold"
        if "SM-S9" in upper:
            return f"Galaxy S ({raw})"
        if raw and raw.upper() != "K":
            return raw
        if "iphone" in lower_ua:
            return "iPhone"
        if "ipad" in lower_ua:
            return "iPad"
        if "android" in lower_ua:
            if "samsung" in lower_ua:
                return "Samsung Galaxy"
            return "Android phone"
        return "Phone"

    def note_client(self, ua: str, model: str = "", platform_version: str = "") -> None:
        """A browser connected to the live socket: counted by user agent, so the Devices
        screen can say which device is actually open right now, signed in or not."""
        ua = (ua or "").strip()[:200]
        if not ua:
            return
        entry = self._live_clients.get(ua)
        if entry is None:
            self._live_clients[ua] = {
                "ua": ua,
                "model": (model or "").strip().strip('"')[:80],
                "platform_version": (platform_version or "").strip().strip('"')[:40],
                "since": now_iso(),
                "count": 1,
            }
        else:
            entry["count"] = int(entry.get("count", 1)) + 1
            if model and not entry.get("model"):
                entry["model"] = model.strip().strip('"')[:80]
            if platform_version and not entry.get("platform_version"):
                entry["platform_version"] = platform_version.strip().strip('"')[:40]

    def note_client_info(
        self,
        ua: str,
        model: str = "",
        platform_version: str = "",
        width: int = 0,
        height: int = 0,
    ) -> None:
        """Record high-entropy Client Hints or WebSocket client_info for a connected browser."""
        ua = (ua or "").strip()[:200]
        if not ua:
            return
        entry = self._live_clients.setdefault(
            ua, {"ua": ua, "since": now_iso(), "count": 1}
        )
        if model:
            entry["model"] = str(model).strip().strip('"')[:80]
        if platform_version:
            entry["platform_version"] = str(platform_version).strip().strip('"')[:40]
        if width > 0:
            entry["width"] = int(width)
        if height > 0:
            entry["height"] = int(height)
        self.bus.publish({"kind": "devices", "devices": self.local_devices_view()})

    def forget_client(self, ua: str) -> None:
        ua = (ua or "").strip()[:200]
        entry = self._live_clients.get(ua)
        if entry is None:
            return
        entry["count"] = max(0, int(entry.get("count", 1)) - 1)
        if entry["count"] <= 0:
            self._live_clients.pop(ua, None)

    def device_nicknames(self) -> dict[str, str]:
        from nanomuse.config import load_app_settings

        raw = load_app_settings(self.data_dir).get("device_nicknames")
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v).strip()[:60] for k, v in raw.items() if str(v).strip()}

    def rename_local_device(self, device_id: str, name: str) -> dict[str, Any]:
        from nanomuse.config import load_app_settings, save_app_settings

        did = str(device_id or "").strip()[:80]
        clean = str(name or "").strip()[:60]
        stored = load_app_settings(self.data_dir)
        nicknames = dict(self.device_nicknames())
        if clean:
            nicknames[did] = clean
        else:
            nicknames.pop(did, None)
        stored["device_nicknames"] = nicknames
        save_app_settings(self.data_dir, stored)
        devices = self.local_devices_view()
        if getattr(self, "bus", None):
            self.bus.publish({"kind": "devices", "devices": devices})
            self.bus.publish({"kind": "settings", "settings": self.settings_view()})
        return devices

    def local_devices_view(
        self, current_ua: str = "", client_device: str = ""
    ) -> dict[str, Any]:
        """The devices this nanoMuse is built on, without any account: this host (computer
        or local Android phone), and other devices talking to it directly. The hub's
        signed-in list sits alongside these."""
        import hashlib
        import os as _os
        import platform as _platform

        is_android_runtime = _os.environ.get("NANOMUSE_DEVICE", "").strip().lower() == "android"
        nicknames = self.device_nicknames()
        comp_id = "local:computer"
        comp_nick = nicknames.get(comp_id, "")
        if is_android_runtime:
            raw_model = _os.environ.get("NANOMUSE_DEVICE_MODEL", "").strip()
            host_model = (
                self._friendly_device_model(raw_model, f"Android {raw_model}".strip())
                if raw_model
                else "This phone"
            )
            host_kind = "phone"
            host_os = "Android"
            sdk = _os.environ.get("NANOMUSE_DEVICE_SDK", "").strip()
            host_version = f"Android {sdk}" if sdk else ""
            host_actions = ["task", "files", "open", "notify"]
        else:
            host_model = _platform.node() or "This computer"
            host_kind = "computer"
            host_os = _platform.system()
            host_version = _platform.release()
            host_actions = ["task", "shell", "files", "open", "screen"]
        computer = {
            "id": comp_id,
            "name": comp_nick or host_model,
            "nickname": comp_nick or None,
            "model": host_model,
            "kind": host_kind,
            "online": True,
            "this": True,
            "os": host_os,
            "version": host_version,
            "actions": host_actions,
            "local": True,
        }
        phones: dict[str, dict[str, Any]] = {}

        def add_phone(
            key: str,
            model_name: str,
            online: bool,
            actions: list[str],
            os: str,
            version: str = "",
            kind: str = "phone",
        ) -> None:
            full_id = f"local:{key}"
            nick = nicknames.get(full_id, "")
            row = phones.setdefault(
                key,
                {
                    "id": full_id,
                    "name": nick or model_name,
                    "nickname": nick or None,
                    "model": model_name,
                    "kind": kind,
                    "online": False,
                    "os": os,
                    "version": version,
                    "actions": [],
                    "local": True,
                },
            )
            row["online"] = row["online"] or online
            if version and not row.get("version"):
                row["version"] = version
            if model_name and row.get("model") in ("", "Android phone", "Phone"):
                row["model"] = model_name
                if not row.get("nickname"):
                    row["name"] = model_name
            for action in actions:
                if action not in row["actions"]:
                    row["actions"].append(action)

        phone_svc = getattr(self, "phone", None)
        link = phone_svc.status() if phone_svc is not None else {}
        if link.get("connected") and link.get("device"):
            if is_android_runtime:
                if "screen" not in computer["actions"]:
                    computer["actions"].append("screen")
            else:
                dev = link["device"]
                raw_name = str(dev.get("name") or "Phone")
                model_name = self._friendly_device_model(
                    raw_name,
                    raw_name,
                    int(dev.get("width") or 0),
                    int(dev.get("height") or 0),
                )
                add_phone(
                    f"phone:{raw_name}",
                    model_name,
                    True,
                    ["open", "screen"],
                    str(dev.get("os") or "Android"),
                )
        seen: set[str] = set()
        live_clients = getattr(self, "_live_clients", {})
        sources = [
            {
                "ua": c.get("ua") or "",
                "model": c.get("model") or "",
                "platform_version": c.get("platform_version") or "",
                "width": int(c.get("width") or 0),
                "height": int(c.get("height") or 0),
                "online": True,
            }
            for c in live_clients.values()
        ]
        push_subs = getattr(getattr(self, "push", None), "subscriptions", []) or []
        sources += [
            {
                "ua": s.get("ua") or "",
                "model": s.get("model") or "",
                "platform_version": "",
                "width": 0,
                "height": 0,
                "online": any(
                    (c.get("ua") or "") == (s.get("ua") or "")
                    for c in live_clients.values()
                ),
            }
            for s in push_subs
        ]
        for src in sources:
            ua = str(src.get("ua") or "")
            if not ua or ua in seen:
                continue
            seen.add(ua)
            lower = ua.lower()
            if is_android_runtime and (
                ua == current_ua
                or "; wv)" in lower
                or "nanomuseandroid" in lower
                or "android" in lower
            ):
                # When running natively on the phone itself, the loopback WebView and
                # background notification socket belong to this phone (`computer`), not an extra device.
                continue
            is_online = bool(src.get("online", True))
            if "iphone" in lower or "ipad" in lower:
                model_name = self._friendly_device_model(str(src.get("model") or ""), ua)
                add_phone(
                    f"phone:{hashlib.md5(ua.encode()).hexdigest()[:8]}",
                    model_name,
                    is_online,
                    ["notify"],
                    "iOS",
                    str(src.get("platform_version") or ""),
                    kind="tablet" if "ipad" in lower else "phone",
                )
            elif "android" in lower or str(src.get("platform") or "").lower() == "android":
                model_name = self._friendly_device_model(
                    str(src.get("model") or ""),
                    ua,
                    int(src.get("width") or 0),
                    int(src.get("height") or 0),
                )
                add_phone(
                    f"phone:{hashlib.md5(ua.encode()).hexdigest()[:8]}",
                    model_name,
                    is_online,
                    ["notify"],
                    "Android",
                    str(src.get("platform_version") or ""),
                    kind="tablet" if "mobile" not in lower else "phone",
                )
        paired_phones = [
            p
            for p in getattr(getattr(self, "sync", None), "peers", []) or []
            if str(p.get("kind") or "").lower() in ("phone", "tablet")
            or str(p.get("id") or "").lower().startswith("phone-")
        ]
        if paired_phones:
            paired_names = {
                str(p.get("name") or "").strip().lower()
                for p in paired_phones
                if p.get("name")
            }
            generic_names = {"phone", "android phone", "paired phone", "android", "localhost"}
            for k in list(phones.keys()):
                pname = str(phones[k].get("name") or "").strip().lower()
                pmodel = str(phones[k].get("model") or "").strip().lower()
                if pname in generic_names or pmodel in generic_names or pname in paired_names or pmodel in paired_names:
                    phones.pop(k, None)
        return {
            "computer": computer,
            "phones": sorted(phones.values(), key=lambda r: not r["online"]),
        }

    # ------------------------------------------------------------------ permissions
    # What each device's capabilities may do, one policy for the account (set from
    # either device's Settings): "always" (no prompt), "ask" (in the moment) or
    # "never". The computer's rows map onto the sentinel's tool lists; the phone's
    # ride the same settings push and are read by the phone app.
    CAPABILITIES: dict[str, list[tuple[str, str]]] = {
        "computer": [
            ("files_read", "Files: read"),
            ("files_write", "Files: write"),
            ("downloads", "Downloads"),
            ("device_settings", "Device settings"),
            ("microphone", "Microphone"),
            ("run_commands", "Run commands"),
            ("send_email", "Send email"),
            ("browse_web", "Use the web browser"),
            ("control_screen", "Control this computer's screen"),
            ("passwords", "Use saved passwords"),
            ("install_software", "Install software & system changes"),
            ("share_location", "Share location"),
            ("other_devices", "Act on your other devices"),
        ],
        "phone": [
            ("location", "Location"),
            ("camera", "Camera"),
            ("microphone", "Microphone"),
            ("calls", "Phone calls"),
            ("texts", "Text messages"),
            ("call_logs", "Call logs"),
            ("downloads", "Downloads"),
            ("device_settings", "Device settings"),
        ],
    }
    # which tools a computer capability stands for (phone capabilities go through
    # the phone app itself; there is no server-side tool for them)
    CAPABILITY_TOOLS: dict[str, list[str]] = {
        "files_read": ["files"],
        "files_write": ["files"],
        "downloads": ["files", "shell"],
        "device_settings": ["pc_control"],
        "run_commands": ["shell", "python_execute"],
        "send_email": ["send_email"],
        "browse_web": ["browser"],
        "control_screen": ["computer_act", "computer_task"],
        "passwords": ["passwords"],
        "install_software": ["system_admin"],
        "share_location": ["location"],
        "other_devices": [
            "delegate",
            "devices",
            "device_shell",
            "device_put",
            "device_get",
            "device_open",
            "device_files",
            "device_screen",
            "device_notify",
        ],
    }

    def _permissions_view(self) -> dict[str, dict[str, str]]:
        from nanomuse.config import load_app_settings

        stored = load_app_settings(self.data_dir).get("permissions")
        if not isinstance(stored, dict):
            stored = {}
        out: dict[str, dict[str, str]] = {}
        for device, caps in self.CAPABILITIES.items():
            rows = stored.get(device) if isinstance(stored.get(device), dict) else {}
            out[device] = {
                cap: str(rows.get(cap) or "ask")
                if str(rows.get(cap) or "ask") in ("always", "ask", "never")
                else "ask"
                for cap, _label in caps
            }
        return out

    def _apply_permission_policy(self) -> None:
        """The computer's capability rows land on the sentinel's tool lists.

        Several capabilities can stand for the same tool (Files: read, Files: write and Downloads
        all use ``files``), so each tool gets the strictest answer of the rows behind it: any
        "never" denies it; it is allowed without asking only when every row behind it says
        "always"; anything else returns it to the configured baseline (so "ask first" never drops
        a tool that the baseline already asks about, such as ``shell``)."""
        policy = self._permissions_view().get("computer", {})
        base = self._sentinel_baseline
        allow = set(base["always_allow"])
        ask = set(base["always_ask"])
        deny = set(base["deny"])
        modes: dict[str, set[str]] = {}
        for cap, tools in self.CAPABILITY_TOOLS.items():
            for tool in tools:
                modes.setdefault(tool, set()).add(policy.get(cap, "ask"))
        for tool, seen in modes.items():
            if "never" in seen:
                allow.discard(tool)
                ask.discard(tool)
                deny.add(tool)
            elif seen == {"always"}:
                ask.discard(tool)
                deny.discard(tool)
                allow.add(tool)
        self.settings.sentinel.always_allow_tools = sorted(allow)
        self.settings.sentinel.always_ask_tools = sorted(ask)
        self.settings.sentinel.deny_tools = sorted(deny)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Bring the services up, one logged step at a time. A step that stalls (an MCP
        server that never answers, a relay that cannot be reached through a broken proxy)
        is given up after its timeout with a warning, so that the app still opens."""
        if self._started:
            return
        started = time.monotonic()
        await self._step("tools", self.app.start(), 90)
        self._started = True
        self._recover_deliveries()
        await self._step("cloud account and hub", self.hub.start(), 30)
        self.sync.start()
        self._scheduler = asyncio.create_task(self._goal_scheduler(), name="goal-scheduler")
        self.starting = ""
        logger.info(
            "MuseService started ({} threads, {:.1f}s)",
            len(self.threads),
            time.monotonic() - started,
        )

    async def _step(self, name: str, work: Coroutine[Any, Any, Any], timeout: float) -> None:
        self.starting = name
        logger.info("starting: {}", name)
        try:
            await asyncio.wait_for(work, timeout)
        except TimeoutError:
            logger.warning(
                "starting: {} did not finish in {:.0f}s; going on without it", name, timeout
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("starting: {} failed: {}: {}", name, type(exc).__name__, exc)

    async def stop(self) -> None:
        loop = asyncio.get_running_loop()
        workers: list[asyncio.Task[None]] = []
        scheduler = self._scheduler
        if scheduler is not None and not scheduler.done() and scheduler.get_loop() is loop:
            # the scheduler may be mid-pass (reading a mailbox, writing a delivery, asking a
            # model for the Feed): it winds down with the workers, before the stores close
            scheduler.cancel()
            workers.append(scheduler)
        elif scheduler is not None:
            scheduler.cancel()
        self._scheduler = None
        for t in self.threads.values():
            work = t.worker
            if work is None or work.done() or work.get_loop() is not loop:
                # nothing to wait for, or a task of a loop that is already closed (an app
                # built twice in one process, as a test does): cancelling it means nothing
                # and waiting on it would raise across the loops
                continue
            work.cancel()
            workers.append(work)
        if workers:
            # cancelled is not finished: a worker still runs its own settling once the
            # CancelledError lands — the stopped notice, the timeline, the store — and doing
            # that after the flushes and the closes below is the "Exception ignored ... during
            # shutdown" of the log. Wait, but only so long: a worker wedged in a provider
            # response must not hold the exit.
            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True),
                    _SHUTDOWN_GRACE_S,
                )
            except TimeoutError:
                logger.warning(
                    "shutdown: {} worker(s) were still running after {:.0f}s; closing over their work",
                    len(workers),
                    _SHUTDOWN_GRACE_S,
                )
        for t in self.threads.values():
            t.timeline.flush()
        self.deliveries.close()
        await self.chatgpt.close()
        if self._started:
            await self.sync.stop()
            await self.coding.close()
            await self.avatar.close()
            await self.hub.stop()
            await self.connections.close()
            await self.app.close()
        self._started = False

    # ------------------------------------------------------------------ token / profile
    def _load_token(self) -> str:
        if not self.settings.server.auth:
            return ""
        if self.settings.server.token:
            return self.settings.server.token
        path = self.data_dir / "server_token"
        if path.exists():
            tok = path.read_text("utf-8").strip()
            if tok:
                return tok
        tok = secrets.token_urlsafe(18)
        path.write_text(tok, "utf-8")
        try:
            keep_private(path)
        except OSError:  # pragma: no cover
            pass
        return tok

    def _load_profile(self) -> Profile:
        path = self.data_dir / "profile.json"
        if path.exists():
            try:
                raw = json.loads(path.read_text("utf-8"))
                loaded = Profile.from_dict(raw)
                if isinstance(raw, dict) and str(raw.get("name") or "").strip().lower() == "nanomuse":
                    try:
                        path.write_text(
                            json.dumps(loaded.to_dict(), ensure_ascii=False, indent=1), "utf-8"
                        )
                    except OSError:
                        pass
                return loaded
            except (OSError, json.JSONDecodeError, ValueError):
                pass
        return Profile(name=self.settings.agent.name)

    def _save_profile(self) -> None:
        (self.data_dir / "profile.json").write_text(
            json.dumps(self.profile.to_dict(), ensure_ascii=False, indent=1), "utf-8"
        )

    def _apply_profile(self) -> None:
        a = self.settings.agent
        a.name = self.profile.name
        extra = ""
        if self.profile.user_name:
            extra += (
                f"\nThe user's name is {self.profile.user_name}; address them by it when natural."
            )
        if self.profile.tagline.strip():
            extra += f"\nYour tagline, the line under your name in the app: {self.profile.tagline.strip()!r}"
        # each choice is its own paragraph, so a change to one leaves the others alone
        if self.profile.tone in TONES:
            extra += "\n\n" + TONES[self.profile.tone]
        if self.profile.communication in COMMUNICATION:
            extra += "\n\n" + COMMUNICATION[self.profile.communication]
        if self.profile.style.strip():
            extra += (
                "\n\nYour personality / style, in the user's own words: "
                + self.profile.style.strip()
            )
        a.instructions = (self._base_instructions.rstrip() + extra).strip()

    def update_profile(self, data: dict[str, Any]) -> Profile:
        data = {k: v for k, v in data.items() if v is not None}
        if "proactive" in data and "proactivity" not in data:
            # the old switch: off, or back to the default level
            data["proactivity"] = (
                "off"
                if not data["proactive"]
                else (self.profile.proactivity if self.profile.proactive else "default")
            )
        data.pop("proactive", None)
        if "updated_at" not in data:
            data["updated_at"] = time.time()
        merged = {**self.profile.to_dict(), **data}
        merged.pop("proactive", None)
        before = (
            self.profile.proactivity,
            self.profile.goal_interval_minutes,
            self.profile.quiet_hours,
        )
        look_before = (
            self.profile.name,
            self.profile.avatar,
            self.profile.emoji,
            self.profile.color,
            self.profile.avatar_bg_mode,
            self.profile.avatar_bg_color,
            self.profile.avatar_bg_image,
        )
        self.profile = Profile.from_dict(merged)
        self.profile.auto_factor = self.auto_proactivity.factor()
        if before != (
            self.profile.proactivity,
            self.profile.goal_interval_minutes,
            self.profile.quiet_hours,
        ):
            self.schedule_next_pass()
        self._save_profile()
        self._apply_profile()
        self.bus.publish({"kind": "profile", "profile": self.profile.to_dict()})
        if look_before != (
            self.profile.name,
            self.profile.avatar,
            self.profile.emoji,
            self.profile.color,
            self.profile.avatar_bg_mode,
            self.profile.avatar_bg_color,
            self.profile.avatar_bg_image,
        ):
            # the account's other devices wear the same name and face
            self.hub.profile.changed()
        return self.profile

    # ------------------------------------------------------------------ first conversation (C4)
    def firstrun_lang(self, lang: str = "") -> str:
        """The language of the opening: the one asked for, else the one Start was pressed in,
        else the agent's reply language."""
        return lang.strip() or self.firstrun.state.lang or self.ui_language()

    def firstrun_view(self, lang: str = "", intro: bool = False) -> dict[str, Any]:
        """``GET /api/firstrun`` and the ``firstrun`` frame: the state and the chips; with
        ``intro``, the three lines the app speaks first, in ``lang``."""
        view = self.firstrun.view(self.firstrun_lang(lang))
        if intro:
            state = self.firstrun.state
            known = state.user_address if state.address_from_setup else None
            view["intro"] = firstrun.intro_lines(self.firstrun_lang(lang), known)
        return view

    # ------------------------------------------------------------------ chat titles
    # A chat used to keep the name it was born with ("New chat", "Side chat") until the
    # person renamed it by hand, so the list filled up with identical rows. Now the first
    # turn of a chat that still wears a placeholder name asks the model for a short one.
    PLACEHOLDER_TITLES = frozenset(
        {
            "",
            "new chat",
            "side chat",
            "main chat",
            "chat",
            "untitled",
            "新对话",
            "侧边对话",
            "主对话",
        }
    )

    def _is_placeholder_title(self, title: str) -> bool:
        return (title or "").strip().lower() in self.PLACEHOLDER_TITLES

    def _first_user_text(self, thread: Thread) -> str:
        for ev in thread.timeline.events:
            if ev.get("type") == "user":
                text = str(ev.get("text") or "").strip()
                if text:
                    return text
        return ""

    def _title_from_text(self, text: str, limit: int = 48) -> str:
        """The fallback when no model answers: the first words of what was asked."""
        flat = " ".join(text.split())
        for sep in (". ", "? ", "! ", "\n"):
            head = flat.split(sep, 1)[0]
            if 3 <= len(head) <= limit:
                flat = head
                break
        flat = flat.strip(" .,:;!?-")
        if len(flat) > limit:
            flat = flat[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
        return flat

    def _maybe_auto_title(self, thread: Thread) -> None:
        """Name a chat from its first message, once, when it still has a placeholder name.

        Runs after the turn (never inside it), on the model the turn used, and falls back
        to the first words of the request when the model cannot be reached or answers
        something unusable. A name the person picked is never touched, and the main chat
        keeps its own name: it is the one chat that is never a placeholder."""
        if thread.id == MAIN_THREAD:
            return
        if not self._is_placeholder_title(thread.title):
            return
        if thread.id in self._title_pending:
            return
        text = self._first_user_text(thread)
        if not text:
            return
        self._title_pending.add(thread.id)
        spawn(self._auto_title(thread.id, text), "naming a chat")

    async def _auto_title(self, thread_id: str, text: str) -> None:
        title = ""
        try:
            title = await self._ask_for_title(text)
        except Exception as exc:  # noqa: BLE001 - a name is a nicety, never a failure
            logger.warning("auto title failed for {}: {}", thread_id, exc)
        finally:
            self._title_pending.discard(thread_id)
        thread = self.threads.get(thread_id)
        if thread is None or not self._is_placeholder_title(thread.title):
            return
        title = title or self._title_from_text(text)
        if not title:
            return
        self.rename_thread(thread_id, title[:200])

    async def _ask_for_title(self, text: str) -> str:
        """One short call: the model's name for this chat, cleaned to a single line."""
        llm = self.app.llm
        if llm is None:
            return ""
        ask = (
            "Name this conversation in 3 to 6 words, the way a person would label it in a "
            "chat list. No quotes, no period, no 'Chat about'. Answer with the name only.\n\n"
            f"First message:\n{text[:1500]}"
        )
        response = await llm.ask(
            [Message(role=Role.USER, content=ask)], max_tokens=40
        )
        raw = (response.content or "").strip()
        raw = raw.splitlines()[0] if raw else ""
        raw = raw.strip().strip('"\'`*').strip()
        for prefix in ("Title:", "title:", "Name:", "name:"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :].strip()
        if len(raw) > 60:
            raw = self._title_from_text(raw, 60)
        return raw if len(raw) >= 3 else ""

    def _publish_firstrun(self) -> None:
        self.bus.publish({"kind": "firstrun", "firstrun": self.firstrun_view()})

    def _firstrun_addendum_for(self, thread_id: str):  # noqa: ANN202 - a closure for the agent
        def addendum() -> str:
            state = self.firstrun.state
            if not firstrun.bound_to(state, thread_id):
                return ""
            return firstrun.prompt_addendum(state, self.firstrun_lang(), self.profile.name) or ""

        return addendum

    def start_first_conversation(self, lang: str = "") -> dict[str, Any]:
        """Start was pressed: the main chat is bound as the first conversation and the
        setup counts as finished (what ``POST /api/onboarded`` records). The app speaks
        first there, from the browser, at no cost in tokens; the model only hears from the
        person. A main chat that already holds a conversation (synced from another device
        of the account, or an older install) is not begun again: no opening, no name to
        choose — the thread simply continues."""
        self.connections.set_onboarded(True)
        state = self.firstrun.state
        main = self.threads.get(MAIN_THREAD)
        underway = main is not None and any(
            e.get("type") in ("user", "assistant") for e in main.timeline.events
        )
        if state.phase != "done":
            base = replace(state, phase="done" if underway else "none")
        else:
            base = state
        lang = lang.strip().lower()
        lang = "zh" if lang.startswith("zh") else ("en" if lang else "")
        started = firstrun.start_conversation(base, MAIN_THREAD, lang)
        # setup already asked what to call the person: do not ask a second time
        known = firstrun.begin_with_known_name(started, self.profile.user_name)
        self.firstrun.save(known)
        if known is not started:
            self._remember_address(known.user_address or "")
        self._publish_firstrun()
        if underway:
            self.first_feed_day()
        return self.firstrun_view(intro=True)

    def pick_first_name(self, name: str) -> dict[str, Any]:
        """A chip was picked on the chooser: the name is on the header before the model has
        even replied; the web then sends the name as the person's message, and the ``named``
        addendum tells the model what happened. :class:`LookupError` when no pick is due."""
        nxt = firstrun.pick_name(self.firstrun.state, name)
        if nxt is None:
            raise LookupError("no name is being chosen right now")
        self.firstrun.save(nxt)
        self.update_profile({"name": name})
        self._publish_firstrun()
        return self.firstrun_view()

    def dismiss_first_chooser(self) -> dict[str, Any]:
        """The person moved on to something the app handles itself: the chooser goes."""
        state = self.firstrun.state
        nxt = firstrun.dismiss_chooser(state)
        if nxt is not state:
            self.firstrun.save(nxt)
            self._publish_firstrun()
            self.first_feed_day()
        return self.firstrun_view()

    def _firstrun_turn_ended(self, thread_id: str, final: str) -> None:
        """The model's reply in the first conversation ended: read its block, move the
        phase, keep what it said — the address on the profile and as the memory line the
        phones write, the name on the profile."""
        state = self.firstrun.state
        if not firstrun.bound_to(state, thread_id) or not firstrun.running(state):
            return
        outcome = firstrun.after_turn(state, final)
        if outcome.state is state:
            return
        self.firstrun.save(outcome.state)
        if outcome.address_given:
            self.update_profile({"user_name": outcome.address_given})
            self._remember_address(outcome.address_given)
        if outcome.named:
            self.update_profile({"name": outcome.named})
        self._publish_firstrun()
        if firstrun.conversation_over(outcome.state):
            # the feed's first day is written once the first conversation is over
            self.first_feed_day()

    def _remember_address(self, address: str) -> None:
        """ "Call them: X" under what is known about the user: one memory, replaced when it
        changes (the phones keep the line under ``## About the user``; the runtime keeps
        memories as items, so the line is one item)."""
        store = self.app.memory
        if store is None:
            return
        line = firstrun.address_line(address)
        try:
            for item in store.all():
                if item.content.lower().startswith(firstrun.ADDRESS_PREFIX.lower()):
                    store.forget(item.id)
            store.add(line, category="profile", source="agent")
        except Exception as exc:  # noqa: BLE001 - the profile already holds the name
            logger.warning("could not remember the address: {}", exc)
        self.bus.publish({"kind": "memory"})

    # ------------------------------------------------------------------ threads
    def _load_threads(self) -> None:
        index = self.threads_dir / "index.json"
        metas: list[dict[str, Any]] = []
        if index.exists():
            try:
                metas = json.loads(index.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                metas = []
        if not any(m.get("id") == MAIN_THREAD for m in metas):
            metas.insert(0, {"id": MAIN_THREAD, "title": "Main chat", "created_at": now_iso()})
        for m in metas:
            tid = str(m.get("id") or "")
            if not _THREAD_ID_RE.fullmatch(tid):
                # an id the app would refuse now: drop the line rather than refuse to start
                logger.warning("threads index: ignoring the entry with the id {!r}", tid)
                continue
            thread = self._make_thread(
                tid, m.get("title", tid), m.get("created_at"), m.get("updated_at")
            )
            # repair what earlier imports left: a chat's dates come from its messages, so
            # the list is ordered by when people last spoke, not by when a file arrived
            thread.refresh_times_from_events()
            if m.get("device"):
                thread.device = str(m["device"])
                thread.device_name = str(m.get("device_name") or m["device"])
            if isinstance(m.get("remote_from"), dict):
                thread.remote_from = dict(m["remote_from"])
            if m.get("origin_device"):
                thread.origin_device = str(m["origin_device"])
                thread.origin_device_name = str(m.get("origin_device_name") or "")
            if m.get("main_of"):
                thread.main_of = str(m["main_of"])
        self._seed_event_totals()
        self._save_index()

    def _seed_event_totals(self) -> None:
        """Chats imported before the counter existed were trimmed to the events they keep,
        so their file cannot say how many they had. The search index still holds every
        imported message: take the count from there once, so a 5,045-message chat is not
        shown as 2,000."""
        try:
            conn = sqlite3.connect(self.data_dir / "personal.db", timeout=10)
            try:
                rows = conn.execute(
                    "SELECT thread_id, COUNT(*) FROM messages_fts GROUP BY thread_id"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return
        for tid, count in rows:
            thread = self.threads.get(str(tid))
            if thread is not None and int(count) > thread.timeline.total:
                thread.timeline.total = int(count)
                thread.timeline.save()

    # ------------------------------------------------------------------ trash
    def _trash_retention_days(self) -> int:
        return self._effective_trash_days()

    def _trash_file(self, thread_id: str, suffix: str) -> Path:
        if not _THREAD_ID_RE.fullmatch(str(thread_id)):
            raise ValueError(f"invalid thread id: {thread_id}")
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        path = (self.trash_dir / f"{thread_id}{suffix}").resolve()
        if path.parent != self.trash_dir.resolve():
            raise ValueError(f"invalid thread id: {thread_id}")
        return path

    def _save_trash(self) -> None:
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        (self.trash_dir / "index.json").write_text(
            json.dumps(self.trash, ensure_ascii=False, indent=1), "utf-8"
        )

    def _publish_trash(self) -> None:
        if getattr(self, "bus", None):
            items = self.list_trash()
            self.bus.publish({"kind": "trash", "trash": items, "count": len(items)})

    def _load_trash(self) -> None:
        index = self.trash_dir / "index.json"
        if not index.exists():
            self.trash = []
            return
        try:
            raw = json.loads(index.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = []
        items: list[dict[str, Any]] = []
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            tid = str(entry.get("id") or "")
            if not _THREAD_ID_RE.fullmatch(tid):
                continue
            items.append(entry)
        self.trash = items
        if self._prune_expired_trash():
            self._save_trash()

    def _prune_expired_trash(self) -> bool:
        now = datetime.now(UTC)
        kept: list[dict[str, Any]] = []
        changed = False
        for entry in self.trash:
            tid = str(entry.get("id") or "")
            exp_raw = str(entry.get("expires_at") or "")
            expired = False
            if exp_raw:
                try:
                    exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    expired = exp_dt <= now
                except ValueError:
                    expired = False
            if expired:
                changed = True
                for suf in (".json", ".session.json"):
                    try:
                        p = self._trash_file(tid, suf)
                        if p.exists():
                            p.unlink()
                    except (OSError, ValueError):
                        pass
            else:
                kept.append(entry)
        self.trash = kept
        return changed

    @staticmethod
    def _visible_message_count(events: list[dict[str, Any]]) -> int:
        return sum(1 for e in events if e.get("type") in ("user", "assistant"))

    def list_trash(self) -> list[dict[str, Any]]:
        if self._prune_expired_trash():
            self._save_trash()
        now = datetime.now(UTC)
        out: list[dict[str, Any]] = []
        for entry in self.trash:
            item = dict(entry)
            exp_raw = str(item.get("expires_at") or "")
            days_left = self._trash_retention_days()
            if exp_raw:
                try:
                    exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    days_left = max(0, (exp_dt - now).days)
                except ValueError:
                    pass
            item["days_left"] = days_left
            out.append(item)
        out.sort(key=lambda e: str(e.get("deleted_at") or ""), reverse=True)
        return out

    def _move_thread_to_trash(
        self, thread: Thread, *, trash_id: str | None = None, was_main: bool = False
    ) -> dict[str, Any]:
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        tid = trash_id or thread.id
        thread.timeline.flush()
        thread.timeline.save()
        thread.agent._save_session()
        now = datetime.now(UTC)
        days = self._trash_retention_days()
        expires = (now + timedelta(days=days)).isoformat().replace("+00:00", "Z")
        deleted = now.isoformat().replace("+00:00", "Z")
        if trash_id is None:
            for src, suffix in (
                (thread.timeline.path, ".json"),
                (thread.agent.session_file, ".session.json"),
            ):
                if src is None:
                    continue
                src_path = Path(src)
                dst_path = self._trash_file(tid, suffix)
                if dst_path.exists():
                    dst_path.unlink()
                if src_path.exists():
                    src_path.replace(dst_path)
        else:
            # Copy snapshot of main chat before clear_thread empties it
            ev_dst = self._trash_file(tid, ".json")
            ev_copy = [dict(ev, thread=tid) for ev in thread.timeline.events]
            ev_dst.write_text(
                json.dumps({"thread": tid, "events": ev_copy}, ensure_ascii=False, indent=1),
                "utf-8",
            )
            if thread.agent.session_file and Path(thread.agent.session_file).exists():
                sess_dst = self._trash_file(tid, ".session.json")
                sess_dst.write_text(
                    Path(thread.agent.session_file).read_text("utf-8"), "utf-8"
                )
        entry: dict[str, Any] = {
            "id": tid,
            "title": thread.title,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
            "deleted_at": deleted,
            "expires_at": expires,
            "retention_days": days,
            "message_count": self._visible_message_count(thread.timeline.events),
            "was_main": was_main,
        }
        if thread.device:
            entry["device"] = thread.device
            entry["device_name"] = thread.device_name
        if thread.remote_from:
            entry["remote_from"] = dict(thread.remote_from)
        if thread.origin_device:
            entry["origin_device"] = thread.origin_device
            entry["origin_device_name"] = thread.origin_device_name
        if thread.main_of:
            entry["main_of"] = thread.main_of
        self.trash = [e for e in self.trash if e.get("id") != tid]
        self.trash.append(entry)
        self._save_trash()
        return entry

    def restore_thread(self, trash_id: str) -> Thread | None:
        entry = next((e for e in self.trash if e.get("id") == trash_id), None)
        if entry is None:
            return None
        target_id = trash_id
        if target_id == MAIN_THREAD or target_id in self.threads:
            target_id = "t_" + uuid.uuid4().hex[:8]
        for suffix in (".json", ".session.json"):
            src = self._trash_file(trash_id, suffix)
            dst = self._thread_file(target_id, suffix)
            if dst.exists():
                dst.unlink()
            if src.exists():
                src.replace(dst)
        thread = self._make_thread(
            target_id,
            str(entry.get("title") or "Restored chat"),
            entry.get("created_at"),
            now_iso(),
        )
        if target_id != trash_id:
            for ev in thread.timeline.events:
                ev["thread"] = target_id
            thread.timeline.save()
            thread.timeline.flush()
        if entry.get("device"):
            thread.device = str(entry["device"])
            thread.device_name = str(entry.get("device_name") or entry["device"])
        if isinstance(entry.get("remote_from"), dict):
            thread.remote_from = dict(entry["remote_from"])
        if entry.get("origin_device"):
            thread.origin_device = str(entry["origin_device"])
            thread.origin_device_name = str(entry.get("origin_device_name") or "")
        if entry.get("main_of"):
            thread.main_of = str(entry["main_of"])
        self.trash = [e for e in self.trash if e.get("id") != trash_id]
        self._save_trash()
        self._save_index()
        for ev in thread.timeline.events:
            self._index_event(ev)
        self.bus.publish({"kind": "thread", "thread": thread.meta()})
        self._publish_trash()
        self.sync.thread_changed(thread)
        self.sync.turn_finished(thread)
        return thread

    def purge_trash_item(self, trash_id: str) -> bool:
        entry = next((e for e in self.trash if e.get("id") == trash_id), None)
        if entry is None:
            return False
        for suffix in (".json", ".session.json"):
            try:
                p = self._trash_file(trash_id, suffix)
                if p.exists():
                    p.unlink()
            except (OSError, ValueError):
                pass
        self._unindex_thread(trash_id)
        self.trash = [e for e in self.trash if e.get("id") != trash_id]
        self._save_trash()
        self._publish_trash()
        return True

    def empty_trash(self) -> int:
        count = len(self.trash)
        for entry in list(self.trash):
            tid = str(entry.get("id") or "")
            for suffix in (".json", ".session.json"):
                try:
                    p = self._trash_file(tid, suffix)
                    if p.exists():
                        p.unlink()
                except (OSError, ValueError):
                    pass
            if tid:
                self._unindex_thread(tid)
        self.trash = []
        self._save_trash()
        self._publish_trash()
        return count

    def _save_index(self) -> None:
        metas = []
        for t in self.threads.values():
            m: dict[str, Any] = {
                "id": t.id,
                "title": t.title,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            if t.device:
                m["device"] = t.device
                m["device_name"] = t.device_name
            if t.remote_from:
                m["remote_from"] = t.remote_from
            if t.origin_device:
                m["origin_device"] = t.origin_device
                m["origin_device_name"] = t.origin_device_name
            if t.main_of:
                m["main_of"] = t.main_of
            metas.append(m)
        (self.threads_dir / "index.json").write_text(
            json.dumps(metas, ensure_ascii=False, indent=1), "utf-8"
        )

    def _thread_file(self, thread_id: str, suffix: str) -> Path:
        """The file a thread id names: checked, not escaped, because the id is something a
        client sends and it would otherwise choose where the thread's data lives."""
        if not _THREAD_ID_RE.fullmatch(str(thread_id)):
            raise ValueError(f"invalid thread id: {thread_id}")
        path = (self.threads_dir / f"{thread_id}{suffix}").resolve()
        if path.parent != self.threads_dir.resolve():
            raise ValueError(f"invalid thread id: {thread_id}")
        return path

    def _make_thread(
        self,
        thread_id: str,
        title: str,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> Thread:
        timeline = Timeline(thread_id, self._thread_file(thread_id, ".json"))
        # Cards that were waiting for an answer when the server stopped can't be answered
        # any more: the agent run behind them is gone.
        stale = [
            ev
            for ev in timeline.events
            if ev.get("type") in ("approval", "question") and ev.get("status") == "pending"
        ]
        for ev in stale:
            timeline.update(ev["id"], status="expired")
        running = [
            ev
            for ev in timeline.events
            if ev.get("type") == "tool" and ev.get("status") == "running"
        ]
        for ev in running:
            timeline.update(ev["id"], status="error", output="interrupted by a restart")
        for ev in timeline.events:
            if ev.get("type") in ("browser", "hands") and ev.get("status") == "live":
                timeline.update(ev["id"], status="done")
            # a hold lives in memory: after a restart nothing waits on it any more
            if ev.get("type") == "hold" and ev.get("status") == "on":
                timeline.update(ev["id"], status="off", stale=True)
        session_file = self._thread_file(thread_id, ".session.json")
        agent = MuseAgent(
            settings=self.settings,
            llm=self.app.llm,
            tools=self.app.tools,
            sentinel=self.app.sentinel,
            ui=self.ui,
            audit=self.app.audit,
            memory=self.app.memory,
            goals=self.app.goals,
            calendar=self.app.calendar,
            contacts=self.app.contacts,
            skills=self.app.skills,
            session_file=session_file,
            conversation_id=thread_id,
        )
        if session_file.exists():
            try:
                agent.load_session(session_file)
            except (OSError, ValueError) as exc:
                logger.warning("could not restore session for {}: {}", thread_id, exc)
        thread = Thread(
            id=thread_id,
            title=title,
            created_at=created_at or now_iso(),
            updated_at=updated_at or created_at or now_iso(),
            timeline=timeline,
            agent=agent,
        )
        self.threads[thread_id] = thread
        return thread

    def timeline(self, thread_id: str) -> Timeline:
        thread = self.threads.get(thread_id)
        if thread is None:
            thread = self._make_thread(thread_id, thread_id)
            self._save_index()
        return thread.timeline

    def create_thread(self, title: str = "") -> Thread:
        tid = "t_" + uuid.uuid4().hex[:8]
        thread = self._make_thread(tid, title.strip() or "Side chat")
        self._save_index()
        self.bus.publish({"kind": "thread", "thread": thread.meta()})
        self.sync.thread_changed(thread)
        return thread

    def ui_language(self) -> str:
        """``zh`` when the agent is told to answer in Chinese, else ``en`` — the two languages
        the runtime's own sentences come in."""
        language = str(self.settings.agent.language or "").lower()
        return "zh" if language.startswith(("中文", "zh", "chinese", "简体", "繁體")) else "en"

    def visible_threads(self) -> list[Thread]:
        """The conversation list (contract C10): signed in, the account's conversations and
        the ones no account has; signed out, every local one. Hidden ones stay on disk.

        Newest activity first, the way a chat list is read: an imported archive starts where
        its first message was, so it lands between the chats it belongs with instead of at
        the end in file order. Threads that only hold notices (documents an older build
        turned into empty chats) come after every conversation somebody spoke in."""
        visible = [t for t in self.threads.values() if self.sync.visible(t)]
        newest = {id(t): t.last_message_ts() for t in visible}
        return sorted(
            visible,
            key=lambda t: (
                1 if newest[id(t)] else 0,
                newest[id(t)] or t.updated_at or "",
                t.created_at or "",
            ),
            reverse=True,
        )

    def rehome_main(self, owner: str, restore: str | None, keep: bool = True) -> Thread | None:
        """A different account signed in (C10): the main chat so far is put aside under a new
        id, marked as ``owner``'s (``keep``; dropped when it is empty and nobody's), and
        ``restore`` — the thread holding the new account's main chat, when it has one — takes
        the ``main`` id back; otherwise a fresh main chat begins. Returns the thread the old
        main chat became, or None when it was dropped."""
        main = self.threads.get(MAIN_THREAD)
        if main is None:
            return None
        if main.busy:
            self.stop_thread(MAIN_THREAD)
        archived = self._move_thread(main, "t_" + uuid.uuid4().hex[:8]) if keep else None
        if archived is not None:
            archived.main_of = owner
        else:
            self._drop_thread(main)
        if restore and restore in self.threads:
            kept = self.threads[restore]
            fresh = self._move_thread(kept, MAIN_THREAD)
            fresh.main_of = ""
            self.bus.publish({"kind": "thread_deleted", "thread": restore})
        else:
            fresh = self._make_thread(MAIN_THREAD, "Main chat")
        self._save_index()
        # the list changed shape: the apps reload it from the snapshot
        if archived is not None:
            self.bus.publish({"kind": "thread", "thread": archived.meta()})
        self.bus.publish({"kind": "thread_cleared", "thread": MAIN_THREAD})
        self.bus.publish({"kind": "thread", "thread": fresh.meta()})
        return archived

    def _drop_thread(self, thread: Thread) -> None:
        """Forget a thread and its files without the sync tombstone (the main chat put aside)."""
        self.threads.pop(thread.id, None)
        if thread.worker:
            thread.worker.cancel()
        for p in (thread.timeline.path, thread.agent.session_file):
            if p and Path(p).exists():
                Path(p).unlink()
        self.app.holds.clear_thread(thread.id)
        self.app.sentinel.end_conversation(thread.id)

    def _move_thread(self, thread: Thread, new_id: str) -> Thread:
        """The same conversation under another id: its timeline and the agent's transcript
        move file by file and the thread is rebuilt from them (the events say the new id)."""
        self.threads.pop(thread.id, None)
        if thread.worker:
            thread.worker.cancel()
        for ev in thread.timeline.events:
            ev["thread"] = new_id
        thread.timeline.save()
        thread.timeline.flush()
        thread.agent._save_session()
        self.app.holds.clear_thread(thread.id)
        self.app.sentinel.end_conversation(thread.id)
        pairs = (
            (thread.timeline.path, self._thread_file(new_id, ".json")),
            (thread.agent.session_file, self._thread_file(new_id, ".session.json")),
        )
        # a write still on its way to disk lands under the new id, not the old one
        thread.timeline.path = pairs[0][1]
        for src, dst in pairs:
            if src is None:
                continue
            src_path = Path(src)
            if dst.exists():
                dst.unlink()
            if src_path.exists():
                src_path.replace(dst)
        moved = self._make_thread(new_id, thread.title, thread.created_at, thread.updated_at)
        moved.device, moved.device_name = thread.device, thread.device_name
        moved.remote_from = thread.remote_from
        moved.origin_device, moved.origin_device_name = (
            thread.origin_device,
            thread.origin_device_name,
        )
        moved.main_of = thread.main_of
        self.ui.last_assistant_text.pop(thread.id, None)
        return moved

    def rename_thread(self, thread_id: str, title: str) -> Thread | None:
        thread = self.threads.get(thread_id)
        if thread is None:
            return None
        thread.title = title.strip() or thread.title
        thread.updated_at = now_iso()
        self._save_index()
        self.bus.publish({"kind": "thread", "thread": thread.meta()})
        self.sync.thread_changed(thread)
        return thread

    def delete_thread(self, thread_id: str) -> bool:
        if thread_id == MAIN_THREAD:
            return False
        thread = self.threads.pop(thread_id, None)
        if thread is None:
            return False
        if thread.worker:
            thread.worker.cancel()
        self._move_thread_to_trash(thread)
        self._unindex_thread(thread_id)
        self.app.holds.clear_thread(thread_id)
        self.app.sentinel.end_conversation(thread_id)
        self._save_index()
        self.bus.publish({"kind": "thread_deleted", "thread": thread_id})
        self._publish_trash()
        self.sync.thread_deleted(thread_id)
        return True

    def stop_thread(self, thread_id: str) -> bool:
        """The stop button: end the run in progress and drop what was queued behind it.
        The conversation stays; a pending approval or question closes unanswered."""
        thread = self.threads.get(thread_id)
        if thread is not None and thread.device and thread.busy:
            spawn(self.hub.stop_remote(thread), "stopping the remote run")
            return True
        if thread is None or not thread.busy or thread.worker is None or thread.worker.done():
            return False
        while not thread.inbox.empty():
            thread.inbox.get_nowait()
        for ev in list(thread.timeline.tail(50)):
            if ev.get("type") in ("approval", "question") and ev.get("status") == "pending":
                self.ui.patch(thread_id, ev["id"], status="expired")
        thread.stopping = True
        thread.worker.cancel()
        return True

    def _settle_stopped(self, thread: Thread) -> None:
        """After a cancelled run: the transcript must not end on a tool call the model never
        saw answered, and the agent goes back to idle so the next message runs cleanly."""
        agent = thread.agent
        msgs = agent.messages
        while msgs and msgs[-1].role == Role.TOOL:
            msgs.pop()
        if msgs and msgs[-1].role == Role.ASSISTANT and msgs[-1].tool_calls:
            for tc in msgs[-1].tool_calls:
                msgs.append(
                    Message.tool("Stopped by the user before this ran.", tc.id, tc.function.name)
                )
        agent.state = agent.state.__class__.IDLE
        agent._save_session()
        thread.stopping = False
        self.ui.emit(
            {"type": "notice", "level": "info", "text": STOPPED_NOTICE, "thread": thread.id}
        )
        # the stop itself; the person's reason, if they give one, follows as its own note
        telemetry.event(
            "turn.ended",
            {"ended": "aborted", "turn_no": agent.turns},
            turn=agent.last_call,
            conversation=thread.id,
        )

    # ------------------------------------------------------------------ feedback
    FEEDBACK_VOTES = ("up", "down", "")
    FEEDBACK_REASONS = ("wrong", "unhelpful", "unsafe", "slow", "other", "")
    STOP_REASONS = ("wrong", "slow", "changed_mind", "done", "other", "skipped")

    def feedback(
        self, thread_id: str, event_id: str, vote: str, reason: str = ""
    ) -> dict[str, Any]:
        """A thumb up or down on a reply (``vote`` "" takes it back), with a word on why
        when the person gave one. The bubble keeps the mark; the account's record hears
        ``turn.feedback`` for the turn the reply came from, when the runtime is signed in.
        The reply's words stay here."""
        thread = self.threads.get(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        if vote not in self.FEEDBACK_VOTES:
            raise ValueError("vote is up, down or empty")
        if reason not in self.FEEDBACK_REASONS:
            raise ValueError("reason is one of " + ", ".join(r for r in self.FEEDBACK_REASONS if r))
        ev = thread.timeline.get(event_id)
        if ev is None or ev.get("type") != "assistant":
            raise KeyError(event_id)
        self.ui.patch(thread_id, event_id, feedback=vote)
        turn = (str(ev.get("turn") or ""), str(ev.get("turn_source") or ""))
        if not turn[0]:
            turn = thread.agent.last_call
        data: dict[str, Any] = {"vote": vote or "none", "turn_no": thread.agent.turns}
        if reason:
            data["reason"] = reason
        telemetry.event("turn.feedback", data, turn=turn, conversation=thread_id)
        return ev

    def stop_reason(self, thread_id: str, reason: str) -> None:
        """Why the person pressed Stop, in one of a few words (``skipped`` when they did not
        say); ``turn.stopped`` in the record for the run that was stopped."""
        thread = self.threads.get(thread_id)
        if thread is None:
            raise KeyError(thread_id)
        if reason not in self.STOP_REASONS:
            raise ValueError("reason is one of " + ", ".join(self.STOP_REASONS))
        telemetry.event(
            "turn.stopped",
            {"reason": reason, "turn_no": thread.agent.turns},
            turn=thread.agent.last_call,
            conversation=thread_id,
        )

    def clear_thread(self, thread_id: str) -> bool:
        thread = self.threads.get(thread_id)
        if thread is None or thread.busy:
            return False
        if self._visible_message_count(thread.timeline.events) > 0:
            snap_id = "t_" + uuid.uuid4().hex[:8]
            self._move_thread_to_trash(
                thread, trash_id=snap_id, was_main=(thread_id == MAIN_THREAD)
            )
        thread.timeline.clear()
        thread.agent.reset()
        thread.agent._save_session()
        self._unindex_thread(thread_id)
        self.app.holds.clear_thread(thread_id)
        self.app.sentinel.end_conversation(thread_id)
        self.bus.publish({"kind": "thread_cleared", "thread": thread_id})
        self._publish_trash()
        return True

    def delete_event(self, thread_id: str, event_id: str) -> None:
        """One bubble out of the thread, at the person's request.

        A user or assistant bubble's words usually live in the model's session too: they
        leave it together with the bubble, so what the model reads is what the thread
        shows and a retried ask does not trip over a reply that was taken away. Tool
        chips, notices and step events exist in the timeline only; they just drop."""
        thread = self.threads.get(thread_id)
        if thread is None:
            raise KeyError(event_id)
        if thread.busy:
            raise RuntimeError("thread is busy")
        ev = thread.timeline.get(event_id)
        if ev is None:
            raise KeyError(event_id)
        removed = dict(ev)
        thread.timeline.remove(event_id)
        thread.timeline.flush()
        role = str(removed.get("type") or "")
        text = str(removed.get("text") or "")
        attachments_count = len(removed.get("attachments") or [])
        if role in ("user", "assistant"):
            thread.agent.forget(role, text, attachments_count=attachments_count)
            self._unindex_event(thread_id, removed)
        thread.updated_at = now_iso()
        self._save_index()
        self.bus.publish({"kind": "event_removed", "thread": thread_id, "id": event_id})
        self.bus.publish({"kind": "thread", "thread": thread.meta()})
        if hasattr(self.sync, "event_deleted"):
            self.sync.event_deleted(thread, removed)

    # ------------------------------------------------------------------ messaging
    def send(
        self,
        thread_id: str,
        text: str,
        source: str = "user",
        label: str = "",
        files: list[str] | None = None,
        language: str = "",
        via: str = "",
        delivery: str = "",
        event_id: str | None = None,
        model: str = "",
    ) -> dict[str, Any]:
        """Queue a message for a thread. Returns the timeline event that was created.

        ``files`` are workspace paths of attachments (uploaded first with ``save_upload``);
        a message may be attachments alone. ``language`` is the BCP-47 tag of the client's
        screens when the client said; the agent answers in it (see MuseAgent.run).
        ``via="cloud"`` is *Use nanoMuse Cloud this time*: this one turn runs on the
        account's recommended chat model (see :meth:`cloud_once`); the next is back on
        the configured model. A CloudError when that is not possible.
        ``model`` is the composer pill's pick for this one message (``flash``, ``pro``,
        ``local``, ``qwen``, ``gemini``); it wins over any switch wording in the text."""
        text = text.strip()
        language = language.strip()
        attachments = [self.attachment(path) for path in files or []]
        if not text and not attachments:
            raise ValueError("empty message")
        if source == "user":
            self._note_reply()  # writing back is what tells "auto" that the check-ins are wanted
        once = None
        cm = self.cloud_mode()
        slots = self.cloud_slots()
        # the composer/header pick for this message, or the account's sticky pick when
        # the client sent none: it wins over any switch wording in the text
        dev_ov = (
            self.sync.state.get("device_overrides")
            if hasattr(self, "sync") and isinstance(getattr(self.sync, "state", None), dict)
            else None
        )
        dev_default_model = (
            str(dev_ov.get("default_model") or "").strip()
            if isinstance(dev_ov, dict) and dev_ov.get("enabled") and source == "user"
            else ""
        )
        explicit = (model or dev_default_model or self.model_choice() or "").strip().lower()
        if cm == "local" and not model:
            # All-Local mode: keep chat & vision on local models unless explicitly overridden per message
            explicit = "local"
        elif cm == "hybrid" and not model and slots.get("chat") == "local" and explicit in ("flash", "deepseek", "pro", "gemini"):
            explicit = "local"
        if explicit in SWITCH_WORDS or explicit == "gemini":
            # the composer's pill picked the model for this message: it wins over any
            # switch wording in the text, which is then sent as-is
            once = self.gemini_once() if explicit == "gemini" else self.alias_once(explicit)
        # User asking FOR a model — not telling it NOT to use it ("don't use gemini for
        # this" used to route the turn TO Gemini). A negation up to three words before the
        # verb kills the match.
        elif cm != "local" and _model_alias_in_text(text, "gemini", direct_address=True):
            once = self.gemini_once()
        elif attachments and any(a.kind == "image" for a in attachments):
            # Local-first (2026-11-09): the configured model is qwen3.5:9b with native
            # vision, so photos are analyzed locally for free, no cap, nothing leaves
            # the PC. Gemini Flash is the fallback when the local model cannot see
            # images or when All-Cloud mode is selected.
            if cm == "cloud":
                once = self.gemini_once()
            elif cm != "local" and self.app.agent.llm.vision_available is not True:
                once = self.gemini_once()
        elif via == "cloud":
            once = self.cloud_once()
        elif cm == "cloud" and not explicit:
            try:
                once = self.cloud_once()
            except Exception:
                once = None
        else:
            # *Use flash / use pro / use local*: one turn on a named model (see
            # `_model_alias_in_text` for the wording; Gemini above keeps its own branch,
            # its direct-address form "Gemini, …" does not fit the others).
            for word in SWITCH_WORDS:
                if _model_alias_in_text(text, word):
                    if cm == "local" and word != "local":
                        continue
                    once = self.alias_once(word)
                    break
        thread = self.threads.get(thread_id) or self._make_thread(thread_id, thread_id)
        if event_id:
            # A client retry that already landed (the offline outbox re-sends with the same
            # id): hand the event back and run nothing twice.
            existing = thread.timeline.get(event_id)
            if existing:
                return existing
        thread.updated_at = now_iso()
        if language:
            # remembered on the conversation: a chat addressed to another device passes it on
            thread.agent.ui_language = language
        if thread.device:
            # a chat addressed to another device: the text runs there, not here
            if thread.busy:
                raise ValueError(f"{thread.device_name or 'the device'} is still busy")
            if once is not None and once[0] is not None:
                # that device picks its own model; the one-turn client is not needed
                spawn(once[0].close(), "closing the one-turn Cloud client")
            event = self.ui.emit({"type": "user", "text": text, "thread": thread_id})
            self.hub.run_remote(thread, text)
            return event
        if source in ("user", "device"):
            event_data: dict[str, Any] = {"type": "user", "text": text, "thread": thread_id}
            if event_id:
                event_data["id"] = event_id
            if attachments:
                event_data["files"] = [a.model_dump() for a in attachments]
            if once is not None:
                # the turn's events name the model it ran on (the reply and a failure too)
                event_data["model_used"] = once[1]
            if source == "device":
                # asked by another device over the hub; shown as a bubble with its name
                event_data["via"] = label
                thread.purposes[text] = f"asked from {label}" if label else "asked from a device"
            note = ""
            if source == "user" and text.startswith("@"):
                # "@Pixel 8 open the calendar": the work is for that device (contract C7, rule
                # 8). The bubble shows the text without the mention and whom it is for; the
                # agent gets one system note in front and hands the task over with `delegate`.
                mention = parse_mention(text, self.hub.others(), self.hub.device_id)
                if mention is not None and (mention.text or attachments):
                    text = mention.text
                    event_data["text"] = text
                    event_data["to_device"] = mention.device_id
                    event_data["to_device_name"] = mention.device_name
                    note = system_note(mention.device_name, mention.device_id)
            event = self.ui.emit(event_data)
            if note:
                text = f"[{note}]\n\n{text}" if text else f"[{note}]"
            self.bus.publish({"kind": "thread", "thread": thread.meta()})
            # the person's words reach the account's other devices now (C8), not after the turn
            self.sync.message_sent(thread)
            # "yes" / "go ahead" / "no" while a card waits: the answer IS the decision,
            # made in the chat where the card sits (the questions card has this already)
            if not attachments and self._decide_approval_from_chat(thread, text):
                return event
            if not attachments and self.ui.answer_question(thread_id, text):
                return event
            # nanoMuse: a request for a new look (or the pick among four candidates) is the
            # studio's, not the agent's — the card in the chat takes it from here; a picture
            # attached to the request is the reference the candidates are drawn from
            if (
                source == "user"
                and all(a.kind == "image" for a in attachments)
                and self.avatar.intercept(thread_id, text, [a.path for a in attachments])
            ):
                # the first conversation's "what should I call you?" is not answered by this
                if firstrun.bound_to(self.firstrun.state, thread_id):
                    self.dismiss_first_chooser()
                return event
        else:
            event = self.ui.emit(
                {
                    "type": "notice",
                    "level": "info",
                    "text": label or text,
                    "source": source,
                    "thread": thread_id,
                }
            )
            if label:
                thread.purposes[text] = label
        run = llm_session.run_of(source)
        if once is not None:
            thread.inbox.put_nowait(
                Incoming(text, attachments, language, once[0], once[1], delivery, run=run)
            )
        else:
            thread.inbox.put_nowait(
                Incoming(text, attachments, language, delivery=delivery, run=run)
                if attachments or language or delivery or run != "person"
                else text
            )
        self._ensure_worker(thread)
        return event

    def gemini_once(self) -> tuple[BaseLLM | None, str]:
        """Run one turn on Google Gemini when explicitly requested.

        Uses `gemini-3.1-flash-lite` for fast non-thinking turns and tool calls
        while respecting a free-tier-safe daily cap.
        """
        usage_file = Path(self.settings.data_dir) / "gemini_usage.json"
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        data = {"date": today, "count": 0}
        if usage_file.exists():
            try:
                data = json.loads(usage_file.read_text(encoding="utf-8"))
                if data.get("date") != today:
                    data = {"date": today, "count": 0}
            except Exception:
                pass
        # hard cap: free-tier safe, enforced even against an older lower one on disk
        limit = 200
        data["limit"] = limit

        if int(data.get("count", 0)) >= limit:
            logger.info("Daily Gemini limit (%s requests) reached; staying on local Qwen", limit)
            return None, self.settings.llm.model

        key = self.app.vault.get("GEMINI_API_KEY") or ""
        if not key:
            return None, self.settings.llm.model

        data["count"] = data.get("count", 0) + 1
        try:
            usage_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

        gemini_cfg = LLMSettings(
            provider="openai",
            model="gemini-3.1-flash-lite",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            api_key=key,
            stream=True,
            tool_mode="auto",
            temperature=0.2,
            max_tokens=4096,
        )
        return create_llm(gemini_cfg, data_dir=self.settings.data_dir), "gemini-3.1-flash-lite"

    def alias_once(self, word: str) -> tuple[BaseLLM | None, str]:
        """One turn on a named model (the chat switch: *use flash / use pro / use local*).
        Same shape as gemini_once(): the client runs this turn only, and a ``None`` client
        keeps the configured model (a missing key, a word that matched nothing real)."""
        if word in ("flash", "deepseek"):
            return self.deepseek_once("deepseek-flash", thinking="disabled")
        if word == "pro":
            return self.deepseek_once("deepseek-v4-pro", thinking="enabled")
        if word in ("local", "qwen"):
            return self.local_once()
        return None, self.settings.llm.model

    def deepseek_once(self, model: str, thinking: str) -> tuple[BaseLLM | None, str]:
        """One turn on DeepSeek using the configured vault key (see the provider catalogue:
        flash reads pictures, V4 Pro does not; thinking mode differs per turn)."""
        key = self.app.vault.get("DEEPSEEK_API_KEY") or ""
        if not key:
            logger.info("no DeepSeek key in the vault; staying on {}", self.settings.llm.model)
            return None, self.settings.llm.model
        base = self.settings.llm
        cfg = LLMSettings(
            provider="openai",
            model=model,
            base_url="https://api.deepseek.com/v1",
            api_key=key,
            stream=base.stream,
            tool_mode="auto",
            temperature=base.temperature,
            max_tokens=base.max_tokens,
            pass_reasoning=True,
            extra_body={"thinking": {"type": thinking}},
        )
        return create_llm(cfg, data_dir=self.settings.data_dir), model

    def local_once(self) -> tuple[BaseLLM | None, str]:
        """One turn on the local Qwen fallback (the benchmarked rollback in config.toml)."""
        base = self.settings.llm
        cfg = LLMSettings(
            provider="openai",
            model="qwen3.5:9b-32k",
            base_url="http://127.0.0.1:11434/v1",
            api_key="ollama",
            stream=base.stream,
            tool_mode="auto",
            temperature=base.temperature,
            max_tokens=2048,
            extra_body={"options": {"num_ctx": 32768}, "keep_alive": "24h"},
        )
        return create_llm(cfg, data_dir=self.settings.data_dir), "qwen3.5:9b-32k"

    def _record_usage(self, thread: Thread, model_used: str | None, source: str) -> None:
        """Every turn lands in the usage ledger: the model, its tokens, what it cost at
        the provider's rate card, and what the turn did (tool calls, files, pictures).
        The chat keeps its one cost line for the models a rate card prices; local turns
        are free and stay quiet."""
        model = model_used or self.settings.llm.model
        traces = InstrumentationRegistry.get().list_traces(30)
        trace = next((t for t in reversed(traces) if t.get("conversation_id") == thread.id), None)
        if not trace or not trace.get("prompt_tokens"):
            return
        started = str(trace.get("start_iso") or "")[:19]
        artifacts = [
            e
            for e in thread.timeline.events
            if e.get("type") == "artifact" and str(e.get("ts", ""))[:19] >= started
        ]
        attached = [
            e
            for e in thread.timeline.events
            if e.get("type") == "user" and str(e.get("ts", ""))[:19] >= started
        ]
        files: list[dict[str, Any]] = []
        for e in attached:
            for f in e.get("files") or []:
                if isinstance(f, dict) and f.get("name"):
                    files.append(f)
        self.usage_ledger.record_turn(
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            thread=thread.id,
            model=model,
            source=source,
            trace=trace,
            artifacts=artifacts,
            attachments=files,
        )
        if known_cost_model(model) and source == "person":
            self.ui.emit(
                {
                    "type": "notice",
                    "level": "info",
                    "source": "usage",
                    "thread": thread.id,
                    "text": usage_note(model, trace),
                }
            )

    def usage_summary(self, days: int = 30, model: str = "") -> dict[str, Any]:
        """What the Usage screen shows, read straight from the local ledger. ``model``
        (an exact model id) narrows every figure to that one AI's turns; the local
        Ollama's loaded sizes ride along for the RAM line on a local model."""
        out = self.usage_ledger.summary(days=max(1, min(days, 365)), model=model)
        out["ollama"] = self._ollama_memory()
        return out

    @staticmethod
    def _ollama_memory() -> dict[str, Any]:
        """The local Ollama's loaded models with their total and VRAM sizes, when it
        answers; nothing here leaves the machine."""
        import json as _json
        import urllib.request

        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=1.5) as r:
                data = _json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - Ollama off or busy: nothing to report
            return {"models": []}
        models = [
            {
                "name": str(m.get("name") or m.get("model") or ""),
                "size_bytes": int(m.get("size") or 0),
                "vram_bytes": int(m.get("size_vram") or 0),
            }
            for m in data.get("models", [])
        ]
        return {"models": models}

    def cloud_once(self) -> tuple[BaseLLM | None, str]:
        """The client for *Use nanoMuse Cloud this time* (the own-key models contract, §4)
        and the model's id: the account's recommended chat model under the Cloud key from
        the vault, for one turn. ``[llm]``, ``[cloud] models`` and the conversation stay as
        they are; a turn later the thread is back on the configured model. Refused with a
        CloudError when nobody is signed in (401 ``signed_out``). When the chat already runs
        on the account there is nothing to switch: the client is None, the id is the
        configured model's."""
        if not self.app.cloud_signed_in():
            raise CloudError(401, "signed_out", "Sign in to nanoMuse Cloud first.")
        if self.app.llm_is_cloud():
            return None, self.settings.llm.model
        model = self.hub.chat_models[0] if self.hub.chat_models else DEFAULT_CHAT_MODEL
        llm = self.settings.llm
        merged = LLMSettings(
            provider="openai",
            model=model,
            base_url=model_url(self.hub.cloud.base_url),
            api_key=self.app.vault.get(CLOUD_KEY) or "",
            tool_mode="auto",
            stream=llm.stream,
            temperature=llm.temperature,
            max_tokens=llm.max_tokens,
            timeout=llm.timeout,
        )
        return create_llm(merged, data_dir=self.settings.data_dir), model

    # ------------------------------------------------------------------ attachments
    def attachment(self, rel: str) -> Attachment:
        """An attachment record for a file in the workspace (the path as the app has it)."""
        target = self.resolve_workspace_path(rel)
        if not target.is_file():
            raise ValueError(f"no such file: {rel}")
        path = target.relative_to(self.workspace()).as_posix()
        return Attachment(
            path=path,
            name=target.name,
            size=target.stat().st_size,
            kind=Attachment.kind_of(target.name),
            mime=mimetypes.guess_type(target.name)[0] or "",
        )

    def save_upload(self, name: str, data: bytes) -> Attachment:
        """A file from the phone into ``attachments/<date>/`` in the workspace, under a
        safe version of its name (a second file with the same name gets a suffix)."""
        limit = self.settings.server.max_upload_mb * 1024 * 1024
        if len(data) > limit:
            raise ValueError(f"file is larger than {self.settings.server.max_upload_mb} MB")
        safe = re.sub(
            r"[^\w.\- ()\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", "_", Path(name).name
        ).strip(" ._")
        safe = safe[:120] or "file"
        folder = self.workspace() / "attachments" / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe
        stem, suffix = target.stem, target.suffix
        n = 2
        while target.exists():
            target = folder / f"{stem} ({n}){suffix}"
            n += 1
        target.write_bytes(data)
        return self.attachment(target.relative_to(self.workspace()).as_posix())

    def _ensure_worker(self, thread: Thread) -> None:
        if thread.worker is None or thread.worker.done():
            thread.worker = asyncio.create_task(self._worker(thread), name=f"thread-{thread.id}")

    async def _worker(self, thread: Thread) -> None:
        token = current_thread.set(thread.id)
        try:
            while not thread.inbox.empty():
                item = thread.inbox.get_nowait()
                thread.busy = True
                incoming = item if isinstance(item, Incoming) else Incoming(item)
                try:
                    # Drain and merge rapid-fire consecutive user messages. Grace window is
                    # short (0.15s): it only needs to outlast the gap between two messages
                    # typed back-to-back, and every millisecond here is added to EVERY turn.
                    texts = [incoming.text]
                    files = list(incoming.files or [])
                    await asyncio.sleep(0.35)  # Grace window for rapid consecutive messages
                    while not thread.inbox.empty():
                        next_item = thread.inbox._queue[0]
                        next_inc = (
                            next_item if isinstance(next_item, Incoming) else Incoming(next_item)
                        )
                        if next_inc.delivery or next_inc.llm is not None or next_inc.run != "person":
                            break
                        thread.inbox.get_nowait()
                        if next_inc.text:
                            texts.append(next_inc.text)
                        if next_inc.files:
                            files.extend(next_inc.files)
                    text = "\n".join(texts)
                    incoming.text = text
                    incoming.files = files
                    # usage limits: checked before the turn starts, for person runs and
                    # background ones alike (a limit nobody can reach is a limit that lies)
                    blocked = await self._usage_gate(thread, incoming)
                except BaseException:
                    thread.busy = False
                    raise
                if blocked is not None:
                    thread.busy = True
                    self.bus.publish({"kind": "thread", "thread": thread.meta()})
                    self.ui.begin_run(thread.id, model_used=incoming.model)
                    self.ui.on_assistant_message(blocked, None)
                    self.ui.end_run(thread.id)
                    thread.busy = False
                    continue
                thread.agent.inbox = thread.inbox
                model_used = incoming.model
                if incoming.llm is not None:
                    # *Use nanoMuse Cloud this time*: this turn on the account's model, the
                    # configured client back when it ends (or whatever a switch meanwhile set)
                    thread.agent.llm = incoming.llm
                elif incoming.run in ("schedule", "goal", "feed") and self.background_model():
                    # the background model runs the quiet work (routines, goal passes, feed
                    # work); _release_llm hands the configured client back when the run ends
                    once = self.alias_once(self.background_model().lower())
                    if once[0] is not None:
                        thread.agent.llm = once[0]
                        model_used = once[1]
                self.bus.publish({"kind": "thread", "thread": thread.meta()})
                # No sentence of its own: each client words the pause between steps
                # itself ("On it: <the request>") in its language.
                self.ui.set_status("working", "", thread.id)
                try:
                    purpose = thread.purposes.pop(text, None)
                    # the first conversation speaks to the person's turns in its own chat;
                    # a background run (a goal pass, a routine) never hears it
                    thread.agent.prompt_addendum = (
                        None if purpose else self._firstrun_addendum_for(thread.id)
                    )
                    self.ui.begin_run(thread.id, background=purpose, model_used=model_used)
                    final = await thread.agent.run(
                        text,
                        purpose=purpose,
                        files=incoming.files,
                        language=incoming.language,
                        run=incoming.run,
                    )
                    quiet, final = (
                        prompts.split_quiet(final or "") if purpose else (False, final or "")
                    )
                    if incoming.delivery:
                        final, quiet = self._validated_reminder_reply(
                            incoming.delivery, final, quiet
                        )
                    self._finish_run(thread, purpose, final.strip(), quiet)
                    if incoming.run == "goal" and final.strip() and not quiet:
                        self._note_checkin()  # a check-in reached the person: "auto" learns if it is answered
                    self._record_usage(thread, model_used, _usage_source(incoming.run, purpose))
                    if incoming.delivery:
                        self._complete_delivery(incoming.delivery)
                except asyncio.CancelledError:
                    if thread.stopping:
                        self._settle_stopped(thread)
                        # the person stopped this run: the occurrence is over, not owed
                        if incoming.delivery:
                            self._complete_delivery(incoming.delivery)
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("thread {} failed", thread.id)
                    notice = failure_notice(exc, thread.id)
                    if model_used:
                        notice["model_used"] = model_used
                    self.ui.emit(notice)
                    if thread.agent.state.value == "error":
                        thread.agent.state = thread.agent.state.__class__.IDLE
                    # The attempt happened and the failure is on screen: the reminder, the
                    # routine or the trigger moves on to its next time rather than standing
                    # due until a restart. Only a run the process never finished (a crash,
                    # a shutdown) is replayed at the next start.
                    if incoming.delivery:
                        self._complete_delivery(incoming.delivery)
                finally:
                    self.ui.end_run(thread.id)
                    thread.agent.inbox = None
                    self._release_llm(thread)
                    thread.busy = False
                    thread.updated_at = now_iso()
                    self.ui.set_status("idle", "", thread.id)
                    self.bus.publish({"kind": "thread", "thread": thread.meta()})
        finally:
            current_thread.reset(token)

    async def _usage_gate(self, thread: Thread, incoming: Incoming) -> str | None:
        """The sentence that replaces this turn when a usage limit stops it, or None.

        A ``warn`` limit only pins a notice to the chat and lets the turn run. A ``stop``
        limit refuses the turn. A ``confirm`` limit asks once per turn through the usual
        approval card; no answer within five minutes counts as no.
        """
        model = incoming.model or self.app.settings.llm.model
        binding = self.usage_limits.evaluate(model)
        if binding is None:
            return None
        if binding["level"] == "warn":
            self.ui.emit(
                {
                    "type": "notice",
                    "level": "warn",
                    "thread": thread.id,
                    "text": binding["text"],
                }
            )
            return None
        if binding.get("action") == "confirm":
            if self.settings.sentinel.mode == "auto":
                self.ui.emit(
                    {
                        "type": "notice",
                        "level": "warn",
                        "thread": thread.id,
                        "text": f"{binding['text']} (Hands-off mode: continuing automatically)",
                    }
                )
                return None
            from nanomuse.ui import ApprovalRequest

            decision = await self.ui.ask_approval(
                ApprovalRequest(
                    tool="usage_limit",
                    args={},
                    summary=f"{binding['text']} Run this turn anyway?",
                    risk="moderate",
                    purpose="usage limit reached",
                    grant_options=["once"],
                )
            )
            if decision.approved:
                return None
        return (
            f"{binding['text']} I did not run this turn. Raise or remove the limit in the "
            "Usage tab, or ask me to (system_admin set_usage_limit), and I will keep going."
        )

    def schedule_restart(self) -> None:
        """Come back up clean: a detached helper waits for the port to free, starts the
        server again with the same arguments, and this process exits."""
        import os
        import subprocess

        try:
            host = self.settings.server.host
            port = self.settings.server.port
            args = sys.argv[1:]
            cmd = [sys.executable, "-m", "nanomuse", *args]
            helper = self.data_dir / "restart_helper.py"
            if not helper.is_file():
                helper.write_text(RESTART_HELPER, "utf-8")
            flags = 0
            if sys.platform == "win32":
                # no console window: a restart must not flash a command prompt at the person
                flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(
                [sys.executable, str(helper), host, str(port), *cmd],
                creationflags=flags,
                close_fds=True,
                cwd=str(self.data_dir),
            )
            logger.info("restart scheduled: helper {} spawned, exiting now", helper)
        except Exception:  # noqa: BLE001
            logger.exception("restart could not be scheduled; staying up instead")
            return
        asyncio.get_running_loop().call_later(0.2, os._exit, 0)

    def _release_llm(self, thread: Thread) -> None:
        """A run ended: the thread goes back to the configured model client. The client the
        run held, when it is another one (the one-turn Cloud client of *Use nanoMuse Cloud
        this time*, or the client a model switch replaced while this turn was running; see
        ``Connections._swap_llm``), is closed once no other running turn still holds it."""
        held = thread.agent.llm
        thread.agent.llm = self.app.llm
        if held is self.app.llm:
            return
        for other in self.threads.values():
            if other is not thread and other.busy and other.agent.llm is held:
                return
        spawn(held.close(), "closing a retired model client")

    def _finish_run(self, thread: Thread, purpose: str | None, final: str, quiet: bool) -> None:
        """The run's last word: shown as the assistant bubble unless it is on screen
        already, and flagged ``final`` so clients can tell it from the step narration
        before it. Background runs push it once, here, not per step."""
        event: dict[str, Any] | None = None
        if (
            final
            and thread.id not in self.ui.reply_shown
            and final != self.ui.last_assistant_text.get(thread.id)
        ):
            event = {"type": "assistant", "text": final, "thread": thread.id, "final": True}
            if quiet:
                event["quiet"] = True
            if thread.id in self.ui.model_once:
                event["model_used"] = self.ui.model_once[thread.id]
            event = self.ui.emit(event)
        else:
            eid = self.ui.last_assistant_event.get(thread.id)
            if eid and (existing := self.ui.get_timeline(thread.id).get(eid)):
                if not existing.get("final"):
                    self.ui.patch(thread.id, eid, final=True)
                event = existing
        if purpose and event and event.get("text") and not event.get("quiet"):
            self._push_background(event)
        elif event and event.get("text") and not event.get("quiet"):
            self._push_reply(thread, event)
        # the turn is done: its texts go to the account's other devices in a moment
        self.sync.turn_finished(thread)
        if not purpose:
            self._firstrun_turn_ended(thread.id, final)
            # a chat still wearing its placeholder name gets one from what was asked
            self._maybe_auto_title(thread)

    # ------------------------------------------------------------------ approvals
    def decide(
        self, approval_id: str, approved: bool, scope: str = "once", reason: str = ""
    ) -> bool:
        return self.ui.resolve_approval(approval_id, approved, normalize_scope(scope), reason)

    def _oldest_pending_approval(self, thread: Thread) -> str | None:
        """The id of the approval card waiting longest in this chat, or None."""
        for e in thread.timeline.events:
            if e.get("type") == "approval" and e.get("status") == "pending":
                return str(e.get("id") or "")
        return None

    def _decide_approval_from_chat(self, thread: Thread, text: str) -> bool:
        """A short reply that reads like a decision settles the card waiting in this
        chat: "yes" approves, "no" denies. Anything longer, or mixed words, is left to
        the agent as a normal message. Returns True when a card was decided."""
        verdict = _chat_approval_verdict(text)
        if verdict is None:
            return False
        approval_id = self._oldest_pending_approval(thread)
        if approval_id is None:
            return False
        decided = self.ui.resolve_approval(
            approval_id, verdict, "once", "your message" if verdict else "declined in chat"
        )
        if decided:
            self.ui.emit(
                {
                    "type": "notice",
                    "level": "info",
                    "source": "approvals",
                    "thread": thread.id,
                    "text": "Approved from your message. Continuing."
                    if verdict
                    else "Denied from your message.",
                }
            )
        return decided

    def forget_approvals(self) -> None:
        self.app.sentinel.forget_approvals()
        self.bus.publish({"kind": "approvals_reset"})

    def revoke_grant(self, key: str) -> bool:
        ok = self.app.sentinel.revoke(key)
        if ok:
            self.bus.publish({"kind": "approvals_reset"})
        return ok

    # ------------------------------------------------------------------ browser view
    @property
    def browser(self) -> Browser | None:
        tool = self.app.tools.get("browser")
        return tool if isinstance(tool, Browser) else None

    def watch_browser(self) -> None:
        """Show the user what the browser tool sees. Called again when the tool is added."""
        tool = self.browser
        if tool is not None:
            tool.on_frame = self.ui.on_browser_frame
            tool.link = self.phone
            tool.chrome_bridge = self.chrome_bridge

    def browser_backend(self) -> str:
        """Where the browser renders now: "playwright", "device", or "" for nowhere."""
        tool = self.browser
        return tool.backend_kind if tool is not None else ""

    async def browser_control(self, thread: str, body: dict[str, Any]) -> dict[str, Any]:
        """The user takes over the agent's browser from the app (tap, type, open a URL).

        ``take_over`` puts a hold on (contract C1): the agent's next browser action waits
        until ``handed_back`` — the Done button — takes it off. Both work whether or not
        a page is open; the other actions need one."""
        action = str(body.get("action") or "")
        if action not in (
            "click",
            "type",
            "key",
            "scroll",
            "back",
            "reload",
            "navigate",
            "look",
            "take_over",
            "handed_back",
        ):
            raise ValueError(f"unknown browser action '{action}'")
        tool = self.browser
        hold: dict[str, Any] | None = None
        if action == "take_over":
            hold = self.app.holds.open(thread, "browser", by="user", reason="").to_event()
        elif action == "handed_back":
            released = self.app.holds.release(thread, "browser")
            hold = released.to_event() if released is not None else None
        if action in ("take_over", "handed_back") and (tool is None or not tool.open):
            return {"url": "", "title": "", "hold": hold}
        if tool is None:
            raise LookupError("the browser tool is not enabled")
        if action not in ("look", "navigate") and not tool.open:
            raise LookupError("the browser is not open right now; open a URL first")
        result = await tool.user_action(
            action,
            thread,
            x=body.get("x"),
            y=body.get("y"),
            text=body.get("text"),
            key=body.get("key"),
            dy=body.get("dy"),
            url=body.get("url"),
        )
        if hold is not None:
            result["hold"] = hold
        return result

    # ------------------------------------------------------------------ holds (C1)
    def holds_view(self) -> list[dict[str, Any]]:
        return self.app.holds.view()

    def open_hold(self, thread: str, tool: str, reason: str = "") -> dict[str, Any]:
        """``POST /api/holds``: the user takes the browser, the computer or the phone over
        for this chat; the agent's actions of that kind wait until :meth:`done_hold`."""
        if thread not in self.threads:
            raise LookupError(f"no thread {thread!r}")
        return self.app.holds.open(thread, tool, by="user", reason=reason).to_event()

    def done_hold(self, hold_id: str) -> dict[str, Any]:
        """``POST /api/holds/{id}/done``: the hold goes off; whatever waited on it looks
        again and goes on. A hold that is already off comes back as it is."""
        hold = self.app.holds.done(hold_id)
        if hold is None:
            raise LookupError(f"no hold {hold_id!r}")
        if hold.tool == "browser":
            # the model hears that the page was used, the way the phone's sheet reports it
            tool = self.browser
            if tool is not None and tool.open:
                spawn(self._browser_handed_back(tool, hold.thread), "browser handed back")
        return hold.to_event()

    async def _browser_handed_back(self, tool: Browser, thread: str) -> None:
        try:
            await tool.user_action("handed_back", thread)
        except Exception as exc:  # noqa: BLE001 — the hold is off either way
            logger.debug("browser handed back: {}", exc)

    # ------------------------------------------------------------------ push
    def pending_count(self) -> int:
        """Cards waiting for the user, across threads — the app badge number."""
        return len(self.ui.pending_approvals) + len(self.ui.pending_questions)

    @staticmethod
    def _push_url(thread: str) -> str:
        return "/" if thread == MAIN_THREAD else f"/?thread={thread}"

    def _on_event(self, event: dict[str, Any]) -> None:
        """Every persisted event: index it for instant search and push when it needs you."""
        self._index_event(event)
        self._maybe_push(event)

    def _index_event(self, event: dict[str, Any]) -> None:
        """New messages join the searchable index the moment they land, so 'retrieve any
        message ever' covers today's chat, not just the imported archive. Short-lived
        connection per event; failures never block the turn."""
        kind = event.get("type")
        text = event.get("text") or ""
        if kind not in ("user", "assistant") or not text:
            return
        try:
            con = sqlite3.connect(self.settings.data_dir / "personal.db", timeout=5)
            try:
                con.execute(
                    "INSERT INTO messages_fts (turn_id, thread_id, role, created_at, content) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event.get("turn") or "",
                        event.get("thread") or MAIN_THREAD,
                        kind,
                        event.get("ts") or now_iso(),
                        text,
                    ),
                )
                con.commit()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - search indexing is best effort
            logger.debug("messages_fts index failed: %s", exc)

    def _unindex_event(self, thread_id: str, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        text = str(event.get("text") or "")
        if kind not in ("user", "assistant") or not text:
            return
        db = self.settings.data_dir / "personal.db"
        if not db.exists():
            return
        try:
            con = sqlite3.connect(db, timeout=5)
            try:
                con.execute(
                    "DELETE FROM messages_fts WHERE rowid IN ("
                    "SELECT rowid FROM messages_fts "
                    "WHERE thread_id = ? AND role = ? AND content = ? "
                    "ORDER BY rowid DESC LIMIT 1)",
                    (thread_id, kind, text),
                )
                con.commit()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("messages_fts unindex event failed: %s", exc)

    def _unindex_thread(self, thread_id: str) -> None:
        db = self.settings.data_dir / "personal.db"
        if not db.exists():
            return
        try:
            con = sqlite3.connect(db, timeout=5)
            try:
                con.execute("DELETE FROM messages_fts WHERE thread_id = ?", (thread_id,))
                con.commit()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("messages_fts unindex thread failed: %s", exc)

    def _maybe_push(self, event: dict[str, Any]) -> None:
        """Called with every persisted event: a card that needs you gets a push. Background
        results go through ``_push_background`` once the run is over, so the step-by-step
        narration never leaves the app; the service worker drops the notification anyway
        if the app is on screen."""
        if not self.push.enabled or not self.push.subscriptions:
            return
        kind = event.get("type")
        thread = event.get("thread") or MAIN_THREAD
        name = self.profile.name
        if kind == "approval" and event.get("status") == "pending":
            self.push.notify(
                f"{name} needs your approval",
                event.get("summary") or event.get("tool") or "",
                tag=f"approval-{event['id']}",
                url=self._push_url(thread),
                badge=self.pending_count(),
                kind="approval",
            )
        elif kind == "question" and event.get("status") == "pending":
            self.push.notify(
                f"{name} has a question",
                event.get("text") or "",
                tag=f"question-{event['id']}",
                url=self._push_url(thread),
                badge=self.pending_count(),
                kind="question",
            )

    def _push_background(self, event: dict[str, Any]) -> None:
        """The last word of a background run (a goal pass, a check-in) worth surfacing."""
        if not self.push.enabled or not self.push.subscriptions:
            return
        thread = event.get("thread") or MAIN_THREAD
        name = self.profile.name
        about = str(event.get("about") or "")
        title = about.replace("Working on your goal: ", "") or name
        kind = "background"
        tag = f"background-{thread}"
        if about.startswith("Check-in: "):
            title = f"{name} · check-in"
        elif about.startswith("Reminder: "):
            # A reminder is an alarm, not a message: it stays on screen until dismissed
            # (requireInteraction) and buzzes hard (the service worker's alarm pattern).
            title = f"{name} · reminder"
            kind = "alarm"
            tag = f"alarm-{thread}"
        elif about.startswith("Routine: "):
            title = about[len("Routine: ") :] or name
        elif about.startswith(("New mail: ", "Coming up: ", "Webhook: ")):
            title = f"{name} · {about.split(': ', 1)[0].lower()}"
        self.push.notify(
            title,
            _first_lines(str(event["text"])),
            tag=tag,
            url=self._push_url(thread),
            badge=self.pending_count(),
            kind=kind,
        )

    def _push_reply(self, thread: Thread, event: dict[str, Any]) -> None:
        """A reply to something the person typed: a buzz when the app is not on screen.
        The phone's service worker shows nothing while the app is already open there."""
        if not self.push.enabled or not self.push.subscriptions:
            return
        self.push.notify(
            f"{self.profile.name} replied",
            _first_lines(str(event["text"])),
            tag=f"reply-{thread.id}",
            url=self._push_url(thread.id),
            badge=self.pending_count(),
            kind="reply",
        )

    # ---------------------------------------------------------- durable scheduled work
    def _validated_reminder_reply(
        self, delivery_id: str, final: str, quiet: bool
    ) -> tuple[str, bool]:
        """A scheduled reminder must actually deliver the reminder. The prompt asks the
        model to do it, and a weak model sometimes answers with unrelated banter instead
        (for example replying with a casual greeting). When the reply does not carry the reminder's words, it is replaced with
        the canonical message. The reminder text is the delivery label minus its prefix.
        """
        delivery = self.deliveries.get(delivery_id)
        if delivery is None or delivery.kind != "reminder":
            return final, quiet
        label = delivery.label or ""
        if not label.startswith("Reminder: "):
            return final, quiet
        payload = label[len("Reminder: ") :].strip()
        if not payload:
            return final, quiet
        reply_words = set(re.findall(r"[a-z0-9']+", final.lower()))
        need = set(re.findall(r"[a-z0-9']+", payload.lower()))
        if not need:
            return final, quiet
        if len(need & reply_words) >= max(2, len(need) // 2):
            return final, quiet
        logger.info(
            "reminder reply did not deliver the reminder; replacing with the canonical message"
        )
        return (
            f"⏰ **Reminder from earlier:** {payload}\nJust keeping this on your radar bro, no stress.",
            False,
        )

    def _queue_delivery(self, delivery: Delivery) -> None:
        self.send(
            delivery.thread if delivery.thread in self.threads else MAIN_THREAD,
            delivery.text,
            source=delivery.kind,
            label=delivery.label,
            delivery=delivery.id,
        )

    def _recover_deliveries(self) -> None:
        """Finish source acknowledgements, then replay work claimed before a crash."""
        for delivery in self.deliveries.unfinalized():
            self._finalize_delivery(delivery)
        for delivery in self.deliveries.pending():
            self._queue_delivery(delivery)

    def _complete_delivery(self, delivery_id: str) -> None:
        delivery = self.deliveries.complete(delivery_id)
        if delivery is not None:
            self._finalize_delivery(delivery)

    def _finalize_delivery(self, delivery: Delivery) -> None:
        if delivery.kind == "reminder":
            self.app.reminders.mark_fired(delivery.source_id)
            self.bus.publish({"kind": "reminders"})
        elif delivery.kind == "goal_check":
            self.app.goals.mark_checked_in(delivery.source_id)
        elif delivery.kind == "trigger":
            self.app.triggers.mark_fired(delivery.source_id, delivery.occurrence)
            self.bus.publish({"kind": "triggers"})
        self.deliveries.finalized(delivery.id)

    def _claim_delivery(
        self,
        source_key: str,
        *,
        kind: str,
        source_id: str,
        occurrence: str,
        thread: str,
        text: str,
        label: str,
    ) -> Delivery | None:
        delivery = self.deliveries.claim(
            source_key,
            kind=kind,
            source_id=source_id,
            occurrence=occurrence,
            thread=thread,
            text=text,
            label=label,
        )
        if delivery is not None:
            self._queue_delivery(delivery)
        return delivery

    # ------------------------------------------------------------------ auto proactivity
    def _refresh_auto(self) -> None:
        self.profile.auto_factor = self.auto_proactivity.factor()

    def _note_checkin(self) -> None:
        self.auto_proactivity.record_sent()
        self._refresh_auto()

    def _note_reply(self) -> None:
        if self.auto_proactivity.record_reply():
            self._refresh_auto()

    # ------------------------------------------------------------------ goals
    def advance_goal(self, goal_id: str, occurrence: str = "") -> Goal:
        goal = self.app.goals.get(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if goal.status != "active":
            raise ValueError(f"goal is {goal.status}")
        surfacing = prompts.SURFACING.get(self.profile.effective_level, prompts.SURFACING["default"])
        stamp = occurrence or new_id()
        self._claim_delivery(
            f"goal:{goal.id}:{stamp}",
            kind="goal",
            source_id=goal.id,
            occurrence=stamp,
            thread=MAIN_THREAD,
            text=prompts.ADVANCE_GOAL_PROMPT.format(goal=goal.render(), surfacing=surfacing),
            label=f"Working on your goal: {goal.title}",
        )
        return goal

    async def _nap(self, seconds: float) -> None:
        """Sleep, unless :meth:`wake` is called first."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except TimeoutError:
            return
        finally:
            self._wake.clear()

    def wake(self) -> None:
        """Run the scheduler's pass now. The Android app calls this from the exact alarm it
        set for :meth:`next_wake_at`, so a reminder fires on time on a phone that was asleep."""
        self._wake.set()

    def next_wake_at(self) -> datetime | None:
        """The earliest moment something scheduled is due — a reminder, a goal check-in, the
        next background pass — or the end of the quiet hours when they hold a check-in back.
        A host that can wake the process (the Android app) sets an alarm for it; on a machine
        that never sleeps the scheduler's own naps are enough."""
        times: list[datetime] = []
        now = datetime.now().astimezone()
        quiet_until = self.profile.quiet_hours_end()
        for r in self.app.reminders.list("active"):
            when = _parse_when(r.next_at)
            if when is not None:
                times.append(when)
        for g in self.app.goals.list("active"):
            when = _parse_when(g.next_check_in)
            if when is not None:
                # a check-in waits for the quiet window to end; wake then
                times.append(max(when, quiet_until) if quiet_until else when)
        if self.profile.proactive and self.next_goal_pass_at is not None:
            times.append(self.next_goal_pass_at.astimezone())
        if not times:
            return None
        return max(min(times), now)

    def _announce_wake(self) -> None:
        """Tell the clients when the next wake is, whenever that changes."""
        when = self.next_wake_at()
        stamp = when.isoformat(timespec="seconds") if when else None
        if stamp == self._announced_wake:
            return
        self._announced_wake = stamp
        self.bus.publish({"kind": "schedule", "next_wake_at": stamp})

    def _next_pass_delay(self) -> float:
        """Seconds until the next background pass: the level's interval, pushed past the
        quiet window if it would land inside one."""
        delay = float(self.profile.interval_seconds)
        due = datetime.now().astimezone() + timedelta(seconds=delay)
        end = self.profile.quiet_hours_end(due)
        if end is not None:
            # a second past the end, so the pass does not wake up still inside the window
            delay = max(delay, (end - datetime.now().astimezone()).total_seconds() + 1)
        return delay

    def schedule_next_pass(self) -> None:
        """(Re)compute when the next background pass is due — on start, after a pass, and
        whenever the level, the interval or the quiet hours change."""
        self.next_goal_pass_at = datetime.now(UTC) + timedelta(seconds=self._next_pass_delay())

    def check_in(self, goal_id: str) -> Goal:
        """Send the reminder for a goal now: a short message from the agent, no work done."""
        goal = self.app.goals.get(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if goal.status != "active":
            raise ValueError(f"goal is {goal.status}")
        occurrence = goal.next_check_in or new_id()
        self._claim_delivery(
            f"goal-check:{goal.id}:{occurrence}",
            kind="goal_check",
            source_id=goal.id,
            occurrence=occurrence,
            thread=MAIN_THREAD,
            text=prompts.CHECK_IN_PROMPT.format(
                goal=goal.render(),
                today=datetime.now().astimezone().strftime("%A, %Y-%m-%d"),
                quiet=prompts.QUIET_MARKER,
            ),
            label=f"Check-in: {goal.title}",
        )
        return goal

    def _run_due_check_ins(self) -> None:
        """Reminders the user asked for fire at any proactivity level, but not in quiet hours
        (they wait for the window to end) and not over a conversation in progress."""
        if self.profile.in_quiet_hours():
            return
        main = self.threads.get(MAIN_THREAD)
        if main is None or main.busy or not main.inbox.empty():
            return
        for goal in self.app.goals.due_check_ins():
            self.check_in(goal.id)
            break  # one at a time; the next tick picks up the rest

    def _pick_goal_for_pass(self) -> Goal | None:
        """Overdue first, then the one that has waited longest."""
        candidates = [g for g in self.app.goals.list("active") if g.next_step is not None]
        candidates.sort(key=lambda g: (not g.overdue, g.updated_at))
        return candidates[0] if candidates else None

    # ------------------------------------------------------------------ reminders
    def _watch_reminders(self) -> None:
        """New items from the tool belong to the chat they were set in, and the Upcoming
        view learns about them right away."""
        tool = self.app.tools.get("reminders")
        if isinstance(tool, Reminders):
            tool.thread_of = current_thread.get
            tool.on_change = lambda: self.bus.publish({"kind": "reminders"})

    def create_reminder(
        self, text: str, at: str = "", repeat: str = "", kind: str = "remind", thread: str = ""
    ) -> Reminder:
        item = self.app.reminders.create(
            text, at=at, repeat=repeat, kind=kind, thread=thread or MAIN_THREAD
        )
        self.bus.publish({"kind": "reminders"})
        return item

    def cancel_reminder(self, reminder_id: str) -> Reminder | None:
        item = self.app.reminders.cancel(reminder_id)
        if item is not None:
            self.bus.publish({"kind": "reminders"})
        return item

    def fire_reminder(self, reminder_id: str) -> Reminder:
        """Hand a reminder or routine to the agent now, in the chat it was set from."""
        item = self.app.reminders.get(reminder_id)
        if item is None:
            raise KeyError(reminder_id)
        if item.status != "active":
            raise ValueError(f"reminder is {item.status}")
        thread = item.thread if item.thread in self.threads else MAIN_THREAD
        now = datetime.now().astimezone().strftime("%A, %B %d, %Y at %I:%M %p")
        if item.kind == "remind":
            reminder_msg = f"Reminder: {item.text}"
            self.ui.emit(
                {
                    "type": "notice",
                    "level": "info",
                    "text": reminder_msg,
                    "source": "reminder",
                    "thread": thread,
                }
            )
            self.app.reminders.mark_fired(item.id)
            self.bus.publish({"kind": "reminders"})
            return self.app.reminders.get(item.id) or item

        template = prompts.ROUTINE_PROMPT
        label = "Routine: " + _short(item.text)
        delivery = self._claim_delivery(
            f"reminder:{item.id}:{item.next_at}",
            kind="reminder",
            source_id=item.id,
            occurrence=item.next_at,
            thread=thread,
            text=template.format(now=now, text=item.text, quiet=prompts.QUIET_MARKER),
            label=label,
        )
        return item if delivery is not None else (self.app.reminders.get(item.id) or item)

    def _run_due_reminders(self) -> None:
        """A time the user named is kept whatever the proactivity level or the quiet hours;
        the message queues behind a conversation in progress rather than skipping."""
        for item in self.app.reminders.due():
            try:
                self.fire_reminder(item.id)
            except (KeyError, ValueError):  # pragma: no cover - raced with a cancel
                continue

    # ------------------------------------------------------------------ triggers
    def _watch_triggers(self) -> None:
        """Triggers set from the chat belong to that chat; the app and the inbox watcher
        learn about a new one right away."""
        tool = self.app.tools.get("triggers")
        if isinstance(tool, Triggers):
            tool.thread_of = current_thread.get
            tool.on_change = self._triggers_changed
            tool.available = self.app.trigger_kinds
            tool.base_url = self.base_url()

    def _triggers_changed(self) -> None:
        self._mail_polled_at = None  # look at the inbox on the next tick
        self.bus.publish({"kind": "triggers"})

    def base_url(self) -> str:
        """Where this server is reached — the address a webhook is given."""
        from nanomuse.server import lan_ip  # here: the package imports this module

        s = self.settings.server
        host = s.host if s.host not in ("", "0.0.0.0", "::") else (lan_ip() or "127.0.0.1")
        return f"http://{host}:{s.port}"

    def hook_url(self, trigger: Trigger) -> str:
        return f"{self.base_url()}/api/hooks/{trigger.id}?key={trigger.secret}"

    def create_trigger(
        self, kind: str, text: str, match: str = "", lead_minutes: int = 30, thread: str = ""
    ) -> Trigger:
        have = self.app.trigger_kinds()
        if not have.get(kind, True):
            raise ValueError(
                {
                    "mail": "connect a mailbox first",
                    "event": "add a calendar first",
                    "device": "join the hub first",
                }.get(kind, "that trigger kind needs a connector")
            )
        item = self.app.triggers.create(
            kind, text, match=match, lead_minutes=lead_minutes, thread=thread or MAIN_THREAD
        )
        self._triggers_changed()
        return item

    def cancel_trigger(self, trigger_id: str) -> Trigger | None:
        item = self.app.triggers.cancel(trigger_id)
        if item is not None:
            self.bus.publish({"kind": "triggers"})
        return item

    def fire_trigger(
        self, trigger_id: str, what: str, context: str = "", key: str = "", title: str = ""
    ) -> Trigger | None:
        """Hand a trigger to the agent now, with what happened as context, in the chat it
        was set from. ``key`` names the occurrence (a mail's UID, an event's start, a
        delivery id): the same key never fires twice, so this returns None the second time.
        ``title`` is the short form for the Feed (the subject, the event, the hook's name)."""
        item = self.app.triggers.get(trigger_id)
        if item is None:
            raise KeyError(trigger_id)
        if item.status != "active":
            raise ValueError(f"trigger is {item.status}")
        occurrence = key or new_id()
        thread = item.thread if item.thread in self.threads else MAIN_THREAD
        now = datetime.now().astimezone().strftime("%A, %B %d, %Y at %I:%M %p")
        prefix = {
            "mail": "New mail: ",
            "event": "Coming up: ",
            "hook": "Webhook: ",
            "device": "Device online: ",
        }[item.kind]
        context = context.strip()[:6000]
        block = f"{_fence(context)}\n{context}\n{_fence(context)}\n\n" if context else ""
        if item.kind != "hook":
            # the mail or the event is the user's private data: from here on, sending
            # anything to a host that is not allowlisted is an approval
            self.app.sentinel.taint(thread)
        delivery = self._claim_delivery(
            f"trigger:{item.id}:{occurrence}",
            kind="trigger",
            source_id=item.id,
            occurrence=occurrence,
            thread=thread,
            text=prompts.TRIGGER_PROMPT.format(
                now=now, what=what, context=block, text=item.text, quiet=prompts.QUIET_MARKER
            ),
            label=prefix + _short(title or item.match or what),
        )
        if delivery is None:
            return None
        return self.app.triggers.get(item.id) or item

    def device_came_online(self, device: dict[str, Any]) -> list[Trigger]:
        """A device of the account just came online (the hub's ``devices`` frame): every
        ``device`` trigger whose words are in the device's name or kind runs in the chat it
        was set from, once per arrival. Returns the triggers that fired."""
        name = str(device.get("name") or "").strip()
        kind = str(device.get("kind") or "").strip()
        device_id = str(device.get("id") or "")
        there = f"{name or 'A device'} ({kind})" if kind else (name or "A device")
        fired: list[Trigger] = []
        for trig in self.app.triggers.active("device"):
            if not matches(trig.match, name, kind):
                continue
            if self.fire_trigger(
                trig.id,
                what=f"{there} just came online",
                key=f"device:{device_id}:{new_id()}",
                title=name or kind or device_id,
            ):
                fired.append(trig)
        return fired

    def deliver_hook(self, trigger_id: str, key: str, body: str, content_type: str = "") -> Trigger:
        """A request to a trigger's webhook URL. Wrong id or key → KeyError (the caller
        answers 404 for both, so the URL cannot be probed); too soon → RuntimeError."""
        item = self.app.triggers.get(trigger_id)
        if (
            item is None
            or item.kind != "hook"
            or not secrets.compare_digest(item.secret.encode(), (key or "").encode())
        ):
            raise KeyError(trigger_id)
        if item.status != "active":
            raise ValueError(f"trigger is {item.status}")
        gap = self.settings.triggers.hook_min_seconds
        if item.last_fired_at:
            since = datetime.now(UTC) - datetime.fromisoformat(item.last_fired_at)
            if since.total_seconds() < gap:
                raise RuntimeError("too soon after the last delivery")
        # `last_fired_at` moves when the run completes; deliveries that arrive while the
        # first still runs are measured against the last one accepted, so a burst of
        # requests starts one run, not one per request (this method runs on the loop
        # thread, so the check and the mark below are one step)
        accepted = self._hook_accepted.get(item.id)
        if accepted is not None and time.monotonic() - accepted < gap:
            raise RuntimeError("too soon after the last delivery")
        self._hook_accepted[item.id] = time.monotonic()
        body = body.strip()
        if content_type.startswith("application/json") and body:
            try:
                body = json.dumps(json.loads(body), indent=2, ensure_ascii=False)
            except ValueError:
                pass
        name = item.match or item.id
        what = f"Webhook “{name}” was called" + (" with:" if body else " (empty body).")
        fired = self.fire_trigger(
            item.id, what=what, context=body, key=f"hook:{new_id()}", title=name
        )
        return fired or item

    def _mail_mark(self) -> str:
        """The meta key of the inbox high-water mark: one per mailbox, so a changed account
        starts fresh instead of replaying by UID. The mailbox is named by a hash of the host
        and the address: the address lives in the vault, and a key in the triggers database
        is not where it gets written out in clear. A mark written by an earlier version
        under the address itself is moved to the hashed key on the way."""
        email = self.settings.connectors.email
        account = self.app.vault.resolve(email.address, strict=False) or email.address
        digest = hashlib.sha256(f"{email.imap_host}\n{account}".encode()).hexdigest()[:24]
        mark = f"mail_uid:{email.imap_host}:{digest}"
        legacy = f"mail_uid:{email.imap_host}:{account}"
        if legacy != mark:
            store = self.app.triggers
            old = store.get_meta(legacy, "")
            if old:
                if not store.get_meta(mark, ""):
                    store.set_meta(mark, old)
                store.delete_meta(legacy)
        return mark

    async def _poll_mail(self) -> None:
        """Look at the inbox for the mail triggers, every ``mail_poll_minutes`` while at
        least one is active. Each new mail is offered to every mail trigger; a trigger
        fires once per mail."""
        triggers = self.app.triggers.active("mail")
        if not triggers:
            return
        email = self.settings.connectors.email
        watcher = MailWatcher(email, vault=self.app.vault)
        if not watcher.configured:
            return
        now = time.monotonic()
        every = max(1, self.settings.triggers.mail_poll_minutes) * 60
        if self._mail_polled_at is not None and now - self._mail_polled_at < every:
            return
        self._mail_polled_at = now
        store = self.app.triggers
        mark = self._mail_mark()
        last = int(store.get_meta(mark, "0") or 0)
        try:
            fresh, newest = await watcher.look(last)
        except (RuntimeError, OSError) as exc:
            self.mail_watch_error = str(exc)[:200]
            logger.warning("mail triggers: {}", exc)
            return
        self.mail_watch_error = ""
        self.mail_checked_at = now_iso()
        # the deliveries first, the high-water mark after: a crash between the two reads
        # the same mails again, and a mail's key never fires twice (fire_trigger); the
        # other order would skip a matching mail for good
        for mail in fresh:
            for trig in triggers:
                if matches(trig.match, mail.sender, mail.subject):
                    self.fire_trigger(
                        trig.id,
                        what=f"New mail from {mail.sender}: {mail.subject or '(no subject)'}",
                        context=mail.render(),
                        key=mail.key,
                        title=mail.subject or mail.sender,
                    )
        store.set_meta(mark, str(newest))
        self.bus.publish({"kind": "triggers"})

    def _run_event_triggers(self) -> None:
        """Fire event triggers ``lead_minutes`` before a matching event; an event that has
        already begun is left alone (a late brief helps no one)."""
        triggers = self.app.triggers.active("event")
        cal = self.app.calendar
        if not triggers or not cal.configured:
            return
        now = datetime.now(cal.tz)
        h, m = (int(x) for x in self.settings.connectors.calendar.day_start.split(":"))
        for occ in cal.agenda(now.date(), 3):  # a lead can be a day
            start = occ.start.astimezone(cal.tz)
            if occ.all_day:
                # an all-day event "starts" when the working day does, not at midnight
                start = datetime.combine(occ.start.date(), time_of_day(h, m), tzinfo=cal.tz)
            if start <= now:
                continue
            for trig in triggers:
                if not matches(trig.match, occ.summary, occ.location):
                    continue
                if now < start - timedelta(minutes=trig.lead_minutes):
                    continue
                minutes = max(1, int((start - now).total_seconds() // 60))
                when = "all day" if occ.all_day else f"{start.strftime('%I:%M %p').lstrip('0')}"
                where = f" · {occ.location}" if occ.location else ""
                self.fire_trigger(
                    trig.id,
                    what=f"Coming up: {occ.summary}, {start:%a %Y-%m-%d} {when}{where} (in {minutes} min)",
                    context=cal.render([occ], now.date())
                    + (f"\n\n{occ.description}" if occ.description else ""),
                    key=f"{occ.uid}:{occ.start.isoformat()}",
                    title=occ.summary,
                )

    def _prune_fired(self) -> None:
        """Firing records (a mail's UID, an event's start) are only needed until the
        occurrence is long past; drop the ones older than 90 days, once a day."""
        now = time.monotonic()
        if self._fired_pruned_at is not None and now - self._fired_pruned_at < 86400:
            return
        self._fired_pruned_at = now
        cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat(timespec="seconds")
        self.app.triggers.forget_fired_before(cutoff)

    def triggers_view(self) -> dict[str, Any]:
        items = []
        for t in self.app.triggers.list(None):
            d = t.to_dict()
            if t.kind == "hook":
                d["url"] = self.hook_url(t) if t.status == "active" else ""
            items.append(d)
        available = self.app.trigger_kinds()
        mail_error = self.mail_watch_error
        if not available["mail"] and any(
            t.kind == "mail" and t.status == "active" for t in self.app.triggers.list("active")
        ):
            mail_error = "no mailbox is connected"
        return {
            "items": items,
            "available": available,
            "mail_checked_at": self.mail_checked_at,
            "mail_error": mail_error,
            "mail_poll_minutes": self.settings.triggers.mail_poll_minutes,
        }

    async def _run_feed_routine(self) -> None:
        """The feed's daily routine (contract C5): a batch at its time, whatever the
        proactivity setting — it is quiet (one notification, no chat message), the person
        turned it on in the Feed, and a model has to be there. A failed batch is tried again
        an hour later, not every tick."""
        if not self.feed_posts_due() or not self.settings_view()["llm_ready"]:
            return
        if self._feed_failed_at and time.monotonic() - self._feed_failed_at < FEED_RETRY_S:
            return
        try:
            await self.write_feed_posts()
            self._feed_failed_at = None
        except Exception as exc:  # noqa: BLE001 — the model may be down; the routine keeps its day
            self._feed_failed_at = time.monotonic()
            logger.info("feed routine: batch not written: {}", exc)

    async def _goal_scheduler(self) -> None:
        self.schedule_next_pass()
        while True:
            try:
                self._run_due_reminders()
                self._run_due_check_ins()
                await self._refresh_calendar()
                self._run_event_triggers()
                await self._poll_mail()
                self._prune_fired()
                self._announce_wake()
                await self._run_feed_routine()
                due = self.next_goal_pass_at or datetime.now(UTC)
                remaining = (due - datetime.now(UTC)).total_seconds()
                if remaining > 0:
                    # short naps so a changed setting takes effect without a restart
                    await self._nap(min(remaining, 30))
                    continue
                self.schedule_next_pass()
                if not self.profile.proactive or self.profile.in_quiet_hours():
                    continue
                main = self.threads.get(MAIN_THREAD)
                if main is None or main.busy or not main.inbox.empty():
                    continue
                if self.memory_tidy_due():
                    # housekeeping takes this tick; the goal pass is next time
                    await self.tidy_memory()
                    continue
                goal = self._pick_goal_for_pass()
                if goal is not None:
                    self.advance_goal(
                        goal.id, occurrence=due.isoformat(timespec="seconds")
                    )  # one goal per tick keeps the chat readable
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001  pragma: no cover
                logger.warning("goal scheduler error: {}", exc)
                await asyncio.sleep(30)

    # ------------------------------------------------------------------ calendar
    async def _refresh_calendar(self) -> None:
        """Fetch the calendar feeds when the cache is older than ``refresh_minutes``; the
        Feed and the system prompt read the cache. Runs whatever the proactivity level:
        it is the user's own data, not a background task."""
        cal = self.app.calendar
        if not cal.configured or not cal.stale():
            return
        before = cal.fetched_at
        await cal.refresh()
        if cal.fetched_at != before:
            self.bus.publish({"kind": "calendar", "calendar": self.calendar_view()})

    # ------------------------------------------------------------------ skills
    def skills_view(self) -> dict[str, Any]:
        lib = self.app.skills
        return {**lib.status(), "skills": [s.to_dict() for s in lib.all()]}

    def skill_view(self, name: str) -> dict[str, Any] | None:
        skill = self.app.skills.get(name)
        return skill.to_dict(body=True) if skill else None

    def save_skill(self, content: str, name: str = "") -> dict[str, Any]:
        """A skill from the text of a SKILL.md, written or pasted in the app."""
        skill = self.app.skills.save_text(content, name=name)
        self._publish_skills()
        return skill.to_dict(body=True)

    async def import_skill(self, url: str) -> dict[str, Any]:
        """A skill from a link to a SKILL.md — a raw file link, or a GitHub folder or file
        page, which is turned into one."""
        from nanomuse.skills import fetch_skill_text

        skill = self.app.skills.save_text(await fetch_skill_text(url))
        self._publish_skills()
        return skill.to_dict(body=True)

    def remove_skill(self, name: str) -> bool:
        ok = self.app.skills.remove(name)
        if ok:
            self._publish_skills()
        return ok

    def set_skill_enabled(self, name: str, enabled: bool) -> dict[str, Any] | None:
        skill = self.app.skills.set_enabled(name, enabled)
        if skill is None:
            return None
        data = self.connections.data
        data["skills"] = {"disabled": list(self.settings.skills.disabled)}
        self.connections._save()
        self._publish_skills()
        return skill.to_dict()

    def _publish_skills(self) -> None:
        self.bus.publish({"kind": "skills", "skills": self.skills_view()})
        # the avatar menu shows the counts from the settings view
        self.bus.publish({"kind": "settings", "settings": self.settings_view()})

    def calendar_view(self, days: int = 2, start_date: str | None = None) -> dict[str, Any]:
        """Calendar occurrences with the feeds' status & editable local events, for the app."""
        from nanomuse.calendar.ics import day_bounds

        cal = self.app.calendar
        status = cal.status()
        today = datetime.now(cal.tz).date()
        base_day = today
        if start_date and start_date.strip():
            try:
                base_day = datetime.fromisoformat(start_date.strip()[:10]).date()
            except ValueError:
                base_day = today
        items = cal.agenda(base_day, days) if cal.configured else []
        if cal.local_store is not None and cal.configured:
            start_dt, _ = day_bounds(base_day, cal.tz)
            _, _ = start_dt, cal.local_store
        local_meta = {e["uid"]: e for e in (cal.local_store.list() if cal.local_store is not None else [])}
        enriched: list[dict[str, Any]] = []
        for o in items:
            d = o.to_dict()
            meta = local_meta.get(o.uid)
            if meta:
                d["editable"] = True
                d["tags"] = meta.get("tags") or []
                d["contact_name"] = meta.get("contact_name") or ""
            else:
                d["editable"] = False
                d["tags"] = []
                d["contact_name"] = ""
            enriched.append(d)
        return {
            **status,
            "configured": cal.configured,
            "today": today.isoformat(),
            "start": base_day.isoformat(),
            "events": enriched,
        }

    # ------------------------------------------------------------------ memory tidy-up
    TIDY_MIN_LINES = 12  # nothing to tidy below this
    TIDY_EVERY_NEW = 8  # lines added since the last pass…
    TIDY_EVERY = timedelta(days=7)  # …or this long, whichever comes first

    def memory_tidy_due(self) -> bool:
        """A tidy-up is due when the store grew by a handful of lines since the last one,
        or a week went by — and never while one is running."""
        store = self.app.memory
        if store is None or self._tidying:
            return False
        count = store.count()
        if count < self.TIDY_MIN_LINES:
            return False
        last_at = store.get_meta("tidied_at")
        if not last_at or store.get_meta("tidy_more") == "1":
            return True
        if count - int(store.get_meta("tidied_count", "0") or 0) >= self.TIDY_EVERY_NEW:
            return True
        try:
            return datetime.now(UTC) - datetime.fromisoformat(last_at) >= self.TIDY_EVERY
        except ValueError:
            return True

    async def tidy_memory(self, dry_run: bool = False) -> dict[str, Any]:
        """One pass of ``memory.tidy``: merge duplicates, keep the newer fact, drop what
        was never a fact about the user. What changed goes to the Feed and to the log
        behind *Memory → Recent changes*, where each change can be undone."""
        store = self.app.memory
        if store is None:
            raise ValueError("memory is disabled")
        if self._tidying:
            raise ValueError("a tidy-up is already running")
        self._tidying = True
        try:
            # a routine's call, whoever asked for the pass: not a turn of a conversation
            with llm_session.run("schedule"):
                report = await tidy(store, self.app.llm, dry_run=dry_run)
        finally:
            self._tidying = False
        if dry_run:
            return report.to_dict()
        store.set_meta("tidied_at", datetime.now(UTC).isoformat(timespec="seconds"))
        store.set_meta("tidied_count", str(store.count()))
        store.set_meta("tidy_more", "1" if report.more else "0")  # continue next tick
        self.bus.publish({"kind": "memory"})
        if report.changed:
            label = "Tidied memory"
            self.ui.emit(
                {
                    "type": "notice",
                    "level": "info",
                    "text": label,
                    "source": "memory",
                    "thread": MAIN_THREAD,
                }
            )
            summary = _tidy_summary(report, self.settings.agent.language)
            self.ui.emit(
                {
                    "type": "assistant",
                    "text": summary,
                    "thread": MAIN_THREAD,
                    "source": "background",
                    "about": label,
                    "final": True,
                }
            )
            self.threads[MAIN_THREAD].updated_at = now_iso()
        return report.to_dict()

    # ------------------------------------------------------------------ ideas
    def _ideas_file(self) -> Path:
        return self.data_dir / "ideas.json"

    def cached_ideas(self) -> dict[str, Any]:
        path = self._ideas_file()
        if path.exists():
            try:
                return json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"generated_at": None, "source": "starter", "ideas": STARTER_IDEAS}

    async def refresh_ideas(self, n: int = 5) -> dict[str, Any]:
        context_lines: list[str] = []
        if self.app.memory is not None:
            for m in self.app.memory.all(limit=30):
                context_lines.append(f"- memory ({m.category}): {m.content}")
        for g in self.app.goals.list("active")[:8]:
            nxt = g.next_step
            context_lines.append(
                f"- goal: {g.title} (progress {g.progress}"
                + (f", next: {nxt.title}" if nxt else "")
                + ")"
            )
        main = self.threads.get(MAIN_THREAD)
        if main is not None:
            recent = [e for e in main.timeline.events if e.get("type") in ("user", "assistant")][
                -8:
            ]
            for e in recent:
                context_lines.append(f"- recent {e['type']}: {str(e.get('text', ''))[:200]}")
        if self.settings.agent.user_profile.strip():
            context_lines.insert(0, f"- profile: {self.settings.agent.user_profile.strip()[:500]}")
        if not context_lines:
            data = {"generated_at": now_iso(), "source": "starter", "ideas": STARTER_IDEAS}
            self._ideas_file().write_text(json.dumps(data, ensure_ascii=False), "utf-8")
            return data
        language = self.settings.agent.language
        prompt = IDEAS_PROMPT.format(
            name=self.profile.name,
            n=n,
            context="\n".join(context_lines),
            language="the same language as the context above"
            if language in ("", "auto")
            else language,
        )
        with llm_session.run("feed"):
            response = await self.app.llm.ask_complete([Message.user(prompt)], tools=None)
        ideas = _parse_ideas(response.content or "")
        data = {
            "generated_at": now_iso(),
            "source": "model" if ideas else "starter",
            "ideas": ideas or STARTER_IDEAS,
        }
        self._ideas_file().write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        self.bus.publish({"kind": "ideas", "ideas": data})
        return data

    # ------------------------------------------------------------------ feed posts
    def _feed_file(self) -> Path:
        return self.data_dir / "feed_posts.json"

    def feed_posts(self) -> dict[str, Any]:
        """The posts written for the user so far, newest first, their feed instructions, and
        the daily routine: ``daily`` (on by default) at ``time`` (``HH:MM``, local; 08:00)."""
        path = self._feed_file()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text("utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, json.JSONDecodeError):
                pass
        data.setdefault("instructions", "")
        data.setdefault("generated_at", None)
        data.setdefault("posts", [])
        data["daily"] = data.get("daily") is not False
        data["time"] = _feed_time(data.get("time")) or FEED_DEFAULT_TIME
        return data

    def _save_feed(self, data: dict[str, Any]) -> None:
        data["posts"] = data["posts"][:FEED_KEEP]
        self._feed_file().write_text(json.dumps(data, ensure_ascii=False), "utf-8")

    def set_feed_instructions(
        self, text: str | None = None, *, daily: bool | None = None, time: str | None = None
    ) -> dict[str, Any]:
        """The feed's preferences: what to write about, and the daily routine (on or off, and
        when). A ``time`` that is not ``HH:MM`` is refused with :class:`ValueError`."""
        data = self.feed_posts()
        if text is not None:
            data["instructions"] = text.strip()[:2000]
        if daily is not None:
            data["daily"] = bool(daily)
        if time is not None:
            clean = _feed_time(time)
            if not clean:
                raise ValueError("time must be HH:MM")
            data["time"] = clean
        self._save_feed(data)
        self.bus.publish({"kind": "feed_posts"})
        return data

    def feed_scheduled_at(self, now: datetime | None = None) -> datetime:
        """The most recent moment the daily routine was due (today's ``time`` once it has
        passed, yesterday's before that), in local time."""
        now = now or datetime.now().astimezone()
        hour, minute = (int(p) for p in self.feed_posts()["time"].split(":"))
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return today if today <= now else today - timedelta(days=1)

    def first_feed_day(self) -> bool:
        """Right after the first conversation (the app set up, a model there), the first feed
        day is written in the background — a person opening the Feed finds it filled rather
        than a promise. Nothing when posts exist already or no model can be reached; the
        model's answer never counts as a task (contract C1: a background run)."""
        s = self.settings_view()
        if not s["llm_ready"] or self.feed_posts()["posts"] or self._writing_feed:
            return False

        async def write() -> None:
            try:
                await self.write_feed_posts()
            except Exception as exc:  # noqa: BLE001 — the daily routine tries again
                logger.info("first feed day not written: {}", exc)

        try:
            spawn(write(), "first feed day")
        except RuntimeError:  # pragma: no cover - no loop (a direct call outside the server)
            return False
        return True

    def delete_feed_post(self, post_id: str) -> bool:
        data = self.feed_posts()
        before = len(data["posts"])
        data["posts"] = [p for p in data["posts"] if p.get("id") != post_id]
        if len(data["posts"]) == before:
            return False
        self._save_feed(data)
        self.bus.publish({"kind": "feed_posts"})
        return True

    def feed_posts_due(self) -> bool:
        """A new feed day is due when the daily routine is on, its time has passed since the
        last batch, and there is something to write from (the first conversation done,
        memory, a goal, or instructions from the user)."""
        if self._writing_feed:
            return False
        data = self.feed_posts()
        if not data["daily"]:
            return False
        known = (
            bool(data["instructions"])
            or bool(self.app.goals.list("active"))
            or bool(self.connections.data.get("onboarded"))
        )
        if not known and self.app.memory is not None:
            known = self.app.memory.count() >= 5
        if not known:
            return False
        last = data.get("generated_at")
        if not last:
            return True
        try:
            written = datetime.fromisoformat(last)
        except ValueError:
            return True
        if written.tzinfo is None:
            written = written.replace(tzinfo=UTC)
        if datetime.now(UTC) - written < timedelta(hours=FEED_MIN_GAP_HOURS):
            return False  # "Write it now" shortly before the hour is today's batch
        return written < self.feed_scheduled_at()

    def _feed_context(self) -> list[str]:
        lines: list[str] = []
        if self.settings.agent.user_profile.strip():
            lines.append(f"- profile: {self.settings.agent.user_profile.strip()[:500]}")
        if self.profile.user_name:
            lines.append(f"- the user's name: {self.profile.user_name}")
        if self.app.memory is not None:
            for m in self.app.memory.all(limit=40):
                lines.append(f"- memory ({m.category}): {m.content}")
        for g in self.app.goals.list("active")[:8]:
            nxt = g.next_step
            lines.append(
                f"- goal: {g.title} (progress {g.progress}"
                + (f", next: {nxt.title}" if nxt else "")
                + ")"
            )
        cal = self.app.calendar
        if cal.configured:
            today = datetime.now().astimezone().date()
            if agenda := cal.agenda(today)[:8]:
                lines.append("- today on the calendar:\n" + cal.render(agenda, today))
        main = self.threads.get(MAIN_THREAD)
        if main is not None:
            recent = [e for e in main.timeline.events if e.get("type") in ("user", "assistant")][
                -6:
            ]
            for e in recent:
                lines.append(f"- recent {e['type']}: {str(e.get('text', ''))[:200]}")
        lines.append(f"- today's date: {datetime.now().strftime('%A %d %B %Y')}")
        return lines

    def feed_language(self) -> str:
        """The language the feed is written in, for the prompt. A reply language set under
        Settings wins; else the language of the person's screens, as the main chat last heard
        it from a client or as Start was pressed in the first conversation (a batch written
        the moment the setup ends has no reply of the person's yet to take the language
        from); else whatever the context is written in."""
        fixed = self.settings.agent.language
        if fixed not in ("", "auto"):
            return fixed
        main = self.threads.get(MAIN_THREAD)
        ui = prompts.language_name(main.agent.ui_language) if main is not None else ""
        if not ui:
            ui = prompts.language_name(self.firstrun.state.lang)
        return ui or "the same language as the context above"

    async def write_feed_posts(self, n: int = 3) -> dict[str, Any]:
        """Write a fresh batch of posts and notify the phone once about the first."""
        if self._writing_feed:
            return self.feed_posts()
        self._writing_feed = True
        try:
            data = self.feed_posts()
            prompt = FEED_PROMPT.format(
                name=self.profile.name,
                n=n,
                instructions=data["instructions"]
                or "(none yet; write what a good personal agent would)",
                context="\n".join(self._feed_context()),
                language=self.feed_language(),
            )
            with llm_session.run("feed"):
                response = await self.app.llm.ask_complete([Message.user(prompt)], tools=None)
            posts = _parse_posts(response.content or "")
            now = now_iso()
            for p in posts:
                p["id"] = new_id("post")
                p["ts"] = now
            data["posts"] = [*posts, *data["posts"]]
            data["generated_at"] = now
            self._save_feed(data)
            self.bus.publish({"kind": "feed_posts"})
            if posts:
                self.push.notify(
                    f"{self.profile.name} · {posts[0]['title']}",
                    _first_lines(posts[0]["body"]),
                    tag="feed-posts",
                    url=self._push_url(MAIN_THREAD).split("?")[0] + "?tab=feed",
                    kind="background",
                )
            return data
        finally:
            self._writing_feed = False

    # ------------------------------------------------------------------ files / artifacts
    def workspace(self) -> Path:
        return self.settings.agent.workspace.resolve()

    def resolve_workspace_path(self, rel: str) -> Path:
        ws = self.workspace()
        target = (ws / rel).resolve()
        if target != ws and ws not in target.parents:
            raise PermissionError("path outside the workspace")
        return target

    def list_files(self, limit: int = 200) -> list[dict[str, Any]]:
        ws = self.workspace()
        out: list[dict[str, Any]] = []
        if not ws.exists():
            return out
        for p in ws.rglob("*"):
            if not p.is_file() or any(part.startswith(".") for part in p.relative_to(ws).parts):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            out.append(
                {
                    "path": p.relative_to(ws).as_posix(),
                    "name": p.name,
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(
                        timespec="seconds"
                    ),
                }
            )
            if len(out) >= 5000:
                break
        out.sort(key=lambda f: f["modified"], reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------ feed / upcoming
    def feed(self, limit: int = 60) -> list[dict[str, Any]]:
        """What happened without you asking, newest first: one entry per background pass
        (its final reply, plus any file it made) and every card still waiting for you."""
        items: list[dict[str, Any]] = []
        for t in self.threads.values():
            items.extend(self._feed_of(t))
        items.sort(key=lambda i: i["ts"], reverse=True)
        return items[:limit]

    def _feed_of(self, t: Thread) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []

        def item(ev: dict[str, Any], kind: str, title: str, text: str) -> dict[str, Any]:
            return {
                "id": ev["id"],
                "ts": ev["ts"],
                "kind": kind,
                "title": title,
                "text": text,
                "thread": t.id,
                "thread_title": t.title,
                "path": ev.get("path"),
                # a pass that found nothing worth interrupting you for
                "quiet": bool(ev.get("quiet")),
            }

        # A background pass is the notice that starts it followed by everything the agent
        # says or makes until you speak. Only its last word goes to the Feed — the
        # step-by-step narration stays in the chat.
        run: dict[str, Any] | None = None

        def flush() -> None:
            if run is None:
                return
            last = run["said"][-1] if run["said"] else None
            if last is not None:
                out.append(item(last, "background", run["label"], last.get("text", "")))
            elif t.busy and run is runs[-1]:
                out.append(item(run["start"], "background", run["label"], "Working on it…"))
            for art in run["made"]:
                out.append(item(art, "artifact", f"Made {art.get('name', 'a file')}", run["label"]))

        runs: list[dict[str, Any]] = []
        for ev in t.timeline.events:
            kind, src = ev.get("type"), ev.get("source")
            if kind == "approval":
                if ev.get("status") == "pending":
                    out.append(
                        item(ev, "approval", "Waiting for your approval", ev.get("summary", ""))
                    )
                continue
            if kind == "question":
                if ev.get("status") == "pending":
                    out.append(
                        item(
                            ev,
                            "question",
                            f"{self.profile.name} has a question",
                            ev.get("text", ""),
                        )
                    )
                continue
            if kind == "notice" and src not in (None, "background"):
                flush()
                run = {"label": ev.get("text", ""), "start": ev, "said": [], "made": []}
                runs.append(run)
            elif src == "background":
                if run is None:
                    run = {
                        "label": ev.get("about") or "While you were away",
                        "start": ev,
                        "said": [],
                        "made": [],
                    }
                    runs.append(run)
                if kind == "artifact":
                    run["made"].append(ev)
                elif kind in ("assistant", "notice"):
                    run["said"].append(ev)
            elif kind == "user":
                flush()
                run = None
        flush()
        return out

    def upcoming(self) -> dict[str, Any]:
        """What is scheduled: the next background pass and the goals it would work on."""
        active = [g for g in self.app.goals.list("active") if g.next_step is not None]
        active.sort(key=lambda g: (not g.overdue, g.updated_at))
        queue = [
            {
                "goal_id": g.id,
                "title": g.title,
                "category": g.category,
                "due": g.due or None,
                "overdue": g.overdue,
                "next_step": g.next_step.title if g.next_step else None,
                "progress": {
                    "done": sum(1 for s in g.steps if s.status in ("done", "skipped")),
                    "total": len(g.steps),
                },
            }
            for g in active
        ]
        check_ins = sorted(
            (
                {"goal_id": g.id, "title": g.title, "at": g.next_check_in, "cadence": g.check_in}
                for g in self.app.goals.list("active")
                if g.next_check_in
            ),
            key=lambda c: c["at"],
        )
        quiet_until = self.profile.quiet_hours_end()
        return {
            "check_ins": check_ins,
            "reminders": [r.to_dict() for r in self.app.reminders.list(None)],
            "triggers": self.triggers_view(),
            "proactive": self.profile.proactive,
            "proactivity": self.profile.proactivity,
            "effective_level": self.profile.effective_level,
            "auto": {**self.auto_proactivity.stats(), "factor": self.profile.auto_factor},
            "interval_minutes": self.profile.goal_interval_minutes,
            "effective_interval_minutes": self.profile.interval_seconds // 60,
            "quiet_hours": self.profile.quiet_hours,
            "quiet_until": quiet_until.isoformat(timespec="seconds") if quiet_until else None,
            "next_pass_at": (
                self.next_goal_pass_at.isoformat(timespec="seconds")
                if self.next_goal_pass_at and self.profile.proactive
                else None
            ),
            "next_wake_at": (
                wake.isoformat(timespec="seconds") if (wake := self.next_wake_at()) else None
            ),
            "queue": queue,
            "busy": any(t.busy for t in self.threads.values()),
        }

    # ------------------------------------------------------------------ state snapshots
    def activity(self, n: int = 100) -> dict[str, Any]:
        s = self.app.sentinel
        s.grants.purge_expired()
        return {
            "audit": self.app.audit.tail(n),
            "grants": [g.to_dict() for g in s.active_grants()],
            "tainted": s.tainted_any(),
        }

    def model_choice(self) -> str:
        """The sticky model pick, one value for the account: read from the app settings
        so it survives restarts and reaches every device ("" = the configured model)."""
        from nanomuse.config import load_app_settings

        value = load_app_settings(self.data_dir).get("agent", {}).get("model_choice", "")
        return str(value)[:30] if value else ""

    def background_model(self) -> str:
        """The model that runs the quiet background work (routines, goal passes, feed
        work): the Settings pick first, the config's ``background_model`` as the fallback,
        "" means the configured model runs everything."""
        from nanomuse.config import load_app_settings

        value = load_app_settings(self.data_dir).get("agent", {}).get("background_model", "")
        if not value:
            value = self.settings.llm.background_model
        return str(value)[:30] if value else ""

    async def request_lock_reset(self, device_id: str = "") -> dict[str, Any]:
        """The lock's rescue hatch: one code, delivered to the owner's own inbox when the
        e-mail connector is configured, a text to the owner's phone when one is named and
        reachable, and the terminal either way."""
        info = self.app_lock.request_reset(device_id)
        code = str(info["code"])
        sent_to = ""
        detail = ""
        try:
            mail_tool = self.app.tools.get("send_email")
            email = self.settings.connectors.email
            if email.enabled and email.smtp_host and mail_tool is not None:
                try:
                    to = mail_tool._creds()[0]  # the account's own address is the owner
                except Exception:
                    to = ""
                if to:
                    res = await mail_tool.execute(
                        to=to,
                        subject="Your MiniMuse lock reset code",
                        body=(
                            f"Your MiniMuse reset code is {code}. It is valid for 30 "
                            "minutes and works once. Open MiniMuse, tap Forgot the PIN, "
                            "and enter it to set a new PIN."
                        ),
                    )
                    if res.ok:
                        sent_to = "email"
            if not sent_to and self.settings.server.owner_phone:
                bridge = self.app.tools.get("phone_bridge")
                if bridge is not None:
                    sent = await bridge._send_sms_directly(  # noqa: SLF001
                        self.settings.server.owner_phone,
                        f"MiniMuse reset code: {code} (valid for 30 minutes)",
                    )
                    if sent is not None and sent.ok:
                        sent_to = "sms"
        except Exception as exc:  # noqa: BLE001 - delivery is a nicety; the terminal stands in
            logger.warning("lock reset delivery failed: {}", exc)
        if not sent_to:
            print(
                f"\nMiniMuse lock reset code: {code}  (valid for 30 minutes)\n",
                flush=True,
            )
            code_path = self.data_dir / "lock-reset-code.txt"
            try:
                from nanomuse.private import keep_private

                code_path.parent.mkdir(parents=True, exist_ok=True)
                code_path.write_text(
                    f"{code}\n# MiniMuse App Lock reset code (valid for 30 minutes)\n",
                    encoding="utf-8",
                )
                keep_private(code_path)
            except Exception:
                pass
            sent_to = "terminal"
            detail = (
                f"Reset code saved to {code_path} (and printed in the MiniMuse terminal if open)."
            )
        return {"sent_to": sent_to, "expires_in": info["expires_in"], "detail": detail}

    def _ui_view(self) -> dict[str, str] | None:
        from nanomuse.config import load_app_settings

        ui = load_app_settings(self.data_dir).get("ui")
        if not isinstance(ui, dict):
            # nothing was ever picked: None, so a device that still holds a local
            # choice pushes it up instead of the empty value wiping its colours
            return None
        return {
            "accent": str(ui.get("accent") or "")[:20],
            "bubble": str(ui.get("bubble") or "")[:20],
            "theme": str(ui.get("theme") or "")[:10],
            "bg": str(ui.get("bg") or "")[:20],
            "scale": str(ui.get("scale") or "")[:10],
        }

    def _set_model_choice(self, choice: str) -> None:
        from nanomuse.config import load_app_settings, save_app_settings

        stored = load_app_settings(self.data_dir)
        stored["agent"] = {**stored.get("agent", {}), "model_choice": choice}
        save_app_settings(self.data_dir, stored)

    def cloud_mode(self) -> str:
        """How nanoMuse routes AI & sync across local vs. nanoMuse Cloud:
        'local' (all local, no sign-in required), 'hybrid' (per-slot toggle), or 'cloud' (all nanoMuse Cloud)."""
        from nanomuse.config import load_app_settings

        val = str(load_app_settings(self.data_dir).get("cloud_mode") or "hybrid").strip().lower()
        return val if val in ("local", "hybrid", "cloud") else "hybrid"

    def cloud_slots(self) -> dict[str, str]:
        """Per-feature routing when cloud_mode == 'hybrid': each of 'chat', 'media', 'sync' is 'local' or 'cloud'."""
        from nanomuse.config import load_app_settings

        raw = load_app_settings(self.data_dir).get("cloud_slots")
        raw = raw if isinstance(raw, dict) else {}
        mode = self.cloud_mode()
        if mode == "local":
            return {"chat": "local", "media": "local", "sync": "local"}
        if mode == "cloud":
            return {"chat": "cloud", "media": "cloud", "sync": "cloud"}
        return {
            "chat": "local" if raw.get("chat") == "local" else "cloud",
            "media": "local" if raw.get("media") == "local" else "cloud",
            "sync": "local" if raw.get("sync") == "local" else "cloud",
        }

    def cloud_prompts(self) -> bool:
        """Whether optional MiniMuse Cloud sign-in prompts/cards appear across Settings,
        Connections, and Devices. Off in 'local' mode and off by default in 'hybrid' (custom)."""
        from nanomuse.config import load_app_settings

        mode = self.cloud_mode()
        if mode == "local":
            return False
        stored = load_app_settings(self.data_dir)
        if "cloud_prompts" in stored and stored["cloud_prompts"] is not None:
            return bool(stored["cloud_prompts"])
        return mode == "cloud"

    def auto_update_enabled(self) -> bool:
        from nanomuse.config import load_app_settings

        stored = load_app_settings(self.data_dir).get("server")
        if isinstance(stored, dict) and "auto_update" in stored:
            return bool(stored["auto_update"])
        return bool(getattr(self.settings.server, "auto_update", True))

    def _effective_trash_days(self) -> int:
        from nanomuse.config import load_app_settings

        stored = load_app_settings(self.data_dir).get("server")
        if isinstance(stored, dict) and "trash_retention_days" in stored:
            try:
                return max(1, min(3650, int(stored["trash_retention_days"])))
            except (TypeError, ValueError):
                pass
        return max(1, min(3650, int(getattr(self.settings.server, "trash_retention_days", 365))))

    def settings_view(self) -> dict[str, Any]:
        s = self.settings
        skills = self.app.skills.status()
        passwords_count = 0
        if getattr(self.app, "passwords", None) is not None:
            try:
                passwords_count = len(self.app.passwords.list())
            except Exception:
                passwords_count = 0
        return {
            "version": __version__,
            "profile": self.profile.to_dict(),
            "sentinel": {
                "mode": s.sentinel.mode,
                "always_ask_tools": s.sentinel.always_ask_tools,
                "always_allow_tools": s.sentinel.always_allow_tools,
                "deny_tools": s.sentinel.deny_tools,
                "taint_tracking": s.sentinel.taint_tracking,
                "egress_allowlist": s.sentinel.egress_allowlist,
            },
            "sandbox": {
                "mode": s.sandbox.mode,
                "active": self.app.sandbox.active,
                "status": self.app.sandbox.status,
            },
            "llm": {
                "provider": s.llm.provider,
                "model": s.llm.model,
                "stream": s.llm.stream,
                # the model that runs the quiet background work when one is named
                "background_model": s.llm.background_model,
                # the account's model answers (Settings offers the own-key door then)
                "cloud": bool(
                    self.cloud_mode() != "local"
                    and s.llm.base_url
                    and s.llm.base_url.rstrip("/") == model_url(self.hub.cloud.base_url)
                ),
            },
            "cloud_mode": self.cloud_mode(),
            "cloud_slots": self.cloud_slots(),
            "cloud_prompts": self.cloud_prompts(),
            "app_lock": self.app_lock.summary(),
            "passwords_count": passwords_count,
            "local_devices": self.local_devices_view(),
            "agent": {
                "language": s.agent.language,
                "max_steps": s.agent.max_steps,
                "auto_continue": s.agent.auto_continue,
                "max_continues": s.agent.max_continues,
                "max_context_messages": s.agent.max_context_messages,
                "max_context_tokens": s.agent.max_context_tokens,
                "max_context_images": s.agent.max_context_images,
                "show_thinking": s.agent.show_thinking,
                "allow_self_improve": s.agent.allow_self_improve,
                "workspace": str(s.agent.workspace),
            },
            # the sticky model pick (Settings -> the header's model menu): "" means the
            # configured model answers. One value for the account, so picking Pro on the
            # phone is Pro on the PC too.
            "model_choice": self.model_choice(),
            # the look choices, one value for every device ("" = the app's own look)
            "ui": self._ui_view(),
            # what each device's capabilities may do (always / ask / never), set from
            # either device's Settings
            "permissions": self._permissions_view(),
            "connectors": {
                "email": s.connectors.email.enabled,
                "calendar": s.connectors.calendar.enabled and bool(s.connectors.calendar.feeds),
                "contacts": s.connectors.contacts.enabled and self.app.contacts.configured,
                "browser": s.browser.enabled,
                "gui": s.gui.enabled,
                "hands": s.hands.enabled,
                "mcp": [m.name for m in s.mcp.servers],
            },
            "phone": self.phone_view(),
            "browser": {"backend": self.browser_backend()},
            "chrome": self.chrome_bridge.status(),
            "tools": [
                {"name": t.name, "risk": t.risk.value, "description": t.description[:160]}
                for t in self.app.tools
            ],
            "memory_enabled": s.memory.enabled,
            "skills": {
                "enabled": s.skills.enabled,
                "count": skills["count"],
                "yours": skills["yours"],
            },
            "data_dir": str(self.data_dir),
            "started_at": self.started_at,
            "onboarded": bool(self.connections.data.get("onboarded")),
            "llm_ready": llm_ready(s.llm, self.app.vault, self.data_dir),
            "auto_update": self.auto_update_enabled(),
            "trash_retention_days": self._effective_trash_days(),
        }

    def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        if "profile" in data and isinstance(data["profile"], dict):
            self.update_profile(data["profile"])
        if (mode := data.get("sentinel_mode")) in ("ask", "task", "strict", "auto"):
            self.settings.sentinel.mode = mode
            if mode == "auto":
                for aid in list(self.ui.pending_approvals.keys()):
                    self.ui.resolve_approval(
                        aid, True, "always", "auto-approved (Hands-off mode enabled)"
                    )
                for t in self.threads.values():
                    for ev in t.timeline.events:
                        if ev.get("type") == "approval" and ev.get("status") == "pending":
                            ev_id = str(ev.get("id") or "")
                            if ev_id:
                                self.ui.patch(
                                    t.id,
                                    ev_id,
                                    status="approved",
                                    scope="always",
                                    decided_ts=now_iso(),
                                )
        if "cloud_mode" in data or "cloud_slots" in data or "cloud_prompts" in data:
            from nanomuse.config import load_app_settings, save_app_settings

            stored = load_app_settings(self.data_dir)
            cloud_cfg = dict(stored.get("cloud") or {})
            slots = stored.get("cloud_slots")
            slots = dict(slots) if isinstance(slots, dict) else {}
            if isinstance(data.get("cloud_slots"), dict):
                for k in ("chat", "media", "sync"):
                    if data["cloud_slots"].get(k) in ("local", "cloud"):
                        slots[k] = data["cloud_slots"][k]
                stored["cloud_slots"] = slots
            if (cm := data.get("cloud_mode")) in ("local", "hybrid", "cloud"):
                stored["cloud_mode"] = cm
                if cm == "local":
                    stored["cloud_slots"] = {"chat": "local", "media": "local", "sync": "local"}
                    stored["cloud_prompts"] = False
                    cloud_cfg.update({"required": False, "models": False, "sync": False})
                    self.settings.cloud.required = False
                    self.settings.cloud.models = False
                    self.settings.cloud.sync = False
                    # When switching to Nothing / All-Local mode, default model_choice to local if it was a cloud tier
                    cur_choice = str(stored.get("agent", {}).get("model_choice") or "")
                    if cur_choice in ("flash", "deepseek", "pro", "qwen", "gemini"):
                        stored["agent"] = {**stored.get("agent", {}), "model_choice": "local"}
                elif cm == "cloud":
                    stored["cloud_slots"] = {"chat": "cloud", "media": "cloud", "sync": "cloud"}
                    stored["cloud_prompts"] = True
                    cloud_cfg.update({"required": False, "models": True, "sync": True})
                    self.settings.cloud.required = False
                    self.settings.cloud.models = True
                    self.settings.cloud.sync = True
                else:
                    stored.setdefault("cloud_prompts", False)
                    eff_chat = slots.get("chat", "cloud")
                    eff_media = slots.get("media", "cloud")
                    eff_sync = slots.get("sync", "cloud")
                    models_on = eff_chat == "cloud" or eff_media == "cloud"
                    sync_on = eff_sync == "cloud"
                    cloud_cfg.update({"required": False, "models": models_on, "sync": sync_on})
                    self.settings.cloud.required = False
                    self.settings.cloud.models = models_on
                    self.settings.cloud.sync = sync_on
            elif isinstance(data.get("cloud_slots"), dict) and stored.get("cloud_mode", "hybrid") == "hybrid":
                eff_chat = slots.get("chat", "cloud")
                eff_media = slots.get("media", "cloud")
                eff_sync = slots.get("sync", "cloud")
                models_on = eff_chat == "cloud" or eff_media == "cloud"
                sync_on = eff_sync == "cloud"
                cloud_cfg.update({"models": models_on, "sync": sync_on})
                self.settings.cloud.models = models_on
                self.settings.cloud.sync = sync_on
            if "cloud_prompts" in data and data["cloud_prompts"] is not None:
                stored["cloud_prompts"] = bool(data["cloud_prompts"])
            if cloud_cfg:
                stored["cloud"] = cloud_cfg
            save_app_settings(self.data_dir, stored)
            if getattr(self, "hub", None) is not None:
                try:
                    self.hub.publish()
                except Exception:
                    pass
        if "model_choice" in data:
            # the sticky pick, one value for every device of the account; an empty
            # string is a real value (back on the configured model)
            choice = str(data["model_choice"] or "")[:30]
            if choice not in ("", "flash", "deepseek", "pro", "local", "qwen", "gemini"):
                choice = ""
            self._set_model_choice(choice)
        if "background_model" in data:
            # the model that runs the quiet background work; an empty string is a real
            # value (back on the configured model)
            from nanomuse.config import load_app_settings, save_app_settings

            choice = str(data["background_model"] or "")[:30]
            if choice not in ("", "flash", "deepseek", "pro", "local", "qwen", "gemini"):
                choice = ""
            stored = load_app_settings(self.data_dir)
            stored["agent"] = {**stored.get("agent", {}), "background_model": choice}
            save_app_settings(self.data_dir, stored)
        if "ui" in data and isinstance(data["ui"], dict):
            from nanomuse.config import load_app_settings, save_app_settings

            stored = load_app_settings(self.data_dir)
            ui = {**stored.get("ui", {}), **{str(k): str(v) for k, v in data["ui"].items()}}
            save_app_settings(self.data_dir, {**stored, "ui": ui})
        if "permissions" in data and isinstance(data["permissions"], dict):
            # one policy for the account: what each device's capabilities may do
            from nanomuse.config import load_app_settings, save_app_settings

            stored = load_app_settings(self.data_dir)
            policy = stored.get("permissions")
            policy = policy if isinstance(policy, dict) else {}
            for device, caps in data["permissions"].items():
                if device not in self.CAPABILITIES or not isinstance(caps, dict):
                    continue
                rows = policy.get(device) if isinstance(policy.get(device), dict) else {}
                for cap, mode in caps.items():
                    if cap in {c for c, _label in self.CAPABILITIES[device]} and mode in (
                        "always",
                        "ask",
                        "never",
                    ):
                        rows[cap] = mode
                policy[device] = rows
            save_app_settings(self.data_dir, {**stored, "permissions": policy})
            self._apply_permission_policy()
        if "show_thinking" in data:
            self.settings.agent.show_thinking = bool(data["show_thinking"])
            self.ui.show_thinking = bool(data["show_thinking"])
        if "allow_self_improve" in data:
            self.settings.agent.allow_self_improve = bool(data["allow_self_improve"])
        if (lang := data.get("language")) is not None:
            self.settings.agent.language = str(lang)[:20] or "auto"
        agent_patch: dict[str, Any] = {}
        sentinel_patch: dict[str, Any] = {}
        if (mode := data.get("sentinel_mode")) in ("ask", "task", "strict", "auto"):
            sentinel_patch["mode"] = mode

        def _agent_int(key: str, minimum: int) -> None:
            if (value := data.get(key)) is not None and isinstance(value, int) and value >= minimum:
                setattr(self.settings.agent, key, value)
                agent_patch[key] = value

        _agent_int("max_steps", 1)
        _agent_int("max_continues", 0)
        _agent_int("max_context_messages", 1)
        # 0 is meaningful here: no cap at all
        _agent_int("max_context_tokens", 0)
        _agent_int("max_context_images", 0)
        if "auto_continue" in data and data["auto_continue"] is not None:
            self.settings.agent.auto_continue = bool(data["auto_continue"])
            agent_patch["auto_continue"] = bool(data["auto_continue"])
        for key in ("always_ask_tools", "always_allow_tools"):
            if isinstance(data.get(key), list):
                setattr(self.settings.sentinel, key, [str(t) for t in data[key]])
                sentinel_patch[key] = [str(t) for t in data[key]]
        if agent_patch or sentinel_patch:
            from nanomuse.config import load_app_settings, save_app_settings

            stored = load_app_settings(self.data_dir)
            if agent_patch:
                stored["agent"] = {**stored.get("agent", {}), **agent_patch}
            if sentinel_patch:
                stored["sentinel"] = {**stored.get("sentinel", {}), **sentinel_patch}
            save_app_settings(self.data_dir, stored)
        server_patch: dict[str, Any] = {}
        if "auto_update" in data and data["auto_update"] is not None:
            val = bool(data["auto_update"])
            self.settings.server.auto_update = val
            server_patch["auto_update"] = val
        if "trash_retention_days" in data and data["trash_retention_days"] is not None:
            try:
                days = max(1, min(3650, int(data["trash_retention_days"])))
                self.settings.server.trash_retention_days = days
                server_patch["trash_retention_days"] = days
            except (TypeError, ValueError):
                pass
        if server_patch:
            from nanomuse.config import load_app_settings, save_app_settings

            stored = load_app_settings(self.data_dir)
            stored["server"] = {**stored.get("server", {}), **server_patch}
            save_app_settings(self.data_dir, stored)
        self.bus.publish({"kind": "settings", "settings": self.settings_view()})
        return self.settings_view()

    def state(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "profile": self.profile.to_dict(),
            "status": self.ui.overall_status(),
            "streams": self.ui.active_streams_view(),
            # the signed-in account's conversations and the ownerless ones (C10)
            "threads": [t.meta() for t in self.visible_threads()],
            "pending_approvals": [
                ev
                for t in self.visible_threads()
                for ev in t.timeline.events
                if ev.get("type") == "approval" and ev.get("status") == "pending"
            ],
            "goals": [goal_to_dict(g) for g in self.app.goals.list()],
            "settings": self.settings_view(),
            "phone": self.phone_view(),
            # the phone this server runs on (the local runtime), or null on a computer
            "device": self.app.device.to_dict() if self.app.device is not None else None,
            # this device on the hub: the account, the other devices
            "hub": self.hub.view(),
            # this computer's own screen and hands
            "hands": self.hands_view(),
            "holds": self.holds_view(),
            # the other devices' turns under way on synced chats (C9), for the line under
            # a message written elsewhere; `working` frames keep it current
            "working": self.sync.working_view(),
            "trash_count": len(self.list_trash()),
            # the first conversation: where it is bound, which phase, the chips (C4)
            "firstrun": self.firstrun_view(),
        }


def _tidy_summary(report: TidyReport, language: str = "auto") -> str:
    """The tidy-up as one chat message: what was merged and dropped, and where to undo it.

    Written in 中文 when that is the reply language, or when the language is "auto" and the
    memories themselves are Chinese; English otherwise (the two languages the app ships in).
    """
    n_m, n_d = len(report.merged), len(report.dropped)
    if language in ("", "auto"):
        text = " ".join(m.content for c in report.merged + report.dropped for m in c.before)
        zh = prompts.detect_language(text) == "Chinese"
    else:
        zh = language.lower().startswith(("中文", "zh", "chinese", "简体", "繁體"))
    if zh:
        parts = ([f"合并了 {n_m} 条"] if n_m else []) + ([f"删除了 {n_d} 条"] if n_d else [])
        head = "我整理了一下记忆，" + "，".join(parts) + "："
        tail = "每一处改动都可以在「记忆 → 最近的改动」里撤销。"
    else:
        parts = ([f"merged {n_m} line{'s' if n_m != 1 else ''}"] if n_m else []) + (
            [f"dropped {n_d}"] if n_d else []
        )
        head = "I tidied your memory: " + " and ".join(parts) + ":"
        tail = "Each change can be undone under Memory → Recent changes."
    body = "\n".join(f"- {line}" for line in report.lines(zh=zh))
    return f"{head}\n{body}\n\n{tail}"


def _fence(text: str) -> str:
    """A code fence the text cannot close: one backtick more than its longest run."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def _short(text: str, limit: int = 60) -> str:
    """One line of ``text`` for a label."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_lines(text: str, limit: int = 200) -> str:
    """The first sentence or two of a reply, without markdown, for a notification body."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def goal_to_dict(g: Goal) -> dict[str, Any]:
    done = sum(1 for s in g.steps if s.status in ("done", "skipped"))
    return {
        "id": g.id,
        "title": g.title,
        "description": g.description,
        "status": g.status,
        "notes": g.notes,
        "category": g.category,
        "kind": getattr(g, "kind", "goal") or "goal",
        "priority": getattr(g, "priority", "medium") or "medium",
        "tags": list(getattr(g, "tags", None) or []),
        "due": g.due,
        "due_time": getattr(g, "due_time", "") or "",
        "recurrence": getattr(g, "recurrence", "") or "",
        "sort_order": int(getattr(g, "sort_order", 0) or 0),
        "completed_at": getattr(g, "completed_at", "") or "",
        "overdue": g.overdue,
        "check_in": g.check_in,
        "next_check_in": g.next_check_in or None,
        "proposal": g.proposal,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
        "progress": {"done": done, "total": len(g.steps)},
        "next_step": g.next_step.title if g.next_step else None,
        "steps": [
            {
                "idx": s.idx,
                "title": s.title,
                "status": s.status,
                "note": s.note,
                "updated_at": s.updated_at,
            }
            for s in g.steps
        ],
    }


def _idea_objects(text: str) -> list[Any]:
    """The JSON objects in a model reply that should have been a JSON array.

    Usually it is one: ``[{...}, {...}]``, possibly inside a code fence. When the
    array does not parse — a real newline inside a string, a trailing comma, the
    reply cut off at max_tokens before the closing bracket — each ``{...}`` that
    parses on its own is kept, so one bad item does not cost the whole list.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1], strict=False)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder(strict=False)
    found: list[Any] = []
    pos = text.find("{")
    while pos >= 0:
        try:
            obj, stop = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            pos = text.find("{", pos + 1)
            continue
        found.append(obj)
        pos = text.find("{", stop)
    return found


def _parse_ideas(text: str) -> list[dict[str, str]]:
    ideas: list[dict[str, str]] = []
    for item in _idea_objects(text):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if title and prompt:
            area = str(item.get("area", "")).strip().lower()
            kind = str(item.get("kind", "")).strip().lower()
            idea = {
                "title": title[:80],
                "detail": str(item.get("detail", "")).strip()[:300],
                "prompt": prompt[:1000],
                "area": area if area in IDEA_AREAS else "fun",
                "kind": kind if kind in IDEA_KINDS else "chat",
            }
            if idea["kind"] == "routine":
                when = str(item.get("time", "")).strip()
                idea["time"] = when if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", when) else "09:00"
            elif idea["kind"] == "goal":
                category = str(item.get("category", "")).strip().lower()
                idea["category"] = category if category in IDEA_GOAL_CATEGORIES else "other"
            ideas.append(idea)
    return ideas[:8]


def _parse_posts(text: str) -> list[dict[str, str]]:
    posts: list[dict[str, str]] = []
    for item in _idea_objects(text):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        body = str(item.get("body", "")).strip()
        if title and body:
            area = str(item.get("area", "")).strip().lower()
            posts.append(
                {
                    "title": title[:120],
                    "body": body[:2000],
                    "area": area if area in IDEA_AREAS else "fun",
                    "prompt": str(item.get("prompt", "")).strip()[:1000],
                }
            )
    return posts[:6]


__all__ = ["MuseService", "Profile", "Thread", "goal_to_dict", "new_id"]


RESTART_HELPER = '''"""Wait for the nanoMuse port to free, then start the server again with the arguments
after host and port. Written by MuseService.schedule_restart and run detached, so the
server can restart itself without a person at the terminal."""

import socket
import subprocess
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
cmd = sys.argv[3:]

deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((host if host else "127.0.0.1", port)) != 0:
            break
    time.sleep(0.5)

flags = 0
if sys.platform == "win32":
    # no console window: a restart must not flash a command prompt at the person
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
subprocess.Popen(cmd, creationflags=flags, close_fds=True)
'''
