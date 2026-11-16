import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "../i18n";
import type { TimelineEvent, ToolEvent } from "../types";

/**
 * The live activity above the composer: the steps the agent is running right now, as
 * small chips with an elapsed timer, visible even when the step chips are kept out of
 * the chat itself. Tapping a chip scrolls back to its card in the timeline, so a run's
 * work can be watched without giving up the message being written.
 */
export function ActivityStrip({
  events,
  onJump,
  busy,
}: {
  events: TimelineEvent[];
  onJump: (id: string) => void;
  /** The turn is under way even when no tool step is running (the model call). */
  busy: boolean;
}) {
  const t = useT();
  // the steps under way, at most the newest three so the strip stays one line
  const running = events.filter((e): e is ToolEvent => e.type === "tool" && e.status === "running").slice(-3);
  // a tick a second moves the elapsed time while something is running; the clock
  // itself is read in the tick, never during render, so the component stays pure
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (running.length === 0 && !busy) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
    // the ids of the running steps decide the clock, not just how many
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running.length, running[0]?.id, busy]);
  if (running.length === 0 && !busy) return null;
  const started = Math.min(
    ...running.map((e) => (e.ts ? new Date(e.ts).getTime() : Number.NaN)).filter((n) => !Number.isNaN(n)),
  );
  const elapsed = now === 0 || Number.isNaN(started) ? null : Math.max(0, Math.floor((now - started) / 1000));
  return (
    <div className="rise mx-3 mb-1 flex items-center gap-1.5 rounded-2xl border border-border/60 bg-surface-2/70 px-2 py-1.5">
      <Loader2 size={14} className="shrink-0 animate-spin text-accent" />
      {running.length > 0 ? (
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto no-scrollbar">
          {running.map((e) => (
            <button
              key={e.id}
              type="button"
              onClick={() => onJump(e.id)}
              className="flex max-w-[240px] shrink-0 items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-[12px] text-fg/85 shadow-sm transition active:scale-95"
              title={t("Watch this step")}
            >
              <span className="truncate">{e.title || e.summary || e.tool}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="text-[12.5px] text-fg/80">{t("Thinking")}</span>
        </div>
      )}
      {elapsed !== null && running.length > 0 && (
        <span className="shrink-0 text-[11.5px] font-medium tabular-nums text-muted">
          {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
        </span>
      )}
    </div>
  );
}
