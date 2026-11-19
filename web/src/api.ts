import type {
  ActivityData,
  ApiConnection,
  ApiEndpoint,
  ApiPreset,
  AttachmentInfo,
  CalendarData,
  CalendarEvent,
  CallCapabilities,
  ChatGptLogin,
  ChatGptStatus,
  ChromeBridgeStatus,
  CloudAccount,
  CloudEvent,
  CloudConfig,
  CloudMe,
  CloudSession,
  CodingAgent,
  CodingRun,
  CodingSession,
  ComputerWindowInfo,
  ConnectionsData,
  CustomEndpoint,
  Contact,
  EmailAccount,
  FeedItem,
  FeedPostsData,
  FileInfo,
  FirstRunView,
  Goal,
  GoalKind,
  GoalPriority,
  HandsStatus,
  HoldEvent,
  HubView,
  IdeasData,
  LocalDevicesView,
  LocationPlace,
  LocationSummary,
  LocationTrafficEstimate,
  LocationTrip,
  MediaSlotData,
  MemoryChange,
  MemoryItem,
  NudgesView,
  PersonalCloudStatus,
  ProvidersView,
  PushInfo,
  Reminder,
  ReminderKind,
  SettingsView,
  SkillDetail,
  SkillInfo,
  SkillsData,
  StateSnapshot,
  StudioSession,
  StudioView,
  NearbyPeer,
  PairCodeInfo,
  SyncState,
  TrashedThread,
  TestResult,
  ThreadMeta,
  TidyReport,
  TimelineEvent,
  Trigger,
  TriggerKind,
  TriggersData,
  UpcomingData,
  UpdateView,
  UsageLimitRow,
  UsageSummary,
  WatchCodeInfo,
  WatchDevicesInfo,
  WatchInstallOptions,
  WatchInstallResult,
  GoogleStatus,
  WsMessage,
  LockView,
  PasswordEntry,
  PasswordsView,
  PasswordImportResult,
} from "./types";
import { signedParams } from "./ticket";
import { readStorage, writeStorage } from "./util";

const TOKEN_KEY = "nanomuse_token";

export class AuthError extends Error {
  constructor() {
    super("unauthorized");
  }
}

/** The token in a link the runtime printed: `#token=…` (the fragment never reaches a
 *  server, so it is in no access log) or, from older links, `?token=…`. Taken off the
 *  address once read. */
