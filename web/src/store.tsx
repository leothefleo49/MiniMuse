import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import { flushSync } from "react-dom";
import { api, AuthError, connectWs, getToken } from "./api";
import { recordDebugEvent } from "./debugLog";
import { enqueue, dequeue, outbox } from "./offline";
import { getLocale, t } from "./i18n";
import { liveWorking } from "./presence";
import { registerWorker, setAppBadge } from "./push";
import { useTheme } from "./theme";
import { readStorage, writeStorage } from "./util";
import type {
  ApprovalEvent,
  AttachmentInfo,
  CodingEvent,
  CodingRun,
  FirstRunView,
  Goal,
  HandsLive,
  HandsStatus,
  SyncState,
  AppUpdateLiveState,
  HoldEvent,
  HubView,
  LocalDevicesView,
  Profile,
  SettingsView,
  StateSnapshot,
  StudioSession,
  Status,
  ThreadMeta,
  TimelineEvent,
  WorkingPresence,
  WsMessage,
} from "./types";

/** Tab bar: chat · feed · ideas · goals · (optional calendar · location) · library. Memory, devices, connections and settings live behind the avatar. */
export type Tab =
  | "chat"
  | "feed"
  | "ideas"
  | "goals"
  | "calendar"
  | "location"
  | "library"
  | "memory"
  | "devices"
  | "connections"
  | "channels"
  | "skills"
  | "you"
  | "account"
  | "usage"
  | "coding"
  | "avatar";
/**
 * The page this app is framed in (the showcase's simulated phone), when it can be named: the
 * ancestor's origin where the browser exposes it, else the referrer's. A message to the parent
 * goes to that origin only — never to whatever happens to hold the frame.
 */
function parentOrigin(): string | null {
  if (window.parent === window) return null;
  const ancestors = window.location.ancestorOrigins;
  if (ancestors && ancestors.length > 0 && ancestors[0] !== "null") return ancestors[0];
  if (document.referrer) {
    try {
      return new URL(document.referrer).origin;
    } catch {
      return null;
    }
  }
  return null;
}

function tellParent(message: { type: string; [k: string]: unknown }): void {
  const origin = parentOrigin();
  if (origin) window.parent.postMessage(message, origin);
}

const TAB_NAMES: Tab[] = [
  "chat",
  "feed",
  "ideas",
  "goals",
  "calendar",
  "location",
  "library",
  "memory",
  "devices",
  "connections",
  "channels",
  "skills",
  "you",
  "account",
  "usage",
  "coding",
  "avatar",
];

/** A coding run being followed live: the run itself and the steps that arrived so far. */
export interface CodingLive {
  run: CodingRun;
  events: CodingEvent[];
  /** the text the agent is writing right now (partial deltas) */
  current: string;
}

const FEED_SEEN_KEY = "nanomuse_feed_seen";
/** The browser stepped past the sign-in door for a key of its own. */
const SIGN_IN_SKIPPED_KEY = "nanomuse_sign_in_skipped";

export interface Stream {
  id: string;
  text: string;
  /** live reasoning, streamed this turn only; the final event carries the fold */
  reasoning: string;
  ended: boolean;
  /** the last event id on screen when the stream began: the live bubble renders right
   *  after it, so a message typed mid-stream stays BELOW the reply that started first */
  anchor?: string;
}

export interface AppState {
  connected: boolean;
  loaded: boolean;
  authError: boolean;
  /** the runtime behind this page has ended (the gateway closed the socket for good) */
  gone: boolean;
  error: string | null;
  version: string;
  profile: Profile | null;
  status: Status;
  threads: ThreadMeta[];
  activeThread: string;
  events: Record<string, TimelineEvent[]>;
  hasMore: Record<string, boolean>;
  streams: Record<string, Stream | undefined>;
  goals: Goal[];
  settings: SettingsView | null;
  goalsVersion: number;
  memoryVersion: number;
  /** Bumped when a reminder or routine is set, fires or is cancelled. */
  remindersVersion: number;
  calendarVersion: number;
  /** Cards waiting for you, across every thread — the approvals queue. */
  pendingApprovals: ApprovalEvent[];
  /** Bumps whenever something lands in the Feed (background work, cards, artifacts). */
  feedVersion: number;
  /** ISO time of the newest Feed item you have looked at. */
  feedSeenAt: string;
  /** Path of the workspace file open in the viewer, if any. */
  viewer: string | null;
  /** Text to put in the chat composer next time it shows (e.g. "/weekly-review "). */
  draft: string | null;
  /** attachments handed over from outside (the phone's share sheet), already uploaded */
  draftFiles: AttachmentInfo[] | null;
  /** The side chat opened from New and not sent to yet (id `DRAFT_THREAD`): its title, until a first send. */
  draftThread: { title: string } | null;
  /** Bumps when a connection (model, email, browser, MCP) changes on the server. */
  connectionsVersion: number;
  /** Bumps when a skill is added, changed, switched or removed on the server. */
  skillsVersion: number;
  /** First-run setup dismissed for this session (the server remembers a finished one). */
  onboardingDismissed: boolean;
  /** Setup opened again on purpose (Settings): shown even when chats already have messages. */
  setupReopened: boolean;
  /** The sign-in door stepped past for a key of one's own (kept in storage): the sign-in stays an invitation under Connections. */
  signInSkipped: boolean;
  tab: Tab;
  /** The screens left behind, newest last: the back arrow returns to the one before, not to the chat. */
  tabHistory: Tab[];
  toast: string | null;
  /** The chats drawer (the phone's hamburger) is open. */
  drawer: boolean;
  /** When the agent last went from working to idle (ms since epoch; 0 = never). The face is pleased for a moment. */
  finishedAt: number;
  /** When a tool call last failed or was refused (ms since epoch; 0 = never). The face is worried for a moment. */
  mishapAt: number;
  /** This device on the hub and the other devices of the account (null until the first state). */
  hub: HubView | null;
  /** The devices this MiniMuse is built on, without any account (null until fetched). */
  devices: LocalDevicesView | null;
  /** Coding runs followed live, newest last (kept for this page only). */
  codingLive: Record<string, CodingLive>;
  /** This computer's own screen and hands. */
  hands: HandsStatus | null;
  /** The holds that are on (contract C1): the person has the browser, the screen or the phone. */
  holds: HoldEvent[];
  /** The latest live step of the hands, for the stage; cleared when the task ends. */
  handsLive: HandsLive | null;
  /** The avatar studio's session as the runtime last reported it (null until one runs). */
  studio: StudioSession | null;
  /**
   * The other devices' turns under way on synced chats (contract C9), by thread: the line
   * "Pixel 8 is working…" under a message written there. Set by the `working` frames, cleared
   * when the reply arrives, when that device says done, or ten minutes after `at`.
   */
  working: Record<string, WorkingPresence | undefined>;
  /**
   * The first conversation (contract C4) as the runtime holds it: which thread it is bound
   * to, its phase, the chooser's names. From the hello snapshot and the `firstrun` frames;
   * null on an older runtime, and the chat then shows its plain greeting.
   */
  firstrun: FirstRunView | null;
  trashCount: number;
  sync: SyncState | null;
  /** A sync pass is in flight with a paired device: how many messages have landed so far. */
  syncProgress: { peer: string; applied: number; done: boolean } | null;
  appUpdate: AppUpdateLiveState | null;
  /**
   * `?ui=lite`: the app as it is shown inside the simulated phone of the showcase
   * (demo/mobilegym) — the phone layout with its tabs at any width, no sidebar, no first-run
   * setup and no desktop hints. Kept for the tab (sessionStorage) so a reload inside the frame
   * stays lite.
   */
  lite: boolean;
}

