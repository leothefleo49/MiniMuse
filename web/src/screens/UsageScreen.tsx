import { BarChart3, Cpu, Database, FileCode2, FileText, Film, Globe, Image as ImageIcon, MessageSquareCode, Pencil, Plus, RefreshCw, Sparkles, TerminalSquare, Trash2, Video } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { PageBar } from "../components/BackBar";
import { LoadError } from "../components/LoadError";
import { MuseCard, MuseDivider, MuseSectionLabel } from "../components/MuseList";
import { useStore } from "../store";
import { useT } from "../i18n";
import type { UsageLimitRow, UsageSummary } from "../types";
import { cx, relativeTime } from "../util";

/**
 * Usage — what this MiniMuse spent and did, read from the local ledger (`usage.jsonl`)
 * on the runtime, no account needed. Turns per model with their tokens (the thinking
 * tokens included), the dollar estimate off the provider rate cards, and what the turns
 * did: reading, files, commands, API calls, coding, tracking, and the pictures and
 * clips written or analyzed. Turns on a local model cost nothing, so the dollar total
 * only covers the priced cloud models.
 */

const RANGES: Array<{ id: number; label: string }> = [
  { id: 1, label: "24 hours" },
  { id: 7, label: "7 days" },
  { id: 30, label: "30 days" },
];

/** "29.0k" for the big counts, plain digits under it. */
function fmtTokens(n: number): string {
  return n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString();
}

