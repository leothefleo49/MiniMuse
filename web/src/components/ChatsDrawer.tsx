import { FileText, Home, MessageSquare, Monitor, MonitorSmartphone, MoreHorizontal, Pencil, Search, Settings, Smartphone, SquarePen, Terminal, Trash2, X } from "lucide-react";
import { TrashModal } from "./TrashModal";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { DRAFT_THREAD, useStore } from "../store";
import type { HubDevice, ThreadMeta } from "../types";
import { intlLocale, useT } from "../i18n";
import { cx } from "../util";
import { longPressHandlers, ThreadActionsSheet, type ThreadSheetMode } from "./ThreadActionsSheet";
import { useFocusTrap } from "./useFocusTrap";
import { useClosable } from "../back";

/**
 * The chats drawer the Android app slides in from the left: the agent's name, the main chat,
 * Devices and (when a computer with one is online) Coding agents as rows, then the side chats
 * under their own heading, and settings · search · new along the bottom. Opened by the round
 * hamburger on the chat and the four tabs; `state.drawer` holds whether it is showing.
 */
export function ChatsDrawer() {
  const { state, setDrawer, setTab, openThread, loadEvents, toast, dispatch } = useStore();
  const t = useT();
  const open = state.drawer;
  const [query, setQuery] = useState("");
  const [sheet, setSheet] = useState<{ thread: ThreadMeta; mode: ThreadSheetMode } | null>(null);
  const [trashOpen, setTrashOpen] = useState(false);
  const panel = useRef<HTMLElement>(null);
  useFocusTrap(panel, open);
  // the phone's back gesture closes the drawer first (back.ts)
  useClosable("chats-drawer", () => {
    setDrawer(false);
    return true;
  }, open);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setDrawer(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setDrawer]);
  useEffect(() => {
    if (!open) {
      setQuery("");
      setSheet(null);
      return;
    }
    // warm the Devices chunk while the drawer is open, so its row opens instantly
    void import("../screens/DevicesScreen").catch(() => undefined);
  }, [open]);

  const name = state.profile?.name || "MiniMuse";
  const side = useMemo(() => {
    const q = query.trim().toLowerCase();
    return state.threads
      .filter((th) => th.id !== "main")
      .filter((th) => !q || (th.title || "").toLowerCase().includes(q))
      .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  }, [state.threads, query]);
  const others: HubDevice[] = (state.hub?.devices ?? []).filter((d) => !d.this && d.kind !== "web");
  const othersOnline = others.filter((d) => d.online).length;
  const hubOn = state.hub?.state === "connected";
  const computersOnline = others.some((d) => d.online && d.kind === "computer");
  const chatHere = state.tab === "chat";

  const pick = (thread: string) => {
    setDrawer(false);
    openThread(thread);
    setTab("chat", { animate: false });
  };
  // Close the drawer first, then switch screens without a view transition: a transition
  // that starts while the drawer is still on screen could leave the old picture showing
  // (the phone then needed a second tap on the menu button before Devices appeared).
  const go = (tab: Parameters<typeof setTab>[0]) => {
    setDrawer(false);
    setTab(tab, { animate: false });
  };
  // New opens a page-only chat: nothing is a record on the runtime until the first
  // message goes out, so an emptied takeaway leaves nothing behind in the list.
  const create = () => {
    dispatch({ type: "draftThread", title: "" });
    pick(DRAFT_THREAD);
  };
  const remove = async (id: string) => {
    try {
      await api.deleteThread(id);
      dispatch({ type: "ws", msg: { kind: "thread_deleted", thread: id } });
      if (state.activeThread === id) openThread("main");
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSheet(null);
    }
  };
  const rename = async (id: string, next: string) => {
    try {
      const th = await api.renameThread(id, next);
      dispatch({ type: "ws", msg: { kind: "thread", thread: th } });
      setSheet(null);
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const clear = async (id: string) => {
    try {
      await api.clearThread(id);
      dispatch({ type: "ws", msg: { kind: "thread_cleared", thread: id } });
      if (state.activeThread === id) void loadEvents(id);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSheet(null);
    }
  };
  const kindOf = (id: string) => state.hub?.devices.find((d) => d.id === id)?.kind ?? "phone";

  const devicesLine = !hubOn ? t("Off") : othersOnline === 0 ? t("Only this one") : t("{n} online", { n: othersOnline });

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50">
      <div className="rise absolute inset-0 bg-black/40" onClick={() => setDrawer(false)} />
      <nav ref={panel} role="dialog" aria-modal="true" aria-label={t("Chats")} className="drawer-in safe-top safe-bottom absolute inset-y-0 left-0 flex w-[82vw] max-w-[320px] flex-col bg-bg shadow-2xl">
        <div className="px-5 pt-5 pb-2 text-[24px] font-bold leading-7 tracking-tight">{name}</div>

        {/* the main chat, Devices and the coding agents: rows, one tap away like a chat */}
        <div className="mt-4 flex flex-1 flex-col min-h-0">
        {(() => {
          const mainThread = state.threads.find((th) => th.id === "main");
          return (
            <div
              onContextMenu={(e) => {
                if (!mainThread) return;
                e.preventDefault();
                setSheet({ thread: mainThread, mode: "menu" });
              }}
              className="group relative flex items-center"
            >
              <div className="min-w-0 flex-1">
                <Row
                  icon={<Home size={20} />}
                  label={mainThread?.title && mainThread.title !== "Main" ? mainThread.title : t("Main chat")}
                  selected={chatHere && state.activeThread === "main"}
                  onClick={() => pick("main")}
                />
              </div>
              {mainThread && (
                <button
                  type="button"
                  aria-label={t("Rename")}
                  onClick={() => setSheet({ thread: mainThread, mode: "rename" })}
                  className="mr-3 p-1.5 text-muted/70 opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100"
                >
                  <Pencil size={14} />
                </button>
              )}
            </div>
          );
        })()}
        <Row icon={<MonitorSmartphone size={20} />} label={t("Devices")} detail={devicesLine} selected={state.tab === "devices"} onClick={() => go("devices")} />
        {computersOnline && (
          <Row icon={<Terminal size={20} />} label={t("Coding agents")} selected={state.tab === "coding"} onClick={() => go("coding")} />
        )}
        <Row
          icon={<Trash2 size={20} />}
          label={t("Trash")}
          detail={state.trashCount > 0 ? String(state.trashCount) : undefined}
          selected={trashOpen}
          onClick={() => setTrashOpen(true)}
        />

        <div className="mt-3 px-5 pb-1">
          <span className="text-[14px] font-medium text-muted">{t("Side chats")}</span>
        </div>
        {side.length === 0 && !query && state.activeThread !== DRAFT_THREAD ? (
          <div className="flex flex-1 flex-col items-center justify-center px-8 text-center">
            <MessageSquare size={28} className="mb-3 text-muted/70" />
            <div className="text-[17px] font-semibold">{t("Start a side chat")}</div>
            <div className="mt-1 text-[13px] leading-snug text-muted">{t("Side chats are an optional way to keep conversations organised by topic.")}</div>
          </div>
        ) : (
          <ul className="min-h-0 flex-1 overflow-y-auto py-1">
            {state.activeThread === DRAFT_THREAD && (
              <li className="mx-2 mb-1 flex items-center gap-2 rounded-2xl bg-surface-2 px-3 py-2.5 text-accent">
                <span className="min-w-0 flex-1 truncate text-[15px] font-medium">{state.draftThread?.title || t("New chat")}</span>
                <span className="shrink-0 text-[11.5px] text-muted">{t("Draft")}</span>
              </li>
            )}
            {side.map((th) => (
              <SideRow
                key={th.id}
                thread={th}
                kind={th.device ? kindOf(th.device) : undefined}
                selected={chatHere && state.activeThread === th.id}
                onPick={() => pick(th.id)}
                onHold={() => setSheet({ thread: th, mode: "menu" })}
                onRename={() => setSheet({ thread: th, mode: "rename" })}
                onDelete={() => setSheet({ thread: th, mode: "delete" })}
              />
            ))}
            {side.length === 0 && state.activeThread !== DRAFT_THREAD && <li className="px-5 py-3 text-[13px] text-muted">{t("No chats match.")}</li>}
          </ul>
        )}
        </div>

        {/* bottom strip: settings · search · new */}
        <div className="flex items-center gap-2 border-t border-border px-3 pt-2 pb-2">
          <button type="button" onClick={() => go("you")} aria-label={t("Settings")} className="flex h-10 w-10 items-center justify-center rounded-full text-fg hover:bg-surface-2">
            <Settings size={20} />
          </button>
          <button type="button" onClick={() => go("memory")} aria-label={t("Memory")} className="-ml-1 flex h-10 w-10 items-center justify-center rounded-full text-fg hover:bg-surface-2">
            <FileText size={20} />
          </button>
          <label className="flex min-w-0 flex-1 items-center gap-2 rounded-full bg-surface-2 px-3 py-2">
            <Search size={16} className="shrink-0 text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("Search")}
              aria-label={t("Search chats")}
              className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted"
            />
            {query && (
              <button type="button" onClick={() => setQuery("")} aria-label={t("Clear")} className="text-muted">
                <X size={14} />
              </button>
            )}
          </label>
          <button type="button" onClick={create} aria-label={t("New side chat")} className="flex h-10 w-10 items-center justify-center rounded-full text-fg hover:bg-surface-2">
            <SquarePen size={20} />
          </button>
        </div>
      </nav>
      {sheet && (
        <ThreadActionsSheet
          thread={sheet.thread}
          initialMode={sheet.mode}
          onClose={() => setSheet(null)}
          onRename={(next) => void rename(sheet.thread.id, next)}
          onClear={() => void clear(sheet.thread.id)}
          onDelete={() => void remove(sheet.thread.id)}
        />
      )}
      <TrashModal open={trashOpen} onClose={() => setTrashOpen(false)} onRestored={(id) => pick(id)} />
    </div>
  );
}

