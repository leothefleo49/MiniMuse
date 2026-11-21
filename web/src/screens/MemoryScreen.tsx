import { Brain, Check, FileText, FolderOpen, Loader2, Plus, Save, Search, Sparkles, Trash2, Undo2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { PageBar } from "../components/BackBar";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Toggle } from "../components/Form";
import { LoadError } from "../components/LoadError";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { MemoryChange, MemoryItem } from "../types";
import { cx, relativeTime } from "../util";

const CATEGORIES = ["profile", "preference", "people", "routine", "constraint", "general"];

type MuseMemFile = {
  path: string;
  name: string;
  folder: string;
  size: number;
  updated_at: string;
  snippet: string;
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** "Your Memory files, which you can read and edit directly." */
export function MemoryScreen({ embedded = false }: { embedded?: boolean } = {}) {
  const { state, toast, openThread, setTab, refreshSettings } = useStore();
  // null until the first answer: the empty state must not flash while the list is on its way
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [changes, setChanges] = useState<MemoryChange[]>([]);
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("profile");
  const [adding, setAdding] = useState(false);
  const [tidying, setTidying] = useState(false);
  const [togglingMem, setTogglingMem] = useState(false);

  // Imported Muse memory documents (data/muse_memory/**)
  const [view, setView] = useState<"facts" | "muse">("facts");
  const [memFiles, setMemFiles] = useState<MuseMemFile[] | null>(null);
  const [memFilesTotal, setMemFilesTotal] = useState(0);
  const [memFolders, setMemFolders] = useState<string[]>([]);
  const [folderFilter, setFolderFilter] = useState<string>("all");
  const [fileQuery, setFileQuery] = useState("");
  const [openFile, setOpenFile] = useState<{ path: string; name: string; content: string; original: string } | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);
  const [savingFile, setSavingFile] = useState(false);
  const [deletingFilePath, setDeletingFilePath] = useState<string | null>(null);

  const name = state.profile?.name ?? "MiniMuse";
  const memoryEnabled = state.settings?.memory_enabled !== false;
  const t = useT();

  // saves arrive together (one turn can store several memories): a slower earlier load
  // must not land over a newer one's answer
  const loadGen = useRef(0);
  const active = useRef(false);
  // `items` in this callback would be stale (the effect deps intentionally omit it), so a
  // failed refresh must not be treated as the first load and swallow the toast.
  const loadedOnce = useRef(false);
  const load = useCallback(
    (q = fileQuery) => {
      if (!active.current) return Promise.resolve();
      const mine = ++loadGen.current;
      return Promise.all([api.memory(), api.memoryChanges(), api.memoryFiles(q).catch(() => null)])
        .then(([nextItems, nextChanges, nextFiles]) => {
          if (loadGen.current !== mine) return;
          setItems(nextItems);
          setChanges(nextChanges);
          if (nextFiles) {
            setMemFiles(nextFiles.files);
            setMemFilesTotal(nextFiles.total);
            setMemFolders(nextFiles.folders);
          } else if (memFiles === null) {
            setMemFiles([]);
          }
          loadedOnce.current = true;
          setLoadError(null);
        })
        .catch((e: Error) => {
          if (loadGen.current !== mine) return;
          if (!loadedOnce.current) setLoadError(e.message);
          else toast(e.message);
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fileQuery],
  );

  useEffect(() => {
    active.current = true;
    void load(fileQuery);
    return () => {
      active.current = false;
      loadGen.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.memoryVersion]);

  const firstQuery = useRef(true);
  useEffect(() => {
    // the first run is covered by load(); only later edits of the search box refetch
    if (firstQuery.current) {
      firstQuery.current = false;
      return;
    }
    const timer = setTimeout(() => {
      if (!active.current) return;
      void api
        .memoryFiles(fileQuery)
        .then((res) => {
          setMemFiles(res.files);
          setMemFilesTotal(res.total);
          setMemFolders(res.folders);
        })
        .catch(() => undefined);
    }, 200);
    return () => clearTimeout(timer);
  }, [fileQuery]);

  const grouped = useMemo(() => {
    const map = new Map<string, MemoryItem[]>();
    for (const m of items ?? []) map.set(m.category, [...(map.get(m.category) ?? []), m]);
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [items]);

  const visibleFiles = useMemo(() => {
    const list = memFiles ?? [];
    if (folderFilter === "all") return list;
    return list.filter((f) => f.folder === folderFilter);
  }, [memFiles, folderFilter]);

  const toggleMemoryEnabled = async (next: boolean) => {
    setTogglingMem(true);
    try {
      await api.updateSettings({ memory_enabled: next });
      await refreshSettings();
      await load();
      toast(next ? t("Memory enabled") : t("Memory paused"));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setTogglingMem(false);
    }
  };

  const add = async () => {
    if (!content.trim()) return;
    setAdding(true);
    try {
      await api.addMemory(content.trim(), category);
      setContent("");
      await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setAdding(false);
    }
  };

  const forget = async (m: MemoryItem) => {
    if (!window.confirm(t("Forget “{text}”?", { text: m.content.slice(0, 60) }))) return;
    try {
      await api.forgetMemory(m.id);
      setItems((prev) => prev?.filter((x) => x.id !== m.id) ?? prev);
      // Retire reads from before the deletion and refresh its undo entry as well.
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  // One pass of the tidy-up the agent also runs on its own: merge lines that say the same
  // thing, drop what was never a fact. Every change lands in "Recent changes" with an undo.
  const tidy = async () => {
    setTidying(true);
    try {
      const report = await api.tidyMemory();
      toast(
        report.changed === 0
          ? t("Nothing to tidy: {n} memories, all distinct.", { n: report.considered })
          : t("Tidied: {merged} merged, {dropped} dropped. Undo below if needed.", {
              merged: report.merged.length,
              dropped: report.dropped.length,
            }),
      );
      await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setTidying(false);
    }
  };

  const restore = async (c: MemoryChange) => {
    try {
      await api.restoreMemoryChange(c.id);
      await load();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const inspectFile = async (f: MuseMemFile) => {
    setLoadingFile(true);
    try {
      const res = await api.readMemoryFile(f.path);
      setOpenFile({ path: res.path, name: res.name, content: res.content, original: res.content });
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setLoadingFile(false);
    }
  };

  const saveOpenFile = async () => {
    if (!openFile) return;
    setSavingFile(true);
    try {
      await api.saveMemoryFile(openFile.path, openFile.content);
      setOpenFile({ ...openFile, original: openFile.content });
      toast(t("Memory file saved"));
      await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSavingFile(false);
    }
  };

  const confirmDeleteFile = async () => {
    if (!deletingFilePath) return;
    const target = deletingFilePath;
    try {
      await api.deleteMemoryFile(target);
      if (openFile?.path === target) setOpenFile(null);
      setMemFiles((prev) => prev?.filter((f) => f.path !== target) ?? prev);
      setMemFilesTotal((n) => Math.max(0, n - 1));
      toast(t("Memory file deleted"));
      await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setDeletingFilePath(null);
    }
  };

  const body = (
    <div className={cx("space-y-4", embedded ? "pb-2" : "flex-1 overflow-y-auto px-4 pb-6")}>
      {/* Memory settings & overview */}
      <div className="rounded-3xl border border-border/70 bg-surface p-4 shadow-sm space-y-3">
        <Toggle
          label={t("Remember across chats")}
          hint={t(
            "Let {name} save and recall personal facts, preferences, and imported Muse memory notes across conversations. Syncs automatically between paired devices.",
            { name },
          )}
          checked={memoryEnabled}
          disabled={togglingMem}
          onChange={(v) => void toggleMemoryEnabled(v)}
        />
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-[12px] text-muted">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-surface-2 px-2.5 py-1 font-medium text-fg/85">
              {t("{n} saved facts", { n: items?.length ?? 0 })}
            </span>
            <span className="rounded-full bg-surface-2 px-2.5 py-1 font-medium text-fg/85">
              {t("{n} imported Muse memory files", { n: memFilesTotal })}
            </span>
          </div>
          {embedded && (items?.length ?? 0) >= 2 && (
            <button
              type="button"
              disabled={tidying}
              onClick={() => void tidy()}
              className="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-3 py-1 text-[12px] font-medium text-fg hover:bg-surface-2/80 disabled:opacity-50"
            >
              <Sparkles size={13} className={tidying ? "animate-pulse text-accent" : "text-accent"} />
              {tidying ? t("Tidying…") : t("Tidy up")}
            </button>
          )}
        </div>
      </div>

      {/* View switch: Saved facts vs Imported Muse memory files */}
      <div role="tablist" className="flex rounded-2xl bg-surface-2/70 p-1">
        <button
          type="button"
          role="tab"
          aria-selected={view === "facts"}
          onClick={() => setView("facts")}
          className={cx(
            "flex flex-1 items-center justify-center gap-1.5 rounded-xl py-2 text-[13.5px] font-medium transition",
            view === "facts" ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          <Brain size={15} />
          <span>{t("Saved facts")}</span>
          <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted">{items?.length ?? 0}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "muse"}
          onClick={() => setView("muse")}
          className={cx(
            "flex flex-1 items-center justify-center gap-1.5 rounded-xl py-2 text-[13.5px] font-medium transition",
            view === "muse" ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg",
          )}
        >
          <FileText size={15} />
          <span>{t("Muse memory")}</span>
          <span className="rounded-full bg-surface-2 px-1.5 py-0.5 text-[11px] text-muted">{memFilesTotal}</span>
        </button>
      </div>

      {view === "facts" ? (
        <>
          <div className="rounded-3xl bg-surface border border-border/70 shadow-sm p-3.5 space-y-2.5">
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={2}
              disabled={!memoryEnabled}
              placeholder={t("Tell {name} something to remember, e.g. “I'm vegetarian” or “My sister's birthday is 14 May”", { name })}
              aria-label={t("Tell {name} something to remember", { name })}
              className="w-full resize-none rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[14.5px] outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50"
            />
            <div className="flex items-center gap-2">
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                disabled={!memoryEnabled}
                aria-label={t("Category")}
                className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none disabled:opacity-50"
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {t(c)}
                  </option>
                ))}
              </select>
              <div className="flex-1" />
              <button
                type="button"
                disabled={!memoryEnabled || !content.trim() || adding}
                onClick={() => void add()}
                className="rounded-2xl bg-accent text-accent-fg px-3.5 py-2 text-[14px] font-medium flex items-center gap-1.5 disabled:opacity-50"
              >
                <Plus size={16} /> {t("Remember")}
              </button>
            </div>
          </div>

          {items === null && loadError && <LoadError message={loadError} onRetry={() => void load()} />}
          {items === null && !loadError && (
            <div className="flex justify-center py-10 text-muted">
              <Loader2 className="animate-spin" size={20} />
            </div>
          )}
          {items !== null && items.length === 0 && (
            <div className="rounded-3xl border border-dashed border-border p-6 text-center space-y-2">
              <Brain className="mx-auto text-accent" />
              <div className="font-semibold">{t("Nothing remembered yet")}</div>
              <p className="text-[13.5px] text-muted">
                {t("{name} saves durable facts you share in chat (preferences, people, routines) and never secrets.", { name })}
              </p>
              <div className="flex flex-wrap justify-center gap-2 pt-1">
                {memFilesTotal > 0 && (
                  <button
                    type="button"
                    onClick={() => setView("muse")}
                    className="rounded-full border border-border bg-surface px-4 py-2 text-[13.5px] font-semibold text-fg hover:bg-surface-2"
                  >
                    {t("Browse {n} imported Muse memory files", { n: memFilesTotal })}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    openThread("main");
                    setTab("chat");
                  }}
                  className="rounded-full bg-accent px-4 py-2 text-[13.5px] font-semibold text-accent-fg"
                >
                  {t("Tell {name} something to remember", { name })}
                </button>
              </div>
            </div>
          )}

          {grouped.map(([cat, list]) => (
            <section key={cat}>
              <div className="px-1 mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted">
                {t(cat)} · {list.length}
              </div>
              <ul className="rounded-3xl bg-surface border border-border/70 shadow-sm divide-y divide-border/70 overflow-hidden">
                {list.map((m) => (
                  <li key={m.id} className="flex items-start gap-3 px-4 py-3">
                    <div className="flex-1 min-w-0">
                      <div className="text-[14.5px] leading-snug break-words">{m.content}</div>
                      <div className="mt-0.5 text-[11.5px] text-muted">
                        {m.source === "user" ? t("added by you") : t("saved by {name}", { name })} · {relativeTime(m.created_at)}
                        {m.updated_at ? ` · ${t("updated {when}", { when: relativeTime(m.updated_at) })}` : ""}
                      </div>
                    </div>
                    <button type="button" aria-label={t("Forget")} onClick={() => void forget(m)} className="p-1.5 text-muted hover:text-rose-500">
                      <Trash2 size={17} />
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}

          {changes.length > 0 && (
            <section>
              <div className="px-1 mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted">{t("Recent changes")}</div>
              <ul className="rounded-3xl bg-surface border border-border/70 shadow-sm divide-y divide-border/70 overflow-hidden">
                {changes.map((c) => (
                  <li key={c.id} className={`px-4 py-3 ${c.restored ? "opacity-60" : ""}`}>
                    <div className="flex items-start gap-3">
                      <div className="flex-1 min-w-0 text-[13.5px] leading-snug">
                        <div className="text-muted">
                          {c.action === "merge" ? t("Merged") : c.action === "drop" ? t("Dropped") : c.action === "forget" ? t("Forgot") : t("Updated")}
                          {c.reason ? ` · ${c.reason}` : ""} · {relativeTime(c.at)}
                          {c.restored ? ` · ${t("restored")}` : ""}
                        </div>
                        {c.before.map((m) => (
                          <div key={m.id} className="break-words line-through text-muted">
                            {m.content}
                          </div>
                        ))}
                        {c.after && <div className="break-words">→ {c.after.content}</div>}
                      </div>
                      {!c.restored && (
                        <button
                          type="button"
                          onClick={() => void restore(c)}
                          className="shrink-0 rounded-xl bg-surface-2 px-2.5 py-1.5 text-[12.5px] font-medium flex items-center gap-1"
                        >
                          <Undo2 size={14} /> {t("Undo")}
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      ) : (
        /* Imported Muse memory files (data/muse_memory/**) */
        <div className="space-y-3">
          <div className="flex items-center gap-2 rounded-2xl border border-border/70 bg-surface px-3 py-2">
            <Search size={15} className="shrink-0 text-muted" />
            <input
              value={fileQuery}
              onChange={(e) => setFileQuery(e.target.value)}
              placeholder={t("Search imported Muse memory files…")}
              aria-label={t("Search imported Muse memory files")}
              className="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-muted"
            />
            {fileQuery && (
              <button type="button" onClick={() => setFileQuery("")} aria-label={t("Clear")} className="text-muted hover:text-fg">
                <X size={14} />
              </button>
            )}
          </div>

          {memFolders.length > 1 && (
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setFolderFilter("all")}
                className={cx(
                  "rounded-full px-3 py-1 text-[12px] font-medium transition",
                  folderFilter === "all" ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-fg",
                )}
              >
                {t("All")} ({memFilesTotal})
              </button>
              {memFolders.map((folder) => (
                <button
                  key={folder}
                  type="button"
                  onClick={() => setFolderFilter(folder)}
                  className={cx(
                    "inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12px] font-medium transition",
                    folderFilter === folder ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-fg",
                  )}
                >
                  <FolderOpen size={12} /> {folder}
                </button>
              ))}
            </div>
          )}

          {openFile && (
            <div className="rounded-3xl border border-accent/40 bg-surface p-4 shadow-sm space-y-3">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[14px] font-semibold">{openFile.name}</div>
                  <div className="truncate font-mono text-[11.5px] text-muted">{openFile.path}</div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    type="button"
                    disabled={savingFile || openFile.content === openFile.original}
                    onClick={() => void saveOpenFile()}
                    className="inline-flex items-center gap-1 rounded-xl bg-accent px-3 py-1.5 text-[12.5px] font-medium text-accent-fg disabled:opacity-40"
                  >
                    {savingFile ? <Loader2 size={13} className="animate-spin" /> : openFile.content === openFile.original ? <Check size={13} /> : <Save size={13} />}
                    {savingFile ? t("Saving…") : openFile.content === openFile.original ? t("Saved") : t("Save")}
                  </button>
                  <button
                    type="button"
                    aria-label={t("Delete")}
                    onClick={() => setDeletingFilePath(openFile.path)}
                    className="flex h-8 w-8 items-center justify-center rounded-xl text-muted hover:bg-surface-2 hover:text-rose-500"
                  >
                    <Trash2 size={15} />
                  </button>
                  <button
                    type="button"
                    aria-label={t("Close")}
                    onClick={() => setOpenFile(null)}
                    className="flex h-8 w-8 items-center justify-center rounded-xl text-muted hover:bg-surface-2 hover:text-fg"
                  >
                    <X size={16} />
                  </button>
                </div>
              </div>
              <textarea
                value={openFile.content}
                onChange={(e) => setOpenFile({ ...openFile, content: e.target.value })}
                rows={12}
                className="w-full rounded-2xl bg-surface-2 p-3 font-mono text-[12.5px] leading-relaxed outline-none focus:ring-2 focus:ring-accent/40"
              />
            </div>
          )}

          {loadingFile && (
            <div className="flex items-center gap-2 px-1 text-[12.5px] text-muted">
              <Loader2 size={14} className="animate-spin" /> {t("Opening memory file…")}
            </div>
          )}

          {memFiles === null ? (
            <div className="flex justify-center py-8 text-muted">
              <Loader2 className="animate-spin" size={20} />
            </div>
          ) : visibleFiles.length === 0 ? (
            <div className="rounded-3xl border border-dashed border-border p-6 text-center space-y-2">
              <FileText className="mx-auto text-muted" />
              <div className="font-semibold">
                {fileQuery ? t("No matching memory files") : t("No imported Muse memory files yet")}
              </div>
              <p className="text-[13px] text-muted">
                {t("Import a Muse export zip under Settings → Import & export to bring over all your Muse memory notes, people profiles, and daily summaries.")}
              </p>
            </div>
          ) : (
            <ul className="rounded-3xl border border-border/70 bg-surface shadow-sm divide-y divide-border/70 overflow-hidden">
              {visibleFiles.map((f) => (
                <li key={f.path} className="flex items-start gap-3 px-4 py-3 hover:bg-surface-2/50 transition">
                  <button
                    type="button"
                    onClick={() => void inspectFile(f)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[14px] font-medium text-fg">{f.name}</span>
                      <span className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10.5px] font-medium text-muted">
                        {f.folder}
                      </span>
                    </div>
                    {f.snippet && (
                      <div className="mt-0.5 line-clamp-2 text-[12.5px] leading-snug text-muted">{f.snippet}</div>
                    )}
                    <div className="mt-1 text-[11px] text-muted/80">
                      {f.path} · {formatBytes(f.size)} · {relativeTime(f.updated_at)}
                    </div>
                  </button>
                  <button
                    type="button"
                    aria-label={t("Delete")}
                    onClick={() => setDeletingFilePath(f.path)}
                    className="p-1.5 text-muted hover:text-rose-500"
                  >
                    <Trash2 size={16} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <ConfirmDialog
        open={deletingFilePath !== null}
        title={t("Delete memory file?")}
        body={t("Remove {path} from Muse memory on this device and synced devices?", { path: deletingFilePath ?? "" })}
        confirmLabel={t("Delete")}
        danger
        onCancel={() => setDeletingFilePath(null)}
        onConfirm={() => void confirmDeleteFile()}
      />
    </div>
  );

  if (embedded) return body;

  return (
    <div className="flex h-full flex-col">
      <PageBar
        title={t("Memory")}
        description={t("What {name} remembers about you. Read it, add to it, or make {name} forget; nothing here is hidden from you.", { name })}
        actions={
          (items?.length ?? 0) >= 2 && (
            <button
              type="button"
              disabled={tidying}
              onClick={() => void tidy()}
              title={t("Merge lines that say the same thing and drop what was never a fact about you. Every change can be undone.")}
              className="flex h-10 items-center gap-1.5 rounded-full bg-surface px-3.5 text-[13.5px] font-medium shadow-[0_1px_4px_rgba(0,0,0,0.14)] disabled:opacity-50 dark:border dark:border-border dark:shadow-none"
            >
              <Sparkles size={15} className={tidying ? "animate-pulse text-accent" : "text-accent"} />
              {tidying ? t("Tidying…") : t("Tidy up")}
            </button>
          )
        }
      />
      {body}
    </div>
  );
}
