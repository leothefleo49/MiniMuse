export type RiskLevel = "safe" | "moderate" | "sensitive";

interface BaseEvent {
  id: string;
  ts: string;
  thread: string;
  updated_ts?: string;
  /** "user" for things you said; "goal" / "background" for work your MiniMuse did on its own. */
  source?: string;
  /** For background events: the short label of the work being done. */
  about?: string;
  /** The device a message came from; "call": said on a voice call in 0.1.20–0.1.21. */
  via?: "call" | string;
  /** on the relay (contract C7): pushed from here, or pulled from another device of the account */
  synced?: boolean;
  mid?: string;
  /** queued in this device's offline outbox: shown, but not sent yet */
  pending?: boolean;
}

/** A file attached to a message: in the workspace under attachments/. */
export interface AttachmentInfo {
  path: string;
  name: string;
  size: number;
  kind: "image" | "pdf" | "data" | "text" | "other";
  mime: string;
}

export interface UserEvent extends BaseEvent {
  type: "user";
  text: string;
  files?: AttachmentInfo[];
  /** the name of the device this was asked from, when another device opened this chat over the hub */
  via?: string;
  /** `@<device>` in front of the message: the device the turn was handed to (contract C7 rule 8) */
  to_device?: string;
  to_device_name?: string;
  /** a synced message written on another device of the account */
  via_device?: string;
  via_device_name?: string;
  /** *Use nanoMuse Cloud this time*: the model id this one turn ran on */
  model_used?: string;
  /** the agent's emoji on this message (its `react` tool), up to three */
  reactions?: string[];
}

export interface AssistantEvent extends BaseEvent {
  type: "assistant";
  text: string;
  reasoning?: string;
  /** A background pass that found nothing worth interrupting you for. */
  quiet?: boolean;
  /** *Use nanoMuse Cloud this time*: the model id this reply came from */
  model_used?: string;
  /** the device whose Muse said this (a chat addressed to a device) */
  device?: string;
  /** a synced reply written on another device of the account */
  via_device?: string;
  via_device_name?: string;
  /** the person's thumb on this reply (`POST /api/threads/{id}/feedback`), "" or absent when none */
  feedback?: "up" | "down" | "";
}

export interface ToolEvent extends BaseEvent {
  type: "tool";
  tool: string;
  summary: string;
  /** the model's own words for the step ("打开携程网站"), when it gave them (0.1.33) */
  title?: string;
  args: Record<string, unknown>;
  status: "running" | "ok" | "error" | "blocked";
  output?: string;
  /** the device the step ran on, when it was not this one */
  device?: string;
}

export interface ApprovalEvent extends BaseEvent {
  type: "approval";
  tool: string;
  summary: string;
  risk: RiskLevel;
  reasons: string[];
  warnings: string[];
  egress_target?: string | null;
  /** What the user asked for — why this action is happening. */
  purpose?: string;
  /** What a standing permission would be bound to (host, recipient, program). */
  target?: string | null;
  grant_key?: string;
  /** Scopes the Sentinel offers for this call, in display order. */
  grant_options?: GrantScope[];
  args: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | "expired";
  scope?: string | null;
  /** raised by another device's Muse (a chat addressed to it); answered here, carried back over the hub */
  remote?: { device: string; name: string; approval_id: string };
}

export type GrantScope = "once" | "task" | "conversation" | "session" | "24h" | "always";

export interface Grant {
  key: string;
  tool: string;
  target: string | null;
  scope: GrantScope;
  granted_at: string;
  expires_at: string | null;
  task_id?: string | null;
}

export interface QuestionEvent extends BaseEvent {
  type: "question";
  text: string;
  status: "pending" | "answered" | "expired";
  answer?: string;
}

/** A device this MiniMuse is built on, without any account: this computer or a phone
 *  that talks to it directly (live socket, push subscription, the ADB link). */
export interface LocalDevice {
  id: string;
  name: string;
  nickname?: string;
  model?: string;
  kind: "computer" | "phone" | "tablet" | "web";
  online: boolean;
  this?: boolean;
  os?: string;
  version?: string;
  actions?: string[];
  last_seen?: string;
}

export interface LocalDevicesView {
  computer: LocalDevice;
  phones: LocalDevice[];
}

/** The card that lands when any AI switches the default model: keep it, or put the
 *  previous default back. */
export interface ModelSwitchEvent extends BaseEvent {
  type: "model_switch";
  model: string;
  previous_model?: string;
  previous_provider?: string;
  previous_base_url?: string;
  status?: "applied" | "accepted" | "reverted";
}

export interface NoticeEvent extends BaseEvent {
  type: "notice";
  level: "info" | "warn" | "error";
  /** an English sentence; the runtime's failure sentences are keys in the dictionary */
  text: string;
  /** a short reason for a failed run: allowance, key, network, timeout, too_long … */
  code?: string;
  /** the raw exception, for bug reports */
  detail?: string;
  /** the failed turn ran on *Use nanoMuse Cloud this time* already: the model id */
  model_used?: string;
  /** values for the {placeholders} in `text` */
  vars?: Record<string, string | number>;
  source?: string;
  /** code "allowance": what the relay said — what is left, the pool, and the three ways on */
  allowance?: {
    left?: number | null;
    grant?: number;
    invite_url?: string;
    invite_bonus_cny?: number;
    invitee_bonus_cny?: number;
    own_key_docs?: string;
    /** relay 0.21: the region's providers and what each covers */
    guidance?: Guidance;
  };
}

export interface ArtifactEvent extends BaseEvent {
  type: "artifact";
  path: string;
  name: string;
  action: string;
}

/** The phone that is connected for GUI operation (the Android app or the MobileGym module). */
export interface PhoneStatus {
  connected: boolean;
  device: {
    id: string;
    name: string;
    platform: string;
    /** whether the phone can be operated through its screen (the accessibility service is on) */
    gui: boolean;
    /** whether the phone shows the step capsule with a Stop button while it is operated */
    capsule?: boolean;
    apps: number;
    width: number;
    height: number;
  } | null;
  last_screen: { app: string; app_name: string; route: string; elements: number; image: string | null; taken_at: number } | null;
}

/** The browser as the agent sees it: one card per run, updated frame after frame. */
export interface BrowserEvent extends BaseEvent {
  type: "browser";
  url: string;
  title: string;
  /** caption of the last action: "Opened example.com", "Clicked 'Sign in'", "You typed" */
  action: string;
  /** id of the latest frame; fetch with frameUrl() — frames live in memory on the server */
  frame: string;
  frames: number;
  status: "live" | "done";
  by_user?: boolean;
  updated_ts?: string;
  /** which browser drew the frame: the phone's own WebView ("device") or Playwright on the server */
  backend?: "device" | "playwright" | string;
  /** frame size in CSS pixels — taps are mapped onto this */
  width?: number;
  height?: number;
}

/** The hands on this computer: one card per task on its screen, updated step by step. */
export interface HandsEvent extends BaseEvent {
  type: "hands";
  /** the goal of the task ("" for a single computer_act) */
  text: string;
  status: "live" | "done" | "stopped";
  steps: number;
  /** the last action: what, the words under the cursor, where (fractions of the screen) */
  last?: { action?: string; label?: string; fx?: number; fy?: number; fx2?: number; fy2?: number; text?: string; keys?: string[] };
  /** the window in front when the last action ran */
  app?: string;
  title?: string;
  /** live JPEG frame id of this computer or target background window */
  frame?: string;
  mode?: "window" | "screen" | string;
  width?: number;
  height?: number;
  /** what the operator wanted the person to know when it stopped to ask */
  notice?: string;
}

/**
 * A hold (contract C1): the person has the browser, this computer's screen or the phone for
 * a while — they took it over, or the agent asked them to (a sign-in, a code). The agent's
 * actions of that kind wait until Done takes the hold off. `ts` arrives as seconds since the
 * epoch and is made ISO by the store like every other event's.
 */
export interface HoldEvent extends BaseEvent {
  type: "hold";
  tool: "browser" | "computer" | "phone" | string;
  status: "on" | "off";
  by: "user" | "agent" | string;
  reason: string;
  done_ts?: number;
  /** the agent waited ten minutes and went on without the person */
  timed_out?: boolean;
  /** found "on" after a restart: nothing waits on it any more */
  stale?: boolean;
}

/** The avatar studio's card: a new face from a description, drawn in the chat (nanomuse/avatar). */
export interface AvatarEvent extends BaseEvent {
  type: "avatar";
  session: string;
  /** estimate (waiting for the tap) · drawing · choose (four wait) · posing · animating (the face is on, clips coming) · done · cancelled · failed */
  stage: "estimate" | "drawing" | "choose" | "posing" | "animating" | "done" | "cancelled" | "failed" | string;
  description: string;
  /** workspace path of a picture attached to the request: the candidates are drawn from it */
  reference?: string;
  /** what it costs: the relay's figure when the account draws; the counts alone with one's own key */
  cost?: { pictures?: number; clips?: number; model?: string; clip_model?: string; cloud?: boolean; cny?: number | null; left_cny?: number | null; unlimited?: boolean; affordable?: boolean; error?: string };
  /** workspace paths of the four candidates (null while one is still being drawn) */
  candidates?: Array<string | null>;
  errors?: string[];
  chosen?: number | null;
  /** the face id once one was picked; the profile's avatar when done */
  face?: string | null;
  /** mood → workspace path, filled as the poses land */
  moods?: Record<string, string>;
  /** mood → workspace path of the clip, filled as they land (idle · working · waiting · happy) */
  clips?: Record<string, string>;
  message?: string;
}