type Action =
  | { type: "hello"; state: StateSnapshot }
  | { type: "ws"; msg: WsMessage }
  | { type: "wsBatch"; msgs: WsMessage[] }
  | { type: "connection"; connected: boolean }
  | { type: "authError" }
  | { type: "gone" }
  | { type: "error"; error: string | null }
  | { type: "events"; thread: string; events: TimelineEvent[]; hasMore: boolean; prepend?: boolean; activeStream?: Stream }
  | { type: "optimisticEvent"; thread: string; event: TimelineEvent }
  | { type: "eventsFailed"; thread: string }
  | { type: "activeThread"; thread: string }
  | { type: "goals"; goals: Goal[] }
  | { type: "settings"; settings: SettingsView }
  | { type: "tab"; tab: Tab }
  | { type: "tabBack" }
  | { type: "drawer"; open: boolean }
  | { type: "feedSeen"; at: string }
  | { type: "viewer"; path: string | null }
  | { type: "draft"; text: string | null }
  | { type: "draftFiles"; files: AttachmentInfo[] | null }
  | { type: "draftThread"; title: string | null }
  | { type: "onboardingDismissed" }
  | { type: "setupReopen" }
  | { type: "signInSkipped" }
  | { type: "toast"; toast: string | null };

const LITE_KEY = "nanomuse.ui.lite";

/**
 * The id of a side chat nothing has been sent to yet: it opened from New and lives on this
 * page alone, so it is not a record anywhere until the first message creates it there.
 */
export const DRAFT_THREAD = "draft";

/** `?ui=lite` on this load, or remembered from an earlier one in this tab. */
function liteFromUrl(): boolean {
  try {
    const url = new URL(window.location.href);
    if (url.searchParams.get("ui") === "lite") {
      sessionStorage.setItem(LITE_KEY, "1");
      return true;
    }
    if (url.searchParams.get("ui") === "full") {
      sessionStorage.removeItem(LITE_KEY);
      return false;
    }
    return sessionStorage.getItem(LITE_KEY) === "1";
  } catch {
    return false;
  }
}

export const initial: AppState = {
  connected: false,
  loaded: false,
  authError: false,
  gone: false,
  error: null,
  version: "",
  profile: null,
  status: { state: "idle", detail: "", thread: "main" },
  threads: [],
  activeThread: "main",
  events: {},
  hasMore: {},
  streams: {},
  goals: [],
  settings: null,
  goalsVersion: 0,
  memoryVersion: 0,
  remindersVersion: 0,
  calendarVersion: 0,
  pendingApprovals: [],
  feedVersion: 0,
  feedSeenAt: readStorage(FEED_SEEN_KEY) ?? "",
  viewer: null,
  draft: null,
  draftFiles: null,
  draftThread: null,
  connectionsVersion: 0,
  skillsVersion: 0,
  onboardingDismissed: false,
  setupReopened: false,
  signInSkipped: readStorage(SIGN_IN_SKIPPED_KEY) === "1",
  tab: "chat",
  tabHistory: [],
  toast: null,
  drawer: false,
  finishedAt: 0,
  mishapAt: 0,
  hub: null,
  devices: null,
  codingLive: {},
  lite: liteFromUrl(),
  hands: null,
  holds: [],
  handsLive: null,
  studio: null,
  working: {},
  firstrun: null,
  trashCount: 0,
  sync: null,
  syncProgress: null,
  appUpdate: null,
};

function upsertApproval(list: ApprovalEvent[], ev: TimelineEvent): ApprovalEvent[] {
  if (ev.type !== "approval") return list;
  const rest = list.filter((a) => a.id !== ev.id);
  return ev.status === "pending" ? [...rest, ev] : rest;
}

/** Events that belong in the Feed: background work, cards waiting for you, artifacts. */
function isFeedWorthy(ev: TimelineEvent): boolean {
  if (ev.type === "approval" || ev.type === "question") return true;
  return ev.source === "background" || ev.source === "goal";
}

/**
 * Events as the chat keeps them. A `hold` event's `ts` is seconds since the epoch (contract
 * C1, shared with the native apps); everything else in the timeline is ISO, so it is made
 * ISO here and the dividers, the thread list and the cards read one kind of time.
 */
