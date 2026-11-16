import {
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  Hash,
  Loader2,
  MapPin,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  User,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Sheet } from "../components/Sheet";
import { TabHeader } from "../components/TabHeader";
import { useT } from "../i18n";
import { DRAFT_THREAD, useStore } from "../store";
import type { CalendarData, CalendarEvent } from "../types";
import { cx } from "../util";

type CalView = "agenda" | "week" | "month";

function toIsoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function addDays(iso: string, delta: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(y, (m || 1) - 1, d || 1);
  dt.setDate(dt.getDate() + delta);
  return toIsoDate(dt);
}

function buildMapUrl(address: string, pref = "google"): string {
  const q = encodeURIComponent(address.trim());
  switch (pref) {
    case "apple":
      return `https://maps.apple.com/?q=${q}`;
    case "waze":
      return `https://waze.com/ul?q=${q}&navigate=yes`;
    case "osm":
      return `https://www.openstreetmap.org/search?query=${q}`;
    case "geo":
      return `geo:0,0?q=${q}`;
    default:
      return `https://www.google.com/maps/search/?api=1&query=${q}`;
  }
}

export function CalendarScreen() {
  const { state, dispatch, openThread, draft, setTab, refreshSettings, toast } = useStore();
  const t = useT();
  const [view, setView] = useState<CalView>("agenda");
  const [anchor, setAnchor] = useState<string>(() => toIsoDate(new Date()));
  const [data, setData] = useState<CalendarData | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  const [feedFilter, setFeedFilter] = useState("all");
  const [tagFilter, setTagFilter] = useState("");

  // Event modal
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingUid, setEditingUid] = useState("");
  const [summary, setSummary] = useState("");
  const [startVal, setStartVal] = useState("");
  const [endVal, setEndVal] = useState("");
  const [allDay, setAllDay] = useState(false);
  const [location, setLocation] = useState("");
  const [contactName, setContactName] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [rrule, setRrule] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  const mapPref = state.settings?.ui?.map_app || "google";

  const load = useCallback(
    async (refresh = false) => {
      if (refresh) setRefreshing(true);
      else setLoading(true);
      try {
        const days = view === "month" ? 42 : view === "week" ? 10 : 7;
        const startParam = view === "month" ? `${anchor.slice(0, 8)}01` : anchor;
        const res = await api.calendar(days, { start: startParam, refresh });
        setData(res);
      } catch (e) {
        toast((e as Error).message || t("Could not load calendar"));
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [anchor, view, t, toast],
  );

  useEffect(() => {
    void load(false);
  }, [load]);

  const events = useMemo(() => data?.events ?? [], [data]);

  const calendars = useMemo(() => {
    const names = new Set<string>(["all"]);
    for (const ev of events) {
      if (ev.calendar) names.add(ev.calendar);
    }
    return Array.from(names);
  }, [events]);

  const allTags = useMemo(() => {
    const tags = new Set<string>();
    for (const ev of events) {
      for (const tg of ev.tags ?? []) tags.add(tg);
    }
    return Array.from(tags).sort();
  }, [events]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((ev) => {
      if (feedFilter !== "all" && ev.calendar !== feedFilter) return false;
      if (tagFilter && !(ev.tags ?? []).includes(tagFilter)) return false;
      if (!q) return true;
      const hay = `${ev.summary} ${ev.location} ${ev.description} ${ev.contact_name ?? ""} ${(ev.tags ?? []).join(" ")} ${ev.calendar}`.toLowerCase();
      return hay.includes(q);
    });
  }, [events, feedFilter, tagFilter, query]);

  const openCreate = (dateSeed = anchor) => {
    setEditingUid("");
    setSummary("");
    setStartVal(`${dateSeed}T09:00`);
    setEndVal(`${dateSeed}T10:00`);
    setAllDay(false);
    setLocation("");
    setContactName("");
    setTagsInput("");
    setRrule("");
    setDescription("");
    setEditorOpen(true);
  };

  const openEdit = (ev: CalendarEvent) => {
    setEditingUid(ev.uid);
    setSummary(ev.summary);
    setStartVal(ev.all_day ? ev.start.slice(0, 10) : ev.start.slice(0, 16));
    setEndVal(ev.all_day ? ev.end.slice(0, 10) : ev.end.slice(0, 16));
    setAllDay(ev.all_day);
    setLocation(ev.location || "");
    setContactName(ev.contact_name || "");
    setTagsInput((ev.tags ?? []).map((t0) => `#${t0}`).join(" "));
    setRrule(ev.rrule || "");
    setDescription(ev.description || "");
    setEditorOpen(true);
  };

  const saveEvent = async () => {
    if (!summary.trim() || !startVal.trim()) return;
    setSaving(true);
    try {
      const tags = tagsInput
        .split(/[\s,]+/)
        .map((x) => x.replace(/^#/, "").trim())
        .filter(Boolean);
      const body = {
        summary: summary.trim(),
        start: startVal.trim(),
        end: endVal.trim() || undefined,
        all_day: allDay,
        location: location.trim(),
        contact_name: contactName.trim(),
        tags,
        rrule: rrule.trim(),
        description: description.trim(),
      };
      if (editingUid) {
        await api.updateCalendarEvent(editingUid, body);
        toast(t("Event updated"));
      } else {
        await api.createCalendarEvent(body);
        toast(t("Event created"));
      }
      setEditorOpen(false);
      await load(true);
    } catch (e) {
      toast((e as Error).message || t("Could not save event"));
    } finally {
      setSaving(false);
    }
  };

  const removeEvent = async (ev: CalendarEvent) => {
    try {
      await api.deleteCalendarEvent(ev.uid);
      toast(t("Event deleted"));
      await load(true);
    } catch (e) {
      toast((e as Error).message || t("Could not delete event"));
    }
  };

  const monthDays = useMemo(() => {
    const [y, m] = anchor.split("-").map(Number);
    const first = new Date(y, (m || 1) - 1, 1);
    const startOffset = first.getDay();
    const gridStart = new Date(first);
    gridStart.setDate(first.getDate() - startOffset);
    return Array.from({ length: 42 }, (_, i) => {
      const d = new Date(gridStart);
      d.setDate(gridStart.getDate() + i);
      const iso = toIsoDate(d);
      return {
        iso,
        day: d.getDate(),
        inMonth: d.getMonth() === first.getMonth(),
        events: filtered.filter((ev) => ev.start.slice(0, 10) === iso),
      };
    });
  }, [anchor, filtered]);

  const hideCalendarTab = async () => {
    await api.updateSettings({ ui: { ...(state.settings?.ui ?? {}), show_calendar: "false" } });
    await refreshSettings();
    setTab("chat");
    toast(t("Calendar tab hidden (re-enable anytime in Settings)"));
  };

  return (
    <div className="flex h-full flex-col">
      <TabHeader
        title={t("Calendar")}
        actions={[
          { label: t("New event"), onClick: () => openCreate() },
          { label: t("Calendar & Google sync"), onClick: () => setTab("connections") },
          { label: t("Hide Calendar tab"), onClick: () => void hideCalendarTab() },
        ]}
      >
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void load(true)}
            disabled={refreshing}
            aria-label={t("Refresh")}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-muted hover:text-fg"
          >
            <RefreshCw size={16} className={refreshing ? "animate-spin" : ""} />
          </button>
          <button
            type="button"
            onClick={() => openCreate()}
            className="flex h-9 items-center gap-1.5 rounded-full bg-accent px-3.5 text-[13px] font-medium text-accent-fg shadow-xs"
          >
            <Plus size={16} /> {t("Event")}
          </button>
        </div>
      </TabHeader>

      <div className="flex-1 overflow-y-auto px-4 pb-8 space-y-3">
        {/* View & Date Navigation */}
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div className="inline-flex rounded-full bg-surface-2 p-1 text-[12.5px]">
            {(["agenda", "week", "month"] as CalView[]).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                className={cx(
                  "rounded-full px-3 py-1 font-medium capitalize transition",
                  view === v ? "bg-surface text-fg shadow-xs" : "text-muted hover:text-fg",
                )}
              >
                {t(v)}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1.5 text-[13px]">
            <button
              type="button"
              onClick={() => setAnchor((a) => addDays(a, view === "month" ? -28 : view === "week" ? -7 : -1))}
              className="rounded-full border border-border p-1.5 text-muted hover:text-fg"
              aria-label={t("Previous")}
            >
              <ChevronLeft size={15} />
            </button>
            <button
              type="button"
              onClick={() => setAnchor(toIsoDate(new Date()))}
              className="rounded-full border border-border px-3 py-1 font-medium hover:bg-surface-2"
            >
              {anchor === toIsoDate(new Date()) ? t("Today") : anchor}
            </button>
            <button
              type="button"
              onClick={() => setAnchor((a) => addDays(a, view === "month" ? 28 : view === "week" ? 7 : 1))}
              className="rounded-full border border-border p-1.5 text-muted hover:text-fg"
              aria-label={t("Next")}
            >
              <ChevronRight size={15} />
            </button>
          </div>
        </div>

        {/* Search & Tag / Feed Filters */}
        <div className="space-y-2">
          <div className="relative">
            <Search size={14} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("Filter events by title, address, contact, or #tag...")}
              className="w-full rounded-2xl bg-surface-2 pl-9 pr-4 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/35"
            />
          </div>
          {(calendars.length > 1 || allTags.length > 0) && (
            <div className="flex flex-wrap gap-1.5">
              {calendars.length > 1 &&
                calendars.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => setFeedFilter(c)}
                    className={cx(
                      "rounded-full border px-2.5 py-0.5 text-[11.5px]",
                      feedFilter === c ? "border-accent bg-accent/10 font-medium text-accent" : "border-border text-muted",
                    )}
                  >
                    {c === "all" ? t("All calendars") : c}
                  </button>
                ))}
              {allTags.map((tg) => (
                <button
                  key={tg}
                  type="button"
                  onClick={() => setTagFilter((cur) => (cur === tg ? "" : tg))}
                  className={cx(
                    "inline-flex items-center gap-0.5 rounded-full border px-2.5 py-0.5 text-[11.5px]",
                    tagFilter === tg ? "border-accent bg-accent/10 font-medium text-accent" : "border-border text-muted",
                  )}
                >
                  <Hash size={10} />
                  {tg}
                </button>
              ))}
            </div>
          )}
        </div>

        {loading && !data ? (
          <div className="flex justify-center py-12 text-muted">
            <Loader2 className="animate-spin" size={20} />
          </div>
        ) : view === "month" ? (
          <div className="rounded-3xl border border-border/70 bg-surface p-2.5">
            <div className="grid grid-cols-7 pb-1.5 text-center text-[11px] font-semibold text-muted">
              {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => (
                <div key={d}>{d}</div>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {monthDays.map((cell) => {
                const isToday = cell.iso === (data?.today ?? toIsoDate(new Date()));
                const isSelected = cell.iso === anchor;
                return (
                  <button
                    key={cell.iso}
                    type="button"
                    onClick={() => setAnchor(cell.iso)}
                    onDoubleClick={() => openCreate(cell.iso)}
                    className={cx(
                      "min-h-[68px] rounded-2xl border p-1.5 text-left transition flex flex-col gap-1",
                      isSelected ? "border-accent bg-accent/8" : "border-border/50 hover:bg-surface-2/50",
                      !cell.inMonth && "opacity-45",
                    )}
                  >
                    <span className={cx("inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold", isToday && "bg-accent text-accent-fg")}>
                      {cell.day}
                    </span>
                    <div className="space-y-0.5 w-full overflow-hidden">
                      {cell.events.slice(0, 2).map((ev) => (
                        <div key={ev.uid} className="truncate rounded bg-accent/15 px-1 text-[10px] font-medium text-accent">
                          {ev.summary}
                        </div>
                      ))}
                      {cell.events.length > 2 && <div className="text-[9.5px] text-muted">+{cell.events.length - 2}</div>}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {/* Event list (also shows day breakdown under Month view!) */}
        {(() => {
          const shown =
            view === "month"
              ? filtered.filter((ev) => ev.start.slice(0, 10) === anchor)
              : filtered;
          if (shown.length === 0) {
            return (
              <div className="rounded-3xl border border-border/70 bg-surface p-6 text-center space-y-2">
                <CalendarDays size={26} className="mx-auto text-muted" />
                <div className="text-[14.5px] font-medium">{t("No events in this range")}</div>
                <p className="text-[12.5px] text-muted">
                  {t("Create local events here or connect Google / .ics feeds under Connections.")}
                </p>
                <div className="pt-1 flex justify-center gap-2">
                  <button type="button" onClick={() => openCreate()} className="rounded-full bg-accent px-4 py-1.5 text-[13px] font-medium text-accent-fg">
                    <Plus size={14} className="inline mr-1" /> {t("Add Event")}
                  </button>
                  <button type="button" onClick={() => setTab("connections")} className="rounded-full border border-border px-4 py-1.5 text-[13px] hover:bg-surface-2">
                    {t("Connect Calendar")}
                  </button>
                </div>
              </div>
            );
          }
          return (
            <div className="space-y-2">
              {shown.map((ev) => (
                <div key={ev.uid} className="rounded-3xl border border-border/70 bg-surface p-3.5 space-y-1.5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-[15px]">{ev.summary}</span>
                        <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10.5px] text-muted">{ev.calendar}</span>
                        {ev.rrule && (
                          <span className="rounded-full bg-accent/12 px-2 py-0.5 text-[10.5px] font-medium text-accent">{ev.rrule}</span>
                        )}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
                        <span className="inline-flex items-center gap-1">
                          <Clock size={12} />
                          {ev.all_day ? `${ev.start.slice(0, 10)} · ${t("All day")}` : `${ev.start.replace("T", " ").slice(0, 16)} -> ${ev.end.replace("T", " ").slice(11, 16)}`}
                        </span>
                        {ev.contact_name && (
                          <span className="inline-flex items-center gap-1 text-fg/80">
                            <User size={12} /> {ev.contact_name}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          dispatch({ type: "draftThread", title: `Event: ${ev.summary}` });
                          openThread(DRAFT_THREAD);
                          draft(`Help me plan/prepare for "${ev.summary}" on ${ev.start}${ev.location ? ` at ${ev.location}` : ""}.`);
                        }}
                        aria-label={t("Ask AI")}
                        className="rounded-xl p-1.5 text-muted hover:bg-surface-2 hover:text-accent"
                      >
                        <Sparkles size={15} />
                      </button>
                      {(ev.editable || ev.source === "local") && (
                        <>
                          <button
                            type="button"
                            onClick={() => openEdit(ev)}
                            aria-label={t("Edit")}
                            className="rounded-xl p-1.5 text-muted hover:bg-surface-2 hover:text-fg"
                          >
                            <Pencil size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={() => void removeEvent(ev)}
                            aria-label={t("Delete")}
                            className="rounded-xl p-1.5 text-muted hover:bg-surface-2 hover:text-rose-600"
                          >
                            <Trash2 size={15} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  {ev.location && (
                    <div className="flex flex-wrap items-center gap-2 pt-0.5">
                      <a
                        href={buildMapUrl(ev.location, mapPref)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1.5 rounded-full bg-accent/10 px-3 py-1 text-[12px] font-medium text-accent hover:bg-accent/15"
                      >
                        <MapPin size={12} /> {ev.location} <ExternalLink size={11} />
                      </a>
                    </div>
                  )}
                  {(ev.tags?.length ?? 0) > 0 && (
                    <div className="flex flex-wrap gap-1 pt-0.5">
                      {ev.tags!.map((tg) => (
                        <span key={tg} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-muted">
                          #{tg}
                        </span>
                      ))}
                    </div>
                  )}
                  {ev.description && <p className="text-[12.5px] text-muted line-clamp-2">{ev.description}</p>}
                </div>
              ))}
            </div>
          );
        })()}
      </div>

      <Sheet open={editorOpen} onClose={() => setEditorOpen(false)} title={editingUid ? t("Edit Event") : t("New Event")}>
        <div className="space-y-3 pb-4">
          <input
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder={t("Event title")}
            className="w-full rounded-2xl bg-surface-2 px-4 py-2.5 text-[15px] outline-none"
          />
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={allDay} onChange={(e) => setAllDay(e.target.checked)} className="accent-[var(--om-accent)]" />
            {t("All-day event")}
          </label>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <label className="text-[11.5px] text-muted">{t("Start")}</label>
              <input
                type={allDay ? "date" : "datetime-local"}
                value={startVal}
                onChange={(e) => setStartVal(e.target.value)}
                className="mt-1 w-full rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
              />
            </div>
            <div>
              <label className="text-[11.5px] text-muted">{t("End")}</label>
              <input
                type={allDay ? "date" : "datetime-local"}
                value={endVal}
                onChange={(e) => setEndVal(e.target.value)}
                className="mt-1 w-full rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
              />
            </div>
          </div>
          <input
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            placeholder={t("Address or place name (opens directly in Maps)")}
            className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none"
          />
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input
              value={contactName}
              onChange={(e) => setContactName(e.target.value)}
              placeholder={t("Related contact (optional)")}
              className="rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none"
            />
            <input
              value={tagsInput}
              onChange={(e) => setTagsInput(e.target.value)}
              placeholder={t("#tags (e.g. #work #dinner)")}
              className="rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none"
            />
          </div>
          <select value={rrule} onChange={(e) => setRrule(e.target.value)} className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none">
            <option value="">{t("Does not repeat")}</option>
            <option value="daily">{t("Every day")}</option>
            <option value="weekly">{t("Every week")}</option>
            <option value="monthly">{t("Every month")}</option>
            <option value="yearly">{t("Every year")}</option>
          </select>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            placeholder={t("Notes or agenda")}
            className="w-full rounded-2xl bg-surface-2 px-4 py-2 text-[13.5px] outline-none resize-none"
          />
          <button
            type="button"
            disabled={saving || !summary.trim() || !startVal.trim()}
            onClick={() => void saveEvent()}
            className="w-full rounded-2xl bg-accent py-2.5 text-[14.5px] font-semibold text-accent-fg disabled:opacity-50"
          >
            {saving ? t("Saving...") : editingUid ? t("Save Event") : t("Create Event")}
          </button>
        </div>
      </Sheet>
    </div>
  );
}