/** The avatar studio's session as `GET /api/avatar` and the "studio" messages carry it. */
export type StudioSession = Omit<AvatarEvent, keyof BaseEvent | "type">;

/** The face the profile wears when the studio drew it (`avatar/<face>/face.json`). */
export interface StudioFace {
  id: string;
  description: string | null;
  style: string | null;
  model: string | null;
  created: number | null;
}

export interface StudioView {
  available: boolean;
  image_model: string;
  video_model: string;
  cloud: boolean;
  host: string;
  current: StudioSession | null;
  face: StudioFace | null;
}

export interface AvatarLibraryAsset {
  file: string;
  path: string;
  kind: "video" | "image";
  label: string;
  size: number;
  canonical: boolean;
}

export interface AvatarLibraryFace {
  id: string;
  name: string;
  style?: string;
  preview?: string;
  description?: string;
  situations: Record<string, string>;
  assets?: AvatarLibraryAsset[];
  clips?: Array<{ file: string; label?: string }>;
  stills?: string[];
  has_clips?: boolean;
}

export interface AvatarLibraryView {
  active: string;
  faces: AvatarLibraryFace[];
  situations: Array<{ id: string; label: string }>;
}

export interface SetupPromptField {
  key: string;
  label: string;
  placeholder?: string;
  secret?: boolean;
}

export interface ZapierRecommendation {
  tool: string;
  calls_this_month: number;
  preset_id: string;
  preset_name: string;
  base_url: string;
  already_connected_direct: boolean;
  reason: string;
}

export interface ZapierQuotaStatus {
  configured: boolean;
  servers: string[];
  month: string;
  used: number;
  limit: number;
  remaining: number;
  percent: number;
  level: "ok" | "moderate" | "high" | "exhausted" | string;
  last_used_at: string;
  by_tool: Record<string, number>;
  should_recommend_direct: boolean;
  summary: string;
  recommendations: ZapierRecommendation[];
}

export interface SetupPromptEvent extends BaseEvent {
  type: "setup_prompt";
  kind:
    | "permission"
    | "api_key"
    | "oauth"
    | "live_notifications"
    | "phone_control"
    | "wireless_debugging"
    | "pc_control"
    | "health_wearable"
    | "calendar"
    | "zapier"
    | "custom_api"
    | string;
  target: string;
  preset_id?: string;
  title: string;
  why: string;
  base_url?: string;
  auth_type?: string;
  docs_url?: string;
  key_url?: string;
  key_hint?: string;
  steps?: string[];
  fields?: SetupPromptField[];
  resume_prompt?: string;
  zapier_quota?: ZapierQuotaStatus | null;
  status?: "pending" | "completed" | "dismissed";
}

export type TimelineEvent =
  | UserEvent
  | AssistantEvent
  | ToolEvent
  | ApprovalEvent
  | QuestionEvent
  | ModelSwitchEvent
  | NoticeEvent
  | ArtifactEvent
  | BrowserEvent
  | HandsEvent
  | HoldEvent
  | AvatarEvent
  | SetupPromptEvent;

export interface ThreadMeta {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** the newest real message (user/assistant); the list is ordered by this */
  last_message_at?: string;
  busy: boolean;
  queued: number;
  events: number;
  /** a chat addressed to another device of the account: what is typed here runs there */
  device?: string;
  device_name?: string;
  /** a chat another device opened here with a task over the hub */
  remote_from?: { device: string; name: string; kind: string; conversation: string };
  /** a synced chat that was started on another device of the account ("From Pixel 8") */
  origin_device?: string;
  origin_device_name?: string;
}

export interface SyncPeerInfo {
  id: string;
  name: string;
  kind?: string;
  url: string;
  urls?: string[];
  remote_ready?: boolean;
  online: boolean;
  last_sync_at: string | null;
  last_error: string;
  pending: number;
}

export interface NearbyPeer {
  device_id: string;
  name: string;
  urls: string[];
  pair_ready: boolean;
  version?: string;
  age_s: number;
}

export interface PairCodeInfo {
  code: string;
  expires_at: string;
  ttl_s: number;
  device_id: string;
  name: string;
  urls: string[];
  pair_uri: string;
  qr_png?: string;
  /** set when no other device could actually reach this one (loopback-only or no network) */
  warning?: string;
}

export interface TrashedThread {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  deleted_at: string;
  expires_at: string;
  retention_days: number;
  days_left: number;
  message_count: number;
  was_main?: boolean;
}

export interface AppUpdateLiveState {
  status: "available" | "applying" | "restarting" | "error";
  current?: string | null;
  latest?: string | null;
  url?: string;
  auto_update?: boolean;
  auto_supported?: boolean;
  error?: string;
}

/** GET /api/sync/state: conversations synced across devices (direct P2P or Cloud). */
export interface SyncState {
  /** the switch on this device */
  enabled: boolean;
  /** C9: this device's side chats too (per device, default off); off = the main chat only */
  side_chats?: boolean;
  /** C9: the other devices' turns under way on synced chats */
  working?: WorkingPresence[];
  /** signed in to nanoMuse Cloud or paired with at least one peer */
  available: boolean;
  cloud_available?: boolean;
  peer_mode?: boolean;
  peers?: SyncPeerInfo[];
  nearby?: NearbyPeer[];
  pending_offline?: number;
  device_id?: string;
  device_name?: string;
  urls?: string[];
  /** the relay refused the key: nothing moves until the next sign-in */
  paused: boolean;
  cursor: number;
  last_pull_at: string | null;
  last_push_at: string | null;
  error: string;
  /** the relay's own view; null when it could not be asked */
  relay?: { enabled: boolean; cursor: number; counts: { conversations: number; messages: number }; limits: { messages: number; text_bytes: number } } | null;
  /** optional per-device overrides (disabled by default) */
  device_overrides?: {
    enabled: boolean;
    keep_local_appearance: boolean;
    keep_local_sentinel?: boolean;
    keep_local_notifications?: boolean;
    default_model: string;
  };
}

/**
 * Another device of the account is working on a synced chat (contract C9): the hub's
 * `working` frame as the runtime keeps it. `at` is Unix seconds; a line older than ten
 * minutes is stale and not shown.
 */
export interface WorkingPresence {
  thread: string;
  cid: string;
  device: string;
  device_name: string;
  /** what the run is doing right now (0.31): working, or waiting on the person */
  state?: "working" | "waiting";
  /** the step's own words ("打开携程网站"), as the run reported it last */
  detail?: string;
  at: number;
}

/** A device of the account on the hub (docs/hub.md). */
export interface HubDevice {
  id: string;
  name: string;
  kind: "phone" | "computer" | "web" | string;
  os?: string;
  version?: string;
  online: boolean;
  actions?: string[];
  last_seen?: string;
  /** this very device */
  this?: boolean;
}

export interface CloudAccount {
  base_url: string;
  signed_in: boolean;
  /** the masked identifier: "1**********11", "s***@example.com" */
  hint: string;
  channel: "sms" | "email" | string;
  signed_in_at?: string | null;
  /** the relay is the model provider right now */
  is_model: boolean;
  /** this runtime insists on an account (self-hosters may turn it off) */
  required?: boolean;
  /** the account's models as a source (*Use nanoMuse Cloud models*); false leaves them out of the automatic order and the listings */
  models?: boolean;
  has_password?: boolean;
  /** on the operator's list: no spend limit */
  member?: boolean;
  /** may name any model of the provider's for its kind (chat, picture, clip), not only the menu's */
  any_model?: boolean;
  /** on the answer to a code sign-in: the account was created by it (first sign-in ever) */
  created?: boolean;
  /** the opaque account id, never the identifier */
  account_id?: string;
  /** where the relay places the account ("cn" = mainland China); older relays do not say */
  region?: string;
}

/** One sign-in of the account (a key), as the relay lists them. */
export interface CloudSession {
  prefix: string;
  device: string;
  via: "code" | "password" | string;
  created_at: number;
  last_used_at: number | null;
  current: boolean;
}

/** A line of the account's own timeline: sign-ins, calls, refusals — never message content. */
export interface CloudEvent {
  ts: number;
  kind: string;
  detail: string;
}

/** Requests, tokens and money for one kind (or one model) of use. */
export interface UsageRow {
  kind: "chat" | "image" | "video" | "realtime" | string;
  model?: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  charged: number;
  cost_cny: number;
}

/** GET /v1/config on the relay (0.15): what a client prints before anyone signs in. */
export interface CloudConfig {
  version?: string;
  signup_open?: boolean;
  allowance_cny?: number;
  allowance_usd?: number;
  invite_bonus_cny?: number;
  invitee_bonus_cny?: number;
  usd_cny?: number;
  invite_url?: string;
  own_key_docs?: string;
  privacy_url?: string;
  repo_url?: string;
  improve_default?: boolean;
  /** Relay policy; absent on older relays, whose minimum was eight. */
  password_min_len?: number;
}