export function normalizeEvent<T extends TimelineEvent>(ev: T): T {
  const ts = ev.ts as unknown;
  if (typeof ts !== "number") return ev;
  return { ...ev, ts: new Date(ts * 1000).toISOString() };
}

export function upsertEvent(list: TimelineEvent[] | undefined, ev: TimelineEvent): TimelineEvent[] {
  const events = list ?? [];
  const idx = events.findIndex((e) => e.id === ev.id);
  if (idx >= 0) {
    const next = events.slice();
    next[idx] = ev;
    return next;
  }
  // A row pulled from another device of the account (C8) may be older than what is on
  // screen: it goes where its time says, after anything written here in the same moment.
  // Everything else happens now and lands at the end.
  if (ev.synced && ev.ts && events.length > 0 && (events[events.length - 1]?.ts ?? "") > ev.ts) {
    let at = events.length;
    while (at > 0 && (events[at - 1]?.ts ?? "") > ev.ts) at -= 1;
    return [...events.slice(0, at), ev, ...events.slice(at)];
  }
  return [...events, ev];
}

/** The open holds: a hold that went off leaves the list, one that went on joins it. */
function upsertHold(list: HoldEvent[], ev: HoldEvent): HoldEvent[] {
  const rest = list.filter((h) => h.id !== ev.id);
  return ev.status === "on" ? [...rest, ev] : rest;
}

function upsertThread(list: ThreadMeta[], meta: ThreadMeta): ThreadMeta[] {
  const idx = list.findIndex((t) => t.id === meta.id);
  if (idx >= 0) {
    const next = list.slice();
    next[idx] = meta;
    return next;
  }
  return [...list, meta];
}

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "hello": {
      const s = action.state;
      for (const k of Object.keys(perThread)) delete perThread[k];
      // Preserve active in-flight streams and merge server's live active streams
      const streams: Record<string, Stream | undefined> = { ...state.streams };
      if (s.streams) {
        for (const [th, st] of Object.entries(s.streams)) {
          if (st) {
            streams[th] = {
              id: st.id,
              text: st.text || streams[th]?.text || "",
              reasoning: st.reasoning || streams[th]?.reasoning || "",
              ended: false,
            };
          }
        }
      }
      return {
        ...state,
        loaded: true,
        authError: false,
        version: s.version,
        profile: s.profile,
        status: s.status,
        streams,
        threads: s.threads,
        goals: s.goals,
        settings: s.settings,
        pendingApprovals: s.pending_approvals,
        feedVersion: state.feedVersion + 1,
        activeThread: s.threads.some((t) => t.id === state.activeThread) || state.activeThread === DRAFT_THREAD ? state.activeThread : "main",
        hub: s.hub ?? state.hub,
        hands: s.hands ?? state.hands,
        holds: (s.holds ?? []).map(normalizeEvent),
        working: liveWorking(s.working),
        firstrun: s.firstrun ?? state.firstrun,
        trashCount: s.trash_count ?? state.trashCount,
      };
    }
    case "connection":
      return { ...state, connected: action.connected };
    case "authError":
      return { ...state, authError: true, connected: false };
    case "gone":
      return { ...state, gone: true, connected: false };
    case "error":
      return { ...state, error: action.error };
    case "optimisticEvent": {
      const existing = state.events[action.thread] ?? [];
      return {
        ...state,
        events: { ...state.events, [action.thread]: upsertEvent(existing, action.event) },
      };
    }
    case "events": {
      const existing = state.events[action.thread] ?? [];
      const incoming = action.events.map(normalizeEvent);
      let merged: TimelineEvent[];
      if (action.prepend) {
        merged = [...incoming, ...existing.filter((e) => !incoming.some((n) => n.id === e.id))];
      } else {
        const incomingIds = new Set(incoming.map((e) => e.id));
        const newerExisting = existing.filter((e) => !incomingIds.has(e.id));
        merged = newerExisting.length > 0 ? [...incoming, ...newerExisting] : incoming;
      }
      let streams = state.streams;
      if (action.activeStream) {
        const cur = streams[action.thread];
        streams = {
          ...streams,
          [action.thread]: {
            id: action.activeStream.id,
            text: action.activeStream.text || cur?.text || "",
            reasoning: action.activeStream.reasoning || cur?.reasoning || "",
            ended: false,
          },
        };
      }
      return {
        ...state,
        events: { ...state.events, [action.thread]: merged },
        hasMore: { ...state.hasMore, [action.thread]: action.hasMore },
        streams,
      };
    }
    case "eventsFailed":
      // the timeline could not be loaded: show the empty chat (with a toast) instead of nothing
      return {
        ...state,
        events: { ...state.events, [action.thread]: state.events[action.thread] ?? [] },
        toast: t("Could not load the conversation. Open the chat again to retry."),
      };
    case "activeThread":
      return { ...state, activeThread: action.thread, tab: "chat" };
    case "goals":
      return { ...state, goals: action.goals };
    case "settings":
      return { ...state, settings: action.settings, profile: action.settings.profile };
    case "tab":
      if (action.tab === state.tab) return state;
      return { ...state, tab: action.tab, tabHistory: [...state.tabHistory, state.tab].slice(-20) };
    case "tabBack": {
      // skip over entries equal to the screen being left, then land on the one before it
      const rest = [...state.tabHistory];
      let to: Tab = "chat";
      while (rest.length) {
        const prev = rest.pop() as Tab;
        if (prev !== state.tab) {
          to = prev;
          break;
        }
      }
      return { ...state, tab: to, tabHistory: rest };
    }
    case "drawer":
      return { ...state, drawer: action.open };
    case "feedSeen":
      writeStorage(FEED_SEEN_KEY, action.at);
      return { ...state, feedSeenAt: action.at };
    case "viewer":
      return { ...state, viewer: action.path };
    case "draft":
      return { ...state, draft: action.text };
    case "draftFiles":
      return { ...state, draftFiles: action.files };
    case "draftThread":
      return { ...state, draftThread: action.title === null ? null : { title: action.title } };
    case "onboardingDismissed":
      return { ...state, onboardingDismissed: true, setupReopened: false };
    case "setupReopen":
      return { ...state, setupReopened: true, onboardingDismissed: false };
    case "signInSkipped":
      return { ...state, signInSkipped: true };
    case "toast":
      return { ...state, toast: action.toast };
    case "ws":
      return applyWs(state, action.msg);
    case "wsBatch": {
      let next = state;
      for (const m of action.msgs) next = applyWs(next, m);
      return next;
    }
    default:
      return state;
  }
}

