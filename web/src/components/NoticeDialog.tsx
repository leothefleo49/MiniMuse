import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useEffect, useRef } from "react";
import { useClosable } from "../back";
import { useT } from "../i18n";
import { cx } from "../util";
import { useFocusTrap } from "./useFocusTrap";

export type NoticeTone = "error" | "warning" | "info" | "success";

export interface NoticeDialogProps {
  open: boolean;
  tone?: NoticeTone;
  title: string;
  body?: string;
  /** numbered things to do next */
  steps?: string[];
  primaryLabel?: string;
  onPrimary?: () => void;
  onClose: () => void;
}

const TONES: Record<NoticeTone, { icon: typeof Info; chip: string }> = {
  error: { icon: XCircle, chip: "bg-rose-500/12 text-rose-500" },
  warning: { icon: AlertTriangle, chip: "bg-amber-500/15 text-amber-600 dark:text-amber-400" },
  info: { icon: Info, chip: "bg-sky-500/12 text-sky-600 dark:text-sky-400" },
  success: { icon: CheckCircle2, chip: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400" },
};

/**
 * A notice in the middle of the screen, for what a person must not miss: nothing was installed, a
 * connection needs approving, something needs doing on another device. A line of changed text inside
 * a card is easy to overlook, especially when the card is scrolled out of view.
 */
export function NoticeDialog({ open, tone = "info", title, body, steps, primaryLabel, onPrimary, onClose }: NoticeDialogProps) {
  const t = useT();
  const panelRef = useRef<HTMLDivElement>(null);

  useFocusTrap(panelRef, open);
  useClosable(
    "notice-dialog",
    () => {
      onClose();
      return true;
    },
    open,
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const { icon: Icon, chip } = TONES[tone];

  return (
    <div className="fixed inset-0 z-[85] flex items-center justify-center bg-black/55 p-4" onClick={onClose}>
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="notice-dialog-title"
        aria-describedby="notice-dialog-body"
        className="rise w-full max-w-md rounded-3xl border border-border bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div className={cx("mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl", chip)}>
            <Icon size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 id="notice-dialog-title" className="text-[16px] font-semibold leading-snug text-fg">
              {title}
            </h3>
            {body && (
              <p id="notice-dialog-body" className="mt-1.5 text-[13.5px] leading-relaxed text-muted whitespace-pre-line">
                {body}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("Close")}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        {steps && steps.length > 0 && (
          <ol className="mt-4 space-y-2">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-2.5 text-[13.5px] leading-snug text-fg">
                <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[11.5px] font-semibold text-muted">
                  {i + 1}
                </span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-2xl border border-border bg-bg px-4 py-2 text-[13.5px] font-medium text-fg transition hover:bg-surface-2"
          >
            {t("Close")}
          </button>
          {onPrimary && (
            <button
              type="button"
              onClick={onPrimary}
              className="rounded-2xl bg-accent px-4 py-2 text-[13.5px] font-semibold text-accent-fg transition hover:opacity-95"
            >
              {primaryLabel ?? t("Try again")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