/** `/api/cloud/me`: the account as the relay sees it. */
export interface CloudMe {
  account: {
    id: string;
    channel: string;
    hint: string;
    created_at: number;
    member: boolean;
    any_model?: boolean;
    has_password: boolean;
    password_set_at: number | null;
    sessions: number;
    signed_in_via: string;
    /** "cn" for a mainland account; older relays do not say */
    region?: string;
  };
  usage: {
    today: { by_kind: UsageRow[] };
    total: { by_kind: UsageRow[]; by_model: UsageRow[] };
    kinds: string[];
  };
  tokens: { unlimited: boolean; granted: number; used: number; remaining: number; used_today: number; daily_cap: number };
  /**
   * Relay 0.5: one pool for the account's lifetime — `total` spent against `grant`, `left`
   * (null = no limit), `warn` from 80 %. `usd_cny` converts for display. The 0.4 names
   * (`today`, `daily_cap`, …) are still sent for one version.
   */
  spend: {
    currency: string;
    today: number;
    total: number;
    grant?: number;
    left?: number | null;
    unlimited: boolean;
    warn?: boolean;
    usd_cny?: number;
    allowance_cny?: number;
    invite_bonus_cny?: number;
    /** relay 0.9: the friend who signs up with the code gets the same */
    invitee_bonus_cny?: number;
    own_key_docs?: string;
    /** relay 0.21 (contract C11): the whole own-key card for this person's region */
    guidance?: Guidance;
    daily_cap: number;
    credit_left?: number;
    left_today?: number | null;
  };
  /** relay 0.17: where the relay places the person */
  region?: "cn" | "intl" | "unknown" | string;
  /** Relay 0.4/0.5: the account's invite code and what came of it (`earned_cny` since 0.5). */
  invite?: { code: string; url: string; invites: number; bonus_cny: number; invitee_bonus_cny?: number; earned_cny?: number; friends: Array<{ hint: string; joined_at: number }> };
  /**
   * Data controls: whether "Help improve MiniMuse's AI models" is on for this account, how many
   * turns the relay keeps, what the relay's default for new accounts is (0.9), what a kept turn
   * holds and where its privacy policy is.
   */
  contribute?: { on: boolean; samples: number; default_on?: boolean; keeps?: { kept: string[]; not_kept: string[] }; privacy_url?: string };
  /** Relay 0.4 counted clips; 0.5 no longer does (always unlimited here). */
  clips?: { unlimited: boolean; allowed: number | null; used: number; left: number | null; per_face: number };
  models?: Array<{ id: string; name?: string; nanomuse?: { kind?: string; recommended?: boolean } }>;
}

// ------------------------------------------------------------------ own-key providers (C11)

export type Capability = "chat" | "vision" | "image" | "video";

/** One entry of `nanomuse/llm/providers.json`, the own-key catalogue every client reads. */
export interface CatalogueProvider {
  id: string;
  name: string;
  name_zh: string;
  protocol: "openai" | "openai-responses" | "anthropic" | "gemini" | string;
  base_url: string;
  base_url_global?: string;
  key_url: string;
  key_url_global?: string;
  key_hint?: string;
  auth: string[];
  /** a sign-in that gives less than the key does (the ChatGPT one: chat and vision) */
  auth_capabilities?: Record<string, Capability[]>;
  regions: Array<"cn" | "global">;
  capabilities: Capability[];
  defaults: Partial<Record<"chat" | "hands" | "image" | "video", string>>;
  /** "custom": the person says what the endpoint can do */
  user_capabilities?: boolean;
  note: string;
  note_zh: string;
  verified: string;
  /** from the runtime: the slots using this provider today */
  configured?: string[];
  /** from the runtime: the ChatGPT store holds tokens (on `openai`) */
  signed_in?: boolean;
}

/** `GET /api/providers` on the runtime (team Runtime's CONTRACT-chatgpt.md, §3). */
export interface ProvidersView {
  providers: CatalogueProvider[];
  region: "cn" | "global" | "";
  configured: Partial<Record<"chat" | "hands" | "image" | "video", { provider: string; model: string; protocol?: string; source?: string } | null>>;
  capabilities: Capability[];
  unavailable: Partial<Record<Capability, string>>;
  chatgpt?: { signed_in: boolean; label?: string; capabilities?: Capability[] };
}

/** The relay's own-key guidance (relay 0.21, `spend.guidance` and the refusal's). */
export interface Guidance {
  version?: number;
  region: "cn" | "intl" | "unknown" | string;
  docs: string;
  providers: Array<{
    id: string;
    name: string;
    name_zh: string;
    protocol?: string;
    base_url?: string;
    key_url: string;
    auth: string[];
    regions?: string[];
    covers: Capability[];
    defaults?: Record<string, string>;
    one_key?: boolean;
    note?: string;
    note_zh?: string;
  }>;
  plans: Array<{ id: string; provider: string; name: string; auth: string; clients: string[]; covers: Capability[] }>;
  local?: Array<{ id: string; name: string; name_zh: string }>;
  caveats?: { chatgpt?: string; chatgpt_zh?: string };
}

/** `POST /api/chatgpt/login`: the page to open; the runtime waits for the callback. */
export interface ChatGptLogin {
  url: string;
  callback?: string;
  expires_in?: number;
  /** false when the runtime could not listen on port 1455: the callback address has to be pasted (`POST /api/chatgpt/callback`) */
  port_bound?: boolean;
}

/** `GET /api/chatgpt/status`: the token store, never the tokens. */
export interface ChatGptStatus {
  signed_in: boolean;
  label?: string;
  plan?: string;
  account_id?: string;
  expires_at?: number;
  expires_in?: number;
  /** a sign-in started and not finished yet */
  pending?: boolean;
  /** the page of a pending sign-in, for a second tab */
  url?: string;
  /** while pending: whether the runtime listens for the callback itself */
  port_bound?: boolean;
  /** while pending: seconds until the login is given up */
  login_expires_in?: number;
  error?: string;
  /** `cancelled`, `timeout`, `state_mismatch`, `exchange_failed`, … */
  error_code?: string;
  models?: string[];
}

/** A coding agent on a computer: Cursor, Codex, Claude Code. */
export interface CodingAgent {
  id: "cursor" | "codex" | "claude" | string;
  name: string;
  installed: boolean;
  cli: string | null;
  version: string;
  sessions_root: string;
  running: number;
}

export interface CodingMessage {
  role: "user" | "assistant" | string;
  text: string;
  ts?: number;
}

/** One chat of a coding agent, as read from its store on disk. */
export interface CodingSession {
  agent: string;
  id: string;
  title: string;
  workspace: string;
  path: string;
  created_at: number;
  updated_at: number;
  messages: number;
  status: "idle" | "active" | "running" | string;
  last_user: string;
  last_assistant: string;
  source: string;
  resumable: boolean;
  transcript?: CodingMessage[];
  runs?: CodingRun[];
}

/** A message sent into a coding agent through MiniMuse, and how it went. */
export interface CodingRun {
  id: string;
  agent: string;
  session_id: string;
  asked_session_id?: string;
  workspace: string;
  text: string;
  started_at: number;
  ended_at: number | null;
  status: "running" | "done" | "failed" | "stopped" | string;
  output: string;
  resumed: boolean;
  error: string;
  tools: number;
  /** set when the run is on another computer */
  device?: string;
}

export interface CodingEvent {
  kind: "started" | "text" | "tool" | "done" | "error" | "run" | string;
  text?: string;
  partial?: boolean;
  run?: string;
  session_id?: string;
  model?: string;
}

/** This device on the hub, and the other devices of the account. */
export interface HubView {
  enabled: boolean;
  remote_control: boolean;
  state: "connected" | "connecting" | "disconnected" | "refused" | "stopped" | "signed_out" | "off" | string;
  detail: string;
  device: { id: string; name: string; kind: string; actions: string[] };
  devices: HubDevice[];
  account: CloudAccount;
}

/** This computer's own screen and hands. */
export interface HandsStatus {
  enabled: boolean;
  available: boolean;
  backend?: string | null;
  reason?: string;
  /** where the hands work: the whole screen, one app's window (macOS), or auto */
  mode?: "auto" | "screen" | "window" | string;
  /** window mode: whether it can run here and what it is working in */
  window?: {
    available: boolean;
    reason?: string;
    active: boolean;
    app?: string;
    title?: string;
    frame?: { id: number; pid: number; app: string; bundle_id: string; title: string; bounds: number[] } | null;
  };
  device?: { id: string; name: string; platform: string; width: number; height: number };
  task_active?: boolean;
  task_text?: string;
  last_screen?: { app: string; app_name: string; route: string; elements: number; image: string | null; taken_at: number } | null;
}

