import { CheckCircle2, Sparkles, Wrench, X, Zap } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import type { ReleaseNotesEntry } from "../types";

export function WhatsNewModal({
  notes,
  onClose,
}: {
  notes: ReleaseNotesEntry | null;
  onClose: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);

  if (!notes) return null;

  const dismiss = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.dismissWhatsNew();
    } catch {
      /* dismiss locally even if transient offline */
    } finally {
      setBusy(false);
      onClose();
    }
  };

  const added = notes.added ?? [];
  const improved = notes.improved ?? [];
  const fixed = notes.fixed ?? [];

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4 backdrop-blur-xs"
      role="dialog"
      aria-modal="true"
      aria-labelledby="whats-new-title"
    >
      <div className="rise flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-3xl border border-border bg-surface shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-border/70 px-5 py-4">
          <div className="min-w-0">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-accent/12 px-2.5 py-0.5 text-[11px] font-semibold text-accent">
              <Sparkles size={12} /> {t("Updated to v{version}", { version: notes.version })}
            </div>
            <h2 id="whats-new-title" className="mt-1.5 text-[18px] font-semibold leading-snug">
              {notes.title || t("What's New in MiniMuse")}
            </h2>
          </div>
          <button
            type="button"
            onClick={() => void dismiss()}
            aria-label={t("Close")}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          {added.length > 0 && (
            <Section
              icon={<Sparkles size={15} className="text-emerald-500" />}
              title={t("Added")}
              items={added}
            />
          )}
          {improved.length > 0 && (
            <Section
              icon={<Zap size={15} className="text-sky-500" />}
              title={t("Improved")}
              items={improved}
            />
          )}
          {fixed.length > 0 && (
            <Section
              icon={<Wrench size={15} className="text-amber-500" />}
              title={t("Fixed")}
              items={fixed}
            />
          )}
          {added.length === 0 && improved.length === 0 && fixed.length === 0 && notes.raw && (
            <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-muted">{notes.raw}</p>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border/70 px-5 py-3.5">
          <button
            type="button"
            disabled={busy}
            onClick={() => void dismiss()}
            className="inline-flex items-center gap-1.5 rounded-full bg-fg px-4 py-2 text-[13.5px] font-semibold text-bg transition active:scale-95 disabled:opacity-60"
          >
            <CheckCircle2 size={15} /> {t("Got it")}
          </button>
        </div>
      </div>
    </div>
  );
}

function Section({
  icon,
  title,
  items,
}: {
  icon: React.ReactNode;
  title: string;
  items: string[];
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-muted">
        {icon}
        <span>{title}</span>
      </div>
      <ul className="space-y-1.5 pl-1">
        {items.map((line, i) => (
          <li key={i} className="flex items-start gap-2 text-[13.5px] leading-snug text-fg/95">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent/70" />
            <span>{line}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
