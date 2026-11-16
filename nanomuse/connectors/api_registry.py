"""Standard API presets & dynamic custom API registry for MiniMuse.

Supports 36+ built-in API templates (Google Workspace, Microsoft Graph, Slack,
Discord, GitHub, Notion, Linear, Home Assistant, Spotify, Stripe, weather,
maps/traffic, etc.), multiple named instances per API (e.g. Work + Personal),
OpenAPI/Swagger endpoint discovery, and AI-learned endpoint schemas.

Secrets always remain inside ``CredentialVault`` (referenced by ``vault_key``
or ``{{vault:KEY}}`` placeholders) and are never returned to the UI or LLM.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from nanomuse.logger import logger
from nanomuse.private import keep_private

if TYPE_CHECKING:
    from nanomuse.vault import CredentialVault

_BLOCKED_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.goog",
    "100.100.100.200",
}

MAX_RESPONSE_CHARS = 16_000
MAX_OPENAPI_ENDPOINTS = 60


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _slug(value: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", (value or "").strip().lower()).strip("_")
    return s[:40] or "api"


def _vault_key_for(api_id: str) -> str:
    return f"API_CONN_{re.sub(r'[^A-Z0-9]+', '_', api_id.upper()).strip('_')}_SECRET"


def validate_api_url(url: str) -> str:
    """Validate URL scheme & reject cloud metadata endpoints."""
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("API URL must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("API URL must include a host name")
    if parsed.username or parsed.password:
        raise ValueError("Do not put username/password directly in the URL; use auth settings")
    host = (parsed.hostname or "").lower()
    if host in _BLOCKED_METADATA_HOSTS:
        raise ValueError(f"Host {host} is blocked for security reasons")
    return cleaned.rstrip("/")


def _endpoint(
    ep_id: str,
    name: str,
    method: str,
    path: str,
    description: str,
    query_params: list[str] | None = None,
    body_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": ep_id,
        "name": name,
        "method": method.upper(),
        "path": path,
        "description": description,
        "query_params": query_params or [],
        "body_schema": body_schema or {},
    }


STANDARD_API_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "id": "gmail",
        "name": "Gmail REST API",
        "category": "Google",
        "description": "Search threads, read messages, create drafts, and send email via Gmail API.",
        "base_url": "https://gmail.googleapis.com/gmail/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.google.com/gmail/api/reference/rest",
        "test_endpoint": {"method": "GET", "path": "/users/me/profile"},
        "ai_guide": "Use /users/me/messages?q=<search> to list IDs, then /users/me/messages/{id} to read.",
        "endpoints": [
            _endpoint("profile", "Get profile", "GET", "/users/me/profile", "Get authenticated Gmail profile"),
            _endpoint("list_messages", "Search messages", "GET", "/users/me/messages", "List/search messages", ["q", "maxResults", "labelIds"]),
            _endpoint("get_message", "Read message", "GET", "/users/me/messages/{id}", "Get full message payload"),
            _endpoint("send_message", "Send raw RFC822 message", "POST", "/users/me/messages/send", "Send base64url encoded message", body_schema={"raw": "base64url"}),
        ],
    },
    "google_calendar": {
        "id": "google_calendar",
        "name": "Google Calendar API",
        "category": "Google",
        "description": "List calendars, read events, create/update meetings, and check free/busy.",
        "base_url": "https://www.googleapis.com/calendar/v3",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.google.com/calendar/api/v3/reference",
        "test_endpoint": {"method": "GET", "path": "/users/me/calendarList?maxResults=5"},
        "ai_guide": "Pass singleEvents=true&orderBy=startTime with timeMin/timeMax RFC3339 timestamps when listing events.",
        "endpoints": [
            _endpoint("calendars", "List calendars", "GET", "/users/me/calendarList", "List all user calendars"),
            _endpoint("events_list", "List events", "GET", "/calendars/primary/events", "List events on primary calendar", ["timeMin", "timeMax", "singleEvents", "orderBy", "q"]),
            _endpoint("events_create", "Create event", "POST", "/calendars/primary/events", "Create calendar event", body_schema={"summary": "string", "start": {"dateTime": "ISO8601"}, "end": {"dateTime": "ISO8601"}, "location": "string", "description": "string"}),
        ],
    },
    "google_drive": {
        "id": "google_drive",
        "name": "Google Drive API",
        "category": "Google",
        "description": "Search files and folders, read file metadata, and export Docs/Sheets.",
        "base_url": "https://www.googleapis.com/drive/v3",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.google.com/drive/api/reference/rest/v3",
        "test_endpoint": {"method": "GET", "path": "/about?fields=user"},
        "ai_guide": "Use q=\"name contains '...' and trashed = false\" and fields=\"files(id,name,mimeType,modifiedTime)\".",
        "endpoints": [
            _endpoint("files_list", "Search Drive files", "GET", "/files", "Search files", ["q", "pageSize", "fields"]),
            _endpoint("file_meta", "Get file metadata", "GET", "/files/{fileId}", "Read file metadata", ["fields"]),
        ],
    },
    "google_tasks": {
        "id": "google_tasks",
        "name": "Google Tasks API",
        "category": "Google",
        "description": "Manage Google Task lists and tasks.",
        "base_url": "https://tasks.googleapis.com/tasks/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.google.com/tasks/reference/rest",
        "test_endpoint": {"method": "GET", "path": "/users/@me/lists"},
        "ai_guide": "Fetch lists first via /users/@me/lists, then manage items under /lists/{tasklist}/tasks.",
        "endpoints": [
            _endpoint("lists", "List tasklists", "GET", "/users/@me/lists", "List all Google task lists"),
            _endpoint("tasks", "List tasks in list", "GET", "/lists/{tasklist}/tasks", "List tasks", ["showCompleted", "showHidden"]),
            _endpoint("create_task", "Create task", "POST", "/lists/@default/tasks", "Add task", body_schema={"title": "string", "notes": "string", "due": "RFC3339"}),
        ],
    },
    "google_maps_places": {
        "id": "google_maps_places",
        "name": "Google Maps Places & Routes",
        "category": "Maps & Weather",
        "description": "Search places, addresses, coordinates, and compute live driving traffic & ETA.",
        "base_url": "https://places.googleapis.com/v1",
        "auth_type": "api_key_header",
        "auth_header": "X-Goog-Api-Key",
        "auth_prefix": "",
        "default_headers": {"X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"},
        "docs_url": "https://developers.google.com/maps/documentation/places/web-service/op-overview",
        "ai_guide": "POST /places:searchText with {\"textQuery\": \"...\"}. For routes use https://routes.googleapis.com/directions/v2:computeRoutes.",
        "endpoints": [
            _endpoint("search_text", "Search places", "POST", "/places:searchText", "Search places or addresses", body_schema={"textQuery": "string"}),
            _endpoint("routes", "Compute driving route & traffic", "POST", "https://routes.googleapis.com/directions/v2:computeRoutes", "Driving distance, ETA, and traffic", body_schema={"origin": {"address": "string"}, "destination": {"address": "string"}, "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE"}),
        ],
    },
    "microsoft_graph": {
        "id": "microsoft_graph",
        "name": "Microsoft Graph (Outlook & 365)",
        "category": "Microsoft",
        "description": "Outlook Mail, Calendar events, To Do tasks, Contacts, and OneDrive files.",
        "base_url": "https://graph.microsoft.com/v1.0",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://learn.microsoft.com/en-us/graph/api/overview",
        "test_endpoint": {"method": "GET", "path": "/me"},
        "ai_guide": "Supports OData params $top, $select, $filter, $orderby on /me/messages, /me/events, /me/contacts.",
        "endpoints": [
            _endpoint("me", "Current user", "GET", "/me", "Get Microsoft profile"),
            _endpoint("messages", "List Outlook emails", "GET", "/me/messages", "List inbox messages", ["$top", "$search", "$filter"]),
            _endpoint("events", "List Outlook calendar events", "GET", "/me/calendarView", "Calendar events in window", ["startDateTime", "endDateTime"]),
            _endpoint("contacts", "List Outlook contacts", "GET", "/me/contacts", "List contacts", ["$top", "$search"]),
        ],
    },
    "slack": {
        "id": "slack",
        "name": "Slack Web API",
        "category": "Communication",
        "description": "List Slack channels, read messages, search history, and post messages.",
        "base_url": "https://slack.com/api",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://api.slack.com/methods",
        "test_endpoint": {"method": "POST", "path": "/auth.test"},
        "ai_guide": "Most Slack endpoints accept form/JSON POST. Check \"ok\": true in JSON response body.",
        "endpoints": [
            _endpoint("channels", "List channels", "GET", "/conversations.list", "List public/private channels", ["types", "limit"]),
            _endpoint("history", "Read channel messages", "GET", "/conversations.history", "Read channel history", ["channel", "limit"]),
            _endpoint("post_message", "Send message", "POST", "/chat.postMessage", "Post message to channel", body_schema={"channel": "C123", "text": "string"}),
        ],
    },
    "discord": {
        "id": "discord",
        "name": "Discord API",
        "category": "Communication",
        "description": "Read/send Discord messages, manage channels, and trigger webhooks.",
        "base_url": "https://discord.com/api/v10",
        "auth_type": "api_key_header",
        "auth_header": "Authorization",
        "auth_prefix": "Bot ",
        "docs_url": "https://discord.com/developers/docs/reference",
        "test_endpoint": {"method": "GET", "path": "/users/@me"},
        "ai_guide": "Bot tokens use prefix 'Bot '. For webhook URLs, POST JSON {\"content\": \"...\"} directly to webhook URL.",
        "endpoints": [
            _endpoint("me", "Current bot user", "GET", "/users/@me", "Get bot info"),
            _endpoint("guilds", "List servers", "GET", "/users/@me/guilds", "List servers the bot belongs to"),
            _endpoint("messages", "Read channel messages", "GET", "/channels/{channel_id}/messages", "Fetch recent messages", ["limit"]),
            _endpoint("send_message", "Send message", "POST", "/channels/{channel_id}/messages", "Post a message", body_schema={"content": "string"}),
        ],
    },
    "telegram": {
        "id": "telegram",
        "name": "Telegram Bot API",
        "category": "Communication",
        "description": "Send messages, read updates, and manage Telegram bot chats.",
        "base_url": "https://api.telegram.org",
        "auth_type": "none",
        "docs_url": "https://core.telegram.org/bots/api",
        "ai_guide": "Place token in vault (e.g. TELEGRAM_BOT_TOKEN) and call /bot{{vault:KEY}}/getMe or /bot{{vault:KEY}}/sendMessage.",
        "endpoints": [
            _endpoint("me", "Get bot info", "GET", "/bot{{vault:TELEGRAM_TOKEN}}/getMe", "Verify bot token"),
            _endpoint("updates", "Get incoming updates", "GET", "/bot{{vault:TELEGRAM_TOKEN}}/getUpdates", "Read recent bot messages", ["limit", "offset"]),
            _endpoint("send", "Send Telegram message", "POST", "/bot{{vault:TELEGRAM_TOKEN}}/sendMessage", "Send chat message", body_schema={"chat_id": "string", "text": "string"}),
        ],
    },
    "twilio": {
        "id": "twilio",
        "name": "Twilio SMS & Voice",
        "category": "Communication",
        "description": "List SMS logs and send SMS via Twilio REST API.",
        "base_url": "https://api.twilio.com/2010-04-01",
        "auth_type": "basic",
        "docs_url": "https://www.twilio.com/docs/usage/api",
        "ai_guide": "Basic auth uses AccountSID:AuthToken. POST bodies to /Accounts/{Sid}/Messages.json are form-encoded.",
        "endpoints": [
            _endpoint("messages", "List SMS messages", "GET", "/Accounts/{AccountSid}/Messages.json", "List recent messages", ["PageSize", "To", "From"]),
        ],
    },
    "github": {
        "id": "github",
        "name": "GitHub REST API",
        "category": "Developer",
        "description": "Repositories, issues, pull requests, releases, commits, and Actions.",
        "base_url": "https://api.github.com",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_headers": {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        "docs_url": "https://docs.github.com/en/rest",
        "test_endpoint": {"method": "GET", "path": "/user"},
        "ai_guide": "Supports pagination with per_page and page query params.",
        "endpoints": [
            _endpoint("user", "Authenticated user", "GET", "/user", "Get current GitHub account"),
            _endpoint("repos", "List repos", "GET", "/user/repos", "List repositories", ["sort", "per_page"]),
            _endpoint("issues", "List repo issues", "GET", "/repos/{owner}/{repo}/issues", "List issues & PRs", ["state", "per_page"]),
            _endpoint("create_issue", "Create issue", "POST", "/repos/{owner}/{repo}/issues", "Open a new issue", body_schema={"title": "string", "body": "string", "labels": ["string"]}),
        ],
    },
    "gitlab": {
        "id": "gitlab",
        "name": "GitLab REST API",
        "category": "Developer",
        "description": "Projects, issues, merge requests, pipelines, and snippets.",
        "base_url": "https://gitlab.com/api/v4",
        "auth_type": "api_key_header",
        "auth_header": "PRIVATE-TOKEN",
        "auth_prefix": "",
        "docs_url": "https://docs.gitlab.com/ee/api/rest/",
        "test_endpoint": {"method": "GET", "path": "/user"},
        "ai_guide": "URL-encode project path (namespace%2Frepo) or pass numeric project ID.",
        "endpoints": [
            _endpoint("user", "Current user", "GET", "/user", "Get GitLab user"),
            _endpoint("projects", "List projects", "GET", "/projects", "List accessible projects", ["membership", "search"]),
            _endpoint("issues", "List issues", "GET", "/issues", "List assigned/created issues", ["state", "scope"]),
        ],
    },
    "linear": {
        "id": "linear",
        "name": "Linear API (GraphQL)",
        "category": "Developer",
        "description": "Query and create issues, cycles, projects, and comments in Linear.",
        "base_url": "https://api.linear.app",
        "auth_type": "api_key_header",
        "auth_header": "Authorization",
        "auth_prefix": "",
        "docs_url": "https://developers.linear.app/docs/graphql/working-with-the-graphql-api",
        "ai_guide": "POST /graphql with JSON {\"query\": \"...\", \"variables\": {}}. Note Linear personal API keys do NOT use Bearer prefix.",
        "endpoints": [
            _endpoint("graphql", "GraphQL query/mutation", "POST", "/graphql", "Run GraphQL query or mutation", body_schema={"query": "query { viewer { id name } issues(first: 10) { nodes { id title state { name } } } }"}),
        ],
    },
    "jira": {
        "id": "jira",
        "name": "Atlassian Jira REST v3",
        "category": "Developer",
        "description": "Search Jira issues with JQL, transition statuses, and comment.",
        "base_url": "https://your-domain.atlassian.net/rest/api/3",
        "auth_type": "basic",
        "docs_url": "https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
        "test_endpoint": {"method": "GET", "path": "/myself"},
        "ai_guide": "Replace your-domain in base_url. Basic auth secret format is base64(email:api_token).",
        "endpoints": [
            _endpoint("myself", "Current user", "GET", "/myself", "Get Jira user"),
            _endpoint("search", "Search issues (JQL)", "GET", "/search/jql", "Query issues using JQL", ["jql", "maxResults"]),
            _endpoint("create_issue", "Create Jira issue", "POST", "/issue", "Create issue", body_schema={"fields": {"project": {"key": "PROJ"}, "summary": "string", "issuetype": {"name": "Task"}}}),
        ],
    },
    "notion": {
        "id": "notion",
        "name": "Notion API",
        "category": "Productivity",
        "description": "Search workspace pages, query databases, create pages, and append blocks.",
        "base_url": "https://api.notion.com/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_headers": {"Notion-Version": "2022-06-28"},
        "docs_url": "https://developers.notion.com/reference/intro",
        "test_endpoint": {"method": "GET", "path": "/users/me"},
        "ai_guide": "Always keep Notion-Version header. POST /search to discover shared pages & database IDs.",
        "endpoints": [
            _endpoint("search", "Search Notion", "POST", "/search", "Search pages and databases", body_schema={"query": "string", "page_size": 20}),
            _endpoint("query_db", "Query database", "POST", "/databases/{database_id}/query", "Query rows in a Notion database", body_schema={"page_size": 25}),
            _endpoint("create_page", "Create page", "POST", "/pages", "Create page inside database or parent page"),
        ],
    },
    "todoist": {
        "id": "todoist",
        "name": "Todoist REST v2",
        "category": "Productivity",
        "description": "List projects and tasks, create due items, and close tasks in Todoist.",
        "base_url": "https://api.todoist.com/rest/v2",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developer.todoist.com/rest/v2/",
        "test_endpoint": {"method": "GET", "path": "/projects"},
        "ai_guide": "Todoist task creation supports natural-language due_string like 'tomorrow at 5pm'.",
        "endpoints": [
            _endpoint("projects", "List projects", "GET", "/projects", "List all projects"),
            _endpoint("tasks", "List active tasks", "GET", "/tasks", "List tasks", ["project_id", "filter"]),
            _endpoint("create_task", "Create task", "POST", "/tasks", "Add a task", body_schema={"content": "string", "due_string": "string", "priority": 4}),
            _endpoint("close_task", "Complete task", "POST", "/tasks/{id}/close", "Mark task completed"),
        ],
    },
    "trello": {
        "id": "trello",
        "name": "Trello REST API",
        "category": "Productivity",
        "description": "Boards, lists, cards, checklists, and labels on Trello.",
        "base_url": "https://api.trello.com/1",
        "auth_type": "api_key_query",
        "auth_param": "key",
        "docs_url": "https://developer.atlassian.com/cloud/trello/rest/",
        "ai_guide": "Trello takes both key and token query params.",
        "endpoints": [
            _endpoint("boards", "List boards", "GET", "/members/me/boards", "List user boards"),
            _endpoint("cards", "Board cards", "GET", "/boards/{boardId}/cards", "List cards on a board"),
        ],
    },
    "asana": {
        "id": "asana",
        "name": "Asana REST API",
        "category": "Productivity",
        "description": "Workspaces, projects, tasks, and subtasks in Asana.",
        "base_url": "https://app.asana.com/api/1.0",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.asana.com/reference/rest-api-reference",
        "test_endpoint": {"method": "GET", "path": "/users/me"},
        "ai_guide": "Read /workspaces first to get workspace gid.",
        "endpoints": [
            _endpoint("me", "Current user", "GET", "/users/me", "Get Asana profile"),
            _endpoint("tasks", "List tasks", "GET", "/tasks", "List tasks by assignee+workspace or project", ["assignee", "workspace", "project"]),
        ],
    },
    "airtable": {
        "id": "airtable",
        "name": "Airtable Web API",
        "category": "Productivity",
        "description": "Read, create, update, and filter Airtable base records.",
        "base_url": "https://api.airtable.com/v0",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://airtable.com/developers/web/api/introduction",
        "test_endpoint": {"method": "GET", "path": "/meta/bases"},
        "ai_guide": "Call /meta/bases to discover base IDs, then /{baseId}/{tableIdOrName}.",
        "endpoints": [
            _endpoint("bases", "List bases", "GET", "/meta/bases", "List accessible bases"),
            _endpoint("records", "List table records", "GET", "/{baseId}/{tableIdOrName}", "Read records", ["maxRecords", "filterByFormula"]),
            _endpoint("create_records", "Create records", "POST", "/{baseId}/{tableIdOrName}", "Insert rows", body_schema={"records": [{"fields": {}}]}),
        ],
    },
    "clickup": {
        "id": "clickup",
        "name": "ClickUp API v2",
        "category": "Productivity",
        "description": "Spaces, folders, lists, and tasks in ClickUp.",
        "base_url": "https://api.clickup.com/api/v2",
        "auth_type": "api_key_header",
        "auth_header": "Authorization",
        "auth_prefix": "",
        "docs_url": "https://clickup.com/api/",
        "test_endpoint": {"method": "GET", "path": "/user"},
        "ai_guide": "ClickUp personal tokens use raw token without 'Bearer ' prefix.",
        "endpoints": [
            _endpoint("user", "Current user", "GET", "/user", "Get ClickUp user"),
            _endpoint("teams", "Workspaces", "GET", "/team", "List workspaces"),
            _endpoint("tasks", "List tasks", "GET", "/list/{list_id}/task", "Get tasks in a list", ["subtasks", "include_closed"]),
        ],
    },
    "obsidian_local": {
        "id": "obsidian_local",
        "name": "Obsidian Local REST API",
        "category": "Productivity",
        "description": "Read, search, append, and write Markdown notes in local Obsidian vault.",
        "base_url": "http://127.0.0.1:27123",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://coddingtonbear.github.io/obsidian-local-rest-api/",
        "test_endpoint": {"method": "GET", "path": "/"},
        "ai_guide": "Runs locally when Obsidian Local REST plugin is enabled (HTTP 27123 or HTTPS 27124).",
        "endpoints": [
            _endpoint("search", "Search vault notes", "POST", "/search/simple/", "Full-text search notes", ["query"]),
            _endpoint("get_note", "Read Markdown note", "GET", "/vault/{filename}", "Read a note from vault"),
            _endpoint("put_note", "Create/update note", "PUT", "/vault/{filename}", "Write Markdown note"),
        ],
    },
    "home_assistant": {
        "id": "home_assistant",
        "name": "Home Assistant REST API",
        "category": "Smart Home",
        "description": "Inspect sensors, thermostats, locks, and control lights/switches at home.",
        "base_url": "http://homeassistant.local:8123/api",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.home-assistant.io/docs/api/rest/",
        "test_endpoint": {"method": "GET", "path": "/"},
        "ai_guide": "GET /states returns all entity_ids and attributes. POST /services/<domain>/<service> with {\"entity_id\": \"...\"} turns devices on/off.",
        "endpoints": [
            _endpoint("states", "All entity states", "GET", "/states", "List every sensor/light/climate state"),
            _endpoint("state_one", "Single entity state", "GET", "/states/{entity_id}", "Inspect one entity"),
            _endpoint("call_service", "Call service", "POST", "/services/{domain}/{service}", "Trigger light/switch/climate/script", body_schema={"entity_id": "light.living_room"}),
        ],
    },
    "philips_hue": {
        "id": "philips_hue",
        "name": "Philips Hue Bridge API v2",
        "category": "Smart Home",
        "description": "Control Hue smart bulbs, scenes, and rooms locally or remotely.",
        "base_url": "https://<bridge-ip>/clip/v2",
        "auth_type": "api_key_header",
        "auth_header": "hue-application-key",
        "auth_prefix": "",
        "docs_url": "https://developers.meethue.com/develop/hue-api-v2/api-reference/",
        "test_endpoint": {"method": "GET", "path": "/resource/light"},
        "ai_guide": "Replace <bridge-ip> in base_url with your Hue Bridge LAN IP.",
        "endpoints": [
            _endpoint("lights", "List lights", "GET", "/resource/light", "Get all Hue lights and states"),
            _endpoint("set_light", "Control light", "PUT", "/resource/light/{id}", "Turn light on/off or dim", body_schema={"on": {"on": True}, "dimming": {"brightness": 80}}),
        ],
    },
    "spotify": {
        "id": "spotify",
        "name": "Spotify Web API",
        "category": "Media",
        "description": "Currently playing track, playlists, playback control, and search.",
        "base_url": "https://api.spotify.com/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developer.spotify.com/documentation/web-api",
        "test_endpoint": {"method": "GET", "path": "/me"},
        "ai_guide": "Requires OAuth access token with user-read-playback-state / user-modify-playback-state scopes.",
        "endpoints": [
            _endpoint("playing", "Currently playing", "GET", "/me/player/currently-playing", "Check active track"),
            _endpoint("recent", "Recently played", "GET", "/me/player/recently-played", "History", ["limit"]),
            _endpoint("playlists", "User playlists", "GET", "/me/playlists", "List playlists", ["limit"]),
            _endpoint("search", "Search catalog", "GET", "/search", "Search tracks/artists", ["q", "type", "limit"]),
        ],
    },
    "strava": {
        "id": "strava",
        "name": "Strava Athlete & Activities",
        "category": "Health & Finance",
        "description": "Sync runs, rides, GPS routes, pace/speed splits, and athlete stats.",
        "base_url": "https://www.strava.com/api/v3",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.strava.com/docs/reference/",
        "test_endpoint": {"method": "GET", "path": "/athlete"},
        "ai_guide": "Average speed is returned in meters/sec (multiply by 2.23694 for mph).",
        "endpoints": [
            _endpoint("athlete", "Authenticated athlete", "GET", "/athlete", "Get athlete profile"),
            _endpoint("activities", "Recent activities", "GET", "/athlete/activities", "List runs/rides/workouts", ["per_page", "after", "before"]),
        ],
    },
    "fitbit": {
        "id": "fitbit",
        "name": "Fitbit Web API",
        "category": "Health & Finance",
        "description": "Daily steps, heart rate, sleep stages, weight, and activity logs.",
        "base_url": "https://api.fitbit.com/1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://dev.fitbit.com/build/reference/web-api/",
        "test_endpoint": {"method": "GET", "path": "/user/-/profile.json"},
        "ai_guide": "Dates use YYYY-MM-DD format; '-' references the authenticated user.",
        "endpoints": [
            _endpoint("summary", "Daily activity summary", "GET", "/user/-/activities/date/{date}.json", "Steps, distance, active minutes"),
            _endpoint("sleep", "Sleep log", "GET", "/user/-/sleep/date/{date}.json", "Sleep stages for date"),
        ],
    },
    "oura": {
        "id": "oura",
        "name": "Oura Ring v2 API",
        "category": "Health & Finance",
        "description": "Readiness scores, sleep architecture, HRV, and daily workouts.",
        "base_url": "https://api.ouraring.com/v2",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://cloud.ouraring.com/v2/docs",
        "test_endpoint": {"method": "GET", "path": "/usercollection/personal_info"},
        "ai_guide": "Pass start_date and end_date (YYYY-MM-DD) query params.",
        "endpoints": [
            _endpoint("sleep", "Daily sleep", "GET", "/usercollection/daily_sleep", "Sleep scores", ["start_date", "end_date"]),
            _endpoint("readiness", "Daily readiness", "GET", "/usercollection/daily_readiness", "Readiness scores", ["start_date", "end_date"]),
        ],
    },
    "open_meteo": {
        "id": "open_meteo",
        "name": "Open-Meteo Weather (No Key)",
        "category": "Maps & Weather",
        "description": "Free hourly & daily weather, precipitation, wind, and air quality anywhere.",
        "base_url": "https://api.open-meteo.com/v1",
        "auth_type": "none",
        "docs_url": "https://open-meteo.com/en/docs",
        "test_endpoint": {"method": "GET", "path": "/forecast?latitude=40.2338&longitude=-111.6585&current=temperature_2m,wind_speed_10m"},
        "ai_guide": "No API key needed! Pass latitude, longitude, current=temperature_2m,weather_code, and temperature_unit=fahrenheit if desired.",
        "endpoints": [
            _endpoint("forecast", "Weather forecast", "GET", "/forecast", "Current & 7-day forecast", ["latitude", "longitude", "current", "daily", "temperature_unit", "timezone"]),
        ],
    },
    "nominatim_osrm": {
        "id": "nominatim_osrm",
        "name": "OpenStreetMap Geocoding & OSRM Routing (No Key)",
        "category": "Maps & Weather",
        "description": "Free address/place geocoding and driving/walking route ETA & distance.",
        "base_url": "https://nominatim.openstreetmap.org",
        "auth_type": "none",
        "default_headers": {"User-Agent": "MiniMuse/1.0"},
        "docs_url": "https://nominatim.org/release-docs/latest/api/Search/",
        "test_endpoint": {"method": "GET", "path": "/search?q=San+Francisco&format=json&limit=1"},
        "ai_guide": "Include format=json on Nominatim. For driving routes use https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=false.",
        "endpoints": [
            _endpoint("geocode", "Search address/place", "GET", "/search", "Geocode text address to lat/lon", ["q", "format", "limit"]),
            _endpoint("reverse", "Reverse geocode coords", "GET", "/reverse", "Lat/lon to street address", ["lat", "lon", "format"]),
        ],
    },
    "tomtom_traffic": {
        "id": "tomtom_traffic",
        "name": "TomTom Traffic & Routing API",
        "category": "Maps & Weather",
        "description": "Live road traffic flow speeds, incident delays, and traffic-aware routes.",
        "base_url": "https://api.tomtom.com",
        "auth_type": "api_key_query",
        "auth_param": "key",
        "docs_url": "https://developer.tomtom.com/traffic-api/documentation/product-information/introduction",
        "ai_guide": "Traffic flow returns currentSpeed, freeFlowSpeed, and confidence; calculate congestion ratio = currentSpeed / freeFlowSpeed.",
        "endpoints": [
            _endpoint("flow", "Road traffic flow", "GET", "/traffic/services/4/flowSegmentData/absolute/10/json", "Current speed vs free-flow speed", ["point"]),
            _endpoint("route", "Traffic-aware route", "GET", "/routing/1/calculateRoute/{locations}/json", "Travel time with live traffic", ["traffic"]),
        ],
    },
    "stripe": {
        "id": "stripe",
        "name": "Stripe API",
        "category": "Health & Finance",
        "description": "Read balances, charges, customers, subscriptions, and invoices.",
        "base_url": "https://api.stripe.com/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://docs.stripe.com/api",
        "test_endpoint": {"method": "GET", "path": "/balance"},
        "ai_guide": "Prefer restricted read-only keys (rk_live_...) when inspecting financial telemetry.",
        "endpoints": [
            _endpoint("balance", "Get Stripe balance", "GET", "/balance", "Current available and pending balances"),
            _endpoint("charges", "Recent charges", "GET", "/charges", "List recent charges", ["limit"]),
            _endpoint("customers", "List customers", "GET", "/customers", "Search or list customers", ["limit", "email"]),
        ],
    },
    "ynab": {
        "id": "ynab",
        "name": "YNAB Budget API",
        "category": "Health & Finance",
        "description": "Inspect budgets, account balances, categories, and log transactions.",
        "base_url": "https://api.ynab.com/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://api.ynab.com/",
        "test_endpoint": {"method": "GET", "path": "/budgets"},
        "ai_guide": "YNAB amounts use milliunits (1000 = $1.00).",
        "endpoints": [
            _endpoint("budgets", "List budgets", "GET", "/budgets", "List all budgets"),
            _endpoint("transactions", "List transactions", "GET", "/budgets/{budget_id}/transactions", "Read transactions", ["since_date"]),
        ],
    },
    "supabase": {
        "id": "supabase",
        "name": "Supabase PostgREST API",
        "category": "Developer",
        "description": "Query and mutate rows in any Supabase Postgres table over REST.",
        "base_url": "https://<project-ref>.supabase.co/rest/v1",
        "auth_type": "api_key_header",
        "auth_header": "apikey",
        "auth_prefix": "",
        "docs_url": "https://supabase.com/docs/guides/api",
        "ai_guide": "Pass both apikey header and Authorization: Bearer <key> if RLS requires JWT; OpenAPI spec lives at root /.",
        "endpoints": [
            _endpoint("openapi", "Discover tables schema", "GET", "/", "Fetch PostgREST OpenAPI spec of all tables"),
        ],
    },
    "cloudflare": {
        "id": "cloudflare",
        "name": "Cloudflare API v4",
        "category": "Developer",
        "description": "Manage DNS records, Workers, KV, R2, and tunnel statuses.",
        "base_url": "https://api.cloudflare.com/client/v4",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://developers.cloudflare.com/api/",
        "test_endpoint": {"method": "GET", "path": "/user/tokens/verify"},
        "ai_guide": "Verify token with /user/tokens/verify; manage DNS under /zones/{zone_id}/dns_records.",
        "endpoints": [
            _endpoint("zones", "List DNS zones", "GET", "/zones", "List domains"),
            _endpoint("dns", "List DNS records", "GET", "/zones/{zone_id}/dns_records", "List zone DNS records"),
        ],
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter API",
        "category": "AI Models",
        "description": "Check model catalogue, credits, generation stats, and completions.",
        "base_url": "https://openrouter.ai/api/v1",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "https://openrouter.ai/docs/api-reference",
        "test_endpoint": {"method": "GET", "path": "/auth/key"},
        "ai_guide": "GET /models works publicly; GET /auth/key checks remaining credits/limits.",
        "endpoints": [
            _endpoint("models", "List available models", "GET", "/models", "List 300+ models & pricing"),
            _endpoint("credits", "Check key & credits", "GET", "/auth/key", "Check remaining OpenRouter allowance"),
        ],
    },
    "webhook_generic": {
        "id": "webhook_generic",
        "name": "Custom REST API / Webhook (n8n, Make, Zapier, Any)",
        "category": "Automation & Data",
        "description": "Connect any arbitrary HTTP/REST/GraphQL endpoint or automation webhook. AI can discover OpenAPI specs or inspect and save endpoints automatically.",
        "base_url": "https://example.com/api",
        "auth_type": "bearer",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "docs_url": "",
        "ai_guide": "First try discover_openapi on base_url, or call GET root/docs to inspect structure, then save_endpoint as you learn how to read & write this API.",
        "endpoints": [],
    },
}


_POPULAR_PRESET_ORDER: tuple[str, ...] = (
    "gmail",
    "google_calendar",
    "github",
    "notion",
    "slack",
    "todoist",
    "spotify",
    "home_assistant",
    "open_meteo",
    "google_maps_places",
    "google_tasks",
    "google_drive",
    "linear",
    "discord",
    "telegram",
    "stripe",
    "microsoft_graph",
    "openrouter",
)


GOOGLE_OAUTH_PRESETS: frozenset[str] = frozenset({
    "gmail",
    "google_calendar",
    "google_tasks",
    "google_drive",
})

PRESET_KEY_SETUP: dict[str, tuple[str, str]] = {
    "google_maps_places": (
        "https://console.cloud.google.com/google/maps-apis/credentials",
        "Create a Google Maps API key (enable Places API & Routes API) and paste it below.",
    ),
    "microsoft_graph": (
        "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        "Paste an OAuth Bearer token or Microsoft Graph token.",
    ),
    "slack": (
        "https://api.slack.com/apps",
        "Create a Slack App → OAuth & Permissions → copy the Bot User OAuth Token (xoxb-...).",
    ),
    "discord": (
        "https://discord.com/developers/applications",
        "Select your Discord App → Bot → Reset/Copy Bot Token.",
    ),
    "telegram": (
        "https://t.me/BotFather",
        "Message @BotFather /newbot on Telegram and paste your bot token into vault.",
    ),
    "twilio": (
        "https://console.twilio.com/",
        "Copy AccountSID:AuthToken (with colon) into Secret below.",
    ),
    "github": (
        "https://github.com/settings/tokens/new?description=MiniMuse&scopes=repo,read:user,notifications",
        "Generate a GitHub Personal Access Token (pre-filled scopes) and paste it below.",
    ),
    "gitlab": (
        "https://gitlab.com/-/user_settings/personal_access_tokens",
        "Create a GitLab Personal Access Token with api/read_user scope.",
    ),
    "linear": (
        "https://linear.app/settings/api",
        "Create a Linear Personal API key and paste it below.",
    ),
    "jira": (
        "https://id.atlassian.com/manage-profile/security/api-tokens",
        "Create an Atlassian API token and enter email:token (and replace your-domain in Base URL).",
    ),
    "notion": (
        "https://www.notion.so/profile/integrations",
        "Create an Internal Integration secret (ntn_... / secret_...) and share pages with it.",
    ),
    "todoist": (
        "https://app.todoist.com/app/settings/integrations/developer",
        "Copy your Todoist API token from Developer settings.",
    ),
    "trello": (
        "https://trello.com/power-ups/admin",
        "Generate a Trello Power-Up API key.",
    ),
    "asana": (
        "https://app.asana.com/0/my-apps",
        "Create a Personal Access Token in Asana Developer Console.",
    ),
    "airtable": (
        "https://airtable.com/create/tokens",
        "Create an Airtable Personal Access Token.",
    ),
    "clickup": (
        "https://app.clickup.com/settings/apps",
        "Generate your ClickUp Personal API Token (pk_...).",
    ),
    "spotify": (
        "https://developer.spotify.com/dashboard",
        "Create a Spotify App or paste a Spotify Web API OAuth token.",
    ),
    "strava": (
        "https://www.strava.com/settings/api",
        "Create a Strava API app and paste your access token.",
    ),
    "fitbit": (
        "https://dev.fitbit.com/apps",
        "Register a Fitbit OAuth 2.0 app and paste your Bearer token.",
    ),
    "oura": (
        "https://cloud.ouraring.com/personal-access-tokens",
        "Create an Oura Personal Access Token.",
    ),
    "tomtom_traffic": (
        "https://developer.tomtom.com/user/me/apps",
        "Copy your TomTom Traffic & Routing API key.",
    ),
    "stripe": (
        "https://dashboard.stripe.com/apikeys",
        "Copy a Stripe Restricted Key (rk_live_... recommended for read-only).",
    ),
    "ynab": (
        "https://app.ynab.com/settings/developer",
        "Create a YNAB Personal Access Token.",
    ),
    "cloudflare": (
        "https://dash.cloudflare.com/profile/api-tokens",
        "Create a scoped Cloudflare API Token.",
    ),
    "openrouter": (
        "https://openrouter.ai/keys",
        "Create or copy an OpenRouter API key (sk-or-...).",
    ),
}


def preset_list() -> list[dict[str, Any]]:
    """Return all standard presets with popular ones on top and rarer ones alphabetically."""
    rank = {pid: i for i, pid in enumerate(_POPULAR_PRESET_ORDER)}
    popular: list[dict[str, Any]] = []
    rare: list[dict[str, Any]] = []
    for pid, preset in STANDARD_API_PRESETS.items():
        row = dict(preset)
        key_setup = PRESET_KEY_SETUP.get(pid)
        if key_setup:
            row["key_url"] = key_setup[0]
            row["key_hint"] = key_setup[1]
        elif pid in GOOGLE_OAUTH_PRESETS:
            row["key_url"] = "https://console.cloud.google.com/apis/credentials"
            row["key_hint"] = "Auto-authorized when you sign in with Google above (no separate key needed once Google is connected)."
            row["uses_google_oauth"] = True
        else:
            row["key_url"] = ""
            row["key_hint"] = ""
        if pid in rank:
            row["popular"] = True
            popular.append(row)
        else:
            row["popular"] = False
            rare.append(row)
    popular.sort(key=lambda r: rank.get(str(r.get("id") or ""), 999))
    rare.sort(key=lambda r: str(r.get("name") or r.get("id") or "").lower())
    return popular + rare


def parse_openapi_spec(spec: dict[str, Any], max_endpoints: int = MAX_OPENAPI_ENDPOINTS) -> dict[str, Any]:
    """Extract base URL, title, description, auth hints, and endpoints from OpenAPI/Swagger JSON."""
    raw_info = spec.get("info")
    info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
    title = str(info.get("title") or "").strip()
    description = str(info.get("description") or "").strip()[:500]
    servers = spec.get("servers")
    base_url = ""
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        base_url = str(servers[0].get("url") or "").strip()
    elif spec.get("host"):
        scheme = "https"
        schemes = spec.get("schemes")
        if isinstance(schemes, list) and schemes:
            scheme = str(schemes[0])
        base_url = f"{scheme}://{spec['host']}{spec.get('basePath') or ''}"

    paths = spec.get("paths")
    endpoints: list[dict[str, Any]] = []
    if isinstance(paths, dict):
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                m_up = str(method).upper()
                if m_up not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    continue
                if not isinstance(op, dict):
                    continue
                op_id = str(op.get("operationId") or f"{m_up.lower()}_{_slug(path)}")
                summary = str(op.get("summary") or op.get("description") or path).strip()[:200]
                qparams: list[str] = []
                for param in op.get("parameters") or []:
                    if isinstance(param, dict) and param.get("in") == "query" and param.get("name"):
                        qparams.append(str(param["name"]))
                body_schema: dict[str, Any] = {}
                rb = op.get("requestBody")
                if isinstance(rb, dict):
                    content = rb.get("content")
                    if isinstance(content, dict):
                        app_json = content.get("application/json")
                        if isinstance(app_json, dict) and isinstance(app_json.get("schema"), dict):
                            props = app_json["schema"].get("properties")
                            if isinstance(props, dict):
                                body_schema = {str(k): str(v.get("type") or "any") if isinstance(v, dict) else "any" for k, v in list(props.items())[:12]}
                endpoints.append(_endpoint(op_id, summary[:60] or op_id, m_up, str(path), summary, qparams[:10], body_schema))
                if len(endpoints) >= max_endpoints:
                    break
            if len(endpoints) >= max_endpoints:
                break
    return {
        "title": title,
        "description": description,
        "base_url": base_url.rstrip("/"),
        "endpoints": endpoints,
    }


@dataclass
class APIConnection:
    id: str
    name: str
    preset_id: str = "custom"
    category: str = "Automation & Data"
    base_url: str = ""
    auth_type: str = "bearer"  # none | bearer | api_key_header | api_key_query | basic | oauth2
    auth_header: str = "Authorization"
    auth_param: str = "api_key"
    auth_prefix: str = "Bearer "
    vault_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    ai_guide: str = ""
    learned_notes: str = ""
    docs_url: str = ""
    test_endpoint: dict[str, str] = field(default_factory=dict)
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_tested_at: str = ""
    last_status: str = ""
    last_error: str = ""

    def to_dict(self, vault: CredentialVault | None = None) -> dict[str, Any]:
        has_secret = False
        if vault is not None:
            if self.vault_key and vault.get(self.vault_key):
                has_secret = True
            elif self.preset_id in GOOGLE_OAUTH_PRESETS and (
                vault.get("GOOGLE_OAUTH_ACCESS_TOKEN") or vault.get("GOOGLE_OAUTH_REFRESH_TOKEN")
            ):
                has_secret = True
        return {
            "id": self.id,
            "name": self.name,
            "preset_id": self.preset_id,
            "category": self.category,
            "base_url": self.base_url,
            "auth_type": self.auth_type,
            "auth_header": self.auth_header,
            "auth_param": self.auth_param,
            "auth_prefix": self.auth_prefix,
            "vault_key": self.vault_key,
            "has_secret": has_secret,
            "headers": dict(self.headers),
            "ai_guide": self.ai_guide,
            "learned_notes": self.learned_notes,
            "docs_url": self.docs_url,
            "test_endpoint": dict(self.test_endpoint),
            "endpoints": list(self.endpoints),
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_tested_at": self.last_tested_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> APIConnection:
        return cls(
            id=str(data.get("id") or f"api_{uuid.uuid4().hex[:8]}"),
            name=str(data.get("name") or "Custom API").strip(),
            preset_id=str(data.get("preset_id") or "custom"),
            category=str(data.get("category") or "Automation & Data"),
            base_url=str(data.get("base_url") or "").rstrip("/"),
            auth_type=str(data.get("auth_type") or "bearer"),
            auth_header=str(data.get("auth_header") or "Authorization"),
            auth_param=str(data.get("auth_param") or "api_key"),
            auth_prefix=str(data.get("auth_prefix") if data.get("auth_prefix") is not None else "Bearer "),
            vault_key=str(data.get("vault_key") or ""),
            headers={str(k): str(v) for k, v in (data.get("headers") or {}).items() if k},
            ai_guide=str(data.get("ai_guide") or ""),
            learned_notes=str(data.get("learned_notes") or ""),
            docs_url=str(data.get("docs_url") or ""),
            test_endpoint={str(k): str(v) for k, v in (data.get("test_endpoint") or {}).items()},
            endpoints=list(data.get("endpoints") or []),
            enabled=bool(data.get("enabled", True)),
            created_at=str(data.get("created_at") or _now_iso()),
            updated_at=str(data.get("updated_at") or _now_iso()),
            last_tested_at=str(data.get("last_tested_at") or ""),
            last_status=str(data.get("last_status") or ""),
            last_error=str(data.get("last_error") or ""),
        )


class APIRegistry:
    """Persistent registry of user-configured standard and custom REST/GraphQL APIs."""

    FILE_NAME = "api_integrations.json"

    def __init__(self, data_dir: Path, vault: CredentialVault | None = None):
        self.data_dir = Path(data_dir)
        self.vault = vault
        self.path = self.data_dir / self.FILE_NAME
        self._items: dict[str, APIConnection] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            rows = raw.get("apis") if isinstance(raw, dict) else raw
            if not isinstance(rows, list):
                return
            for row in rows:
                if isinstance(row, dict):
                    conn = APIConnection.from_dict(row)
                    self._items[conn.id] = conn
        except (OSError, ValueError) as exc:
            logger.warning("api_integrations.json unreadable: {}", exc)

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "apis": [c.to_dict(vault=None) for c in self._items.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        keep_private(self.path)

    def list(self) -> list[dict[str, Any]]:
        return [c.to_dict(vault=self.vault) for c in self._items.values()]

    def get(self, identifier: str) -> APIConnection | None:
        ident = (identifier or "").strip()
        if not ident:
            return None
        if ident in self._items:
            return self._items[ident]
        lowered = ident.lower()
        for c in self._items.values():
            if c.name.lower() == lowered or c.preset_id.lower() == lowered:
                return c
        return None

    def upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_id = str(payload.get("id") or "").strip()
        preset_id = str(payload.get("preset_id") or "").strip() or "custom"
        preset = STANDARD_API_PRESETS.get(preset_id)

        existing = self._items.get(api_id) if api_id else None
        if existing is None:
            new_id = api_id or f"{_slug(preset_id)}_{uuid.uuid4().hex[:6]}"
            base_url_raw = str(payload.get("base_url") or (preset["base_url"] if preset else "")).strip()
            base_url = validate_api_url(base_url_raw) if base_url_raw else ""
            vault_key = str(payload.get("vault_key") or _vault_key_for(new_id)).strip()
            name = str(payload.get("name") or (preset["name"] if preset else "Custom API")).strip()
            raw_eps = payload.get("endpoints") if payload.get("endpoints") is not None else (preset.get("endpoints") if preset else None)
            endpoints_list = [dict(ep) for ep in raw_eps if isinstance(ep, dict)] if isinstance(raw_eps, list) else []
            conn = APIConnection(
                id=new_id,
                name=name,
                preset_id=preset_id,
                category=str(payload.get("category") or (preset.get("category") if preset else "Automation & Data")),
                base_url=base_url,
                auth_type=str(payload.get("auth_type") or (preset.get("auth_type") if preset else "bearer")),
                auth_header=str(payload.get("auth_header") or (preset.get("auth_header") if preset else "Authorization")),
                auth_param=str(payload.get("auth_param") or (preset.get("auth_param") if preset else "api_key")),
                auth_prefix=str(
                    payload["auth_prefix"]
                    if "auth_prefix" in payload and payload["auth_prefix"] is not None
                    else (preset.get("auth_prefix", "Bearer ") if preset else "Bearer ")
                ),
                vault_key=vault_key,
                headers={
                    **(preset.get("default_headers") or {} if preset else {}),
                    **({str(k): str(v) for k, v in (payload.get("headers") or {}).items()} if isinstance(payload.get("headers"), dict) else {}),
                },
                ai_guide=str(payload.get("ai_guide") or (preset.get("ai_guide") if preset else "")),
                learned_notes=str(payload.get("learned_notes") or ""),
                docs_url=str(payload.get("docs_url") or (preset.get("docs_url") if preset else "") or ""),
                test_endpoint=dict(payload.get("test_endpoint") or (preset.get("test_endpoint") if preset else None) or {}),
                endpoints=endpoints_list,
                enabled=bool(payload.get("enabled", True)),
            )
        else:
            conn = existing
            if payload.get("name"):
                conn.name = str(payload["name"]).strip()
            if "base_url" in payload and payload["base_url"] is not None:
                raw_u = str(payload["base_url"]).strip()
                conn.base_url = validate_api_url(raw_u) if raw_u else ""
            for fld in ("category", "auth_type", "auth_header", "auth_param", "ai_guide", "learned_notes", "docs_url"):
                if fld in payload and payload[fld] is not None:
                    setattr(conn, fld, str(payload[fld]))
            if "auth_prefix" in payload and payload["auth_prefix"] is not None:
                conn.auth_prefix = str(payload["auth_prefix"])
            if isinstance(payload.get("headers"), dict):
                conn.headers = {str(k): str(v) for k, v in payload["headers"].items() if k}
            if isinstance(payload.get("endpoints"), list):
                conn.endpoints = list(payload["endpoints"])
            if isinstance(payload.get("test_endpoint"), dict):
                conn.test_endpoint = {str(k): str(v) for k, v in payload["test_endpoint"].items()}
            if "enabled" in payload and payload["enabled"] is not None:
                conn.enabled = bool(payload["enabled"])
            if not conn.vault_key:
                conn.vault_key = _vault_key_for(conn.id)
            conn.updated_at = _now_iso()

        secret = payload.get("secret")
        if secret is not None and str(secret).strip() and self.vault is not None:
            self.vault.set(conn.vault_key, str(secret).strip())
        self._items[conn.id] = conn
        self._save()
        return conn.to_dict(vault=self.vault)

    def remove(self, api_id: str) -> bool:
        conn = self.get(api_id)
        if conn is None:
            return False
        if self.vault is not None and conn.vault_key:
            try:
                self.vault.delete(conn.vault_key)
            except Exception:
                pass
        self._items.pop(conn.id, None)
        self._save()
        return True

    def save_endpoint(
        self,
        api_id: str,
        endpoint_data: dict[str, Any],
        learned_notes: str | None = None,
    ) -> dict[str, Any]:
        conn = self.get(api_id)
        if conn is None:
            raise KeyError(f"Unknown API connection: {api_id}")
        ep = _endpoint(
            str(endpoint_data.get("id") or f"{str(endpoint_data.get('method') or 'GET').lower()}_{_slug(str(endpoint_data.get('path') or 'ep'))}"),
            str(endpoint_data.get("name") or endpoint_data.get("path") or "Endpoint"),
            str(endpoint_data.get("method") or "GET"),
            str(endpoint_data.get("path") or "/"),
            str(endpoint_data.get("description") or ""),
            list(endpoint_data.get("query_params") or []),
            dict(endpoint_data.get("body_schema") or {}),
        )
        replaced = False
        for i, existing in enumerate(conn.endpoints):
            if existing.get("id") == ep["id"] or (
                str(existing.get("method", "")).upper() == ep["method"] and existing.get("path") == ep["path"]
            ):
                conn.endpoints[i] = ep
                replaced = True
                break
        if not replaced:
            conn.endpoints.append(ep)
        if learned_notes is not None and learned_notes.strip():
            conn.learned_notes = learned_notes.strip()
        conn.updated_at = _now_iso()
        self._save()
        return conn.to_dict(vault=self.vault)

    def _resolve_vault(self, value: Any) -> Any:
        if isinstance(value, str):
            def _sub(m: re.Match[str]) -> str:
                key = m.group(1).strip()
                val = self.vault.get(key) if self.vault is not None else None
                return str(val or "")

            return re.sub(r"\{\{vault:([^}]+)\}\}", _sub, value)
        if isinstance(value, dict):
            return {k: self._resolve_vault(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve_vault(v) for v in value]
        return value

    def _apply_auth(
        self,
        conn: APIConnection | None,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> None:
        if conn is None or conn.auth_type == "none":
            return
        secret = (self.vault.get(conn.vault_key) if self.vault is not None and conn.vault_key else "") or ""
        if not secret and self.vault is not None and conn.preset_id in GOOGLE_OAUTH_PRESETS:
            try:
                from nanomuse.connectors.google_oauth import refresh_access_token_sync

                secret = refresh_access_token_sync(self.vault)
            except Exception:  # noqa: BLE001
                secret = str(self.vault.get("GOOGLE_OAUTH_ACCESS_TOKEN") or "").strip()
        if not secret:
            return
        if conn.auth_type in ("bearer", "oauth2"):
            headers.setdefault(conn.auth_header or "Authorization", f"{conn.auth_prefix}{secret}")
        elif conn.auth_type == "api_key_header":
            headers.setdefault(conn.auth_header or "X-API-Key", f"{conn.auth_prefix}{secret}")
        elif conn.auth_type == "api_key_query":
            params.setdefault(conn.auth_param or "api_key", secret)
        elif conn.auth_type == "basic":
            import base64

            raw_token = secret
            if ":" in secret:
                raw_token = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            headers.setdefault("Authorization", f"Basic {raw_token}")

    async def execute(
        self,
        *,
        api_id: str | None = None,
        url: str | None = None,
        method: str = "GET",
        path: str = "",
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        body: Any = None,
        timeout_s: float = 20.0,
    ) -> dict[str, Any]:
        conn = self.get(api_id) if api_id else None
        if api_id and conn is None:
            raise KeyError(f"Unknown API connection: {api_id}")

        target_url = (url or "").strip()
        if not target_url:
            if conn is None or not conn.base_url:
                raise ValueError("Provide either `url` or a configured `api_id` with a `base_url`.")
            p = (path or "").strip()
            if p.startswith(("http://", "https://")):
                target_url = p
            elif p:
                target_url = f"{conn.base_url.rstrip('/')}/{p.lstrip('/')}"
            else:
                target_url = conn.base_url

        resolved_url = validate_api_url(str(self._resolve_vault(target_url)))
        merged_headers: dict[str, str] = {}
        if conn is not None:
            for k, v in conn.headers.items():
                merged_headers[k] = str(self._resolve_vault(v))
        if isinstance(headers, dict):
            for k, v in headers.items():
                merged_headers[str(k)] = str(self._resolve_vault(str(v)))

        merged_params: dict[str, Any] = {}
        if isinstance(params, dict):
            merged_params = dict(self._resolve_vault(params))

        self._apply_auth(conn, merged_headers, merged_params)
        resolved_body = self._resolve_vault(body) if body is not None else None
        method_up = (method or "GET").upper()
        if method_up not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            raise ValueError(f"Unsupported HTTP method: {method_up}")

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            req_kwargs: dict[str, Any] = {
                "method": method_up,
                "url": resolved_url,
                "headers": merged_headers,
                "params": merged_params or None,
            }
            if resolved_body is not None and method_up in ("POST", "PUT", "PATCH", "DELETE"):
                if isinstance(resolved_body, (dict, list)):
                    req_kwargs["json"] = resolved_body
                else:
                    req_kwargs["content"] = str(resolved_body)
            resp = await client.request(**req_kwargs)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        text = resp.text or ""
        parsed: Any = None
        try:
            parsed = resp.json()
        except ValueError:
            parsed = None

        return {
            "ok": resp.is_success,
            "status_code": resp.status_code,
            "ms": elapsed_ms,
            "url": str(resp.url),
            "json": parsed,
            "text": text[:MAX_RESPONSE_CHARS] if parsed is None else "",
            "truncated": len(text) > MAX_RESPONSE_CHARS,
        }

    async def test(self, api_id: str) -> dict[str, Any]:
        conn = self.get(api_id)
        if conn is None:
            raise KeyError(f"Unknown API connection: {api_id}")
        ep = conn.test_endpoint or {"method": "GET", "path": "/"}
        try:
            res = await self.execute(
                api_id=conn.id,
                method=ep.get("method", "GET"),
                path=ep.get("path", "/"),
                timeout_s=12.0,
            )
            conn.last_tested_at = _now_iso()
            conn.last_status = "ok" if res["ok"] else f"http_{res['status_code']}"
            conn.last_error = "" if res["ok"] else f"HTTP {res['status_code']}"
            self._save()
            return {"ok": res["ok"], "status_code": res["status_code"], "ms": res["ms"], "error": conn.last_error}
        except Exception as exc:  # noqa: BLE001
            conn.last_tested_at = _now_iso()
            conn.last_status = "error"
            conn.last_error = str(exc)[:240]
            self._save()
            return {"ok": False, "error": conn.last_error}

    async def discover_openapi(self, api_id: str | None = None, spec_url: str = "") -> dict[str, Any]:
        """Probe OpenAPI/Swagger endpoints for an API and save discovered operations."""
        conn = self.get(api_id) if api_id else None
        candidates: list[str] = []
        if spec_url.strip():
            candidates.append(spec_url.strip())
        elif conn and conn.base_url:
            b = conn.base_url.rstrip("/")
            candidates.extend([
                f"{b}/openapi.json",
                f"{b}/swagger.json",
                f"{b}/v3/api-docs",
                f"{b}/.well-known/openapi.json",
                b,
            ])
        else:
            raise ValueError("Provide `spec_url` or an `api_id` with `base_url`.")

        for cand in candidates:
            try:
                res = await self.execute(api_id=conn.id if conn else None, url=cand, method="GET", timeout_s=10.0)
                if res["ok"] and isinstance(res["json"], dict) and ("paths" in res["json"] or "openapi" in res["json"] or "swagger" in res["json"]):
                    parsed = parse_openapi_spec(res["json"])
                    if conn is not None and parsed["endpoints"]:
                        conn.endpoints = parsed["endpoints"]
                        if parsed["description"] and not conn.learned_notes:
                            conn.learned_notes = parsed["description"]
                        conn.updated_at = _now_iso()
                        self._save()
                    return {"ok": True, "spec_url": cand, "endpoints_count": len(parsed["endpoints"]), **parsed}
            except Exception:
                continue
        return {"ok": False, "error": "No OpenAPI/Swagger JSON found at common spec paths."}
