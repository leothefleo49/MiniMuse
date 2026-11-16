import { AlertTriangle, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useClosable } from "../back";
import { useT } from "../i18n";
import { cx } from "../util";
import { useFocusTrap } from "./useFocusTrap";

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * In-app confirmation dialog shown before opening popups, OS permission screens,
 * switching into less guarded modes, or moving/deleting data.
 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  cancelLabel,
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const t = useT();
  const panelRef = useRef<HTMLDivElement>(null);

  useFocusTrap(panelRef, open);
  useClosable(
    "confirm-dialog",
    () => {
      onCancel();
      return true;
    },
    open,
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      onClick={onCancel}
    >
      <div
        ref={panelRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-body"
        className="rise w-full max-w-sm rounded-3xl border border-border bg-surface p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3">
          <div
            className={cx(
              "mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl",
              danger ? "bg-rose-500/12 text-rose-500" : "bg-amber-500/15 text-amber-600 dark:text-amber-400",
            )}
          >
            <AlertTriangle size={18} />
          </div>
          <div className="min-w-0 flex-1">
            <h3 id="confirm-dialog-title" className="text-[15.5px] font-semibold leading-snug text-fg">
              {title}
            </h3>
            <p id="confirm-dialog-body" className="mt-1.5 text-[13.5px] leading-relaxed text-muted whitespace-pre-line">
              {body}
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            aria-label={t("Close")}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={16} />
          </button>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-2xl border border-border bg-bg px-4 py-2 text-[13.5px] font-medium text-fg hover:bg-surface-2 transition"
          >
            {cancelLabel ?? t("Cancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={cx(
              "rounded-2xl px-4 py-2 text-[13.5px] font-semibold transition",
              danger ? "bg-rose-600 text-white hover:bg-rose-700" : "bg-accent text-accent-fg hover:opacity-95",
            )}
          >
            {confirmLabel ?? t("Continue")}
          </button>
        </div>
      </div>
    </div>
  );
}
