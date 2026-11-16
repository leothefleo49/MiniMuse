import { Check, ChevronDown, ChevronUp, Info, Loader2, Plus, Search, Sparkles, X } from "lucide-react";
import { useMemo, useState } from "react";
import { t } from "../i18n";
import type { ModelCatalogInfoEntry } from "../types";

export interface ModelSearchFieldProps {
  value: string;
  onChange: (modelId: string) => void;
  models: string[];
  popular?: string[];
  recommended?: string;
  catalogInfo?: Record<string, ModelCatalogInfoEntry>;
  placeholder?: string;
  loading?: boolean;
  label?: string;
  compact?: boolean;
}

export const CLIENT_MODEL_CATALOG_INFO: Record<string, ModelCatalogInfoEntry> = {
  "deepseek/deepseek-v4.1-flash-1017": {
    company: "DeepSeek",
    tier: "normal",
    tier_label: "Normal Use (Best Default)",
    good_for: "Everyday agent tasks, multi-step tool calling, coding, fast reasoning, and web/email automation.",
    cost_input_1m: 0.14,
    cost_output_1m: 0.28,
    cost_summary: "$0.14 / 1M input · $0.28 / 1M output",
    free_tier_info: "Low-cost prepaid credits on DeepSeek or OpenRouter; automatic cache discount.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["chat", "default", "coding"],
  },
  "deepseek-v4.1-flash-1017": {
    company: "DeepSeek",
    tier: "normal",
    tier_label: "Normal Use (Best Default)",
    good_for: "Everyday agent tasks, multi-step tool calling, coding, fast reasoning, and web/email automation.",
    cost_input_1m: 0.14,
    cost_output_1m: 0.28,
    cost_summary: "$0.14 / 1M input · $0.28 / 1M output",
    free_tier_info: "Direct DeepSeek API credits; auto-falls back to OpenRouter or Gemini when credits run out.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["chat", "default", "coding"],
  },
  "deepseek/deepseek-v4.1-pro-1017": {
    company: "DeepSeek",
    tier: "heavy",
    tier_label: "Heavy Use",
    good_for: "Deep multi-file coding, complex math/logic, long research synthesis, and difficult debugging.",
    cost_input_1m: 0.55,
    cost_output_1m: 2.19,
    cost_summary: "$0.55 / 1M input · $2.19 / 1M output",
    free_tier_info: "Pay-as-you-go credits via DeepSeek or OpenRouter.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["heavy", "reasoning"],
  },
  "deepseek-v4.1-pro-1017": {
    company: "DeepSeek",
    tier: "heavy",
    tier_label: "Heavy Use",
    good_for: "Deep multi-file coding, complex math/logic, long research synthesis, and difficult debugging.",
    cost_input_1m: 0.55,
    cost_output_1m: 2.19,
    cost_summary: "$0.55 / 1M input · $2.19 / 1M output",
    free_tier_info: "Pay-as-you-go credits via DeepSeek API.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["heavy", "reasoning"],
  },
  "google/gemini-3.1-flash-lite": {
    company: "Google",
    tier: "light",
    tier_label: "Light Use",
    good_for: "Ultra-fast background tasks, memory tidying, quick Q&A, notifications, and high-volume light chat.",
    cost_input_1m: 0.075,
    cost_output_1m: 0.30,
    cost_summary: "$0.075 / 1M input · $0.30 / 1M output (Free tier on Google AI Studio)",
    free_tier_info: "Free up to 1,500 requests/day & 15 RPM on Google AI Studio key (resets daily at midnight PT).",
    quota_kind: "requests_and_tokens",
    reset_period: "daily",
    recommended_tasks: ["light", "background"],
    vision: true,
  },
  "gemini-3.1-flash-lite": {
    company: "Google",
    tier: "light",
    tier_label: "Light Use",
    good_for: "Ultra-fast background tasks, memory tidying, quick Q&A, notifications, and high-volume light chat.",
    cost_input_1m: 0.075,
    cost_output_1m: 0.30,
    cost_summary: "Free tier on Google AI Studio ($0.075 / $0.30 paid)",
    free_tier_info: "Free up to 1,500 requests/day & 15 RPM on Google AI Studio (resets daily).",
    quota_kind: "requests_and_tokens",
    reset_period: "daily",
    recommended_tasks: ["light", "background"],
    vision: true,
  },
  "google/gemini-2.5-flash": {
    company: "Google",
    tier: "normal",
    tier_label: "Normal Use (Vision & Multimodal)",
    good_for: "Multimodal vision, screen/phone GUI hands, long 1M-token documents, and fast everyday assistant work.",
    cost_input_1m: 0.15,
    cost_output_1m: 0.60,
    cost_summary: "$0.15 / 1M input · $0.60 / 1M output (Free tier on Google AI Studio)",
    free_tier_info: "Free daily request & token allowance with a Google AI Studio key (resets daily).",
    quota_kind: "requests_and_tokens",
    reset_period: "daily",
    recommended_tasks: ["chat", "vision", "hands"],
    vision: true,
  },
  "gemini-2.5-flash": {
    company: "Google",
    tier: "normal",
    tier_label: "Normal Use (Vision & Multimodal)",
    good_for: "Multimodal vision, screen/phone GUI hands, long 1M-token documents, and fast everyday assistant work.",
    cost_input_1m: 0.15,
    cost_output_1m: 0.60,
    cost_summary: "Free tier on Google AI Studio ($0.15 / $0.60 paid)",
    free_tier_info: "Free daily request & token allowance on Google AI Studio (resets daily).",
    quota_kind: "requests_and_tokens",
    reset_period: "daily",
    recommended_tasks: ["chat", "vision", "hands"],
    vision: true,
  },
  "google/gemini-2.5-pro": {
    company: "Google",
    tier: "heavy",
    tier_label: "Heavy Use",
    good_for: "Complex multimodal reasoning, huge codebases (1M+ context), deep analysis, and hard screen tasks.",
    cost_input_1m: 1.25,
    cost_output_1m: 10.0,
    cost_summary: "$1.25 / 1M input · $10.00 / 1M output",
    free_tier_info: "Limited free daily requests on Google AI Studio (50 RPD / 2 RPM); resets daily.",
    quota_kind: "requests_and_tokens",
    reset_period: "daily",
    recommended_tasks: ["heavy", "vision"],
    vision: true,
  },
  "gemini-2.5-pro": {
    company: "Google",
    tier: "heavy",
    tier_label: "Heavy Use",
    good_for: "Complex multimodal reasoning, huge codebases (1M+ context), deep analysis, and hard screen tasks.",
    cost_input_1m: 1.25,
    cost_output_1m: 10.0,
    cost_summary: "Limited free tier on Google AI Studio ($1.25 / $10.00 paid)",
    free_tier_info: "Free up to 50 requests/day on Google AI Studio; resets daily.",
    quota_kind: "requests_and_tokens",
    reset_period: "daily",
    recommended_tasks: ["heavy", "vision"],
    vision: true,
  },
  "openai/gpt-5-mini": {
    company: "OpenAI",
    tier: "normal",
    tier_label: "Normal Use",
    good_for: "Fast general assistant, reliable tool calling, vision, and structured JSON output.",
    cost_input_1m: 0.25,
    cost_output_1m: 2.00,
    cost_summary: "$0.25 / 1M input · $2.00 / 1M output",
    free_tier_info: "Prepaid credits on OpenAI Platform or OpenRouter.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["chat", "vision"],
    vision: true,
  },
  "gpt-5-mini": {
    company: "OpenAI",
    tier: "normal",
    tier_label: "Normal Use",
    good_for: "Fast general assistant, reliable tool calling, vision, and structured JSON output.",
    cost_input_1m: 0.25,
    cost_output_1m: 2.00,
    cost_summary: "$0.25 / 1M input · $2.00 / 1M output",
    free_tier_info: "Prepaid credits on OpenAI Platform.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["chat", "vision"],
    vision: true,
  },
  "openai/gpt-5": {
    company: "OpenAI",
    tier: "heavy",
    tier_label: "Heavy Use",
    good_for: "Flagship reasoning, complex software engineering, computer/browser control, and high-stakes tasks.",
    cost_input_1m: 1.25,
    cost_output_1m: 10.0,
    cost_summary: "$1.25 / 1M input · $10.00 / 1M output",
    free_tier_info: "Prepaid credits on OpenAI Platform or OpenRouter.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["heavy", "vision", "hands"],
    vision: true,
  },
  "anthropic/claude-sonnet-4.5": {
    company: "Anthropic",
    tier: "heavy",
    tier_label: "Heavy Use (Top Coding & Agent)",
    good_for: "Best-in-class coding, computer use, nuanced writing, and complex multi-step agent workflows.",
    cost_input_1m: 3.00,
    cost_output_1m: 15.00,
    cost_summary: "$3.00 / 1M input · $15.00 / 1M output",
    free_tier_info: "Prepaid credits on Anthropic Console or OpenRouter.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["heavy", "coding", "hands"],
    vision: true,
  },
  "anthropic/claude-haiku-4.5": {
    company: "Anthropic",
    tier: "light",
    tier_label: "Light / Normal Use",
    good_for: "Fast coding edits, quick tool dispatch, vision, and responsive everyday chat.",
    cost_input_1m: 0.80,
    cost_output_1m: 4.00,
    cost_summary: "$0.80 / 1M input · $4.00 / 1M output",
    free_tier_info: "Prepaid credits on Anthropic Console or OpenRouter.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["light", "chat"],
    vision: true,
  },
  "qwen/qwen3.5-plus": {
    company: "Alibaba / Qwen",
    tier: "normal",
    tier_label: "Normal Use",
    good_for: "Strong bilingual (English/Chinese) agent tasks, coding, and tool calling at low cost.",
    cost_input_1m: 0.20,
    cost_output_1m: 0.60,
    cost_summary: "$0.20 / 1M input · $0.60 / 1M output",
    free_tier_info: "Prepaid credits via OpenRouter or DashScope.",
    quota_kind: "credits",
    reset_period: "never",
    recommended_tasks: ["chat"],
    vision: true,
  },
  "qwen3.5:9b-32k": {
    company: "Local (Alibaba Qwen via Ollama)",
    tier: "normal",
    tier_label: "Local Offline (8 GB+ RAM)",
    good_for: "100% private offline chat, local tool calling, and zero-cost fallback when offline or out of API credits.",
    cost_input_1m: 0.0,
    cost_output_1m: 0.0,
    cost_summary: "$0.00 (Runs 100% locally on your hardware)",
    free_tier_info: "Unlimited free local usage; no internet or API key required.",
    quota_kind: "local",
    reset_period: "unlimited",
    recommended_tasks: ["local", "chat"],
    local: true,
  },
};