function applyWs(state: AppState, msg: WsMessage): AppState {
  switch (msg.kind) {
    case "hello":
      return reducer(state, { type: "hello", state: msg.state });
    case "event":
    case "update": {
      const ev = normalizeEvent(msg.event);
      const streams = { ...state.streams };
      const stream = streams[ev.thread];
      if (ev.type === "assistant" && stream && stream.id === ev.id) streams[ev.thread] = undefined;
      const threads = state.threads.map((t) =>
        t.id === ev.thread && msg.kind === "event" ? { ...t, updated_at: ev.ts } : t,
      );
      // Only threads whose history has been loaded get the event merged in; the rest are
      // fetched when opened. The approvals queue and the Feed follow every thread.
      const loaded = state.events[ev.thread] !== undefined;
      const isActive = ev.thread === state.activeThread;
      const mishap = (ev.type === "tool" && (ev.status === "error" || ev.status === "blocked")) || (ev.type === "notice" && ev.level === "error");
      // the reply from the device that was working arrived: its line goes (C9)
      let working = state.working;
      const line = working[ev.thread];
      if (line && ev.type === "assistant" && ev.synced && ev.via_device === line.device) {
        working = { ...working, [ev.thread]: undefined };
      }
      return {
        ...state,
        streams,
        threads,
        working,
        events: loaded
          ? { ...state.events, [ev.thread]: upsertEvent(state.events[ev.thread], ev) }
          : isActive
          ? { ...state.events, [ev.thread]: [ev] }
          : state.events,
        holds: ev.type === "hold" ? upsertHold(state.holds, ev) : state.holds,
        pendingApprovals: upsertApproval(state.pendingApprovals, ev),
        feedVersion: isFeedWorthy(ev) ? state.feedVersion + 1 : state.feedVersion,
        mishapAt: mishap ? Date.now() : state.mishapAt,
      };
    }
    case "stream_start": {
      const anchor = state.events[msg.thread]?.slice(-1)[0]?.id;
      return {
        ...state,
        streams: {
          ...state.streams,
          [msg.thread]: { id: msg.id, text: "", reasoning: "", ended: false, anchor },
        },
      };
    }
    case "delta": {
      const current = state.streams[msg.thread];
      const anchor = current?.anchor ?? state.events[msg.thread]?.slice(-1)[0]?.id;
      const stream: Stream =
        current && current.id === msg.id
          ? { ...current, text: current.text + msg.text }
          : { id: msg.id, text: msg.text, reasoning: "", ended: false, anchor };
      return { ...state, streams: { ...state.streams, [msg.thread]: stream } };
    }
    case "reasoning_delta": {
      const current = state.streams[msg.thread];
      const anchor = current?.anchor ?? state.events[msg.thread]?.slice(-1)[0]?.id;
      const stream: Stream =
        current && current.id === msg.id
          ? { ...current, reasoning: current.reasoning + msg.text }
          : { id: msg.id, text: "", reasoning: msg.text, ended: false, anchor };
      return { ...state, streams: { ...state.streams, [msg.thread]: stream } };
    }
    case "stream_end": {
      const current = state.streams[msg.thread];
      if (!current || current.id !== msg.id) return state;
      // Keep it until the persisted assistant event replaces it (avoids flicker);
      // an empty stream, or one the server says was not a reply, can go right away.
      if (msg.discard || !current.text.trim()) return { ...state, streams: { ...state.streams, [msg.thread]: undefined } };
      return { ...state, streams: { ...state.streams, [msg.thread]: { ...current, ended: true } } };
    }
    case "status": {
      const st = msg.status;
      const streams = { ...state.streams };
      const s = streams[st.thread];
      if (s?.ended && st.state !== "working") streams[st.thread] = undefined;
      const overall = pickOverall(state.status, st);
      const finished = state.status.state === "working" && overall.state === "idle";
      return { ...state, status: overall, streams, finishedAt: finished ? Date.now() : state.finishedAt };
    }
    case "thread":
      return { ...state, threads: upsertThread(state.threads, msg.thread) };
    case "thread_deleted": {
      const events = { ...state.events };
      delete events[msg.thread];
      return {
        ...state,
        events,
        holds: state.holds.filter((h) => h.thread !== msg.thread),
        threads: state.threads.filter((t) => t.id !== msg.thread),
        pendingApprovals: state.pendingApprovals.filter((a) => a.thread !== msg.thread),
        activeThread: state.activeThread === msg.thread ? "main" : state.activeThread,
      };
    }
    case "thread_cleared":
      return {
        ...state,
        events: { ...state.events, [msg.thread]: [] },
        holds: state.holds.filter((h) => h.thread !== msg.thread),
        pendingApprovals: state.pendingApprovals.filter((a) => a.thread !== msg.thread),
      };
    case "event_removed": {
      const list = state.events[msg.thread];
      if (!list) return state;
      const next = list.filter((e) => e.id !== msg.id);
      if (next.length === list.length) return state;
      return {
        ...state,
        events: { ...state.events, [msg.thread]: next },
        threads: state.threads.map((t) =>
          t.id === msg.thread ? { ...t, events: Math.max(0, t.events - 1) } : t,
        ),
      };
    }
    case "firstrun":
      // the first conversation moved on (contract C4): the chat redraws its chooser
      return { ...state, firstrun: msg.firstrun };
    case "trash":
      return { ...state, trashCount: msg.count };
    case "sync":
      return { ...state, sync: msg.sync };
    case "sync_progress":
      if (msg.done) {
        const n = Number(msg.applied ?? 0);
        const peer = String(msg.peer ?? "");
        return {
          ...state,
          syncProgress: null,
          toast: n > 0 ? t("Synced {n} messages with {peer}.", { n: String(n), peer }) : state.toast,
        };
      }
      return {
        ...state,
        syncProgress: {
          peer: String(msg.peer ?? ""),
          applied: Number(msg.applied ?? 0),
          done: false,
        },
      };
    case "paired":
      return { ...state, toast: t("Paired with {peer}.", { peer: msg.name }) };
    case "app_update":
      return { ...state, appUpdate: msg.update };
    case "working": {
      // another device started or finished a turn on a synced chat (C9); a `false` from a
      // device other than the one shown changes nothing
      const { kind: _kind, working: on, ...who } = msg;
      void _kind;
      const current = state.working[who.thread];
      if (on) return { ...state, working: { ...state.working, [who.thread]: who } };
      if (!current || current.device !== who.device) return state;
      return { ...state, working: { ...state.working, [who.thread]: undefined } };
    }
    case "goals":
      return { ...state, goalsVersion: state.goalsVersion + 1 };
    case "memory":
      return { ...state, memoryVersion: state.memoryVersion + 1 };
    case "reminders":
    case "triggers":
      return { ...state, remindersVersion: state.remindersVersion + 1 };
    case "calendar":
      return { ...state, calendarVersion: state.calendarVersion + 1 };
    case "feed_posts":
      return { ...state, feedVersion: state.feedVersion + 1 };
    case "profile":
      return {
        ...state,
        profile: msg.profile,
        settings: state.settings ? { ...state.settings, profile: msg.profile } : state.settings,
      };
    case "settings":
      return { ...state, settings: msg.settings, profile: msg.settings.profile };
    case "connections":
      return { ...state, connectionsVersion: state.connectionsVersion + 1 };
    case "hub":
      return { ...state, hub: msg.hub };
    case "devices":
      return { ...state, devices: msg.devices };
    case "coding": {
      const id = msg.run?.id ?? msg.event.run;
      if (!id) return state;
      const prev = state.codingLive[id];
      const run: CodingRun | undefined = msg.run ?? prev?.run;
      if (!run) return state;
      const events = msg.event.kind === "run" ? (prev?.events ?? []) : [...(prev?.events ?? []), msg.event].slice(-400);
      let current = prev?.current ?? "";
      if (msg.event.kind === "text") current = msg.event.partial ? current + (msg.event.text ?? "") : "";
      if (msg.event.kind === "done" || msg.event.kind === "error") current = "";
      const live = { ...state.codingLive, [id]: { run: { ...run, device: msg.device ?? run.device }, events, current } };
      // keep the last 20 runs on the page
      const ids = Object.keys(live);
      if (ids.length > 20) for (const old of ids.sort((a, b) => live[a].run.started_at - live[b].run.started_at).slice(0, ids.length - 20)) delete live[old];
      return { ...state, codingLive: live };
    }
    case "hands_state":
      return { ...state, hands: msg.hands };
    case "studio":
      return { ...state, studio: msg.current };
    case "hands": {
      const { kind: _kind, ...live } = msg;
      void _kind;
      const ended = live.event === "end" || live.event === "stop";
      return { ...state, handsLive: ended ? null : live };
    }
    case "skills":
      return { ...state, skillsVersion: state.skillsVersion + 1 };
    case "error":
      return { ...state, toast: t(msg.error) };
    case "pong":
      return { ...state, status: msg.status };
    default:
      return state;
  }
}

