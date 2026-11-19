"""Turn cost estimates and the activity ledger for the usage page.

Every turn the runtime makes is recorded in ``<data_dir>/usage.jsonl``: the model, its
tokens (input, cached, reasoning, output), what the turn cost at the provider's own
rate card, and what the turn did (tool calls by kind, pictures and clips written or
read). The chat shows one line per DeepSeek turn with what it spent; the Usage screen
aggregates the ledger locally — no account, no sign-in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# USD per 1M tokens: input on a cache miss, input on a cache hit, output. Peak hours
# double every number (off-peak is everything else, weekends included).
_DEEPSEEK_RATES: dict[str, dict[str, float]] = {
    "deepseek-flash": {"in": 0.15, "hit": 0.003, "out": 0.60},
    "deepseek-v4-pro": {"in": 0.66, "hit": 0.022, "out": 1.98},
}

# The other cloud models the rate cards of their vendors publish (2026-11); a cache
# miss rate stands in for the hit when a vendor does not bill cached input apart.
# Local Ollama models are absent on purpose: they cost nothing.
_OTHER_RATES: dict[str, dict[str, float]] = {
    "gemini-3.1-flash-lite": {"in": 0.10, "hit": 0.025, "out": 0.40},
    "gemini-3-flash-preview": {"in": 0.35, "hit": 0.09, "out": 1.20},
    "gemini-3.1-pro-preview": {"in": 1.25, "hit": 0.31, "out": 10.00},
    "glm-5.3": {"in": 0.60, "hit": 0.11, "out": 2.20},
    "glm-4.6v": {"in": 0.60, "hit": 0.11, "out": 1.80},
    "kimi-k2.6": {"in": 0.60, "hit": 0.11, "out": 2.50},
    "kimi-k3": {"in": 0.60, "hit": 0.11, "out": 2.50},
    "qwen3.8-27b": {"in": 0.30, "hit": 0.08, "out": 0.80},
    "deepseek-v4.1-flash": {"in": 0.15, "hit": 0.003, "out": 0.60},
}

_RATES: dict[str, dict[str, float]] = {**_OTHER_RATES, **_DEEPSEEK_RATES}

# Config may price a model the cards do not know: [llm] input_usd_per_million /
# output_usd_per_million in config.toml, read once at startup (see service).
_USER_RATES: dict[str, dict[str, float]] = {}


def set_user_rates(model: str, input_usd_per_million: float, output_usd_per_million: float) -> None:
    """A rate card from the owner's config for a model the catalogue does not price."""
    if input_usd_per_million <= 0 and output_usd_per_million <= 0:
        return
    _USER_RATES[model] = {
        "in": input_usd_per_million,
        "hit": input_usd_per_million,
        "out": output_usd_per_million,
    }


def rates_for(model: str) -> dict[str, float] | None:
    return _RATES.get(model) or _USER_RATES.get(model)


def known_cost_model(model: str) -> bool:
    """Whether a turn on this model gets a dollar estimate (local models: never)."""
    return rates_for(model) is not None


def _peak_hour(now: datetime) -> bool:
    now = now.astimezone(UTC)
    if now.weekday() >= 5:  # Saturday, Sunday: off-peak all day
        return False
    hour = now.hour + now.minute / 60.0
    return (1.0 <= hour < 4.0) or (6.0 <= hour < 10.0)


def estimate_turn_cost(
    model: str,
    prompt_tokens: int,
    cache_tokens: int,
    completion_tokens: int,
    now: datetime | None = None,
) -> float:
    """What a turn costs in USD, priced off the model's rate card. Unknown models cost
    zero here on purpose: an estimate must not be presented as one."""
    rates = rates_for(model)
    if rates is None or prompt_tokens <= 0:
        return 0.0
    factor = 2.0 if (_DEEPSEEK_RATES.get(model) and _peak_hour(now or datetime.now(UTC))) else 1.0
    cache = min(cache_tokens, prompt_tokens)
    miss = max(prompt_tokens - cache, 0)
    raw = miss * rates["in"] + cache * rates["hit"] + completion_tokens * rates["out"]
    return raw * factor / 1_000_000


