import { Ban, ChevronRight, Clock, Gauge } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import { ConfirmDialog } from "./ConfirmDialog";

/**
 * Stop everything now: every chat that is working is stopped, and background check-ins are turned
 * off, so nothing keeps acting while the person works out what happened. It does not delete
 * anything; check-ins come back from Settings, under Proactivity.
 */
export function EmergencyStop() {
  const t = useT();
  const { state, toast } = useStore();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);

  const stopAll = async () => {
    setConfirm(false);
    setBusy(true);
    try {
      const working = state.threads.filter((th) => th.busy);
      const results = await Promise.allSettled(working.map((th) => api.stopThread(th.id)));
      await api.updateSettings({ profile: { proactivity: "off" } });
      const stopped = results.filter((r) => r.status === "fulfilled").length;
      toast(t("Stopped {n} chats and turned background check-ins off.", { n: stopped }));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="flex items-center justify-between gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/5 px-3.5 py-3">
        <div className="min-w-0">
          <div className="text-[14px] font-medium text-fg">{t("Emergency stop")}</div>
          <div className="mt-0.5 text-[12.5px] leading-[17px] text-muted">
            {t("Stops every chat that is working and turns background check-ins off. Nothing is deleted.")}
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => setConfirm(true)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-rose-600 px-3.5 py-2 text-[12.5px] font-semibold text-white hover:bg-rose-700 disabled:opacity-60"
        >
          <Ban size={14} />
          {t("Stop everything")}
        </button>
      </div>
      <ConfirmDialog
        open={confirm}
        danger
        title={t("Stop everything now?")}
        body={t("Every chat that is working is stopped and background check-ins are turned off. You can turn check-ins back on in Settings, under Proactivity.")}
        confirmLabel={t("Stop everything")}
        onConfirm={() => void stopAll()}
        onCancel={() => setConfirm(false)}
      />
    </>
  );
}

/** Safety controls that live elsewhere in Settings, one tap away from here. */
export function RelatedSafety({ onQuietHours, onLimits }: { onQuietHours: () => void; onLimits: () => void }) {
  const t = useT();
  const row = (icon: ReactNode, title: string, body: string, onClick: () => void) => (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-surface-2"
    >
      <span className="text-muted">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block text-[14px] font-medium text-fg">{title}</span>
        <span className="mt-0.5 block text-[12.5px] leading-[17px] text-muted">{body}</span>
      </span>
      <ChevronRight size={16} className="shrink-0 text-muted" />
    </button>
  );
  return (
    <div className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/70 bg-surface">
      {row(<Clock size={18} />, t("Quiet hours"), t("No background work in the hours you pick."), onQuietHours)}
      {row(<Gauge size={18} />, t("Spending and usage limits"), t("Stop or switch model before a limit you set is passed."), onLimits)}
    </div>
  );
}