/** Per-thread statuses arrive one at a time; show the busiest one under the avatar. */
const perThread: Record<string, Status> = {};
function pickOverall(prev: Status, incoming: Status): Status {
  perThread[incoming.thread] = incoming;
  const working = Object.values(perThread).filter((s) => s.state !== "idle");
  if (!working.length) return { state: "idle", detail: "", thread: "main" };
  working.sort((a, b) => Number(a.thread !== "main") - Number(b.thread !== "main"));
  return working[0] ?? prev;
}

interface StoreValue {
  state: AppState;
  dispatch: (a: Action) => void;
  send: (thread: string, text: string, files?: string[], model?: string) => Promise<void>;
  /** *Use nanoMuse Cloud this time*: the words again, one turn on the account's model; rejects with the runtime's sentence */
  sendViaCloud: (thread: string, text: string, files?: string[]) => Promise<void>;
  decide: (id: string, approved: boolean, scope?: string) => Promise<void>;
  loadEvents: (thread: string, before?: string) => Promise<void>;
  refreshGoals: () => Promise<void>;
  refreshSettings: () => Promise<void>;
  refreshHub: () => Promise<void>;
  /** Switch screens. `animate: false` skips the view transition (the drawer uses it: a
   *  transition started while the drawer closes could leave the old picture on screen
   *  in the Android WebView until the next tap). */
  setTab: (tab: Tab, opts?: { animate?: boolean }) => void;
  /** One step back through the screens visited (the chat when there is nothing before). */
  goBack: () => void;
  /** Open or close the chats drawer (the phone's hamburger). */
  setDrawer: (open: boolean) => void;
  openThread: (thread: string) => void;
  deleteEvent: (thread: string, id: string) => Promise<void>;
  markFeedSeen: (at: string) => void;
  openFile: (path: string | null) => void;
  /** Put text in the chat composer and switch to the chat (a skill's "Use", for one). */
  draft: (text: string | null) => void;
  draftFiles: (files: AttachmentInfo[] | null) => void;
  dismissOnboarding: () => void;
  /** Open the guided setup again (a setup cut short, or to change what was chosen). */
  reopenSetup: () => void;
  /** Past the sign-in door with a key of one's own; remembered by this browser. */
  skipSignIn: () => void;
  /** A short message at the bottom of the page. */
  toast: (text: string) => void;
}

