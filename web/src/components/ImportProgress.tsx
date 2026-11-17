import { AlertTriangle, Check, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type ImportJobView } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import { summaryOf } from "./ImportExport";

const ACTIVE = new Set(["queued", "reading", "applying"]);

/**
 * The progress of imports running in the background, shown on every screen (setup too) so an
 * import can be left to work while the rest of the app is used. It follows the runtime's job
 * list, so it also picks up an import started before a reload or from another device.
 */
export function ImportProgress() {
  const t = useT();
  const { toast, refreshSettings } = useStore();
  const [jobs, setJobs] = useState<ImportJobView[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [watched, setWatched] = useState<Set<string>>(new Set());
  const announced = useRef<Set<string>>(new Set());
  const watchedRef = useRef<Set<string>>(new Set());
  const baseline = useRef<Set<string> | null>(null);

  useEffect(() => {
    let timer = 0;
    let stopped = false;
    const poll = async (): Promise<boolean> => {
      try {
        const { jobs: list } = await api.importJobs();
        if (stopped) return false;
        setJobs(list);
        // the history at first look is not news; anything newer, or running now, is watched
        // (even an import that finished between two polls)
        baseline.current ??= new Set(list.map((j) => j.id));
        const known = baseline.current;
        const fresh = list.filter((j) => (ACTIVE.has(j.phase) || !known.has(j.id)) && !watchedRef.current.has(j.id));
        if (fresh.length) {
          watchedRef.current = new Set([...watchedRef.current, ...fresh.map((j) => j.id)]);
          setWatched(watchedRef.current);
        }
        for (const job of list) {
          if (ACTIVE.has(job.phase) || !watchedRef.current.has(job.id) || announced.current.has(job.id)) continue;
          announced.current.add(job.id);
          if (job.phase === "done" && job.result) {
            toast(t("Import finished: {summary}", { summary: summaryOf(job.result, t) }));
            if (job.result.profile_updated || job.result.avatar_files) void refreshSettings().catch(() => undefined);
          } else {
            toast(t("Import failed: {error}", { error: job.error }));
          }
        }
        return list.some((j) => ACTIVE.has(j.phase));
      } catch {
        return false;
      }
    };
    const loop = async () => {
      const running = await poll();
      if (stopped) return;
      timer = window.setTimeout(() => void loop(), running ? 1200 : 15000);
    };
    const kick = () => {
      window.clearTimeout(timer);
      void loop();
    };
    void loop();
    window.addEventListener("nanomuse:import-started", kick);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      window.removeEventListener("nanomuse:import-started", kick);
    };
  }, [toast, refreshSettings, t]);

  const shown = jobs.filter((j) => !hidden.has(j.id) && (ACTIVE.has(j.phase) || watched.has(j.id)));
  if (!shown.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 top-[calc(0.5rem+var(--nm-safe-top,0px))] z-[80] flex flex-col items-center gap-2 px-3">
      {shown.map((job) => {
        const active = ACTIVE.has(job.phase);
        const failed = job.phase === "error";
        const label = failed
          ? t("Import failed")
          : job.phase === "applying"
            ? t("Adding to your chats...")
            : job.phase === "done"
              ? t("Import finished")
              : t("Importing {name}", { name: job.name });
        return (
          <div
            key={job.id}
            role="status"
            className="pointer-events-auto w-full max-w-[420px] rounded-2xl border border-border bg-surface px-3 py-2 shadow-lg"
          >
            <div className="flex items-center gap-2 text-[13px]">
              {active ? (
                <Loader2 size={15} className="shrink-0 animate-spin text-accent" />
              ) : failed ? (
                <AlertTriangle size={15} className="shrink-0 text-red-500" />
              ) : (
                <Check size={15} className="shrink-0 text-accent" />
              )}
              <span className="min-w-0 flex-1 truncate">{label}</span>
              {active && <span className="shrink-0 tabular-nums text-muted">{job.percent}%</span>}
              {!active && (
                <button
                  type="button"
                  aria-label={t("Dismiss")}
                  onClick={() => setHidden((h) => new Set(h).add(job.id))}
                  className="shrink-0 rounded-full p-1 text-muted active:bg-surface-2"
                >
                  <X size={14} />
                </button>
              )}
            </div>
            {active && (
              <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div className="h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${Math.max(job.percent, 3)}%` }} />
              </div>
            )}
            {failed && job.error && <p className="mt-1 text-[12px] text-muted">{job.error}</p>}
          </div>
        );
      })}
    </div>
  );
}