def usage_note(model: str, trace: dict[str, Any], now: datetime | None = None) -> str:
    """The one chat line after a DeepSeek turn: tokens in, tokens cached, cost."""
    prompt = int(trace.get("prompt_tokens") or 0)
    cache = min(int(trace.get("cache_tokens") or 0), prompt)
    out = int(trace.get("completion_tokens") or 0)
    cost = estimate_turn_cost(model, prompt, cache, out, now=now)
    cached = f" ({cache / 1000:.1f}k cached)" if cache else ""
    return f"{model}: {prompt / 1000:.1f}k prompt{cached}, {out} reply, ~${cost:.4f} this turn"


# ---------------------------------------------------------------------- activities

# The usage page answers "what was it doing" in the words of the tools: one turn's
# tool calls grouped into the activities a person recognises. A tool may land in two
# activities when it both reads and writes (the files tool): the action decides.
_TOOL_ACTIVITIES: dict[str, str] = {
    # reading & browsing: looking things up on the internet and in the world
    "web_search": "web_browsing",
    "web_fetch": "web_browsing",
    "browser": "web_browsing",
    "read_emails": "email_calendar",
    "send_email": "email_calendar",
    "calendar": "email_calendar",
    "contacts": "email_calendar",
    "deep_memory": "reading",
    "recall": "reading",
    "computer_look": "device_control",
    "phone_look": "device_control",
    "phone_screen": "device_control",
    # commands & device control: the shell, the PC's hands, the phone's hands doing things
    "shell": "commands",
    "python_execute": "commands",
    "pc_control": "device_control",
    "computer_act": "device_control",
    "computer_task": "device_control",
    "phone_act": "device_control",
    "phone_task": "device_control",
    "phone_tap": "device_control",
    "device_shell": "commands",
    # APIs: connectors, the hub's other devices
    "external_connectors": "apis",
    "api_connector": "apis",
    "app_builder": "app_built",
    "phone_bridge": "apis",
    "mcp": "apis",
    # coding: the coding agents
    "coding": "coding",
    # live calls
    "live_call": "live_calls",
    # personal tracking: local tracker and memory tools
    "ofa_tracker": "tracking",
    "remember": "tracking",
    "forget": "tracking",
    "goals": "tracking",
    "reminders": "tracking",
    "triggers": "tracking",
    "skills": "tracking",
    "devices": "device_control",
    "device_files": "files",
    "device_get": "files",
    "device_put": "files",
    # files: read or write is decided per call where the action is known
    "files": "files",
}

ACTIVITIES = (
    "reading",
    "web_browsing",
    "email_calendar",
    "live_calls",
    "device_control",
    "files_read",
    "files_written",
    "commands",
    "apis",
    "coding",
    "tracking",
    "app_built",
    "image_generation",
    "image_editing",
    "video_generation",
    "video_editing",
    "photo_analysis",
    "video_analysis",
)


def tool_activity_counts(
    tool_names: list[str], tool_actions: dict[str, str] | None = None
) -> dict[str, int]:
    """A turn's tool calls grouped into activities, the way the Usage screen shows them.

    ``tool_actions`` maps a call to ``read`` or ``write`` where the caller knows it
    (the files tool records its action); the rest follow the table above.
    """
    counts = {a: 0 for a in ACTIVITIES}
    actions = tool_actions or {}
    for i, name in enumerate(tool_names or []):
        action = actions.get(f"{i}:{name}") or actions.get(name) or ""
        activity = _TOOL_ACTIVITIES.get(name)
        if name == "files":
            activity = "files_written" if action == "write" else "files_read"
        if activity:
            counts[activity] += 1
    return counts
