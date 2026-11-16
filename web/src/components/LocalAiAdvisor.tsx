import { Check, Copy, Cpu, HardDrive, Loader2, RefreshCw, Sparkles, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { LocalModelRecommendation, SystemCapabilities } from "../types";

export interface LocalAiAdvisorProps {
  onPickLocalPreset?: (presetId: string, modelId?: string) => void;
  onPickCloud?: () => void;
  selectedPreset?: string;
  selectedModel?: string;
  compact?: boolean;
}

function fitBadge(fit: LocalModelRecommendation["fit"]): { label: string; cls: string } {
  if (fit === "fits") {
    return { label: t("Fits well"), cls: "bg-ok/15 text-ok" };
  }
  if (fit === "tight") {
    return { label: t("Tight fit"), cls: "bg-warn/15 text-warn" };
  }
  return { label: t("Too heavy"), cls: "bg-surface-2 text-muted" };
}

export function LocalAiAdvisor({
  onPickLocalPreset,
  onPickCloud,
  selectedPreset,
  selectedModel,
  compact = false,
}: LocalAiAdvisorProps) {
  const [cap, setCap] = useState<SystemCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [copiedCmd, setCopiedCmd] = useState<string>("");
  const [showAllModels, setShowAllModels] = useState(false);

  const loadCapabilities = useCallback(() => {
    setLoading(true);
    setFailed(false);
    const nav = typeof navigator !== "undefined" ? (navigator as Navigator & { deviceMemory?: number }) : undefined;
    const ramGb = typeof nav?.deviceMemory === "number" ? nav.deviceMemory : undefined;
    const cores = typeof navigator?.hardwareConcurrency === "number" ? navigator.hardwareConcurrency : undefined;
    const mobile =
      typeof navigator !== "undefined"
        ? /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent || "")
        : false;
    api
      .systemCapabilities({ ramGb, cores, mobile })
      .then((res) => {
        setCap(res);
      })
      .catch(() => {
        setFailed(true);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    loadCapabilities();
  }, [loadCapabilities]);

  const copyCommand = (cmd: string) => {
    try {
      void navigator.clipboard?.writeText(cmd);
      setCopiedCmd(cmd);
      window.setTimeout(() => setCopiedCmd((prev) => (prev === cmd ? "" : prev)), 2000);
    } catch {
      // Clipboard may be unavailable in restricted contexts
    }
  };

  if (loading && !cap) {
    return (
      <div className="mb-3 rounded-2xl border border-border bg-surface-2/40 px-4 py-3 text-[12.5px] text-muted">
        <span className="inline-flex items-center gap-2">
          <Loader2 size={14} className="animate-spin text-accent" />
          {t("Checking what local AI models your device can handle...")}
        </span>
      </div>
    );
  }

  if (failed && !cap) {
    return (
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-border bg-surface-2/40 px-4 py-2.5 text-[12.5px] text-muted">
        <span>{t("Device capability check unavailable right now.")}</span>
        <button
          type="button"
          onClick={loadCapabilities}
          className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[11.5px] font-medium text-fg hover:border-accent"
        >
          <RefreshCw size={12} /> {t("Retry check")}
        </button>
      </div>
    );
  }

  if (!cap) return null;

  const { host, verdict, runtimes, recommended_models: models, client } = cap;
  const verdictTone =
    verdict.worth_it === "worth_it"
      ? "border-ok/35 bg-ok/[0.06]"
      : verdict.worth_it === "cloud_recommended"
        ? "border-warn/45 bg-warn/[0.08]"
        : "border-accent/30 bg-accent/[0.05]";

  const modelsToShow = showAllModels ? models : models.slice(0, compact ? 4 : 6);
  const runningRuntimes = runtimes.filter((r) => r.running);

  return (
    <div
      data-testid="local-ai-advisor"
      className="mb-4 rounded-2xl border border-border bg-surface p-3.5 shadow-sm sm:p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Cpu size={16} className="text-accent" />
          <span className="text-[13.5px] font-semibold">{t("Local AI & Device Check")}</span>
        </div>
        <button
          type="button"
          onClick={loadCapabilities}
          className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[11px] font-medium text-muted hover:border-accent hover:text-fg"
        >
          <RefreshCw size={11} /> {t("Re-scan")}
        </button>
      </div>

      {/* Hardware specs summary */}
      <div className="mt-2.5 flex flex-wrap gap-1.5 text-[11.5px]">
        <span className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 text-fg">
          <HardDrive size={11} className="text-muted" />
          {host.total_ram_gb > 0
            ? `${host.total_ram_gb} GB RAM (${host.free_ram_gb} GB ${t("free")})`
            : t("RAM unknown")}
        </span>
        <span className="rounded-full bg-surface-2 px-2.5 py-1 text-muted">
          {host.cpu_cores} {t("cores")} ({host.is_android ? "Android" : host.os} / {host.arch})
        </span>
        {host.gpu_name ? (
          <span className="rounded-full bg-surface-2 px-2.5 py-1 text-muted">
            GPU: {host.gpu_name}
            {host.vram_gb > 0 ? ` (${host.vram_gb} GB VRAM)` : ""}
          </span>
        ) : host.apple_silicon ? (
          <span className="rounded-full bg-surface-2 px-2.5 py-1 text-muted">
            Apple Silicon ({t("unified memory")})
          </span>
        ) : (
          <span className="rounded-full bg-surface-2 px-2.5 py-1 text-muted">{t("CPU only (no dedicated GPU)")}</span>
        )}
      </div>

      {client?.remote_client_mismatch && (
        <p className="mt-2 text-[11.5px] text-muted">
          {t(
            "You are viewing from a mobile browser ({clientRam} GB reported). Local models run on the server machine above, while on-device phone inference would hit phone limits.",
            { clientRam: client.ram_gb ? String(client.ram_gb) : "?" },
          )}
        </p>
      )}

      {/* Verdict banner: Is local AI worth it? */}
      <div className={`mt-3 rounded-xl border p-3 ${verdictTone}`}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-[13px] font-semibold text-fg">{t(verdict.headline)}</div>
            <p className="mt-1 text-[12px] leading-relaxed text-muted">{t(verdict.explanation)}</p>
            <p className="mt-1 text-[11.5px] font-medium text-fg/85">{t(verdict.tradeoff_summary)}</p>
          </div>
        </div>
        {onPickCloud && (
          <div className="mt-2.5 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onPickCloud}
              className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-[12px] font-medium text-accent-fg"
            >
              <Sparkles size={12} />
              {verdict.worth_it === "cloud_recommended"
                ? t("Use Cheap/Free Cloud Instead (OpenRouter)")
                : t("Compare with Cloud (OpenRouter)")}
            </button>
          </div>
        )}
      </div>

      {/* Detected local runtimes */}
      <div className="mt-3">
        <div className="mb-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-muted">
          {t("Local runtimes on this machine")}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {runtimes.map((rt) => (
            <div
              key={rt.id}
              className={`inline-flex items-center gap-1.5 rounded-xl border px-2.5 py-1.5 text-[11.5px] ${
                rt.running ? "border-ok/40 bg-ok/[0.06] text-fg" : "border-border bg-surface-2/40 text-muted"
              }`}
            >
              <span className={`h-2 w-2 rounded-full ${rt.running ? "bg-ok" : "bg-muted/40"}`} />
              <span className="font-medium">{rt.name}</span>
              {rt.running ? (
                <span className="text-[11px] text-ok">
                  {t("Running")} ({rt.models.length})
                </span>
              ) : (
                <a
                  href={rt.install_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-[11px] text-accent underline underline-offset-2"
                >
                  {t("Install")}
                </a>
              )}
            </div>
          ))}
        </div>

        {/* Already installed local models */}
        {runningRuntimes.some((r) => r.models.length > 0) && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-muted">{t("Already downloaded:")}</span>
            {runningRuntimes.flatMap((rt) =>
              rt.models.slice(0, 6).map((mId) => {
                const active = selectedPreset === rt.id && selectedModel === mId;
                return (
                  <button
                    key={`${rt.id}:${mId}`}
                    type="button"
                    onClick={() => onPickLocalPreset?.(rt.id, mId)}
                    className={`rounded-full px-2.5 py-1 font-mono text-[11px] transition ${
                      active
                        ? "bg-accent text-accent-fg"
                        : "border border-ok/40 bg-ok/[0.08] text-fg hover:border-accent"
                    }`}
                  >
                    <Zap size={10} className="mr-1 inline" />
                    {mId}
                  </button>
                );
              }),
            )}
          </div>
        )}
      </div>

      {/* Model size catalog matching this device */}
      {models.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11.5px] font-semibold uppercase tracking-wide text-muted">
              {t("What local models fit your hardware")}
            </span>
            {models.length > (compact ? 4 : 6) && (
              <button
                type="button"
                onClick={() => setShowAllModels((v) => !v)}
                className="text-[11.5px] font-medium text-accent hover:underline"
              >
                {showAllModels ? t("Show fewer") : t("Show all ({n})", { n: models.length })}
              </button>
            )}
          </div>

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {modelsToShow.map((m) => {
              const badge = fitBadge(m.fit);
              const active = selectedPreset === (m.preset || "ollama") && selectedModel === m.id;
              return (
                <div
                  key={m.id}
                  className={`flex flex-col justify-between rounded-xl border p-2.5 ${
                    active
                      ? "border-accent bg-accent/[0.06]"
                      : m.fit === "too_heavy"
                        ? "border-border/70 bg-surface-2/25 opacity-80"
                        : "border-border bg-surface-2/40"
                  }`}
                >
                  <div>
                    <div className="flex items-start justify-between gap-1.5">
                      <div className="min-w-0">
                        <div className="truncate text-[12.5px] font-semibold text-fg">{m.name}</div>
                        <div className="truncate font-mono text-[11px] text-muted">{m.id}</div>
                      </div>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-medium ${badge.cls}`}>
                        {badge.label}
                      </span>
                    </div>
                    <p className="mt-1 text-[11.5px] leading-snug text-muted">{t(m.summary)}</p>
                    <div className="mt-1.5 flex flex-wrap gap-1 text-[10.5px] text-muted">
                      <span className="rounded bg-surface px-1.5 py-0.5">{m.params}</span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        ~{m.min_ram_gb} GB RAM
                      </span>
                      <span className="rounded bg-surface px-1.5 py-0.5">
                        {t("Tools:")} {t(m.tool_quality)}
                      </span>
                    </div>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {onPickLocalPreset && (
                      <button
                        type="button"
                        onClick={() => onPickLocalPreset(m.preset || "ollama", m.id)}
                        className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${
                          active
                            ? "bg-accent text-accent-fg"
                            : "bg-fg text-bg hover:opacity-90"
                        }`}
                      >
                        {m.installed ? t("Use installed model") : t("Select preset")}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => copyCommand(m.pull_cmd)}
                      title={m.pull_cmd}
                      className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2 py-1 font-mono text-[10.5px] text-muted hover:border-accent hover:text-fg"
                    >
                      {copiedCmd === m.pull_cmd ? <Check size={10} className="text-ok" /> : <Copy size={10} />}
                      {copiedCmd === m.pull_cmd ? t("Copied") : m.pull_cmd}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
