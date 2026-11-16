"""Smart multi-API routing, model recommendations, and adaptive usage/quota tracking."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from nanomuse.config import LLMSettings
from nanomuse.llm.base import BaseLLM, DeltaCallback
from nanomuse.schema import LLMResponse, Message

if TYPE_CHECKING:
    from nanomuse.vault.vault import CredentialVault

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated Model Catalog with Cost, Tier, Company & Task Details
# ---------------------------------------------------------------------------

MODEL_CATALOG_INFO: list[dict[str, Any]] = [
    {
        "id": "deepseek/deepseek-v4.1-flash-1017",
        "aliases": ["deepseek-v4.1-flash-1017", "deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash", "deepseek-flash"],
        "label": "DeepSeek V4.1 Flash (1017)",
        "company": "DeepSeek",
        "provider_ids": ["deepseek", "openrouter", "bailian"],
        "use_tier": "normal",
        "use_tier_label": "Normal Use (Best Overall Default)",
        "recommended_for": "Best all-around daily model — fast responses, vision/screenshot reading, tool calling, and coding at a fraction of typical flagship cost.",
        "best_tasks": ["Daily Chat", "Vision & Screen Reading", "Tool Calling", "Coding & Automation"],
        "input_cost_per_m": 0.14,
        "output_cost_per_m": 0.28,
        "cost_summary": "$0.14 / 1M input · $0.28 / 1M output (~$0.50–$2/mo typical use)",
        "quota_kind": "credits",
        "reset_schedule": "never",
        "free_tier_info": "Uses prepaid DeepSeek credits (no expiry) or OpenRouter balance; supports automatic failover to OpenRouter or free Gemini when credits run out.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 128000,
        "is_default_for": ["chat", "hands"],
        "suggested_rank": 1,
    },
    {
        "id": "deepseek/deepseek-v4.1-pro-1017",
        "aliases": ["deepseek-v4.1-pro-1017", "deepseek-v4-pro", "deepseek/deepseek-v4-pro", "deepseek-reasoner"],
        "label": "DeepSeek V4.1 Pro (1017)",
        "company": "DeepSeek",
        "provider_ids": ["deepseek", "openrouter"],
        "use_tier": "heavy",
        "use_tier_label": "Heavy Use (Deep Reasoning & Complex Code)",
        "recommended_for": "Deep multi-step reasoning, complex math, architecture planning, and hard debugging where accuracy matters more than speed.",
        "best_tasks": ["Deep Reasoning", "Complex Coding", "Research & Analysis", "Math & Logic"],
        "input_cost_per_m": 0.55,
        "output_cost_per_m": 2.19,
        "cost_summary": "$0.55 / 1M input · $2.19 / 1M output (~$2–$8/mo heavy use)",
        "quota_kind": "credits",
        "reset_schedule": "never",
        "free_tier_info": "Token-based billing from DeepSeek or OpenRouter prepaid balance; off-peak discounts available on DeepSeek API.",
        "capabilities": ["chat", "tools", "reasoning"],
        "context_window": 128000,
        "is_default_for": ["heavy"],
        "suggested_rank": 2,
    },
    {
        "id": "google/gemini-2.5-flash",
        "aliases": ["gemini-2.5-flash", "gemini-3.8-flash", "google/gemini-3.8-flash"],
        "label": "Gemini 2.5 Flash",
        "company": "Google",
        "provider_ids": ["gemini", "openrouter"],
        "use_tier": "normal",
        "use_tier_label": "Normal Use (Best Free Tier + Huge Context)",
        "recommended_for": "1M-token context window, fast multimodal vision, and generous free daily usage via Google AI Studio key.",
        "best_tasks": ["Free Daily Chat", "Long Documents (1M ctx)", "Vision & Audio", "Fast Tool Use"],
        "input_cost_per_m": 0.15,
        "output_cost_per_m": 0.60,
        "cost_summary": "FREE on Google AI Studio (up to daily limit) · $0.15 / $0.60 per 1M on OpenRouter",
        "quota_kind": "free_daily",
        "reset_schedule": "daily",
        "free_tier_info": "Free tier on Google AI Studio resets every day at midnight PT (~500–1,500 requests/day & 15 RPM). Can auto-pause until reset or fall back to OpenRouter.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 1048576,
        "is_default_for": [],
        "suggested_rank": 3,
    },
    {
        "id": "google/gemini-3.1-flash-lite",
        "aliases": ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "google/gemini-2.5-flash-lite"],
        "label": "Gemini 3.1 Flash-Lite",
        "company": "Google",
        "provider_ids": ["gemini", "openrouter"],
        "use_tier": "light",
        "use_tier_label": "Light Use (Background Tasks, Summaries & Titles)",
        "recommended_for": "Ultra-fast, near-zero cost model ideal for background memory tidying, notifications, quick lookups, and high-volume light tasks.",
        "best_tasks": ["Background Tasks", "Memory & Summarization", "Quick Q&A", "Low-Budget Chat"],
        "input_cost_per_m": 0.075,
        "output_cost_per_m": 0.30,
        "cost_summary": "FREE on Google AI Studio (up to 1,500 req/day) · $0.075 / $0.30 per 1M paid",
        "quota_kind": "free_daily",
        "reset_schedule": "daily",
        "free_tier_info": "Generous free daily quota on Google AI Studio (resets every 24h) or ultra-cheap per-token on OpenRouter.",
        "capabilities": ["chat", "vision", "tools"],
        "context_window": 1048576,
        "is_default_for": ["background"],
        "suggested_rank": 4,
    },
    {
        "id": "google/gemini-2.5-pro",
        "aliases": ["gemini-2.5-pro"],
        "label": "Gemini 2.5 Pro",
        "company": "Google",
        "provider_ids": ["gemini", "openrouter"],
        "use_tier": "heavy",
        "use_tier_label": "Heavy Use (Large Codebases & Deep Multimodal)",
        "recommended_for": "Top-tier Google reasoning model with 1M+ context window for analyzing entire repositories, long PDFs, and complex multimodal workflows.",
        "best_tasks": ["Full-Repo Analysis", "Complex Reasoning", "Multimodal Vision", "Research"],
        "input_cost_per_m": 1.25,
        "output_cost_per_m": 10.00,
        "cost_summary": "Free limited daily quota on AI Studio · $1.25 / 1M input · $10.00 / 1M output paid",
        "quota_kind": "free_daily",
        "reset_schedule": "daily",
        "free_tier_info": "Includes a smaller free daily request allowance on Google AI Studio (resets daily); automatically falls back when daily limit is reached.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 1048576,
        "is_default_for": [],
        "suggested_rank": 5,
    },
    {
        "id": "openai/gpt-5-mini",
        "aliases": ["gpt-5-mini", "gpt-5.4-mini", "openai/gpt-5.4-mini"],
        "label": "GPT-5 Mini",
        "company": "OpenAI",
        "provider_ids": ["openai", "openrouter"],
        "use_tier": "normal",
        "use_tier_label": "Normal Use (Fast & Reliable Agent Actions)",
        "recommended_for": "Balanced OpenAI model with strong instruction following, reliable function calling, and vision support at low cost.",
        "best_tasks": ["Daily Assistant", "Structured Tool Use", "Vision", "Fast Coding"],
        "input_cost_per_m": 0.25,
        "output_cost_per_m": 2.00,
        "cost_summary": "$0.25 / 1M input · $2.00 / 1M output (~$1–$4/mo typical)",
        "quota_kind": "credits",
        "reset_schedule": "never",
        "free_tier_info": "Uses OpenAI API prepaid credits, ChatGPT sign-in (where supported), or OpenRouter balance.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 200000,
        "is_default_for": [],
        "suggested_rank": 6,
    },
    {
        "id": "anthropic/claude-sonnet-4.5",
        "aliases": ["claude-sonnet-4-5", "claude-sonnet-4-6", "anthropic/claude-sonnet-4-6", "claude-sonnet-5-5"],
        "label": "Claude Sonnet 4.5",
        "company": "Anthropic",
        "provider_ids": ["anthropic", "openrouter"],
        "use_tier": "heavy",
        "use_tier_label": "Heavy Use (Premium Coding, Writing & Computer Use)",
        "recommended_for": "Industry-leading nuanced writing, front-end/full-stack coding, and desktop/browser computer-use tasks.",
        "best_tasks": ["Software Engineering", "Creative & Technical Writing", "Computer Use (Hands)", "Complex Agents"],
        "input_cost_per_m": 3.00,
        "output_cost_per_m": 15.00,
        "cost_summary": "$3.00 / 1M input · $15.00 / 1M output (~$5–$20/mo active use)",
        "quota_kind": "credits",
        "reset_schedule": "never",
        "free_tier_info": "Pay-as-you-go via Anthropic API credits or OpenRouter; best paired with a lighter background model to save credits.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 200000,
        "is_default_for": [],
        "suggested_rank": 7,
    },
    {
        "id": "anthropic/claude-haiku-4.5",
        "aliases": ["claude-haiku-4-5", "claude-haiku-5-5"],
        "label": "Claude Haiku 4.5",
        "company": "Anthropic",
        "provider_ids": ["anthropic", "openrouter"],
        "use_tier": "light",
        "use_tier_label": "Light / Normal Use (Fast Claude with Vision)",
        "recommended_for": "Fast, affordable Claude experience for quick coding, chat, and screen inspection without Sonnet pricing.",
        "best_tasks": ["Fast Chat", "Screen Inspection", "Light Coding", "Background Tasks"],
        "input_cost_per_m": 0.80,
        "output_cost_per_m": 4.00,
        "cost_summary": "$0.80 / 1M input · $4.00 / 1M output",
        "quota_kind": "credits",
        "reset_schedule": "never",
        "free_tier_info": "Pay-as-you-go token billing via Anthropic or OpenRouter.",
        "capabilities": ["chat", "vision", "tools"],
        "context_window": 200000,
        "is_default_for": [],
        "suggested_rank": 8,
    },
    {
        "id": "openai/gpt-5",
        "aliases": ["gpt-5", "gpt-5.4", "openai/gpt-5.4"],
        "label": "GPT-5",
        "company": "OpenAI",
        "provider_ids": ["openai", "openrouter"],
        "use_tier": "heavy",
        "use_tier_label": "Heavy Use (Flagship Multimodal & Reasoning)",
        "recommended_for": "High-precision multimodal reasoning, visual UI automation, and complex multi-tool orchestration.",
        "best_tasks": ["Flagship Reasoning", "Desktop Vision & Hands", "Complex Planning"],
        "input_cost_per_m": 1.25,
        "output_cost_per_m": 10.00,
        "cost_summary": "$1.25 / 1M input · $10.00 / 1M output",
        "quota_kind": "credits",
        "reset_schedule": "never",
        "free_tier_info": "Pay-as-you-go via OpenAI API key, OpenRouter, or ChatGPT Plus/Pro OAuth sign-in.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 200000,
        "is_default_for": [],
        "suggested_rank": 9,
    },
    {
        "id": "qwen/qwen3.5-plus",
        "aliases": ["qwen3.8-27b", "qwen/qwen3.8-27b", "qwen-plus", "qwen3-max"],
        "label": "Qwen 3.5 Plus / 3.8 Vision",
        "company": "Alibaba Qwen",
        "provider_ids": ["openrouter", "bailian", "groq"],
        "use_tier": "normal",
        "use_tier_label": "Normal Use (Multilingual & Visual Agent)",
        "recommended_for": "Strong visual grounding (GUI clicking/hands), multilingual fluency, and low-cost tool execution.",
        "best_tasks": ["Visual Hands & GUI", "Multilingual Chat", "Tool Calling"],
        "input_cost_per_m": 0.20,
        "output_cost_per_m": 0.60,
        "cost_summary": "$0.20 / 1M input · $0.60 / 1M output (free daily quota on Groq)",
        "quota_kind": "credits",
        "reset_schedule": "daily",
        "free_tier_info": "Free daily token/request allowance when routed via Groq; pay-as-you-go via OpenRouter or Bailian.",
        "capabilities": ["chat", "vision", "tools", "reasoning"],
        "context_window": 131072,
        "is_default_for": [],
        "suggested_rank": 10,
    },
    {
        "id": "qwen3.5:9b-32k",
        "aliases": ["qwen3:8b", "qwen3.5:4b-32k", "qwen3.5:2b-32k", "gemma4:e4b-32k"],
        "label": "Qwen 3.5 (Local Ollama)",
        "company": "Alibaba Qwen (Local on Device)",
        "provider_ids": ["ollama"],
        "use_tier": "normal",
        "use_tier_label": "Local Offline Use (100% Free & Private)",
        "recommended_for": "Runs completely offline on your computer via Ollama with zero API cost and full privacy; ideal primary offline model or automatic fallback when cloud credits run out.",
        "best_tasks": ["100% Private Chat", "Offline Fallback", "Zero-Cost Unlimited Use"],
        "input_cost_per_m": 0.0,
        "output_cost_per_m": 0.0,
        "cost_summary": "$0.00 — 100% Free & Unlimited (runs on your hardware)",
        "quota_kind": "local_unlimited",
        "reset_schedule": "unlimited",
        "free_tier_info": "Unlimited forever on your own device; no API key or internet required once downloaded.",
        "capabilities": ["chat", "tools", "reasoning"],
        "context_window": 32768,
        "is_default_for": ["local"],
        "suggested_rank": 11,
    },
]

RECOMMENDED_DEFAULT_SET: dict[str, Any] = {
    "chat": "deepseek/deepseek-v4.1-flash-1017",
    "chat_direct_deepseek": "deepseek-v4.1-flash-1017",
    "chat_direct_gemini": "gemini-2.5-flash",
    "hands": "deepseek/deepseek-v4.1-flash-1017",
    "background_model": "google/gemini-3.1-flash-lite",
    "heavy_model": "deepseek/deepseek-v4.1-pro-1017",
    "local_model": "qwen3.5:9b-32k",
    "summary": (
        "Recommended Best Set: DeepSeek V4.1 Flash (1017) for fast daily chat & vision hands, "
        "Gemini 3.1 Flash-Lite for background tasks & memory tidying, "
        "DeepSeek V4.1 Pro (1017) for heavy reasoning, and Local Ollama as offline backup."
    ),
    "tasks": [
        {
            "task": "chat",
            "label": "Daily Chat & Tool Use",
            "model": "deepseek/deepseek-v4.1-flash-1017",
            "fallback": "google/gemini-2.5-flash",
            "reason": "Fastest latest V4.1 Flash (1017) with native tool use and ultra-low token cost.",
        },
        {
            "task": "hands",
            "label": "Vision & Screen Control (Hands)",
            "model": "deepseek/deepseek-v4.1-flash-1017",
            "fallback": "google/gemini-2.5-flash",
            "reason": "Reads screenshots and UI elements accurately without high vision token charges.",
        },
        {
            "task": "background",
            "label": "Background Memory, Summaries & Light Tasks",
            "model": "google/gemini-3.1-flash-lite",
            "fallback": "deepseek/deepseek-v4.1-flash-1017",
            "reason": "Uses free daily Google AI Studio quota or ultra-light $0.075/1M tokens so background jobs never drain main credits.",
        },
        {
            "task": "heavy",
            "label": "Heavy Reasoning, Coding & Research",
            "model": "deepseek/deepseek-v4.1-pro-1017",
            "fallback": "anthropic/claude-sonnet-4.5",
            "reason": "Deep thinking model for complex debugging, math, and multi-file planning.",
        },
        {
            "task": "local",
            "label": "Offline / Zero-Credit Local Fallback",
            "model": "qwen3.5:9b-32k",
            "fallback": "qwen3.5:4b-32k",
            "reason": "Runs locally via Ollama when offline or if all cloud quotas/credits are paused.",
        },
    ],
}


def get_model_info(model_id: str) -> dict[str, Any] | None:
    """Return rich catalog metadata for ``model_id`` (matching exact id or alias)."""
    needle = (model_id or "").strip().lower()
    if not needle:
        return None
    # Strip ":free" suffix for lookup if present
    bare = needle[:-5] if needle.endswith(":free") else needle
    for entry in MODEL_CATALOG_INFO:
        if entry["id"].lower() == bare:
            return dict(entry)
        for alias in entry.get("aliases", []):
            if alias.lower() == bare:
                return dict(entry)
    # Heuristic fallback for any other model id
    maker = "OpenAI" if "gpt" in bare or bare.startswith("o1") or bare.startswith("o3") else (
        "Google" if "gemini" in bare or "gemma" in bare else (
            "Anthropic" if "claude" in bare else (
                "DeepSeek" if "deepseek" in bare else (
                    "Alibaba Qwen" if "qwen" in bare else (
                        "xAI" if "grok" in bare else (
                            "Moonshot AI" if "kimi" in bare or "moonshot" in bare else (
                                "Zhipu AI" if "glm" in bare else "Cloud / Open Model"
                            )
                        )
                    )
                )
            )
        )
    )
    is_lite = any(k in bare for k in ("lite", "mini", "flash", "haiku", "small", "2b", "4b"))
    is_heavy = any(k in bare for k in ("pro", "opus", "sonnet", "reasoner", "large", "max", "70b", "120b"))
    tier = "light" if (is_lite and not is_heavy) else ("heavy" if is_heavy else "normal")
    return {
        "id": model_id,
        "aliases": [],
        "label": model_id,
        "company": maker,
        "provider_ids": ["openrouter"],
        "use_tier": tier,
        "use_tier_label": (
            "Light Use (Fast & Economical)"
            if tier == "light"
            else ("Heavy Use (Deep Reasoning)" if tier == "heavy" else "Normal Use (Balanced)")
        ),
        "recommended_for": f"{maker} model for {'fast everyday tasks' if tier != 'heavy' else 'complex reasoning and coding'}.",
        "best_tasks": ["Chat", "Tool Calling"] + (["Reasoning"] if tier == "heavy" else ["Fast Responses"]),
        "input_cost_per_m": 0.0 if needle.endswith(":free") or ":" in bare else (0.15 if tier == "light" else (2.0 if tier == "heavy" else 0.30)),
        "output_cost_per_m": 0.0 if needle.endswith(":free") or ":" in bare else (0.60 if tier == "light" else (8.0 if tier == "heavy" else 1.00)),
        "cost_summary": (
            "Free tier / $0.00"
            if needle.endswith(":free") or ":" in bare
            else "Pay-as-you-go per 1M tokens (exact rate depends on provider)"
        ),
        "quota_kind": "free_daily" if needle.endswith(":free") else ("local_unlimited" if ":" in bare else "credits"),
        "reset_schedule": "daily" if needle.endswith(":free") else ("unlimited" if ":" in bare else "never"),
        "free_tier_info": "Automatically tracks usage and learns rate-limit / quota patterns from API responses.",
        "capabilities": ["chat", "tools"],
        "context_window": 128000,
        "is_default_for": [],
        "suggested_rank": 99,
    }


# ---------------------------------------------------------------------------
# Provider Quota Profiles & Multi-Provider Key Mapping
# ---------------------------------------------------------------------------

PROVIDER_KEY_SLOTS: list[dict[str, Any]] = [
    {
        "provider": "gemini",
        "vault_key": "GEMINI_API_KEY",
        "name": "Google AI Studio (Gemini)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_url": "https://aistudio.google.com/apikey",
        "key_hint": "AIza…",
        "default_quota_type": "requests_and_tokens",
        "default_reset_period": "daily",
        "default_daily_requests": 1500,
        "default_rpm": 15,
        "default_tpm": 1000000,
        "description": "Free daily Gemini usage (resets daily at midnight PT) or paid Tier 1+. Ideal for free Gemini 2.5 Flash & 3.1 Flash-Lite.",
    },
    {
        "provider": "deepseek",
        "vault_key": "DEEPSEEK_API_KEY",
        "name": "DeepSeek Direct API",
        "base_url": "https://api.deepseek.com/v1",
        "key_url": "https://platform.deepseek.com/api_keys",
        "key_hint": "sk-…",
        "default_quota_type": "credits",
        "default_reset_period": "never",
        "supports_live_balance": True,
        "description": "Use your direct DeepSeek credits first for DeepSeek V4.1 Flash (1017) & Pro, then auto-switch when balance hits $0.",
    },
    {
        "provider": "openrouter",
        "vault_key": "OPENROUTER_API_KEY",
        "name": "OpenRouter (Universal Catch-All & Fallback)",
        "base_url": "https://openrouter.ai/api/v1",
        "key_url": "https://openrouter.ai/keys",
        "key_hint": "sk-or-v1-…",
        "default_quota_type": "credits",
        "default_reset_period": "never",
        "supports_live_balance": True,
        "description": "One key for 300+ models (DeepSeek, Gemini, Claude, GPT, Qwen). Can act as your primary gateway or automatic fallback when direct keys run out.",
    },
    {
        "provider": "openai",
        "vault_key": "OPENAI_API_KEY",
        "name": "OpenAI Direct API",
        "base_url": "https://api.openai.com/v1",
        "key_url": "https://platform.openai.com/api-keys",
        "key_hint": "sk-proj-…",
        "default_quota_type": "credits",
        "default_reset_period": "never",
        "description": "Direct key for GPT-5, GPT-5 Mini, and image generation.",
    },
    {
        "provider": "anthropic",
        "vault_key": "ANTHROPIC_API_KEY",
        "name": "Anthropic Direct API (Claude)",
        "base_url": "https://api.anthropic.com",
        "key_url": "https://platform.claude.com/settings/keys",
        "key_hint": "sk-ant-…",
        "default_quota_type": "credits",
        "default_reset_period": "never",
        "description": "Direct key for Claude Sonnet 4.5 & Haiku 4.5.",
    },
    {
        "provider": "groq",
        "vault_key": "GROQ_API_KEY",
        "name": "Groq Cloud (Fast Open Models)",
        "base_url": "https://api.groq.com/openai/v1",
        "key_url": "https://console.groq.com/keys",
        "key_hint": "gsk_…",
        "default_quota_type": "tokens_and_requests",
        "default_reset_period": "daily",
        "default_daily_requests": 1000,
        "default_daily_tokens": 500000,
        "default_rpm": 30,
        "description": "Free daily request & token allowance (resets every 24h) for ultra-fast Qwen & GPT-OSS.",
    },
    {
        "provider": "moonshot",
        "vault_key": "MOONSHOT_API_KEY",
        "name": "Kimi (Moonshot AI)",
        "base_url": "https://api.moonshot.ai/v1",
        "key_url": "https://platform.kimi.ai/console",
        "key_hint": "sk-…",
        "default_quota_type": "credits",
        "default_reset_period": "never",
        "supports_live_balance": True,
        "description": "Direct Kimi K2.6 / K3 key with live balance checking.",
    },
    {
        "provider": "siliconflow",
        "vault_key": "SILICONFLOW_API_KEY",
        "name": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "key_url": "https://cloud.siliconflow.cn/account/ak",
        "key_hint": "sk-…",
        "default_quota_type": "credits",
        "default_reset_period": "never",
        "supports_live_balance": True,
        "description": "Prepaid balance for DeepSeek, Qwen, and FLUX models with live balance checking.",
    },
]

PROVIDER_BY_ID: dict[str, dict[str, Any]] = {p["provider"]: p for p in PROVIDER_KEY_SLOTS}


def default_routing_config() -> dict[str, Any]:
    """Default multi-API smart routing configuration."""
    return {
        "enabled": True,
        "prefer_direct_keys": True,
        "auto_learn_quotas": True,
        "global_fallback_order": ["openrouter", "gemini", "deepseek", "groq", "ollama"],
        "rules": [
            {
                "id": "gemini-free-first",
                "match": "gemini",
                "label": "Gemini Models (Use Free Google AI Studio First)",
                "primary_provider": "gemini",
                "on_exhausted": "fallback_openrouter",
                "fallback_provider": "openrouter",
                "fallback_model": "deepseek/deepseek-v4.1-flash-1017",
                "cooldown_Policy": "wait_for_reset",
                "enabled": True,
            },
            {
                "id": "deepseek-credits-first",
                "match": "deepseek",
                "label": "DeepSeek Models (Use Direct DeepSeek Credits First)",
                "primary_provider": "deepseek",
                "on_exhausted": "fallback_openrouter",
                "fallback_provider": "openrouter",
                "fallback_model": "google/gemini-2.5-flash",
                "cooldown_policy": "until_balance_added",
                "enabled": True,
            },
            {
                "id": "openrouter-catchall",
                "match": "*",
                "label": "All Other Models / Catch-All Fallback",
                "primary_provider": "openrouter",
                "on_exhausted": "switch_model",
                "fallback_provider": "gemini",
                "fallback_model": "google/gemini-2.5-flash",
                "enabled": True,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Adaptive Quota & Live Balance Tracker
# ---------------------------------------------------------------------------

def _window_keys(now: float | None = None) -> dict[str, str]:
    t = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc)
    iso_year, iso_week, _ = t.isocalendar()
    return {
        "minute": t.strftime("%Y-%m-%dT%H:%M"),
        "day": t.strftime("%Y-%m-%d"),
        "week": f"{iso_year}-W{iso_week:02d}",
        "month": t.strftime("%Y-%m"),
    }


def _next_reset_timestamp(reset_period: str, now: float | None = None) -> float | None:
    ts = now if now is not None else time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if reset_period == "rolling_minute":
        return ts + 60.0
    if reset_period == "daily":
        # Next UTC midnight (or +24h window)
        tomorrow = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp() + 86400.0
        return tomorrow
    if reset_period == "weekly":
        days_ahead = 7 - dt.weekday()
        next_week = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp() + days_ahead * 86400.0
        return next_week
    if reset_period == "monthly":
        if dt.month == 12:
            nxt = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            nxt = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
        return nxt.timestamp()
    return None


class QuotaTracker:
    """Persists usage windows, live API balances, and adaptively learned rate/quota limits."""

    def __init__(self, data_dir: Path | None) -> None:
        self.data_dir = data_dir
        self.path = (data_dir / "quota-state.json") if data_dir is not None else None

    def _load(self) -> dict[str, Any]:
        if self.path is None or not self.path.exists():
            return {"providers": {}, "models": {}, "events": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                raw.setdefault("providers", {})
                raw.setdefault("models", {})
                raw.setdefault("events", [])
                return raw
        except Exception:
            pass
        return {"providers": {}, "models": {}, "events": []}

    def _save(self, state: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:
            logger.debug("QuotaTracker save failed: %s", exc)

    def _ensure_provider_Bucket(self, state: dict[str, Any], provider: str, now: float | None = None) -> dict[str, Any]:
        ts = now if now is not None else time.time()
        w = _window_keys(ts)
        prof = PROVIDER_BY_ID.get(provider, {})
        p = state["providers"].setdefault(
            provider,
            {
                "provider": provider,
                "quota_type": prof.get("default_quota_type", "credits"),
                "reset_period": prof.get("default_reset_period", "never"),
                "windows": dict(w),
                "minute_requests": 0,
                "minute_tokens": 0,
                "day_requests": 0,
                "day_tokens": 0,
                "week_requests": 0,
                "week_tokens": 0,
                "month_requests": 0,
                "month_tokens": 0,
                "total_requests": 0,
                "total_tokens": 0,
                "estimated_spend_usd": 0.0,
                "learned_limits": {
                    "day_requests": prof.get("default_daily_requests"),
                    "day_tokens": prof.get("default_daily_tokens"),
                    "minute_requests": prof.get("default_rpm"),
                    "minute_tokens": prof.get("default_tpm"),
                },
                "live_balance": None,
                "exhausted": False,
                "exhausted_reason": "",
                "cooldown_until": None,
                "last_error": "",
                "last_updated": ts,
            },
        )
        old_w = p.setdefault("windows", dict(w))
        if old_w.get("minute") != w["minute"]:
            p["minute_requests"] = 0
            p["minute_tokens"] = 0
            old_w["minute"] = w["minute"]
        if old_w.get("day") != w["day"]:
            p["day_requests"] = 0
            p["day_tokens"] = 0
            old_w["day"] = w["day"]
            if p.get("reset_period") == "daily":
                p["exhausted"] = False
                p["exhausted_reason"] = ""
                p["cooldown_until"] = None
        if old_w.get("week") != w["week"]:
            p["week_requests"] = 0
            p["week_tokens"] = 0
            old_w["week"] = w["week"]
            if p.get("reset_period") == "weekly":
                p["exhausted"] = False
                p["exhausted_reason"] = ""
                p["cooldown_until"] = None
        if old_w.get("month") != w["month"]:
            p["month_requests"] = 0
            p["month_tokens"] = 0
            old_w["month"] = w["month"]
            if p.get("reset_period") == "monthly":
                p["exhausted"] = False
                p["exhausted_reason"] = ""
                p["cooldown_until"] = None
        # Clear expired cooldown
        cd = p.get("cooldown_until")
        if isinstance(cd, (int, float)) and ts >= cd:
            p["cooldown_until"] = None
            if p.get("reset_period") in ("rolling_minute", "daily", "weekly", "monthly"):
                p["exhausted"] = False
                p["exhausted_reason"] = ""
        return p

    def record_usage(
        self,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        headers: dict[str, str] | None = None,
    ) -> None:
        now = time.time()
        state = self._load()
        p = self._ensure_provider_Bucket(state, provider, now)
        total_tok = max(0, int(prompt_tokens) + int(completion_tokens))
        p["minute_requests"] = int(p.get("minute_requests", 0)) + 1
        p["minute_tokens"] = int(p.get("minute_tokens", 0)) + total_tok
        p["day_requests"] = int(p.get("day_requests", 0)) + 1
        p["day_tokens"] = int(p.get("day_tokens", 0)) + total_tok
        p["week_requests"] = int(p.get("week_requests", 0)) + 1
        p["week_tokens"] = int(p.get("week_tokens", 0)) + total_tok
        p["month_requests"] = int(p.get("month_requests", 0)) + 1
        p["month_tokens"] = int(p.get("month_tokens", 0)) + total_tok
        p["total_requests"] = int(p.get("total_requests", 0)) + 1
        p["total_tokens"] = int(p.get("total_tokens", 0)) + total_tok

        info = get_model_info(model)
        if info:
            in_cost = (prompt_tokens / 1_000_000.0) * float(info.get("input_cost_per_m") or 0.0)
            out_cost = (completion_tokens / 1_000_000.0) * float(info.get("output_cost_per_m") or 0.0)
            p["estimated_spend_usd"] = round(float(p.get("estimated_spend_usd") or 0.0) + in_cost + out_cost, 6)

        p["last_updated"] = now
        if headers:
            self._learn_from_headers_in_place(p, headers)
        self._save(state)

    def _learn_from_headers_in_place(self, p: dict[str, Any], headers: dict[str, str]) -> None:
        low = {k.lower(): str(v) for k, v in headers.items()}
        limits = p.setdefault("learned_limits", {})
        for hdr, key in (
            ("x-ratelimit-limit-requests", "day_requests"),
            ("x-ratelimit-limit-tokens", "minute_tokens"),
            ("x-ratelimit-remaining-requests", "header_remaining_requests"),
            ("x-ratelimit-remaining-tokens", "header_remaining_tokens"),
        ):
            val = low.get(hdr)
            if val and val.isdigit():
                limits[key] = int(val)

    def learn_from_error(
        self,
        provider: str,
        model: str,
        error_text: str,
        status_code: int | None = None,
    ) -> dict[str, Any]:
        """Analyze an API failure (429/402/403) and adaptively learn the quota type, ceiling, and reset schedule."""
        now = time.time()
        state = self._load()
        p = self._ensure_provider_Bucket(state, provider, now)
        msg = (error_text or "").strip()
        low = msg.lower()

        # 1. Classify whether this is credits, free trial, token limit, or request limit
        is_credits = (
            status_code == 402
            or any(
                kw in low
                for kw in (
                    "insufficient_quota",
                    "insufficient balance",
                    "balance is not enough",
                    "out of credits",
                    "credit balance is too low",
                    "exceeded your current quota, please check your plan and billing",
                    "payment required",
                    "arrears",
                )
            )
        )
        is_trial = any(kw in low for kw in ("free trial", "trial quota", "trial has expired"))
        is_tokens = any(kw in low for kw in ("tokens per", "tpm", "tpd", "token limit", "token quota"))
        is_requests = (
            status_code == 429
            or any(
                kw in low
                for kw in (
                    "rate limit",
                    "ratelimit",
                    "too many requests",
                    "resource_exhausted",
                    "requests per",
                    "rpm",
                    "rpd",
                    "quota exceeded",
                )
            )
        )

        # 2. Detect reset schedule from error message
        learned_reset = p.get("reset_period") or "never"
        cooldown_seconds = 60.0

        # Check explicit retry-after / "retry in Xs"
        retry_match = re.search(r"retry in (\d+(?:\.\d+)?)\s*(ms|s|sec|seconds|m|min|minutes|h|hours)", low)
        if is_credits or is_trial:
            p["quota_type"] = "free_trial" if is_trial else "credits"
            p["reset_period"] = "never"
            p["exhausted"] = True
            p["exhausted_reason"] = (
                "Free trial expired" if is_trial else "Out of API credits / prepaid balance"
            )
            p["cooldown_until"] = None  # Stays exhausted until balance/key changes or user resets
        elif is_tokens or is_requests:
            p["quota_type"] = "tokens" if is_tokens else "requests"
            if any(k in low for k in ("per day", "daily", "rpd", "tpd", "per_day", "day")):
                learned_reset = "daily"
                cooldown_seconds = max(300.0, (_next_reset_timestamp("daily", now) or (now + 3600)) - now)
                # Learn the ceiling from how much was used today!
                limits = p.setdefault("learned_limits", {})
                if is_tokens and p.get("day_tokens", 0) > 0:
                    limits["day_tokens"] = int(p["day_tokens"])
                elif p.get("day_requests", 0) > 0:
                    limits["day_requests"] = int(p["day_requests"])
            elif any(k in low for k in ("per week", "weekly")):
                learned_reset = "weekly"
                cooldown_seconds = max(3600.0, (_next_reset_timestamp("weekly", now) or (now + 86400)) - now)
            elif any(k in low for k in ("per month", "monthly", "billing cycle")):
                learned_reset = "monthly"
                cooldown_seconds = max(3600.0, (_next_reset_timestamp("monthly", now) or (now + 86400)) - now)
            else:
                learned_reset = "rolling_minute"
                cooldown_seconds = 65.0
                limits = p.setdefault("learned_limits", {})
                if is_tokens and p.get("minute_tokens", 0) > 0:
                    limits["minute_tokens"] = int(p["minute_tokens"])
                elif p.get("minute_requests", 0) > 0:
                    limits["minute_requests"] = int(p["minute_requests"])

            if retry_match:
                val = float(retry_match.group(1))
                unit = retry_match.group(2)
                if unit == "ms":
                    cooldown_seconds = max(5.0, val / 1000.0)
                elif unit.startswith("m"):
                    cooldown_seconds = val * 60.0
                elif unit.startswith("h"):
                    cooldown_seconds = val * 3600.0
                else:
                    cooldown_seconds = max(5.0, val)

            p["reset_period"] = learned_reset
            p["exhausted"] = learned_reset in ("daily", "weekly", "monthly")
            p["exhausted_reason"] = (
                f"Reached {p['quota_type']} limit ({learned_reset.replace('_', ' ')} reset)"
            )
            p["cooldown_until"] = now + cooldown_seconds

        p["last_error"] = msg[:300]
        p["last_updated"] = now
        events = state.setdefault("events", [])
        events.append(
            {
                "timestamp": now,
                "provider": provider,
                "model": model,
                "quota_type": p.get("quota_type"),
                "reset_period": p.get("reset_period"),
                "reason": p.get("exhausted_reason") or msg[:160],
            }
        )
        state["events"] = events[-30:]
        self._save(state)
        return dict(p)

    def clear_exhausted(self, provider: str) -> None:
        state = self._load()
        p = self._ensure_provider_Bucket(state, provider)
        p["exhausted"] = False
        p["exhausted_reason"] = ""
        p["cooldown_until"] = None
        p["last_error"] = ""
        self._save(state)

    def is_provider_available(self, provider: str) -> tuple[bool, str]:
        """Return ``(available, reason_if_unavailable)`` for ``provider`` right now."""
        if provider in ("ollama", "lm-studio", "vllm"):
            return True, ""
        now = time.time()
        state = self._load()
        p = self._ensure_provider_Bucket(state, provider, now)
        # Check live balance if known
        bal = p.get("live_balance")
        if isinstance(bal, dict) and bal.get("available") is False:
            return False, bal.get("summary") or "Balance is $0.00"
        cd = p.get("cooldown_until")
        if isinstance(cd, (int, float)) and now < cd:
            rem = int(max(1, cd - now))
            if rem < 120:
                return False, f"Rate limit cooldown ({rem}s remaining)"
            if rem < 7200:
                return False, f"Quota cooldown ({rem // 60}m remaining)"
            return False, f"Quota cooldown ({rem // 3600}h remaining until reset)"
        if p.get("exhausted"):
            return False, str(p.get("exhausted_reason") or "Quota or credits exhausted")
        # Check learned daily ceiling proactively
        limits = p.get("learned_limits") or {}
        day_req_lim = limits.get("day_requests")
        if isinstance(day_req_lim, int) and day_req_lim > 0 and int(p.get("day_requests", 0)) >= day_req_lim:
            return False, f"Daily request limit reached ({p.get('day_requests')}/{day_req_lim})"
        day_tok_lim = limits.get("day_tokens")
        if isinstance(day_tok_lim, int) and day_tok_lim > 0 and int(p.get("day_tokens", 0)) >= day_tok_lim:
            return False, f"Daily token limit reached ({p.get('day_tokens')}/{day_tok_lim})"
        return True, ""

    async def refresh_balances(self, vault: CredentialVault | None) -> dict[str, Any]:
        """Query live API balances from providers that expose balance/credits endpoints."""
        if vault is None:
            return self.status_report(vault)
        state = self._load()
        now = time.time()

        async with httpx.AsyncClient(timeout=8.0) as client:
            # 1. DeepSeek balance: GET https://api.deepseek.com/user/balance
            ds_key = _get_provider_api_key("deepseek", vault)
            if ds_key:
                p = self._ensure_provider_Bucket(state, "deepseek", now)
                try:
                    resp = await client.get(
                        "https://api.deepseek.com/user/balance",
                        headers={"Authorization": f"Bearer {ds_key}", "Accept": "application/json"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        infos = data.get("balance_infos") or []
                        is_avail = bool(data.get("is_available", True))
                        total_bal = 0.0
                        currency = "USD"
                        if infos and isinstance(infos, list):
                            first = infos[0]
                            currency = str(first.get("currency") or "USD")
                            total_bal = float(first.get("total_balance") or 0.0)
                            is_avail = is_avail and (total_bal > 0.0)
                        p["live_balance"] = {
                            "currency": currency,
                            "remaining": total_bal,
                            "available": is_avail,
                            "summary": f"{currency} {total_bal:.2f} remaining",
                            "checked_at": now,
                        }
                        if not is_avail or total_bal <= 0.0:
                            p["exhausted"] = True
                            p["exhausted_reason"] = f"DeepSeek balance is {currency} 0.00"
                        else:
                            p["exhausted"] = False
                            p["exhausted_reason"] = ""
                except Exception as exc:
                    logger.debug("DeepSeek balance check failed: %s", exc)

            # 2. OpenRouter balance: GET https://openrouter.ai/api/v1/credits & /api/v1/auth/key
            or_key = _get_provider_api_key("openrouter", vault)
            if or_key:
                p = self._ensure_provider_Bucket(state, "openrouter", now)
                try:
                    resp = await client.get(
                        "https://openrouter.ai/api/v1/credits",
                        headers={"Authorization": f"Bearer {or_key}", "Accept": "application/json"},
                    )
                    if resp.status_code == 200:
                        cdata = (resp.json() or {}).get("data") or {}
                        total_credits = float(cdata.get("total_credits") or 0.0)
                        total_usage = float(cdata.get("total_usage") or 0.0)
                        remaining = round(max(0.0, total_credits - total_usage), 4)
                        is_avail = remaining > 0.0005 or total_credits == 0.0
                        p["live_balance"] = {
                            "currency": "USD",
                            "total_credits": round(total_credits, 2),
                            "total_usage": round(total_usage, 4),
                            "remaining": remaining,
                            "available": is_avail,
                            "summary": (
                                f"${remaining:.2f} remaining (${total_usage:.2f} used of ${total_credits:.2f})"
                                if total_credits > 0
                                else f"${total_usage:.2f} used (Free / Pay-as-you-go)"
                            ),
                            "checked_at": now,
                        }
                        if total_credits > 0 and remaining <= 0.0005:
                            p["exhausted"] = True
                            p["exhausted_reason"] = "OpenRouter credits exhausted ($0.00 left)"
                        elif remaining > 0.001:
                            p["exhausted"] = False
                            p["exhausted_reason"] = ""
                except Exception as exc:
                    logger.debug("OpenRouter balance check failed: %s", exc)

            # 3. Moonshot balance: GET https://api.moonshot.ai/v1/users/me/balance
            ms_key = _get_provider_api_key("moonshot", vault)
            if ms_key:
                p = self._ensure_provider_Bucket(state, "moonshot", now)
                for host in ("https://api.moonshot.ai/v1", "https://api.moonshot.cn/v1"):
                    try:
                        resp = await client.get(
                            f"{host}/users/me/balance",
                            headers={"Authorization": f"Bearer {ms_key}", "Accept": "application/json"},
                        )
                        if resp.status_code == 200:
                            bdata = (resp.json() or {}).get("data") or {}
                            avail = float(bdata.get("available_balance") or 0.0)
                            p["live_balance"] = {
                                "currency": "USD/CNY",
                                "remaining": avail,
                                "available": avail > 0.0,
                                "summary": f"{avail:.2f} available balance",
                                "checked_at": now,
                            }
                            break
                    except Exception:
                        continue

            # 4. SiliconFlow balance: GET https://api.siliconflow.cn/v1/user/info
            sf_key = _get_provider_api_key("siliconflow", vault)
            if sf_key:
                p = self._ensure_provider_Bucket(state, "siliconflow", now)
                try:
                    resp = await client.get(
                        "https://api.siliconflow.cn/v1/user/info",
                        headers={"Authorization": f"Bearer {sf_key}", "Accept": "application/json"},
                    )
                    if resp.status_code == 200:
                        udata = (resp.json() or {}).get("data") or {}
                        avail = float(udata.get("totalBalance") or udata.get("balance") or 0.0)
                        p["live_balance"] = {
                            "currency": "CNY",
                            "remaining": avail,
                            "available": avail > 0.0,
                            "summary": f"¥{avail:.2f} remaining",
                            "checked_at": now,
                        }
                except Exception:
                    pass

        self._save(state)
        return self.status_report(vault)

    def status_report(self, vault: CredentialVault | None = None) -> dict[str, Any]:
        """Build a complete status report of all provider keys, remaining quotas, and learned patterns."""
        now = time.time()
        state = self._load()
        providers_out: list[dict[str, Any]] = []
        for slot in PROVIDER_KEY_SLOTS:
            pid = slot["provider"]
            p = self._ensure_provider_Bucket(state, pid, now)
            has_key = bool(_get_provider_api_key(pid, vault)) if vault is not None else False
            avail, unavail_reason = self.is_provider_available(pid)
            limits = p.get("learned_limits") or {}
            day_req_lim = limits.get("day_requests")
            day_req_used = int(p.get("day_requests", 0))
            day_tok_lim = limits.get("day_tokens")
            day_tok_used = int(p.get("day_tokens", 0))

            remaining_pct: int | None = None
            remaining_summary = ""
            live_bal = p.get("live_balance")
            if isinstance(live_bal, dict) and live_bal.get("summary"):
                remaining_summary = str(live_bal["summary"])
                if live_bal.get("total_credits") and float(live_bal["total_credits"]) > 0:
                    remaining_pct = int(
                        round(
                            100.0
                            * float(live_bal.get("remaining") or 0.0)
                            / float(live_bal["total_credits"])
                        )
                    )
            elif isinstance(day_req_lim, int) and day_req_lim > 0:
                rem_req = max(0, day_req_lim - day_req_used)
                remaining_pct = int(round(100.0 * rem_req / day_req_lim))
                remaining_summary = (
                    f"{rem_req:,} / {day_req_lim:,} requests left today ({remaining_pct}%)"
                )
            elif isinstance(day_tok_lim, int) and day_tok_lim > 0:
                rem_tok = max(0, day_tok_lim - day_tok_used)
                remaining_pct = int(round(100.0 * rem_tok / day_tok_lim))
                remaining_summary = (
                    f"{rem_tok:,} / {day_tok_lim:,} tokens left today ({remaining_pct}%)"
                )
            else:
                est = float(p.get("estimated_spend_usd") or 0.0)
                remaining_summary = (
                    f"{day_req_used:,} reqs today · {int(p.get('day_tokens', 0)):,} tokens (~${est:.2f} est. total)"
                )

            next_reset = _next_reset_timestamp(str(p.get("reset_period") or "never"), now)
            providers_out.append(
                {
                    "provider": pid,
                    "name": slot["name"],
                    "vault_key": slot["vault_key"],
                    "key_url": slot["key_url"],
                    "key_hint": slot["key_hint"],
                    "description": slot["description"],
                    "has_key": has_key,
                    "available": avail if has_key else False,
                    "status_reason": unavail_reason if has_key else "No API key saved",
                    "quota_type": p.get("quota_type", slot["default_quota_type"]),
                    "reset_period": p.get("reset_period", slot["default_reset_period"]),
                    "next_reset_at": next_reset,
                    "cooldown_until": p.get("cooldown_until"),
                    "exhausted": bool(p.get("exhausted", False)),
                    "exhausted_reason": p.get("exhausted_reason", ""),
                    "day_requests": day_req_used,
                    "day_tokens": day_tok_used,
                    "minute_requests": int(p.get("minute_requests", 0)),
                    "total_requests": int(p.get("total_requests", 0)),
                    "total_tokens": int(p.get("total_tokens", 0)),
                    "estimated_spend_usd": float(p.get("estimated_spend_usd") or 0.0),
                    "learned_limits": limits,
                    "live_balance": live_bal,
                    "remaining_pct": remaining_pct,
                    "remaining_summary": remaining_summary,
                    "last_error": p.get("last_error", ""),
                }
            )
        self._save(state)
        return {
            "providers": providers_out,
            "events": state.get("events", [])[-15:],
            "updated_at": now,
        }


# ---------------------------------------------------------------------------
# Route Resolution & Failover Engine
# ---------------------------------------------------------------------------

def _get_provider_api_key(
    provider: str,
    vault: CredentialVault | None,
    primary_settings: LLMSettings | None = None,
) -> str:
    """Resolve the stored API key for ``provider`` from the credential vault or active slot."""
    slot = PROVIDER_BY_ID.get(provider)
    if vault is not None:
        if slot:
            val = vault.get(slot["vault_key"])
            if val:
                return val
        generic = vault.get(f"LLM_KEY_{provider.upper().replace('-', '_')}")
        if generic:
            return generic
    if primary_settings is not None:
        # If the primary settings point to this provider, use its resolved key
        p_id = (primary_settings.provider or "").lower()
        b_url = (primary_settings.base_url or "").lower()
        if p_id == provider or (slot and slot["base_url"].lower() in b_url):
            raw_key = primary_settings.api_key or ""
            if vault is not None and vault.has_placeholders(raw_key):
                try:
                    return vault.resolve(raw_key, strict=False)
                except Exception:
                    return ""
            return raw_key
    return ""


def _adapt_model_for_provider(model: str, target_provider: str) -> str:
    """Translate a model ID between OpenRouter (``maker/model``) and direct vendor APIs."""
    m = (model or "").strip()
    low = m.lower()
    if target_provider == "openrouter":
        if "/" in m:
            return m
        if low.startswith("gemini-"):
            return f"google/{m}"
        if low.startswith("deepseek-"):
            return f"deepseek/{m}"
        if low.startswith("gpt-") or low.startswith("o1") or low.startswith("o3"):
            return f"openai/{m}"
        if low.startswith("claude-"):
            return f"anthropic/{m}"
        if low.startswith("qwen"):
            return f"qwen/{m}"
        if low.startswith("grok-"):
            return f"x-ai/{m}"
        return m
    if target_provider == "gemini":
        bare = m.split("/", 1)[1] if low.startswith("google/") else m
        if bare.endswith(":free"):
            bare = bare[:-5]
        return bare if bare.lower().startswith("gemini") else "gemini-2.5-flash"
    if target_provider == "deepseek":
        bare = m.split("/", 1)[1] if low.startswith("deepseek/") else m
        if bare.endswith(":free"):
            bare = bare[:-5]
        return bare if bare.lower().startswith("deepseek") else "deepseek-v4.1-flash-1017"
    if target_provider == "openai":
        bare = m.split("/", 1)[1] if low.startswith("openai/") else m
        return bare if (bare.lower().startswith("gpt") or bare.lower().startswith("o")) else "gpt-5-mini"
    if target_provider == "anthropic":
        bare = m.split("/", 1)[1] if low.startswith("anthropic/") else m
        return bare if bare.lower().startswith("claude") else "claude-sonnet-4-5"
    if target_provider == "groq":
        return m if ("qwen" in low or "gpt-oss" in low or "llama" in low) else "qwen/qwen3.8-27b"
    if target_provider == "ollama":
        return m if ":" in m else "qwen3.5:9b-32k"
    return m


def load_routing_config(data_dir: Path | None) -> dict[str, Any]:
    """Load ``llm-routing.json`` from ``data_dir`` (merging with defaults)."""
    cfg = default_routing_config()
    if data_dir is None:
        return cfg
    path = data_dir / "llm-routing.json"
    if not path.exists():
        return cfg
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            cfg.update(saved)
    except Exception:
        pass
    return cfg


def save_routing_config(data_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Save ``llm-routing.json`` into ``data_dir``."""
    merged = default_routing_config()
    merged.update(config)
    path = data_dir / "llm-routing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