function fmtCost(usd: number): string {
  return usd >= 1 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(4)}`;
}

/** "1.2 GB" / "430 MB" for the RAM lines. */
function fmtBytes(n: number): string {
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`;
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`;
  return `${Math.round(n / 1024)} KB`;
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="min-w-0 flex-1 px-3 py-3">
      <div className="truncate text-[15.5px] font-semibold leading-5">{value}</div>
      <div className="mt-0.5 truncate text-[12px] leading-4 text-muted">{label}</div>
      {sub && <div className="truncate text-[11.5px] leading-4 text-muted">{sub}</div>}
    </div>
  );
}

function StatLine({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div className="flex min-h-[44px] items-center gap-3 px-4 py-1.5">
      <span className="shrink-0 text-fg/80">{icon}</span>
      <span className="min-w-0 flex-1 truncate text-[15px] leading-[20px]">{label}</span>
      <span className="shrink-0 text-[14px] font-medium tabular-nums text-muted">{value}</span>
    </div>
  );
}

export function UsageScreen({ embedded = false }: { embedded?: boolean } = {}) {
  const { setTab } = useStore();
  const t = useT();
  const [days, setDays] = useState(7);
  const [model, setModel] = useState("");
  const [allModels, setAllModels] = useState<string[]>([]);
  const [data, setData] = useState<UsageSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const loadGen = useRef(0);

  const load = (d: number, m = "") => {
    const mine = ++loadGen.current;
    setRefreshing(true);
    return api
      .usage(d, m)
      .then((r) => {
        if (loadGen.current !== mine) return;
        setData(r);
        setLoadError(null);
        if (m === "") setAllModels(r.per_model.map((x) => x.model));
      })
      .catch((e: Error) => {
        if (loadGen.current !== mine) return;
        setLoadError(e.message);
      })
      .finally(() => {
        if (loadGen.current === mine) setRefreshing(false);
      });
  };

  useEffect(() => {
    void load(days, model);
    return () => {
      loadGen.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, model]);

  // the local Ollama's line for the chosen AI: total size and how much sits in VRAM
  const ollamaModel = data?.ollama?.models.find((m) => m.name === data.model || m.name === model);
  const localLines =
    ollamaModel && ollamaModel.size_bytes > 0
      ? [
          t("Loaded: {size}", { size: fmtBytes(ollamaModel.size_bytes) }),
          ...(ollamaModel.vram_bytes > 0 ? [t("In VRAM: {size}", { size: fmtBytes(ollamaModel.vram_bytes) })] : []),
        ]
      : [];

  const refreshBtn = (
    <button
      type="button"
      onClick={() => void load(days, model)}
      aria-label={t("Refresh")}
      title={t("Refresh")}
      className="flex h-9 w-9 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
    >
      <RefreshCw size={17} className={cx(refreshing && "animate-spin")} />
    </button>
  );

  return (
    <div className={embedded ? "-mx-4 flex flex-col" : "flex h-full flex-col"}>
      {!embedded ? (
        <PageBar
          title={t("Usage")}
          description={t("What your MiniMuse spent and did, kept on your machine. Nothing here needs an account.")}
          onBack={() => setTab("you")}
          backLabel={t("Settings")}
          actions={refreshBtn}
        />
      ) : (
        <div className="flex items-center justify-between gap-2 px-4 pb-2">
          <p className="text-[12.5px] leading-snug text-muted">
            {t("What your MiniMuse spent and did, kept on your machine. Nothing here needs an account.")}
          </p>
          {refreshBtn}
        </div>
      )}

      {/* the range */}
      <div className="mx-4 mb-1 flex rounded-full bg-surface-2/70 p-1 ring-1 ring-border/40" role="group" aria-label={t("Range")}>
        {RANGES.map((r) => (
          <button
            key={r.id}
            type="button"
            aria-pressed={days === r.id}
            onClick={() => setDays(r.id)}
            className={cx(
              "flex flex-1 items-center justify-center rounded-full py-1.5 text-[13.5px] font-medium transition",
              days === r.id ? "bg-fg text-bg" : "text-muted hover:text-fg",
            )}
          >
            {t(r.label)}
          </button>
        ))}
      </div>

      {/* the AI pick: every model, or one of them */}
      {allModels.length > 1 && (
        <div className="mx-4 mb-1 flex gap-1.5 overflow-x-auto no-scrollbar pb-0.5">
          {["", ...allModels].map((m) => (
            <button
              key={m || "all"}
              type="button"
              aria-pressed={model === m}
              onClick={() => setModel(m)}
              className={cx(
                "shrink-0 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition",
                model === m ? "bg-accent text-accent-fg" : "bg-surface-2/70 text-muted ring-1 ring-border/40 hover:text-fg",
              )}
            >
              {m || t("All models")}
            </button>
          ))}
        </div>
      )}

      <div className={embedded ? "pb-2 pt-2" : "flex-1 overflow-y-auto pb-8 pt-2"}>
        {loadError && data === null ? (
          <LoadError message={loadError} onRetry={() => void load(days, model)} />
        ) : !data ? (
          <div className="flex justify-center py-12 text-muted">
            <RefreshCw size={20} className="animate-spin" />
          </div>
        ) : data.turns === 0 ? (
          <div className="px-8 py-14 text-center">
            <BarChart3 size={30} className="mx-auto text-muted" />
            <p className="mt-3 text-[14.5px] font-medium">{t("Nothing counted yet in this range.")}</p>
            <p className="mt-1 text-[13px] leading-[19px] text-muted">{t("Send your MiniMuse a message, then come back: every turn is written to the ledger on your machine, the free local ones included.")}</p>
          </div>
        ) : (
          <>
            {/* the headline: turns, cost, tokens */}
            <MuseCard className="mb-1">
              <div className="flex divide-x divide-border/60">
                <Stat label={t("Turns")} value={data.turns.toLocaleString()} sub={data.tool_calls ? t("{n} tool calls", { n: data.tool_calls }) : undefined} />
                <Stat
                  label={t("Estimated cost")}
                  value={fmtCost(data.cost_usd)}
                  sub={t("priced cloud turns; local turns are free")}
                />
                <Stat label={t("Avg turn")} value={data.avg_duration_s ? `${data.avg_duration_s}s` : "—"} />
              </div>
              <MuseDivider inset={16} />
              <div className="flex divide-x divide-border/60">
                <Stat label={t("Prompt tokens")} value={fmtTokens(data.prompt_tokens)} sub={data.cached_tokens ? t("{n} cached", { n: fmtTokens(data.cached_tokens) }) : undefined} />
                <Stat label={t("Thinking tokens")} value={fmtTokens(data.reasoning_tokens)} />
                <Stat label={t("Reply tokens")} value={fmtTokens(data.completion_tokens)} />
              </div>
            </MuseCard>

            {/* a local AI: what it takes on this machine instead of a dollar figure */}
            {localLines.length > 0 && (
              <MuseCard className="mb-1">
                <StatLine label={t("On this computer")} value={localLines.join(" · ")} icon={<Cpu size={19} />} />
              </MuseCard>
            )}

            {/* what the turns did */}
            <MuseSectionLabel>{t("What it did")}</MuseSectionLabel>
            <MuseCard className="mb-1">
              <ActivityRows
                rows={[
                  { key: "reading", label: t("Reading (web, mail, screens)"), icon: <Globe size={19} />, n: data.activities.reading },
                  { key: "files_read", label: t("Files read"), icon: <FileText size={19} />, n: data.activities.files_read },
                  { key: "files_written", label: t("Files written"), icon: <FileCode2 size={19} />, n: data.activities.files_written },
                  { key: "commands", label: t("Commands (shell, PC)"), icon: <TerminalSquare size={19} />, n: data.activities.commands },
                  { key: "apis", label: t("API calls"), icon: <Database size={19} />, n: data.activities.apis },
                  { key: "coding", label: t("Coding"), icon: <MessageSquareCode size={19} />, n: data.activities.coding },
                  { key: "tracking", label: t("Tracking (memory, goals, reminders)"), icon: <Sparkles size={19} />, n: data.activities.tracking },
                ]}
                empty={t("No tool runs in this range.")}
              />
            </MuseCard>

            {/* pictures and clips */}
            <MuseSectionLabel>{t("Pictures and clips")}</MuseSectionLabel>
            <MuseCard className="mb-1">
              <ActivityRows
                rows={[
                  { key: "image_generation", label: t("Images generated"), icon: <ImageIcon size={19} />, n: data.media.image_generation },
                  { key: "image_editing", label: t("Images edited"), icon: <Pencil size={19} />, n: data.media.image_editing },
                  { key: "video_generation", label: t("Videos generated"), icon: <Video size={19} />, n: data.media.video_generation },
                  { key: "video_editing", label: t("Videos edited"), icon: <Film size={19} />, n: data.media.video_editing },
                  { key: "photo_analysis", label: t("Photos analyzed"), icon: <ImageIcon size={19} />, n: data.media.photo_analysis },
                  { key: "video_analysis", label: t("Videos analyzed"), icon: <Film size={19} />, n: data.media.video_analysis },
                ]}
                empty={t("No pictures or clips in this range.")}
              />
            </MuseCard>

            {/* per model */}
            <MuseSectionLabel>{t("By model")}</MuseSectionLabel>
            <MuseCard className="mb-1">
              {data.per_model.map((m, i) => {
                const sub = [
                  t("{n} turns", { n: m.turns }),
                  t("{n} prompt tokens", { n: fmtTokens(m.prompt_tokens) }),
                  t("{n} reply tokens", { n: fmtTokens(m.completion_tokens) }),
                  ...(m.reasoning_tokens ? [t("{n} thinking tokens", { n: fmtTokens(m.reasoning_tokens) })] : []),
                  ...(m.cached_tokens ? [t("{n} cached", { n: fmtTokens(m.cached_tokens) })] : []),
                ].join(" · ");
                return (
                  <div key={m.model}>
                    {i > 0 && <MuseDivider inset={16} />}
                    <div className="flex items-center justify-between gap-3 px-4 py-3">
                      <div className="min-w-0">
                        <div className="truncate text-[15px] font-medium leading-5">{m.model}</div>
                        <div className="mt-0.5 truncate text-[12px] leading-4 text-muted">{sub}</div>
                      </div>
                      <div className="shrink-0 text-[13.5px] font-medium tabular-nums text-muted">{m.cost_usd > 0 ? fmtCost(m.cost_usd) : t("free")}</div>
                    </div>
                  </div>
                );
              })}
              <MuseDivider inset={16} />
              <p className="px-4 py-2.5 text-[12.5px] leading-[18px] text-muted">{t("Costs are estimates off each provider's rate card. A local model bills nothing.")}</p>
            </MuseCard>

            {/* per day */}
            <MuseSectionLabel>{t("Turns per day")}</MuseSectionLabel>
            <MuseCard className="mb-1">
              <PerDay perDay={data.per_day} />
            </MuseCard>

            {/* recent turns */}
            <MuseSectionLabel>{t("Latest turns")}</MuseSectionLabel>
            <MuseCard>
              {data.recent.map((r, i) => (
                <div key={`${r.ts ?? i}-${r.model ?? ""}`}>
                  {i > 0 && <MuseDivider inset={16} />}
                  <div className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <div className="min-w-0">
                      <div className="truncate text-[14px] leading-5">{r.model ?? "?"}</div>
                      <div className="mt-0.5 text-[12px] leading-4 text-muted">
                        {relativeTime(r.ts ?? null)}
                        {r.source && r.source !== "person" ? ` · ${t(SOURCE_LABELS[r.source] ?? r.source)}` : ""}
                      </div>
                    </div>
                    <div className="shrink-0 text-[12.5px] tabular-nums text-muted">
                      {r.duration_s ? `${r.duration_s}s` : ""}
                      {r.cost_usd ? ` · ${fmtCost(r.cost_usd)}` : ""}
                    </div>
                  </div>
                </div>
              ))}
            </MuseCard>
          </>
        )}

        {/* the caps the runtime checks before every turn, and the reply caps it works under */}
        <MuseSectionLabel>{t("Limits")}</MuseSectionLabel>
        <LimitsCard />
      </div>
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  person: "you",
  device: "asked from a device",
  background: "on its own",
};

/** The usage limits, editable: a cap on dollars, tokens or requests per day, week or
 *  month, global or for one model. At the warning share it tells the person; at the cap
 *  it warns, stops the next turn, or asks once. The reply caps the agent itself works
 *  under are shown so both sides of "how much is it allowed" sit in one place. */
function LimitsCard() {
  const { toast, state } = useStore();
  const t = useT();
  const [rows, setRows] = useState<UsageLimitRow[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<UsageLimitRow>({
    name: "",
    scope: "global",
    model: "",
    unit: "cost",
    window: "month",
    amount: 10,
    warning_pct: 80,
    on_full: "stop",
  });
  const load = useCallback(
    () =>
      api
        .usageLimits()
        .then(setRows)
        .catch(() => setRows([])),
    [],
  );
  useEffect(() => {
    void load();
  }, [load]);
  const save = async (next: UsageLimitRow[]) => {
    try {
      const saved = await api.saveUsageLimits(next);
      setRows(saved);
      toast(t("Limits saved."));
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const remove = (id: string | undefined) => {
    if (!rows || !id) return;
    void save(rows.filter((r) => r.id !== id));
  };
  // the agent's own working caps, as the settings hold them
  const a = state.settings?.agent;
  const capsText = a
    ? [
        t("up to {steps} steps per reply", { steps: a.max_steps }),
        ...(a.max_continues ? [t("up to {n} continuations", { n: a.max_continues })] : []),
        ...(a.max_context_messages || a.max_context_tokens
          ? [
              t("context keeps {m} messages and {k} tokens", {
                m: a.max_context_messages ?? "—",
                k: a.max_context_tokens ? fmtTokens(a.max_context_tokens) : "—",
              }),
            ]
          : []),
      ].join(" · ")
    : t("the ones in the runtime's settings");
  const add = () => {
    if (!rows) return;
    const row = { ...draft, name: (draft.name ?? "").trim() || t(draft.unit === "cost" ? "Spending cap" : draft.unit === "tokens" ? "Token cap" : "Request cap") };
    if (!(row.amount > 0)) {
      toast(t("Set an amount above zero."));
      return;
    }
    if (row.scope === "model" && !(row.model ?? "").trim()) {
      toast(t("Name the model the cap is for."));
      return;
    }
    void save([...rows, { ...row, model: (row.model ?? "").trim() }]);
    setAdding(false);
    setDraft({ ...draft, name: "", model: "" });
  };
  const field = "min-w-0 flex-1 rounded-xl border border-border bg-bg px-2.5 py-1.5 text-[13px] outline-none focus:ring-2 focus:ring-accent/30";
  const windows: Array<{ value: UsageLimitRow["window"]; label: string }> = [
    { value: "day", label: t("a day") },
    { value: "week", label: t("a week") },
    { value: "month", label: t("a month") },
  ];
  const units: Array<{ value: UsageLimitRow["unit"]; label: string }> = [
    { value: "cost", label: t("dollars") },
    { value: "tokens", label: t("tokens") },
    { value: "requests", label: t("requests") },
  ];
  const actions: Array<{ value: UsageLimitRow["on_full"]; label: string }> = [
    { value: "warn", label: t("tell me") },
    { value: "stop", label: t("stop the turn") },
    { value: "confirm", label: t("ask first") },
  ];
  return (
    <MuseCard className="mb-1">
      {rows === null ? (
        <div className="flex justify-center py-5 text-muted">
          <RefreshCw size={18} className="animate-spin" />
        </div>
      ) : rows.length === 0 && !adding ? (
        <p className="px-4 py-4 text-[13.5px] leading-[19px] text-muted">
          {t("No caps set. Without one the agent spends what the work needs; a cap stops it at the line you chose.")}
        </p>
      ) : (
        rows.map((r, i) => (
          <div key={r.id ?? i}>
            {i > 0 && <MuseDivider inset={16} />}
            <div className="flex items-center gap-3 px-4 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-[14.5px] font-medium leading-5">{r.name || t("Spending cap")}</div>
                <div className="mt-0.5 truncate text-[12px] leading-4 text-muted">
                  {r.scope === "model" && r.model ? `${r.model} · ` : ""}
                  {t("at most {amount} {unit} {window}, warns at {pct}%, then {action}.", {
                    amount: r.unit === "cost" ? `$${r.amount}` : r.amount.toLocaleString(),
                    unit: t(units.find((u) => u.value === r.unit)?.label || r.unit),
                    window: t(windows.find((w) => w.value === r.window)?.label || r.window),
                    pct: r.warning_pct,
                    action: t(actions.find((a) => a.value === r.on_full)?.label || r.on_full),
                  })}
                </div>
              </div>
              <button
                type="button"
                onClick={() => remove(r.id)}
                aria-label={t("Remove this cap")}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
              >
                <Trash2 size={15} />
              </button>
            </div>
          </div>
        ))
      )}
      {adding ? (
        <div className="space-y-2 border-t border-border/60 px-4 py-3">
          <div className="flex gap-2">
            <select value={draft.scope} onChange={(e) => setDraft({ ...draft, scope: e.target.value as UsageLimitRow["scope"] })} className={field} aria-label={t("What the cap covers")}>
              <option value="global">{t("All models")}</option>
              <option value="model">{t("One model")}</option>
            </select>
            {draft.scope === "model" && (
              <input
                value={draft.model}
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                placeholder={t("model name")}
                aria-label={t("model name")}
                className={field}
              />
            )}
          </div>
          <div className="flex gap-2">
            <input
              type="number"
              min="0"
              step="any"
              value={draft.amount}
              onChange={(e) => setDraft({ ...draft, amount: Number(e.target.value) })}
              className={field}
              aria-label={t("The cap itself")}
            />
            <select value={draft.unit} onChange={(e) => setDraft({ ...draft, unit: e.target.value as UsageLimitRow["unit"] })} className={field} aria-label={t("Counted in")}>
              {units.map((u) => (
                <option key={u.value} value={u.value}>
                  {u.label}
                </option>
              ))}
            </select>
            <select value={draft.window} onChange={(e) => setDraft({ ...draft, window: e.target.value as UsageLimitRow["window"] })} className={field} aria-label={t("Over")}>
              {windows.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex gap-2">
            <label className="flex min-w-0 flex-1 items-center gap-2 text-[12.5px] text-muted">
              {t("warns at")}
              <input
                type="number"
                min="1"
                max="99"
                value={draft.warning_pct}
                onChange={(e) => setDraft({ ...draft, warning_pct: Math.max(1, Math.min(99, Number(e.target.value) || 80)) })}
                className={cx(field, "w-16 flex-none")}
              />
              %
            </label>
            <select value={draft.on_full} onChange={(e) => setDraft({ ...draft, on_full: e.target.value as UsageLimitRow["on_full"] })} className={field} aria-label={t("At the cap")}>
              {actions.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={() => setAdding(false)} className="rounded-xl px-3 py-1.5 text-[13.5px] text-muted hover:text-fg">
              {t("Cancel")}
            </button>
            <button type="button" onClick={add} className="rounded-xl bg-accent px-3.5 py-1.5 text-[13.5px] font-medium text-accent-fg">
              {t("Add the cap")}
            </button>
          </div>
        </div>
      ) : (
        <div className="px-4 pb-1 pt-2">
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-border py-2 text-[13.5px] font-medium text-fg/85 hover:bg-surface-2 active:scale-[0.98] transition"
          >
            <Plus size={15} /> {t("Add a cap")}
          </button>
        </div>
      )}
      <MuseDivider inset={16} />
      <p className="px-4 py-2.5 text-[12.5px] leading-[18px] text-muted">
        {t("Reply caps: {caps}. Every turn is written to the ledger on this machine, priced turns and free local ones alike.", { caps: capsText })}
      </p>
    </MuseCard>
  );
}


function ActivityRows({ rows, empty }: { rows: Array<{ key: string; label: string; icon: ReactNode; n: number | undefined }>; empty: string }) {
  const shown = rows.filter((r) => (r.n ?? 0) > 0);
  if (shown.length === 0) {
    return <p className="px-4 py-4 text-[13.5px] leading-[19px] text-muted">{empty}</p>;
  }
  return (
    <>
      {shown.map((r, i) => (
        <div key={r.key}>
          {i > 0 && <MuseDivider inset={52} />}
          <StatLine label={r.label} icon={r.icon} value={(r.n ?? 0).toLocaleString()} />
        </div>
      ))}
    </>
  );
}

/** The turns per day as small bars; the tallest day fills the row, empty days are a hairline. */
function PerDay({ perDay }: { perDay: UsageSummary["per_day"] }) {
  const max = Math.max(...perDay.map((d) => d.turns), 1);
  return (
    <div className="flex items-end gap-1.5 px-4 pb-3 pt-4">
      {perDay.map((d) => (
        <div key={d.day} className="flex min-w-0 flex-1 flex-col items-center gap-1">
          <div
            className="w-full rounded-sm bg-accent/70"
            style={{ height: `${Math.max(3, Math.round((d.turns / max) * 64))}px` }}
            title={`${d.day}: ${d.turns}`}
          />
          <span className="w-full truncate text-center text-[10.5px] leading-3 text-muted">{d.day.slice(5)}</span>
        </div>
      ))}
    </div>
  );
}