const StoreContext = createContext<StoreValue | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  const wsRef = useRef<ReturnType<typeof connectWs> | null>(null);

  // The offline outbox: retry everything queued while the runtime was unreachable, in
  // order. The same event id goes out again, so the server hands an already-landed
  // message back instead of running the turn twice.
  const flushOutbox = useCallback(async () => {
    const queued = outbox();
    if (queued.length === 0) return;
    let sent = 0;
    for (const item of queued) {
      try {
        await api.send(item.thread, item.text, item.files, item.language, item.via, item.id);
        dequeue(item.id);
        sent += 1;
      } catch (e) {
        if (e instanceof AuthError) dispatch({ type: "authError" });
        break; // still offline: keep the rest queued
      }
    }
    if (sent > 0 && outbox().length === 0) {
      dispatch({ type: "toast", toast: t("Sent what was waiting while you were offline.") });
    }
  }, []);

  useEffect(() => {
    const onOnline = () => void flushOutbox();
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [flushOutbox]);
  useEffect(() => {
    getToken();
    // Frames that arrive together are coalesced and applied in one reducer pass.
    // Streaming token deltas are batched per animation frame (~60fps cap) instead
    // of cloning full state on every websocket frame, keeping typing and scroll smooth.
    const queue: WsMessage[] = [];
    let flushTimer = 0;
    let flushRaf = 0;
    const cancelPendingFlush = () => {
      if (flushTimer) {
        window.clearTimeout(flushTimer);
        flushTimer = 0;
      }
      if (flushRaf && typeof window.cancelAnimationFrame === "function") {
        window.cancelAnimationFrame(flushRaf);
        flushRaf = 0;
      }
    };
    const drain = () => {
      cancelPendingFlush();
      if (queue.length === 0) return;
      const batch = queue.splice(0);
      if (batch.length === 1) dispatch({ type: "ws", msg: batch[0] });
      else dispatch({ type: "wsBatch", msgs: batch });
    };
    const scheduleFlush = (streamingDelta: boolean) => {
      if (streamingDelta && typeof window.requestAnimationFrame === "function" && document.visibilityState !== "hidden") {
        if (!flushRaf && !flushTimer) {
          flushRaf = window.requestAnimationFrame(drain);
        }
        return;
      }
      if (!flushTimer) {
        if (flushRaf && typeof window.cancelAnimationFrame === "function") {
          window.cancelAnimationFrame(flushRaf);
          flushRaf = 0;
        }
        flushTimer = window.setTimeout(drain, 0);
      }
    };
    const ws = connectWs({
      onMessage: (msg) => {
        if (typeof window !== "undefined") {
          if (msg.kind === "pair_request") {
            window.dispatchEvent(new CustomEvent("nanomuse:pair-request", { detail: msg }));
          } else if (msg.kind === "personal_cloud") {
            window.dispatchEvent(new CustomEvent("nanomuse:personal-cloud", { detail: msg }));
          } else if (msg.kind === "paired") {
            window.dispatchEvent(new CustomEvent("nanomuse:peer-paired", { detail: msg }));
          }
        }
        const last = queue[queue.length - 1];
        if (
          last &&
          ((msg.kind === "delta" && last.kind === "delta") ||
            (msg.kind === "reasoning_delta" && last.kind === "reasoning_delta")) &&
          last.thread === msg.thread &&
          last.id === msg.id
        ) {
          queue[queue.length - 1] = { ...last, text: last.text + msg.text };
          scheduleFlush(true);
          return;
        }
        queue.push(msg);
        scheduleFlush(msg.kind === "delta" || msg.kind === "reasoning_delta");
      },
      onOpen: () => {
        dispatch({ type: "connection", connected: true });
        void flushOutbox();
        // Announce high-entropy device model + screen geometry so the server can show
        // the real phone model (e.g. "Pixel 9") instead of generic "Android phone".
        void (async () => {
          let model = "";
          let platform_version = "";
          try {
            const uaData = (navigator as unknown as {
              userAgentData?: {
                getHighEntropyValues?: (hints: string[]) => Promise<{ model?: string; platformVersion?: string }>;
              };
            }).userAgentData;
            if (uaData?.getHighEntropyValues) {
              const high = await uaData.getHighEntropyValues(["model", "platformVersion"]);
              model = high.model ?? "";
              platform_version = high.platformVersion ?? "";
            }
          } catch {
            // high-entropy hints optional
          }
          ws.send({
            kind: "client_info",
            model,
            platform_version,
            screen_w: typeof window !== "undefined" ? (window.screen?.width ?? 0) : 0,
            screen_h: typeof window !== "undefined" ? (window.screen?.height ?? 0) : 0,
          });
        })();
      },
      onClose: () => dispatch({ type: "connection", connected: false }),
      onAuthError: () => dispatch({ type: "authError" }),
      onGone: () => dispatch({ type: "gone" }),
    });
    wsRef.current = ws;
    api.state()
      .then((s) => dispatch({ type: "hello", state: s }))
      .catch((e) => {
        if (e instanceof AuthError) dispatch({ type: "authError" });
        else dispatch({ type: "error", error: String(e.message ?? e) });
      });
    return () => {
      cancelPendingFlush();
      ws.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- flushOutbox is stable (no deps); the socket is mounted once
  }, [flushOutbox]);

  const loadGeneration = useRef<Record<string, number>>({});
  const loadEvents = useCallback(async (thread: string, before?: string) => {
    // a fresh load (not a scroll-back) supersedes any earlier one still in flight for
    // the same thread: the older answer is dropped when it finally comes
    const generation = before ? undefined : (loadGeneration.current[thread] = (loadGeneration.current[thread] ?? 0) + 1);
    try {
      const data = await api.events(thread, 150, before);
      if (generation !== undefined && loadGeneration.current[thread] !== generation) return;
      dispatch({ type: "events", thread, events: data.events, hasMore: data.has_more, prepend: !!before, activeStream: data.active_stream as Stream | undefined });
    } catch (e) {
      if (e instanceof AuthError) dispatch({ type: "authError" });
      else dispatch({ type: "eventsFailed", thread });
    }
  }, []);

  // The account as the runtime sees it, fetched directly: after a sign-in the socket
  // usually brings it, but the gate must not depend on the socket being up.
  const refreshHub = useCallback(async () => {
    try {
      dispatch({ type: "ws", msg: { kind: "hub", hub: await api.hub() } });
    } catch {
      /* offline */
    }
  }, []);

  useEffect(() => {
    // the draft side chat is not a record yet: nothing to load, the composer alone
    if (state.loaded && state.activeThread !== DRAFT_THREAD) void loadEvents(state.activeThread);
  }, [state.loaded, state.activeThread, loadEvents]);

  // Reload the current thread after a reconnect so nothing is missed.
  const wasConnected = useRef(false);
  useEffect(() => {
    if (state.connected && wasConnected.current && state.loaded && state.activeThread !== DRAFT_THREAD) {
      void loadEvents(state.activeThread);
    }
    wasConnected.current = state.connected;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- loadEvents identity changes with threads
  }, [state.connected, state.loaded, state.activeThread, loadEvents]);

  // Automatically trigger catch-up peer sync when network connectivity returns after offline work.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => {
      void api
        .syncNow()
        .then((sync) => dispatch({ type: "ws", msg: { kind: "sync", sync } }))
        .catch(() => undefined);
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, []);

  const refreshGoals = useCallback(async () => {
    try {
      dispatch({ type: "goals", goals: await api.goals() });
    } catch {
      /* offline */
    }
  }, []);

  useEffect(() => {
    if (state.loaded) void refreshGoals();
  }, [state.goalsVersion, state.loaded, refreshGoals]);

  const refreshSettings = useCallback(async () => {
    try {
      dispatch({ type: "settings", settings: await api.settings() });
    } catch {
      /* offline */
    }
  }, []);

  useEffect(() => {
    if (!state.toast) return;
    const t = window.setTimeout(() => dispatch({ type: "toast", toast: null }), 3500);
    return () => window.clearTimeout(t);
  }, [state.toast]);

  // The service worker (push + app badge) and where a notification tap should land.
  useEffect(() => {
    void registerWorker();
    // `?thread=<id>` opens a chat, `?tab=goals` (feed, ideas, library, connections) a tab —
    // used by notification taps and by links into the app.
    // `?draft=<text>&attach=<json>` puts text and already-uploaded files into the composer
    // without sending — how "Share to MiniMuse" from another app on the phone arrives.
    const openFromUrl = (href: string) => {
      const url = new URL(href, window.location.origin);
      const thread = url.searchParams.get("thread");
      const tab = url.searchParams.get("tab");
      if (thread) dispatch({ type: "activeThread", thread });
      if (tab && TAB_NAMES.includes(tab as Tab) && !thread) dispatch({ type: "tab", tab: tab as Tab });
      else dispatch({ type: "tab", tab: "chat" });
      const text = url.searchParams.get("draft");
      if (text) dispatch({ type: "draft", text });
      const attach = url.searchParams.get("attach");
      if (attach) {
        try {
          const files = JSON.parse(attach) as AttachmentInfo[];
          if (Array.isArray(files) && files.length) dispatch({ type: "draftFiles", files: files.filter((f) => f && typeof f.path === "string") });
        } catch {
          /* not ours */
        }
      }
    };
    const initial = new URL(window.location.href);
    if (["thread", "tab", "draft", "attach", "ui"].some((k) => initial.searchParams.has(k))) {
      openFromUrl(initial.href);
      for (const k of ["thread", "tab", "draft", "attach", "ui"]) initial.searchParams.delete(k);
      window.history.replaceState({}, "", initial.toString());
    }
    const onMessage = (ev: MessageEvent) => {
      if (ev.data && ev.data.type === "open") openFromUrl(String(ev.data.url || "/"));
      // the notification's Stop now action: App stops the running turn
      if (ev.data && ev.data.type === "stop-now") window.dispatchEvent(new Event("nanomuse:stop-now"));
    };
    navigator.serviceWorker?.addEventListener("message", onMessage);
    // Inside another page's frame (the simulated phone of the showcase), the page may hand
    // over a draft the same way a link does — `{type: "nanomuse:draft", text}` from the
    // parent window, and only from it. Text in the composer is all it can do; sending is a tap.
    const onParent = (ev: MessageEvent) => {
      if (window.parent === window || ev.source !== window.parent) return;
      const data = ev.data as { type?: unknown; text?: unknown } | null;
      if (!data || data.type !== "nanomuse:draft" || typeof data.text !== "string") return;
      dispatch({ type: "tab", tab: "chat" });
      dispatch({ type: "draft", text: data.text.slice(0, 4000) });
    };
    window.addEventListener("message", onParent);
    return () => {
      navigator.serviceWorker?.removeEventListener("message", onMessage);
      window.removeEventListener("message", onParent);
    };
  }, []);

  // The embedding page learns when the app is up (and which agent it shows), so it can hand
  // over a draft only once there is a composer to put it in — and which colour scheme the app
  // is in, so the frame around it can match (`nanomuse:theme` again whenever that changes).
  const theme = useTheme();
  useEffect(() => {
    if (!state.loaded) return;
    tellParent({ type: "nanomuse:ready", name: state.profile?.name ?? "", theme });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.loaded, state.profile?.name]);
  useEffect(() => {
    if (!state.loaded) return;
    tellParent({ type: "nanomuse:theme", theme });
  }, [state.loaded, theme]);

  // The number on the app icon: cards waiting for you.
  useEffect(() => {
    if (state.loaded) setAppBadge(state.pendingApprovals.length);
  }, [state.loaded, state.pendingApprovals.length]);

  const value = useMemo<StoreValue>(
    () => ({
      state,
      dispatch,
      send: async (thread, text, files = [], model = "") => {
        if (!text.trim() && files.length === 0) return;
        const language = getLocale();
        // a new chat from New with nothing sent yet: the first message itself brings the
        // record onto the runtime, with the title already picked, if one was picked
        let tid = thread;
        if (tid === DRAFT_THREAD) {
          try {
            // no placeholder name: the runtime names the chat from the first message
            // (see Service._maybe_auto_title), and the list shows it as soon as it does
            const created = await api.createThread(state.draftThread?.title?.trim() || "");
            tid = created.id;
            dispatch({ type: "ws", msg: { kind: "thread", thread: created } });
            dispatch({ type: "draftThread", title: null });
            dispatch({ type: "activeThread", thread: created.id });
          } catch (e) {
            if (e instanceof AuthError) {
              dispatch({ type: "authError" });
              throw e;
            }
            dispatch({ type: "toast", toast: (e as Error).message || t("Could not start the chat") });
            throw e;
          }
        }
        const clientEventId = "u_" + Math.random().toString(36).slice(2, 10);
        const optimisticEvent: TimelineEvent = {
          id: clientEventId,
          type: "user",
          text: text.trim(),
          ts: new Date().toISOString(),
          thread: tid,
          files: files.map((f) => ({ path: f, name: f.split("/").pop() || f, kind: "other", size: 0, mime: "" })),
        };
        dispatch({
          type: "optimisticEvent",
          thread: tid,
          event: optimisticEvent,
        });
        try {
          if (files.length > 0 || !wsRef.current?.send({ kind: "send", thread: tid, text, language, id: clientEventId, model })) {
            await api.send(tid, text, files, language, "", clientEventId, model);
          }
        } catch (e) {
          if (e instanceof AuthError) {
            dispatch({ type: "authError" });
            throw e;
          }
          // Offline: keep the message on this device and send it when we can reach
          // the runtime again. The bubble stays (marked "sending") and is replaced
          // by the server's echo once the retry lands.
          enqueue({
            id: clientEventId,
            thread: tid,
            text: text.trim(),
            files,
            language,
            via: "",
            at: new Date().toISOString(),
          });
          dispatch({
            type: "optimisticEvent",
            thread: tid,
            event: { ...optimisticEvent, pending: true },
          });
          dispatch({ type: "toast", toast: t("Saved on this device. It will send when you are back online.") });
        }
      },
      sendViaCloud: async (thread, text, files = []) => {
        if (!text.trim() && files.length === 0) return;
        const clientEventId = "u_" + Math.random().toString(36).slice(2, 10);
        const optimisticEvent: TimelineEvent = {
          id: clientEventId,
          type: "user",
          text: text.trim(),
          ts: new Date().toISOString(),
          thread,
          files: files.map((f) => ({ path: f, name: f.split("/").pop() || f, kind: "other", size: 0, mime: "" })),
        };
        dispatch({
          type: "optimisticEvent",
          thread,
          event: optimisticEvent,
        });
        try {
          await api.send(thread, text, files, getLocale(), "cloud", clientEventId);
        } catch (e) {
          if (e instanceof AuthError) {
            dispatch({ type: "authError" });
            throw e;
          }
          enqueue({
            id: clientEventId,
            thread,
            text: text.trim(),
            files,
            language: getLocale(),
            via: "cloud",
            at: new Date().toISOString(),
          });
          dispatch({
            type: "optimisticEvent",
            thread,
            event: { ...optimisticEvent, pending: true },
          });
          dispatch({ type: "toast", toast: t("Saved on this device. It will send when you are back online.") });
        }
      },
      decide: async (id, approved, scope = "once") => {
        try {
          if (!wsRef.current?.send({ kind: "approval", id, approved, scope })) {
            await api.decide(id, approved, scope);
          }
        } catch (e) {
          if (e instanceof AuthError) dispatch({ type: "authError" });
          throw e;
        }
      },
      loadEvents,
      refreshGoals,
      refreshSettings,
      refreshHub,
      setTab: (tab, opts) => {
        recordDebugEvent("nav", `tab -> ${tab}`);
        // one cross-fade (and a hair of rise) between screens, on the browsers that support
        // the view transition: the switch feels connected instead of a hard cut. The Android
        // app's WebView skips it: there a transition racing a drawer or a lazy screen could
        // keep showing the old screen until the next tap.
        const inAndroidApp = typeof window !== "undefined" && !!(window as unknown as { NanoMuseAndroid?: unknown }).NanoMuseAndroid;
        const animate =
          opts?.animate !== false &&
          !inAndroidApp &&
          typeof document !== "undefined" &&
          typeof document.startViewTransition === "function" &&
          !matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (animate) {
          document.startViewTransition(() => {
            flushSync(() => dispatch({ type: "tab", tab }));
          });
        } else {
          dispatch({ type: "tab", tab });
        }
      },
      goBack: () => dispatch({ type: "tabBack" }),
      setDrawer: (open) => dispatch({ type: "drawer", open }),
      openThread: (thread) => dispatch({ type: "activeThread", thread }),
      deleteEvent: async (thread, id) => {
        await api.deleteEvent(thread, id);
        dispatch({ type: "ws", msg: { kind: "event_removed", thread, id } });
      },
      markFeedSeen: (at) => dispatch({ type: "feedSeen", at }),
      openFile: (path) => dispatch({ type: "viewer", path }),
      draftFiles: (files) => dispatch({ type: "draftFiles", files }),
      draft: (text) => {
        dispatch({ type: "draft", text });
        if (text !== null) dispatch({ type: "tab", tab: "chat" });
      },
      dismissOnboarding: () => dispatch({ type: "onboardingDismissed" }),
      reopenSetup: () => dispatch({ type: "setupReopen" }),
      skipSignIn: () => {
        writeStorage(SIGN_IN_SKIPPED_KEY, "1");
        dispatch({ type: "signInSkipped" });
      },
      toast: (text) => dispatch({ type: "toast", toast: t(text) }),
    }),
    [state, loadEvents, refreshGoals, refreshSettings, refreshHub],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreValue {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore outside provider");
  return ctx;
}