export function tokenFromLink(url: URL): string | null {
  const parts = url.hash.replace(/^#/, "").split("&").filter(Boolean);
  const index = parts.findIndex((p) => p.startsWith("token="));
  if (index >= 0) {
    const token = decodeURIComponent(parts[index].slice("token=".length));
    parts.splice(index, 1);
    url.hash = parts.length ? `#${parts.join("&")}` : "";
    return token || null;
  }
  const query = url.searchParams.get("token");
  if (query) {
    url.searchParams.delete("token");
    return query;
  }
  return null;
}

/** Read the token from the link (first visit via QR code) or localStorage. */
export function getToken(): string {
  const url = new URL(window.location.href);
  const fromUrl = tokenFromLink(url);
  if (fromUrl) {
    writeStorage(TOKEN_KEY, fromUrl);
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }
  return readStorage(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  writeStorage(TOKEN_KEY, token || null);
}

const DEVICE_KEY = "nanomuse_device_id";

/** The browser's own device id for the per-device lock; made once and kept. */
function lockDeviceId(): string {
  let id = readStorage(DEVICE_KEY) || "";
  if (!id) {
    id =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : "d-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    writeStorage(DEVICE_KEY, id);
  }
  return id;
}

/** Headers for every lock call: this device's id, and the credentials it registered. */
function lockHeaders(): Record<string, string> {
  const local = [readStorage("nanomuse_bio_cred_id"), readStorage("nanomuse_passkey_cred_id")]
    .filter((v) => !!v && v.trim())
    .join(",");
  const headers: Record<string, string> = { "X-Nanomuse-Device": lockDeviceId() };
  if (local) headers["X-Nanomuse-Lock-Local"] = local;
  return headers;
}

/** A workspace file for an `<img>`, `<video>`, `<iframe>` or a download link: signed with
 *  the token rather than carrying it (ticket.ts) — the link opens this path for a few hours
 *  and nothing else, should it end up in a log. */
export function fileUrl(path: string, download = false): string {
  const token = getToken();
  const params = token ? signedParams(token, `/api/files/${path}`) : new URLSearchParams();
  if (download) params.set("download", "1");
  const q = params.toString();
  return `/api/files/${path.split("/").map(encodeURIComponent).join("/")}${q ? `?${q}` : ""}`;
}

/** A browser frame (JPEG) kept in memory on the server for the current run. */
export function frameUrl(thread: string, frame: string): string {
  const token = getToken();
  const q = token ? `?${signedParams(token, `/api/browser/${thread}/frames/${frame}.jpg`)}` : "";
  return `/api/browser/${encodeURIComponent(thread)}/frames/${encodeURIComponent(frame)}.jpg${q}`;
}

/** A hands screen/window JPEG frame kept in memory on the server. */
export function handsFrameUrl(thread: string, frame: string): string {
  const token = getToken();
  const q = token ? `?${signedParams(token, `/api/hands/${thread}/frames/${frame}.jpg`)}` : "";
  return `/api/hands/${encodeURIComponent(thread)}/frames/${encodeURIComponent(frame)}.jpg${q}`;
}

export function chromeExtensionZipUrl(): string {
  return "/api/chrome/extension.zip";
}

export interface BrowserControl {
  action: "click" | "type" | "key" | "scroll" | "navigate" | "look" | "take_over" | "back" | "reload" | "handed_back";
  x?: number;
  y?: number;
  text?: string;
  key?: string;
  dy?: number;
  url?: string;
}

/** The switches of Settings → Import & export (POST /api/data/import). */
export type ImportOptions = {
  /** imported conversations become chats; a same-titled chat merges in time order */
  restore_chats: boolean;
  /** imported messages also go into the deep archive the agent searches */
  background_memory: boolean;
  /** what happens to the code Muse built */
  apps: "save" | "restore" | "both" | "neither";
  /** what happens to the Muse main chat */
  main_chat: "side" | "replace" | "merge";
  /** keep the uploaded file itself under workspace/imports/ instead of deleting it */
  keep_copy?: boolean;
};

/** The archive's own counts against what this import staged, line by line. */
export type ImportVerify = {
  ok: boolean;
  lines: string[];
};

/** What an import found and did, as the runtime reports it. */
export type ImportResult = {
  ok: boolean;
  conversations: number;
  events_added: number;
  threads_created: number;
  threads_merged: number;
  messages_indexed: number;
  memory_files: number;
  apps_saved: number;
  apps_registered: number;
  profile_updated?: boolean;
  avatar_files?: number;
  avatar_moods?: number;
  ui_preferences?: Record<string, string> | null;
  skipped_files: number;
  duplicates: number;
  note: string;
  /** where the original upload was kept, when the switch was on */
  kept_copy?: string;
  /** declared-vs-staged counts, when the archive declared its own */
  verify?: ImportVerify;
};

/** One file kept under workspace/imports/ after a transfer. */
export type KeptCopy = {
  name: string;
  size: number;
  modified: string;
  report: boolean;
};

/** Where the one-tap browser install is. */
export type BrowserInstallState = {
  status: "idle" | "running" | "done" | "error";
  step: string;
  message: string;
  log: string[];
};

/** One import running (or lately finished) in the background. */
export type ImportJobView = {
  id: string;
  name: string;
  phase: "queued" | "reading" | "applying" | "done" | "error";
  done: number;
  total: number;
  percent: number;
  elapsed_s: number;
  result: ImportResult | null;
  error: string;
};

/** One call to the runtime: the bearer token, JSON in and out, 401 as `AuthError`, the
 *  server's `detail` as the error's text. `channels-api.ts` shares it. */
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (init.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    // the browser's "Failed to fetch": the runtime is down or the network is
    throw new Error("Cannot reach your MiniMuse right now.");
  }
  if (res.status === 401) throw new AuthError();
  if (!res.ok) {
    let data: unknown;
    try {
      data = await res.json();
    } catch {
      /* not JSON: the sentence below stands for it */
    }
    throw new Error(failureDetail(res.status, data));
  }
  return (await res.json()) as T;
}

/**
 * What a refused reply says to the person: the runtime's `detail` when it is a sentence (the
 * screens translate the ones they know), otherwise one sentence for the status. A validation
 * error's `detail` is a list and a crashed route sends no JSON; neither is shown as it came,
 * and the status number never is.
 */
export function failureDetail(status: number, data: unknown): string {
  const detail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string" && detail.trim()) return detail;
  return status >= 500 ? "Your MiniMuse hit a problem; try again in a moment." : "Your MiniMuse could not do that.";
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const api = {
  state: () => request<StateSnapshot>("/api/state"),
  // ---- MiniMuse Cloud (the account) and the hub (the other devices)
  cloud: () => request<CloudAccount>("/api/cloud"),
  cloudCode: (identifier: string) => request<{ ok: boolean }>("/api/cloud/code", json({ identifier })),
  cloudVerify: (identifier: string, code: string, invite = "") =>
    request<CloudAccount>("/api/cloud/verify", json(invite ? { identifier, code, invite } : { identifier, code })),
  cloudSignOut: () => request<CloudAccount>("/api/cloud/sign-out", json({})),
  /** *Use MiniMuse Cloud models*: the account's models as a source, on or off; `409 chat_on_cloud` while the chat model is the account's */
  cloudModels: (on: boolean) => request<CloudAccount>("/api/cloud/models", json({ on })),
  cloudLogin: (identifier: string, password: string) => request<CloudAccount>("/api/cloud/login", json({ identifier, password })),
  /** set or change the password; "" with the current one removes it */
  cloudPassword: (password: string, current?: string) => request<CloudAccount>("/api/cloud/password", json({ password, current: current ?? null })),
  cloudSessions: () => request<{ sessions: CloudSession[] }>("/api/cloud/sessions"),
  cloudRevokeSession: (prefix: string) => request<{ sessions: CloudSession[] }>(`/api/cloud/sessions/${encodeURIComponent(prefix)}`, { method: "DELETE" }),
  cloudSignOutAll: (all = false) => request<{ signed_out: number } & CloudAccount>("/api/cloud/sign-out-all", json({ all })),
  cloudEvents: (limit = 50) => request<{ events: CloudEvent[] }>(`/api/cloud/events?limit=${limit}`),
  cloudDelete: () => request<CloudAccount>("/api/cloud/delete", json({})),
  cloudMe: () => request<CloudMe>("/api/cloud/me"),
  /** The relay's public figures (relay 0.15); {} from an older relay. */
  cloudConfig: () => request<CloudConfig>("/api/cloud/config"),
  cloudContribute: (on: boolean) => request<{ on: boolean; samples: number; default_on?: boolean; privacy_url?: string }>("/api/cloud/contribute", json({ on })),
  cloudDeleteSamples: () => request<{ deleted: number }>("/api/cloud/samples", { method: "DELETE" }),
  cloudUseAsModel: (model = "") => request<Record<string, unknown>>("/api/cloud/use-as-model", json({ model })),
  // ---- conversations synced between the account's devices (contract C7)
  syncState: () => request<SyncState>("/api/sync/state"),
  /** off tells the relay, which deletes what it stores; on pushes this device's chats again */
  syncSetState: (enabled: boolean) => request<SyncState>("/api/sync/state", { method: "PUT", body: JSON.stringify({ enabled }) }),
  /** C9: "Also sync side chats" — per device; on pulls the other devices' side chats once from the start */
  syncSetSideChats: (side_chats: boolean) => request<SyncState>("/api/sync/state", { method: "PUT", body: JSON.stringify({ side_chats }) }),
  /** "Delete synced conversations": the relay's store emptied, the switch and the local chats kept */
  syncDelete: () => request<SyncState>("/api/sync/delete", json({})),
  syncPull: () => request<SyncState & { applied: number }>("/api/sync/pull", json({})),
  syncDiscover: () => request<{ nearby: NearbyPeer[]; device_id: string; name: string; urls: string[] }>("/api/sync/discover"),
  syncPairCode: () => request<PairCodeInfo>("/api/sync/pair/code", json({})),
  syncPairClaim: (body: { target: string; code?: string; token?: string; name?: string; sync_side_chats?: boolean }) =>
    request<{
      ok: boolean;
      peer?: string;
      sync: SyncState;
      setup_synced?: { applied?: boolean; ui_preferences?: Record<string, string> | null; profile?: Record<string, string>; onboarded?: boolean };
    }>("/api/sync/pair/claim", json(body)),
  syncSetDeviceOverrides: (body: { enabled?: boolean; keep_local_appearance?: boolean; default_model?: string }) =>
    request<SyncState>("/api/sync/device-overrides", { method: "PUT", body: JSON.stringify(body) }),
  exportSetupBundle: () => request<Record<string, unknown>>("/api/sync/setup-bundle"),
  applySetupBundle: (bundle: Record<string, unknown>) =>
    request<{ applied?: boolean; ui_preferences?: Record<string, string> | null; profile?: Record<string, string>; onboarded?: boolean }>(
      "/api/sync/setup-bundle",
      json(bundle),
    ),
  syncUnpair: (identifier: string) =>
    request<{ ok: boolean } & SyncState>("/api/sync/pair/unpair", json({ identifier })),
  syncNow: () =>
    request<{ ok: boolean; applied: number; pushed: number } & SyncState>("/api/sync/now", json({})),
  /** the avatar studio: whether a face can be drawn, and the session under way */
  avatarView: () => request<StudioView>("/api/avatar"),
  /** a session; `thread` "" runs it from the studio screen, without a card in the chat */
  avatarBegin: (description: string, thread = "main", style = "muse") =>
    request<StudioSession & { available?: boolean; message?: string }>("/api/avatar/begin", json({ description, thread, style })),
  avatarStart: (session: string) => request<StudioSession>("/api/avatar/start", json({ session })),
  avatarChoose: (session: string, index: number) => request<StudioSession>("/api/avatar/choose", json({ session, index })),
  avatarCancel: (session: string) => request<StudioSession>("/api/avatar/cancel", json({ session })),
  /** the poses (and clips) of the face the profile wears, drawn again from its idle still */
  avatarMoods: () => request<StudioSession>("/api/avatar/moods", json({})),
  // ---- coding agents, here or on another computer of yours
  coding: (device = "") => request<{ agents: CodingAgent[]; runs: CodingRun[]; device?: string }>(`/api/coding${device ? `?device=${encodeURIComponent(device)}` : ""}`),
  codingSessions: (q: { agent?: string; limit?: number; workspace?: string; device?: string } = {}) => {
    const params = new URLSearchParams();
    if (q.agent) params.set("agent", q.agent);
    if (q.limit) params.set("limit", String(q.limit));
    if (q.workspace) params.set("workspace", q.workspace);
    if (q.device) params.set("device", q.device);
    const qs = params.toString();
    return request<{ sessions: CodingSession[] }>(`/api/coding/sessions${qs ? `?${qs}` : ""}`);
  },
  codingSession: (agent: string, id: string, device = "") =>
    request<CodingSession>(`/api/coding/sessions/${encodeURIComponent(agent)}/${encodeURIComponent(id)}${device ? `?device=${encodeURIComponent(device)}` : ""}`),
  codingSend: (body: { agent: string; text: string; session_id?: string; workspace?: string; device?: string }) => request<CodingRun>("/api/coding/send", json(body)),
  codingStop: (run: string, device = "") => request<{ stopped: boolean }>("/api/coding/stop", json({ run, device })),
  hub: () => request<HubView>("/api/hub"),
  /** the devices this MiniMuse is built on, without any account */
  devices: () => request<LocalDevicesView>("/api/devices"),
  renameDevice: (id: string, name: string) =>
    request<LocalDevicesView>(`/api/devices/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify({ name }) }),
  updateHub: (body: { enabled?: boolean; remote_control?: boolean; name?: string }) =>
    request<HubView>("/api/hub", { method: "PUT", body: JSON.stringify(body) }),
  joinHub: () => request<HubView>("/api/hub/join", json({})),
  leaveHub: () => request<HubView>("/api/hub/leave", json({})),
  refreshHub: () => request<HubView>("/api/hub/refresh", json({})),
  forgetDevice: (id: string) => request<HubView>(`/api/hub/devices/${encodeURIComponent(id)}`, { method: "DELETE" }),
  askDevice: (device: string, text = "") => request<{ thread: ThreadMeta; event: TimelineEvent | null }>("/api/hub/ask", json({ device, text })),
  // ---- this computer's screen and hands
  hands: () => request<HandsStatus>("/api/hands"),
  setHands: (body: { enabled?: boolean; backend?: string; mode?: string }) =>
    request<HandsStatus>("/api/connections/hands", { method: "PUT", body: JSON.stringify(body) }),
  stopHands: () => request<{ stopped: boolean }>("/api/hands/stop", json({})),
  // holds (contract C1): take the browser, the screen or the phone over; Done gives it back
  holds: () => request<HoldEvent[]>("/api/holds"),
  openHold: (thread: string, tool: "browser" | "computer" | "phone", reason = "") =>
    request<HoldEvent>("/api/holds", json({ thread, tool, reason })),
  doneHold: (id: string) => request<HoldEvent>(`/api/holds/${encodeURIComponent(id)}/done`, json({})),
  health: () => request<{ ok: boolean; version: string; auth: boolean }>("/api/health", { cache: "no-store" }),
  /** The local usage ledger, aggregated; no account needed. */
  usage: (days = 30, model = "") =>
    request<UsageSummary>(`/api/usage?days=${days}${model ? `&model=${encodeURIComponent(model)}` : ""}`),
  threads: () => request<ThreadMeta[]>("/api/threads"),
  createThread: (title: string) => request<ThreadMeta>("/api/threads", json({ title })),
  renameThread: (id: string, title: string) =>
    request<ThreadMeta>(`/api/threads/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteThread: (id: string) => request<{ ok: boolean }>(`/api/threads/${id}`, { method: "DELETE" }),
  deleteEvent: (threadId: string, eventId: string) =>
    request<{ ok: boolean }>(`/api/threads/${encodeURIComponent(threadId)}/events/${encodeURIComponent(eventId)}`, { method: "DELETE" }),
  listTrash: () => request<{ items: TrashedThread[]; retention_days: number }>("/api/trash"),
  restoreTrash: (id: string) => request<{ ok: boolean; thread: ThreadMeta }>(`/api/trash/${encodeURIComponent(id)}/restore`, json({})),
  purgeTrash: (id: string) => request<{ ok: boolean }>(`/api/trash/${encodeURIComponent(id)}`, { method: "DELETE" }),
  emptyTrash: () => request<{ ok: boolean; removed: number }>("/api/trash/empty", json({})),
  clearThread: (id: string) => request<{ ok: boolean }>(`/api/threads/${id}/clear`, { method: "POST" }),
  stopThread: (id: string) => request<{ ok: boolean }>(`/api/threads/${id}/stop`, { method: "POST" }),
  /** The lightest feedback (relay 0.31): a thumb on a reply (`event` is its id; `vote` "" takes it back;
   * `reason` a code with a thumb down), or why a Stop was pressed (`stop_reason`, "skipped" included). */
  feedback: (id: string, body: { event: string; vote: "up" | "down" | ""; reason?: string } | { stop_reason: string }) =>
    request<{ ok: boolean }>(`/api/threads/${id}/feedback`, json(body)),
  events: (thread: string, limit = 200, before?: string) =>
    request<{ thread: ThreadMeta; events: TimelineEvent[]; has_more: boolean; active_stream?: unknown }>(
      `/api/threads/${thread}/events?limit=${limit}${before ? `&before=${encodeURIComponent(before)}` : ""}`,
    ),
  /** `language` is the locale of this console's screens; the reply is written in it. `via: "cloud"` is
   * *Use MiniMuse Cloud this time*: this one turn on the account's model (401 `signed_out` when signed out).
   * `model` is the composer pill's pick for this one message (`flash`, `pro`, `local`, `gemini`); "" keeps the configured model. */
  send: (thread: string, text: string, files: string[] = [], language = "", via = "", id = "", model = "") => {
    const payload: { text: string; files: string[]; language: string; via?: string; id?: string; model?: string } = { text, files, language };
    if (via) payload.via = via;
    if (id) payload.id = id;
    if (model) payload.model = model;
    return request<{ event: TimelineEvent; thread: ThreadMeta }>(`/api/threads/${thread}/send`, json(payload));
  },
  /** A file to attach: the bytes as the body, the name in the query. */
  upload: (file: File) =>
    request<AttachmentInfo>(`/api/files/upload?name=${encodeURIComponent(file.name || "photo.jpg")}`, {
      method: "POST",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
    }),
  decide: (id: string, approved: boolean, scope = "once", reason = "") =>
    request<{ ok: boolean }>(`/api/approvals/${id}`, json({ approved, scope, reason })),
  resetApprovals: () => request<{ ok: boolean }>("/api/approvals", { method: "DELETE" }),
  revokeGrant: (key: string) =>
    request<{ ok: boolean }>(`/api/approvals/grants/${encodeURIComponent(key)}`, { method: "DELETE" }),
  goals: (params?: { status?: string; category?: string; kind?: string; priority?: string; tag?: string; sort?: string }) => {
    const sp = new URLSearchParams();
    if (params?.status) sp.set("status", params.status);
    if (params?.category) sp.set("category", params.category);
    if (params?.kind) sp.set("kind", params.kind);
    if (params?.priority) sp.set("priority", params.priority);
    if (params?.tag) sp.set("tag", params.tag);
    if (params?.sort) sp.set("sort", params.sort);
    const qs = sp.toString();
    return request<Goal[]>(`/api/goals${qs ? `?${qs}` : ""}`);
  },
  createGoal: (body: {
    title: string;
    description?: string;
    steps?: string[];
    category?: string;
    due?: string;
    due_time?: string;
    recurrence?: string;
    check_in?: string;
    kind?: GoalKind;
    priority?: GoalPriority;
    tags?: string[] | string;
  }) => request<Goal>("/api/goals", json(body)),
  patchGoal: (id: string, patch: Record<string, unknown>) =>
    request<Goal>(`/api/goals/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  addStep: (id: string, title: string) => request<Goal>(`/api/goals/${id}/steps`, json({ title })),
  deleteStep: (id: string, stepIndex: number) =>
    request<Goal>(`/api/goals/${id}/steps/${stepIndex}`, { method: "DELETE" }),
  reorderSteps: (id: string, order: number[]) =>
    request<Goal>(`/api/goals/${id}/steps/reorder`, json({ order })),
  advanceGoal: (id: string) => request<Goal>(`/api/goals/${id}/advance`, { method: "POST" }),
  checkInGoal: (id: string) => request<Goal>(`/api/goals/${id}/check-in`, { method: "POST" }),
  acceptProposal: (id: string) => request<Goal>(`/api/goals/${id}/proposal/accept`, { method: "POST" }),
  dismissProposal: (id: string) => request<Goal>(`/api/goals/${id}/proposal`, { method: "DELETE" }),
  deleteGoal: (id: string) => request<{ ok: boolean }>(`/api/goals/${id}`, { method: "DELETE" }),
  memory: () => request<MemoryItem[]>("/api/memory"),
  addMemory: (content: string, category: string) =>
    request<MemoryItem>("/api/memory", json({ content, category })),
  forgetMemory: (id: string) => request<{ ok: boolean }>(`/api/memory/${id}`, { method: "DELETE" }),
  tidyMemory: () => request<TidyReport>("/api/memory/tidy", { method: "POST" }),
  memoryChanges: (limit = 30) => request<MemoryChange[]>(`/api/memory/changes?limit=${limit}`),
  restoreMemoryChange: (id: string) =>
    request<MemoryChange>(`/api/memory/changes/${id}/restore`, { method: "POST" }),
  ideas: (refresh = false) => request<IdeasData>(`/api/ideas${refresh ? "?refresh=1" : ""}`),
  activity: (n = 150) => request<ActivityData>(`/api/activity?n=${n}`),
  feed: (limit = 60) => request<FeedItem[]>(`/api/feed?limit=${limit}`),
  feedPosts: () => request<FeedPostsData>("/api/feed/posts"),
  /** The feed's preferences; only the fields given change (`daily`/`time` are the 08:00 routine). */
  setFeedInstructions: (body: { instructions?: string; daily?: boolean; time?: string }) =>
    request<FeedPostsData>("/api/feed/instructions", { method: "PUT", body: JSON.stringify(body) }),
  refreshFeedPosts: () => request<FeedPostsData>("/api/feed/posts/refresh", { method: "POST" }),
  deleteFeedPost: (id: string) => request<{ ok: boolean }>(`/api/feed/posts/${id}`, { method: "DELETE" }),
  upcoming: () => request<UpcomingData>("/api/upcoming"),
  reminders: (all = false) => request<Reminder[]>(`/api/reminders${all ? "?all=1" : ""}`),
  createReminder: (body: { text: string; kind?: ReminderKind; at?: string; repeat?: string; thread?: string }) =>
    request<Reminder>("/api/reminders", { method: "POST", body: JSON.stringify(body) }),
  fireReminder: (id: string) => request<Reminder>(`/api/reminders/${id}/fire`, { method: "POST" }),
  cancelReminder: (id: string) => request<Reminder>(`/api/reminders/${id}`, { method: "DELETE" }),
  triggers: () => request<TriggersData>("/api/triggers"),
  createTrigger: (body: { kind: TriggerKind; text: string; match?: string; lead_minutes?: number; thread?: string }) =>
    request<Trigger>("/api/triggers", { method: "POST", body: JSON.stringify(body) }),
  fireTrigger: (id: string) => request<Trigger>(`/api/triggers/${id}/fire`, { method: "POST" }),
  cancelTrigger: (id: string) => request<Trigger>(`/api/triggers/${id}`, { method: "DELETE" }),
  files: (limit = 300) => request<FileInfo[]>(`/api/files?limit=${limit}`),
  /** Raw contents of a workspace file, fetched with the token in a header (never in a URL). */
  fileText: async (path: string): Promise<string> => {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`/api/files/${path.split("/").map(encodeURIComponent).join("/")}`, { headers });
    if (res.status === 401) throw new AuthError();
    if (!res.ok) throw new Error(failureDetail(res.status, await res.json().catch(() => undefined)));
    return res.text();
  },
  settings: () => request<SettingsView>("/api/settings"),
  /**
   * The agent's data as one zip (parity note 37): the runtime answers with the file itself,
   * so a browser on another machine gets it too. The name comes from the reply.
   */
  exportData: async (): Promise<{ blob: Blob; name: string }> => {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch("/api/data/export", { headers });
    if (res.status === 401) throw new AuthError();
    if (!res.ok) throw new Error(failureDetail(res.status, await res.json().catch(() => undefined)));
    const disposition = res.headers.get("content-disposition") ?? "";
    const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "nanomuse-data.zip";
    return { blob: await res.blob(), name };
  },
  /** The switches of Settings → Import & export; the reply says what was found and done. */
  importData: async (file: Blob, name: string, options: ImportOptions): Promise<ImportResult> => {
    const params = new URLSearchParams({
      name,
      restore_chats: String(options.restore_chats),
      background_memory: String(options.background_memory),
      apps: options.apps,
      main_chat: options.main_chat,
      keep_copy: String(options.keep_copy ?? true),
    });
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`/api/data/import?${params.toString()}`, { method: "POST", headers, body: file });
    if (res.status === 401) throw new AuthError();
    if (!res.ok) throw new Error(failureDetail(res.status, await res.json().catch(() => undefined)));
    return (await res.json()) as ImportResult;
  },
  /** Same upload, but the runtime reads and applies it in the background and answers at once
   *  with a job to follow (`importJobs`); the app stays usable meanwhile.
   *
   *  Sent with XHR rather than fetch only because a multi-gigabyte export takes minutes to
   *  leave the machine, and XHR is the only way to say how far the upload has got. After it
   *  lands, the job's own progress takes over. */
  importDataBackground: (
    file: Blob,
    name: string,
    options: ImportOptions,
    onUpload?: (percent: number) => void,
  ): Promise<ImportJobView> => {
    const params = new URLSearchParams({
      name,
      restore_chats: String(options.restore_chats),
      background_memory: String(options.background_memory),
      apps: options.apps,
      main_chat: options.main_chat,
      keep_copy: String(options.keep_copy ?? true),
      background: "true",
    });
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/data/import?${params.toString()}`);
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable || !onUpload) return;
        onUpload(Math.min(100, Math.round((e.loaded / Math.max(e.total, 1)) * 100)));
      };
      xhr.onload = () => {
        if (xhr.status === 401) {
          reject(new AuthError());
          return;
        }
        let body: unknown = undefined;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          body = undefined;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(failureDetail(xhr.status, body as never)));
          return;
        }
        const job = (body as { job?: ImportJobView } | undefined)?.job;
        if (!job) {
          reject(new Error("the app did not start an import"));
          return;
        }
        resolve(job);
      };
      xhr.onerror = () => reject(new Error("the upload could not reach the app"));
      xhr.onabort = () => reject(new Error("the upload was stopped"));
      xhr.send(file);
    });
  },
  importJobs: () => request<{ jobs: ImportJobView[] }>("/api/data/import/jobs", { cache: "no-store" }),
  /** The originals kept after a transfer, newest first (reports flagged). */
  importCopies: () => request<KeptCopy[]>("/api/data/import/copies", { cache: "no-store" }),
  /** One kept file removed at the person's request; the imported data is untouched. */
  deleteImportCopy: (name: string) =>
    request<{ ok: boolean; name: string }>(`/api/data/import/copies/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  /** Paste text straight into the import (transcripts, notes, anything readable). */
  importText: async (text: string, name: string, options: ImportOptions): Promise<ImportResult> => {
    const params = new URLSearchParams({
      name,
      restore_chats: String(options.restore_chats),
      background_memory: String(options.background_memory),
      apps: options.apps,
      main_chat: options.main_chat,
    });
    const headers: Record<string, string> = { "Content-Type": "text/plain; charset=utf-8" };
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch(`/api/data/import/text?${params.toString()}`, { method: "POST", headers, body: text });
    if (res.status === 401) throw new AuthError();
    if (!res.ok) throw new Error(failureDetail(res.status, await res.json().catch(() => undefined)));
    return (await res.json()) as ImportResult;
  },
  /** Installed and latest release (a day's cache on the runtime; `refresh` asks now). */
  update: (refresh = false) => request<UpdateView>(`/api/update${refresh ? "?refresh=1" : ""}`, { cache: "no-store" }),
  /** Self-update: download the newest release's wheel, install it, and the runtime re-execs itself. */
  applyUpdate: () => request<{ ok: boolean; from?: string; to?: string; error?: string }>("/api/update/apply", { method: "POST" }),
  updateAutoSettings: (auto_update: boolean) =>
    request<{ ok: boolean; auto_update: boolean } & UpdateView>("/api/update/settings", { method: "PUT", body: JSON.stringify({ auto_update }) }),
  /** Put back the wheel kept before the last self-update and restart. */
  rollbackUpdate: () => request<{ ok: boolean; to?: string; error?: string }>("/api/update/rollback", { method: "POST" }),
  dismissWhatsNew: () => request<{ ok: boolean; seen: string }>("/api/update/whats-new/dismiss", { method: "POST" }),
  personalCloudStatus: () => request<PersonalCloudStatus>("/api/personal-cloud/status"),
  personalCloudStart: () => request<PersonalCloudStatus>("/api/personal-cloud/start", { method: "POST" }),
  personalCloudStop: () => request<PersonalCloudStatus>("/api/personal-cloud/stop", { method: "POST" }),
  chromeStatus: () => request<ChromeBridgeStatus>("/api/chrome/status"),
  chromeDetach: () => request<ChromeBridgeStatus>("/api/chrome/detach", { method: "POST" }),
  computerWindows: () =>
    request<{ available: boolean; reason?: string; active: boolean; target: { app: string; title: string }; windows: ComputerWindowInfo[] }>("/api/computer/windows"),
  computerTarget: (app: string, title = "") =>
    request<Record<string, unknown>>("/api/computer/target", json({ app, title })),
  callCapabilities: () => request<CallCapabilities>("/api/call/capabilities"),
  setCallConfig: (provider: string, model: string) =>
    request<CallCapabilities>("/api/call/config", { method: "PUT", body: JSON.stringify({ provider, model }) }),
  watchCode: () => request<WatchCodeInfo>("/api/watch/code", { method: "POST" }),
  watchDevices: () => request<WatchDevicesInfo>("/api/watch/devices"),
  watchInstall: (targetOrOpts?: string | WatchInstallOptions) =>
    request<WatchInstallResult>(
      "/api/watch/install",
      json(
        typeof targetOrOpts === "string"
          ? { target: targetOrOpts }
          : (targetOrOpts ?? {}),
      ),
    ),
  /** When the app may ask for a star (contract C1): the relay's policy, through the runtime. */
  nudges: () => request<NudgesView>("/api/nudges"),
  updateSettings: (body: Record<string, unknown>) =>
    request<SettingsView>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  /** The usage limits checked before every turn (the Usage screen edits the list). */
  usageLimits: () => request<UsageLimitRow[]>("/api/usage-limits"),
  saveUsageLimits: (rows: UsageLimitRow[]) =>
    request<UsageLimitRow[]>("/api/usage-limits", { method: "PUT", body: JSON.stringify(rows) }),
  /** Restart the runtime in place; the page reconnects on its own. */
  restartServer: () => request<{ ok: boolean }>("/api/restart", json({})),
  // ---- the lock screen: a PIN over the page, plus optional device biometric or passkey
  // The lock is per device: every call carries this browser's own id, so a lock set on
  // the phone does not lock the computer, and the other way round.
  lockView: () => request<LockView>("/api/lock", { headers: lockHeaders() }),
  lockSet: (pin: string, keepBiometric = false, timeoutSeconds?: number, lockOnLeave?: boolean) =>
    request<LockView>("/api/lock", {
      method: "PUT",
      headers: lockHeaders(),
      body: JSON.stringify(
        timeoutSeconds !== undefined || lockOnLeave !== undefined
          ? { pin, keep_biometric: keepBiometric, timeout_seconds: timeoutSeconds, lock_on_leave: lockOnLeave }
          : { pin, keep_biometric: keepBiometric },
      ),
    }),
  lockSetTimeout: (timeoutSeconds: number, lockOnLeave?: boolean) =>
    request<LockView>("/api/lock", {
      method: "PUT",
      headers: lockHeaders(),
      body: JSON.stringify({ pin: "__KEEP__", timeout_seconds: timeoutSeconds, lock_on_leave: lockOnLeave }),
    }),
  answerModelSwitch: (thread: string, eventId: string, accept: boolean) =>
    request<{ ok: boolean }>("/api/model-switch", { method: "POST", body: JSON.stringify({ thread, event_id: eventId, accept }) }),
  lockVerify: (pin: string) => request<{ ok: boolean }>("/api/lock/verify", { ...json({ pin }), headers: lockHeaders() }),
  lockResetRequest: () =>
    request<{ sent_to: string; expires_in: number; detail?: string }>("/api/lock/reset", { ...json({}), headers: lockHeaders() }),
  lockResetApply: (code: string, pin: string) =>
    request<LockView>("/api/lock/reset/apply", { ...json({ code, pin }), headers: lockHeaders() }),
  lockResetDisable: (code: string) =>
    request<LockView>("/api/lock/reset/disable", { ...json({ code }), headers: lockHeaders() }),
  lockBiometric: (credentialId: string, kind: "biometric" | "passkey" = "biometric") =>
    request<LockView>("/api/lock/webauthn", { ...json({ credential_id: credentialId, kind }), headers: lockHeaders() }),
  lockCredential: (kind: "biometric" | "passkey" | "" = "") =>
    request<string>(`/api/lock/webauthn/credential${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`, {
      headers: lockHeaders(),
    }),
  // ---- passwords & logins manager (encrypted vault + CSV/JSON import/export + browser auto-login)
  passwords: (q = "") => request<PasswordsView>(`/api/passwords${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  passwordAdd: (body: { id?: string; title?: string; url?: string; username?: string; password?: string; totp?: string; notes?: string }) =>
    request<PasswordEntry>("/api/passwords", json(body)),
  passwordDelete: (id: string) => request<PasswordsView>(`/api/passwords/${encodeURIComponent(id)}`, { method: "DELETE" }),
  passwordsImport: (text: string, filename = "") =>
    request<PasswordImportResult>("/api/passwords/import", json({ text, filename })),
  passwordTotp: (id: string) =>
    request<{ code: string; remaining_seconds: number }>(`/api/passwords/${encodeURIComponent(id)}/totp`),
  passwordsExportCsv: async (): Promise<{ blob: Blob; name: string }> => {
    const headers: Record<string, string> = {};
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const res = await fetch("/api/passwords/export?format=csv", { headers });
    if (res.status === 401) throw new AuthError();
    if (!res.ok) throw new Error(failureDetail(res.status, await res.json().catch(() => undefined)));
    return { blob: await res.blob(), name: "nanomuse-passwords.csv" };
  },
  // connections: secrets go into the vault on the server; only names ever come back
  connections: () => request<ConnectionsData>("/api/connections"),
  /** the own-key catalogue with what is configured and what that covers (contract C11) */
  providers: (region?: string) => request<ProvidersView>(`/api/providers${region ? `?region=${encodeURIComponent(region)}` : ""}`),
  /**
   * The ChatGPT sign-in through the runtime (the Codex OAuth): `login` starts the PKCE flow
   * and returns the page to open; `status` is polled until it is done; `logout` deletes the
   * token store. The routes follow the shared brief; the runtime's CONTRACT-chatgpt.md
   * fixes the CLI and `/api/providers` — a runtime without these routes answers 404, and
   * the card then names the CLI (`nanomuse chatgpt login`).
   */
  chatgptLogin: () => request<ChatGptLogin>("/api/chatgpt/login", json({})),
  chatgptStatus: () => request<ChatGptStatus>("/api/chatgpt/status"),
  /** The address the browser landed on, pasted by hand when the runtime could not take the callback itself. */
  chatgptCallback: (url: string) => request<{ ok: boolean }>("/api/chatgpt/callback", json({ url })),
  chatgptLogout: () => request<{ ok: boolean; was_signed_in?: boolean }>("/api/chatgpt/logout", json({})),
  setLLM: (body: Record<string, unknown>) =>
    request<ConnectionsData["llm"]>("/api/connections/llm", { method: "PUT", body: JSON.stringify(body) }),
  setModelOptions: (options: Array<{ model: string; label?: string; provider?: string; base_url?: string; endpoint?: string }>) =>
    request<Array<{ model: string; label?: string; provider?: string; base_url?: string; endpoint?: string }>>(
      "/api/connections/llm/options",
      { method: "PUT", body: JSON.stringify({ options }) },
    ),
  applyRecommendedDefaults: (body?: { preset?: string; api_key?: string; provider_keys?: Record<string, string> }) =>
    request<ConnectionsData>("/api/connections/llm/apply-defaults", json(body ?? {})),
  getLlmRouting: () => request<import("./types").LlmRoutingView>("/api/connections/llm/routing"),
  setLlmRouting: (body: Record<string, unknown>) =>
    request<import("./types").LlmRoutingView>("/api/connections/llm/routing", { method: "PUT", body: JSON.stringify(body) }),
  getLlmUsageStatus: () => request<import("./types").LlmUsageStatusView>("/api/connections/llm/usage-status"),
  refreshLlmUsageStatus: () =>
    request<import("./types").LlmUsageStatusView>("/api/connections/llm/usage-status/refresh", { method: "POST" }),
  /** the header's model menu: saved multi-model options, built-in aliases, and every endpoint added */
  models: () =>
    request<{
      default: { model: string; provider: string; base_url?: string; endpoint?: string };
      options?: Array<{ model: string; label?: string; provider?: string; base_url?: string; endpoint?: string }>;
      aliases: { value: string; label: string; hint: string }[];
      endpoints: { id: string; label: string; models: string[] }[];
    }>("/api/models"),
  testLLM: () => request<TestResult>("/api/connections/llm/test", { method: "POST" }),
  /** the models an endpoint offers: its own /models when it answers, else the preset's catalogue
   *  (or, for a custom endpoint of the list named by `endpoint`, the models saved with it) */
  llmModels: (body: { preset?: string; base_url?: string; api_key?: string; endpoint?: string }) =>
    request<{
      models: string[];
      popular?: string[];
      catalog_info?: Record<string, import("./types").ModelCatalogInfoEntry>;
      recommended_defaults?: import("./types").RecommendedDefaultSet;
      image_models?: string[];
      video_models?: string[];
      // MiniMuse Cloud, relay 0.10: the chat models on the menu and the other usable ones under
      // the operator's key (a member's list), likewise for pictures / clips; which chat models read pictures
      menu?: string[];
      catalog?: string[];
      image_catalog?: string[];
      video_catalog?: string[];
      vision?: string[];
      /** contract C4: the relay's hands models, recommended first; the two recommendations */
      gui?: string[];
      gui_recommended?: string;
      chat_recommended?: string;
      source: "live" | "catalogue";
      error?: string;
    }>(
      "/api/llm/models",
      json(body),
    ),
  systemCapabilities: (hints?: { ramGb?: number; cores?: number; mobile?: boolean }) => {
    const sp = new URLSearchParams();
    if (hints?.ramGb !== undefined && hints.ramGb > 0) sp.set("client_ram_gb", String(hints.ramGb));
    if (hints?.cores !== undefined && hints.cores > 0) sp.set("client_cores", String(hints.cores));
    if (hints?.mobile !== undefined) sp.set("client_mobile", String(hints.mobile));
    const qs = sp.toString();
    return request<import("./types").SystemCapabilities>(`/api/system/capabilities${qs ? `?${qs}` : ""}`);
  },
  /** a custom OpenAI-compatible endpoint joins the runtime's list (#294); `models` typed when it does not list them */
  addEndpoint: (body: { label?: string; base_url: string; api_key?: string; models?: string }) =>
    request<CustomEndpoint>("/api/connections/endpoints", json(body)),
  /** a new name, address, key or typed models; the slots that use the endpoint follow */
  updateEndpoint: (id: string, body: { label?: string; base_url?: string; api_key?: string; models?: string }) =>
    request<CustomEndpoint>(`/api/connections/endpoints/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  /** 409 while the chat model answers through it */
  removeEndpoint: (id: string) =>
    request<{ ok: boolean }>(`/api/connections/endpoints/${encodeURIComponent(id)}`, { method: "DELETE" }),
  /** asks the endpoint for its models again; `kept` when it did not answer and the saved list stands */
  refreshEndpointModels: (id: string) =>
    request<CustomEndpoint & { source: "live" | "kept"; error?: string }>(
      `/api/connections/endpoints/${encodeURIComponent(id)}/models`,
      json({}),
    ),
  setEmbeddings: (body: Record<string, unknown>) =>
    request<ConnectionsData["embeddings"]>("/api/connections/embeddings", { method: "PUT", body: JSON.stringify(body) }),
  testEmbeddings: () => request<TestResult>("/api/connections/embeddings/test", { method: "POST" }),
  setSearch: (body: Record<string, unknown>) =>
    request<ConnectionsData["search"]>("/api/connections/search", { method: "PUT", body: JSON.stringify(body) }),
  testSearch: () => request<TestResult>("/api/connections/search/test", { method: "POST" }),
  setEmail: (body: Record<string, unknown>) =>
    request<ConnectionsData["email"]>("/api/connections/email", { method: "PUT", body: JSON.stringify(body) }),
  disconnectEmail: () => request<ConnectionsData["email"]>("/api/connections/email", { method: "DELETE" }),
  testEmail: () => request<TestResult>("/api/connections/email/test", { method: "POST" }),
  emailAccounts: () => request<EmailAccount[]>("/api/connections/email/accounts"),
  upsertEmailAccount: (body: Record<string, unknown>) =>
    request<EmailAccount[]>("/api/connections/email/accounts", json(body)),
  removeEmailAccount: (id: string) =>
    request<EmailAccount[]>(`/api/connections/email/accounts/${encodeURIComponent(id)}`, { method: "DELETE" }),
  testEmailAccount: (id: string) =>
    request<TestResult>(`/api/connections/email/accounts/${encodeURIComponent(id)}/test`, { method: "POST" }),
  googleStatus: () => request<GoogleStatus>("/api/connections/google/status"),
  googleOAuthStart: (body?: { client_id?: string; client_secret?: string }) =>
    request<{ auth_url: string; redirect_uri: string; state: string }>("/api/connections/google/oauth/start", json(body ?? {})),
  googleOAuthCallback: (url: string) =>
    request<GoogleStatus>("/api/connections/google/oauth/callback", json({ url })),
  googleSync: (email?: string) => {
    const qs = email ? `?email=${encodeURIComponent(email)}` : "";
    return request<{ ok: boolean; calendar_events?: number; contacts?: number; errors?: string[]; last_sync_at?: string; google?: GoogleStatus }>(
      `/api/connections/google/sync${qs}`,
      { method: "POST" },
    );
  },
  googleEnableApis: () =>
    request<{ ok: boolean; apis: ApiConnection[] }>("/api/connections/google/enable-apis", { method: "POST" }),
  enableFreeApis: () =>
    request<{ ok: boolean; apis: ApiConnection[] }>("/api/connections/apis/enable-free", { method: "POST" }),
  importCalendar: (name: string, text: string) =>
    request<ConnectionsData["calendar"] & { error?: string }>("/api/connections/calendar/import", {
      method: "POST",
      body: JSON.stringify({ name, text }),
    }),
  googleLogout: (email?: string) => {
    const qs = email ? `?email=${encodeURIComponent(email)}` : "";
    return request<GoogleStatus>(`/api/connections/google/oauth/logout${qs}`, { method: "POST" });
  },
  apiPresets: (q = "") => {
    const qs = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
    return request<{ presets: ApiPreset[]; count: number }>(`/api/connections/presets${qs}`);
  },
  apis: () =>
    request<{ apis?: ApiConnection[]; count?: number } | ApiConnection[]>("/api/connections/apis").then((r) =>
      Array.isArray(r) ? r : Array.isArray(r?.apis) ? r.apis : [],
    ),
  upsertApi: (body: Record<string, unknown>) =>
    request<ApiConnection>("/api/connections/apis", json(body)),
  patchApi: (id: string, body: Record<string, unknown>) =>
    request<ApiConnection>(`/api/connections/apis/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteApi: (id: string) =>
    request<{ removed: boolean }>(`/api/connections/apis/${encodeURIComponent(id)}`, { method: "DELETE" }),
  saveApiEndpoint: (id: string, body: ApiEndpoint) =>
    request<ApiConnection>(`/api/connections/apis/${encodeURIComponent(id)}/endpoints`, json(body)),
  testApi: (id: string) =>
    request<TestResult>(`/api/connections/apis/${encodeURIComponent(id)}/test`, { method: "POST" }),
  discoverApi: (id: string, spec_url = "") =>
    request<Record<string, unknown>>(`/api/connections/apis/${encodeURIComponent(id)}/discover`, json({ spec_url })),
  callApiRequest: (body: Record<string, unknown>) =>
    request<Record<string, unknown>>("/api/connections/apis/request", json(body)),
  calendar: (days = 2, opts?: { start?: string; refresh?: boolean }) => {
    const sp = new URLSearchParams({ days: String(days) });
    if (opts?.start) sp.set("start", opts.start);
    if (opts?.refresh) sp.set("refresh", "true");
    return request<CalendarData>(`/api/calendar?${sp.toString()}`);
  },
  createCalendarEvent: (body: Record<string, unknown>) =>
    request<CalendarEvent>("/api/calendar/events", json(body)),
  updateCalendarEvent: (uid: string, patch: Record<string, unknown>) =>
    request<CalendarEvent>(`/api/calendar/events/${encodeURIComponent(uid)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  deleteCalendarEvent: (uid: string) =>
    request<{ removed: boolean }>(`/api/calendar/events/${encodeURIComponent(uid)}`, { method: "DELETE" }),
  locationSummary: () => request<LocationSummary>("/api/location"),
  locationSearch: (params?: { q?: string; tag?: string; category?: string }) => {
    const sp = new URLSearchParams();
    if (params?.q) sp.set("q", params.q);
    if (params?.tag) sp.set("tag", params.tag);
    if (params?.category) sp.set("category", params.category);
    const qs = sp.toString();
    return request<Record<string, unknown>>(`/api/location/search${qs ? `?${qs}` : ""}`);
  },
  addLocationPlace: (body: Record<string, unknown>) =>
    request<LocationPlace>("/api/location/places", json(body)),
  updateLocationPlace: (id: string, patch: Record<string, unknown>) =>
    request<LocationPlace>(`/api/location/places/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) }),
  visitLocationPlace: (id: string) =>
    request<LocationPlace>(`/api/location/places/${encodeURIComponent(id)}/visit`, { method: "POST" }),
  deleteLocationPlace: (id: string) =>
    request<{ removed: boolean }>(`/api/location/places/${encodeURIComponent(id)}`, { method: "DELETE" }),
  logLocationTrip: (body: Record<string, unknown>) =>
    request<LocationTrip>("/api/location/trips", json(body)),
  pingLocation: (body: Record<string, unknown>) =>
    request<LocationTrip>("/api/location/ping", json(body)),
  deleteLocationTrip: (id: string) =>
    request<{ removed: boolean }>(`/api/location/trips/${encodeURIComponent(id)}`, { method: "DELETE" }),
  locationTraffic: (origin: string, destination: string) =>
    request<LocationTrafficEstimate>("/api/location/traffic", json({ origin, destination })),
  setCalendar: (body: Record<string, unknown>) =>
    request<ConnectionsData["calendar"]>("/api/connections/calendar", { method: "PUT", body: JSON.stringify(body) }),
  addCalendarFeed: (name: string, url: string) =>
    request<ConnectionsData["calendar"] & { error?: string }>("/api/connections/calendar/feeds", json({ name, url })),
  removeCalendarFeed: (name: string) =>
    request<ConnectionsData["calendar"]>(`/api/connections/calendar/feeds/${encodeURIComponent(name)}`, { method: "DELETE" }),
  testCalendar: () => request<TestResult>("/api/connections/calendar/test", { method: "POST" }),
  syncCalendar: () => request<TestResult>("/api/connections/calendar/sync", { method: "POST" }),
  contacts: (q = "", limit = 8) =>
    request<{ count: number; people: Contact[] }>(`/api/contacts?q=${encodeURIComponent(q)}&limit=${limit}`),
  setContacts: (body: Record<string, unknown>) =>
    request<ConnectionsData["contacts"]>("/api/connections/contacts", { method: "PUT", body: JSON.stringify(body) }),
  addContactsSource: (name: string, url: string) =>
    request<ConnectionsData["contacts"] & { error?: string }>("/api/connections/contacts/sources", json({ name, url })),
  importContacts: (name: string, text: string) =>
    request<ConnectionsData["contacts"] & { error?: string }>(`/api/connections/contacts/import?name=${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "text/vcard; charset=utf-8" },
      body: text,
    }),
  removeContactsSource: (name: string) =>
    request<ConnectionsData["contacts"]>(`/api/connections/contacts/sources/${encodeURIComponent(name)}`, { method: "DELETE" }),
  testContacts: () => request<TestResult>("/api/connections/contacts/test", { method: "POST" }),
  syncContacts: () => request<TestResult>("/api/connections/contacts/sync", { method: "POST" }),
  // skills
  skills: () => request<SkillsData>("/api/skills"),
  skill: (name: string) => request<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`),
  saveSkill: (name: string, content: string) =>
    request<SkillDetail>(`/api/skills/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ content }) }),
  deleteSkill: (name: string) => request<SkillsData>(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  setSkillEnabled: (name: string, enabled: boolean) =>
    request<SkillInfo>(`/api/skills/${encodeURIComponent(name)}/enabled`, json({ enabled })),
  importSkill: (url: string) => request<SkillDetail>("/api/skills/import", json({ url })),
  setGui: (body: Record<string, unknown>) => request<ConnectionsData["gui"]>("/api/connections/gui", { method: "PUT", body: JSON.stringify(body) }),
  testGui: () => request<TestResult & { model?: string }>("/api/connections/gui/test", { method: "POST" }),
  /** where pictures (`image`) or clips (`video`) come from: the slot as set and what it resolves to (0.1.41) */
  media: (slot: "image" | "video") => request<MediaSlotData>(`/api/connections/${slot}`),
  /** provider (a catalogue id), model, base_url, api_key; all "" clears the slot */
  setMedia: (slot: "image" | "video", body: Record<string, unknown>) =>
    request<MediaSlotData>(`/api/connections/${slot}`, { method: "PUT", body: JSON.stringify(body) }),
  setBrowser: (enabled: boolean) =>
    request<ConnectionsData["browser"]>("/api/connections/browser", { method: "PUT", body: JSON.stringify({ enabled }) }),
  /** One-tap install of the computer's browser control; poll `browserInstallStatus` while it runs. */
  installBrowser: () => request<BrowserInstallState>("/api/connections/browser/install", { method: "POST" }),
  browserInstallStatus: () =>
    request<BrowserInstallState & { available: boolean }>("/api/connections/browser/install", { cache: "no-store" }),
  addMCP: (body: Record<string, unknown>) => request<ConnectionsData>("/api/connections/mcp", json(body)),
  removeMCP: (name: string) =>
    request<{ ok: boolean }>(`/api/connections/mcp/${encodeURIComponent(name)}`, { method: "DELETE" }),
  vaultSet: (name: string, value: string) =>
    request<string[]>(`/api/vault/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ value }) }),
  vaultDelete: (name: string) => request<{ ok: boolean }>(`/api/vault/${encodeURIComponent(name)}`, { method: "DELETE" }),
  onboarded: (done = true) => request<{ onboarded: boolean; feed_started?: boolean }>("/api/onboarded", json({ done })),
  // ---- the first conversation (contract C4): the runtime owns the state, the browser draws it
  firstrun: (lang: string) => request<FirstRunView>(`/api/firstrun?lang=${encodeURIComponent(lang)}`),
  firstrunStart: (lang: string) => request<FirstRunView>("/api/firstrun/start", json({ lang })),
  firstrunPick: (name: string) => request<FirstRunView>("/api/firstrun/pick", json({ name })),
  firstrunDismiss: () => request<FirstRunView>("/api/firstrun/dismiss", json({})),
  // browser view
  browserControl: (thread: string, body: BrowserControl) =>
    request<{ url: string; title: string; hold?: HoldEvent | null }>(`/api/browser/${encodeURIComponent(thread)}/control`, json(body)),
  // push
  push: () => request<PushInfo>("/api/push"),
  pushSubscribe: (subscription: Record<string, unknown>) => request<PushInfo>("/api/push/subscribe", json({ subscription })),
  pushUnsubscribe: (endpoint: string) => request<PushInfo>("/api/push/unsubscribe", json({ endpoint })),
  pushTest: () => request<TestResult & { sent?: number; failed?: number }>("/api/push/test", { method: "POST" }),
};

