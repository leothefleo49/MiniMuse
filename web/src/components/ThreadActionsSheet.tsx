import { Pencil, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import type { ThreadMeta } from "../types";
import { useT } from "../i18n";
import { cx } from "../util";
import { Sheet } from "./Sheet";

export type ThreadSheetMode = "menu" | "rename" | "clear" | "delete";

/**
 * The one place a chat's actions live, opened by holding the chat's name in either list
 * or by the row's own buttons. Rename always; everything destructive (clearing the
 * history, deleting the chat) asks once more on its own step before it happens.
 */
export function ThreadActionsSheet({
  thread,
  initialMode = "menu",
  onClose,
  onRename,
  onClear,
  onDelete,
}: {
  thread: ThreadMeta | null;
  /** which step to land on: the row buttons jump straight to their own step, a hold lands here */
  initialMode?: ThreadSheetMode;
  onClose: () => void;
  /** the parent does the work and closes the sheet itself when it went through */
  onRename: (title: string) => void;
  onClear: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const [mode, setMode] = useState<ThreadSheetMode>(initialMode);
  const [name, setName] = useState("");
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!thread) return;
    setMode(initialMode);
    setName(thread.title);
    if (initialMode === "rename") {
      const pick = window.setTimeout(() => {
        input.current?.focus();
        input.current?.select();
      }, 40);
      return () => window.clearTimeout(pick);
    }
    // by id, not by object: a list row's meta can be fresh from the socket at any beat,
    // and the input must not reset under it (identity changes without an id change)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the sheet remounts per open; the id alone restarts the step
  }, [thread?.id, initialMode]);

  if (!thread) return null;
  const title = thread.title || t("New chat");
  const main = thread.id === "main";

  const save = (e?: FormEvent) => {
    e?.preventDefault();
    const next = name.trim();
    if (!next || next === title || main) return;
    onRename(next);
  };

  const step = (() => {
    if (mode === "rename") {
      return (
        <form onSubmit={save}>
          <p className="text-[13.5px] text-muted">{t("Rename this chat")}</p>
          <input
            ref={input}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("Type the new name")}
            aria-label={t("Rename this chat")}
            className="mt-2 w-full rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
          />
          <div className="mt-4 flex justify-end gap-2">
            <button type="button" onClick={() => setMode("menu")} className={ghostBtn}>
              {t("Back")}
            </button>
            <button
              type="submit"
              disabled={!name.trim() || name.trim() === title || main}
              className="rounded-2xl bg-ink px-4 py-2.5 text-[15px] font-semibold text-ink-fg disabled:opacity-50"
            >
              {t("Save")}
            </button>
          </div>
        </form>
      );
    }
    if (mode === "clear") {
      return (
        <div>
          <p className="text-[14.5px] leading-relaxed">{t("Clear “{title}”? Every message in it goes; memory, goals and reminders stay.", { title })}</p>
          <div className="mt-4 flex flex-col gap-2">
            <button type="button" onClick={onClear} className={dangerBtn}>
              {t("Clear the history")}
            </button>
            <button type="button" onClick={() => setMode("menu")} className={ghostBtn}>
              {t("Keep it")}
            </button>
          </div>
        </div>
      );
    }
    if (mode === "delete") {
      return (
        <div>
          <p className="text-[14.5px] leading-relaxed">{t("Move “{title}” to Trash? It stays recoverable for 365 days.", { title })}</p>
          <div className="mt-4 flex flex-col gap-2">
            <button type="button" onClick={onDelete} className={dangerBtn}>
              {t("Move to Trash")}
            </button>
            <button type="button" onClick={() => setMode("menu")} className={ghostBtn}>
              {t("Keep it")}
            </button>
          </div>
        </div>
      );
    }
    return (
      <div className="flex flex-col gap-1">
        <MenuRow icon={<Pencil size={16} />} label={t("Rename")} onClick={() => setMode("rename")} />
        <MenuRow icon={<X size={16} />} label={t("Clear the history")} onClick={() => setMode("clear")} />
        {!main && <MenuRow icon={<Trash2 size={16} />} label={t("Move to Trash")} danger onClick={() => setMode("delete")} />}
        {main && <p className="px-1 pt-1 text-[12.5px] text-muted">{t("This is the main chat: you can rename it or clear its messages into Trash.")}</p>}
      </div>
    );
  })();

  return (
    <Sheet open onClose={onClose} title={title}>
      {step}
    </Sheet>
  );
}

function MenuRow({ icon, label, danger, onClick }: { icon: ReactNode; label: string; danger?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "flex w-full items-center gap-3 rounded-2xl px-3.5 py-3 text-left text-[15px] hover:bg-surface-2",
        danger ? "text-rose-500" : "text-fg",
      )}
    >
      {icon}
      <span className="flex-1">{label}</span>
    </button>
  );
}

const ghostBtn = "rounded-2xl border border-border px-4 py-2.5 text-[15px] font-medium text-fg";
const dangerBtn = cx("rounded-2xl bg-rose-500 px-4 py-2.5 text-[15px] font-semibold text-white");

/**
 * A name that answers a hold: hold long enough and the chat's actions open instead of
 * the chat itself. Sliding the finger away cancels; the one tap right after a hold is
 * swallowed, so the sheet never picks a row behind it. Not a hook: each row builds its
 * own pair of handlers inside a map.
 */
export function longPressHandlers(onLongPress: () => void, onClick: () => void, ms = 450) {
  let timer: number | null = null;
  let start: { x: number; y: number } | null = null;
  let fired = false;
  const clear = () => {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    start = null;
  };
  return {
    onPointerDown: (e: ReactPointerEvent) => {
      fired = false;
      start = { x: e.clientX, y: e.clientY };
      timer = window.setTimeout(() => {
        fired = true;
        clear();
        onLongPress();
      }, ms);
    },
    onPointerMove: (e: ReactPointerEvent) => {
      if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 12) clear();
    },
    onPointerUp: clear,
    onPointerCancel: clear,
    onPointerLeave: clear,
    onClick: (e: { preventDefault: () => void; stopPropagation: () => void }) => {
      // a click ends the press's business one way or another
      clear();
      if (fired) {
        e.preventDefault();
        e.stopPropagation();
        fired = false;
        return;
      }
      onClick();
    },
  };
}
