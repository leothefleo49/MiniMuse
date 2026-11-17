"""HTTP + WebSocket API for the nanoMuse app (and anything else that wants to talk to your nanoMuse).

    GET  /api/state                      snapshot: profile, status, threads, goals, settings
    GET  /api/threads                    list threads          POST /api/threads {title}
    GET  /api/threads/{id}/events        timeline (?limit&before)
    POST /api/threads/{id}/send {text}   queue a message (non-blocking)
    POST /api/approvals/{id} {approved, scope, reason}
    DELETE /api/approvals                 forget every granted permission
    DELETE /api/approvals/grants/{key}    revoke one (key = "tool" or "tool:target")
    GET  /api/goals  POST /api/goals  GET|PATCH|DELETE /api/goals/{id}  POST /api/goals/{id}/advance|steps|check-in
    POST /api/goals/{id}/proposal/accept  DELETE /api/goals/{id}/proposal   the agent's plan change
    GET  /api/memory  POST /api/memory  DELETE /api/memory/{id}
    POST /api/memory/tidy (?dry_run=1)   GET /api/memory/changes   POST /api/memory/changes/{id}/restore
    GET  /api/ideas (?refresh=1)
    GET  /api/activity                   audit tail + approvals granted
    GET  /api/feed                        what happened without you asking
    GET  /api/feed/posts                  posts written for you, and your feed instructions
    PUT  /api/feed/instructions           what you want to read there
    POST /api/feed/posts/refresh          write a new batch now
    GET  /api/upcoming                    next background pass and the goals in line
    POST /api/tick                        run the scheduler's pass now (the phone's alarm)
    GET  /api/calendar (?days&refresh=1)  today's and tomorrow's events from the calendar feeds
    GET  /api/files  GET /api/files/{path}  POST /api/files/upload?name=  (body: the bytes)
    GET|PUT /api/settings
    GET  /api/connections                 model, email, browser, MCP servers, vault names
    PUT  /api/connections/llm|embeddings|email|browser|calendar   POST /api/connections/llm|embeddings|email|calendar/test
    PUT  /api/connections/gui             the hands' model (a protocol or a catalogue id, as for llm)   POST /api/connections/gui/test
    GET|PUT /api/connections/image|video  where pictures and clips come from: provider (a catalogue id), model, base_url, api_key
    POST /api/llm/models                 the models an endpoint offers (live /models, else the catalogue)
    POST /api/connections/endpoints {label,base_url,api_key,models}  PUT|DELETE /api/connections/endpoints/{id}
    POST /api/connections/endpoints/{id}/models   custom OpenAI-compatible endpoints, several at once (#294)
    GET  /api/providers (?lang=en|zh&region=cn|global)  the provider catalogue, which slots use which, what that covers
    POST /api/chatgpt/login  GET /api/chatgpt/status  GET /api/chatgpt/usage
    POST /api/chatgpt/callback {url}  POST /api/chatgpt/logout
    POST /api/connections/calendar/feeds {name,url}  DELETE /api/connections/calendar/feeds/{name}
    PUT  /api/connections/contacts {enabled}  POST /api/connections/contacts/sources {name,url}
    POST /api/connections/contacts/import?name= (body: the .vcf text)  DELETE /api/connections/contacts/sources/{name}
    POST /api/connections/contacts/test      GET /api/contacts?q=&limit=   look people up
    GET  /api/skills  GET /api/skills/{name}  PUT /api/skills/{name} {content}  DELETE /api/skills/{name}
    POST /api/skills/{name}/enabled {enabled}  POST /api/skills/import {url}
    POST /api/connections/mcp  DELETE /api/connections/mcp/{name}
    GET  /api/vault  PUT|DELETE /api/vault/{name}   (names only ever come back)
    POST /api/onboarded
    GET  /api/firstrun (?lang=en|zh)      the first conversation: phase, chips, the three opening lines
    POST /api/firstrun/start {lang}  POST /api/firstrun/pick {name}  POST /api/firstrun/dismiss
    GET  /api/nudges (?refresh=1)         when the app may ask for a star: the relay's policy, read once a day
    GET|PUT /api/sync/state {enabled}     conversations synced between the account's devices: the switch, the cursor, the relay's counts
    POST /api/sync/delete                 delete what the relay stores for the account (the switch stays)
    POST /api/sync/pull                   pull the other devices' changes now
    GET  /api/data/export                 the agent's data as one zip
    POST /api/data/import?name=           import a file (Muse export zip, transcript, nanoMuse export): the bytes as the body
    POST /api/data/import/text?name=      import pasted text: the text as the body
    GET  /api/update (?refresh=1)         installed and latest release (nanomuse.cn/dl first, GitHub second; a day's cache)
    GET  /api/avatar                      the avatar studio: can a face be drawn, the session under way
    POST /api/avatar/begin {description, style?}  a session: the card with the cost in the chat
    POST /api/avatar/start|choose|cancel {session, index?}   draw (or redraw) the four, pick one, stop
    POST /api/avatar/moods                the poses of the current face drawn again
    WS   /ws                              live events; the first frame is {"kind": "auth", "token": …}

All endpoints require ``Authorization: Bearer <token>`` unless ``server.auth = false``.
The token is printed (with a QR code, in the link's ``#fragment``) by ``nanomuse serve``.
The bytes the app shows inline — ``/api/files/*`` and the browser frames — also open with
a link the client signed with its token (``?exp=&sig=``, :mod:`nanomuse.server.tickets`),
so no token travels in a URL. ``?token=`` is no longer accepted (0.1.33): a request that
still carries one gets a 401 with a message saying where the token goes now.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.responses import PlainTextResponse

from nanomuse.avatar.studio import StudioError
from nanomuse.bridge.server import BridgeError
from nanomuse.cloud import CloudError
from nanomuse.coding.service import CodingError
from nanomuse.config import Settings
from nanomuse.fences import MAX_NAME
from nanomuse.hub.client import HubError
from nanomuse.llm.chatgpt import ChatGPTError
from nanomuse.logger import logger
from nanomuse.server import data_export, data_import, log_export, tickets, watch_discovery
from nanomuse.server.channels_api import install_channels
from nanomuse.server.chrome_bridge import build_extension_zip
from nanomuse.server.events import MAIN_THREAD
from nanomuse.server.ofa_api import install_ofa
from nanomuse.server.personal_cloud import PersonalCloudManager
from nanomuse.server.realtime import call_capabilities, handle_call_websocket, set_call_config
from nanomuse.server.release_notes import ReleaseNotesTracker
from nanomuse.server.selfupdate import SelfUpdate, cleanup_updates_dir, consume_swap_error
from nanomuse.server.service import MuseService, goal_to_dict
from nanomuse.server.spaces_service import install_spaces
from nanomuse.server.update import UpdateCheck

if TYPE_CHECKING:
    from starlette.types import Scope

STATIC_DIR = Path(__file__).parent / "static"
# Said to a client that still puts the token in the URL (taken until 0.1.32).
LEGACY_TOKEN_MESSAGE = (
    "the token no longer travels in the URL: send it as 'Authorization: Bearer …' "
    "(or as the socket's first frame); update the app, or pair again with a #token= link"
)


# ----------------------------------------------------------------------------- request models
class SendBody(BaseModel):
    id: str | None = None
    text: str = Field("", max_length=20_000)
    # workspace paths from POST /api/files/upload; a message may be attachments alone
    files: list[str] = Field(default_factory=list, max_length=10)
    # the language of the client's screens (a BCP-47 tag such as "en" or "zh-CN"); the reply
    # is written in it unless Settings fixes one. Optional: without it the message's script decides.
    language: str = Field("", max_length=20)
    # "cloud": *Use nanoMuse Cloud this time*; this one turn runs on the account's recommended
    # chat model, the next is back on the configured model. 401 `signed_out` when signed out.
    via: str = Field("", max_length=20)
    # the composer pill's pick for this one message ("flash", "pro", "local", "qwen",
    # "gemini"); it wins over any switch wording in the text. "" keeps the configured model.
    model: str = Field("", max_length=20)


class ThreadBody(BaseModel):
    title: str = ""


class FeedbackBody(BaseModel):
    """A thumb on a reply (``event`` + ``vote`` ``up``/``down``/"" to take it back, and
    ``reason`` when the person said: ``wrong``, ``unhelpful``, ``unsafe``, ``slow``,
    ``other``), or why Stop was pressed (``stop_reason``: ``wrong``, ``slow``,
    ``changed_mind``, ``done``, ``other``, ``skipped``). Either, not both."""

    event: str = Field("", max_length=64)
    vote: str = Field("", max_length=8)
    reason: str = Field("", max_length=16)
    stop_reason: str = Field("", max_length=16)


class FeedInstructionsBody(BaseModel):
    """The feed's preferences; only the fields given change (``daily``/``time`` are the
    daily routine, see ``MuseService.feed_posts``)."""

    instructions: str | None = None
    daily: bool | None = None
    time: str | None = None


class ApprovalBody(BaseModel):
    approved: bool
    scope: str = "once"
    reason: str = ""


class GoalBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    steps: list[str] = Field(default_factory=list)
    category: str = ""
    due: str = ""
    check_in: str = ""
    kind: str = "goal"
    priority: str = "medium"
    tags: list[str] | str = Field(default_factory=list)
    due_time: str = ""
    recurrence: str = ""


class GoalPatch(BaseModel):
    status: str | None = None
    note: str | None = None
    step_index: int | None = None
    step_status: str | None = None
    step_note: str | None = None
    step_title: str | None = None
    # the goal's own fields; "" clears due / check_in
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    category: str | None = None
    due: str | None = None
    check_in: str | None = None
    kind: str | None = None
    priority: str | None = None
    tags: list[str] | str | None = None
    due_time: str | None = None
    recurrence: str | None = None
    sort_order: int | None = None


class StepReorderBody(BaseModel):
    order: list[int] = Field(default_factory=list)


class StepBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class ReminderBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: str = "remind"
    at: str = ""
    repeat: str = ""
    thread: str = ""


class TriggerBody(BaseModel):
    kind: str = Field(pattern="^(mail|event|hook|device)$")
    text: str = Field(min_length=1, max_length=2000)
    match: str = Field(default="", max_length=200)
    lead_minutes: int = Field(default=30, ge=0, le=1440)
    thread: str = ""


class MemoryBody(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    category: str = "profile"


class SettingsBody(BaseModel):
    profile: dict[str, Any] | None = None
    sentinel_mode: str | None = None
    show_thinking: bool | None = None
    language: str | None = None
    # the working caps, editable live (no restart); a value <= 0 is ignored
    max_steps: int | None = Field(default=None, ge=0)
    auto_continue: bool | None = None
    max_continues: int | None = Field(default=None, ge=0)
    max_context_messages: int | None = Field(default=None, ge=0)
    max_context_tokens: int | None = Field(default=None, ge=0)
    max_context_images: int | None = Field(default=None, ge=0)
    # the sentinel lists, editable live; None keeps them, [] clears them
    always_ask_tools: list[str] | None = None
    always_allow_tools: list[str] | None = None
    # the sticky model pick, synced across the account's devices ("", flash, pro, local, gemini)
    model_choice: str | None = None
    # the model that runs the quiet background work ("", flash, pro, local, qwen, gemini)
    background_model: str | None = None
    # how AI & sync route across local vs. nanoMuse Cloud ("local", "hybrid", "cloud")
    cloud_mode: str | None = None
    cloud_slots: dict[str, str] | None = None
    cloud_prompts: bool | None = None
    # the look choices, synced across the account's devices
    ui: dict[str, str] | None = None
    # what each device's capabilities may do, one policy for the account
    permissions: dict[str, dict[str, str]] | None = None
    auto_update: bool | None = None
    trash_retention_days: int | None = Field(default=None, ge=1, le=3650)


class DeviceRenameBody(BaseModel):
    name: str = Field(default="", max_length=60)


class LLMBody(BaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    tool_mode: str | None = None
    # a new key goes straight into the vault; "" removes the key; None keeps it
    api_key: str | None = None
    # `[llm] proxy`: http(s):// or socks5(h):// for this slot's requests only; "" clears it
    proxy: str | None = None
    # one of the endpoint list (#294): the slot takes its address and its key; provider,
    # base_url and api_key in the body are then ignored
    endpoint: str | None = None
    model_options: list[Any] | None = None


class ModelOptionsBody(BaseModel):
    options: list[Any] = Field(default_factory=list)


class MediaBody(BaseModel):
    """One of the ``image`` / ``video`` slots. ``provider`` is a catalogue id, or ``openai``
    / ``openai_responses`` with a ``base_url``; ``model`` empty means the catalogue's default;
    ``api_key`` goes into the vault, "" removes it, None keeps it. All four "" clears the
    slot: pictures (clips) come from the chat provider again, or the account. ``endpoint``
    names one of the endpoint list instead of a provider (#294)."""

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    endpoint: str | None = None


class GuiBody(BaseModel):
    """The phone operator: its switch and its model. ``provider`` is a protocol or a
    catalogue id, as for the chat model; ``api_key`` goes into the vault, "" removes it,
    None keeps it; ``endpoint`` names one of the endpoint list instead (#294)."""

    enabled: bool | None = None
    provider: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4000)
    endpoint: str | None = Field(default=None, max_length=200)


class HandsBody(BaseModel):
    """This computer's own screen and hands: the switch, which backend drives them and
    whether the operator sees the whole screen or one window. An unknown ``backend`` or
    ``mode`` is 400 with the choices (checked in :meth:`Connections.set_hands`)."""

    enabled: bool | None = None
    backend: str | None = Field(default=None, max_length=40)
    mode: str | None = Field(default=None, max_length=40)


class LLMModelsBody(BaseModel):
    preset: str = ""
    base_url: str = ""
    api_key: str = ""
    # one of the endpoint list (#294): its address and its own key
    endpoint: str = ""


class EndpointBody(BaseModel):
    """A custom OpenAI-compatible endpoint of the list (#294). ``base_url`` is required to
    add one; ``label`` empty means the host name; ``api_key`` goes into the vault, "" means
    no key (a local server), None keeps it; ``models`` are the ids the person typed, for an
    endpoint that does not list its own (a string with commas, or a list)."""

    label: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=4000)
    models: str | list[str] | None = None


class ChatGptCallbackBody(BaseModel):
    # the full address the browser landed on: http://localhost:1455/auth/callback?code=…&state=…
    url: str = Field(min_length=1, max_length=4000)


class EmbeddingsBody(BaseModel):
    mode: str | None = None  # auto | on | off
    model: str | None = None  # "" → the default for the endpoint
    base_url: str | None = None  # "" → the model's endpoint
    api_key: str | None = None  # into the vault; "" → the model's key; None keeps it


class SearchBody(BaseModel):
    # duckduckgo | brave | tavily | serply | you | parallel | keenable | searxng
    provider: str | None = None
    api_key: str | None = None  # into the vault; "" removes it; None keeps it
    base_url: str | None = None  # SearXNG instance


class EmailBody(BaseModel):
    enabled: bool | None = None
    address: str | None = None
    password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_starttls: bool | None = None
    auth_mode: str | None = Field(default=None, pattern="^(password|oauth)$")


class GoogleOAuthStartBody(BaseModel):
    client_id: str | None = Field(default=None, max_length=500)
    client_secret: str | None = Field(default=None, max_length=500)


class GoogleOAuthCallbackBody(BaseModel):
    url: str = Field(min_length=1, max_length=4000)


class EmailAccountBody(BaseModel):
    id: str | None = None
    label: str = ""
    address: str = ""
    password: str | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_starttls: bool | None = None
    auth_mode: str | None = None
    enabled: bool | None = None
    primary: bool | None = None


class ApiConnectionBody(BaseModel):
    id: str | None = None
    preset_id: str | None = "custom"
    name: str = ""
    category: str | None = None
    base_url: str = ""
    auth_type: str = "bearer"
    auth_header: str = "Authorization"
    auth_param: str = "api_key"
    auth_prefix: str = "Bearer "
    secret: str | None = None
    headers: dict[str, str] | None = None
    ai_guide: str | None = None
    learned_notes: str | None = None
    docs_url: str | None = None
    test_endpoint: dict[str, str] | None = None
    endpoints: list[dict[str, Any]] | None = None
    enabled: bool | None = None


class ApiEndpointBody(BaseModel):
    id: str | None = None
    name: str = ""
    method: str = "GET"
    path: str = "/"
    description: str = ""
    query_params: list[str] = Field(default_factory=list)
    body_schema: dict[str, Any] = Field(default_factory=dict)
    learned_notes: str | None = None


class ApiRequestBody(BaseModel):
    api_id: str | None = None
    url: str | None = None
    method: str = "GET"
    path: str = ""
    headers: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    body: Any = None


class ApiDiscoverBody(BaseModel):
    spec_url: str = ""


class CalendarEventBody(BaseModel):
    summary: str = Field(min_length=1, max_length=300)
    start: str = Field(min_length=4, max_length=64)
    end: str = ""
    all_day: bool | None = None
    location: str = ""
    description: str = ""
    calendar: str = "Personal"
    tags: list[str] | str = Field(default_factory=list)
    contact_name: str = ""
    rrule: str = ""


class CalendarEventPatchBody(BaseModel):
    summary: str | None = Field(default=None, max_length=300)
    start: str | None = None
    end: str | None = None
    all_day: bool | None = None
    location: str | None = None
    description: str | None = None
    calendar: str | None = None
    tags: list[str] | str | None = None
    contact_name: str | None = None
    rrule: str | None = None


class PlaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    category: str = "other"
    tags: list[str] | str = Field(default_factory=list)
    context: str = ""
    contact_id: str = ""
    contact_name: str = ""
    favorite: bool = False
    visit_count: int = 1


class PlacePatchBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    category: str | None = None
    tags: list[str] | str | None = None
    context: str | None = None
    contact_id: str | None = None
    contact_name: str | None = None
    favorite: bool | None = None
    visit_count: int | None = None


class TripBody(BaseModel):
    place_id: str = ""
    place_name: str = ""
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    origin_name: str = ""
    origin_address: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_min: float = 0.0
    distance_mi: float = 0.0
    avg_speed_mph: float | None = None
    max_speed_mph: float | None = None
    current_speed_mph: float = 0.0
    mode: str = ""
    traffic_level: str = "unknown"
    traffic_note: str = ""
    tags: list[str] | str = Field(default_factory=list)
    notes: str = ""
    source: str = "manual"


class TrafficBody(BaseModel):
    origin: str = ""
    destination: str = Field(min_length=1, max_length=400)


class WatchInstallBody(BaseModel):
    target: str | None = Field(default=None, max_length=200)
    pair_host_port: str | None = Field(default=None, max_length=200)
    pair_code: str | None = Field(default=None, max_length=32)
    connect_host_port: str | None = Field(default=None, max_length=200)
    auto_pair_watch: bool = True
    grant_permissions: bool = True


class BrowserBody(BaseModel):
    enabled: bool


class UsageLimitBody(BaseModel):
    """One spending cap, as the Usage screen sends it; PUT replaces the whole list."""

    id: str | None = None
    name: str = ""
    scope: str = Field(default="global", pattern="^(global|model)$")
    model: str = ""
    unit: str = Field(default="cost", pattern="^(cost|tokens|requests)$")
    window: str = Field(default="month", pattern="^(day|week|month)$")
    amount: float = Field(gt=0)
    warning_pct: int = Field(default=80, ge=1, le=99)
    on_full: str = Field(default="stop", pattern="^(warn|stop|confirm)$")


class LockBody(BaseModel):
    pin: str = Field(default="", min_length=0, max_length=64)
    keep_biometric: bool = False
    timeout_seconds: int | None = None
    lock_on_leave: bool | None = None


class LockVerifyBody(BaseModel):
    pin: str = Field(min_length=0, max_length=64)


class LockBiometricBody(BaseModel):
    credential_id: str = Field(default="", min_length=0, max_length=300)
    kind: str = Field(default="biometric", pattern="^(biometric|passkey)$")


class LockResetBody(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    pin: str = Field(min_length=4, max_length=64)


class LockDisableResetBody(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class PasswordEntryBody(BaseModel):
    id: str = ""
    title: str = Field(default="", max_length=200)
    url: str = Field(default="", max_length=2000)
    username: str = Field(default="", max_length=300)
    password: str = Field(default="", max_length=2000)
    totp: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=4000)


class PasswordImportBody(BaseModel):
    text: str = Field(min_length=1, max_length=16_000_000)
    filename: str = Field(default="", max_length=300)


class CalendarFeedBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1, max_length=2000)


class CalendarImportBody(BaseModel):
    name: str = Field(default="Phone & Google Calendar", max_length=60)
    text: str = Field(min_length=1, max_length=16_000_000)


class ContactsSourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(min_length=1, max_length=2000)


class ContactsBody(BaseModel):
    enabled: bool | None = None


class SkillBody(BaseModel):
    content: str = Field(max_length=70_000)  # the SKILL.md text


class SkillEnabledBody(BaseModel):
    enabled: bool


class SkillImportBody(BaseModel):
    url: str = Field(max_length=2000)


class CalendarBody(BaseModel):
    enabled: bool | None = None
    refresh_minutes: int | None = Field(default=None, ge=5, le=1440)
    day_start: str | None = None
    day_end: str | None = None


class MCPBody(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    risk: str = "moderate"
    egress: bool = True
    reads_private_data: bool = False


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=10_000)


class OnboardedBody(BaseModel):
    done: bool = True


class FirstRunStartBody(BaseModel):
    # the language the opening is shown in ("en" or "zh"); the agent's reply language when empty
    lang: str = Field("", max_length=10)


class FirstRunPickBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class BrowserControlBody(BaseModel):
    """The user drives the agent's browser. ``x``/``y`` are fractions of the frame."""

    action: str
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    text: str | None = Field(default=None, max_length=4000)
    key: str | None = Field(default=None, max_length=40)
    dy: float | None = Field(default=None, ge=-5000, le=5000)
    url: str | None = Field(default=None, max_length=2000)


