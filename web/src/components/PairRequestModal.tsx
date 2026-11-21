import { Check, Copy, Globe, Loader2, QrCode, RefreshCw, Smartphone, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { PairRequestEvent } from "../types";

/**
 * App-wide pairing code & QR popup modal.
 * Opens automatically over whichever screen is currently open whenever:
 *  - Another MiniMuse device requests to pair with this device (`nanomuse:pair-request` WS event), or
 *  - The user clicks "Show pairing code" (`nanomuse:open-pair-code` event).
 *
 * Automatically upgrades its QR & link when the Free Cloudflare Quick Tunnel comes online,
 * and auto-closes with a confirmation checkmark the instant pairing completes!
 */
export function PairRequestModal() {
  const t = useT();
  const { toast } = useStore();
  const [info, setInfo] = useState<PairRequestEvent | null>(null);
  const [copied, setCopied] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [enablingRemote, setEnablingRemote] = useState(false);
  const [pairedName, setPairedName] = useState<string | null>(null);

  const refreshCode = useCallback(async () => {
    setRefreshing(true);
    try {
      const next = await api.syncPairCode();
      setInfo((prev) => ({
        ...(prev ? { from_id: prev.from_id, from_name: prev.from_name, from_kind: prev.from_kind } : {}),
        ...next,
      }));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setRefreshing(false);
    }
  }, [toast]);

  const enableRemote = async () => {
    setEnablingRemote(true);
    try {
      await api.personalCloudStart(true);
      await refreshCode();
      toast(t("Personal Cloud remote tunnel is live! Your QR & link now work from anywhere."));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setEnablingRemote(false);
    }
  };

  useEffect(() => {
    const onPairRequest = (ev: Event) => {
      const detail = (ev as CustomEvent<PairRequestEvent>).detail;
      if (!detail || !detail.code) return;
      setPairedName(null);
      setInfo(detail);
    };
    const onOpenLocal = (ev: Event) => {
      const detail = (ev as CustomEvent<PairRequestEvent | undefined>).detail;
      setPairedName(null);
      if (detail?.code) {
        setInfo(detail);
      } else {
        void refreshCode();
      }
    };
    const onTunnelReady = () => {
      // If the popup is open and wasn't remote-ready yet, refresh the code & QR so it carries the new HTTPS URL!
      setInfo((cur) => {
        if (cur && !cur.remote_ready) {
          void refreshCode();
        }
        return cur;
      });
    };
    const onPaired = (ev: Event) => {
      const detail = (ev as CustomEvent<{ name?: string }>).detail;
      setInfo((cur) => {
        if (!cur) return null;
        setPairedName(detail?.name || cur.from_name || t("your other device"));
        window.setTimeout(() => {
          setInfo(null);
          setPairedName(null);
        }, 1700);
        return cur;
      });
    };
    window.addEventListener("nanomuse:pair-request", onPairRequest);
    window.addEventListener("nanomuse:open-pair-code", onOpenLocal);
    window.addEventListener("nanomuse:personal-cloud", onTunnelReady);
    window.addEventListener("nanomuse:peer-paired", onPaired);
    return () => {
      window.removeEventListener("nanomuse:pair-request", onPairRequest);
      window.removeEventListener("nanomuse:open-pair-code", onOpenLocal);
      window.removeEventListener("nanomuse:personal-cloud", onTunnelReady);
      window.removeEventListener("nanomuse:peer-paired", onPaired);
    };
  }, [refreshCode, t]);

  useEffect(() => {
    if (!info) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setInfo(null);
        setPairedName(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [info]);

  if (!info) return null;

  const copyLink = async () => {
    if (!info.pair_uri) return;
    try {
      await navigator.clipboard.writeText(info.pair_uri);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
      toast(t("Pairing link copied."));
    } catch {
      toast(t("Could not copy automatically. Select the link below to copy it."));
    }
  };

  return (
    <div
      className="fixed inset-0 z-[95] flex items-center justify-center bg-black/60 p-4 backdrop-blur-[2px]"
      onClick={() => {
        setInfo(null);
        setPairedName(null);
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t("Pair device")}
        className="rise w-full max-w-sm overflow-hidden rounded-3xl border border-border bg-surface p-5 shadow-2xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        {pairedName ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-500">
              <Check size={28} />
            </span>
            <h3 className="text-[17px] font-semibold text-fg">
              {t("Paired with {peer}!", { peer: pairedName })}
            </h3>
            <p className="text-[13px] text-muted">
              {t("Syncing your chats, avatar, apps, and Cloud Link automatically…")}
            </p>
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <h3 className="flex items-center gap-2 text-[16px] font-semibold text-fg">
                  {info.from_name ? (
                    <Smartphone size={18} className="shrink-0 text-accent" />
                  ) : (
                    <QrCode size={18} className="shrink-0 text-accent" />
                  )}
                  <span className="truncate">
                    {info.from_name
                      ? t("{name} wants to pair", { name: info.from_name })
                      : t("Pair another device")}
                  </span>
                </h3>
                <p className="mt-1 text-[12.5px] leading-snug text-muted">
                  {t("Scan this QR code or enter the 6-character code on your other device.")}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setInfo(null)}
                aria-label={t("Close")}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex flex-col items-center gap-3 rounded-2xl border border-border/70 bg-surface-2/60 p-4">
              {info.qr_png && (
                <img
                  src={`data:image/png;base64,${info.qr_png}`}
                  alt={t("Direct pairing QR code")}
                  className="h-44 w-44 rounded-2xl bg-white p-2 shadow-sm"
                />
              )}
              <div className="text-center">
                <div className="text-[11.5px] uppercase tracking-wider text-muted">
                  {t("Pairing code")}
                </div>
                <div className="mt-0.5 select-all font-mono text-[28px] font-bold tracking-[0.22em] text-fg">
                  {info.code}
                </div>
              </div>
            </div>

            {info.remote_ready ? (
              <div className="flex items-center gap-2 rounded-xl bg-emerald-500/10 px-3 py-2 text-[12px] leading-snug text-emerald-700 dark:text-emerald-300">
                <Globe size={14} className="shrink-0" />
                <span>{t("Cloud Link & remote tunnel ready — works anywhere on Wi-Fi or cellular.")}</span>
              </div>
            ) : info.tunnel_starting ? (
              <div className="flex items-center gap-2 rounded-xl bg-accent/10 px-3 py-2 text-[12px] leading-snug text-accent">
                <Loader2 size={14} className="shrink-0 animate-spin" />
                <span>{t("Starting remote tunnel so this works from anywhere… (home Wi-Fi works right now)")}</span>
              </div>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-amber-500/10 px-3 py-2 text-[12px] leading-snug text-amber-700 dark:text-amber-300">
                <span className="min-w-0 flex-1">
                  {t("Home Wi-Fi ready now. Start Free Tunnel to also pair away from home.")}
                </span>
                <button
                  type="button"
                  disabled={enablingRemote}
                  onClick={() => void enableRemote()}
                  className="inline-flex shrink-0 items-center gap-1 rounded-full bg-accent px-3 py-1 text-[11.5px] font-medium text-accent-fg disabled:opacity-50"
                >
                  {enablingRemote ? <Loader2 size={12} className="animate-spin" /> : null}
                  {enablingRemote ? t("Starting…") : t("Enable Pair From Anywhere")}
                </button>
              </div>
            )}

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void copyLink()}
                className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-full bg-accent px-3.5 py-2 text-[12.5px] font-medium text-accent-fg"
              >
                {copied ? <Check size={14} /> : <Copy size={14} />}
                {copied ? t("Copied") : t("Copy pairing link")}
              </button>
              <button
                type="button"
                disabled={refreshing}
                onClick={() => void refreshCode()}
                className="inline-flex items-center gap-1.5 rounded-full border border-border px-3.5 py-2 text-[12.5px] font-medium text-fg hover:bg-surface-2 disabled:opacity-50"
              >
                <RefreshCw size={13} className={refreshing ? "animate-spin" : ""} />
                {t("New code")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