const POPULAR_CLIENT_FALLBACK = [
  "deepseek/deepseek-v4.1-flash-1017",
  "deepseek-v4.1-flash-1017",
  "google/gemini-2.5-flash",
  "google/gemini-3.1-flash-lite",
  "deepseek/deepseek-v4.1-pro-1017",
  "deepseek-v4.1-pro-1017",
  "anthropic/claude-sonnet-4.5",
  "openai/gpt-5-mini",
  "openai/gpt-5",
  "google/gemini-2.5-pro",
  "anthropic/claude-haiku-4.5",
  "qwen/qwen3.5-plus",
  "gemini-2.5-flash",
  "gemini-3.1-flash-lite",
  "gemini-2.5-pro",
  "gpt-5-mini",
  "gpt-5",
  "claude-sonnet-4-5",
  "qwen3.5:9b-32k",
  "qwen3:8b",
  "gemma3:4b",
];

function scoreFallbackPopularity(id: string): number {
  const low = id.toLowerCase();
  const idx = POPULAR_CLIENT_FALLBACK.findIndex((p) => low === p || low.endsWith(`/${p}`));
  if (idx !== -1) return idx;
  if (/deepseek-v4\.1|claude-(sonnet-4|haiku-4|3\.7)|gpt-5|gpt-4o|gemini-3\.1|gemini-2\.5|qwen3\.5|qwen3|llama-3\.3|gemma3/.test(low)) {
    return 100;
  }
  return -1;
}