class HoldBody(BaseModel):
    """The user takes the browser, the computer or the phone over for a chat (contract C1)."""

    thread: str = Field(default=MAIN_THREAD, max_length=64)
    tool: str = Field(pattern="^(browser|computer|phone)$")
    reason: str | None = Field(default=None, max_length=300)


class PushSubscribeBody(BaseModel):
    # the PushSubscription.toJSON() of the browser: {endpoint, expirationTime, keys{p256dh, auth}}
    subscription: dict[str, Any]


class PushUnsubscribeBody(BaseModel):
    endpoint: str


class CloudCodeBody(BaseModel):
    identifier: str = Field(min_length=3, max_length=200)


class CloudVerifyBody(BaseModel):
    identifier: str = Field(default="", max_length=200)
    code: str = Field(min_length=4, max_length=12)
    invite: str = Field(default="", max_length=32)


class CloudModelBody(BaseModel):
    model: str = Field(default="", max_length=120)


class AvatarBeginBody(BaseModel):
    description: str = Field(min_length=1, max_length=200)
    # the chat the card goes to; "" from the studio screen, which has no card
    thread: str = MAIN_THREAD
    # one of the studio's STYLES (the phone's list); Muse's 3D toy look when left out
    style: str = Field(default="muse", max_length=20)


class AvatarSessionBody(BaseModel):
    session: str
    index: int | None = None


class CloudModelsBody(BaseModel):
    """``POST /api/cloud/models``: the account's models as a source, on or off."""

    on: bool = True


class CloudContributeBody(BaseModel):
    on: bool = False


class SyncStateBody(BaseModel):
    enabled: bool | None = None
    # C9: this device's side chats too (per device, default off)
    side_chats: bool | None = None


class PairWithBody(BaseModel):
    target: str = Field(default="", max_length=2000)
    code: str = Field(default="", max_length=32)
    token: str = Field(default="", max_length=512)
    name: str = Field(default="", max_length=60)
    sync_side_chats: bool = True


class UnpairBody(BaseModel):
    identifier: str = Field(min_length=1, max_length=200)


class PeerClaimBody(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    device_id: str = Field(default="", max_length=120)
    name: str = Field(default="", max_length=60)
    urls: list[str] = Field(default_factory=list)
    peer_token: str = Field(default="", max_length=512)


class AutoUpdateSettingsBody(BaseModel):
    auto_update: bool


class CloudLoginBody(BaseModel):
    identifier: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=128)


class CloudPasswordBody(BaseModel):
    password: str = Field(default="", max_length=128)
    current: str | None = Field(default=None, max_length=128)


class CloudSignOutAllBody(BaseModel):
    all: bool = False


class CodingSendBody(BaseModel):
    agent: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=1, max_length=20000)
    session_id: str = Field(default="", max_length=120)
    workspace: str = Field(default="", max_length=1000)
    device: str = Field(default="", max_length=120)


class CodingStopBody(BaseModel):
    run: str = Field(min_length=1, max_length=40)
    device: str = Field(default="", max_length=120)


class HubBody(BaseModel):
    enabled: bool | None = None
    remote_control: bool | None = None
    name: str | None = Field(default=None, max_length=60)


class AskDeviceBody(BaseModel):
    device: str = Field(min_length=1, max_length=120)
    text: str = Field(default="", max_length=20_000)


class ComputerTargetBody(BaseModel):
    app: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=160)


class CallConfigBody(BaseModel):
    provider: str = Field(default="", max_length=80)
    model: str = Field(default="", max_length=160)


class WatchClaimBody(BaseModel):
    code: str = Field(min_length=4, max_length=16)