/** The runtime behind this page is gone for good — the showcase gateway closes a socket with
 *  this code when the demo session has ended or was never there (no point reconnecting). */
export const WS_GONE = 4404;
/** The token is not right: a sign-in is needed, not a retry. */
export const WS_UNAUTHORIZED = 4401;

/** Which way a closed socket goes: `retry` with backoff, `auth` (ask for the token), or
 *  `gone` (stop — the runtime behind the page has ended). Pure, for the tests. */
export function closeOutcome(code: number): "retry" | "auth" | "gone" {
  if (code === WS_UNAUTHORIZED) return "auth";
  if (code === WS_GONE) return "gone";
  return "retry";
}

/** WebSocket with automatic reconnect. Returns a disposer. */
export function connectWs(handlers: {
  onMessage: (msg: WsMessage) => void;
  onOpen?: () => void;
  onClose?: () => void;
  onAuthError?: () => void;
  /** the runtime has ended (gateway close 4404): no more retries */
  onGone?: () => void;
}): { send: (msg: unknown) => boolean; close: () => void } {
  let ws: WebSocket | null = null;
  let closed = false;
  let attempt = 0;
  let timer: number | undefined;
  let openedAt = 0;

  const open = () => {
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    // the token goes in the first frame, never in the address (which proxies log)
    ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    ws.onopen = () => {
      const token = getToken();
      if (token) ws?.send(JSON.stringify({ kind: "auth", token }));
      openedAt = Date.now();
      handlers.onOpen?.();
    };
    ws.onmessage = (ev) => {
      try {
        handlers.onMessage(JSON.parse(ev.data) as WsMessage);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = (ev) => {
      handlers.onClose?.();
      const outcome = closeOutcome(ev.code);
      if (outcome === "auth") {
        handlers.onAuthError?.();
        return;
      }
      if (outcome === "gone") {
        // the gateway says the session behind this page is over: a page that kept
        // reconnecting every few seconds for hours was what the production logs showed
        closed = true;
        handlers.onGone?.();
        return;
      }
      if (closed) return;
      // A drop after a healthy stretch is an ordinary drop: reconnect fast. A socket
      // that barely lived means the runtime behind it is flapping (a restart loop,
      // a bad radio), so the retries back off instead of strobing the page with a
      // reconnect every half second.
      const lived = openedAt > 0 ? Date.now() - openedAt : 0;
      if (lived >= 10_000) attempt = 0;
      const delay = Math.min(15000, 500 * 2 ** attempt++);
      timer = window.setTimeout(open, delay);
    };
    ws.onerror = () => ws?.close();
  };
  open();

  const checkHealthOnResume = () => {
    if (closed) return;
    if (typeof document !== "undefined" && document.visibilityState === "visible") {
      if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        if (timer) window.clearTimeout(timer);
        attempt = 0;
        open();
      }
    }
  };
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", checkHealthOnResume);
    window.addEventListener("focus", checkHealthOnResume);
  }

  return {
    send: (msg) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
        return true;
      }
      return false;
    },
    close: () => {
      closed = true;
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", checkHealthOnResume);
        window.removeEventListener("focus", checkHealthOnResume);
      }
      if (timer) window.clearTimeout(timer);
      ws?.close();
    },
  };
}
