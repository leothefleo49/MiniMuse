import {
  ArrowDown,
  ArrowUp,
  Bell,
  BellRing,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleDashed,
  CircleSlash,
  Hash,
  ListTodo,
  MessageCircle,
  MoreVertical,
  OctagonAlert,
  Pause,
  Pencil,
  Play,
  Plus,
  Repeat,
  Search,
  Sparkles,
  Square,
  SquareCheck,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { MuseRoundButton } from "../components/MuseHeader";
import { Sheet } from "../components/Sheet";
import { StarNudgeOnce } from "../components/StarNudge";
import { TabHeader } from "../components/TabHeader";
import { useT } from "../i18n";
import { CADENCES, CATEGORIES, WEEKDAYS, categoryOf, describeCadence, dueLabel, joinCadence, ordinal, splitCadence, weekdayName } from "../goals";
import { useStore } from "../store";
import type { Goal, GoalCategory, GoalKind, GoalPriority, GoalStep } from "../types";
import { cx, relativeTime, timeShort } from "../util";

type SortMode = "smart" | "priority" | "due" | "progress" | "updated" | "newest" | "title" | "manual";

const STEP_ICON: Record<GoalStep["status"], (p: { size: number; className?: string }) => ReactNode> = {
  pending: (p) => <Circle {...p} />,
  in_progress: (p) => <CircleDashed {...p} className={cx(p.className, "text-accent")} />,
  done: (p) => <CheckCircle2 {...p} className={cx(p.className, "text-emerald-500")} />,
  blocked: (p) => <OctagonAlert {...p} className={cx(p.className, "text-rose-500")} />,
  skipped: (p) => <CircleSlash {...p} className={cx(p.className, "text-muted")} />,
};

const STEP_NEXT: Record<GoalStep["status"], GoalStep["status"]> = {
  pending: "done",
  in_progress: "done",
  done: "pending",
  blocked: "pending",
  skipped: "pending",
};

const PRIORITY_RANK: Record<GoalPriority, number> = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const PRIORITY_BADGE: Record<GoalPriority, { label: string; cls: string }> = {
  urgent: { label: "Urgent", cls: "bg-rose-500/15 text-rose-600 dark:text-rose-300" },
  high: { label: "High", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  medium: { label: "Medium", cls: "bg-sky-500/12 text-sky-700 dark:text-sky-300" },
  low: { label: "Low", cls: "bg-surface-2 text-muted" },
};

const KIND_LABELS: Record<GoalKind, string> = {
  task: "Task",
  goal: "Goal",
  habit: "Habit",
};

function sortGoals(items: Goal[], mode: SortMode): Goal[] {
  const copy = [...items];
  const pRank = (g: Goal) => PRIORITY_RANK[(g.priority ?? "medium") as GoalPriority] ?? 2;
  const pct = (g: Goal) => (g.progress.total > 0 ? g.progress.done / g.progress.total : 0);
  const dueKey = (g: Goal) => (g.due ? `${g.due}T${g.due_time || "23:59"}` : "9999-99-99T99:99");
  switch (mode) {
    case "priority":
      return copy.sort((a, b) => pRank(a) - pRank(b) || dueKey(a).localeCompare(dueKey(b)));
    case "due":
      return copy.sort((a, b) => dueKey(a).localeCompare(dueKey(b)) || pRank(a) - pRank(b));
    case "progress":
      return copy.sort((a, b) => pct(b) - pct(a) || pRank(a) - pRank(b));
    case "updated":
      return copy.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
    case "newest":
      return copy.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
    case "title":
      return copy.sort((a, b) => a.title.localeCompare(b.title));
    case "manual":
      return copy.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
    case "smart":
    default:
      return copy.sort(
        (a, b) =>
          (b.overdue ? 1 : 0) - (a.overdue ? 1 : 0) ||
          pRank(a) - pRank(b) ||
          dueKey(a).localeCompare(dueKey(b)) ||
          (b.updated_at || "").localeCompare(a.updated_at || ""),
      );
  }
}

export function GoalsScreen() {
  const { state, refreshGoals, send, openThread, toast } = useStore();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [sheetCategory, setSheetCategory] = useState<(typeof CATEGORIES)[number] | null>(null);
  const [allFinished, setAllFinished] = useState(false);

  // Tasks & filtering/sorting state
  const [quickTitle, setQuickTitle] = useState("");
  const [quickKind, setQuickKind] = useState<GoalKind>("task");
  const [quickPriority, setQuickPriority] = useState<GoalPriority>("medium");
  const [quickDue, setQuickDue] = useState("");
  const [quickBusy, setQuickBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("smart");
  const [kindFilter, setKindFilter] = useState<"all" | GoalKind>("all");
  const [priorityFilter, setPriorityFilter] = useState<"all" | GoalPriority>("all");
  const [tagFilter, setTagFilter] = useState("");
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({});

  const createRef = useRef<HTMLDivElement>(null);
  const name = state.profile?.name ?? "MiniMuse";
  const t = useT();

  useEffect(() => {
    void refreshGoals();
  }, [refreshGoals]);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    for (const g of state.goals) {
      for (const tg of g.tags ?? []) tags.add(tg);
    }
    return Array.from(tags).sort();
  }, [state.goals]);

  const matchesFilters = useMemo(() => {
    const q = search.trim().toLowerCase().replace(/^#/, "");
    return (g: Goal) => {
      if (kindFilter !== "all" && (g.kind || "goal") !== kindFilter) return false;
      if (priorityFilter !== "all" && (g.priority || "medium") !== priorityFilter) return false;
      if (tagFilter && !(g.tags ?? []).includes(tagFilter)) return false;
      if (!q) return true;
      const hay = `${g.title} ${g.description} ${(g.tags ?? []).join(" ")} ${g.steps.map((s) => s.title).join(" ")}`.toLowerCase();
      return hay.includes(q);
    };
  }, [kindFilter, priorityFilter, tagFilter, search]);

  const proposals = state.goals.filter((g) => g.proposal && g.status !== "cancelled");
  const tracked = useMemo(
    () =>
      sortGoals(
        [...state.goals.filter((g) => g.status === "active"), ...state.goals.filter((g) => g.status === "paused")].filter(matchesFilters),
        sortMode,
      ),
    [state.goals, matchesFilters, sortMode],
  );
  const finished = useMemo(
    () => sortGoals(state.goals.filter((g) => g.status === "done" || g.status === "cancelled").filter(matchesFilters), sortMode),
    [state.goals, matchesFilters, sortMode],
  );
  const goal = state.goals.find((g) => g.id === selected) ?? null;

  const quickCreate = async () => {
    const raw = quickTitle.trim();
    if (!raw) return;
    setQuickBusy(true);
    try {
      const hashTags = Array.from(raw.matchAll(/#([A-Za-z0-9_-]+)/g)).map((m) => m[1].toLowerCase());
      const cleanTitle = raw.replace(/#[A-Za-z0-9_-]+/g, "").replace(/\s+/g, " ").trim() || raw;
      await api.createGoal({
        title: cleanTitle,
        kind: quickKind,
        priority: quickPriority,
        due: quickDue,
        tags: hashTags,
      });
      setQuickTitle("");
      setQuickDue("");
      await refreshGoals();
      toast(t("Added"));
    } catch (e) {
      toast((e as Error).message || t("Could not add task"));
    } finally {
      setQuickBusy(false);
    }
  };

  const advance = async (g: Goal) => {
    try {
      await api.advanceGoal(g.id);
      toast(t("{name} is working on “{title}” in the main chat", { name, title: g.title }));
      openThread("main");
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const [goalDone, setGoalDone] = useState(false);
  const toggleDone = async (g: Goal) => {
    try {
      const done = g.status !== "done";
      await api.patchGoal(g.id, { status: done ? "done" : "active" });
      if (done) setGoalDone(true);
      void refreshGoals();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const toggleSubtask = async (g: Goal, step: GoalStep) => {
    try {
      await api.patchGoal(g.id, { step_index: step.idx, step_status: STEP_NEXT[step.status] });
      void refreshGoals();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const addInlineSubtask = async (g: Goal, stepTitle: string) => {
    if (!stepTitle.trim()) return;
    try {
      await api.addStep(g.id, stepTitle.trim());
      void refreshGoals();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const startGoal = (c: (typeof CATEGORIES)[number]) => {
    setSheetCategory(null);
    send(
      "main",
      t("I'd like to create a {category} goal. Ask me a few short questions, one at a time (what exactly I want, why and by when, how often to check in), then create it with concrete steps using the goals tool.", {
        category: t(c.label),
      }),
    ).catch((e: Error) => toast(e.message || t("Could not send")));
    openThread("main");
  };

  return (
    <div className="flex h-full flex-col">
      <TabHeader title={t("Tasks & Goals")}>
        <MuseRoundButton small onClick={() => setCreating(true)} label={t("New goal")}>
          <Plus size={22} />
        </MuseRoundButton>
      </TabHeader>

      <div className="flex-1 overflow-y-auto pb-6">
        {/* Quick Add Bar */}
        <div className="px-4 pt-1 pb-2.5 space-y-2">
          <div className="rounded-3xl border border-border/70 bg-surface p-2.5 space-y-2">
            <div className="flex gap-2">
              <input
                value={quickTitle}
                onChange={(e) => setQuickTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void quickCreate()}
                placeholder={t("Quick add task or goal... (use #tags inline)")}
                className="flex-1 rounded-2xl bg-surface-2 px-3.5 py-2 text-[14px] outline-none focus:ring-2 focus:ring-accent/35"
              />
              <button
                type="button"
                disabled={quickBusy || !quickTitle.trim()}
                onClick={() => void quickCreate()}
                className="rounded-2xl bg-accent px-4 py-2 text-[13.5px] font-semibold text-accent-fg disabled:opacity-45"
              >
                {t("Add")}
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-1.5 text-[11.5px]">
              {(["task", "goal", "habit"] as GoalKind[]).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setQuickKind(k)}
                  className={cx(
                    "rounded-full border px-2.5 py-0.5 font-medium capitalize",
                    quickKind === k ? "border-accent bg-accent/10 text-accent" : "border-border text-muted",
                  )}
                >
                  {t(KIND_LABELS[k])}
                </button>
              ))}
              <select
                value={quickPriority}
                onChange={(e) => setQuickPriority(e.target.value as GoalPriority)}
                aria-label={t("Priority")}
                className="rounded-full border border-border bg-surface px-2.5 py-0.5 text-[11.5px] outline-none"
              >
                <option value="urgent">{t("Urgent priority")}</option>
                <option value="high">{t("High priority")}</option>
                <option value="medium">{t("Medium priority")}</option>
                <option value="low">{t("Low priority")}</option>
              </select>
              <input
                type="date"
                value={quickDue}
                onChange={(e) => setQuickDue(e.target.value)}
                aria-label={t("Due date")}
                className="rounded-full border border-border bg-surface px-2.5 py-0.5 text-[11.5px] outline-none"
              />
            </div>
          </div>

          {/* Search, Sort & Filter Toolbar */}
          <div className="flex flex-wrap items-center gap-1.5">
            <div className="relative flex-1 min-w-[160px]">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t("Search tasks, steps, #tags...")}
                className="w-full rounded-2xl bg-surface-2 pl-8 pr-3 py-1.5 text-[12.5px] outline-none"
              />
            </div>
            <select
              value={sortMode}
              onChange={(e) => setSortMode(e.target.value as SortMode)}
              aria-label={t("Sort by")}
              className="rounded-2xl bg-surface-2 px-3 py-1.5 text-[12.5px] font-medium outline-none"
            >
              <option value="smart">{t("Sort: Smart")}</option>
              <option value="priority">{t("Sort: Priority")}</option>
              <option value="due">{t("Sort: Due date")}</option>
              <option value="progress">{t("Sort: Progress")}</option>
              <option value="updated">{t("Sort: Updated")}</option>
              <option value="newest">{t("Sort: Newest")}</option>
              <option value="title">{t("Sort: A-Z")}</option>
              <option value="manual">{t("Sort: Manual")}</option>
            </select>
          </div>

          <div className="flex flex-wrap items-center gap-1 text-[11px]">
            {(["all", "task", "goal", "habit"] as const).map((k) => (
              <button
                key={k}
                type="button"
                onClick={() => setKindFilter(k)}
                className={cx(
                  "rounded-full border px-2.5 py-0.5 font-medium capitalize",
                  kindFilter === k ? "border-accent bg-accent/10 text-accent" : "border-border text-muted",
                )}
              >
                {k === "all" ? t("All types") : t(KIND_LABELS[k])}
              </button>
            ))}
            {(["urgent", "high", "medium", "low"] as GoalPriority[]).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => setPriorityFilter((cur) => (cur === p ? "all" : p))}
                className={cx(
                  "rounded-full border px-2.5 py-0.5 capitalize",
                  priorityFilter === p ? "border-accent bg-accent/10 font-medium text-accent" : "border-border text-muted",
                )}
              >
                {p}
              </button>
            ))}
            {allTags.map((tg) => (
              <button
                key={tg}
                type="button"
                onClick={() => setTagFilter((cur) => (cur === tg ? "" : tg))}
                className={cx(
                  "inline-flex items-center gap-0.5 rounded-full border px-2 py-0.5",
                  tagFilter === tg ? "border-accent bg-accent/10 font-medium text-accent" : "border-border text-muted",
                )}
              >
                <Hash size={9} /> {tg}
              </button>
            ))}
          </div>
        </div>

        {proposals.length > 0 && (
          <section className="px-4 pb-2">
            <div className="px-1 mb-2 text-[12px] font-semibold uppercase tracking-wide text-muted">{t("{name} suggests", { name })}</div>
            <div className="space-y-2.5">
              {proposals.map((g) => (
                <ProposalCard key={g.id} goal={g} onChanged={refreshGoals} onOpen={() => setSelected(g.id)} />
              ))}
            </div>
          </section>
        )}

        <StarNudgeOnce moment="goal_done" due={goalDone} className="mx-4 mb-3" />
        <SectionHeader label={t("Active ({n})", { n: tracked.length })} dot onAdd={() => setCreating(true)} />
        {tracked.length === 0 ? (
          <p className="px-5 pb-3 text-[13px] text-muted">{t("Nothing matching your filters")}</p>
        ) : (
          tracked.map((g) => (
            <GoalRow
              key={g.id}
              goal={g}
              expanded={!!expandedIds[g.id]}
              onToggleExpand={() => setExpandedIds((e) => ({ ...e, [g.id]: !e[g.id] }))}
              onOpen={() => setSelected(g.id)}
              onToggle={() => void toggleDone(g)}
              onToggleStep={(step) => void toggleSubtask(g, step)}
              onAddSubtask={(text) => void addInlineSubtask(g, text)}
            />
          ))
        )}

        {finished.length > 0 && (
          <>
            <div className="h-1.5" />
            <SectionHeader label={t("Finished ({n})", { n: finished.length })} />
            {(allFinished ? finished : finished.slice(0, 3)).map((g) => (
              <GoalRow
                key={g.id}
                goal={g}
                expanded={!!expandedIds[g.id]}
                onToggleExpand={() => setExpandedIds((e) => ({ ...e, [g.id]: !e[g.id] }))}
                onOpen={() => setSelected(g.id)}
                onToggle={() => void toggleDone(g)}
                onToggleStep={(step) => void toggleSubtask(g, step)}
                onAddSubtask={(text) => void addInlineSubtask(g, text)}
              />
            ))}
            {!allFinished && finished.length > 3 && (
              <button type="button" onClick={() => setAllFinished(true)} className="px-5 py-2 text-[13.5px] font-medium text-accent">
                {t("Show {n} more", { n: finished.length - 3 })}
              </button>
            )}
          </>
        )}

        <div ref={createRef} className="mt-2.5 border-t border-border/70 px-5 pt-3.5 pb-1.5">
          <h3 className="text-[17px] font-semibold">{t("Create a guided goal")}</h3>
          <p className="mt-1 text-[13px] leading-[18px] text-muted">{t("Pick a category and tell me the goal you have in mind. I'll shape a plan with you and keep improving it as you go.")}</p>
        </div>
        {CATEGORIES.map((c) => (
          <button key={c.id} type="button" onClick={() => setSheetCategory(c)} className="flex h-[50px] w-full items-center gap-3.5 pl-5 pr-2 text-left active:bg-surface-2/70">
            <span className="text-fg">{c.icon({ size: 22 })}</span>
            <span className="flex-1 text-[15px] font-medium">{t(c.label)}</span>
            <span className="flex h-10 w-10 items-center justify-center text-muted">
              <Plus size={22} />
            </span>
          </button>
        ))}
      </div>

      <GoalDetail goal={goal} onClose={() => setSelected(null)} onAdvance={advance} onChanged={refreshGoals} />
      <NewGoalSheet
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          void refreshGoals();
        }}
      />
      <Sheet open={sheetCategory !== null} onClose={() => setSheetCategory(null)}>
        {sheetCategory && (
          <div className="px-1 pb-2">
            <h3 className="text-[20px] font-bold">{t("Create a {category} goal", { category: t(sheetCategory.label) })}</h3>
            <p className="mt-3.5 text-[15px] leading-[22px]">{t("First, we'll shape the goal together in the chat. I'll ask a few questions so I understand exactly what you're after.")}</p>
            <p className="mt-3.5 text-[15px] leading-[22px]">{t("Once it's set, I'll track your progress here.")}</p>
            <button type="button" onClick={() => startGoal(sheetCategory)} className="mt-6 flex h-[50px] w-full items-center justify-center gap-2 rounded-full bg-accent text-[16px] font-semibold text-accent-fg">
              <MessageCircle size={18} /> {t("Start")}
            </button>
          </div>
        )}
      </Sheet>
    </div>
  );
}

function SectionHeader({ label, dot = false, onAdd }: { label: string; dot?: boolean; onAdd?: () => void }) {
  return (
    <div className="flex items-center pl-5 pr-2 pt-1">
      {dot && <span className="mr-2.5 h-2 w-2 rounded-full bg-emerald-500" />}
      <span className={cx("flex-1 text-[15px] font-semibold", dot && "text-emerald-600 dark:text-emerald-400")}>{label}</span>
      {onAdd ? (
        <button type="button" onClick={onAdd} aria-label={label} className="flex h-10 w-10 items-center justify-center text-muted">
          <Plus size={22} />
        </button>
      ) : (
        <span className="h-10" />
      )}
    </div>
  );
}

function CategoryBadge({ id, size = "sm" }: { id: GoalCategory; size?: "sm" | "md" }) {
  const t = useT();
  const c = categoryOf(id);
  if (!c) return null;
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full font-medium", c.tone, size === "sm" ? "px-2 py-0.5 text-[11.5px]" : "px-2.5 py-1 text-[12.5px]")}>
      {c.icon({ size: size === "sm" ? 11 : 13 })} {t(c.label)}
    </span>
  );
}

function GoalRow({
  goal,
  expanded,
  onToggleExpand,
  onOpen,
  onToggle,
  onToggleStep,
  onAddSubtask,
}: {
  goal: Goal;
  expanded: boolean;
  onToggleExpand: () => void;
  onOpen: () => void;
  onToggle: () => void;
  onToggleStep: (step: GoalStep) => void;
  onAddSubtask: (title: string) => void;
}) {
  const t = useT();
  const [miniStep, setMiniStep] = useState("");
  const done = goal.status === "done";
  const pct = goal.progress.total ? Math.round((goal.progress.done / goal.progress.total) * 100) : 0;
  const due = dueLabel(goal.due, goal.overdue);
  const subtitle = goal.next_step ? t("Next: {step}", { step: goal.next_step }) : goal.description || "";
  const priority = (goal.priority ?? "medium") as GoalPriority;
  const pBadge = PRIORITY_BADGE[priority] ?? PRIORITY_BADGE.medium;
  const kind = (goal.kind ?? "goal") as GoalKind;
  const cadence =
    goal.status === "paused"
      ? t("Paused")
      : goal.status === "cancelled"
        ? t("Cancelled")
        : done
          ? t("Done")
          : goal.check_in
            ? describeCadence(goal.check_in)
            : t("Updated {when}", { when: relativeTime(goal.updated_at) });

  return (
    <div className="border-b border-border/35 last:border-b-0 px-2">
      <div className="flex items-start pl-2.5 pr-1 pt-2.5 pb-2">
        <button type="button" onClick={onToggle} aria-label={done ? t("Mark as not done") : t("Mark as done")} className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center text-muted">
          {done ? <SquareCheck size={22} className="text-accent" /> : <Square size={22} />}
        </button>
        <div className="ml-2.5 min-w-0 flex-1 pt-[2px]">
          <button type="button" onClick={onOpen} className="w-full text-left">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className={cx("line-clamp-2 text-[15px] font-semibold leading-snug", done && "line-through text-muted")}>{goal.title}</span>
              {priority !== "medium" && (
                <span className={cx("rounded-full px-2 py-0.2 text-[10px] font-semibold", pBadge.cls)}>{t(pBadge.label)}</span>
              )}
              {kind !== "goal" && (
                <span className="rounded-full bg-surface-2 px-2 py-0.2 text-[10px] capitalize text-muted">{t(KIND_LABELS[kind])}</span>
              )}
            </div>
            {subtitle && <span className="mt-0.5 line-clamp-2 block text-[13px] leading-[18px] text-muted">{subtitle}</span>}
          </button>

          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11.5px] text-muted">
            <span className="truncate">
              {cadence}
              {due && goal.status === "active" ? ` · ${due}${goal.due_time ? ` ${goal.due_time}` : ""}` : ""}
            </span>
            {goal.recurrence && (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-surface-2 px-1.5 py-0.2 text-[10.5px]">
                <Repeat size={10} /> {goal.recurrence}
              </span>
            )}
            {pct > 0 && !done && <span className="shrink-0 font-medium text-emerald-600 dark:text-emerald-400">{pct}%</span>}
            {(goal.tags ?? []).map((tg) => (
              <span key={tg} className="rounded-full bg-surface-2 px-1.5 py-0.2 text-[10.5px]">#{tg}</span>
            ))}
            <button
              type="button"
              onClick={onToggleExpand}
              className="inline-flex items-center gap-1 rounded-full bg-surface-2/80 px-2 py-0.5 text-[11px] font-medium text-fg/80 hover:text-accent"
            >
              {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              <ListTodo size={11} />
              {goal.progress.total > 0 ? `${goal.progress.done}/${goal.progress.total}` : t("Subtasks")}
            </button>
          </div>
        </div>
        <button type="button" onClick={onOpen} aria-label={t("More")} className="flex h-10 w-10 shrink-0 items-center justify-center text-muted">
          <MoreVertical size={20} />
        </button>
      </div>

      {/* Expandable Inline Subtasks */}
      {expanded && (
        <div className="ml-11 mr-3 mb-2.5 rounded-2xl bg-surface-2/50 p-2.5 space-y-1.5">
          {goal.steps.map((s) => {
            const Icon = STEP_ICON[s.status];
            return (
              <button
                key={s.idx}
                type="button"
                onClick={() => onToggleStep(s)}
                className="flex w-full items-center gap-2 rounded-xl px-1.5 py-1 text-left hover:bg-surface"
              >
                <Icon size={16} className="shrink-0 text-muted" />
                <span className={cx("flex-1 text-[13px]", (s.status === "done" || s.status === "skipped") && "line-through text-muted")}>
                  {s.title}
                </span>
              </button>
            );
          })}
          <div className="flex gap-1.5 pt-1">
            <input
              value={miniStep}
              onChange={(e) => setMiniStep(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && miniStep.trim()) {
                  onAddSubtask(miniStep);
                  setMiniStep("");
                }
              }}
              placeholder={t("Add subtask...")}
              className="flex-1 rounded-xl bg-surface px-2.5 py-1 text-[12.5px] outline-none"
            />
            <button
              type="button"
              onClick={() => {
                if (miniStep.trim()) {
                  onAddSubtask(miniStep);
                  setMiniStep("");
                }
              }}
              className="rounded-xl bg-accent px-2.5 py-1 text-[12px] font-medium text-accent-fg"
            >
              +
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ProposalCard({ goal, onChanged, onOpen }: { goal: Goal; onChanged: () => void; onOpen: () => void }) {
  const { toast, state } = useStore();
  const [busy, setBusy] = useState(false);
  const name = state.profile?.name ?? "MiniMuse";
  const t = useT();
  const p = goal.proposal;
  if (!p) return null;
  const act = async (accept: boolean) => {
    setBusy(true);
    try {
      if (accept) await api.acceptProposal(goal.id);
      else await api.dismissProposal(goal.id);
      toast(accept ? t("Plan updated") : t("Kept your plan"));
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const kept = goal.steps.filter((s) => s.status === "done" || s.status === "skipped").length;
  return (
    <div className="rounded-3xl border border-accent/30 bg-accent/5 px-4 py-3.5">
      <button type="button" onClick={onOpen} className="w-full text-left">
        <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-accent">
          <Sparkles size={13} /> {t("Adjust the plan for “{title}”?", { title: goal.title })}
        </div>
        <p className="mt-1.5 text-[14px] leading-snug">{p.reason}</p>
        <div className="mt-2 text-[12px] text-muted">
          {kept > 0 && <span>{kept === 1 ? t("Keeps the 1 step already done.") : t("Keeps the {n} steps already done.", { n: kept })} </span>}
          {t("New remaining steps:")}
        </div>
        <ol className="mt-1 space-y-0.5 text-[13.5px] list-decimal pl-5">
          {p.steps.map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ol>
      </button>
      <div className="mt-3 flex gap-2">
        <button type="button" disabled={busy} onClick={() => void act(false)} className="flex-1 rounded-2xl border border-border py-2 text-[13.5px] font-medium disabled:opacity-50">
          {t("Keep my plan")}
        </button>
        <button type="button" disabled={busy} onClick={() => void act(true)} className="flex-1 rounded-2xl bg-accent text-accent-fg py-2 text-[13.5px] font-medium disabled:opacity-50">
          {t("Use {name}'s plan", { name })}
        </button>
      </div>
    </div>
  );
}

function CategoryPicker({ value, onChange }: { value: GoalCategory; onChange: (v: GoalCategory) => void }) {
  const t = useT();
  return (
    <div className="flex flex-wrap gap-1.5">
      {CATEGORIES.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => onChange(value === c.id ? "" : c.id)}
          className={cx(
            "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[12.5px] font-medium transition border",
            value === c.id ? cx(c.tone, "border-transparent ring-2 ring-accent/40") : "border-border text-muted",
          )}
        >
          {c.icon({ size: 12 })} {t(c.label)}
        </button>
      ))}
    </div>
  );
}

function CadencePicker({ value, onChange }: { value: string; onChange: (spec: string) => void }) {
  const t = useT();
  const { cadence, anchor, time } = splitCadence(value);
  const set = (c: string, a: string, at: string) => onChange(joinCadence(c, a, at));
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {CADENCES.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => set(c.id, c.id === "weekly" ? anchor || "mon" : c.id === "monthly" ? anchor || "1" : "", time)}
            className={cx(
              "rounded-full px-2.5 py-1 text-[12.5px] font-medium border transition",
              cadence === c.id ? "bg-accent text-accent-fg border-transparent" : "border-border text-muted",
            )}
          >
            {t(c.label)}
          </button>
        ))}
      </div>
      {cadence && (
        <div className="flex items-center gap-2 text-[13.5px]">
          {cadence === "weekly" && (
            <select value={anchor || "mon"} onChange={(e) => set(cadence, e.target.value, time)} aria-label={t("Weekday")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 outline-none">
              {WEEKDAYS.map((d) => (
                <option key={d} value={d}>
                  {weekdayName(d)}
                </option>
              ))}
            </select>
          )}
          {cadence === "monthly" && (
            <select value={anchor || "1"} onChange={(e) => set(cadence, e.target.value, time)} aria-label={t("Day of the month")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 outline-none">
              {Array.from({ length: 28 }, (_, i) => String(i + 1)).map((d) => (
                <option key={d} value={d}>
                  {ordinal(Number(d))}
                </option>
              ))}
            </select>
          )}
          <span className="text-muted">{t("at")}</span>
          <input type="time" value={time} onChange={(e) => set(cadence, anchor, e.target.value || "09:00")} aria-label={t("Time")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 outline-none" />
        </div>
      )}
    </div>
  );
}

function GoalDetail({
  goal,
  onClose,
  onAdvance,
  onChanged,
}: {
  goal: Goal | null;
  onClose: () => void;
  onAdvance: (g: Goal) => void;
  onChanged: () => void;
}) {
  const { toast, send, openThread, state } = useStore();
  const [newStep, setNewStep] = useState("");
  const [editing, setEditing] = useState(false);
  const [editingTitle, setEditingTitle] = useState("");
  const [editingDesc, setEditingDesc] = useState("");
  const [editingTags, setEditingTags] = useState("");
  const [renamingStepIdx, setRenamingStepIdx] = useState<number | null>(null);
  const [renamingStepText, setRenamingStepText] = useState("");
  const name = state.profile?.name ?? "MiniMuse";
  const t = useT();

  useEffect(() => {
    if (goal) {
      setEditingTitle(goal.title);
      setEditingDesc(goal.description || "");
      setEditingTags((goal.tags ?? []).map((x) => `#${x}`).join(" "));
    }
  }, [goal]);

  const patch = async (body: Record<string, unknown>) => {
    if (!goal) return;
    try {
      await api.patchGoal(goal.id, body);
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const addStep = async () => {
    if (!goal || !newStep.trim()) return;
    try {
      await api.addStep(goal.id, newStep.trim());
      setNewStep("");
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const moveStep = async (idxPosition: number, dir: -1 | 1) => {
    if (!goal) return;
    const indices = goal.steps.map((s) => s.idx);
    const target = idxPosition + dir;
    if (target < 0 || target >= indices.length) return;
    const copy = [...indices];
    const [picked] = copy.splice(idxPosition, 1);
    copy.splice(target, 0, picked);
    try {
      await api.reorderSteps(goal.id, copy);
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const removeStep = async (stepIdx: number) => {
    if (!goal) return;
    try {
      await api.deleteStep(goal.id, stepIdx);
      onChanged();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const commitStepRename = async (stepIdx: number) => {
    if (!goal || !renamingStepText.trim()) {
      setRenamingStepIdx(null);
      return;
    }
    await patch({ step_index: stepIdx, step_title: renamingStepText.trim() });
    setRenamingStepIdx(null);
  };

  const saveMeta = async () => {
    if (!goal) return;
    const tags = editingTags
      .split(/[\s,]+/)
      .map((x) => x.replace(/^#/, "").trim())
      .filter(Boolean);
    await patch({
      title: editingTitle.trim() || goal.title,
      description: editingDesc.trim(),
      tags,
    });
    setEditing(false);
  };

  const checkIn = async () => {
    if (!goal) return;
    try {
      await api.checkInGoal(goal.id);
      toast(t("{name} will check in with you in the main chat", { name }));
      openThread("main");
      onClose();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const remove = async () => {
    if (!goal || !window.confirm(t("Delete “{title}”?", { title: goal.title }))) return;
    try {
      await api.deleteGoal(goal.id);
      onChanged();
      onClose();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const due = goal ? dueLabel(goal.due, goal.overdue) : "";

  return (
    <Sheet
      open={!!goal}
      onClose={onClose}
      title={goal?.title}
      footer={
        goal && (
          <div className="flex gap-2">
            {goal.status === "active" ? (
              <button type="button" onClick={() => void patch({ status: "paused" })} className="flex-1 rounded-2xl border border-border py-2.5 font-medium flex items-center justify-center gap-1.5">
                <Pause size={16} /> {t("Pause")}
              </button>
            ) : goal.status === "paused" ? (
              <button type="button" onClick={() => void patch({ status: "active" })} className="flex-1 rounded-2xl border border-border py-2.5 font-medium flex items-center justify-center gap-1.5">
                <Play size={16} /> {t("Resume")}
              </button>
            ) : null}
            {goal.status === "active" && (
              <button type="button" onClick={() => onAdvance(goal)} className="flex-1 rounded-2xl bg-accent text-accent-fg py-2.5 font-medium flex items-center justify-center gap-1.5">
                <Play size={16} /> {t("Work on it now")}
              </button>
            )}
            <button type="button" onClick={() => void remove()} aria-label={t("Delete goal")} className="rounded-2xl border border-border px-3 text-muted hover:text-rose-500">
              <Trash2 size={18} />
            </button>
          </div>
        )
      }
    >
      {goal && (
        <div className="space-y-4">
          {goal.proposal && <ProposalCard goal={goal} onChanged={onChanged} onOpen={() => undefined} />}
          {goal.description && !editing && <p className="text-[14px] text-muted leading-snug">{goal.description}</p>}

          <div className="rounded-2xl bg-surface-2/60 px-3.5 py-3">
            {!editing ? (
              <button type="button" onClick={() => setEditing(true)} className="w-full text-left">
                <div className="flex flex-wrap items-center gap-1.5 text-[12.5px]">
                  <span className={cx("rounded-full px-2.5 py-0.5 font-semibold", (PRIORITY_BADGE[(goal.priority ?? "medium") as GoalPriority] ?? PRIORITY_BADGE.medium).cls)}>
                    {t((PRIORITY_BADGE[(goal.priority ?? "medium") as GoalPriority] ?? PRIORITY_BADGE.medium).label)}
                  </span>
                  <span className="rounded-full bg-surface px-2.5 py-0.5 capitalize text-muted">
                    {t(KIND_LABELS[(goal.kind ?? "goal") as GoalKind] ?? "Goal")}
                  </span>
                  {goal.category ? <CategoryBadge id={goal.category} size="md" /> : null}
                  <span className={cx("inline-flex items-center gap-1 rounded-full px-2.5 py-1", goal.overdue ? "bg-rose-500/12 text-rose-600 dark:text-rose-300 font-medium" : "bg-surface text-muted")}>
                    <CalendarDays size={13} /> {due ? `${due}${goal.due_time ? ` ${goal.due_time}` : ""}` : t("No target date")}
                  </span>
                  {goal.recurrence && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-surface px-2.5 py-1 text-muted">
                      <Repeat size={12} /> {goal.recurrence}
                    </span>
                  )}
                  <span className="inline-flex items-center gap-1 rounded-full bg-surface px-2.5 py-1 text-muted">
                    <Bell size={13} /> {goal.check_in ? describeCadence(goal.check_in) : t("No reminders")}
                  </span>
                </div>
                {(goal.tags?.length ?? 0) > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {goal.tags!.map((tg) => <span key={tg} className="rounded-full bg-surface px-2 py-0.5 text-[11px] text-muted">#{tg}</span>)}
                  </div>
                )}
                {goal.next_check_in && goal.status === "active" && (
                  <div className="mt-1.5 text-[12px] text-muted">{t("Next check-in {when}", { when: relativeTime(goal.next_check_in) })} · {timeShort(goal.next_check_in)}</div>
                )}
                <div className="mt-1.5 text-[12px] text-accent font-medium">{t("Edit details, priority & #tags")}</div>
              </button>
            ) : (
              <div className="space-y-3">
                <input
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  className="w-full rounded-xl bg-surface px-3 py-1.5 text-[14px] font-medium outline-none"
                  placeholder={t("Title")}
                />
                <textarea
                  value={editingDesc}
                  onChange={(e) => setEditingDesc(e.target.value)}
                  rows={2}
                  className="w-full rounded-xl bg-surface px-3 py-1.5 text-[13px] outline-none resize-none"
                  placeholder={t("Description")}
                />
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Type")}</div>
                    <select value={goal.kind || "goal"} onChange={(e) => void patch({ kind: e.target.value })} className="w-full rounded-xl bg-surface px-2.5 py-1.5 text-[13px] outline-none">
                      <option value="task">{t("Task")}</option>
                      <option value="goal">{t("Goal")}</option>
                      <option value="habit">{t("Habit")}</option>
                    </select>
                  </div>
                  <div>
                    <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Priority")}</div>
                    <select value={goal.priority || "medium"} onChange={(e) => void patch({ priority: e.target.value })} className="w-full rounded-xl bg-surface px-2.5 py-1.5 text-[13px] outline-none">
                      <option value="urgent">{t("Urgent")}</option>
                      <option value="high">{t("High")}</option>
                      <option value="medium">{t("Medium")}</option>
                      <option value="low">{t("Low")}</option>
                    </select>
                  </div>
                </div>
                <input
                  value={editingTags}
                  onChange={(e) => setEditingTags(e.target.value)}
                  className="w-full rounded-xl bg-surface px-3 py-1.5 text-[13px] outline-none"
                  placeholder={t("#hashtags")}
                />
                <div>
                  <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Area of life")}</div>
                  <CategoryPicker value={goal.category} onChange={(v) => void patch({ category: v })} />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Due date")}</div>
                    <input type="date" value={goal.due} onChange={(e) => void patch({ due: e.target.value })} className="w-full rounded-xl bg-surface px-2.5 py-1.5 text-[13px] outline-none" />
                  </div>
                  <div>
                    <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Due time")}</div>
                    <input type="time" value={goal.due_time || ""} onChange={(e) => void patch({ due_time: e.target.value })} className="w-full rounded-xl bg-surface px-2.5 py-1.5 text-[13px] outline-none" />
                  </div>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Repeat")}</div>
                  <select value={goal.recurrence || ""} onChange={(e) => void patch({ recurrence: e.target.value })} className="w-full rounded-xl bg-surface px-2.5 py-1.5 text-[13px] outline-none">
                    <option value="">{t("None")}</option>
                    <option value="daily">{t("Daily")}</option>
                    <option value="weekdays">{t("Weekdays")}</option>
                    <option value="weekly">{t("Weekly")}</option>
                    <option value="monthly">{t("Monthly")}</option>
                  </select>
                </div>
                <div>
                  <div className="text-[11px] font-semibold uppercase text-muted mb-1">{t("Reminders from {name}", { name })}</div>
                  <CadencePicker value={goal.check_in} onChange={(spec) => void patch({ check_in: spec })} />
                </div>
                <button type="button" onClick={() => void saveMeta()} className="rounded-xl bg-accent px-4 py-1.5 text-[12.5px] font-medium text-accent-fg">
                  {t("Save details")}
                </button>
              </div>
            )}
          </div>

          <div>
            <div className="text-[12px] font-semibold uppercase tracking-wide text-muted mb-2">
              {t("Subtasks & Steps")} · {goal.progress.done}/{goal.progress.total}
            </div>
            <ul className="space-y-1">
              {goal.steps.map((s, pos) => {
                const Icon = STEP_ICON[s.status];
                const isRenaming = renamingStepIdx === s.idx;
                return (
                  <li key={s.idx} className="flex items-start gap-2 rounded-2xl px-2 py-1.5 hover:bg-surface-2/60">
                    <button
                      type="button"
                      aria-label={t("Mark step {n} {status}", { n: s.idx, status: t(STEP_NEXT[s.status]) })}
                      onClick={() => void patch({ step_index: s.idx, step_status: STEP_NEXT[s.status] })}
                      className="mt-0.5 text-muted"
                    >
                      <Icon size={20} />
                    </button>
                    <div className="flex-1 min-w-0">
                      {isRenaming ? (
                        <div className="flex gap-1">
                          <input
                            value={renamingStepText}
                            onChange={(e) => setRenamingStepText(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && void commitStepRename(s.idx)}
                            className="flex-1 rounded-lg bg-surface px-2 py-0.5 text-[13.5px] outline-none"
                          />
                          <button type="button" onClick={() => void commitStepRename(s.idx)} className="text-[12px] font-medium text-accent px-1.5">
                            {t("Save")}
                          </button>
                        </div>
                      ) : (
                        <div className={cx("text-[14.5px] leading-snug", (s.status === "done" || s.status === "skipped") && "line-through text-muted")}>
                          {s.idx}. {s.title}
                        </div>
                      )}
                      {s.note && <div className="text-[12.5px] text-muted mt-0.5 whitespace-pre-wrap">{s.note}</div>}
                    </div>
                    <div className="flex items-center gap-0.5 text-muted">
                      <button type="button" disabled={pos === 0} onClick={() => void moveStep(pos, -1)} className="p-1 hover:text-fg disabled:opacity-30" aria-label={t("Move up")}>
                        <ArrowUp size={13} />
                      </button>
                      <button type="button" disabled={pos === goal.steps.length - 1} onClick={() => void moveStep(pos, 1)} className="p-1 hover:text-fg disabled:opacity-30" aria-label={t("Move down")}>
                        <ArrowDown size={13} />
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setRenamingStepIdx(s.idx);
                          setRenamingStepText(s.title);
                        }}
                        className="p-1 hover:text-fg"
                        aria-label={t("Edit step")}
                      >
                        <Pencil size={13} />
                      </button>
                      <button type="button" onClick={() => void removeStep(s.idx)} className="p-1 hover:text-rose-500" aria-label={t("Delete step")}>
                        <X size={13} />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
            <div className="mt-2 flex gap-2">
              <input
                value={newStep}
                onChange={(e) => setNewStep(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void addStep()}
                placeholder={t("Add a subtask")}
                aria-label={t("Add a step")}
                className="flex-1 rounded-2xl bg-surface-2 px-3.5 py-2 text-[14px] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button type="button" onClick={() => void addStep()} className="rounded-2xl bg-surface-2 px-3 text-accent" aria-label={t("Add step")}>
                <Plus size={18} />
              </button>
            </div>
          </div>

          {goal.notes && (
            <div>
              <div className="text-[12px] font-semibold uppercase tracking-wide text-muted mb-1.5">{t("Notes from {name}", { name })}</div>
              <pre className="whitespace-pre-wrap break-words text-[13px] leading-snug text-muted font-sans">{goal.notes}</pre>
            </div>
          )}
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            <button
              type="button"
              onClick={() => {
                send("main", t("About my goal “{title}” ({id}): what's the status, and what should we do next?", { title: goal.title, id: goal.id })).catch((e: Error) => toast(e.message || t("Could not send")));
                openThread("main");
                onClose();
              }}
              className="text-[13.5px] text-accent font-medium"
            >
              {t("Discuss this goal in chat →")}
            </button>
            {goal.status === "active" && (
              <button type="button" onClick={() => void checkIn()} className="text-[13.5px] text-accent font-medium inline-flex items-center gap-1">
                <BellRing size={14} /> {t("Check in with me now")}
              </button>
            )}
          </div>
        </div>
      )}
    </Sheet>
  );
}

function NewGoalSheet({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: () => void }) {
  const { toast, send, openThread, state } = useStore();
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<GoalKind>("task");
  const [priority, setPriority] = useState<GoalPriority>("medium");
  const [tags, setTags] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState("");
  const [category, setCategory] = useState<GoalCategory>("");
  const [due, setDue] = useState("");
  const [dueTime, setDueTime] = useState("");
  const [recurrence, setRecurrence] = useState("");
  const [checkIn, setCheckIn] = useState("");
  const [busy, setBusy] = useState(false);
  const name = state.profile?.name ?? "MiniMuse";
  const t = useT();

  const reset = () => {
    setTitle("");
    setKind("task");
    setPriority("medium");
    setTags("");
    setDescription("");
    setSteps("");
    setCategory("");
    setDue("");
    setDueTime("");
    setRecurrence("");
    setCheckIn("");
  };

  const create = async () => {
    if (!title.trim()) return;
    setBusy(true);
    try {
      await api.createGoal({
        title: title.trim(),
        kind,
        priority,
        tags,
        description: description.trim(),
        steps: steps.split("\n").map((s) => s.trim()).filter(Boolean),
        category,
        due,
        due_time: dueTime,
        recurrence,
        check_in: checkIn,
      });
      reset();
      onCreated();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const askMuse = () => {
    if (!title.trim()) return;
    const extras = [
      category ? `category: ${category}` : "",
      priority ? `priority: ${priority}` : "",
      due ? `target date: ${due}` : "",
      checkIn ? `check_in "${checkIn}"` : "",
    ].filter(Boolean);
    send(
      "main",
      t('Create a goal for me: "{title}"{description}{extras}. Break it into concrete steps with the goals tool, then tell me the plan.', {
        title: title.trim(),
        description: description.trim() ? ` (${description.trim()})` : "",
        extras: extras.length ? ` (${extras.join(", ")})` : "",
      }),
    ).catch((e: Error) => toast(e.message || t("Could not send")));
    reset();
    onClose();
    openThread("main");
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title={t("New task or goal")}
      footer={
        <div className="flex gap-2">
          <button type="button" onClick={askMuse} disabled={!title.trim()} className="flex-1 rounded-2xl border border-border py-2.5 font-medium disabled:opacity-50 flex items-center justify-center gap-1.5">
            <Sparkles size={16} /> {t("Let {name} plan it", { name })}
          </button>
          <button type="button" onClick={() => void create()} disabled={!title.trim() || busy} className="flex-1 rounded-2xl bg-accent text-accent-fg py-2.5 font-medium disabled:opacity-50">
            {t("Create")}
          </button>
        </div>
      }
    >
      <div className="space-y-3.5">
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("What do you want to achieve?")} aria-label={t("What do you want to achieve?")} className="w-full rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-accent/40" />
        <div className="grid grid-cols-2 gap-2">
          <select value={kind} onChange={(e) => setKind(e.target.value as GoalKind)} className="rounded-xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none">
            <option value="task">{t("Task")}</option>
            <option value="goal">{t("Goal")}</option>
            <option value="habit">{t("Habit")}</option>
          </select>
          <select value={priority} onChange={(e) => setPriority(e.target.value as GoalPriority)} className="rounded-xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none">
            <option value="urgent">{t("Urgent")}</option>
            <option value="high">{t("High")}</option>
            <option value="medium">{t("Medium")}</option>
            <option value="low">{t("Low")}</option>
          </select>
        </div>
        <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder={t("#hashtags (e.g. #home #errands)")} className="w-full rounded-2xl bg-surface-2 px-3.5 py-2 text-[13.5px] outline-none" />
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-wide text-muted mb-1.5">{t("Area of life")}</div>
          <CategoryPicker value={category} onChange={setCategory} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label={t("Target date")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 text-[13.5px] outline-none" />
          <input type="time" value={dueTime} onChange={(e) => setDueTime(e.target.value)} aria-label={t("Due time")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 text-[13.5px] outline-none" />
        </div>
        <select value={recurrence} onChange={(e) => setRecurrence(e.target.value)} className="w-full rounded-xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none">
          <option value="">{t("Does not repeat")}</option>
          <option value="daily">{t("Every day")}</option>
          <option value="weekdays">{t("Weekdays")}</option>
          <option value="weekly">{t("Every week")}</option>
          <option value="monthly">{t("Every month")}</option>
        </select>
        <div>
          <div className="text-[12px] font-semibold uppercase tracking-wide text-muted mb-1.5">{t("Reminders from {name}", { name })}</div>
          <CadencePicker value={checkIn} onChange={setCheckIn} />
        </div>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t("Why it matters, constraints (optional)")} aria-label={t("Why it matters, constraints (optional)")} rows={2} className="w-full rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[14px] outline-none focus:ring-2 focus:ring-accent/40 resize-none" />
        <textarea value={steps} onChange={(e) => setSteps(e.target.value)} placeholder={t("Subtasks / steps, one per line (optional)")} aria-label={t("Steps, one per line (optional, or let {name} plan them)", { name })} rows={3} className="w-full rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[14px] outline-none focus:ring-2 focus:ring-accent/40 resize-none" />
      </div>
    </Sheet>
  );
}