class WatchApproveBody(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    approved: bool = True
    scope: str = Field(default="once", max_length=20)
    kind: str = Field(default="approval", max_length=20)


# ----------------------------------------------------------------------------- app factory
def create_app(settings: Settings, service: MuseService | None = None) -> FastAPI:
    svc = service or MuseService(settings)
    update_check = UpdateCheck(svc.settings.server.update_check, repo=svc.settings.server.update_repo)
    personal_cloud = PersonalCloudManager(port=svc.settings.server.port)
    watch_codes: dict[str, float] = {}
    # what the background watch finder last saw (shown in the Devices screen)
    watch_auto: dict[str, Any] = {"last_check": 0.0, "last_result": "not checked yet", "watch_online": False}
    self_update = SelfUpdate(update_check, svc.settings.server.update_repo)
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        swap_err = consume_swap_error()
        if swap_err:
            self_update.last_error = swap_err
        with contextlib.suppress(OSError):
            cleanup_updates_dir()
    release_tracker = ReleaseNotesTracker(svc.data_dir, svc.settings_view()["version"])
    release_tracker.detect_on_startup()
    auto_update_wake = asyncio.Event()

    def _notes_payload(view: dict[str, Any]) -> dict[str, Any] | None:
        latest = str(view.get("latest") or "").strip()
        if not latest:
            return None
        body = str(view.get("release_body") or "")
        title = str(view.get("release_name") or "")
        if body:
            return release_tracker.cache_release(latest, body, title)
        return release_tracker.notes_for(latest, title=title)

    async def _auto_update_loop() -> None:
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("NANOMUSE_NO_UPDATE_CHECK"):
            return
        await asyncio.sleep(12.0)
        if self_update.last_error:
            svc.bus.publish(
                {
                    "kind": "app_update",
                    "update": {"status": "error", "error": self_update.last_error},
                }
            )
        while True:
            try:
                if svc.settings.server.update_check or svc.auto_update_enabled():
                    update_check.setting = True
                    view = await update_check.view(force=True)
                    latest = str(view.get("latest") or "")
                    if view.get("newer") and latest:
                        status_info = self_update.status()
                        notes = _notes_payload(view)
                        svc.bus.publish(
                            {
                                "kind": "app_update",
                                "update": {
                                    "status": "available",
                                    "current": view.get("current"),
                                    "latest": latest,
                                    "url": view.get("url") or "",
                                    "auto_update": svc.auto_update_enabled(),
                                    "auto_supported": status_info["auto_supported"],
                                    "notes": notes,
                                },
                            }
                        )
                        if (
                            svc.auto_update_enabled()
                            and status_info["auto_supported"]
                            and self_update.last_auto_attempt != latest
                            and not self_update.busy
                        ):
                            # Wait peacefully if the AI is currently mid-turn or waiting on a hold so no data is ever lost
                            for _ in range(120):
                                if not any(t.busy for t in svc.threads.values()) and not svc.app.holds.active():
                                    break
                                await asyncio.sleep(3.0)
                            if not any(t.busy for t in svc.threads.values()) and not svc.app.holds.active():
                                self_update.last_auto_attempt = latest
                                svc.bus.publish(
                                    {
                                        "kind": "app_update",
                                        "update": {
                                            "status": "applying",
                                            "current": view.get("current"),
                                            "latest": latest,
                                            "notes": notes,
                                        },
                                    }
                                )
                                res = await self_update.apply()
                                if res.get("ok"):
                                    svc.bus.publish(
                                        {
                                            "kind": "app_update",
                                            "update": {
                                                "status": "restarting",
                                                "current": view.get("current"),
                                                "latest": latest,
                                                "notes": notes,
                                            },
                                        }
                                    )
                                    svc.ui.emit(
                                        {
                                            "type": "notice",
                                            "level": "info",
                                            "source": "update",
                                            "text": f"MiniMuse updated to v{latest} and is restarting. See What's New after reload.",
                                        }
                                    )
                                    self_update.restart_in_place()
                                    return
                                self_update.last_error = str(res.get("error") or "update failed")
                                svc.bus.publish(
                                    {
                                        "kind": "app_update",
                                        "update": {
                                            "status": "error",
                                            "latest": latest,
                                            "error": self_update.last_error,
                                        },
                                    }
                                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("background update check failed: {}", exc)
            auto_update_wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(auto_update_wake.wait(), timeout=900)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        starting = asyncio.create_task(svc.start(), name="services-start")
        await asyncio.wait({starting}, timeout=settings.server.start_grace)
        updater_task = asyncio.create_task(_auto_update_loop(), name="auto-update-loop")
        watch_task = asyncio.create_task(_watch_autodetect_loop(), name="watch-autodetect")
        on_ready = getattr(app.state, "on_ready", None)
        if callable(on_ready):
            on_ready()
        try:
            yield
        finally:
            updater_task.cancel()
            watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await updater_task
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await watch_task
            if not starting.done():
                starting.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await starting
            await personal_cloud.stop_tunnel()
            await svc.stop()

    app = FastAPI(
        title="MiniMuse",
        version=svc.settings_view()["version"],
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.service = svc
    app.state.update_check = update_check
    app.state.self_update = self_update
    app.state.watch_autodetect_once = lambda: _autodetect_once()
    app.state.watch_auto_status = watch_auto

    if settings.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.server.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # ------------------------------------------------------------------ auth
    def _is_trusted(request: Request) -> bool:
        client_host = request.client.host if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost"):
            return True
        return False

    def _check_token(token: str | None) -> None:
        if not svc.token:
            return
        if not token or not secrets.compare_digest(token.encode(), svc.token.encode()):
            raise HTTPException(status_code=401, detail="invalid or missing token")

    def auth(request: Request) -> None:
        if _is_trusted(request):
            return
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        if token is None:
            token = request.cookies.get("nanomuse_token")
        if token is None and "token" in request.query_params:
            q_token = request.query_params.get("token")
            if (
                svc.token
                and q_token
                and secrets.compare_digest(q_token.encode(), svc.token.encode())
            ):
                token = q_token
            else:
                raise HTTPException(status_code=401, detail=LEGACY_TOKEN_MESSAGE)
        _check_token(token)

    def auth_or_signed(request: Request) -> None:
        """A header as everywhere — or, for the bytes an ``<img>``/``<video>``/``<iframe>``
        fetches, a link the client signed with its token (:mod:`nanomuse.server.tickets`)."""
        query = request.query_params
        if svc.token and "sig" in query and "authorization" not in request.headers:
            if tickets.check(svc.token, request.scope["path"], query.get("exp"), query.get("sig")):
                return
            raise HTTPException(status_code=401, detail="this link has expired")
        auth(request)

    def sync_auth(request: Request) -> None:
        if _is_trusted(request):
            return
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else None
        if token and svc.sync.pairing.verify_inbound_token(token):
            return
        _check_token(token or request.cookies.get("nanomuse_token"))

    dep = [Depends(auth)]
    dep_or_signed = [Depends(auth_or_signed)]
    dep_sync = [Depends(sync_auth)]

    def _thread_or_404(thread_id: str):  # noqa: ANN202
        thread = svc.threads.get(thread_id)
        if thread is None:
            raise HTTPException(404, "no such thread")
        return thread

    def _goal_or_404(goal_id: str):  # noqa: ANN202
        goal = svc.app.goals.get(goal_id)
        if goal is None:
            raise HTTPException(404, "no such goal")
        return goal

    # ------------------------------------------------------------------ state
    @app.get("/api/state", dependencies=dep)
    async def get_state(response: Response) -> dict[str, Any]:
        if svc.token:
            response.set_cookie(
                key="nanomuse_token",
                value=svc.token,
                httponly=True,
                samesite="lax",
                path="/",
            )
        return svc.state()

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        out = {"ok": True, "version": svc.settings_view()["version"], "auth": bool(svc.token)}
        if svc.starting:
            out["starting"] = svc.starting
        return out

    @app.get("/api/diagnostics/traces", dependencies=dep)
    async def get_diagnostic_traces(limit: int = 50) -> list[dict[str, Any]]:
        from nanomuse.instrumentation import InstrumentationRegistry

        return InstrumentationRegistry.get().list_traces(limit)

    @app.get("/api/diagnostics/summary", dependencies=dep)
    async def get_diagnostic_summary() -> dict[str, Any]:
        from nanomuse.instrumentation import InstrumentationRegistry

        return InstrumentationRegistry.get().get_summary()

    @app.get("/api/usage", dependencies=dep)
    async def get_usage(days: int = 30, model: str = "") -> dict[str, Any]:
        """The local usage ledger, aggregated: tokens, cost, activities, media. Works
        signed-out on purpose; nothing here needs an account. `model` narrows it to one AI."""
        return svc.usage_summary(days, model)

    @app.get("/api/usage-limits", dependencies=dep)
    async def get_usage_limits() -> list[dict[str, Any]]:
        """The usage limits and warnings (per model or global) checked before every turn."""
        return svc.usage_limits.all()

    @app.get("/api/models", dependencies=dep)
    async def models_list() -> dict[str, Any]:
        """What the header's model menu offers: saved multi-model options, built-in aliases,
        the configured default, and every endpoint added by the person or an AI."""
        llm = svc.app.settings.llm
        return {
            "default": {
                "model": llm.model,
                "provider": llm.provider,
                "base_url": llm.base_url or "",
                "endpoint": svc.connections.endpoints.id_for(llm.endpoint),
            },
            "options": svc.connections.model_options(),
            "aliases": [
                {"value": "", "label": "Default", "hint": llm.model},
                {"value": "flash", "label": "Flash", "hint": "deepseek-flash, thinking off"},
                {"value": "pro", "label": "Pro", "hint": "deepseek-v4-pro, thinking on"},
                {
                    "value": "local",
                    "label": "Local",
                    "hint": "qwen3.5:9b-32k, Ollama, this computer",
                },
                {
                    "value": "gemini",
                    "label": "Gemini",
                    "hint": "gemini-3.1-flash-lite, free tier, at most 200 a day",
                },
            ],
            "endpoints": [
                {
                    "id": row["id"],
                    "label": row.get("label") or "",
                    "models": row.get("models") or [],
                }
                for row in svc.connections.endpoints.rows()
            ],
        }

    @app.put("/api/usage-limits", dependencies=dep)
    async def put_usage_limits(body: list[UsageLimitBody]) -> list[dict[str, Any]]:
        """Replace the whole list (the Usage screen edits it as one list). A row's id is
        kept when it carries one, so re-saving does not reset anything."""
        from nanomuse.server.usage_limits import new_id

        rows: list[dict[str, Any]] = []
        for row in body:
            data = row.model_dump()
            data["id"] = data.get("id") or new_id()
            if data.get("scope") != "model":
                data["model"] = ""
            rows.append(data)
        svc.usage_limits.save(rows)
        return svc.usage_limits.all()

    @app.post("/api/restart", dependencies=dep)
    async def restart_server() -> dict[str, Any]:
        """Restart the server in place: the helper brings it back with the same
        arguments; the web app reconnects on its own."""
        asyncio.get_running_loop().call_later(0.5, svc.schedule_restart)
        return {"ok": True, "restarting": True}

    # ---- the app's lock screen: a PIN over the page, so a device someone else picks
    # up asks before it shows the conversation (the token stays the real gate).  The
    # lock is per device: the browser sends its own id on every call, so the phone's
    # PIN or fingerprint never locks the computer, and the other way round.
    def _lock_device(request: Request) -> tuple[str, tuple[str, ...]]:
        device_id = request.headers.get("x-nanomuse-device", "").strip()[:80]
        local = request.headers.get("x-nanomuse-lock-local", "")
        creds = tuple(c.strip() for c in local.split(",") if c.strip())
        return device_id, creds

    @app.get("/api/lock", dependencies=dep)
    async def lock_view(request: Request) -> dict[str, Any]:
        device_id, creds = _lock_device(request)
        return svc.app_lock.view(device_id, creds)

    @app.put("/api/lock", dependencies=dep)
    async def put_lock(request: Request, body: LockBody) -> dict[str, Any]:
        device_id, _ = _lock_device(request)
        pin = body.pin.strip()
        if pin == "__KEEP__" and (body.timeout_seconds is not None or body.lock_on_leave is not None):
            return svc.app_lock.set_timeout(device_id, body.timeout_seconds, body.lock_on_leave)
        if pin and len(pin) < 4:
            raise HTTPException(422, "Use at least 4 characters.")
        return svc.app_lock.set_pin(
            device_id,
            pin,
            keep_biometric=body.keep_biometric,
            timeout_seconds=body.timeout_seconds,
            lock_on_leave=body.lock_on_leave,
        )

    @app.post("/api/lock/reset", dependencies=dep)
    async def lock_reset_request(request: Request) -> dict[str, Any]:
        """One reset code for this device, delivered to the owner's inbox, phone or the terminal."""
        device_id, _ = _lock_device(request)
        return await svc.request_lock_reset(device_id)

    @app.post("/api/lock/reset/apply", dependencies=dep)
    async def lock_reset_apply(request: Request, body: LockResetBody) -> dict[str, Any]:
        """The code sets a new password/PIN on this device and the lock opens with it."""
        device_id, _ = _lock_device(request)
        try:
            return svc.app_lock.reset_with_code(device_id, body.code, body.pin)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/lock/reset/disable", dependencies=dep)
    async def lock_reset_disable(request: Request, body: LockDisableResetBody) -> dict[str, Any]:
        """The code turns off App Lock completely for this device."""
        device_id, _ = _lock_device(request)
        try:
            return svc.app_lock.disable_with_code(device_id, body.code)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    class ModelSwitchAnswer(BaseModel):
        thread: str
        event_id: str
        accept: bool = True

    @app.post("/api/model-switch", dependencies=dep)
    async def answer_model_switch(body: ModelSwitchAnswer) -> dict[str, Any]:
        """Answer the model-switch card: keep the new default, or put the previous one back."""
        timeline = svc.ui.get_timeline(body.thread)
        ev = timeline.get(body.event_id)
        if not ev or ev.get("type") != "model_switch" or ev.get("status") == "accepted":
            raise HTTPException(404, "no such card")
        if body.accept:
            svc.ui.patch(body.thread, body.event_id, status="accepted")
            return {"ok": True}
        prev = {
            "model": ev.get("previous_model"),
            "provider": ev.get("previous_provider"),
            "base_url": ev.get("previous_base_url"),
        }
        try:
            svc.connections.set_llm({k: v for k, v in prev.items() if v})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        svc.ui.patch(body.thread, body.event_id, status="reverted")
        return {"ok": True}

    @app.post("/api/lock/verify", dependencies=dep)
    async def lock_verify(request: Request, body: LockVerifyBody) -> dict[str, bool]:
        device_id, _ = _lock_device(request)
        return {"ok": svc.app_lock.verify(device_id, body.pin)}

    @app.post("/api/lock/webauthn", dependencies=dep)
    async def lock_webauthn(request: Request, body: LockBiometricBody) -> dict[str, Any]:
        """Register this browser's platform credential (fingerprint / face) or passkey
        (NordPass / password manager) for this device's lock screen."""
        device_id, _ = _lock_device(request)
        return svc.app_lock.register_biometric(device_id, body.credential_id, kind=body.kind)

    @app.get("/api/lock/webauthn/credential", dependencies=dep)
    async def lock_credential(request: Request, kind: str = Query("", max_length=20)) -> str:
        """The registered credential's id, for the browser's unlock request."""
        device_id, _ = _lock_device(request)
        cid = svc.app_lock.biometric_credential(device_id, kind)
        if not cid:
            raise HTTPException(404, "no credential registered")
        return cid

    @app.get("/api/update", dependencies=dep)
    async def update_status(refresh: bool = False) -> dict[str, Any]:
        """The installed and the latest release (a day's cache, ``?refresh=1`` asks now)."""
        if refresh or svc.auto_update_enabled():
            update_check.setting = True
        view = await update_check.view(force=refresh)
        latest_notes = _notes_payload(view)
        return {
            **view,
            **self_update.status(),
            "auto_update": svc.auto_update_enabled(),
            "latest_notes": latest_notes,
            "whats_new": release_tracker.pending(),
            "current_notes": release_tracker.notes_for(svc.settings_view()["version"]),
        }

    @app.post("/api/update/whats-new/dismiss", dependencies=dep)
    async def update_whats_new_dismiss() -> dict[str, Any]:
        release_tracker.dismiss()
        return {"ok": True}

    @app.put("/api/update/settings", dependencies=dep)
    async def update_auto_settings(body: AutoUpdateSettingsBody) -> dict[str, Any]:
        svc.update_settings({"auto_update": body.auto_update})
        if body.auto_update:
            auto_update_wake.set()
        return {"ok": True, "auto_update": svc.auto_update_enabled(), **self_update.status()}

    @app.post("/api/update/apply", dependencies=dep)
    async def update_apply() -> dict[str, Any]:
        """Download the newest release asset, install it under this runtime, and restart."""
        result = await self_update.apply()
        if result.get("ok"):
            svc.bus.publish(
                {
                    "kind": "app_update",
                    "update": {
                        "status": "restarting",
                        "current": result.get("from"),
                        "latest": result.get("to"),
                    },
                }
            )
            self_update.restart_in_place()
        return result

    @app.post("/api/system/shutdown", dependencies=dep)
    async def system_shutdown(request: Request) -> dict[str, Any]:
        """Cleanly exit when asked by a newer local MiniMuse launcher during upgrade."""
        client_host = request.client.host if request.client else ""
        if client_host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "loopback only")

        def _exit_now() -> None:
            from nanomuse.server.selfupdate import _RESTART_HOOK

            if _RESTART_HOOK is not None:
                with contextlib.suppress(Exception):
                    _RESTART_HOOK()
            import _thread

            _thread.interrupt_main()
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                import threading

                threading.Timer(0.25, lambda: os._exit(0)).start()

        asyncio.get_running_loop().call_later(0.15, _exit_now)
        return {"ok": True}

    @app.post("/api/update/rollback", dependencies=dep)
    async def update_rollback() -> dict[str, Any]:
        """Put back the wheel that was installed before the last self-update, and restart."""
        result = await self_update.rollback()
        if result.get("ok"):
            self_update.restart_in_place()
        return result

    @app.get("/api/update/status", dependencies=dep)
    async def update_self_status() -> dict[str, Any]:
        """What the self-updater knows: busy, frozen, whether auto-update is supported."""
        return {**self_update.status(), "auto_update": svc.auto_update_enabled()}

    # ------------------------------------------------------------------ threads & chat
    @app.get("/api/threads", dependencies=dep)
    async def list_threads() -> list[dict[str, Any]]:
        """The current account's conversations and the ones no account has (contract C10);
        every local one when signed out."""
        return [t.meta() for t in svc.visible_threads()]

    @app.post("/api/threads", dependencies=dep)
    async def create_thread(body: ThreadBody) -> dict[str, Any]:
        return svc.create_thread(body.title).meta()

    @app.patch("/api/threads/{thread_id}", dependencies=dep)
    async def rename_thread(thread_id: str, body: ThreadBody) -> dict[str, Any]:
        thread = svc.rename_thread(thread_id, body.title)
        if thread is None:
            raise HTTPException(404, "no such thread")
        return thread.meta()

    @app.delete("/api/threads/{thread_id}", dependencies=dep)
    async def delete_thread(thread_id: str) -> dict[str, Any]:
        if thread_id == MAIN_THREAD:
            raise HTTPException(400, "the main chat cannot be deleted")
        if not svc.delete_thread(thread_id):
            raise HTTPException(404, "no such thread")
        return {"ok": True}

    @app.get("/api/trash", dependencies=dep)
    async def list_trash() -> dict[str, Any]:
        """Deleted chats held in Trash (default 365 days) before permanent expiry."""
        return {
            "items": svc.list_trash(),
            "retention_days": svc._trash_retention_days(),
        }

    @app.post("/api/trash/{thread_id}/restore", dependencies=dep)
    async def restore_trash(thread_id: str) -> dict[str, Any]:
        thread = svc.restore_thread(thread_id)
        if thread is None:
            raise HTTPException(404, "no such trashed thread")
        return {"ok": True, "thread": thread.meta()}

    @app.delete("/api/trash/{thread_id}", dependencies=dep)
    async def purge_trash(thread_id: str) -> dict[str, Any]:
        if not svc.purge_trash_item(thread_id):
            raise HTTPException(404, "no such trashed thread")
        return {"ok": True}

    @app.post("/api/trash/empty", dependencies=dep)
    async def empty_trash() -> dict[str, Any]:
        return {"ok": True, "removed": svc.empty_trash()}

    @app.post("/api/threads/{thread_id}/stop", dependencies=dep)
    async def stop_thread(thread_id: str) -> dict[str, Any]:
        _thread_or_404(thread_id)
        return {"ok": svc.stop_thread(thread_id)}

    @app.post("/api/threads/{thread_id}/feedback", dependencies=dep)
    async def thread_feedback(thread_id: str, body: FeedbackBody) -> dict[str, Any]:
        """The lightest feedback: a thumb up or down beside a reply, or the reason for a
        Stop. Kept with the conversation; told to the account's record when signed in."""
        _thread_or_404(thread_id)
        try:
            if body.stop_reason:
                svc.stop_reason(thread_id, body.stop_reason)
                return {"ok": True}
            if not body.event:
                raise HTTPException(400, "event or stop_reason")
            ev = svc.feedback(thread_id, body.event, body.vote, body.reason)
            return {"ok": True, "event": ev}
        except KeyError:
            raise HTTPException(404, "no such event") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/threads/{thread_id}/clear", dependencies=dep)
    async def clear_thread(thread_id: str) -> dict[str, Any]:
        _thread_or_404(thread_id)
        if not svc.clear_thread(thread_id):
            raise HTTPException(409, "thread is busy")
        return {"ok": True}

    @app.get("/api/threads/{thread_id}/events", dependencies=dep)
    async def thread_events(
        thread_id: str, limit: int = Query(200, ge=1, le=1000), before: str | None = None
    ) -> dict[str, Any]:
        thread = _thread_or_404(thread_id)
        events = thread.timeline.tail(limit, before)
        active_stream = svc.ui.active_streams_view().get(thread_id)
        return {
            "thread": thread.meta(),
            "events": events,
            "has_more": len(thread.timeline.events) > len(events),
            "active_stream": active_stream,
        }

    @app.delete("/api/threads/{thread_id}/events/{event_id}", dependencies=dep)
    async def delete_event(thread_id: str, event_id: str) -> dict[str, Any]:
        try:
            svc.delete_event(thread_id, event_id)
        except KeyError as exc:
            raise HTTPException(404, "no such event") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/threads/{thread_id}/send", dependencies=dep)
    async def send_message(thread_id: str, body: SendBody) -> dict[str, Any]:
        thread = _thread_or_404(thread_id)
        try:
            event = svc.send(
                thread.id,
                body.text,
                files=body.files,
                language=body.language,
                via=body.via,
                event_id=body.id,
                model=body.model,
            )
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return {"event": event, "thread": thread.meta()}

    # ------------------------------------------------------------------ approvals
    @app.post("/api/approvals/{approval_id}", dependencies=dep)
    async def decide_approval(approval_id: str, body: ApprovalBody) -> dict[str, Any]:
        if approval_id in svc.hub.remote_approvals:
            # a card raised by another device's run, shown in its side chat here
            if not await svc.hub.decide_remote(approval_id, body.approved):
                raise HTTPException(502, "the device did not take the answer")
            return {"ok": True}
        if not svc.decide(approval_id, body.approved, body.scope, body.reason):
            raise HTTPException(404, "no pending approval with that id")
        return {"ok": True}

    @app.delete("/api/approvals", dependencies=dep)
    async def reset_approvals() -> dict[str, Any]:
        svc.forget_approvals()
        return {"ok": True}

    @app.delete("/api/approvals/grants/{key:path}", dependencies=dep)
    async def revoke_grant(key: str) -> dict[str, Any]:
        if not svc.revoke_grant(key):
            raise HTTPException(404, "no such permission")
        return {"ok": True}

    # ------------------------------------------------------------------ goals
    @app.get("/api/goals", dependencies=dep)
    async def list_goals(
        status: str | None = None,
        category: str | None = None,
        kind: str | None = None,
        priority: str | None = None,
        tag: str | None = None,
        sort: str = "created",
    ) -> list[dict[str, Any]]:
        return [
            goal_to_dict(g)
            for g in svc.app.goals.list(
                status,
                category,
                kind=kind,
                priority=priority,
                tag=tag,
                sort=sort,
            )
        ]

    @app.post("/api/goals", dependencies=dep)
    async def create_goal(body: GoalBody) -> dict[str, Any]:
        try:
            goal = svc.app.goals.create(
                body.title,
                body.description,
                [s for s in body.steps if s.strip()],
                category=body.category,
                due=body.due,
                check_in=body.check_in,
                kind=body.kind,
                priority=body.priority,
                tags=body.tags,
                due_time=body.due_time,
                recurrence=body.recurrence,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(goal)

    @app.get("/api/goals/{goal_id}", dependencies=dep)
    async def get_goal(goal_id: str) -> dict[str, Any]:
        return goal_to_dict(_goal_or_404(goal_id))

    @app.patch("/api/goals/{goal_id}", dependencies=dep)
    async def patch_goal(goal_id: str, body: GoalPatch) -> dict[str, Any]:
        _goal_or_404(goal_id)
        store = svc.app.goals
        try:
            if body.status:
                store.set_status(goal_id, body.status)
            if body.note:
                store.append_note(goal_id, body.note)
            if body.step_index is not None and (
                body.step_status or body.step_note is not None or body.step_title is not None
            ):
                store.update_step(
                    goal_id,
                    body.step_index,
                    body.step_status,
                    body.step_note,
                    title=body.step_title,
                )
            if any(
                v is not None
                for v in (
                    body.title,
                    body.description,
                    body.category,
                    body.due,
                    body.check_in,
                    body.kind,
                    body.priority,
                    body.tags,
                    body.due_time,
                    body.recurrence,
                    body.sort_order,
                )
            ):
                store.update(
                    goal_id,
                    title=body.title,
                    description=body.description,
                    category=body.category,
                    due=body.due,
                    check_in=body.check_in,
                    kind=body.kind,
                    priority=body.priority,
                    tags=body.tags,
                    due_time=body.due_time,
                    recurrence=body.recurrence,
                    sort_order=body.sort_order,
                )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(_goal_or_404(goal_id))

    @app.post("/api/goals/{goal_id}/check-in", dependencies=dep)
    async def check_in_goal(goal_id: str) -> dict[str, Any]:
        """Send this goal's reminder now."""
        try:
            goal = svc.check_in(goal_id)
        except KeyError as exc:
            raise HTTPException(404, "no such goal") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return goal_to_dict(goal)

    @app.post("/api/goals/{goal_id}/proposal/accept", dependencies=dep)
    async def accept_proposal(goal_id: str) -> dict[str, Any]:
        goal = _goal_or_404(goal_id)
        if not goal.proposal:
            raise HTTPException(409, "nothing proposed")
        svc.app.goals.accept_proposal(goal_id)
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(_goal_or_404(goal_id))

    @app.delete("/api/goals/{goal_id}/proposal", dependencies=dep)
    async def dismiss_proposal(goal_id: str) -> dict[str, Any]:
        goal = _goal_or_404(goal_id)
        if not goal.proposal:
            raise HTTPException(409, "nothing proposed")
        svc.app.goals.dismiss_proposal(goal_id)
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(_goal_or_404(goal_id))

    @app.post("/api/goals/{goal_id}/steps", dependencies=dep)
    async def add_step(goal_id: str, body: StepBody) -> dict[str, Any]:
        _goal_or_404(goal_id)
        svc.app.goals.add_step(goal_id, body.title)
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(_goal_or_404(goal_id))

    @app.delete("/api/goals/{goal_id}/steps/{step_index}", dependencies=dep)
    async def delete_step(goal_id: str, step_index: int) -> dict[str, Any]:
        _goal_or_404(goal_id)
        try:
            svc.app.goals.delete_step(goal_id, step_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(_goal_or_404(goal_id))

    @app.post("/api/goals/{goal_id}/steps/reorder", dependencies=dep)
    async def reorder_steps(goal_id: str, body: StepReorderBody) -> dict[str, Any]:
        _goal_or_404(goal_id)
        svc.app.goals.reorder_steps(goal_id, body.order)
        svc.bus.publish({"kind": "goals"})
        return goal_to_dict(_goal_or_404(goal_id))

    @app.post("/api/goals/{goal_id}/advance", dependencies=dep)
    async def advance_goal(goal_id: str) -> dict[str, Any]:
        try:
            goal = svc.advance_goal(goal_id)
        except KeyError as exc:
            raise HTTPException(404, "no such goal") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return goal_to_dict(goal)

    @app.delete("/api/goals/{goal_id}", dependencies=dep)
    async def delete_goal(goal_id: str) -> dict[str, Any]:
        if not svc.app.goals.delete(goal_id):
            raise HTTPException(404, "no such goal")
        svc.bus.publish({"kind": "goals"})
        return {"ok": True}

    # ------------------------------------------------------------------ reminders
    @app.get("/api/reminders", dependencies=dep)
    async def list_reminders(all: bool = False) -> list[dict[str, Any]]:  # noqa: A002
        """Active reminders and routines, soonest first; ``?all=1`` adds recently finished ones."""
        return [r.to_dict() for r in svc.app.reminders.list(None if all else "active")]

    @app.post("/api/reminders", dependencies=dep)
    async def create_reminder(body: ReminderBody) -> dict[str, Any]:
        try:
            item = svc.create_reminder(
                body.text, at=body.at, repeat=body.repeat, kind=body.kind, thread=body.thread
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return item.to_dict()

    @app.post("/api/reminders/{reminder_id}/fire", dependencies=dep)
    async def fire_reminder(reminder_id: str) -> dict[str, Any]:
        """Deliver it now instead of waiting for its time."""
        try:
            return svc.fire_reminder(reminder_id).to_dict()
        except KeyError as exc:
            raise HTTPException(404, "no such reminder") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/reminders/{reminder_id}", dependencies=dep)
    async def cancel_reminder(reminder_id: str) -> dict[str, Any]:
        item = svc.cancel_reminder(reminder_id)
        if item is None:
            if svc.app.reminders.get(reminder_id) is None:
                raise HTTPException(404, "no such reminder")
            raise HTTPException(409, "already finished")
        return item.to_dict()

    # ------------------------------------------------------------------ triggers
    @app.get("/api/triggers", dependencies=dep)
    async def list_triggers() -> dict[str, Any]:
        """Triggers (active and cancelled), which kinds have their connector, and how the
        inbox watch is doing."""
        return svc.triggers_view()

    @app.post("/api/triggers", dependencies=dep)
    async def create_trigger(body: TriggerBody) -> dict[str, Any]:
        try:
            item = svc.create_trigger(
                body.kind,
                body.text,
                match=body.match,
                lead_minutes=body.lead_minutes,
                thread=body.thread,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        out = item.to_dict()
        if item.kind == "hook":
            out["url"] = svc.hook_url(item)
        return out

    @app.post("/api/triggers/{trigger_id}/fire", dependencies=dep)
    async def fire_trigger(trigger_id: str) -> dict[str, Any]:
        """Run it now with a sample occurrence, to see what it does."""
        item = svc.app.triggers.get(trigger_id)
        if item is None:
            raise HTTPException(404, "no such trigger")
        what = {
            "mail": "A test: you pressed Run now, as if a matching mail had just arrived (none did)",
            "event": "A test: you pressed Run now, as if a matching event were about to start",
            "hook": "A test: you pressed Run now, as if the webhook had been called (empty body)",
            "device": "A test: you pressed Run now, as if the device had just come online (it did not)",
        }.get(item.kind, "A test: you pressed Run now")
        try:
            fired = svc.fire_trigger(
                trigger_id, what=what, key=f"test:{secrets.token_hex(4)}", title="test run"
            )
        except KeyError as exc:
            raise HTTPException(404, "no such trigger") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return (fired or item).to_dict()

    @app.delete("/api/triggers/{trigger_id}", dependencies=dep)
    async def cancel_trigger(trigger_id: str) -> dict[str, Any]:
        item = svc.cancel_trigger(trigger_id)
        if item is None:
            if svc.app.triggers.get(trigger_id) is None:
                raise HTTPException(404, "no such trigger")
            raise HTTPException(409, "already cancelled")
        return item.to_dict()

    @app.post("/api/hooks/{trigger_id}")
    async def deliver_hook(trigger_id: str, request: Request) -> dict[str, Any]:
        """A trigger's webhook. No app token: the key in the URL is the credential, and a
        wrong id or key is a 404 either way. The body (up to 64 KB, JSON or text) is what
        the agent gets as context."""
        key = request.query_params.get("key") or request.headers.get("x-hook-key") or ""
        limit = 64 * 1024
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "body too large (64 KB max)")
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, "body too large (64 KB max)")
            chunks.append(chunk)
        body = b"".join(chunks).decode("utf-8", errors="replace")
        try:
            item = svc.deliver_hook(trigger_id, key, body, request.headers.get("content-type", ""))
        except KeyError as exc:
            raise HTTPException(404, "not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(429, str(exc)) from exc
        return {"ok": True, "trigger": item.id, "fired": item.fired}

    # ------------------------------------------------------------------ memory
    @app.get("/api/memory", dependencies=dep)
    async def list_memory() -> list[dict[str, Any]]:
        if svc.app.memory is None:
            return []
        return [m.__dict__ for m in svc.app.memory.all()]

    @app.post("/api/memory", dependencies=dep)
    async def add_memory(body: MemoryBody) -> dict[str, Any]:
        if svc.app.memory is None:
            raise HTTPException(400, "memory is disabled")
        item = svc.app.memory.add(body.content, body.category or "profile", source="user")
        svc.bus.publish({"kind": "memory"})
        return item.__dict__

    @app.delete("/api/memory/{memory_id}", dependencies=dep)
    async def forget_memory(memory_id: str) -> dict[str, Any]:
        if svc.app.memory is None or not svc.app.memory.forget(memory_id):
            raise HTTPException(404, "no such memory")
        svc.bus.publish({"kind": "memory"})
        return {"ok": True}

    @app.post("/api/memory/tidy", dependencies=dep)
    async def tidy_memory(dry_run: bool = False) -> dict[str, Any]:
        """One tidy-up pass now (merge duplicates, drop non-facts); ``dry_run`` only plans."""
        try:
            return await svc.tidy_memory(dry_run=dry_run)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/memory/changes", dependencies=dep)
    async def memory_changes(limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
        """What tidy-ups and updates changed, newest first; each entry can be restored."""
        if svc.app.memory is None:
            return []
        return [c.to_dict() for c in svc.app.memory.history(limit)]

    @app.post("/api/memory/changes/{change_id}/restore", dependencies=dep)
    async def restore_memory_change(change_id: str) -> dict[str, Any]:
        if svc.app.memory is None:
            raise HTTPException(400, "memory is disabled")
        change = svc.app.memory.restore(change_id)
        if change is None:
            raise HTTPException(404, "no such change")
        svc.bus.publish({"kind": "memory"})
        return change.to_dict()

    # ------------------------------------------------------------------ ideas
    @app.get("/api/ideas", dependencies=dep)
    async def ideas(refresh: bool = False) -> dict[str, Any]:
        if refresh:
            try:
                return await svc.refresh_ideas()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ideas refresh failed: {}", exc)
                data = svc.cached_ideas()
                data["error"] = f"{type(exc).__name__}: {exc}"
                return data
        return svc.cached_ideas()

    # ------------------------------------------------------------------ activity / settings
    @app.get("/api/activity", dependencies=dep)
    async def activity(n: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        return svc.activity(n)

    @app.get("/api/settings", dependencies=dep)
    async def get_settings() -> dict[str, Any]:
        return svc.settings_view()

    @app.put("/api/settings", dependencies=dep)
    async def put_settings(body: SettingsBody) -> dict[str, Any]:
        out = svc.update_settings(body.model_dump(exclude_none=True))
        if body.auto_update:
            auto_update_wake.set()
        return out

    # ------------------------------------------------------------------ files
    # ------------------------------------------------------------------ connections
    conn = svc.connections

    @app.get("/api/connections", dependencies=dep)
    async def get_connections() -> dict[str, Any]:
        return conn.view()

    @app.get("/api/system/capabilities", dependencies=dep)
    async def system_capabilities(
        client_ram_gb: float | None = Query(default=None, ge=0.0, le=4096.0),
        client_cores: int | None = Query(default=None, ge=1, le=512),
        client_mobile: bool | None = Query(default=None),
    ) -> dict[str, Any]:
        """Inspect host specs, running local AI servers, and advise on local model feasibility."""
        from nanomuse.server.capabilities import inspect_system_capabilities

        return await inspect_system_capabilities(
            client_ram_gb=client_ram_gb,
            client_cores=client_cores,
            client_mobile=client_mobile,
        )

    @app.put("/api/connections/llm", dependencies=dep)
    async def put_llm(body: LLMBody) -> dict[str, Any]:
        try:
            return conn.set_llm(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/connections/llm/options", dependencies=dep)
    async def put_llm_options(body: ModelOptionsBody) -> list[dict[str, str]]:
        return conn.set_model_options(body.options)

    @app.post("/api/connections/llm/apply-defaults", dependencies=dep)
    async def post_llm_apply_defaults(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        return conn.apply_recommended_defaults(body if isinstance(body, dict) else {})

    @app.get("/api/connections/llm/routing", dependencies=dep)
    async def get_llm_routing() -> dict[str, Any]:
        return conn.llm_routing_view()

    @app.put("/api/connections/llm/routing", dependencies=dep)
    async def put_llm_routing(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, "body must be a JSON object")
        return conn.set_llm_routing(body)

    @app.get("/api/connections/llm/usage-status", dependencies=dep)
    async def get_llm_usage_status() -> dict[str, Any]:
        return conn.usage_status()

    @app.post("/api/connections/llm/usage-status/refresh", dependencies=dep)
    async def refresh_llm_usage_status() -> dict[str, Any]:
        return await conn.refresh_usage_status()

    @app.post("/api/connections/llm/test", dependencies=dep)
    async def test_llm() -> dict[str, Any]:
        return await conn.test_llm()

    @app.post("/api/llm/models", dependencies=dep)
    async def llm_models(body: LLMModelsBody) -> dict[str, Any]:
        """The models an endpoint offers (its /models, else the preset's catalogue); saves nothing."""
        try:
            return await conn.llm_models(body.model_dump())
        except ValueError as exc:  # an address with user:pass@ in it
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------------ endpoints (#294)
    @app.post("/api/connections/endpoints", dependencies=dep)
    async def add_endpoint(body: EndpointBody) -> dict[str, Any]:
        """A custom OpenAI-compatible endpoint added to the list: its models are asked for
        (``GET /models``) unless typed; 400 when there is neither, 409 for an address
        another endpoint already has."""
        try:
            return await conn.endpoints.add(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(_endpoint_status(exc), str(exc)) from exc

    def _endpoint_status(exc: ValueError) -> int:
        text = str(exc)
        if text.startswith("no endpoint"):
            return 404
        return 409 if text.startswith("that address is already") else 400

    @app.put("/api/connections/endpoints/{endpoint_id}", dependencies=dep)
    async def update_endpoint(endpoint_id: str, body: EndpointBody) -> dict[str, Any]:
        """Rename, change the key or the address, retype the models; the slots that use the
        endpoint follow a new address or key. 409 for an address another endpoint has."""
        try:
            return await conn.endpoints.update(endpoint_id, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(_endpoint_status(exc), str(exc)) from exc

    @app.delete("/api/connections/endpoints/{endpoint_id}", dependencies=dep)
    async def remove_endpoint(endpoint_id: str) -> dict[str, Any]:
        """The endpoint, its key and the hands, picture and clip slots that used it (back to
        automatic); 409 while the chat model answers through it."""
        try:
            if not conn.endpoints.remove(endpoint_id):
                raise HTTPException(404, "no such endpoint")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/connections/endpoints/{endpoint_id}/models", dependencies=dep)
    async def refresh_endpoint_models(endpoint_id: str) -> dict[str, Any]:
        """Ask the endpoint for its models again; the saved list stays when it does not answer."""
        try:
            return await conn.endpoints.refresh_models(endpoint_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ------------------------------------------------------------------ providers (contract C11)
    @app.get("/api/providers", dependencies=dep)
    async def providers(
        lang: str = Query("", max_length=10), region: str = Query("", max_length=10)
    ) -> dict[str, Any]:
        """The catalogue with what each slot uses, the union of what that covers, and one
        sentence per capability nothing covers (``lang``: en|zh; ``region``: cn|global)."""
        return svc.providers.view(lang=lang.strip().lower(), region=region.strip().lower())

    # ------------------------------------------------------------------ the ChatGPT sign-in
    def _chatgpt_http(exc: ChatGPTError) -> HTTPException:
        status = {"state_mismatch": 400, "bad_callback": 400, "no_login": 409}.get(exc.code, 502)
        return HTTPException(status, f"{exc.message} ({exc.code})")

    @app.post("/api/chatgpt/login", dependencies=dep)
    async def chatgpt_login() -> dict[str, Any]:
        """Start the Codex sign-in (the flow of ``nanomuse chatgpt login``) in the background:
        the page to open, where the callback is expected; the one under way when there is."""
        return await svc.chatgpt.login()

    @app.get("/api/chatgpt/status", dependencies=dep)
    async def chatgpt_status() -> dict[str, Any]:
        """The token store without the tokens; ``pending`` and ``url`` while a sign-in waits;
        ``error`` after one failed, until the next login."""
        return svc.chatgpt.status()

    @app.get("/api/chatgpt/usage", dependencies=dep)
    async def chatgpt_usage() -> dict[str, Any]:
        """What is left of the plan's usage windows, as OpenAI reports it (one request to
        chatgpt.com at most once a minute; ``limits`` null when it reports nothing)."""
        return await svc.chatgpt.usage()

    @app.post("/api/chatgpt/callback", dependencies=dep)
    async def chatgpt_callback(body: ChatGptCallbackBody) -> dict[str, Any]:
        """The callback address pasted from the browser's address bar, for a runtime the
        browser cannot reach on port 1455; a ``state`` that is not the pending login's is 400."""
        try:
            return svc.chatgpt.callback(body.url)
        except ChatGPTError as exc:
            raise _chatgpt_http(exc) from exc

    @app.post("/api/chatgpt/logout", dependencies=dep)
    async def chatgpt_logout() -> dict[str, Any]:
        """Forget the sign-in (and stop one under way); nothing is revoked upstream."""
        return await svc.chatgpt.logout()

    @app.put("/api/connections/embeddings", dependencies=dep)
    async def put_embeddings(body: EmbeddingsBody) -> dict[str, Any]:
        try:
            return conn.set_embeddings(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connections/embeddings/test", dependencies=dep)
    async def test_embeddings() -> dict[str, Any]:
        return await conn.test_embeddings()

    @app.put("/api/connections/search", dependencies=dep)
    async def put_search(body: SearchBody) -> dict[str, Any]:
        try:
            return conn.set_search(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connections/search/test", dependencies=dep)
    async def test_search() -> dict[str, Any]:
        return await conn.test_search()

    @app.put("/api/connections/email", dependencies=dep)
    async def put_email(body: EmailBody) -> dict[str, Any]:
        return conn.set_email(body.model_dump(exclude_none=True))

    @app.delete("/api/connections/email", dependencies=dep)
    async def delete_email() -> dict[str, Any]:
        return conn.disconnect_email()

    @app.post("/api/connections/email/test", dependencies=dep)
    async def test_email() -> dict[str, Any]:
        return await conn.test_email()

    @app.get("/api/connections/google/status", dependencies=dep)
    async def google_status() -> dict[str, Any]:
        return conn.google.status()

    @app.post("/api/connections/google/oauth/start", dependencies=dep)
    async def google_oauth_start(body: GoogleOAuthStartBody | None = None) -> dict[str, Any]:
        from nanomuse.connectors.google_oauth import GoogleOAuthError

        try:
            return await conn.google_oauth_start(
                client_id=body.client_id if body else None,
                client_secret=body.client_secret if body else None,
            )
        except GoogleOAuthError as exc:
            raise HTTPException(400, exc.message) from exc

    @app.post("/api/connections/google/oauth/callback", dependencies=dep)
    async def google_oauth_callback(body: GoogleOAuthCallbackBody) -> dict[str, Any]:
        from nanomuse.connectors.google_oauth import GoogleOAuthError

        try:
            return await conn.google_oauth_callback(body.url)
        except GoogleOAuthError as exc:
            raise HTTPException(400, exc.message) from exc

    @app.post("/api/connections/google/sync", dependencies=dep)
    async def google_sync(email: str | None = Query(None, max_length=200)) -> dict[str, Any]:
        from nanomuse.connectors.google_oauth import GoogleOAuthError

        try:
            return await conn.google_sync(email=email)
        except GoogleOAuthError as exc:
            raise HTTPException(400, exc.message) from exc

    @app.post("/api/connections/google/oauth/logout", dependencies=dep)
    async def google_oauth_logout(email: str | None = Query(None, max_length=200)) -> dict[str, Any]:
        return await conn.google_logout(email=email)

    @app.post("/api/connections/google/enable-apis", dependencies=dep)
    async def google_enable_apis() -> dict[str, Any]:
        apis = conn.google_enable_apis()
        return {"ok": True, "apis": apis}

    @app.post("/api/connections/apis/enable-free", dependencies=dep)
    async def apis_enable_free() -> dict[str, Any]:
        apis = conn.enable_free_apis()
        return {"ok": True, "apis": apis}

    # ------------------------------------------------------------------ multi-account email
    @app.get("/api/connections/email/accounts", dependencies=dep)
    async def list_email_accounts() -> list[dict[str, Any]]:
        return conn.email_accounts.list()

    @app.post("/api/connections/email/accounts", dependencies=dep)
    async def upsert_email_account(body: EmailAccountBody) -> list[dict[str, Any]]:
        return conn.upsert_email_account(body.model_dump(exclude_none=True))

    @app.delete("/api/connections/email/accounts/{account_id}", dependencies=dep)
    async def delete_email_account(account_id: str) -> list[dict[str, Any]]:
        try:
            return conn.remove_email_account(account_id)
        except KeyError as exc:
            raise HTTPException(404, "no such email account") from exc

    @app.post("/api/connections/email/accounts/{account_id}/test", dependencies=dep)
    async def test_email_account_route(account_id: str) -> dict[str, Any]:
        return await conn.test_email_account(account_id)

    # ------------------------------------------------------------------ universal API presets & custom APIs
    @app.get("/api/connections/presets", dependencies=dep)
    async def list_api_presets(q: str = Query("", max_length=120)) -> dict[str, Any]:
        from nanomuse.connectors.api_registry import preset_list

        items = preset_list()
        if q.strip():
            needle = q.strip().lower()
            items = [
                p
                for p in items
                if needle in p["id"].lower()
                or needle in p["name"].lower()
                or needle in p["category"].lower()
                or needle in p["description"].lower()
            ]
        return {"count": len(items), "presets": items}

    @app.get("/api/connections/apis", dependencies=dep)
    async def list_custom_apis() -> dict[str, Any]:
        return {"apis": svc.app.api_registry.list()}

    @app.post("/api/connections/apis", dependencies=dep)
    async def upsert_custom_api(body: ApiConnectionBody) -> dict[str, Any]:
        try:
            saved = svc.app.api_registry.upsert(body.model_dump(exclude_none=True))
            conn._publish()
            return saved
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/api/connections/apis/{api_id}", dependencies=dep)
    async def patch_custom_api(api_id: str, body: ApiConnectionBody) -> dict[str, Any]:
        payload = body.model_dump(exclude_none=True)
        payload["id"] = api_id
        try:
            saved = svc.app.api_registry.upsert(payload)
            conn._publish()
            return saved
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/connections/apis/{api_id}", dependencies=dep)
    async def delete_custom_api(api_id: str) -> dict[str, Any]:
        if not svc.app.api_registry.remove(api_id):
            raise HTTPException(404, "no such API connection")
        conn._publish()
        return {"ok": True}

    @app.post("/api/connections/apis/{api_id}/endpoints", dependencies=dep)
    async def save_api_endpoint(api_id: str, body: ApiEndpointBody) -> dict[str, Any]:
        try:
            saved = svc.app.api_registry.save_endpoint(
                api_id, body.model_dump(), learned_notes=body.learned_notes
            )
            conn._publish()
            return saved
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/connections/apis/{api_id}/test", dependencies=dep)
    async def test_custom_api(api_id: str) -> dict[str, Any]:
        try:
            res = await svc.app.api_registry.test(api_id)
            conn._publish()
            return res
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/connections/apis/{api_id}/discover", dependencies=dep)
    async def discover_custom_api(api_id: str, body: ApiDiscoverBody | None = None) -> dict[str, Any]:
        try:
            res = await svc.app.api_registry.discover_openapi(
                api_id=api_id, spec_url=body.spec_url if body else ""
            )
            conn._publish()
            return res
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connections/apis/request", dependencies=dep)
    async def execute_custom_api_request(body: ApiRequestBody) -> dict[str, Any]:
        try:
            return await svc.app.api_registry.execute(
                api_id=body.api_id,
                url=body.url,
                method=body.method,
                path=body.path,
                headers=body.headers,
                params=body.params,
                body=body.body,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/connections/gui", dependencies=dep)
    async def set_gui(body: GuiBody) -> dict[str, Any]:
        try:
            return svc.connections.set_gui(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connections/gui/test", dependencies=dep)
    async def test_gui() -> dict[str, Any]:
        return await svc.connections.test_gui()

    @app.get("/api/connections/image", dependencies=dep)
    async def get_image() -> dict[str, Any]:
        return conn.media_view("image")

    @app.put("/api/connections/image", dependencies=dep)
    async def put_image(body: MediaBody) -> dict[str, Any]:
        try:
            return conn.set_media("image", body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/connections/video", dependencies=dep)
    async def get_video() -> dict[str, Any]:
        return conn.media_view("video")

    @app.put("/api/connections/video", dependencies=dep)
    async def put_video(body: MediaBody) -> dict[str, Any]:
        try:
            return conn.set_media("video", body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/hands", dependencies=dep)
    async def hands_status() -> dict[str, Any]:
        return svc.hands_view()

    @app.put("/api/connections/hands", dependencies=dep)
    async def set_hands(body: HandsBody) -> dict[str, Any]:
        try:
            return svc.connections.set_hands(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/hands/stop", dependencies=dep)
    async def stop_hands() -> dict[str, Any]:
        return {"stopped": svc.stop_hands()}

    # ------------------------------------------------------------------ holds (C1)
    @app.get("/api/holds", dependencies=dep)
    async def holds_list() -> list[dict[str, Any]]:
        """The holds that are on: the person has the browser, the screen or the phone."""
        return svc.holds_view()

    @app.post("/api/holds", dependencies=dep)
    async def hold_open(body: HoldBody) -> dict[str, Any]:
        """Take over: the agent's actions of that kind in that chat wait for Done."""
        try:
            return svc.open_hold(body.thread, body.tool, body.reason or "")
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/holds/{hold_id}/done", dependencies=dep)
    async def hold_done(hold_id: str) -> dict[str, Any]:
        """Done: the hold goes off; the waiting tool looks again and continues."""
        try:
            return svc.done_hold(hold_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    # ------------------------------------------------------------------ the avatar studio
    def _studio_http(exc: StudioError) -> HTTPException:
        return HTTPException(409, str(exc))

    @app.get("/api/avatar", dependencies=dep)
    async def avatar_view() -> dict[str, Any]:
        """Whether a face can be drawn (an image model on the chat model's host), and the
        session under way if any."""
        return svc.avatar.view()

    @app.post("/api/avatar/begin", dependencies=dep)
    async def avatar_begin(body: AvatarBeginBody) -> dict[str, Any]:
        """A new session from a description: the card with the cost appears in the chat."""
        return await svc.avatar.begin(body.thread, body.description.strip(), body.style)

    @app.post("/api/avatar/start", dependencies=dep)
    async def avatar_start(body: AvatarSessionBody) -> dict[str, Any]:
        """The tap on *Draw*: four candidates (also *redraw*)."""
        try:
            return await svc.avatar.start(body.session)
        except StudioError as exc:
            raise _studio_http(exc) from exc

    @app.post("/api/avatar/choose", dependencies=dep)
    async def avatar_choose(body: AvatarSessionBody) -> dict[str, Any]:
        if body.index is None:
            raise HTTPException(422, "index is required")
        try:
            return await svc.avatar.choose(body.session, body.index)
        except StudioError as exc:
            raise _studio_http(exc) from exc

    @app.post("/api/avatar/cancel", dependencies=dep)
    async def avatar_cancel(body: AvatarSessionBody) -> dict[str, Any]:
        try:
            return svc.avatar.cancel(body.session)
        except StudioError as exc:
            raise _studio_http(exc) from exc

    @app.post("/api/avatar/moods", dependencies=dep)
    async def avatar_moods() -> dict[str, Any]:
        """*Redraw the poses* of the face the profile wears, from its idle still (and the
        clips, where the endpoint has a video model)."""
        try:
            return await svc.avatar.redraw_moods()
        except StudioError as exc:
            raise _studio_http(exc) from exc

    @app.get("/api/phone", dependencies=dep)
    async def phone_status() -> dict[str, Any]:
        view = svc.phone_view()
        if svc.phone.last_screen is not None:
            view["screen"] = svc.phone.last_screen.to_dict()
        return view

    # ------------------------------------------------------------------ cloud account & hub
    def _cloud_http(exc: CloudError) -> HTTPException:
        """A relay refusal as the app sees it: the sentence from :data:`nanomuse.cloud.MESSAGES`
        (the web app translates it) and the relay's code in a header, for apps that act on it."""
        return HTTPException(
            exc.status if exc.status >= 400 else 502,
            exc.describe(),
            headers={"X-Nanomuse-Code": exc.code or "error"},
        )

    @app.get("/api/cloud", dependencies=dep)
    async def cloud_status() -> dict[str, Any]:
        return svc.hub.account_view()

    @app.get("/api/cloud/config", dependencies=dep)
    async def cloud_config() -> dict[str, Any]:
        """The relay's public figures — the allowance a new account gets, the invite bonus,
        whether sign-up is open, the repository to star — so the app prints the relay's
        numbers, not its own (relay 0.15). An older relay or no relay: ``{}``."""
        try:
            return await svc.hub.cloud.config()
        except CloudError:
            return {}

    @app.post("/api/cloud/code", dependencies=dep)
    async def cloud_code(body: CloudCodeBody) -> dict[str, Any]:
        try:
            await svc.hub.request_code(body.identifier)
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return {"ok": True}

    def _with_nudges(data: dict[str, Any]) -> dict[str, Any]:
        """A sign-in or an account read answers with the relay's ``/v1/me``, which carries
        the nudges policy (contract C1): that copy is today's, no separate fetch needed."""
        svc.nudges.take(data.get("nudges"))
        return data

    @app.post("/api/cloud/verify", dependencies=dep)
    async def cloud_verify(body: CloudVerifyBody) -> dict[str, Any]:
        try:
            return _with_nudges(
                await svc.hub.verify(body.identifier, body.code, invite=body.invite)
            )
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.post("/api/cloud/login", dependencies=dep)
    async def cloud_login(body: CloudLoginBody) -> dict[str, Any]:
        try:
            return _with_nudges(await svc.hub.login(body.identifier, body.password))
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.get("/api/nudges", dependencies=dep)
    async def nudges(refresh: bool = False) -> dict[str, Any]:
        """When the app may ask for a star on GitHub (contract C1): the relay's policy, read
        at most once a day (``?refresh=1`` asks now), the last good copy or the built-in
        defaults otherwise. The web keeps its own ledger of asks shown."""
        return await svc.nudges.refresh(force=refresh)

    @app.post("/api/cloud/password", dependencies=dep)
    async def cloud_password(body: CloudPasswordBody) -> dict[str, Any]:
        try:
            return await svc.hub.set_password(body.password, body.current)
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.get("/api/cloud/sessions", dependencies=dep)
    async def cloud_sessions() -> dict[str, Any]:
        try:
            return {"sessions": await svc.hub.sessions()}
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.delete("/api/cloud/sessions/{prefix}", dependencies=dep)
    async def cloud_revoke_session(prefix: str) -> dict[str, Any]:
        try:
            await svc.hub.revoke_session(prefix)
            return {"sessions": await svc.hub.sessions()}
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.post("/api/cloud/sign-out-all", dependencies=dep)
    async def cloud_sign_out_all(body: CloudSignOutAllBody) -> dict[str, Any]:
        try:
            n = await svc.hub.sign_out_all(body.all)
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return {"signed_out": n, **svc.hub.account_view()}

    @app.get("/api/cloud/events", dependencies=dep)
    async def cloud_events(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        try:
            return {"events": await svc.hub.events(limit)}
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.post("/api/cloud/delete", dependencies=dep)
    async def cloud_delete_account() -> dict[str, Any]:
        try:
            await svc.hub.delete_account()
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return svc.hub.account_view()

    @app.post("/api/cloud/sign-out", dependencies=dep)
    async def cloud_sign_out() -> dict[str, Any]:
        await svc.hub.sign_out()
        return svc.hub.account_view()

    @app.get("/api/cloud/me", dependencies=dep)
    async def cloud_me() -> dict[str, Any]:
        try:
            return _with_nudges(await svc.hub.me())
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.post("/api/cloud/models", dependencies=dep)
    async def cloud_models(body: CloudModelsBody) -> dict[str, Any]:
        """*Use nanoMuse Cloud models*: off, the account's models leave the automatic order
        of the hands, pictures and clips and the providers' listing; the sign-in stays.
        ``409 chat_on_cloud`` while the chat model is the account's."""
        try:
            return svc.hub.set_models(body.on)
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.post("/api/cloud/contribute", dependencies=dep)
    async def cloud_contribute(body: CloudContributeBody) -> dict[str, Any]:
        """Data controls: turn "Help improve nanoMuse's AI models" on or off for the account
        (the relay keeps the text of chat turns only while it is on)."""
        try:
            return await svc.hub.set_contribute(body.on)
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    @app.delete("/api/cloud/samples", dependencies=dep)
    async def cloud_delete_samples() -> dict[str, Any]:
        try:
            return {"deleted": await svc.hub.delete_samples()}
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    # ------------------------------------------------------------------ conversation sync (contract C7)
    async def _sync_view() -> dict[str, Any]:
        return {**svc.sync.view(), "relay": await svc.sync.relay_state()}

    @app.get("/api/sync/state", dependencies=dep)
    async def sync_state() -> dict[str, Any]:
        """Data controls: the switch here, whether the account is signed in (``available``),
        the cursor, and the relay's own counts under ``relay`` (null when it cannot be asked)."""
        return await _sync_view()

    @app.put("/api/sync/state", dependencies=dep)
    async def sync_set_state(body: SyncStateBody) -> dict[str, Any]:
        """``{enabled}``: off tells the relay, which deletes everything stored for the account;
        on re-pushes this device's chats and pulls the others'. ``{side_chats}`` (C9, per
        device): on sends this device's side chats too and pulls the other devices' once
        from the start; off stops both and leaves what was synced where it is."""
        if body.enabled is None and body.side_chats is None:
            raise HTTPException(400, "say enabled or side_chats")
        try:
            if body.enabled is not None:
                await svc.sync.set_enabled(body.enabled)
            if body.side_chats is not None:
                svc.sync.set_side_chats(body.side_chats)
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return await _sync_view()

    @app.post("/api/sync/delete", dependencies=dep)
    async def sync_delete() -> dict[str, Any]:
        """“Delete synced conversations”: the relay's store emptied; the switch and the local
        chats stay."""
        try:
            await svc.sync.delete_remote()
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return await _sync_view()

    @app.post("/api/sync/pull", dependencies=dep)
    async def sync_pull() -> dict[str, Any]:
        try:
            applied = await svc.sync.pull()
        except CloudError as exc:
            raise _cloud_http(exc) from exc
        return {"applied": applied, **svc.sync.view()}

    @app.get("/api/sync/peers", dependencies=dep)
    async def sync_peers_list() -> dict[str, Any]:
        return svc.sync.view()

    @app.get("/api/sync/discover", dependencies=dep)
    async def sync_discover() -> dict[str, Any]:
        return {
            "nearby": svc.sync.discovery.list_nearby(),
            "device_id": svc.hub.device_id,
            "name": svc.hub.device_name,
            "urls": svc.sync.view().get("urls", []),
        }

    @app.post("/api/sync/pair/code", dependencies=dep)
    async def sync_pair_code() -> dict[str, Any]:
        """Generate a short-lived 6-character pairing code & QR link for another device."""
        from nanomuse.server.channels_api import _qr_png

        info = svc.sync.issue_pair_code()
        uri = str(info.get("pair_uri") or "")
        return {**info, "qr_png": _qr_png(uri) if uri else ""}

    @app.post("/api/sync/pair/claim", dependencies=dep)
    async def sync_pair_with(body: PairWithBody) -> dict[str, Any]:
        """Pair this device directly with another device (no nanoMuse Cloud required)."""
        try:
            return await svc.sync.pair_with_remote(
                target=body.target,
                code=body.code,
                token=body.token,
                name=body.name,
                sync_side_chats=body.sync_side_chats,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/sync/pair/unpair", dependencies=dep)
    async def sync_unpair(body: UnpairBody) -> dict[str, Any]:
        removed = svc.sync.unpair_peer(body.identifier)
        return {"ok": removed, **svc.sync.view()}

    @app.put("/api/sync/device-overrides", dependencies=dep)
    async def put_sync_device_overrides(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        return svc.sync.set_device_overrides(body if isinstance(body, dict) else {})

    @app.get("/api/sync/setup-bundle", dependencies=dep)
    @app.get("/v1/sync/setup-bundle", dependencies=dep_sync)
    async def get_sync_setup_bundle() -> dict[str, Any]:
        return svc.sync.export_setup_bundle()

    @app.post("/api/sync/setup-bundle", dependencies=dep)
    async def post_sync_setup_bundle(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = {}
        return svc.sync.apply_setup_bundle(body if isinstance(body, dict) else {})

    @app.post("/api/sync/now", dependencies=dep)
    async def sync_now() -> dict[str, Any]:
        """Mirror offline local edits and bidirectionally pull/merge/push all peers & Cloud."""
        svc.sync.mirror_local_now()
        peer_res = await svc.sync.sync_peers()
        cloud_applied = 0
        if svc.hub.signed_in and svc.sync.enabled:
            try:
                cloud_applied = await svc.sync.pull()
                await svc.sync.push()
            except CloudError:
                pass
        return {
            "ok": True,
            "applied": int(peer_res.get("applied") or 0) + cloud_applied,
            "pushed": int(peer_res.get("pushed") or 0),
            **svc.sync.view(),
        }

    @app.post("/api/cloud/use-as-model", dependencies=dep)
    async def cloud_use_as_model(body: CloudModelBody) -> dict[str, Any]:
        try:
            return await svc.hub.use_as_model(body.model)
        except CloudError as exc:
            raise _cloud_http(exc) from exc

    # ------------------------------------------------------------------ coding agents
    def _coding_error(exc: CodingError) -> HTTPException:
        status = {
            "unknown_agent": 404,
            "no_session": 404,
            "no_device": 404,
            "device_offline": 409,
            "busy": 409,
            "not_installed": 412,
            "not_supported": 412,
            "usage": 400,
            "unknown_action": 400,
        }.get(exc.code, 502)
        return HTTPException(status, f"{exc.message} ({exc.code})")

    @app.get("/api/coding", dependencies=dep)
    async def coding_status(device: str = "") -> dict[str, Any]:
        """Installed agents and recent runs — here, or on another computer of the account."""
        try:
            if device:
                remote = await svc.coding.remote(device, "coding.agents", {})
                runs = await svc.coding.remote(device, "coding.runs", {})
                return {"device": device, **remote, **runs}
            # detection shells out to each CLI and walks its session folders: off the loop
            return {
                "agents": await asyncio.to_thread(svc.coding.agents),
                "runs": svc.coding.list_runs(),
            }
        except CodingError as exc:
            raise _coding_error(exc) from exc

    @app.get("/api/coding/sessions", dependencies=dep)
    async def coding_sessions(
        agent: str = "", limit: int = 30, workspace: str = "", device: str = ""
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        args: dict[str, Any] = {"agent": agent, "limit": limit, "workspace": workspace}
        try:
            if device:
                return await svc.coding.remote(device, "coding.sessions", args)
            return {
                "sessions": await asyncio.to_thread(
                    svc.coding.sessions, agent or None, limit, workspace or None
                )
            }
        except CodingError as exc:
            raise _coding_error(exc) from exc

    @app.get("/api/coding/sessions/{agent}/{session_id}", dependencies=dep)
    async def coding_session(agent: str, session_id: str, device: str = "") -> dict[str, Any]:
        try:
            if device:
                return await svc.coding.remote(
                    device, "coding.session", {"agent": agent, "session_id": session_id}
                )
            return await asyncio.to_thread(svc.coding.session, agent, session_id)
        except CodingError as exc:
            raise _coding_error(exc) from exc

    @app.post("/api/coding/send", dependencies=dep)
    async def coding_send(body: CodingSendBody) -> dict[str, Any]:
        """A message into a coding agent's session; returns the run at once — follow it on
        the WebSocket (``kind: "coding"``) or poll ``/api/coding``."""
        try:
            if body.device:
                return await svc.coding.remote_send(
                    body.device,
                    {
                        "agent": body.agent,
                        "text": body.text,
                        "session_id": body.session_id,
                        "workspace": body.workspace,
                    },
                )
            return await svc.coding.send(
                body.agent, body.text, session_id=body.session_id, workspace=body.workspace
            )
        except CodingError as exc:
            raise _coding_error(exc) from exc

    @app.post("/api/coding/stop", dependencies=dep)
    async def coding_stop(body: CodingStopBody) -> dict[str, Any]:
        try:
            if body.device:
                return await svc.coding.remote(body.device, "coding.stop", {"run": body.run})
            return {"stopped": svc.coding.stop(body.run)}
        except CodingError as exc:
            raise _coding_error(exc) from exc

    @app.get("/api/hub", dependencies=dep)
    async def hub_status() -> dict[str, Any]:
        return svc.hub.view()

    @app.put("/api/hub", dependencies=dep)
    async def hub_update(body: HubBody) -> dict[str, Any]:
        try:
            if body.name is not None:
                await svc.hub.rename(body.name)
            if body.remote_control is not None:
                svc.hub.set_remote_control(body.remote_control)
            if body.enabled is not None:
                await svc.hub.set_enabled(body.enabled)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return svc.hub.view()

    @app.post("/api/hub/join", dependencies=dep)
    async def hub_join() -> dict[str, Any]:
        if not svc.hub.signed_in:
            raise HTTPException(401, "Sign in to nanoMuse Cloud first.")
        await svc.hub.set_enabled(True)
        return svc.hub.view()

    @app.post("/api/hub/leave", dependencies=dep)
    async def hub_leave() -> dict[str, Any]:
        await svc.hub.set_enabled(False)
        return svc.hub.view()

    @app.post("/api/hub/refresh", dependencies=dep)
    async def hub_refresh() -> dict[str, Any]:
        if svc.hub.client is not None and svc.hub.client.connected.is_set():
            await svc.hub.client.request_devices()
        return svc.hub.view()

    @app.delete("/api/hub/devices/{device_id}", dependencies=dep)
    async def hub_forget(device_id: str) -> dict[str, Any]:
        try:
            await svc.hub.forget(device_id)
        except HubError as exc:
            raise HTTPException(502, exc.message) from exc
        return svc.hub.view()

    @app.post("/api/hub/ask", dependencies=dep)
    async def hub_ask(body: AskDeviceBody) -> dict[str, Any]:
        """The side chat addressed to a device (created if needed); with ``text``, the first
        message in it, which runs on that device."""
        device = svc.hub.device(body.device) or svc.hub.find(body.device)
        if device is None:
            raise HTTPException(404, f"no device matches '{body.device}'")
        thread = svc.hub.ask_device(str(device["id"]))
        event = None
        if body.text.strip():
            try:
                event = svc.send(thread.id, body.text)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        return {"thread": thread.meta(), "event": event}

    @app.put("/api/connections/browser", dependencies=dep)
    async def put_browser(body: BrowserBody) -> dict[str, Any]:
        return conn.set_browser(body.enabled)

    @app.post("/api/connections/browser/install", dependencies=dep)
    async def install_browser() -> dict[str, Any]:
        """Install Playwright and its Chromium in the background: one tap instead of a command."""
        from nanomuse.server import browser_install

        return browser_install.start()

    @app.get("/api/connections/browser/install", dependencies=dep)
    async def browser_install_status() -> dict[str, Any]:
        from nanomuse.server import browser_install
        from nanomuse.tools.browser import playwright_available

        return {**browser_install.status(), "available": playwright_available()}

    @app.put("/api/connections/calendar", dependencies=dep)
    async def put_calendar(body: CalendarBody) -> dict[str, Any]:
        try:
            return conn.set_calendar(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connections/calendar/feeds", dependencies=dep)
    async def add_calendar_feed(body: CalendarFeedBody) -> dict[str, Any]:
        try:
            return await conn.add_calendar_feed(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/connections/calendar/feeds/{name}", dependencies=dep)
    async def delete_calendar_feed(name: str) -> dict[str, Any]:
        if not conn.remove_calendar_feed(name):
            raise HTTPException(404, "no such calendar (feeds from config.toml are removed there)")
        return conn.view()["calendar"]

    @app.post("/api/connections/calendar/test", dependencies=dep)
    async def test_calendar() -> dict[str, Any]:
        return await conn.test_calendar()

    @app.post("/api/connections/calendar/sync", dependencies=dep)
    async def sync_calendar() -> dict[str, Any]:
        return await conn.sync_calendar()

    @app.post("/api/connections/calendar/import", dependencies=dep)
    async def import_calendar_feed(body: CalendarImportBody) -> dict[str, Any]:
        try:
            return await conn.import_calendar_ics(body.name, body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/calendar", dependencies=dep)
    async def calendar(
        days: int = Query(2, ge=1, le=90),
        start: str | None = Query(None, max_length=20),
        refresh: int = 0,
    ) -> dict[str, Any]:
        if refresh:
            await svc.app.calendar.refresh(force=True)
        else:
            await svc.app.calendar.refresh()
        return svc.calendar_view(days, start_date=start)

    @app.post("/api/calendar/events", dependencies=dep)
    async def create_calendar_event(body: CalendarEventBody) -> dict[str, Any]:
        store = svc.app.local_calendar
        try:
            ev = store.create(
                summary=body.summary,
                start=body.start,
                end=body.end,
                all_day=body.all_day,
                location=body.location,
                description=body.description,
                calendar=body.calendar,
                tags=body.tags,
                contact_name=body.contact_name,
                rrule=body.rrule,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        svc.bus.publish({"kind": "calendar"})
        return ev

    @app.patch("/api/calendar/events/{uid}", dependencies=dep)
    async def update_calendar_event(uid: str, body: CalendarEventPatchBody) -> dict[str, Any]:
        store = svc.app.local_calendar
        try:
            ev = store.update(uid, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if ev is None:
            raise HTTPException(404, "no such local calendar event")
        svc.bus.publish({"kind": "calendar"})
        return ev

    @app.delete("/api/calendar/events/{uid}", dependencies=dep)
    async def delete_calendar_event(uid: str) -> dict[str, Any]:
        if not svc.app.local_calendar.delete(uid):
            raise HTTPException(404, "no such local calendar event")
        svc.bus.publish({"kind": "calendar"})
        return {"ok": True}

    # ------------------------------------------------------------------ location & travel
    @app.get("/api/location", dependencies=dep)
    async def get_location_summary() -> dict[str, Any]:
        return svc.app.location.summary(svc.app.contacts.contacts)

    @app.get("/api/location/search", dependencies=dep)
    async def search_location(
        q: str = Query("", max_length=200),
        tag: str = Query("", max_length=60),
        category: str = Query("", max_length=40),
    ) -> dict[str, Any]:
        return svc.app.location.search(
            q, contacts=svc.app.contacts.contacts, tag=tag, category=category
        )

    @app.post("/api/location/places", dependencies=dep)
    async def add_location_place(body: PlaceBody) -> dict[str, Any]:
        try:
            place = svc.app.location.add_place(**body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return place

    @app.patch("/api/location/places/{place_id}", dependencies=dep)
    async def patch_location_place(place_id: str, body: PlacePatchBody) -> dict[str, Any]:
        place = svc.app.location.update_place(place_id, body.model_dump(exclude_none=True))
        if place is None:
            raise HTTPException(404, "no such place")
        return place

    @app.post("/api/location/places/{place_id}/visit", dependencies=dep)
    async def visit_location_place(place_id: str) -> dict[str, Any]:
        place = svc.app.location.record_visit(place_id)
        if place is None:
            raise HTTPException(404, "no such place")
        return place

    @app.delete("/api/location/places/{place_id}", dependencies=dep)
    async def delete_location_place(place_id: str) -> dict[str, Any]:
        if not svc.app.location.delete_place(place_id):
            raise HTTPException(404, "no such place")
        return {"ok": True}

    @app.post("/api/location/trips", dependencies=dep)
    async def add_location_trip(body: TripBody) -> dict[str, Any]:
        return svc.app.location.log_trip(**body.model_dump())

    @app.post("/api/location/ping", dependencies=dep)
    async def ping_location(body: TripBody) -> dict[str, Any]:
        data = body.model_dump()
        # `source` defaults to "manual" on the model, so "was it given?" must ask the request, not the
        # value: a ping that did not name its source is a position sample, not a trip someone logged
        if "source" not in body.model_fields_set:
            data["source"] = "gps_ping"
        if not data.get("place_name"):
            data["place_name"] = "Current GPS sample"
        return svc.app.location.log_trip(**data)

    @app.delete("/api/location/trips/{trip_id}", dependencies=dep)
    async def delete_location_trip(trip_id: str) -> dict[str, Any]:
        if not svc.app.location.delete_trip(trip_id):
            raise HTTPException(404, "no such trip")
        return {"ok": True}

    @app.post("/api/location/traffic", dependencies=dep)
    async def check_location_traffic(body: TrafficBody) -> dict[str, Any]:
        from nanomuse.location.store import estimate_route_and_traffic

        return await estimate_route_and_traffic(
            body.destination,
            body.origin,
            store=svc.app.location,
            contacts=svc.app.contacts.contacts,
        )

    @app.put("/api/connections/contacts", dependencies=dep)
    async def put_contacts(body: ContactsBody) -> dict[str, Any]:
        return conn.set_contacts(body.model_dump(exclude_none=True))

    @app.post("/api/connections/contacts/sources", dependencies=dep)
    async def add_contacts_source(body: ContactsSourceBody) -> dict[str, Any]:
        try:
            return await conn.add_contacts_source(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/connections/contacts/import", dependencies=dep)
    async def import_contacts(
        request: Request, name: str = Query("", max_length=40)
    ) -> dict[str, Any]:
        """The text of a ``.vcf`` file as the request body (no multipart needed; 16 MB max,
        checked as it arrives: a larger file is refused before it is all in memory)."""
        limit = 16 * 1024 * 1024
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            raise HTTPException(413, "the file is larger than 16 MB")
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(413, "the file is larger than 16 MB")
            chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            return await conn.import_contacts(name, raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/connections/contacts/sources/{name}", dependencies=dep)
    async def delete_contacts_source(name: str) -> dict[str, Any]:
        if not conn.remove_contacts_source(name):
            raise HTTPException(
                404, "no such address book (sources from config.toml are removed there)"
            )
        return conn.view()["contacts"]

    @app.post("/api/connections/contacts/test", dependencies=dep)
    async def test_contacts() -> dict[str, Any]:
        return await conn.test_contacts()

    @app.post("/api/connections/contacts/sync", dependencies=dep)
    async def sync_contacts() -> dict[str, Any]:
        return await conn.sync_contacts()

    @app.get("/api/contacts", dependencies=dep)
    async def contacts(
        q: str = Query("", max_length=200), limit: int = Query(8, ge=1, le=50)
    ) -> dict[str, Any]:
        """Look people up as the agent does (``q`` empty: the first people alphabetically)."""
        book = svc.app.contacts
        people = (
            book.search(q, limit=limit)
            if q.strip()
            else sorted(book.contacts, key=lambda c: c.name.lower())[:limit]
        )
        return {"count": len(book), "people": [c.to_dict() for c in people]}

    # ------------------------------------------------------------------ skills
    @app.get("/api/skills", dependencies=dep)
    async def skills() -> dict[str, Any]:
        return svc.skills_view()

    @app.get("/api/skills/{name}", dependencies=dep)
    async def skill(name: str) -> dict[str, Any]:
        view = svc.skill_view(name)
        if view is None:
            raise HTTPException(404, "no such skill")
        return view

    @app.put("/api/skills/{name}", dependencies=dep)
    async def put_skill(name: str, body: SkillBody) -> dict[str, Any]:
        """Write one of your skills from the text of its SKILL.md (a built-in of the same
        name is replaced by it)."""
        try:
            return svc.save_skill(body.content, name=name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(500, f"could not write the skill: {exc}") from exc

    @app.delete("/api/skills/{name}", dependencies=dep)
    async def delete_skill(name: str) -> dict[str, Any]:
        if not svc.remove_skill(name):
            raise HTTPException(
                404, "no such skill of yours (built-in skills are switched off, not removed)"
            )
        return svc.skills_view()

    @app.post("/api/skills/{name}/enabled", dependencies=dep)
    async def skill_enabled(name: str, body: SkillEnabledBody) -> dict[str, Any]:
        view = svc.set_skill_enabled(name, body.enabled)
        if view is None:
            raise HTTPException(404, "no such skill")
        return view

    @app.post("/api/skills/import", dependencies=dep)
    async def import_skill(body: SkillImportBody) -> dict[str, Any]:
        try:
            return await svc.import_skill(body.url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — a network error, reported as such
            raise HTTPException(502, f"could not fetch the skill: {exc}") from exc

    @app.post("/api/connections/mcp", dependencies=dep)
    async def add_mcp(body: MCPBody) -> dict[str, Any]:
        try:
            return await conn.add_mcp(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.delete("/api/connections/mcp/{name}", dependencies=dep)
    async def remove_mcp(name: str) -> dict[str, Any]:
        if not await conn.remove_mcp(name):
            raise HTTPException(404, "no such server (servers from config.toml are removed there)")
        return {"ok": True}

    @app.get("/api/vault", dependencies=dep)
    async def vault_names() -> list[str]:
        return svc.app.vault.names()

    @app.put("/api/vault/{name}", dependencies=dep)
    async def vault_set(name: str, body: SecretBody) -> list[str]:
        try:
            return conn.set_secret(name, body.value)
        except Exception as exc:  # noqa: BLE001 - VaultError on a bad name
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/vault/{name}", dependencies=dep)
    async def vault_delete(name: str) -> dict[str, Any]:
        if not conn.delete_secret(name):
            raise HTTPException(404, "no such secret")
        return {"ok": True}

    # ------------------------------------------------------------------ passwords & logins manager
    @app.get("/api/passwords", dependencies=dep)
    async def passwords_list(q: str = Query("", max_length=200)) -> dict[str, Any]:
        items = svc.app.passwords.list_entries(q)
        return {"items": items, "count": len(items)}

    @app.post("/api/passwords", dependencies=dep)
    async def passwords_add(body: PasswordEntryBody) -> dict[str, Any]:
        if not (body.title.strip() or body.url.strip()) or not (
            body.username.strip() or body.password
        ):
            raise HTTPException(400, "A site/title and username or password are required.")
        entry, _ = svc.app.passwords.add_or_update(
            entry_id=body.id,
            title=body.title,
            url=body.url,
            username=body.username,
            password=body.password,
            totp=body.totp,
            notes=body.notes,
            dedupe=not bool(body.id),
        )
        return entry

    @app.delete("/api/passwords/{entry_id}", dependencies=dep)
    async def passwords_delete(entry_id: str) -> dict[str, Any]:
        if not svc.app.passwords.delete(entry_id):
            raise HTTPException(404, "no such login")
        items = svc.app.passwords.list_entries()
        return {"items": items, "count": len(items)}

    @app.post("/api/passwords/import", dependencies=dep)
    async def passwords_import(body: PasswordImportBody) -> dict[str, Any]:
        try:
            return svc.app.passwords.import_data(body.text, body.filename)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Could not import passwords: {exc}") from exc

    @app.get("/api/passwords/export", dependencies=dep)
    async def passwords_export(format: str = Query("csv", max_length=10)) -> Response:
        csv_text = svc.app.passwords.export_csv()
        return Response(
            content=csv_text.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="nanomuse-passwords.csv"'},
        )

    @app.get("/api/passwords/{entry_id}/totp", dependencies=dep)
    async def passwords_totp(entry_id: str) -> dict[str, Any]:
        try:
            code, rem = svc.app.passwords.totp_code(entry_id)
            return {"code": code, "remaining_seconds": rem}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/onboarded", dependencies=dep)
    async def onboarded(body: OnboardedBody) -> dict[str, Any]:
        """The first run is complete. With a model there, the first feed day is written in
        the background right away (contract C5); ``feed_started`` says whether it was."""
        conn.set_onboarded(body.done)
        started = svc.first_feed_day() if body.done else False
        return {"onboarded": body.done, "feed_started": started}

    # ------------------------------------------------------------------ the first conversation (C4)
    @app.get("/api/firstrun", dependencies=dep)
    async def firstrun_state(lang: str = Query("", max_length=10)) -> dict[str, Any]:
        """Where the first conversation stands, the chips for the chooser, and the three
        lines the app speaks first in ``lang`` (``en`` or ``zh``; the opening's own language
        when empty)."""
        return svc.firstrun_view(lang, intro=True)

    @app.post("/api/firstrun/start", dependencies=dep)
    async def firstrun_start(body: FirstRunStartBody) -> dict[str, Any]:
        """Start was pressed: the main chat is bound as the first conversation and the
        setup counts as finished (as ``POST /api/onboarded`` does)."""
        return svc.start_first_conversation(body.lang)

    @app.post("/api/firstrun/pick", dependencies=dep)
    async def firstrun_pick(body: FirstRunPickBody) -> dict[str, Any]:
        """A chip was picked: the name is saved now; the web sends it as the person's
        message next, and the model's reply is its first as itself."""
        name = " ".join(body.name.split())[:MAX_NAME].strip()
        if not name:
            raise HTTPException(400, "name is required")
        try:
            return svc.pick_first_name(name)
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/firstrun/dismiss", dependencies=dep)
    async def firstrun_dismiss() -> dict[str, Any]:
        """The person moved on: the chooser goes, the first conversation counts as over."""
        return svc.dismiss_first_chooser()

    # ------------------------------------------------------------------ browser view
    @app.get("/api/browser/{thread_id}/frames/{frame_id}.jpg", dependencies=dep_or_signed)
    async def browser_frame(thread_id: str, frame_id: str) -> Response:
        jpeg = svc.ui.browser_frame(thread_id, frame_id)
        if jpeg is None:
            raise HTTPException(404, "that frame is gone")
        return Response(
            content=jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    @app.post("/api/browser/{thread_id}/control", dependencies=dep)
    async def browser_control(thread_id: str, body: BrowserControlBody) -> dict[str, Any]:
        _thread_or_404(thread_id)
        try:
            return await svc.browser_control(thread_id, body.model_dump(exclude_none=True))
        except LookupError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - playwright raises many error types
            raise HTTPException(502, f"browser error: {str(exc).splitlines()[0][:200]}") from exc

    # ------------------------------------------------------------------ Chrome companion extension
    @app.get("/api/chrome/status", dependencies=dep)
    async def chrome_status() -> dict[str, Any]:
        return svc.chrome_bridge.status()

    @app.get("/api/chrome/extension.zip")
    async def chrome_extension_zip() -> Response:
        blob = build_extension_zip()
        if blob is None:
            raise HTTPException(404, "Chrome extension bundle not found")
        return Response(
            content=blob,
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="MiniMuse-Chrome-Extension.zip"',
                "Cache-Control": "private, no-store",
            },
        )

    @app.post("/api/chrome/detach", dependencies=dep)
    async def chrome_detach() -> dict[str, Any]:
        if svc.chrome_bridge.connected:
            try:
                await svc.chrome_bridge.request("detach", timeout=5.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("chrome detach: {}", exc)
        return svc.chrome_bridge.status()

    # ------------------------------------------------------------------ hands background view & windows
    @app.get("/api/hands/{thread_id}/frames/{frame_id}.jpg", dependencies=dep_or_signed)
    async def hands_frame(thread_id: str, frame_id: str) -> Response:
        jpeg = svc.ui.hands_frame(thread_id, frame_id)
        if jpeg is None:
            raise HTTPException(404, "that frame is gone")
        return Response(
            content=jpeg,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    @app.get("/api/computer/windows", dependencies=dep)
    async def computer_windows() -> dict[str, Any]:
        comp = svc.app.computer
        if comp is None:
            return {"available": False, "windows": [], "target": {"app": "", "title": ""}}
        ok, why = comp.window_available()
        windows = await asyncio.to_thread(comp.list_windows) if ok else []
        return {
            "available": ok,
            "reason": why,
            "active": comp.in_window_mode(),
            "target": {"app": comp.target_app, "title": comp.target_title},
            "windows": windows,
        }

    @app.post("/api/computer/target", dependencies=dep)
    async def computer_target(body: ComputerTargetBody) -> dict[str, Any]:
        comp = svc.app.computer
        if comp is None:
            raise HTTPException(400, "computer hands are not available on this host")
        comp.set_target(body.app, body.title)
        return comp.status()["window"]

    # ------------------------------------------------------------------ Free Personal MiniMuse Cloud
    @app.get("/api/personal-cloud/status", dependencies=dep)
    async def personal_cloud_status() -> dict[str, Any]:
        return await asyncio.to_thread(personal_cloud.status, probe_tailscale=True)

    @app.post("/api/personal-cloud/start", dependencies=dep)
    async def personal_cloud_start() -> dict[str, Any]:
        return await personal_cloud.start_tunnel()

    @app.post("/api/personal-cloud/stop", dependencies=dep)
    async def personal_cloud_stop() -> dict[str, Any]:
        return await personal_cloud.stop_tunnel()

    # ------------------------------------------------------------------ Live Call & Watch pairing
    @app.get("/api/call/capabilities", dependencies=dep)
    async def get_call_capabilities() -> dict[str, Any]:
        return call_capabilities(svc)

    @app.put("/api/call/config", dependencies=dep)
    async def update_call_config(body: CallConfigBody) -> dict[str, Any]:
        return set_call_config(svc, body.provider, body.model)

    @app.post("/api/watch/code", dependencies=dep)
    async def create_watch_code() -> dict[str, Any]:
        """Generate a short 6-digit watch pair code valid for 10 minutes."""
        now = time.time()
        for k, exp in list(watch_codes.items()):
            if exp < now:
                watch_codes.pop(k, None)
        code = "".join(str(secrets.randbelow(10)) for _ in range(6))
        watch_codes[code] = now + 600.0
        st = await asyncio.to_thread(personal_cloud.status, probe_tailscale=True)
        return {
            "code": code,
            "expires_in": 600,
            "best_url": st.get("best_url") or "",
            "lan_urls": st.get("lan_urls") or [],
            "agent_name": svc.profile.name,
        }

    @app.post("/api/watch/claim")
    async def claim_watch_code(body: WatchClaimBody) -> dict[str, Any]:
        """Pair a Wear OS watch using the 6-digit pair code without typing long tokens."""
        cleaned = re.sub(r"[^0-9A-Za-z]", "", body.code).upper()
        now = time.time()
        exp = watch_codes.get(cleaned)
        if exp is None or exp < now:
            watch_codes.pop(cleaned, None)
            raise HTTPException(400, "watch pair code is invalid or expired")
        watch_codes.pop(cleaned, None)
        return {
            "ok": True,
            "token": svc.token,
            "name": svc.profile.name,
            "version": svc.settings_view()["version"],
            "call": call_capabilities(svc),
        }

    @app.get("/api/watch/state", dependencies=dep)
    async def watch_state() -> dict[str, Any]:
        """Compact state snapshot tailored for Wear OS watches."""
        st = svc.state()
        main_thread = svc.threads.get(MAIN_THREAD)
        recent: list[dict[str, Any]] = []
        if main_thread is not None:
            for ev in reversed(main_thread.timeline.events):
                if ev.get("type") in ("user", "assistant"):
                    txt = str(ev.get("text") or "").strip()
                    if txt:
                        recent.append(
                            {
                                "id": ev.get("id"),
                                "role": ev.get("type"),
                                "text": txt[:600],
                                "ts": ev.get("ts"),
                            }
                        )
                    if len(recent) >= 8:
                        break
        recent.reverse()
        from nanomuse import __version__

        return {
            "agent_name": svc.profile.name,
            "version": svc.settings_view()["version"],
            "busy": bool(main_thread.busy) if main_thread is not None else False,
            "call": call_capabilities(svc),
            "pending_approvals": [
                {
                    "id": str(ev.get("id") or ""),
                    "tool": str(ev.get("tool") or ""),
                    "summary": str(ev.get("summary") or ev.get("text") or "Approval needed")[:160],
                }
                for ev in (st.get("pending_approvals") or [])
            ],
            "pending_holds": [
                {
                    "id": str(h.get("id") or ""),
                    "tool": str(h.get("tool") or ""),
                    "reason": str(h.get("reason") or "")[:160],
                }
                for h in (st.get("holds") or [])
            ],
            "recent": recent,
            "actions": [
                {"label": "Daily Briefing", "prompt": "Give me a crisp spoken daily briefing of my calendar and top priorities."},
                {"label": "Check Messages", "prompt": "Summarize any important unread emails or messages."},
                {"label": "Today's Agenda", "prompt": "What's left on my calendar today?"},
            ],
            "update": {
                "version": __version__,
                "shell_version": "1.0.25",
                "apk_url": "/api/watch/apk",
            },
        }

    @app.get("/api/watch/version", dependencies=dep)
    async def watch_version() -> dict[str, Any]:
        """Return latest Wear OS companion version and downloadable APK status."""
        from nanomuse import __version__

        apk = _find_watch_apk()
        if apk is None and not os.environ.get("PYTEST_CURRENT_TEST"):
            asyncio.create_task(_ensure_watch_apk(), name="watch-apk-prefetch")
        return {
            "version": __version__,
            "shell_version": "1.0.25",
            "apk_ready": apk is not None,
            "apk_url": "/api/watch/apk",
        }

    @app.post("/api/watch/ask", dependencies=dep)
    async def watch_ask(body: SendBody) -> dict[str, Any]:
        """Queue a voice/text prompt from the watch onto the main chat thread."""
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "empty prompt")
        ev = svc.send(MAIN_THREAD, text, language=body.language or "")
        return {"ok": True, "event": ev}

    @app.post("/api/watch/approve", dependencies=dep)
    async def watch_approve(body: WatchApproveBody) -> dict[str, Any]:
        """Approve/deny a pending approval or finish a hold from the watch."""
        if body.kind == "hold":
            try:
                return {"ok": True, "hold": svc.done_hold(body.id)}
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
        ok = svc.decide(body.id, body.approved, body.scope, "")
        if not ok:
            raise HTTPException(404, "approval not found")
        return {"ok": True}

    def _find_watch_apk() -> Path | None:
        from nanomuse import __version__

        repo_root = Path(__file__).resolve().parents[2]
        candidates = [
            svc.data_dir / "updates",
            repo_root / "dist",
            repo_root / "android-classic" / "wear" / "build" / "outputs" / "apk" / "release",
            repo_root / "android-classic" / "wear" / "build" / "outputs" / "apk" / "debug",
        ]
        for folder in candidates:
            with contextlib.suppress(OSError):
                if not folder.is_dir():
                    continue
                for exact_name in (
                    f"MiniMuse-{__version__}-wear.apk",
                    f"MiniMuse-wear-{__version__}.apk",
                    "MiniMuse-wear.apk",
                ):
                    exact = folder / exact_name
                    if exact.is_file() and exact.stat().st_size > 50_000:
                        return exact
                apks = sorted(
                    (
                        p
                        for p in folder.glob("*.apk")
                        if "wear" in p.name.lower() and p.is_file() and p.stat().st_size > 50_000
                    ),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if apks:
                    return apks[0]
        return None

    async def _ensure_watch_apk() -> Path | None:
        local = _find_watch_apk()
        if local is not None:
            return local
        from nanomuse import __version__

        updates_dir = svc.data_dir / "updates"
        updates_dir.mkdir(parents=True, exist_ok=True)
        dest = updates_dir / f"MiniMuse-{__version__}-wear.apk"
        repo = svc.settings.server.update_repo.strip() or "leothefleo49/MiniMuse"

        # 1. Check current version or latest release assets on GitHub
        urls_to_try: list[str] = [
            f"https://github.com/{repo}/releases/download/v{__version__}/MiniMuse-{__version__}-wear.apk",
            f"https://github.com/{repo}/releases/download/v{__version__}/MiniMuse-wear-{__version__}.apk",
            f"https://github.com/{repo}/releases/latest/download/MiniMuse-{__version__}-wear.apk",
            f"https://github.com/{repo}/releases/latest/download/MiniMuse-wear.apk",
        ]
        with contextlib.suppress(Exception):
            assets = await self_update._release_assets(__version__)
            for asset in assets:
                name = str(asset.get("name") or "")
                dl = str(asset.get("browser_download_url") or "")
                if "wear" in name.lower() and name.lower().endswith(".apk") and dl:
                    urls_to_try.insert(0, dl)
                    break
            else:
                view = await update_check.view(force=False)
                latest = str(view.get("latest") or "").strip()
                if latest and latest != __version__:
                    urls_to_try.append(
                        f"https://github.com/{repo}/releases/download/v{latest}/MiniMuse-{latest}-wear.apk"
                    )
                    for asset in await self_update._release_assets(latest):
                        name = str(asset.get("name") or "")
                        dl = str(asset.get("browser_download_url") or "")
                        if "wear" in name.lower() and name.lower().endswith(".apk") and dl:
                            urls_to_try.append(dl)

        for url in urls_to_try:
            try:
                await self_update._download_to(url, dest)
                if dest.is_file() and dest.stat().st_size > 50_000:
                    return dest
            except Exception as exc:  # noqa: BLE001
                logger.debug("watch APK download attempt from {} failed: {}", url, exc)
                with contextlib.suppress(OSError):
                    dest.unlink(missing_ok=True)
        return _find_watch_apk()

    def _find_adb() -> str | None:
        import shutil

        which_adb = shutil.which("adb")
        if which_adb:
            return which_adb
        exe_name = "adb.exe" if os.name == "nt" else "adb"
        candidates = [
            svc.data_dir / "platform-tools" / "platform-tools" / exe_name,
            svc.data_dir / "platform-tools" / exe_name,
            Path("/usr/bin/adb"),
            Path("/usr/local/bin/adb"),
        ]
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            candidates.append(Path(local_app) / "Android" / "Sdk" / "platform-tools" / exe_name)
        home = Path.home()
        candidates.extend(
            [
                home / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / exe_name,
                home / "Android" / "Sdk" / "platform-tools" / exe_name,
                home / "Library" / "Android" / "sdk" / "platform-tools" / exe_name,
            ]
        )
        for cand in candidates:
            with contextlib.suppress(OSError):
                if cand.is_file():
                    return str(cand)
        return None

    async def _ensure_adb() -> str | None:
        existing = _find_adb()
        if existing:
            return existing
        import platform as _plat
        import subprocess
        import zipfile

        # Inside Alpine PRoot on Android or Linux, install android-tools automatically
        if Path("/etc/alpine-release").is_file() and Path("/sbin/apk").is_file():
            def _apk_install() -> None:
                with contextlib.suppress(Exception):
                    subprocess.run(
                        ["/sbin/apk", "add", "--no-cache", "android-tools"],
                        capture_output=True,
                        text=True,
                        timeout=90,
                        check=False,
                    )

            await asyncio.to_thread(_apk_install)
            found = _find_adb()
            if found:
                return found

        # On Windows, macOS, or x86_64 Linux, download official Google platform-tools automatically
        sys_os = "windows" if os.name == "nt" else ("darwin" if os.sys.platform == "darwin" else "linux")
        arch = _plat.machine().lower()
        if sys_os == "linux" and arch in ("aarch64", "arm64"):
            return _find_adb()
        pt_url = f"https://dl.google.com/android/repository/platform-tools-latest-{sys_os}.zip"
        pt_dir = svc.data_dir / "platform-tools"
        pt_dir.mkdir(parents=True, exist_ok=True)
        zip_dest = pt_dir / f"platform-tools-{sys_os}.zip"
        try:
            await self_update._download_to(pt_url, zip_dest)

            def _extract_pt() -> None:
                with zipfile.ZipFile(zip_dest, "r") as zf:
                    zf.extractall(pt_dir)
                with contextlib.suppress(OSError):
                    zip_dest.unlink(missing_ok=True)
                if os.name != "nt":
                    for bin_name in ("adb", "fastboot"):
                        for p in (pt_dir / "platform-tools" / bin_name, pt_dir / bin_name):
                            if p.is_file():
                                p.chmod(0o755)

            await asyncio.to_thread(_extract_pt)
        except Exception as exc:  # noqa: BLE001
            logger.debug("automatic platform-tools download failed: {}", exc)
        return _find_adb()

    def _probe_adb_device(adb: str, dev_id: str) -> dict[str, Any]:
        """Inspect a connected ADB device to distinguish Wear OS watches from phones."""
        import subprocess

        from nanomuse.win_proc import hidden_subprocess_kwargs

        chars = ""
        model = ""
        has_watch_feat = ""
        serial = ""
        try:
            proc = subprocess.run(
                [
                    adb,
                    "-s",
                    dev_id,
                    "shell",
                    "getprop ro.build.characteristics; echo '|'; getprop ro.product.model; echo '|'; pm has-feature android.hardware.type.watch; echo '|'; getprop ro.serialno",
                ],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            raw = (proc.stdout or "").strip()
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) >= 1:
                chars = parts[0].strip()
            if len(parts) >= 2:
                model = parts[1].strip()
            if len(parts) >= 3:
                has_watch_feat = parts[2].strip().lower()
            if len(parts) >= 4:
                serial = parts[3].strip()
        except (OSError, subprocess.SubprocessError):
            pass

        low_chars = chars.lower()
        low_model = model.lower()
        is_watch_signal = (
            "watch" in low_chars
            or has_watch_feat == "true"
            or any(
                w in low_model
                for w in ("watch", "wear", "sm-r", "sm-l", "pixel watch", "ticwatch", "oneplus watch")
            )
        )
        is_phone_signal = (bool(chars) and "watch" not in low_chars and has_watch_feat != "true")
        is_watch: bool | None = True if is_watch_signal else (False if is_phone_signal else None)
        return {"characteristics": chars, "model": model, "is_watch": is_watch, "serial": serial}

    def _browse_mdns_watch_adb(adb: str | None = None, timeout_s: float = 1.4) -> dict[str, list[dict[str, Any]]]:
        """Wireless-debugging endpoints on the LAN, from `adb mdns services` and our own query.

        Our own query asks on every network interface and requests unicast replies (see
        watch_discovery): a single interface-less query is lost on computers that also have a
        Tailscale, WSL or VPN adapter.
        """
        import subprocess

        from nanomuse.win_proc import hidden_subprocess_kwargs

        from_adb: dict[str, list[dict[str, Any]]] = {"connect": [], "pairing": []}
        if adb:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                proc = subprocess.run(
                    [adb, "mdns", "services"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
                from_adb = watch_discovery.parse_adb_mdns_services(proc.stdout or "")
        own = watch_discovery.browse_mdns(timeout_s=timeout_s)
        return watch_discovery.merge_endpoints(from_adb, own)

    def _list_adb_devices(adb: str, probe_watch: bool = False) -> list[dict[str, Any]]:
        import subprocess

        from nanomuse.win_proc import hidden_subprocess_kwargs

        devices: list[dict[str, Any]] = []
        try:
            proc = subprocess.run(
                [adb, "devices", "-l"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            for line in (proc.stdout or "").splitlines()[1:]:
                line = line.strip()
                if not line or line.startswith("*"):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                entry: dict[str, Any] = {"id": parts[0], "state": parts[1]}
                for kv in parts[2:]:
                    if ":" in kv:
                        k, v = kv.split(":", 1)
                        if k in ("model", "product", "device"):
                            entry[k] = v
                if probe_watch and entry["state"] == "device":
                    info = _probe_adb_device(adb, entry["id"])
                    entry["is_watch"] = info["is_watch"]
                    entry["characteristics"] = info["characteristics"]
                    # a stable identity: unlike ip:port it survives wireless debugging being toggled
                    entry["serial"] = info["serial"] or watch_discovery.service_serial(entry["id"]) or ""
                    if info["model"] and not entry.get("model"):
                        entry["model"] = info["model"]
                devices.append(entry)
        except (OSError, subprocess.SubprocessError):
            pass
        return devices

    def _watch_devices_sync() -> dict[str, Any]:
        apk = _find_watch_apk()
        adb = _find_adb()
        devices = _list_adb_devices(adb, probe_watch=True) if adb else []
        mdns = _browse_mdns_watch_adb(adb, timeout_s=1.0)
        return {
            "adb_available": True,
            "adb_installed": bool(adb),
            "devices": devices,
            "mdns_connect": mdns["connect"],
            "mdns_pairing": mdns["pairing"],
            "auto": {**watch_auto, "saved": _load_saved_watch()},
            "apk_path": str(apk) if apk else None,
            "apk_ready": True,
            "apk_cached": apk is not None,
            "download_url": "/api/watch/apk",
        }

    @app.get("/api/watch/devices", dependencies=dep)
    async def watch_devices() -> dict[str, Any]:
        """List connected ADB devices and whether a local or auto-downloadable Wear OS APK is ready."""
        return await asyncio.to_thread(_watch_devices_sync)

    @app.post("/api/watch/install", dependencies=dep)
    async def watch_install(body: WatchInstallBody | None = None) -> dict[str, Any]:
        """Auto-download the Wear OS APK, optionally pair/connect over Wireless Debugging,
        install onto the watch, grant watch permissions, and auto-pair with this server."""
        import subprocess

        from nanomuse.win_proc import hidden_subprocess_kwargs

        adb = await _ensure_adb()
        apk = await _ensure_watch_apk()
        release_url = "/api/watch/apk"
        if not adb:
            return {
                "ok": False,
                "code": "adb_missing",
                "error": "Could not locate or auto-install ADB on this host. Tap 'Download Watch APK to Phone' below or enter Watch Wi-Fi Debugging details.",
                "download_url": release_url,
            }
        if apk is None or not apk.is_file():
            return {
                "ok": False,
                "code": "apk_missing",
                "error": "Could not download MiniMuse-wear.apk automatically. Check your internet connection and try again.",
                "download_url": release_url,
            }

        st = await asyncio.to_thread(personal_cloud.status, probe_tailscale=True)
        best_url = str(st.get("best_url") or "")
        if not best_url:
            lan_urls = st.get("lan_urls") or []
            if lan_urls:
                best_url = str(lan_urls[0])
        pair_hp = (body.pair_host_port or "").strip() if body else ""
        pair_code = (body.pair_code or "").strip() if body else ""
        conn_hp = (body.connect_host_port or "").strip() if body else ""
        req_target = (body.target or "").strip() if body else ""
        grant_perms = True if body is None else bool(body.grant_permissions)
        auto_pair = True if body is None else bool(body.auto_pair_watch)

        def _fail(code: str, error: str, devs: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
            out: dict[str, Any] = {"ok": False, "code": code, "error": error, "download_url": release_url}
            if devs is not None:
                out["devices"] = devs
            out.update(extra)
            return out

        def _install() -> dict[str, Any]:
            import time as _time

            steps_log: list[str] = []

            def run_adb(*args: str, timeout: int = 20) -> str:
                try:
                    proc = subprocess.run(
                        [adb, *args],
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        check=False,
                        **hidden_subprocess_kwargs(),
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    return str(exc)
                return (proc.stdout or proc.stderr or "").strip()

            def host_of(dev_id: str) -> str:
                return "" if dev_id.startswith("adb-") or ":" not in dev_id else dev_id.rpartition(":")[0]

            def phone_hosts_of(devs_now: list[dict[str, Any]], endpoints: list[dict[str, Any]]) -> set[str]:
                """Addresses of devices already known to be phones, so we never poke them again."""
                hosts = {host_of(d["id"]) for d in devs_now if d.get("is_watch") is False}
                serials = {d.get("serial") for d in devs_now if d.get("is_watch") is False and d.get("serial")}
                hosts.update(ep["host"] for ep in endpoints if ep.get("serial") and ep["serial"] in serials)
                hosts.discard("")
                return hosts

            def is_attached(ep: dict[str, Any], devs_now: list[dict[str, Any]]) -> bool:
                return any(
                    d["id"] == ep["host_port"] or (ep.get("serial") and ep["serial"] == watch_discovery.service_serial(d["id"]))
                    for d in devs_now
                )

            # 1. Look around first: what is already attached, and what does the network offer?
            devs = _list_adb_devices(adb, probe_watch=True)
            found = _browse_mdns_watch_adb(adb, timeout_s=1.4)
            skip_hosts = phone_hosts_of(devs, found["connect"] + found["pairing"])
            pairing_eps = [ep for ep in found["pairing"] if ep["host"] not in skip_hosts]

            # 2. Pair. When a watch shows its pairing screen the user types only the 6-digit code:
            #    where to send it comes from discovery, not from typing an IP and a port.
            paired_host = ""
            if pair_code:
                target_hp = pair_hp
                if not target_hp:
                    if len(pairing_eps) == 1:
                        target_hp = pairing_eps[0]["host_port"]
                    elif len(pairing_eps) > 1:
                        return _fail(
                            "choose_pairing",
                            "More than one device is showing a pairing code. Pick the watch from the list.",
                            devs,
                            pairing=pairing_eps,
                        )
                    else:
                        return _fail(
                            "no_pairing_service",
                            "No device is showing a pairing screen. On the watch open Settings -> Developer options -> "
                            "Wireless debugging -> Pair new device with pairing code, then enter the code here before it expires. "
                            "(No Developer options? Settings -> About watch -> Software info, tap Build number 7 times.)",
                            devs,
                        )
                p_out = run_adb("pair", target_hp, pair_code, timeout=25)
                steps_log.append(f"pair {target_hp}: {p_out}")
                if "successfully paired" not in p_out.lower():
                    return _fail(
                        "pair_failed",
                        f"The watch did not accept that code ({p_out or 'no answer'}). A pairing code expires within a minute: "
                        "tap Pair new device with pairing code again and use the new 6 digits.",
                        devs,
                    )
                paired_host = target_hp.rpartition(":")[0]

            # 3. Connect. Ordered from most to least specific; every discovered watch is tried.
            explicit_connect = conn_hp or (req_target if ":" in req_target and not req_target.startswith("adb-") else "")
            if not explicit_connect and pair_hp and not pair_code and ":" in pair_hp:
                explicit_connect = pair_hp  # the user typed only IP:port (legacy "Debug over Wi-Fi")
            attempted_failures: list[str] = []
            if explicit_connect:
                c_out = run_adb("connect", explicit_connect, timeout=20)
                steps_log.append(f"connect {explicit_connect}: {c_out}")
                if "connected" not in c_out.lower() or "failed" in c_out.lower():
                    attempted_failures.append(explicit_connect)
            else:
                # right after pairing the watch publishes its connect service a moment later
                deadline = _time.monotonic() + (9.0 if paired_host else 0.0)
                tried: set[str] = set()
                while True:
                    eps = [
                        ep
                        for ep in found["connect"]
                        if ep["host"] not in skip_hosts
                        and ep["host_port"] not in tried
                        and not is_attached(ep, devs)
                        and (not paired_host or ep["host"] == paired_host)
                    ]
                    for ep in eps:
                        tried.add(ep["host_port"])
                        c_out = run_adb("connect", ep["host_port"], timeout=15)
                        steps_log.append(f"mdns connect {ep['host_port']}: {c_out}")
                        if "connected" not in c_out.lower() or "failed" in c_out.lower():
                            attempted_failures.append(ep["host_port"])
                    devs = _list_adb_devices(adb, probe_watch=True)
                    if any(d.get("state") == "device" and d.get("is_watch") is True for d in devs):
                        break
                    if _time.monotonic() >= deadline:
                        break
                    _time.sleep(1.0)
                    found = _browse_mdns_watch_adb(adb, timeout_s=1.0)

            devs = _list_adb_devices(adb, probe_watch=True)

            # 3b. Old-style "Debug over Wi-Fi" watches listen on port 5555 with no mDNS service at all.
            if not any(d.get("state") in ("device", "unauthorized") and d.get("is_watch") is not False for d in devs) and not explicit_connect:
                known = phone_hosts_of(devs, found["connect"]) | {host_of(d["id"]) for d in devs}
                own = set(watch_discovery.local_ipv4_addresses())
                candidates = [h for h in watch_discovery.lan_neighbors(own=own) if h not in known]
                for host in watch_discovery.probe_tcp_open(candidates, port=5555, timeout_s=0.6):
                    c_out = run_adb("connect", f"{host}:5555", timeout=15)
                    steps_log.append(f"legacy connect {host}:5555: {c_out}")
                devs = _list_adb_devices(adb, probe_watch=True)

            # 4. Resolve the target device. NEVER allow installing the Wear APK onto a phone.
            ready_devs = [d for d in devs if d.get("state") == "device"]
            chosen_target = req_target
            if not chosen_target and explicit_connect:
                for d in ready_devs:
                    if d["id"] == explicit_connect or d["id"].startswith(explicit_connect.split(":")[0]):
                        chosen_target = d["id"]
                        break
            if chosen_target:
                matched = next((d for d in ready_devs if d["id"] == chosen_target), None)
                if matched and matched.get("is_watch") is False:
                    dev_label = matched.get("model") or matched["id"]
                    return _fail(
                        "phone_only",
                        f"Refused to install onto '{dev_label}' ({matched['id']}) because it is a phone, "
                        "not a Wear OS watch. Enable Settings -> Developer options -> Wireless debugging on your "
                        "smartwatch and select the watch device or enter the watch's IP:Port.",
                        devs,
                    )
            if not chosen_target and ready_devs:
                watch_devs = [d for d in ready_devs if d.get("is_watch") is True]
                if watch_devs:
                    chosen_target = watch_devs[0]["id"]
                else:
                    # Only confirmed phone(s) attached: do not fall back to installing on the phone!
                    if all(d.get("is_watch") is False for d in ready_devs):
                        phone_names = ", ".join(d.get("model") or d["id"] for d in ready_devs)
                        return _fail(
                            "phone_only",
                            f"Only phone ADB target(s) found ({phone_names}); no Wear OS smartwatch is connected yet. "
                            "Turn on Developer Options -> Wireless debugging on your watch (no Developer Options? "
                            "Settings -> About watch -> Software info, tap Build number 7 times), pair it once if needed, "
                            "and enter your watch's Wi-Fi IP:Port above.",
                            devs,
                            pairing=pairing_eps,
                        )
                    # Unknown characteristics fallback (not explicitly a phone)
                    chosen_target = ready_devs[0]["id"]

            if not chosen_target and not ready_devs:
                unauth = [d for d in devs if d.get("state") == "unauthorized"]
                if unauth:
                    return _fail(
                        "unauthorized",
                        "Your watch is connected but waiting for authorization. Check your watch screen and tap "
                        "'Always allow from this computer/phone', then click Auto-Install again.",
                        devs,
                    )
                if pairing_eps:
                    return _fail(
                        "need_pairing_code",
                        "Your watch is showing its pairing screen. Type the 6-digit pairing code from the watch.",
                        devs,
                        pairing=pairing_eps,
                    )
                if attempted_failures:
                    return _fail(
                        "needs_pairing",
                        "A watch is on the network with Wireless debugging on but this computer is not paired with it yet. "
                        "On the watch open Wireless debugging -> Pair new device with pairing code; MiniMuse will see it and "
                        "ask only for the 6 digits.",
                        devs,
                        connect_failed=attempted_failures,
                    )
                return _fail(
                    "no_watch",
                    "No watch detected on this network. On the watch: Settings -> About watch -> Software info, tap Build "
                    "number 7 times, then Settings -> Developer options -> turn on ADB debugging and Wireless debugging, "
                    "and make sure the watch is on the same Wi-Fi as this computer.",
                    devs,
                )

            cmd = [adb]
            if chosen_target:
                cmd.extend(["-s", chosen_target])
            cmd.extend(["install", "-r", "-g", str(apk)])
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return _fail("install_failed", str(exc))
            out = (proc.stdout or proc.stderr or "").strip()
            if proc.returncode != 0:
                # Retry without -g in case older Wear OS rejects -g flag
                cmd_no_g = [c for c in cmd if c != "-g"]
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    proc = subprocess.run(
                        cmd_no_g,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                        **hidden_subprocess_kwargs(),
                    )
                    out = (proc.stdout or proc.stderr or "").strip()
            if proc.returncode != 0:
                return _fail("install_failed", out or f"adb exited with status {proc.returncode}")

            base_adb = [adb, "-s", chosen_target] if chosen_target else [adb]
            # Persist watch target for future background self-updates when connected
            installed_on = next((d for d in devs if d["id"] == chosen_target), {})
            if chosen_target:
                with contextlib.suppress(OSError):
                    (svc.data_dir / "watch_target.json").write_text(
                        json.dumps(
                            {
                                "target": chosen_target,
                                "serial": installed_on.get("serial") or watch_discovery.service_serial(chosen_target) or "",
                                "model": installed_on.get("model") or "",
                                "host": host_of(chosen_target),
                                "saved_at": _time.time(),
                            }
                        ),
                        encoding="utf-8",
                    )
            # 4. Grant runtime permissions on the watch automatically
            permissions_granted = False
            if grant_perms:
                for perm in (
                    "android.permission.RECORD_AUDIO",
                    "android.permission.POST_NOTIFICATIONS",
                ):
                    with contextlib.suppress(OSError, subprocess.SubprocessError):
                        subprocess.run(
                            [*base_adb, "shell", "pm", "grant", "io.github.nanomuse.wear", perm],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=False,
                            **hidden_subprocess_kwargs(),
                        )
                permissions_granted = True

            # 5. Launch the watch app and auto-pair it with the server URL and token
            auto_paired = False
            if auto_pair:
                launch_cmd = [
                    *base_adb,
                    "shell",
                    "am",
                    "start",
                    "-n",
                    "io.github.nanomuse.wear/.MainActivity",
                ]
                if best_url:
                    launch_cmd.extend(["--es", "server_url", best_url])
                if svc.token:
                    launch_cmd.extend(["--es", "token", svc.token])
                if svc.profile.name:
                    launch_cmd.extend(["--es", "agent_name", svc.profile.name])
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    l_proc = subprocess.run(
                        launch_cmd,
                        capture_output=True,
                        text=True,
                        timeout=12,
                        check=False,
                        **hidden_subprocess_kwargs(),
                    )
                    auto_paired = l_proc.returncode == 0 and bool(best_url)

            return {
                "ok": True,
                "target": chosen_target,
                "apk": apk.name,
                "output": out,
                "permissions_granted": permissions_granted,
                "auto_paired": auto_paired,
                "best_url": best_url,
                "steps": steps_log,
            }

        return await asyncio.to_thread(_install)

    @app.get("/api/watch/apk", dependencies=dep)
    async def download_watch_apk() -> FileResponse:
        apk = await _ensure_watch_apk()
        if apk is None or not apk.is_file():
            raise HTTPException(404, "Wear OS APK could not be found or downloaded")
        return FileResponse(
            apk,
            filename=apk.name,
            media_type="application/vnd.android.package-archive",
        )

    def _parse_ver_tuple(raw: str) -> tuple[int, ...]:
        parts: list[int] = []
        for seg in raw.strip().lstrip("vV").split("-")[0].split("."):
            digits = "".join(ch for ch in seg if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts)

    def _load_saved_watch() -> dict[str, Any]:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads((svc.data_dir / "watch_target.json").read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("target"):
                return data
        return {}

    def _is_saved_watch(dev: dict[str, Any], saved: dict[str, Any]) -> bool:
        """Is this attached device the watch we installed on before? Matched by serial first:
        ip:port changes whenever Wireless debugging restarts, the serial does not."""
        if dev.get("state") != "device" or dev.get("is_watch") is not True:
            return False
        serial = str(saved.get("serial") or "")
        if serial and dev.get("serial") == serial:
            return True
        if dev["id"] == saved.get("target"):
            return True
        host = str(saved.get("host") or "")
        return bool(host and dev["id"].startswith(host + ":"))

    def _find_saved_watch_sync() -> tuple[str, str] | None:
        """Look for the watch on this network and connect to it. Returns (adb, target) when it is up."""
        import subprocess

        from nanomuse.win_proc import hidden_subprocess_kwargs

        saved = _load_saved_watch()
        adb = _find_adb()
        if not saved or not adb:
            return None

        def connect(hp: str) -> None:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    [adb, "connect", hp],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )

        devs = _list_adb_devices(adb, probe_watch=True)
        hit = next((d for d in devs if _is_saved_watch(d, saved)), None)
        if hit is None:
            serial = str(saved.get("serial") or "")
            host = str(saved.get("host") or "")
            for ep in _browse_mdns_watch_adb(adb, timeout_s=1.2)["connect"]:
                if serial and ep.get("serial") != serial:
                    continue
                if not serial and ep["host"] != host:
                    continue
                if any(d["id"] == ep["host_port"] for d in devs):
                    continue
                connect(ep["host_port"])
            devs = _list_adb_devices(adb, probe_watch=True)
            hit = next((d for d in devs if _is_saved_watch(d, saved)), None)
            if hit is None and host and watch_discovery.probe_tcp_open([host], port=5555, timeout_s=0.6):
                connect(f"{host}:5555")
                devs = _list_adb_devices(adb, probe_watch=True)
                hit = next((d for d in devs if _is_saved_watch(d, saved)), None)
        if hit is None:
            return None
        # keep the remembered identity current: the port and even the address move over time
        fresh = {
            "target": hit["id"],
            "serial": hit.get("serial") or saved.get("serial") or "",
            "model": hit.get("model") or saved.get("model") or "",
            "host": hit["id"].rpartition(":")[0] if ":" in hit["id"] and not hit["id"].startswith("adb-") else saved.get("host", ""),
            "saved_at": saved.get("saved_at", 0),
        }
        if fresh != {k: saved.get(k) for k in fresh}:
            with contextlib.suppress(OSError):
                (svc.data_dir / "watch_target.json").write_text(json.dumps(fresh), encoding="utf-8")
        return adb, hit["id"]

    def _push_update_sync(adb: str, target: str, apk: Path) -> str:
        import re as _re
        import subprocess

        from nanomuse import __version__
        from nanomuse.win_proc import hidden_subprocess_kwargs

        try:
            proc = subprocess.run(
                [adb, "-s", target, "shell", "dumpsys", "package", "io.github.nanomuse.wear"],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            m = _re.search(r"versionName=([^\s]+)", proc.stdout or "")
            installed_ver = m.group(1) if m else ""
            if installed_ver and _parse_ver_tuple(installed_ver) >= _parse_ver_tuple(__version__):
                return f"watch is up to date ({installed_ver})"
            res = subprocess.run(
                [adb, "-s", target, "install", "-r", "-g", str(apk)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if res.returncode == 0:
                logger.info("Auto-updated paired watch {} to {}", target, __version__)
                return f"updated the watch to {__version__}"
            return f"update failed: {(res.stdout or res.stderr or '').strip()[:160]}"
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("watch auto-push skipped: {}", exc)
            return f"update skipped: {exc}"

    async def _autodetect_once() -> None:
        import time as _time

        watch_auto["last_check"] = _time.time()
        if not _load_saved_watch():
            watch_auto.update(last_result="no watch installed yet", watch_online=False)
            return
        found = await asyncio.to_thread(_find_saved_watch_sync)
        if not found:
            watch_auto.update(last_result="watch not reachable on this network right now", watch_online=False)
            return
        adb, target = found
        watch_auto.update(watch_online=True, last_result="watch found")
        apk = await _ensure_watch_apk()
        if apk is None or not apk.is_file():
            watch_auto["last_result"] = "watch found, but the Wear APK could not be fetched"
            return
        watch_auto["last_result"] = await asyncio.to_thread(_push_update_sync, adb, target, apk)

    async def _maybe_auto_push_watch_update() -> None:
        """Kept for callers that ask for a single look; the loop below does it continuously."""
        await _autodetect_once()

    async def _watch_autodetect_loop() -> None:
        """Keep looking for the watch once it has been installed on, so updates need no tapping.

        Every couple of minutes while the watch is around (every five when it is not): find it by
        its serial, reconnect, and update it when this server is newer. Wireless-debugging ports
        and addresses change; the loop does not care.
        """
        if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("NANOMUSE_NO_WATCH_AUTODETECT"):
            return
        await asyncio.sleep(float(os.environ.get("NANOMUSE_WATCH_FIRST_CHECK_S") or 20.0))
        while True:
            delay = 300.0
            try:
                if svc.auto_update_enabled() and _load_saved_watch():
                    await _autodetect_once()
                    delay = 120.0 if watch_auto["watch_online"] else 300.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("watch autodetect tick failed: {}", exc)
            await asyncio.sleep(delay)

    # ------------------------------------------------------------------ the CLI bridge
    # Not behind the app token: a command the agent runs holds a call token instead, minted
    # for it alone by the shell tool and good while it runs (nanomuse.bridge).
    def _bridge_token(request: Request) -> str | None:
        return request.headers.get("x-nanomuse-bridge") or None

    @app.get("/api/bridge/tools")
    async def bridge_tools(request: Request) -> dict[str, Any]:
        if svc.bridge.tokens.get(_bridge_token(request)) is None:
            raise HTTPException(401, "the call token is missing, unknown or expired")
        return {"ok": True, "tools": svc.bridge.device_tools()}

    @app.post("/api/bridge/{kind}")
    async def bridge_call(kind: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "the request body must be JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "the request body must be a JSON object")
        try:
            return await svc.bridge.handle(_bridge_token(request), kind, body)
        except BridgeError as exc:
            raise HTTPException(exc.status, exc.message) from exc

    # ------------------------------------------------------------------ push
    @app.get("/api/push", dependencies=dep)
    async def push_view() -> dict[str, Any]:
        return svc.push.view()

    @app.post("/api/push/subscribe", dependencies=dep)
    async def push_subscribe(body: PushSubscribeBody, request: Request) -> dict[str, Any]:
        try:
            svc.push.subscribe(body.subscription, ua=request.headers.get("user-agent", ""))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return svc.push.view()

    @app.post("/api/push/unsubscribe", dependencies=dep)
    async def push_unsubscribe(body: PushUnsubscribeBody) -> dict[str, Any]:
        svc.push.unsubscribe(body.endpoint)
        return svc.push.view()

    @app.post("/api/push/test", dependencies=dep)
    async def push_test() -> dict[str, Any]:
        return await svc.push.test(svc.profile.name)

    @app.get("/api/feed", dependencies=dep)
    async def feed(limit: int = Query(60, ge=1, le=500)) -> list[dict[str, Any]]:
        return svc.feed(limit)

    @app.get("/api/feed/posts", dependencies=dep)
    async def feed_posts() -> dict[str, Any]:
        return svc.feed_posts()

    @app.put("/api/feed/instructions", dependencies=dep)
    async def feed_instructions(body: FeedInstructionsBody) -> dict[str, Any]:
        try:
            return svc.set_feed_instructions(body.instructions, daily=body.daily, time=body.time)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/feed/posts/refresh", dependencies=dep)
    async def feed_refresh() -> dict[str, Any]:
        try:
            return await svc.write_feed_posts()
        except Exception as exc:  # noqa: BLE001 — the model may be down; the app shows why
            data = svc.feed_posts()
            data["error"] = f"{type(exc).__name__}: {exc}"
            return data

    @app.delete("/api/feed/posts/{post_id}", dependencies=dep)
    async def feed_delete(post_id: str) -> dict[str, Any]:
        if not svc.delete_feed_post(post_id):
            raise HTTPException(404, "no such post")
        return {"ok": True}

    @app.post("/api/tick", dependencies=dep)
    async def tick() -> dict[str, Any]:
        """Run the scheduler's pass now — the phone's alarm calls this when the process was
        asleep — and say when the next wake is due."""
        svc.wake()
        await asyncio.sleep(0)
        when = svc.next_wake_at()
        return {"ok": True, "next_wake_at": when.isoformat(timespec="seconds") if when else None}

    @app.get("/api/upcoming", dependencies=dep)
    async def upcoming() -> dict[str, Any]:
        return svc.upcoming()

    @app.get("/api/files", dependencies=dep)
    async def list_files(limit: int = Query(200, ge=1, le=2000)) -> list[dict[str, Any]]:
        return svc.list_files(limit)

    @app.post("/api/files/upload", dependencies=dep)
    async def upload_file(
        request: Request, name: str = Query(..., min_length=1, max_length=255)
    ) -> dict[str, Any]:
        """A file to attach to a message: the bytes as the request body (no multipart),
        the file name in ``name``. Lands in ``attachments/<date>/`` in the workspace;
        the reply is what to put in ``files`` when sending."""
        limit = svc.settings.server.max_upload_mb * 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(
                413, f"the file is larger than {svc.settings.server.max_upload_mb} MB"
            )
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    413, f"the file is larger than {svc.settings.server.max_upload_mb} MB"
                )
            chunks.append(chunk)
        if total == 0:
            raise HTTPException(400, "empty file")
        try:
            return svc.save_upload(name, b"".join(chunks)).model_dump()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/data/export", dependencies=dep_or_signed)
    async def export_data() -> Response:
        """The agent's data as one zip (parity note 37): what the runtime keeps about the
        person and about its own work, and never a credential. The reply is the file itself,
        so a browser on another machine gets it. Private snapshot files are removed first."""
        data = await asyncio.to_thread(
            data_export.agent_data_zip,
            svc.settings.data_dir,
            databases=svc.data_export_sources(),
        )
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{data_export.filename()}"',
                "Cache-Control": "private, no-store",
            },
        )

    background_tasks: set[asyncio.Task[None]] = set()

    @app.post("/api/data/import", dependencies=dep)
    async def import_data(
        request: Request,
        name: str = Query(..., min_length=1, max_length=255),
        restore_chats: bool = Query(True),
        background_memory: bool = Query(True),
        apps: str = Query("both"),
        main_chat: str = Query("side"),
        background: bool = Query(False),
    ) -> dict[str, Any]:
        """Import one file (a Muse export zip, a nanoMuse export zip, or a transcript):
        the bytes as the request body (no multipart), the file name in ``name``. The four
        switches are the Import & export screen's; the reply says what was found and done.
        Large archives are streamed to a staging file and read there, never held in memory."""
        try:
            options = data_import.ImportOptions(
                restore_chats=restore_chats,
                background_memory=background_memory,
                apps=apps,
                main_chat=main_chat,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # no size limit: the disk is the only bound, and it is checked before a byte is kept
        limit = data_import.IMPORT_UPLOAD_MB * 1024 * 1024 if data_import.IMPORT_UPLOAD_MB else 0
        staging_dir = svc.data_dir / "imports" / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit():
            if limit and int(declared) > limit:
                raise HTTPException(
                    413, f"the file is larger than {data_import.IMPORT_UPLOAD_MB} MB"
                )
            # the upload is staged on disk and then read from there: it needs room for itself
            # plus a margin for what it unpacks into
            if int(declared) + data_import.DISK_MARGIN_BYTES > data_import.free_bytes(staging_dir):
                raise HTTPException(
                    507,
                    "there is not enough free storage to import this file "
                    f"({int(declared) // (1024 * 1024)} MB needed; free some space and try again)",
                )
        staging = staging_dir / f"{uuid.uuid4().hex}.upload"
        staging_kept = False
        try:
            total = 0
            with staging.open("wb") as out:
                async for chunk in request.stream():
                    total += len(chunk)
                    if limit and total > limit:
                        raise HTTPException(
                            413, f"the file is larger than {data_import.IMPORT_UPLOAD_MB} MB"
                        )
                    out.write(chunk)
            if total == 0:
                raise HTTPException(400, "empty file")
            if background:
                job = data_import.new_job(name)

                async def run_job() -> None:
                    try:
                        job.phase = "reading"
                        plan = await asyncio.to_thread(
                            data_import.ingest_path, svc, staging, name, options, job.progress
                        )
                        job.phase = "applying"
                        job.result = data_import.apply_plan(svc, plan, options)
                        job.phase = "done"
                    except Exception as exc:  # the job reports it; the server keeps running
                        logger.warning("background import failed: %s", exc)
                        job.error = str(exc) or exc.__class__.__name__
                        job.phase = "error"
                    finally:
                        job.finished = time.time()
                        staging.unlink(missing_ok=True)

                task = asyncio.create_task(run_job())
                background_tasks.add(task)
                task.add_done_callback(background_tasks.discard)
                staging_kept = True
                return {"ok": True, "background": True, "job": job.view()}
            # reading and staging happen in a worker thread; the service's threads are
            # only touched back on the event loop (apply_plan), where the runtime expects it
            plan = await asyncio.to_thread(
                data_import.ingest_path, svc, staging, name, options
            )
            return data_import.apply_plan(svc, plan, options)
        finally:
            if not staging_kept:
                staging.unlink(missing_ok=True)

    @app.get("/api/data/import/jobs", dependencies=dep)
    async def import_jobs() -> dict[str, Any]:
        """Every import running or recently finished, newest first, for the progress bar."""
        jobs = sorted(data_import.IMPORT_JOBS.values(), key=lambda j: j.started, reverse=True)
        return {"jobs": [j.view() for j in jobs]}

    @app.get("/api/data/import/jobs/{job_id}", dependencies=dep)
    async def import_job(job_id: str) -> dict[str, Any]:
        job = data_import.IMPORT_JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "no such import")
        return job.view()

    @app.post("/api/data/import/text", dependencies=dep)
    async def import_text(
        request: Request,
        name: str = Query("Imported text", max_length=255),
        restore_chats: bool = Query(True),
        background_memory: bool = Query(True),
        apps: str = Query("both"),
        main_chat: str = Query("side"),
    ) -> dict[str, Any]:
        """Import pasted text: the text itself as the request body. The switches work as
        for ``/api/data/import``; ``apps`` and ``main_chat`` simply do not apply."""
        try:
            options = data_import.ImportOptions(
                restore_chats=restore_chats,
                background_memory=background_memory,
                apps=apps,
                main_chat=main_chat,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        body = await request.body()
        text = body.decode("utf-8", errors="replace")
        try:
            plan = await asyncio.to_thread(
                data_import.ingest_text, svc, text, name, options
            )
            return data_import.apply_plan(svc, plan, options)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/logs/export", dependencies=dep_or_signed)
    @app.get("/api/diagnostics/export", dependencies=dep_or_signed)
    async def export_logs() -> Response:
        """Export all server logs and error summaries as a zip archive."""
        data = await asyncio.to_thread(log_export.build_logs_zip)
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{log_export.logs_filename()}"',
                "Cache-Control": "private, no-store",
            },
        )

    @app.get("/api/logs/summary", dependencies=dep_or_signed)
    @app.get("/api/logs/errors", dependencies=dep_or_signed)
    async def error_summary() -> PlainTextResponse:
        """Live plain-text summary of recent errors and system diagnostics."""
        text = await asyncio.to_thread(log_export.get_error_summary)
        return PlainTextResponse(text, headers={"Cache-Control": "no-cache"})

    @app.get("/api/files/{path:path}", dependencies=dep_or_signed)
    async def get_file(path: str, download: bool = False) -> Response:
        try:
            target = svc.resolve_workspace_path(path)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        if not target.is_file():
            raise HTTPException(404, "no such file")
        media, _ = mimetypes.guess_type(str(target))
        if media is None or target.suffix.lower() in (
            ".md",
            ".txt",
            ".log",
            ".csv",
            ".json",
            ".py",
        ):
            media = (
                "text/plain; charset=utf-8"
                if target.suffix.lower() != ".json"
                else "application/json"
            )
        headers: dict[str, str] = {}
        if media.startswith("text/html") or media == "image/svg+xml":
            # Files the agent wrote never run with the app's origin: no token, no API.
            headers["Content-Security-Policy"] = "sandbox allow-scripts allow-popups"
        # ``filename=`` lets Starlette write the Content-Disposition header itself, which
        # encodes a name that is not ASCII (报告.txt) instead of failing on it.
        return FileResponse(
            target, media_type=media, headers=headers, filename=target.name if download else None
        )

    # ------------------------------------------------------------------ websocket
    @app.get("/api/devices", dependencies=dep)
    async def devices_view(request: Request) -> dict[str, Any]:
        """The devices this nanoMuse is built on, without any account: this computer and
        the phones talking to it directly. The hub's signed-in list sits alongside."""
        return svc.local_devices_view(
            current_ua=request.headers.get("user-agent", ""),
            client_device=request.headers.get("x-nanomuse-device", ""),
        )

    def _resolve_look(request: Request) -> tuple[str, str, str]:
        """Resolve (theme, bg_hex, bar_color) from ?look=, server ui settings, and nm_look cookie."""
        import re as _re

        ui_cfg = (svc._ui_view() if hasattr(svc, "_ui_view") else None) or {}
        saved_bg = str(ui_cfg.get("bg") or "").strip()
        if saved_bg.lower() == "#000000":
            saved_bg = ""
        saved_theme = str(ui_cfg.get("theme") or "light").strip()

        q_look = (request.query_params.get("look") or "").strip()
        c_look = (request.cookies.get("nm_look") or "").strip()

        bg = saved_bg
        theme = saved_theme
        if q_look:
            if ":" in q_look:
                theme, bg = q_look.split(":", 1)
            else:
                theme, bg = q_look, ""
        elif ":" in c_look:
            c_theme, c_bg = c_look.split(":", 1)
            if (
                not bg
                and _re.fullmatch(r"#[0-9a-fA-F]{6}", c_bg or "")
                and c_bg.lower() != "#000000"
            ):
                bg = c_bg
            if not bg and c_theme in ("light", "dark"):
                theme = c_theme

        if _re.fullmatch(r"#[0-9a-fA-F]{6}", bg or ""):
            r = int(bg[1:3], 16) / 255.0
            g = int(bg[3:5], 16) / 255.0
            b = int(bg[5:7], 16) / 255.0
            lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
            theme = "dark" if lum < 0.45 else "light"
            bar_color = bg
        elif theme == "dark":
            bg = ""
            bar_color = "#181819"
        else:
            theme = "light"
            bg = ""
            bar_color = "#ffffff"
        return theme, bg, bar_color

    @app.get("/manifest.webmanifest")
    async def manifest(request: Request) -> JSONResponse:
        """The installed app's manifest, served with the bars matching the look the
        device chose: the client passes ``?look=theme:bghex`` or keeps an ``nm_look``
        cookie ("theme:bghex"), falling back to the saved ``ui`` settings on the server.
        With the light look (or no setting) both bars are white."""
        _, _, bar_color = _resolve_look(request)
        data: dict[str, Any] = {}
        manifest_path = STATIC_DIR / "manifest.webmanifest"
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        changed = (
            data.get("theme_color") != bar_color
            or data.get("background_color") != bar_color
            or data.get("display") != "fullscreen"
        )
        data["theme_color"] = bar_color
        data["background_color"] = bar_color
        data["display"] = "fullscreen"
        data["display_override"] = ["fullscreen", "standalone"]
        if changed and manifest_path.is_file():
            with contextlib.suppress(OSError):
                manifest_path.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        return JSONResponse(
            data,
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache"},
        )

    @app.put("/api/devices/{device_id:path}", dependencies=dep)
    async def rename_local_device(device_id: str, body: DeviceRenameBody) -> dict[str, Any]:
        """Set or clear a custom nickname for a local device (PC or phone)."""
        return svc.rename_local_device(device_id, body.name)

    @app.websocket("/ws")
    async def websocket(ws: WebSocket) -> None:
        if "token" in ws.query_params:
            # ``?token=`` lands in access logs: taken until 0.1.32, refused now. The socket is
            # accepted for a moment so the client reads why (an older app sees 4401 either way).
            await ws.accept()
            await ws.send_json(
                {"kind": "error", "code": "legacy_token", "message": LEGACY_TOKEN_MESSAGE}
            )
            await ws.close(code=4401, reason="legacy_token")
            return
        await ws.accept()
        client_ip = ws.client.host if ws.client else ""
        # the token is the real gate on the socket too, same hosts as the REST API: the
        # tests' testclient client authenticates like anything else
        is_trusted = client_ip in ("127.0.0.1", "::1", "localhost")
        if svc.token and not is_trusted and not await _ws_first_frame_auth(ws, svc.token):
            await ws.close(code=4401)
            return
        queue = svc.bus.subscribe()
        client_ua = ws.headers.get("user-agent", "")
        ch_model = ws.headers.get("sec-ch-ua-model", "").strip().strip('"')
        ch_plat_ver = ws.headers.get("sec-ch-ua-platform-version", "").strip().strip('"')
        svc.note_client(client_ua, model=ch_model, platform_version=ch_plat_ver)
        await ws.send_json({"kind": "hello", "state": svc.state()})
        conn_id = f"ws-{uuid.uuid4().hex[:8]}"
        # one writer for this socket: the pump and the device requests share it
        lock = asyncio.Lock()

        async def send(msg: dict[str, Any]) -> None:
            async with lock:
                await ws.send_json(msg)

        async def pump() -> None:
            while True:
                msg = await queue.get()
                await send(msg)

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                data = await ws.receive_json()
                try:
                    await _handle_ws_message(svc, ws, data, conn_id=conn_id, send=send)
                except Exception as exc:  # noqa: BLE001
                    # one bad frame (an unknown keyword, a client bug) must not take the
                    # socket down: the client would just reconnect and send it again, and
                    # the page would strobe "Reconnecting" forever.
                    logger.warning("websocket message failed: {}", exc)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("websocket closed: {}", exc)
        finally:
            pump_task.cancel()
            svc.bus.unsubscribe(queue)
            svc.phone.detach(conn_id)
            svc.forget_client(client_ua)

    async def _accept_authed_ws(ws: WebSocket) -> bool:
        await ws.accept()
        client_ip = ws.client.host if ws.client else ""
        is_trusted = client_ip in ("127.0.0.1", "::1", "localhost")
        if svc.token and not is_trusted and not await _ws_first_frame_auth(ws, svc.token):
            await ws.close(code=4401)
            return False
        return True

    @app.websocket("/ws/chrome")
    async def chrome_extension_ws(ws: WebSocket) -> None:
        """WebSocket channel for the MiniMuse Chrome extension (Side Panel + background tab)."""
        if not await _accept_authed_ws(ws):
            return
        async def send(msg: dict[str, Any]) -> None:
            await ws.send_json(msg)

        session = None
        try:
            while True:
                data = await ws.receive_json()
                if not isinstance(data, dict):
                    continue
                kind = str(data.get("kind") or data.get("type") or "")
                if kind == "auth":
                    continue
                if session is None:
                    session = svc.chrome_bridge.attach_socket(
                        send,
                        version=str(data.get("version") or ""),
                        browser=str(data.get("browser") or "Chrome"),
                    )
                svc.chrome_bridge.handle_message(session, data)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("chrome websocket closed: {}", exc)
        finally:
            svc.chrome_bridge.detach_socket(session)

    @app.websocket("/ws/call")
    async def call_websocket(ws: WebSocket) -> None:
        """WebSocket channel for Live Call mode (Native Realtime + Universal Hands-Free Loop)."""
        if not await _accept_authed_ws(ws):
            return
        thread_id = str(ws.query_params.get("thread") or MAIN_THREAD)
        try:
            patience_ms = int(ws.query_params.get("patience_ms") or 2600)
        except ValueError:
            patience_ms = 2600
        try:
            await handle_call_websocket(svc, ws, thread_id=thread_id, patience_ms=patience_ms)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("call websocket closed: {}", exc)

    # Device-to-device sync without a relay: this runtime also serves the relay's sync
    # protocol, plus easy 6-character pairing, so devices sync straight to each other.
    # Must be registered BEFORE the static SPA catch-all route below so GET /v1/sync/*
    # never resolves to index.html.
    from nanomuse.sync.peer import make_peer_router

    @app.get("/v1/sync/hello")
    async def peer_hello() -> dict[str, Any]:
        return {
            "id": svc.hub.device_id,
            "name": svc.hub.device_name,
            "kind": "phone" if svc.app.device is not None else "computer",
            "version": svc.settings_view()["version"],
            "pair_ready": svc.sync.pairing.pair_ready,
        }

    @app.post("/v1/sync/pair/claim")
    async def peer_claim(body: PeerClaimBody, request: Request) -> JSONResponse:
        ip = request.client.host if request.client else ""
        ok, payload, status = svc.sync.accept_inbound_claim(
            code=body.code,
            remote_id=body.device_id,
            remote_name=body.name,
            remote_urls=body.urls,
            return_token=body.peer_token,
            client_ip=ip,
        )
        return JSONResponse(payload, status_code=status)

    app.include_router(
        make_peer_router(
            settings.data_dir / "sync-peer.db",
            store=svc.sync.local_store,
            on_push=lambda _payload: svc.sync.on_inbound_peer_push(),
            before_pull=svc.sync.mirror_local_now,
        ),
        prefix="/v1/sync",
        dependencies=dep_sync,
        tags=["sync"],
    )

    # ------------------------------------------------------------------ static SPA
    if STATIC_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles

        # nanoMuse: the avatars are WebP; older Python mimetypes tables do not know it
        mimetypes.add_type("image/webp", ".webp")

        class HashedAssets(StaticFiles):
            """The build's files under /assets carry a content hash in their names.

            A new build gets new names, so a browser may keep a copy for a year without
            ever asking again; index.html (``no-cache``) is what names the current set.
            """

            def file_response(
                self,
                full_path: str | os.PathLike[str],
                stat_result: os.stat_result,
                scope: Scope,
                status_code: int = 200,
            ) -> Response:
                response = super().file_response(full_path, stat_result, scope, status_code)
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return response

        assets = STATIC_DIR / "assets"
        if assets.is_dir():
            app.mount("/assets", HashedAssets(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str, request: Request) -> Response:
            if full_path.startswith("api/") or full_path.startswith("v1/sync"):
                # a wrong API path is an error, not the app's HTML with a 200 — clients
                # (and people with curl) see what happened
                raise HTTPException(404, f"no such endpoint: /{full_path}")
            candidate = (STATIC_DIR / full_path).resolve() if full_path else None
            if candidate and STATIC_DIR.resolve() in candidate.parents and candidate.is_file():
                if candidate.name == "sw.js":
                    # the service worker: always revalidated, so a new build reaches phones
                    return FileResponse(
                        candidate,
                        media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"},
                    )
                if candidate.name == "manifest.webmanifest":
                    return await manifest(request)
                return FileResponse(candidate)
            index = STATIC_DIR / "index.html"
            if index.is_file():
                spa_headers = {
                    "Cache-Control": "no-cache",
                    "Accept-CH": "Sec-CH-UA-Model, Sec-CH-UA-Platform, Sec-CH-UA-Platform-Version",
                }
                try:
                    theme, bg, bar_color = _resolve_look(request)
                    html = index.read_text(encoding="utf-8")
                    html = html.replace(
                        '<meta name="theme-color" content="#ffffff" />',
                        f'<meta name="theme-color" content="{bar_color}" />',
                        1,
                    )
                    boot_payload = json.dumps(
                        {"theme": theme, "bg": bg, "bar_color": bar_color},
                        ensure_ascii=False,
                    )
                    html = html.replace(
                        "<script>",
                        f"<script>window.__NM_SERVER_UI__={boot_payload};",
                        1,
                    )
                    return HTMLResponse(html, headers=spa_headers)
                except Exception:  # noqa: BLE001
                    return FileResponse(index, headers=spa_headers)
            raise HTTPException(404)

    else:

        @app.get("/", include_in_schema=False)
        async def no_frontend() -> Response:
            return JSONResponse(
                {
                    "message": "nanoMuse API is running, but the web app is not built. "
                    "Run `cd web && npm install && npm run build` or use the CLI (`nanomuse chat`).",
                }
            )

    install_channels(app, svc, dep)  # /api/channels — Feishu, DingTalk, WeCom, Telegram
    install_ofa(app, svc, dep)
    install_spaces(app, dep)  # /api/ofa/*, /one-for-all, /mha-rankings, /apps/{slug}
    return app


WS_AUTH_TIMEOUT_S = 10.0
"""How long a fresh socket has to send ``{"kind": "auth", "token": …}`` before it is closed."""


async def _ws_first_frame_auth(ws: WebSocket, token: str) -> bool:
    """The first frame on a socket opened without ``?token=`` carries the token, so it never
    appears in a URL. Anything else, a wrong token or silence closes the socket with 4401."""
    try:
        data = await asyncio.wait_for(ws.receive_json(), WS_AUTH_TIMEOUT_S)
    except (TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
        return False
    if not isinstance(data, dict) or data.get("kind") != "auth":
        return False
    given = str(data.get("token") or "")
    return bool(given) and secrets.compare_digest(given.encode(), token.encode())


async def _handle_ws_message(
    svc: MuseService,
    ws: WebSocket,
    data: dict[str, Any],
    conn_id: str = "",
    send: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> None:
    kind = data.get("kind") or data.get("type")
    reply = send or ws.send_json  # one writer per socket when the pump shares it
    try:
        if kind == "device":
            # a phone announcing itself: from now on the server may ask it for its screen
            svc.phone.attach(conn_id, data, reply)
            await reply({"kind": "device_ack", "phone": svc.phone_view()})
        elif kind == "client_info":
            svc.note_client_info(
                ws.headers.get("user-agent", ""),
                model=str(data.get("model") or ""),
                platform_version=str(data.get("platform_version") or ""),
                width=int(data.get("screen_w") or 0),
                height=int(data.get("screen_h") or 0),
            )
            await reply({"kind": "local_devices", "local_devices": svc.local_devices_view()})
        elif kind == "device_result":
            if not svc.phone.resolve(data):
                logger.debug("device result for no pending request: {}", data.get("id"))
        elif kind == "send":
            files = data.get("files")
            svc.send(
                str(data.get("thread") or MAIN_THREAD),
                str(data.get("text", ""))[:20_000],
                files=[str(f) for f in files][:10] if isinstance(files, list) else None,
                language=str(data.get("language") or "")[:20],
                via=str(data.get("via") or "")[:20],
                event_id=str(data.get("id") or "") or None,
                model=str(data.get("model") or "")[:20],
            )
        elif kind == "approval":
            approval_id = str(data.get("id", ""))
            if approval_id in svc.hub.remote_approvals:
                # nanoMuse: a card raised by another device's run — the answer goes back over the hub
                ok = await svc.hub.decide_remote(approval_id, bool(data.get("approved")))
                if not ok:
                    await reply({"kind": "error", "error": "the device did not take the answer"})
                return
            ok = svc.decide(
                approval_id,
                bool(data.get("approved")),
                str(data.get("scope", "once")),
                str(data.get("reason", "")),
            )
            if not ok:
                await reply({"kind": "error", "error": "no pending approval with that id"})
        elif kind == "ping":
            await reply({"kind": "pong", "status": svc.ui.overall_status()})
        elif kind == "auth":
            pass  # a client that sends its token first even though the runtime has none
        else:
            await reply({"kind": "error", "error": f"unknown message kind: {kind}"})
    except (ValueError, PermissionError) as exc:
        await reply({"kind": "error", "error": str(exc)})
    except CloudError as exc:
        await reply({"kind": "error", "error": exc.describe(), "code": exc.code})


__all__ = ["STATIC_DIR", "create_app"]
