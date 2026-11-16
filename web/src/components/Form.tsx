import { ChevronDown, ChevronUp, Info } from "lucide-react";
import { useState, type ReactNode } from "react";
import { cx } from "../util";

/** One settings card: an icon, a title with a status pill, a one-line summary, a body that folds out. */
export function Card({
  icon,
  title,
  summary,
  status,
  open,
  onToggle,
  trailing,
  children,
}: {
  icon: ReactNode;
  title: string;
  summary: string;
  status: { text: string; tone: string };
  open: boolean;
  onToggle?: () => void;
  trailing?: ReactNode;
  children?: ReactNode;
}) {
  const head = (
    <>
      <span className="text-accent mt-0.5">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="text-[15px] font-semibold">{title}</span>
          <span
            className={cx(
              "rounded-full px-2 py-0.5 text-[11px] font-medium",
              status.tone === "ok" &&
                "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
              status.tone === "warn" &&
                "bg-amber-500/15 text-amber-700 dark:text-amber-300",
              status.tone === "off" && "bg-surface-2 text-muted",
            )}
          >
            {status.text}
          </span>
        </span>
        <span className="block text-[12.5px] text-muted truncate">
          {summary}
        </span>
      </span>
      {trailing ??
        (onToggle ? (
          open ? (
            <ChevronUp size={18} className="text-muted" />
          ) : (
            <ChevronDown size={18} className="text-muted" />
          )
        ) : null)}
    </>
  );
  return (
    <section className="rounded-3xl bg-surface border border-border/70 shadow-sm">
      {onToggle ? (
        <button
          type="button"
          onClick={onToggle}
          className="flex w-full items-start gap-3 px-4 py-3.5 text-left"
        >
          {head}
        </button>
      ) : (
        <div className="flex w-full items-start gap-3 px-4 py-3.5">{head}</div>
      )}
      {(open || (!onToggle && children)) && children ? (
        <div className="px-4 pb-4 space-y-3">{children}</div>
      ) : null}
    </section>
  );
}

export const inputCls =
  "w-full rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[14.5px] outline-none focus:ring-2 focus:ring-accent/40";
export const primaryBtn =
  "flex items-center justify-center gap-1.5 rounded-2xl bg-accent text-accent-fg px-4 py-2.5 text-[14px] font-medium disabled:opacity-40";
export const secondaryBtn =
  "flex items-center justify-center gap-1.5 rounded-2xl border border-border px-4 py-2.5 text-[14px] font-medium text-muted disabled:opacity-40";

/** A labelled switch with an optional inline or click-to-expand detail hint. */
export function Toggle({
  label,
  hint,
  checked,
  onChange,
  disabled,
  collapsibleHint = false,
  forceHintOpen = false,
  detailLabel,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  collapsibleHint?: boolean;
  forceHintOpen?: boolean;
  detailLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const shown = collapsibleHint ? open || forceHintOpen : true;
  return (
    <div className="space-y-1.5">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChange(!checked)}
              className="text-left text-[14px] text-fg cursor-pointer disabled:cursor-default"
            >
              {label}
            </button>
            {collapsibleHint && hint && (
              <button
                type="button"
                aria-expanded={shown}
                aria-label={detailLabel || `${label} details`}
                onClick={() => setOpen((v) => !v)}
                className={cx(
                  "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full transition",
                  shown ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                <Info size={13} />
              </button>
            )}
          </div>
          {!collapsibleHint && hint && <div className="text-[12.5px] text-muted leading-snug">{hint}</div>}
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-label={label}
          disabled={disabled}
          onClick={() => onChange(!checked)}
          className={cx(
            "relative mt-0.5 h-7 w-12 shrink-0 rounded-full transition disabled:opacity-50",
            checked ? "bg-accent" : "bg-surface-2 border border-border",
          )}
        >
          <span className={cx("absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition", checked ? "left-[22px]" : "left-0.5")} />
        </button>
      </div>
      {collapsibleHint && hint && shown && (
        <div className="rounded-xl border border-border/60 bg-surface-2/65 px-3 py-2 text-[12.5px] leading-relaxed text-fg/85">
          {hint}
        </div>
      )}
    </div>
  );
}