def build_candidate_routes(
    settings: LLMSettings,
    data_dir: Path | None,
    vault: CredentialVault | None,
) -> list[tuple[ str, LLMSettings]]:
    """Build an ordered list of ``(provider_id, LLMSettings)`` routes for ``settings``."""
    cfg = load_routing_config(data_dir)
    if not cfg.get("enabled", True):
        return [(settings.provider or "openai", settings)]

    tracker = QuotaTracker(data_dir)
    model = (settings.model or "").strip()
    low_model = model.lower()
    candidates: list[tuple[str, LLMSettings]] = []
    seen: set[tuple[str, str]] = set()

    def _add_candidate(prov: str, target_model: str) -> None:
        if prov == "ollama":
            cand = settings.model_copy(
                update={
                    "provider": "openai",
                    "base_url": "http://127.0.0.1:11434/v1",
                    "api_key": "ollama",
                    "model": _adapt_model_for_provider(target_model, "ollama"),
                }
            )
            sig = (prov, cand.model)
            if sig not in seen:
                seen.add(sig)
                candidates.append((prov, cand))
            return

        slot = PROVIDER_BY_ID.get(prov)
        key = _get_provider_api_key(prov, vault, settings)
        if not key:
            return
        avail, _ = tracker.is_provider_available(prov)
        if not avail:
            return
        adapted_model = _adapt_model_for_provider(target_model, prov)
        base_url = slot["base_url"] if slot else settings.base_url
        protocol = "openai"
        cand = settings.model_copy(
            update={
                "provider": protocol,
                "base_url": base_url,
                "api_key": key,
                "model": adapted_model,
            }
        )
        sig = (prov, adapted_model)
        if sig not in seen:
            seen.add(sig)
            candidates.append((prov, cand))

    # 1. Check matching rules (e.g. Gemini -> direct Google AI Studio first; DeepSeek -> direct DeepSeek first)
    rules = cfg.get("rules") or []
    matched_rule: dict[str, Any] | None = None
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        m_pat = str(rule.get("match") or "*").lower()
        if m_pat != "*" and m_pat in low_model:
            matched_rule = rule
            break

    if matched_rule is None:
        # Check if model implicitly belongs to Gemini or DeepSeek and prefer_direct_keys is True
        if cfg.get("prefer_direct_keys", True):
            if "gemini" in low_model:
                _add_candidate("gemini", model)
            elif "deepseek" in low_model:
                _add_candidate("deepseek", model)
            elif "claude" in low_model:
                _add_candidate("anthropic", model)
            elif "gpt-" in low_model:
                _add_candidate("openai", model)
    else:
        prim = str(matched_rule.get("primary_provider") or "")
        if prim:
            _add_candidate(prim, model)
        on_ex = str(matched_rule.get("on_exhausted") or "fallback_openrouter")
        fb_prov = str(matched_rule.get("fallback_provider") or "openrouter")
        fb_model = str(matched_rule.get("fallback_model") or model)
        if on_ex == "fallback_openrouter":
            _add_candidate("openrouter", model)
            if fb_model and fb_model != model:
                _add_candidate(fb_prov, fb_model)
        elif on_ex == "switch_model":
            _add_candidate(fb_prov, fb_model)
            _add_candidate("openrouter", fb_model)
        elif on_ex == "cooldown_skip":
            _add_candidate(fb_prov, fb_model)
        elif on_ex == "fallback_local":
            _add_candidate("ollama", fb_model or "qwen3.5:9b-32k")
        elif on_ex == "stop":
            # Do not add fallbacks if user explicitly wants to stop when primary is out
            if candidates:
                return candidates

    # 2. Always include the user's configured primary slot if not exhausted
    prim_prov = (settings.provider or "openai").lower()
    for p_id, slot in PROVIDER_BY_ID.items():
        if slot["base_url"].lower() in (settings.base_url or "").lower():
            prim_prov = p_id
            break
    avail, _ = tracker.is_provider_available(prim_prov)
    if avail:
        sig = (prim_prov, settings.model)
        if sig not in seen:
            seen.add(sig)
            candidates.append((prim_prov, settings))

    # 3. Add global fallback chain (OpenRouter -> Gemini -> DeepSeek -> Groq)
    for fb in cfg.get("global_fallback_order") or ["openrouter", "gemini", "deepseek"]:
        if fb == "ollama":
            continue  # Only add ollama if explicitly requested or no cloud keys exist
        _add_candidate(str(fb), model)

    if not candidates:
        return [(prim_prov, settings)]
    return candidates