export function splitPopularAndAlphabetical(
  models: string[],
  popular?: string[],
  recommended?: string,
): { popularList: string[]; otherAlphabetical: string[] } {
  const seen = new Set<string>();
  const clean: string[] = [];
  for (const m of models) {
    const trimmed = (m || "").trim();
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed);
      clean.push(trimmed);
    }
  }
  const popSet = new Set<string>();
  const popularList: string[] = [];
  if (recommended && seen.has(recommended)) {
    popSet.add(recommended);
    popularList.push(recommended);
  }
  if (Array.isArray(popular) && popular.length > 0) {
    for (const m of popular) {
      if (seen.has(m) && !popSet.has(m)) {
        popSet.add(m);
        popularList.push(m);
      }
    }
  } else {
    const scored = clean
      .filter((m) => !popSet.has(m))
      .map((m) => ({ m, s: scoreFallbackPopularity(m) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => a.s - b.s || a.m.localeCompare(b.m));
    for (const item of scored) {
      popSet.add(item.m);
      popularList.push(item.m);
    }
  }
  const otherAlphabetical = clean
    .filter((m) => !popSet.has(m))
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  return { popularList, otherAlphabetical };
}

export function lookupModelCatalogInfo(
  modelId: string,
  catalogInfo?: Record<string, ModelCatalogInfoEntry>,
): ModelCatalogInfoEntry | null {
  const m = (modelId || "").trim();
  if (!m) return null;
  if (catalogInfo?.[m]) return catalogInfo[m];
  if (CLIENT_MODEL_CATALOG_INFO[m]) return CLIENT_MODEL_CATALOG_INFO[m];
  const bare = m.includes("/") ? m.split("/").slice(1).join("/") : m;
  if (catalogInfo?.[bare]) return catalogInfo[bare];
  if (CLIENT_MODEL_CATALOG_INFO[bare]) return CLIENT_MODEL_CATALOG_INFO[bare];
  const low = m.toLowerCase();
  if (low.includes("deepseek")) {
    const isPro = low.includes("pro") || low.includes("r1") || low.includes("reasoner");
    return {
      company: "DeepSeek",
      tier: isPro ? "heavy" : "normal",
      tier_label: isPro ? "Heavy Use" : "Normal Use",
      good_for: isPro ? "Deep reasoning, math, and multi-file coding." : "Fast everyday chat, coding, and tool calling.",
      cost_input_1m: isPro ? 0.55 : 0.14,
      cost_output_1m: isPro ? 2.19 : 0.28,
      cost_summary: isPro ? "$0.55 / 1M in · $2.19 / 1M out" : "$0.14 / 1M in · $0.28 / 1M out",
      free_tier_info: "Prepaid credits via DeepSeek API or OpenRouter.",
      quota_kind: "credits",
      reset_period: "never",
    };
  }
  if (low.includes("gemini")) {
    const isLite = low.includes("lite");
    const isPro = low.includes("pro");
    return {
      company: "Google",
      tier: isLite ? "light" : isPro ? "heavy" : "normal",
      tier_label: isLite ? "Light Use" : isPro ? "Heavy Use" : "Normal Use",
      good_for: isLite
        ? "Ultra-fast background tasks, memory tidying, and light Q&A."
        : "Multimodal vision, long context (1M tokens), and agent tool calling.",
      cost_input_1m: isLite ? 0.075 : isPro ? 1.25 : 0.15,
      cost_output_1m: isLite ? 0.30 : isPro ? 10.0 : 0.60,
      cost_summary: isLite
        ? "$0.075 / 1M in · $0.30 / 1M out (Free tier on Google AI Studio)"
        : isPro
          ? "$1.25 / 1M in · $10.00 / 1M out"
          : "$0.15 / 1M in · $0.60 / 1M out (Free tier on Google AI Studio)",
      free_tier_info: "Free daily requests/tokens on Google AI Studio key (resets daily at midnight PT).",
      quota_kind: "requests_and_tokens",
      reset_period: "daily",
      vision: true,
    };
  }
  if (low.includes("claude")) {
    const isHaiku = low.includes("haiku");
    return {
      company: "Anthropic",
      tier: isHaiku ? "light" : "heavy",
      tier_label: isHaiku ? "Light / Normal Use" : "Heavy Use (Coding & Agent)",
      good_for: isHaiku ? "Fast coding edits and responsive everyday chat." : "Complex software engineering, computer use, and agent workflows.",
      cost_input_1m: isHaiku ? 0.80 : 3.00,
      cost_output_1m: isHaiku ? 4.00 : 15.00,
      cost_summary: isHaiku ? "$0.80 / 1M in · $4.00 / 1M out" : "$3.00 / 1M in · $15.00 / 1M out",
      free_tier_info: "Prepaid credits via Anthropic Console or OpenRouter.",
      quota_kind: "credits",
      reset_period: "never",
      vision: true,
    };
  }
  if (low.includes("gpt-")) {
    const isMini = low.includes("mini") || low.includes("nano");
    return {
      company: "OpenAI",
      tier: isMini ? "normal" : "heavy",
      tier_label: isMini ? "Normal Use" : "Heavy Use",
      good_for: isMini ? "Fast general assistant, vision, and reliable tool calling." : "Flagship reasoning, coding, and multimodal tasks.",
      cost_input_1m: isMini ? 0.25 : 1.25,
      cost_output_1m: isMini ? 2.00 : 10.00,
      cost_summary: isMini ? "$0.25 / 1M in · $2.00 / 1M out" : "$1.25 / 1M in · $10.00 / 1M out",
      free_tier_info: "Prepaid credits via OpenAI Platform or OpenRouter.",
      quota_kind: "credits",
      reset_period: "never",
      vision: true,
    };
  }
  if (low.includes(":") || low.startsWith("qwen3:") || low.startsWith("llama") || low.startsWith("gemma")) {
    return {
      company: "Local (Ollama / LM Studio)",
      tier: "normal",
      tier_label: "Local Offline",
      good_for: "100% private offline chat and zero-cost local execution on your hardware.",
      cost_input_1m: 0,
      cost_output_1m: 0,
      cost_summary: "$0.00 (100% Local)",
      free_tier_info: "Unlimited local usage; no API key or internet required.",
      quota_kind: "local",
      reset_period: "unlimited",
      local: true,
    };
  }
  return null;
}

/** Mobile-safe searchable model input & browser replacing native <datalist>, which many mobile WebViews ignore. */
export function ModelSearchField({
  value,
  onChange,
  models,
  popular,
  recommended,
  catalogInfo,
  placeholder,
  loading = false,
  compact = false,
}: ModelSearchFieldProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [showAllTail, setShowAllTail] = useState(false);
  const [expandedModel, setExpandedModel] = useState<string | null>(null);

  const { popularList, otherAlphabetical } = useMemo(
    () => splitPopularAndAlphabetical(models, popular, recommended),
    [models, popular, recommended],
  );

  const q = query.trim().toLowerCase();
  const filteredPopular = useMemo(
    () => (q ? popularList.filter((m) => m.toLowerCase().includes(q)) : popularList),
    [popularList, q],
  );
  const filteredOther = useMemo(
    () => (q ? otherAlphabetical.filter((m) => m.toLowerCase().includes(q)) : otherAlphabetical),
    [otherAlphabetical, q],
  );

  const visibleOther = q || showAllTail ? filteredOther : filteredOther.slice(0, 40);
  const totalCount = popularList.length + otherAlphabetical.length;

  const pick = (m: string) => {
    onChange(m);
    setOpen(false);
  };

  const selectedInfo = lookupModelCatalogInfo(value, catalogInfo);
  const inspectedModel = expandedModel || value;
  const inspectedInfo = lookupModelCatalogInfo(inspectedModel, catalogInfo);

  return (
    <div className="space-y-2">
      <div className="flex items-stretch gap-1.5">
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          aria-label={t("Model ID")}
          className="min-w-0 flex-1 rounded-xl border border-border bg-bg px-3 py-2 font-mono text-[13px] text-fg focus:border-accent focus:outline-none"
        />
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="inline-flex shrink-0 items-center gap-1 rounded-xl border border-border bg-surface-2 px-3 py-2 text-[12.5px] font-medium text-fg hover:border-accent"
        >
          {loading ? <Loader2 size={13} className="animate-spin text-accent" /> : <Search size={13} />}
          <span>{open ? t("Hide list") : t("Browse ({n})", { n: totalCount })}</span>
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        </button>
      </div>

      {/* Quick popular chips right below input when closed or compact, with expandable cost/use-case details */}
      {!open && popularList.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-medium text-muted">{t("Suggested:")}</span>
            {popularList.slice(0, compact ? 5 : 7).map((m) => {
              const active = m === value;
              const info = lookupModelCatalogInfo(m, catalogInfo);
              const isExpanded = expandedModel === m;
              return (
                <div key={m} className="inline-flex items-center rounded-full border border-border bg-surface-2 p-0.5">
                  <button
                    type="button"
                    onClick={() => onChange(m)}
                    className={`rounded-full px-2.5 py-0.5 font-mono text-[11px] transition ${
                      active ? "bg-accent text-accent-fg font-medium" : "text-fg hover:text-accent"
                    }`}
                  >
                    {m}
                  </button>
                  {info && (
                    <button
                      type="button"
                      onClick={() => setExpandedModel(isExpanded ? null : m)}
                      aria-label={t("Details for {model}", { model: m })}
                      title={t("Show cost, company & recommended use")}
                      className={`rounded-full px-1.5 py-0.5 text-[10px] transition ${
                        isExpanded ? "bg-accent/20 text-accent font-semibold" : "text-muted hover:text-fg"
                      }`}
                    >
                      <Info size={11} />
                    </button>
                  )}
                </div>
              );
            })}
            {selectedInfo && !expandedModel && (
              <button
                type="button"
                onClick={() => setExpandedModel(value)}
                className="inline-flex items-center gap-1 text-[11px] text-accent hover:underline"
              >
                <Info size={11} /> {t("Cost & use-case details")}
              </button>
            )}
          </div>

          {expandedModel && inspectedInfo && (
            <div className="rounded-2xl border border-accent/30 bg-surface p-3 text-[12px] space-y-1.5 shadow-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-semibold text-fg">{inspectedModel}</span>
                  <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10.5px] font-medium text-muted">
                    {inspectedInfo.company}
                  </span>
                  <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10.5px] font-semibold text-accent">
                    {t(inspectedInfo.tier_label)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5">
                  {inspectedModel !== value && (
                    <button
                      type="button"
                      onClick={() => onChange(inspectedModel)}
                      className="rounded-full bg-accent px-2.5 py-0.5 text-[11px] font-medium text-accent-fg"
                    >
                      {t("Select")}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setExpandedModel(null)}
                    className="rounded-full p-0.5 text-muted hover:text-fg"
                    aria-label={t("Close details")}
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>
              <div className="text-fg/90">
                <span className="font-medium text-muted">{t("Best for:")} </span>
                {t(inspectedInfo.good_for)}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11.5px] text-muted">
                <span>
                  <strong className="text-fg">{t("Cost:")}</strong> {inspectedInfo.cost_summary}
                </span>
                <span>
                  <strong className="text-fg">{t("Quota / Free tier:")}</strong> {t(inspectedInfo.free_tier_info)}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Mobile-friendly inline searchable model browser */}
      {open && (
        <div
          data-testid="model-search-browser"
          className="rounded-2xl border border-border bg-bg p-2.5 shadow-sm"
        >
          <div className="flex items-center gap-2 rounded-xl border border-border bg-surface px-2.5 py-2 focus-within:border-accent">
            <Search size={15} className="shrink-0 text-muted" />
            <input
              type="search"
              inputMode="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("Search all models...")}
              aria-label={t("Search all models...")}
              className="min-w-0 flex-1 bg-transparent text-[13.5px] text-fg placeholder:text-muted focus:outline-none"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("Clear search")}
                className="rounded-full p-1 text-muted hover:bg-surface-2 hover:text-fg"
              >
                <X size={14} />
              </button>
            )}
          </div>

          <div className="mt-2 max-h-[300px] space-y-2.5 overflow-y-auto overscroll-contain pr-1">
            {totalCount === 0 && (
              <div className="px-2 py-3 text-[12.5px] text-muted">
                {loading ? t("Loading models...") : t("Type any model ID above, or fetch models from your provider.")}
              </div>
            )}

            {totalCount > 0 && filteredPopular.length === 0 && filteredOther.length === 0 && (
              <div className="space-y-2 px-2 py-3 text-[12.5px] text-muted">
                <div>{t("No model matches \"{q}\"", { q: query.trim() })}</div>
                <button
                  type="button"
                  onClick={() => pick(query.trim())}
                  className="inline-flex items-center gap-1 rounded-full bg-accent px-3 py-1.5 text-[12px] font-medium text-accent-fg"
                >
                  <Plus size={12} /> {t("Use \"{id}\" anyway", { id: query.trim() })}
                </button>
              </div>
            )}

            {filteredPopular.length > 0 && (
              <div>
                <div className="mb-1 flex items-center justify-between gap-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-accent">
                  <span className="flex items-center gap-1">
                    <Sparkles size={11} />
                    {t("Popular & recommended")}
                  </span>
                  <span className="text-[10px] font-normal normal-case text-muted">
                    {t("Tap ⓘ to expand cost, company & use-case")}
                  </span>
                </div>
                <div className="space-y-1">
                  {filteredPopular.map((m) => {
                    const active = m === value;
                    const isRec = m === recommended || m === popularList[0];
                    const info = lookupModelCatalogInfo(m, catalogInfo);
                    const isExp = expandedModel === m;
                    return (
                      <div key={m} className="rounded-xl border border-border/50 bg-surface/50">
                        <div
                          className={`flex min-h-[40px] w-full items-center justify-between gap-2 rounded-xl px-2.5 py-1.5 text-left transition ${
                            active ? "bg-accent/15 text-fg" : "hover:bg-surface-2"
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => pick(m)}
                            className="min-w-0 flex-1 text-left"
                          >
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="truncate font-mono text-[12.5px] font-medium">{m}</span>
                              {info && (
                                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10px] text-muted">
                                  {info.company} · {t(info.tier_label)}
                                </span>
                              )}
                            </div>
                          </button>
                          <span className="flex shrink-0 items-center gap-1.5">
                            {isRec && (
                              <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10.5px] font-medium text-accent">
                                {t("Recommended")}
                              </span>
                            )}
                            {info && (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setExpandedModel(isExp ? null : m);
                                }}
                                aria-label={t("Expand details for {model}", { model: m })}
                                className="rounded-full p-1 text-muted hover:bg-surface-2 hover:text-accent"
                              >
                                <Info size={14} />
                              </button>
                            )}
                            {active && <Check size={14} className="text-accent" />}
                          </span>
                        </div>
                        {isExp && info && (
                          <div className="border-t border-border/50 bg-surface px-3 py-2 text-[11.5px] space-y-1 rounded-b-xl">
                            <div className="text-fg/90">
                              <strong className="text-muted">{t("Good for:")}</strong> {t(info.good_for)}
                            </div>
                            <div className="text-muted">
                              <strong className="text-fg">{t("Cost:")}</strong> {info.cost_summary}
                            </div>
                            <div className="text-muted">
                              <strong className="text-fg">{t("Free tier / Quota:")}</strong> {t(info.free_tier_info)}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {filteredOther.length > 0 && (
              <div>
                <div className="mb-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {t("All models (A-Z)")} ({filteredOther.length})
                </div>
                <div className="space-y-1">
                  {visibleOther.map((m) => {
                    const active = m === value;
                    return (
                      <button
                        key={m}
                        type="button"
                        onClick={() => pick(m)}
                        className={`flex min-h-[40px] w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left transition ${
                          active ? "bg-accent/15 text-fg" : "hover:bg-surface-2 active:bg-surface-2"
                        }`}
                      >
                        <span className="min-w-0 truncate font-mono text-[12.5px]">{m}</span>
                        {active && <Check size={14} className="shrink-0 text-accent" />}
                      </button>
                    );
                  })}
                </div>
                {!q && !showAllTail && filteredOther.length > 40 && (
                  <button
                    type="button"
                    onClick={() => setShowAllTail(true)}
                    className="mt-1.5 w-full rounded-xl border border-border py-1.5 text-[12px] font-medium text-accent hover:bg-surface-2"
                  >
                    {t("Show remaining {n} models", { n: filteredOther.length - 40 })}
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
