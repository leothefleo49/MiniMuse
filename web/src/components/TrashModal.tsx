import { Loader2, RotateCcw, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useClosable } from "../back";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { TrashedThread } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";
import { useFocusTrap } from "./useFocusTrap";

export function TrashModal({
  open,
  onClose,
  onRestored,
}: {
  open: boolean;
  onClose: () => void;
  onRestored?: (threadId: string) => void;
}) {
  const t = useT();
  const { state, openThread, setTab, toast } = useStore();
  const panelRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<TrashedThread[]>([]);
  const [retentionDays, setRetentionDays] = useState(365);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirmPurge, setConfirmPurge] = useState<TrashedThread | null>(null);
  const [confirmEmpty, setConfirmEmpty] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listTrash();
      setItems(res.items);
      if (typeof res.retention_days === "number") setRetentionDays(res.retention_days);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (open) void load();
  }, [open, load, state.trashCount]);

  useFocusTrap(panelRef, open && !confirmPurge && !confirmEmpty);
  useClosable(
    "trash-modal",
    () => {
      onClose();
      return true;
    },
    open && !confirmPurge && !confirmEmpty,
  );

  useEffect(() => {
    if (!open || confirmPurge || confirmEmpty) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, confirmPurge, confirmEmpty, onClose]);

  if (!open) return null;

  const restore = async (item: TrashedThread) => {
    setBusyId(item.id);
    try {
      const res = await api.restoreTrash(item.id);
      setItems((prev) => prev.filter((x) => x.id !== item.id));
      toast(t("Restored \"{title}\"", { title: res.thread.title || item.title }));
      openThread(res.thread.id);
      setTab("chat");
      onRestored?.(res.thread.id);
      onClose();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const purge = async (item: TrashedThread) => {
    setConfirmPurge(null);
    setBusyId(item.id);
    try {
      await api.purgeTrash(item.id);
      setItems((prev) => prev.filter((x) => x.id !== item.id));
      toast(t("Deleted permanently"));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  const emptyAll = async () => {
    setConfirmEmpty(false);
    setBusyId("__empty__");
    try {
      await api.emptyTrash();
      setItems([]);
      toast(t("Trash emptied"));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/45 p-4" onClick={onClose}>
        <div
          ref={panelRef}
          role="dialog"
          aria-modal="true"
          aria-label={t("Trash")}
          className="rise flex max-h-[82vh] w-full max-w-md flex-col overflow-hidden rounded-3xl border border-border bg-surface shadow-xl"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between border-b border-border/70 px-5 py-3.5">
            <div className="flex items-center gap-2">
              <Trash2 size={18} className="text-muted" />
              <h2 className="text-[16px] font-semibold">{t("Trash")}</h2>
              {items.length > 0 && (
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11.5px] font-medium text-muted">
                  {items.length}
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              {items.length > 0 && (
                <button
                  type="button"
                  disabled={busyId !== null}
                  onClick={() => setConfirmEmpty(true)}
                  className="rounded-xl px-2.5 py-1 text-[12px] font-medium text-rose-500 hover:bg-rose-500/10 disabled:opacity-50"
                >
                  {t("Empty Trash")}
                </button>
              )}
              <button
                type="button"
                onClick={onClose}
                aria-label={t("Close")}
                className="flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
              >
                <X size={18} />
              </button>
            </div>
          </div>

          <div className="border-b border-border/50 bg-surface-2/40 px-5 py-2 text-[12px] text-muted">
            {t("Deleted chats stay recoverable here for {days} days before automatic expiry.", { days: retentionDays })}
          </div>

          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {loading && items.length === 0 ? (
              <div className="flex items-center justify-center py-10 text-muted">
                <Loader2 size={18} className="animate-spin" />
              </div>
            ) : items.length === 0 ? (
              <div className="py-12 text-center text-[13.5px] text-muted">
                {t("Trash is empty.")}
              </div>
            ) : (
              items.map((item) => {
                const busy = busyId === item.id;
                return (
                  <div
                    key={item.id}
                    className="flex items-center gap-2.5 rounded-2xl border border-border/70 bg-bg px-3.5 py-2.5"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14px] font-medium text-fg">
                        {item.title || t("Untitled chat")}
                      </div>
                      <div className="mt-0.5 text-[11.5px] text-muted">
                        {item.was_main ? `${t("Main snapshot")} · ` : ""}
                        {t("{count} messages · {days} days left", {
                          count: item.message_count ?? 0,
                          days: item.days_left ?? retentionDays,
                        })}
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={busyId !== null}
                      onClick={() => void restore(item)}
                      className="inline-flex shrink-0 items-center gap-1 rounded-xl bg-accent/12 px-2.5 py-1.5 text-[12px] font-semibold text-accent hover:bg-accent/20 disabled:opacity-50"
                      title={t("Restore")}
                    >
                      {busy ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
                      <span>{t("Restore")}</span>
                    </button>
                    <button
                      type="button"
                      disabled={busyId !== null}
                      onClick={() => setConfirmPurge(item)}
                      aria-label={t("Delete permanently")}
                      title={t("Delete permanently")}
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-muted hover:bg-rose-500/12 hover:text-rose-500 disabled:opacity-50"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmPurge !== null}
        title={t("Delete permanently?")}
        body={t("\"{title}\" and all its messages will be removed permanently. This cannot be undone.", {
          title: confirmPurge?.title || t("Untitled chat"),
        })}
        confirmLabel={t("Delete permanently")}
        danger
        onConfirm={() => confirmPurge && void purge(confirmPurge)}
        onCancel={() => setConfirmPurge(null)}
      />

      <ConfirmDialog
        open={confirmEmpty}
        title={t("Empty Trash?")}
        body={t("All {n} deleted chats in Trash will be removed permanently. This cannot be undone.", {
          n: items.length,
        })}
        confirmLabel={t("Empty Trash")}
        danger
        onConfirm={() => void emptyAll()}
        onCancel={() => setConfirmEmpty(false)}
      />
    </>
  );
}