function Row({ icon, label, detail, selected, onClick }: { icon: ReactNode; label: string; detail?: string; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx("mx-2 flex items-center gap-3 rounded-2xl px-3 py-2.5 text-left", selected ? "bg-surface-2" : "hover:bg-surface-2/60")}
    >
      <span className="text-fg">{icon}</span>
      <span className="min-w-0 flex-1 truncate text-[16px]">{label}</span>
      {detail && <span className="shrink-0 text-[13px] text-muted">{detail}</span>}
    </button>
  );
}

function SideRow({
  thread,
  kind,
  selected,
  onPick,
  onHold,
  onRename,
  onDelete,
}: {
  thread: ThreadMeta;
  kind?: string;
  selected: boolean;
  onPick: () => void;
  onHold: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const hold = longPressHandlers(onHold, onPick);
  return (
    <li
      onContextMenu={(e) => {
        e.preventDefault();
        onHold();
      }}
      className={cx("group mx-2 flex items-center gap-1 rounded-2xl px-3", selected ? "bg-surface-2" : "hover:bg-surface-2/60")}
    >
      {/* the name answers a hold or menu with the chat's actions (rename, clear, delete) */}
      <button type="button" {...hold} className="flex min-w-0 flex-1 items-center gap-2 py-2.5 text-left">
        {thread.device ? (
          <span className="shrink-0 text-accent">{kind === "computer" ? <Monitor size={15} /> : <Smartphone size={15} />}</span>
        ) : thread.remote_from ? (
          <MonitorSmartphone size={15} className="shrink-0 text-muted" />
        ) : null}
        <span className="min-w-0 flex-1 truncate text-[15px]">{thread.title || t("Naming…")}</span>
        {thread.busy && <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-amber-400" />}
        <span className="shrink-0 text-[12px] text-muted">{relativeDay(thread.updated_at)}</span>
      </button>
      <button type="button" aria-label={t("Rename")} onClick={onRename} className="p-1 text-muted/70 opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100">
        <Pencil size={14} />
      </button>
      <button type="button" aria-label={t("Move to Trash")} onClick={onDelete} className="p-1 text-muted/70 opacity-0 hover:text-rose-500 focus:opacity-100 group-hover:opacity-100">
        <Trash2 size={15} />
      </button>
      <button type="button" aria-label={t("Chat options")} onClick={onHold} className="p-1 text-muted/70 opacity-0 hover:text-fg focus:opacity-100 group-hover:opacity-100">
        <MoreHorizontal size={14} />
      </button>
    </li>
  );
}

/** The Android drawer's time column: the clock today, the weekday within a week, the date after. */
export function relativeDay(ts: string | undefined, now = new Date()): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return "";
  const diff = now.getTime() - d.getTime();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString(intlLocale(), { hour: "2-digit", minute: "2-digit" });
  if (diff < 7 * 24 * 3600_000) return d.toLocaleDateString(intlLocale(), { weekday: "short" });
  return d.toLocaleDateString(intlLocale(), { month: "short", day: "numeric" });
}