/** A live step of the hands, for the stage overlay (not persisted). */
export interface HandsLive {
  event: "begin" | "act" | "end" | "stop" | "notice" | string;
  thread: string;
  text?: string;
  action?: string;
  label?: string;
  fx?: number;
  fy?: number;
  fx2?: number;
  fy2?: number;
  /** the point in screen pixels (for a cursor sprite); window mode maps the model's window pixels here */
  x?: number;
  y?: number;
  x2?: number;
  y2?: number;
  mode?: "screen" | "window" | string;
  window?: { id: number; pid: number; app: string; bundle_id: string; title: string; bounds: number[] };
  app?: string;
  title?: string;
  ts: number;
}

export interface Status {
  state: "idle" | "working" | "waiting";
  detail: string;
  thread: string;
  ts?: string;
}

export interface Profile {
  name: string;
  /** "dragon", the id of a face drawn in the avatar studio (see avatars.ts), or "" for the emoji on a colour. */
  avatar: string;
  emoji: string;
  color: string;
  /** "theme" (matches active light/dark/custom theme), "default" (#f1efeb), "color" (custom color), or "image" (custom background image) */
  avatar_bg_mode?: "theme" | "default" | "color" | "image" | string;
  avatar_bg_color?: string;
  avatar_bg_image?: string;
  /** "theme" (matches theme accent/avatar color), "color" (custom border color), or "none" */
  avatar_border_mode?: "theme" | "color" | "none" | string;
  avatar_border_color?: string;
  /** 0..100 border glow intensity (default 55) */
  avatar_border_glow?: number;
  /** Whether live looping 3D/video avatar animations play (default true) */
  avatar_animated?: boolean;
  /** Custom situation -> animation/image filename mapping */
  avatar_situation_clips?: Record<string, string>;
  updated_at?: number;
  /** one line under the name */
  tagline: string;
  /** how it talks: "" | formal | casual | playful | concise */
  tone: string;
  /** how much and in what shape: "" | short | detailed | bullets */
  communication: string;
  /** free text about its personality, in the user's words */
  style: string;
  /** What you want to be called. */
  user_name: string;
  /** How eagerly background work runs and reaches out. */
  proactivity: Proactivity;
  /** `proactivity !== "off"`, kept for older clients. */
  proactive: boolean;
  goal_interval_minutes: number;
  /** "22:00-08:00" in the server's local time, or "" for none. */
  quiet_hours: string;
}

export type Proactivity = "off" | "low" | "default" | "high" | "auto";

export interface GoalStep {
  idx: number;
  title: string;
  status: "pending" | "in_progress" | "done" | "blocked" | "skipped";
  note: string;
  updated_at: string;
}

export type GoalCategory =
  | ""
  | "health"
  | "finance"
  | "career"
  | "learning"
  | "relationships"
  | "family"
  | "home"
  | "travel"
  | "creative"
  | "other";

/** A plan change the agent suggested; the user accepts or dismisses it. */
export interface GoalProposal {
  reason: string;
  steps: string[];
  created_at: string;
}

export type GoalKind = "goal" | "task" | "habit";
export type GoalPriority = "urgent" | "high" | "medium" | "low";
export type GoalRecurrence = "" | "daily" | "weekdays" | "weekly" | "biweekly" | "monthly" | "yearly";

export interface Goal {
  id: string;
  title: string;
  description: string;
  status: "active" | "paused" | "done" | "cancelled";
  notes: string;
  category: GoalCategory;
  kind?: GoalKind;
  priority?: GoalPriority;
  tags?: string[];
  /** Target date, YYYY-MM-DD, or "". */
  due: string;
  due_time?: string;
  recurrence?: GoalRecurrence;
  sort_order?: number;
  completed_at?: string;
  overdue: boolean;
  /** Reminder cadence such as "daily 08:00" or "weekly mon 09:00", or "". */
  check_in: string;
  next_check_in: string | null;
  proposal: GoalProposal | null;
  created_at: string;
  updated_at: string;
  progress: { done: number; total: number };
  next_step: string | null;
  steps: GoalStep[];
}

export interface MemoryItem {
  id: string;
  content: string;
  category: string;
  created_at: string;
  source: string;
  /** set when the line was rewritten or is the result of a merge */
  updated_at?: string;
}

/** One entry of the memory log: a tidy-up merge or drop, a forget, or an update the agent made. Undoable. */
export interface MemoryChange {
  id: string;
  at: string;
  action: "merge" | "rewrite" | "drop" | "forget";
  before: MemoryItem[];
  after: MemoryItem | null;
  reason: string;
  restored: boolean;
}

export interface TidyReport {
  considered: number;
  changed: number;
  merged: MemoryChange[];
  dropped: MemoryChange[];
  skipped: string[];
  planned: { op: string; before: string[]; content?: string; reason?: string }[];
  lines: string[];
}

export interface Idea {
  title: string;
  detail: string;
  prompt: string;
  /** planning | goals | research | money | health | home | learning | people | files | fun */
  area?: string;
  /** what tapping it does: chat (a message in the main chat, the default) · routine (a daily
   *  repeating reminder with the prompt) · goal (a goal conversation in the chat) */
  kind?: "chat" | "routine" | "goal" | string;
  /** routines: the time of day, "HH:MM" */
  time?: string;
  /** goals: the category key (health, finance, … other) */
  category?: string;
}

export interface FeedPost {
  id: string;
  ts: string;
  title: string;
  /** Markdown, a short read. */
  body: string;
  area: string;
  /** A follow-up the user could send, or "". */
  prompt: string;
}

export interface FeedPostsData {
  instructions: string;
  generated_at: string | null;
  posts: FeedPost[];
  /** The daily routine (contract C5): on by default, at `time` (`HH:MM`, local; 08:00). */
  daily: boolean;
  time: string;
  error?: string;
}

export interface IdeasData {
  generated_at: string | null;
  source: string;
  ideas: Idea[];
  error?: string;
}

export interface ToolInfo {
  name: string;
  risk: RiskLevel;
  description: string;
}

export interface ReleaseNotesEntry {
  version: string;
  title?: string;
  added?: string[];
  improved?: string[];
  fixed?: string[];
  raw?: string;
}

export interface PersonalCloudStatus {
  host: string;
  port: number;
  lan_urls: string[];
  all_urls?: string[];
  tailscale: {
    installed: boolean;
    running: boolean;
    ip: string;
    dns_name: string;
    url: string;
  };
  cloudflared_installed: boolean;
  auto_tunnel?: boolean;
  tunnel: {
    running: boolean;
    starting: boolean;
    installing?: boolean;
    needs_install?: boolean;
    url: string;
    error: string;
  };
  best_url: string;
}

export interface VrHeadsetDevice {
  id: string;
  state: string;
  is_vr: boolean;
  name?: string;
  manufacturer?: string;
  model?: string;
  device?: string;
  android_version?: string;
  serial?: string;
}

export interface VrDevicesInfo {
  adb_installed: boolean;
  headsets: VrHeadsetDevice[];
  devices: VrHeadsetDevice[];
  saved?: { target?: string; serial?: string; name?: string; saved_at?: number };
  apk_cached: boolean;
  download_url: string;
  release_download_url: string;
}

export interface VrInstallResult {
  ok: boolean;
  target?: string;
  name?: string;
  apk?: string;
  accessibility_enabled?: boolean;
  wifi_target?: string;
  auto_paired?: boolean;
  best_url?: string;
  error?: string;
}

export interface ChromeBridgeStatus {
  extension_packaged: boolean;
  connected: boolean;
  attached: boolean;
  version: string;
  browser: string;
  tab_id: number | null;
  url: string;
  title: string;
  width: number;
  height: number;
}

export interface CallCapabilities {
  supported: boolean;
  voice_loop: boolean;
  mode: "openai_realtime" | "gemini_live" | "voice_loop" | string;
  native_realtime: boolean;
  provider: string;
  model: string;
  override_active: boolean;
  has_api_key: boolean;
  default_patience_ms: number;
  available_realtime_options: Array<{ provider: string; model: string; kind: string }>;
}

export interface ComputerWindowInfo {
  window_id: number;
  owner: string;
  app_id: string;
  title: string;
}

export interface WatchCodeInfo {
  code: string;
  expires_in: number;
  best_url: string;
  lan_urls: string[];
  agent_name: string;
}

/** `GET /api/update`: the installed and the latest release (contract C2). */
export interface UpdateView {
  current: string;
  enabled: boolean;
  latest: string | null;
  newer: boolean;
  /** The release page of the latest. */
  url: string;
  /** The download page (nanomuse.cn/dl). */
  download_url?: string;
  /** When the runtime last asked (ISO, UTC), or null before the first check. */
  checked_at?: string | null;
  /** Where the answer came from: `nanomuse.cn`, `github`, or null. */
  source?: string | null;
  error?: string | null;
  auto_update?: boolean;
  auto_supported?: boolean;
  editable?: boolean;
  frozen?: boolean;
  busy?: boolean;
  last_error?: string;
  latest_notes?: ReleaseNotesEntry | null;
  whats_new?: ReleaseNotesEntry | null;
  current_notes?: ReleaseNotesEntry | null;
}

/** `GET /api/nudges`: the star-ask policy (contract C1) as the runtime holds it. */
export interface NudgesView {
  policy: unknown;
  fetched_at: number | null;
  source: "default" | "relay" | "me" | string;
  stale: boolean;
  error: string | null;
}