def _is_quota_or_transient_error(exc: Exception) -> tuple[bool, int | None]:
    """Return ``(should_failover, status_code)`` for an exception raised during ``llm.ask``."""
    status = getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if status in (402, 429, 502, 503, 504):
        return True, int(status)
    if any(
        kw in msg
        for kw in (
            "429",
            "402",
            "rate limit",
            "ratelimit",
            "insufficient_quota",
            "insufficient balance",
            "balance is not enough",
            "out of credits",
            "credit balance is too low",
            "resource_exhausted",
            "quota exceeded",
            "too many requests",
            "payment required",
        )
    ):
        code = 402 if ("402" in msg or "balance" in msg or "credit" in msg) else 429
        return True, code
    return False, status


class RoutedLLM(BaseLLM):
    """Wraps a slot's LLM client with multi-API routing, live usage tracking, and automatic failover."""

    def __init__(
        self,
        settings: LLMSettings,
        data_dir: Path | None,
        inner_builder: Any,
    ) -> None:
        self.settings = settings
        self.data_dir = data_dir
        self._inner_builder = inner_builder
        self._primary = inner_builder(settings, data_dir)
        self.name = getattr(self._primary, "name", "openai")
        self.supports_native_tools = getattr(self._primary, "supports_native_tools", True)
        self.vision_available = getattr(self._primary, "vision_available", None)

    def _load_vault(self) -> CredentialVault | None:
        if self.data_dir is None:
            return None
        try:
            from nanomuse.vault.vault import CredentialVault

            return CredentialVault.in_dir(self.data_dir)
        except Exception:
            return None

    async def ask(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        on_delta: DeltaCallback | None = None,
        on_reasoning_delta: DeltaCallback | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        vault = self._load_vault()
        tracker = QuotaTracker(self.data_dir)
        routes = build_candidate_routes(self.settings, self.data_dir, vault)

        last_exc: Exception | None = None
        for idx, (prov_id, cand_settings) in enumerate(routes):
            client = (
                self._primary
                if (
                    cand_settings.base_url == self.settings.base_url
                    and cand_settings.model == self.settings.model
                    and cand_settings.api_key == self.settings.api_key
                )
                else self._inner_builder(cand_settings, self.data_dir)
            )
            try:
                resp = await client.ask(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    on_delta=on_delta,
                    on_reasoning_delta=on_reasoning_delta,
                    max_tokens=max_tokens,
                )
                usage = resp.usage or {}
                tracker.record_usage(
                    provider=prov_id,
                    model=cand_settings.model,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                )
                self.vision_available = getattr(client, "vision_available", self.vision_available)
                return resp
            except Exception as exc:
                last_exc = exc
                should_failover, status_code = _is_quota_or_transient_error(exc)
                if should_failover:
                    tracker.learn_from_error(
                        provider=prov_id,
                        model=cand_settings.model,
                        error_text=str(exc),
                        status_code=status_code,
                    )
                    if idx + 1 < len(routes):
                        next_prov, next_settings = routes[idx + 1]
                        logger.warning(
                            "Provider %s (%s) hit quota/rate limit (%s); failing over to %s (%s)",
                            prov_id,
                            cand_settings.model,
                            exc,
                            next_prov,
                            next_settings.model,
                        )
                        continue
                raise
            finally:
                if client is not self._primary:
                    try:
                        await client.close()
                    except Exception:
                        pass

        if last_exc is not None:
            raise last_exc
        return await self._primary.ask(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            on_delta=on_delta,
            on_reasoning_delta=on_reasoning_delta,
            max_tokens=max_tokens,
        )

    async def close(self) -> None:
        await self._primary.close()