export interface SettingsView {
  version: string;
  profile: Profile;
  sentinel: {
    mode: "ask" | "strict" | "auto";
    always_ask_tools: string[];
    always_allow_tools: string[];
    deny_tools: string[];
    taint_tracking: boolean;
    egress_allowlist: string[];
  };
  /** Each shell / python call in its own bubblewrap namespace (Linux); status says why not. */
  sandbox: { mode: "auto" | "bwrap" | "off"; active: boolean; status: string };
  llm: { provider: string; model: string; stream: boolean; cloud?: boolean; background_model?: string };
  agent: {
    language: string;
    max_steps: number;
    /** keep going at the step cap instead of stopping (the live default) */
    auto_continue?: boolean;
    /** how many cap-crossings one run may ride through before it wraps up */
    max_continues?: number;
    /** the context window's size: messages and tokens kept for the model */
    max_context_messages?: number;
    max_context_tokens?: number;
    show_thinking: boolean;
    allow_self_improve?: boolean;
    workspace: string;
  };
  connectors: { email: boolean; calendar: boolean; contacts: boolean; browser: boolean; gui: boolean; mcp: string[] };
  phone: PhoneStatus & { gui_enabled: boolean };
  tools: ToolInfo[];
  memory_enabled: boolean;
  /** Skills: recipes for jobs (SKILL.md folders); count = the ones switched on. */
  skills: { enabled: boolean; count: number; yours: number };
  data_dir: string;
  started_at: string;
  /** First-run setup finished (or skipped) in the app. */
  onboarded: boolean;
  /** A model is configured with a key (or a local endpoint that needs none). */
  llm_ready: boolean;
  /** Whether runtime automatically checks and installs release updates in the background. */
  auto_update?: boolean;
  /** How many days deleted chats stay recoverable in Trash before permanent expiry. */
  trash_retention_days?: number;
  /** The sticky model pick, one value for every device ("" = the configured model). */
  model_choice?: string;
  /** How AI & sync route across local vs. nanoMuse Cloud ("local", "hybrid", "cloud"). */
  cloud_mode?: "local" | "hybrid" | "cloud";
  /** Per-feature routing when cloud_mode === "hybrid". */
  cloud_slots?: { chat?: "local" | "cloud"; media?: "local" | "cloud"; sync?: "local" | "cloud" };
  /** Whether unsolicited nanoMuse Cloud sign-in cards/prompts appear across tabs. */
  cloud_prompts?: boolean;
  /** Current app lock summary for Settings preview and safety prompt. */
  app_lock?: LockView;
  /** Number of saved credentials in the password vault. */
  passwords_count?: number;
  /** Local computer + connected phones summary for Settings preview. */
  local_devices?: LocalDevicesView;
  /** The look choices, one value for every device ("" = the app's own look). */
  ui?: {
    accent?: string;
    bubble?: string;
    theme?: string;
    bg?: string;
    scale?: string;
    show_calendar?: string;
    show_location?: string;
    map_app?: string;
  } | null;
  /** What each device's capabilities may do ("always" | "ask" | "never"), one policy
   *  for the account, set from either device's Settings. */
  permissions?: { computer: Record<string, string>; phone: Record<string, string> };
}

// ----------------------------------------------------------------------------- skills
/** A skill: how a job is done, written down as a SKILL.md folder. */
export interface SkillInfo {
  name: string;
  description: string;
  /** "built-in" ships with the app; "yours" lives in <data_dir>/skills (and replaces a built-in of the same name). */
  source: "built-in" | "yours";
  enabled: boolean;
  path: string;
  /** scripts/, references/, assets/ that come with it. */
  files: string[];
  allowed_tools: string[];
  metadata: Record<string, string>;
  updated_at: string | null;
}

export interface SkillDetail extends SkillInfo {
  /** The instructions (Markdown after the front matter). */
  body: string;
  /** The whole SKILL.md, for editing. */
  content: string;
}

export interface SkillsData {
  count: number;
  built_in: number;
  yours: number;
  dir: string;
  errors: Record<string, string>;
  skills: SkillInfo[];
}

// ----------------------------------------------------------------------------- connections
export interface ProviderPreset {
  label: string;
  /** who is behind it, or what protocol: "Moonshot AI · 月之暗面", "Responses API" */
  subtitle?: string;
  /** how the form groups them: "cloud" (the account), "openai" (Chat Completions), "responses", "local" */
  group?: "cloud" | "openai" | "responses" | "local" | string;
  provider: "openai" | "openai_responses" | string;
  base_url: string;
  /** the fallback catalogue; the live list comes from /api/llm/models */
  models?: string[];
  no_key?: boolean;
  /** the nanoMuse Cloud account as the provider: the account key is the key, saved through /api/cloud/use-as-model */
  cloud?: boolean;
  /** a key may be left empty (a gateway or local server without one) */
  key_optional?: boolean;
  /** where a key comes from */
  key_url?: string;
  /** what a key looks like there, as the field's placeholder */
  key_hint?: string;
  /** the hands model the preset suggests beside its chat model (contract C4): one that sees */
  gui_model?: string;
  /** the account's hands models, recommended first (the nanoMuse Cloud preset) */
  gui_models?: string[];
  /** "cn": the vendor only signs up accounts from mainland China (contract C5) */
  region?: string;
}

/** One of the `image` / `video` slots: as set, and what it resolves to today. */
export interface MediaSlotData {
  /** a catalogue id, or `openai` / `openai_responses` with a `base_url`; "" when not set */
  provider: string;
  /** the catalogue entry that means, by id or by the URL's host; "" when unlisted or not set */
  provider_id: string;
  /** "" = the catalogue's default for the provider */
  model: string;
  base_url: string;
  key_source: "none" | "vault" | "config" | "missing";
  /** something is set here (an explicit choice) */
  configured: boolean;
  from_app: boolean;
  /** what the slot resolves to: a catalogue id, `nanomuse_cloud`, `custom`; "" when nothing draws */
  effective_provider: string;
  effective_model: string;
  /** `app` / `config` for a choice, `chat` for the chat provider's own model, `cloud` for the relay, "" for none */
  effective_source: "app" | "config" | "chat" | "cloud" | "";
  /** the custom endpoint (of `ConnectionsData.endpoints`) the slot stands on; "" or absent otherwise (#294) */
  endpoint?: string;
}

/**
 * A custom OpenAI-compatible endpoint the runtime keeps in its list (#294): a name, an
 * address, a key of its own in the vault, and the models it serves. Never the key itself.
 */
export interface CustomEndpoint {
  id: string;
  label: string;
  base_url: string;
  key_source: "none" | "vault" | "config" | "missing";
  models: string[];
  /** the slots that answer through it today */
  slots: Array<"chat" | "hands" | "image" | "video">;
}

export interface ModelCatalogInfoEntry {
  company: string;
  tier: "light" | "normal" | "heavy" | string;
  tier_label: string;
  good_for: string;
  cost_input_1m: number;
  cost_output_1m: number;
  cost_summary: string;
  free_tier_info: string;
  quota_kind: string;
  reset_period: string;
  recommended_tasks?: string[];
  vision?: boolean;
  local?: boolean;
}

export interface RecommendedDefaultSet {
  label: string;
  description: string;
  tasks: Record<string, { label: string; model: string; preset: string; base_url: string; why: string }>;
  options: Array<{ model: string; label?: string; provider?: string; base_url?: string }>;
}

export interface ProviderKeySlotView {
  id: string;
  label: string;
  vault_key: string;
  base_url: string;
  provider: string;
  quota_kind: string;
  default_reset: string;
  has_balance_api: boolean;
  key_url?: string;
  key_hint?: string;
  configured: boolean;
  rule: {
    enabled: boolean;
    priority: number;
    quota_kind: string;
    reset_period: string;
    daily_request_limit: number;
    daily_token_limit: number;
    monthly_budget_usd: number;
    on_exhausted: "fallback_openrouter" | "switch_model" | "cooldown_skip" | "fallback_local" | "stop" | string;
    fallback_model: string;
  };
}

export interface LlmRoutingView {
  enabled: boolean;
  global_on_exhausted: string;
  openrouter_fallback: boolean;
  local_last_resort: boolean;
  provider_rules: Record<string, Record<string, unknown>>;
  model_rules: Array<Record<string, unknown>>;
  task_models: Record<string, string>;
  provider_slots: ProviderKeySlotView[];
}

export interface ProviderUsageStatus {
  id: string;
  label: string;
  configured: boolean;
  quota_kind: string;
  reset_period: string;
  in_cooldown: boolean;
  cooldown_until: number;
  cooldown_remaining_s: number;
  exhausted_reason: string;
  next_reset_at: number;
  next_reset_in_s: number;
  usage: {
    minute: { requests: number; tokens: number; cost_usd: number };
    day: { requests: number; tokens: number; cost_usd: number };
    week: { requests: number; tokens: number; cost_usd: number };
    month: { requests: number; tokens: number; cost_usd: number };
  };
  remaining: {
    requests_day?: number | null;
    tokens_day?: number | null;
    budget_month_usd?: number | null;
    balance?: number | null;
    balance_currency?: string | null;
    header_remaining_requests?: number | null;
    header_remaining_tokens?: number | null;
  };
  learned: {
    quota_kind?: string;
    reset_period?: string;
    observed_request_ceiling?: number;
    observed_token_ceiling?: number;
    hit_count?: number;
    last_hit_at?: number;
    last_error_excerpt?: string;
  };
  live_balance: {
    balance?: number;
    currency?: string;
    limit?: number | null;
    usage?: number;
    is_free_tier?: boolean;
    fetched_at?: number;
  };
  rate_headers: Record<string, unknown>;
}

export interface LlmUsageStatusView {
  updated_at: number;
  providers: Record<string, ProviderUsageStatus>;
  events: Array<{
    ts: number;
    provider_id: string;
    model: string;
    kind: string;
    reset_period: string;
    cooldown_s: number;
    detail: string;
  }>;
}

export interface SavedModelOption {
  model: string;
  label?: string;
  provider?: string;
  base_url?: string;
  endpoint?: string;
}

export interface ConnectionsData {
  llm: {
    provider: string;
    model: string;
    base_url: string;
    tool_mode: string;
    stream: boolean;
    /** vault = key entered in the app; config = from config.toml / env; missing = referenced but not set. */
    key_source: "vault" | "config" | "missing" | "none";
    from_app: boolean;
    /** the model is the nanoMuse Cloud account's */
    cloud?: boolean;
    /** the avatar studio's picture model at the same host; "" = the runtime's pick */
    image_model?: string;
    /** the clip model at the same host; "" = the runtime's pick */
    video_model?: string;
    /** `[llm] proxy` for this slot's requests; credentials masked; "" = none (older runtimes: absent) */
    proxy?: string;
    /** the custom endpoint the chat model answers through; "" when none (older runtimes: absent) */
    endpoint?: string;
    /** configured multi-model quick-switch options for the chat model menu */
    model_options?: SavedModelOption[];
  };
  providers: Record<string, ProviderPreset>;
  /** The custom endpoints in the runtime's list (#294); absent on a runtime before it kept one. */
  endpoints?: CustomEndpoint[];
  /** Recall by meaning: memories embedded through an OpenAI-compatible /embeddings endpoint. */
  embeddings: {
    mode: "auto" | "on" | "off";
    /** "" → default_model */
    model: string;
    default_model: string;
    /** "" → the model's endpoint */
    base_url: string;
    effective_base_url: string;
    /** model = the model's own key on its endpoint; vault/config/missing = a key of its own */
    key_source: "model" | "vault" | "config" | "missing";
    from_app: boolean;
    memory_enabled: boolean;
    /** null until the first call of this run */
    available: boolean | null;
    reason: string;
    dims: number;
    indexed: number;
    total: number;
    status: string;
  };
  search: {
    provider:
      | "duckduckgo"
      | "brave"
      | "tavily"
      | "serply"
      | "you"
      | "parallel"
      | "keenable"
      | "searxng";
    /** SearXNG instance */
    base_url: string;
    key_source: "none" | "vault" | "config" | "missing";
    /** what the provider needs is there (a key, or an instance URL) */
    configured: boolean;
    from_app: boolean;
    providers: Array<{
      id: string;
      label: string;
      needs_key: boolean;
      /** works without a key; one raises the limit (You.com, Parallel, Keenable) */
      optional_key?: boolean;
      keys_url: string;
    }>;
  };
  email: {
    enabled: boolean;
    configured: boolean;
    address: string;
    imap_host: string;
    imap_port: number;
    smtp_host: string;
    smtp_port: number;
    smtp_starttls: boolean;
    auth_mode?: "password" | "oauth";
    password_set: boolean;
  };
  email_accounts?: EmailAccount[];
  apis?: ApiConnection[];
  google?: GoogleStatus;
  browser: { enabled: boolean; available: boolean };
  /** Operating the phone through its screen (the GUI agent) and the operator's model. */
  gui: {
    enabled: boolean;
    /** a protocol (`openai` / `openai_responses`) or a catalogue id, as for the chat model */
    provider: string;
    /** the catalogue entry `provider` means, by id or by the URL's host; "" when unlisted (0.1.41) */
    provider_id?: string;
    model: string;
    /** the model the hands use right now: `model`, else the default below */
    effective_model?: string;
    /** where that comes from: `gui` (set here), `chat` (the chat provider's hands model), `cloud` (the relay's) */
    effective_source?: "gui" | "chat" | "cloud";
    /** what the hands use when `model` is empty: the relay's hands model with the account, the chat model otherwise */
    default_model?: string;
    /** the chat model is the account (the relay) */
    cloud?: boolean;
    base_url: string;
    key_source: "none" | "vault" | "config" | "missing";
    /** the custom endpoint the hands model answers through when set here; "" otherwise (#294) */
    endpoint?: string;
    max_steps: number;
    phone: PhoneStatus;
  };
  /** Where pictures come from (`[image]`); absent on a runtime before 0.1.41. */
  image?: MediaSlotData;
  /** Where clips come from (`[video]`); absent on a runtime before 0.1.41. */
  video?: MediaSlotData;
  calendar: {
    enabled: boolean;
    configured: boolean;
    refresh_minutes: number;
    /** Working hours, "HH:MM", for "when am I free". */
    day_start: string;
    day_end: string;
    feeds: CalendarFeed[];
  };
  contacts: {
    enabled: boolean;
    /** Anyone to look up: a source, or people the agent was told about. */
    configured: boolean;
    count: number;
    /** People in the agent's own book ("My contacts"). */
    own: number;
    sources: ContactSource[];
  };
  mcp: Array<{
    name: string;
    command: string | null;
    args: string[];
    url: string | null;
    risk: string;
    tools: number;
    connected: boolean;
    from_app: boolean;
    /** the phone's own capabilities (local build): always there, cannot be removed */
    builtin?: boolean;
    /**
     * The server's environment variables: the names, and whether each value is a vault
     * reference (`{{vault:NAME}}`); never a value. A plain value is one written as text in
     * config.toml. Older runtimes leave it out.
     */
    env?: Array<{ name: string; vault: boolean }>;
  }>;
  /**
   * Connections the account's other devices hold (contract C3): a name, an address and how
   * it signs in — never a key. ``here`` when a server of that name is connected on this one.
   * Older runtimes leave it out.
   */
  shared?: SharedConnector[];
  model_catalog_info?: Record<string, ModelCatalogInfoEntry>;
  recommended_defaults?: RecommendedDefaultSet;
  llm_routing?: LlmRoutingView;
  llm_usage_status?: LlmUsageStatusView;
  vault: string[];
  onboarded: boolean;
}

export interface GoogleAccountInfo {
  email: string;
  name?: string;
  primary?: boolean;
  scopes?: string[];
  last_sync_at?: string | null;
  last_sync?: Record<string, unknown>;
}

export interface EmailAccount {
  id: string;
  label: string;
  address: string;
  imap_host: string;
  imap_port: number;
  smtp_host: string;
  smtp_port: number;
  smtp_starttls: boolean;
  auth_mode: "password" | "oauth" | string;
  password_set: boolean;
  enabled: boolean;
  primary: boolean;
}

export interface ApiEndpoint {
  id: string;
  name: string;
  method: string;
  path: string;
  description?: string;
  query_params?: string[];
  body_schema?: Record<string, unknown>;
}

export interface ApiPreset {
  id: string;
  name: string;
  category: string;
  description: string;
  base_url: string;
  auth_type: string;
  auth_header?: string;
  auth_param?: string;
  auth_prefix?: string;
  default_headers?: Record<string, string>;
  ai_guide?: string;
  docs_url?: string;
  key_url?: string;
  key_hint?: string;
  uses_google_oauth?: boolean;
  endpoints?: ApiEndpoint[];
  fields_hint?: string[];
  popular?: boolean;
}

export interface LocalRuntimeStatus {
  id: "ollama" | "lm_studio" | "vllm" | string;
  name: string;
  base_url: string;
  running: boolean;
  models: string[];
  install_url: string;
}

export interface LocalModelRecommendation {
  id: string;
  name: string;
  params: string;
  size_gb: number;
  min_ram_gb: number;
  tier: "ultra_tiny" | "lightweight" | "sweet_spot" | "workstation" | string;
  summary: string;
  tool_quality: string;
  recommended_for: string;
  fit: "fits" | "tight" | "too_heavy";
  installed: boolean;
  preset: string;
  pull_cmd: string;
}

export interface SystemCapabilities {
  host: {
    os: string;
    arch: string;
    cpu_cores: number;
    total_ram_gb: number;
    free_ram_gb: number;
    is_android: boolean;
    is_mobile: boolean;
    apple_silicon: boolean;
    gpu_name: string;
    vram_gb: number;
  };
  client: {
    ram_gb?: number | null;
    cores?: number | null;
    is_mobile: boolean;
    remote_client_mismatch: boolean;
  } | null;
  runtimes: LocalRuntimeStatus[];
  verdict: {
    tier: "weak_device" | "limited" | "good" | "workstation" | string;
    worth_it: "cloud_recommended" | "mixed" | "worth_it" | string;
    headline: string;
    explanation: string;
    tradeoff_summary: string;
  };
  recommended_models: LocalModelRecommendation[];
}

export interface ApiConnection {
  id: string;
  name: string;
  preset_id: string;
  category: string;
  base_url: string;
  auth_type: string;
  auth_header: string;
  auth_param: string;
  auth_prefix: string;
  vault_key: string;
  has_secret: boolean;
  headers: Record<string, string>;
  ai_guide: string;
  learned_notes: string;
  docs_url: string;
  test_endpoint: { method?: string; path?: string };
  endpoints: ApiEndpoint[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
  last_tested_at: string;
  last_status: string;
  last_error: string;
}

export interface GoogleStatus {
  signed_in: boolean;
  email: string;
  accounts?: GoogleAccountInfo[];
  has_client_id: boolean;
  client_id_preview?: string;
  scopes?: string[];
  pending?: boolean;
  auth_error?: string;
  last_sync_at?: string;
  last_sync?: {
    calendar_events?: number;
    contacts?: number;
    email?: string;
    errors?: string[];
  };
}

/** A wireless-debugging service found on the network (a watch's pairing screen or connect port). */
export interface WatchEndpoint {
  name: string;
  host: string;
  port: number;
  host_port: string;
  kind?: "connect" | "pairing";
  /** the device serial inside the service name: stable, unlike the port */
  serial?: string | null;
}

export interface WatchDevicesInfo {
  adb_available: boolean;
  adb_installed?: boolean;
  devices: Array<{ id: string; state: string; model?: string; product?: string; device?: string; is_watch?: boolean | null; characteristics?: string; serial?: string }>;
  mdns_connect?: WatchEndpoint[];
  mdns_pairing?: WatchEndpoint[];
  /** what the background finder last saw, and the watch it remembers */
  auto?: {
    last_check: number;
    last_result: string;
    watch_online: boolean;
    saved?: { target?: string; serial?: string; model?: string; host?: string };
  };
  apk_path: string | null;
  apk_ready: boolean;
  apk_cached?: boolean;
  download_url: string;
}

export interface WatchInstallOptions {
  target?: string;
  pair_host_port?: string;
  pair_code?: string;
  connect_host_port?: string;
  auto_pair_watch?: boolean;
  grant_permissions?: boolean;
}

/** Why an install did not happen: the screen shows a different popup for each. */
export type WatchInstallCode =
  | "adb_missing"
  | "apk_missing"
  | "no_watch"
  | "phone_only"
  | "needs_pairing"
  | "need_pairing_code"
  | "choose_pairing"
  | "no_pairing_service"
  | "pair_failed"
  | "unauthorized"
  | "install_failed";

export interface WatchInstallResult {
  ok: boolean;
  /** set when `ok` is false */
  code?: WatchInstallCode;
  pairing?: WatchEndpoint[];
  connect_failed?: string[];
  target?: string;
  apk?: string;
  output?: string;
  error?: string;
  download_url?: string;
  permissions_granted?: boolean;
  auto_paired?: boolean;
  best_url?: string;
  steps?: string[];
}

/** One connection another device of the account reports. */
export interface SharedConnector {
  id: string;
  label: string;
  url: string;
  auth: "oauth" | "key" | "open" | string;
  /** the device's human label and the hub id behind it */
  device: string;
  device_id: string;
  enabled: boolean;
  /** ISO-8601 */
  at: string;
  here: boolean;
}

/** One connected calendar: a private .ics link (kept in the vault) or a file. */
export interface CalendarFeed {
  name: string;
  from_app: boolean;
  events: number;
  fetched_at: string | null;
  error: string;
}

/** One connected address book: a .vcf file (uploaded or on disk) or a link kept in the vault. */
export interface ContactSource {
  name: string;
  from_app: boolean;
  file: boolean;
  contacts: number;
  fetched_at: string | null;
  error: string;
}

/** One person, as GET /api/contacts returns them. */
export interface Contact {
  id: string;
  name: string;
  first: string;
  last: string;
  nickname: string;
  /** "alice@example.com" or "alice@example.com (work)". */
  emails: string[];
  phones: string[];
  org: string;
  title: string;
  birthday: string;
  addresses: string[];
  urls: string[];
  note: string;
  source: string;
}

export interface CalendarEvent {
  uid: string;
  summary: string;
  start: string;
  end: string;
  all_day: boolean;
  location: string;
  description: string;
  calendar: string;
  source?: string;
  editable?: boolean;
  tags?: string[];
  contact_id?: string;
  contact_name?: string;
  rrule?: string;
}

/** GET /api/calendar: today's and tomorrow's events with the feeds' status. */
export interface CalendarData {
  enabled: boolean;
  configured: boolean;
  today: string;
  events: CalendarEvent[];
  feeds: Array<{ name: string; events: number; fetched_at: string | null; error: string }>;
  fetched_at: string | null;
  stale: boolean;
}

export interface TestResult {
  ok: boolean;
  /** what went wrong, as the sentence a chat turn would show (a dictionary key) */
  error?: string;
  /** a model test: the error's own words, one tap away for bug reports */
  detail?: string;
  code?: string;
  reply?: string;
  ms?: number;
  inbox?: number | null;
  /** calendar test: events across the feeds */
  events?: number;
  feeds?: number;
  /** contacts test: people across the sources */
  contacts?: number;
  sources?: number;
  /** embeddings test */
  model?: string;
  dims?: number;
  indexed?: number;
  /** search test */
  provider?: string;
  results?: number;
  first?: string;
}

export interface StateSnapshot {
  version: string;
  profile: Profile;
  status: Status;
  threads: ThreadMeta[];
  pending_approvals: ApprovalEvent[];
  goals: Goal[];
  settings: SettingsView;
  hub?: HubView;
  hands?: HandsStatus;
  /** the holds that are on (contract C1), as `hold` events */
  holds?: HoldEvent[];
  /** the other devices' turns under way on synced chats (contract C9) */
  working?: WorkingPresence[];
  /** the first conversation (contract C4); absent on an older runtime */
  firstrun?: FirstRunView;
  /** active in-flight streams so reconnecting clients never lose streaming bubbles */
  streams?: Record<string, { id: string; text: string; reasoning: string; ended?: boolean } | undefined>;
  /** number of deleted chats waiting in long-term Trash */
  trash_count?: number;
}

/** The first conversation's phases: `none → ask_user_name → ask_agent_name → named → done`. */
export type FirstRunPhase = "none" | "ask_user_name" | "ask_agent_name" | "named" | "done";

/** `GET /api/firstrun` and the `firstrun` frame: where the first conversation stands (contract C4). */
export interface FirstRunView {
  phase: FirstRunPhase;
  /** the thread it is bound to; null until Start */
  session_id: string | null;
  user_address: string | null;
  /** the model's name suggestions for itself, when it gave some */
  suggestions: string[];
  /** what the chooser offers: the suggestions, or two from the built-in pool */
  chips: string[];
  chosen: string | null;
  /** the language the opening was shown in: "en", "zh", or "" */
  lang: string;
  /** started and not over: its turns are never tasks */
  running: boolean;
  started_at: number;
  finished_at: number;
  /** the three lines the app speaks first, in the language asked for (GET only) */
  intro?: string[];
}

export interface AuditEntry {
  ts: string;
  event: string;
  session?: string;
  tool?: string;
  /** which rung the call was: gui (the phone's screen), browser, web, cli, device, api, local */
  channel?: string;
  summary?: string;
  risk?: string;
  decision?: string;
  approved?: boolean | null;
  approval_scope?: string | null;
  reasons?: string[];
  ok?: boolean;
  error?: string | null;
  duration_ms?: number;
  content?: string;
  tool_calls?: string[];
  [key: string]: unknown;
}

export interface ActivityData {
  audit: AuditEntry[];
  grants: Grant[];
  tainted: boolean;
}

/** One entry of the Feed: something that happened without you asking. */
export interface FeedItem {
  id: string;
  ts: string;
  kind: "background" | "artifact" | "approval" | "question";
  title: string;
  text: string;
  thread: string;
  thread_title: string;
  path?: string | null;
  quiet?: boolean;
}

export interface UpcomingData {
  proactive: boolean;
  proactivity: Proactivity;
  interval_minutes: number;
  /** The interval after the level's stretch/shrink. */
  effective_interval_minutes: number;
  quiet_hours: string;
  /** End of the current quiet window, if we are in one. */
  quiet_until: string | null;
  next_pass_at: string | null;
  queue: Array<{
    goal_id: string;
    title: string;
    category: GoalCategory;
    due: string | null;
    overdue: boolean;
    next_step: string | null;
    progress: { done: number; total: number };
  }>;
  /** Goal check-ins, soonest first. */
  check_ins: Array<{ goal_id: string; title: string; at: string; cadence: string }>;
  /** Reminders and routines: active ones soonest first, then recently finished. */
  reminders: Reminder[];
  /** Triggers: work that starts when something happens. */
  triggers: TriggersData;
  busy: boolean;
}

/** The local usage ledger (`/api/usage`), aggregated server-side from `usage.jsonl`.
 *  Works signed-out; the costs are the provider rate cards' estimates, nothing else. */
export interface UsageDay {
  day: string;
  turns: number;
  prompt_tokens: number;
  cached_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface UsageModel {
  model: string;
  turns: number;
  prompt_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface UsageRecent {
  ts: string | null;
  thread: string | null;
  model: string | null;
  source: string | null;
  cost_usd: number | null;
  duration_s: number | null;
  completion_tokens: number;
}

export interface UsageSummary {
  days: number;
  /** the exact model id this summary was narrowed to ("" = every model) */
  model?: string;
  turns: number;
  prompt_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  avg_duration_s: number;
  tool_calls: number;
  per_day: UsageDay[];
  per_model: UsageModel[];
  activities: Record<string, number>;
  media: Record<string, number>;
  recent: UsageRecent[];
  /** the local Ollama's loaded models (size and VRAM), for the RAM line on a local AI */
  ollama?: { models: Array<{ name: string; size_bytes: number; vram_bytes: number }> };
}

/** A spending cap the runtime checks before every turn: per model or global, over a
 *  day, a week or a month; it warns at `warning_pct` and then warns / stops / asks. */
export interface UsageLimitRow {
  id?: string;
  name?: string;
  scope: "global" | "model";
  model?: string;
  unit: "cost" | "tokens" | "requests";
  window: "day" | "week" | "month";
  amount: number;
  warning_pct: number;
  on_full: "warn" | "stop" | "confirm";
}

/** The lock screen's status: whether a PIN is set, and whether a fingerprint or face
 *  is registered on this device as the presence proof. */
export interface LockView {
  enabled: boolean;
  biometric: boolean;
  passkey?: boolean;
  pin_enabled?: boolean;
  biometric_credential_id?: string | null;
  passkey_credential_id?: string | null;
  /** how long an unlock lasts, in seconds; 0 asks on every fresh open */
  timeout_seconds?: number;
  /** lock as soon as the app is switched away */
  lock_on_leave?: boolean;
}

export interface PasswordEntry {
  id: string;
  title: string;
  url: string;
  domain: string;
  username: string;
  has_password: boolean;
  has_totp: boolean;
  notes: string;
  vault_key: string;
  updated_at: string;
}

export interface PasswordsView {
  items: PasswordEntry[];
  count: number;
}

export interface PasswordImportResult {
  added: number;
  updated: number;
  skipped: number;
  total: number;
  items: PasswordEntry[];
}

export type TriggerKind = "mail" | "event" | "hook" | "device";
export type TriggerStatus = "active" | "cancelled";

export interface Trigger {
  id: string;
  kind: TriggerKind;
  /** Words that must all appear in the sender/subject (mail), title/place (event) or the device's name and kind (device); a hook's name. */
  match: string;
  /** What to do when it fires. */
  text: string;
  thread: string;
  status: TriggerStatus;
  /** event: how long before the start. */
  lead_minutes: number;
  /** hook: the key in the URL ("" for other kinds). */
  secret: string;
  /** hook: the full URL to call ("" once cancelled). */
  url?: string;
  created_at: string;
  last_fired_at: string | null;
  fired: number;
}

export interface TriggersData {
  items: Trigger[];
  /** Which kinds have their connector: mail needs the mailbox, event the calendar, device this computer being on the hub. */
  available: Record<TriggerKind, boolean>;
  /** When the inbox was last looked at for mail triggers (ISO), and the last error if any. */
  mail_checked_at: string | null;
  mail_error: string;
  mail_poll_minutes: number;
}

export type ReminderKind = "remind" | "task";
export type ReminderStatus = "active" | "done" | "cancelled";

export interface Reminder {
  id: string;
  text: string;
  kind: ReminderKind;
  thread: string;
  status: ReminderStatus;
  /** Next time it fires (ISO); null once a one-off has fired. */
  next_at: string | null;
  /** "" for a one-off, else a cadence such as "daily 08:00". */
  repeat: string;
  created_at: string;
  last_fired_at: string | null;
  fired: number;
}

export interface PushInfo {
  /** pywebpush is installed on the server. */
  available: boolean;
  /** VAPID public key (base64url) the browser subscribes with. */
  public_key: string;
  subscriptions: number;
  devices: Array<{ endpoint: string; created_at: string | null; ua: string }>;
}

export interface FileInfo {
  path: string;
  name: string;
  size: number;
  modified: string;
}

export type WsMessage =
  | { kind: "hello"; state: StateSnapshot }
  | { kind: "event"; event: TimelineEvent }
  | { kind: "update"; event: TimelineEvent }
  | { kind: "stream_start"; thread: string; id: string; ts: string }
  | { kind: "delta"; thread: string; id: string; text: string }
  | { kind: "reasoning_delta"; thread: string; id: string; text: string }
  | { kind: "stream_end"; thread: string; id: string; discard?: boolean }
  | { kind: "status"; status: Status }
  | { kind: "thread"; thread: ThreadMeta }
  | { kind: "thread_deleted"; thread: string }
  | { kind: "thread_cleared"; thread: string }
  /** a synced message deleted on another device (contract C7 tombstone) */
  | { kind: "event_removed"; thread: string; id: string }
  /** another device started (`working: true`) or finished a turn on a synced chat (contract C9) */
  | ({ kind: "working"; working: boolean } & WorkingPresence)
  | { kind: "goals" }
  | { kind: "memory" }
  | { kind: "reminders" }
  | { kind: "triggers" }
  | { kind: "calendar"; calendar: CalendarData }
  | { kind: "feed_posts" }
  | { kind: "ideas"; ideas: IdeasData }
  | { kind: "profile"; profile: Profile }
  | { kind: "settings"; settings: SettingsView }
  | { kind: "phone"; phone: PhoneStatus & { gui_enabled: boolean } }
  | { kind: "hub"; hub: HubView }
  | { kind: "devices"; devices: LocalDevicesView }
  | { kind: "hands_state"; hands: HandsStatus }
  | ({ kind: "hands" } & HandsLive)
  /** the avatar studio's session, every time it changes (the studio screen watches this; the chat has its card) */
  | { kind: "studio"; current: StudioSession | null }
  | { kind: "connections"; connections: ConnectionsData }
  /** the first conversation moved on (Start, the model's block, a pick) */
  | { kind: "firstrun"; firstrun: FirstRunView }
  | { kind: "skills"; skills: SkillsData }
  | { kind: "approvals_reset" }
  | { kind: "coding"; event: CodingEvent; agent: string; session_id: string; device?: string; run: CodingRun | null }
  | { kind: "trash"; trash: TrashedThread[]; count: number }
  | { kind: "sync"; sync: SyncState }
  | { kind: "sync_progress"; peer: string; applied: number; done: boolean }
  | { kind: "paired"; name: string }
  | { kind: "chrome_state"; connected: boolean; attached: boolean; tab_id: number | null; url: string; title: string }
  | { kind: "app_update"; update: AppUpdateLiveState }
  | { kind: "error"; error: string }
  | { kind: "pong"; status: Status };

export interface MapLinks {
  google_maps: string;
  google_directions: string;
  apple_maps: string;
  waze: string;
  openstreetmap: string;
  geo: string;
}

export interface LocationPlace {
  id: string;
  name: string;
  address: string;
  lat: number | null;
  lng: number | null;
  category: string;
  tags: string[];
  context: string;
  contact_id: string;
  contact_name: string;
  visit_count: number;
  last_visited_at: string;
  favorite: boolean;
  created_at: string;
  updated_at: string;
  map_links: MapLinks;
}

export interface LocationTrip {
  id: string;
  place_id: string;
  place_name: string;
  address: string;
  lat: number | null;
  lng: number | null;
  origin_name: string;
  origin_address: string;
  started_at: string;
  ended_at: string;
  duration_min: number;
  distance_mi: number;
  avg_speed_mph: number;
  max_speed_mph: number;
  current_speed_mph: number;
  mode: string;
  traffic_level: string;
  traffic_note: string;
  tags: string[];
  notes: string;
  source: string;
  created_at: string;
  map_links: MapLinks;
}

export interface ContactLocation {
  id: string;
  name: string;
  nickname: string;
  address: string;
  phones: string[];
  emails: string[];
  org: string;
  note: string;
  source: string;
  tags: string[];
  map_links: MapLinks;
}

export interface LocationSummary {
  places: LocationPlace[];
  frequent: LocationPlace[];
  trips: LocationTrip[];
  contacts: ContactLocation[];
  stats: {
    total_trips: number;
    avg_driving_mph: number;
    max_speed_mph: number;
    latest_speed_mph: number;
    total_miles: number;
    total_places: number;
    contact_addresses: number;
    categories: Record<string, number>;
  };
  tags: string[];
}

export interface LocationTrafficEstimate {
  ok: boolean;
  error?: string;
  origin?: { label: string; address: string; lat: number; lng: number };
  destination?: { label: string; address: string; lat: number; lng: number };
  distance_mi?: number;
  freeflow_min?: number;
  estimated_min?: number;
  avg_speed_mph?: number;
  traffic_level?: string;
  traffic_note?: string;
  map_links?: MapLinks;
}
