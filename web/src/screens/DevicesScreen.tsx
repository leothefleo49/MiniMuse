import { Check, ChevronDown, ChevronUp, Download, Globe, Hand, Laptop, Loader2, MessageSquare, MoreHorizontal, Pencil, QrCode, RefreshCw, Smartphone, Square, Watch, Wifi, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { androidApp, discoverWatchAdbFromPhone, type WatchAdbDiscoveryResult } from "../android";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { NoticeDialog } from "../components/NoticeDialog";
import { SwitchModeConfirm } from "../components/SwitchModeConfirm";
import { SyncControls } from "../components/DataControls";
import { countDevices, peerDisplayName, peerHost, peerKind, uniqueDirectPeers } from "../devicePeers";
import { api, chromeExtensionZipUrl } from "../api";
import { PageBar } from "../components/BackBar";
import { CloudCard } from "../components/CloudCard";
import { MuseCaption, MuseCard, MuseDivider, MuseRow, MuseSwitchRow } from "../components/MuseList";
import { useMenuKeys } from "../components/useMenuKeys";
import { useT, type t as T } from "../i18n";
import { kindTitle, kindWord } from "../device";
import { useStore } from "../store";
import type { ChromeBridgeStatus, ComputerWindowInfo, HandsStatus, HubDevice, HubView, LocalDevice, WatchCodeInfo, WatchDevicesInfo } from "../types";
import { cx, relativeTime } from "../util";
import { cleanPairCode, isCompletePairCode, watchFailureNotice, watchPhase, type WatchNotice } from "../watchInstall";

/**
 * Devices — the account's devices as a settings page (docs/every-device.md): a line on what
 * they are for and a quiet count, then **this device** as a card (its glyph, its name — edited in
 * place —, OS and version, the online dot, what it can do as chips, the hub and remote-control
 * switches), then **the other devices** as a grid of the same cards with "Online · just now" /
 * "Last seen 2 h ago", an `@name` hint for the ones that take tasks, *Ask* and an overflow menu
 * with *Forget*. Only this device: a card on how to add one. On a computer the card for its
 * own hands follows, as before. There is no "cloud computer" section — we have none.
 */
const DOWNLOAD_URL = "https://github.com/leothefleo49/MiniMuse/releases/latest";

/** Names compare case-insensitively; a device that never reported a name must not crash the screen. */
export function sameDeviceName(a?: string | null, b?: string | null): boolean {
  return String(a ?? "").toLowerCase() === String(b ?? "").toLowerCase();
}

/** The directly-connected phones that are not already shown as one of the signed-in devices. */
export function onlyUnlistedPhones<P extends { name?: string | null }>(phones: P[], listed: Array<{ name?: string | null }>): P[] {
  return phones.filter((p) => !listed.some((d) => sameDeviceName(d.name, p.name)));
}

export function DevicesScreen() {
  const { state, dispatch, openThread, setTab, toast } = useStore();
  const t = useT();
  const hub = state.hub;
  const local = state.devices;
  const hands = state.hands;
  const [refreshing, setRefreshing] = useState(false);
  const [switchOpen, setSwitchOpen] = useState(false);

  const reload = async () => {
    try {
      const [h, hs, dv] = await Promise.all([api.hub(), api.hands(), api.devices()]);
      dispatch({ type: "ws", msg: { kind: "hub", hub: h } });
      dispatch({ type: "ws", msg: { kind: "hands_state", hands: hs } });
      dispatch({ type: "ws", msg: { kind: "devices", devices: dv } });
    } catch {
      /* offline; the live socket fills it in */
    }
    // the devices paired straight to this one are their own list: without this the page
    // said "1 device" while Data controls showed the paired phone
    try {
      const sync = await api.syncState();
      dispatch({ type: "ws", msg: { kind: "sync", sync } });
    } catch {
      /* an older runtime without sync */
    }
  };
  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const refresh = async () => {
    setRefreshing(true);
    await reload();
    setRefreshing(false);
  };

  const ask = async (d: HubDevice) => {
    try {
      const r = await api.askDevice(d.id);
      dispatch({ type: "ws", msg: { kind: "thread", thread: r.thread } });
      openThread(r.thread.id);
      setTab("chat");
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const forget = (d: HubDevice) => {
    if (!window.confirm(t("Forget {name}? It can join again by signing in.", { name: d.name }))) return;
    void api.forgetDevice(d.id).then((h) => dispatch({ type: "ws", msg: { kind: "hub", hub: h } })).catch((e: Error) => toast(e.message));
  };

  const hubOthers = (hub?.devices ?? []).filter((d) => !d.this && d.kind !== "web");
  // the phones talking to this MiniMuse directly (no account) sit with the signed-in ones
  const localPhones = onlyUnlistedPhones(local?.phones ?? [], hubOthers);
  const localIds = new Set(localPhones.map((p) => p.id));
  const others = [...hubOthers, ...localPhones];
  // pairing without an account: unified with Other devices, deduplicated against hub/local entries
  const peers = uniqueDirectPeers(state.sync, others);
  const signedIn = !!hub?.account.signed_in;
  const { total, online } = countDevices(hub, local, state.sync);
  const [syncingPeer, setSyncingPeer] = useState<string | null>(null);

  const syncNowWithPeer = async (peerKey: string) => {
    setSyncingPeer(peerKey);
    try {
      const res = await api.syncNow();
      dispatch({ type: "ws", msg: { kind: "sync", sync: res } });
      toast(
        res.applied || res.pushed
          ? t("Synced ({a} received, {p} sent).", { a: String(res.applied ?? 0), p: String(res.pushed ?? 0) })
          : t("All devices are up to date."),
      );
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSyncingPeer(null);
    }
  };

  const unpairPeer = async (identifier: string) => {
    try {
      await api.syncUnpair(identifier);
      const sync = await api.syncState().catch(() => null);
      if (sync) dispatch({ type: "ws", msg: { kind: "sync", sync } });
      toast(t("Unpaired."));
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const renameDevice = async (deviceId: string, name: string) => {
    try {
      const updated = await api.renameDevice(deviceId, name);
      dispatch({ type: "ws", msg: { kind: "devices", devices: updated } });
      toast(name.trim() ? t("Device nickname saved.") : t("Nickname cleared."));
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <PageBar
        title={t("Devices")}
        actions={
          <button type="button" onClick={() => void refresh()} aria-label={t("Refresh")} title={t("Refresh")} className="flex h-9 w-9 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg">
            <RefreshCw size={17} className={cx(refreshing && "animate-spin")} />
          </button>
        }
      />
      <div className="@container flex-1 overflow-y-auto pb-8">
        {/* the header: what devices are for, and how many there are */}
        <div className="px-5 pb-4 pt-1">
          <p className="text-[14px] leading-[20px] text-muted">{t("Every device signed in to your account is a Muse of its own, on the same conversations. Type @ and a device's name in the chat to hand it work; a computer lends its hands.")}</p>
          {total > 0 && (
            <p className="mt-1.5 text-[12.5px] font-medium uppercase tracking-wide text-muted">
              {total === 1 ? t("1 device") : t("{n} devices", { n: total })} · {t("{n} online", { n: online })}
            </p>
          )}
        </div>

        {(signedIn || state.settings?.cloud_mode === "cloud" || state.settings?.cloud_prompts) && (
          <div className="px-4 pb-3">
            <CloudCard account={hub?.account ?? null} onChange={() => void reload()} />
          </div>
        )}

        {androidApp() && (
          <div className="px-4 pb-3">
            <DeviceShell className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-[14.5px] font-semibold leading-tight">{t("Pair with your computer")}</p>
                <p className="mt-0.5 text-[12.5px] leading-snug text-muted">
                  {androidApp()?.mode?.() === "local"
                    ? t("Currently running locally on this phone. Scan your PC's QR code anytime to pair with it or switch.")
                    : t("Connected to a computer server. Scan a new QR code or switch back to local.")}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => androidApp()?.scanToPair?.()}
                  className="inline-flex h-9 items-center gap-1.5 rounded-full bg-accent px-3.5 text-[13px] font-medium text-accent-fg active:scale-[0.98]"
                >
                  <QrCode size={15} /> {t("Scan QR code")}
                </button>
                <button
                  type="button"
                  onClick={() => setSwitchOpen(true)}
                  className="inline-flex h-9 items-center gap-1.5 rounded-full border border-border px-3.5 text-[13px] font-medium hover:bg-surface-2"
                >
                  {t("Switch mode")}
                </button>
              </div>
            </DeviceShell>
            <SwitchModeConfirm open={switchOpen} onCancel={() => setSwitchOpen(false)} />
          </div>
        )}

        {hub && signedIn && (
          <div className="px-4">
            <SectionLabel>{t("This device")}</SectionLabel>
            <ThisDeviceCard hub={hub} hands={hands ?? undefined} onChange={() => void reload()} />
          </div>
        )}
        {local && (!hub || !signedIn) && (
          <div className="px-4">
            <SectionLabel>{t("This device")}</SectionLabel>
            <LocalComputerCard device={local.computer} onRename={(name) => renameDevice(local.computer.id, name)} />
          </div>
        )}

        {/* the other devices — hub, local, and directly paired peers unified into one list */}
        <div className="px-4 pt-4">
          <SectionLabel>{t("Other devices")}</SectionLabel>
          {state.syncProgress && (
            <div className="mb-3 flex items-center gap-2 rounded-2xl bg-accent/12 px-3 py-2 text-[12.5px] text-accent">
              <Loader2 size={13} className="animate-spin" />
              {t("Syncing with {peer}: {n} messages so far...", {
                peer: state.syncProgress.peer,
                n: String(state.syncProgress.applied),
              })}
            </div>
          )}
          {others.length === 0 && peers.length === 0 ? (
            <EmptyDevices hub={hub ?? undefined} signedIn={signedIn} cloudMode={state.settings?.cloud_mode} />
          ) : (
            <div className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
              {others.map((d) => (
                <DeviceCard
                  key={d.id}
                  device={d}
                  onRename={(name) => renameDevice(d.id, name)}
                  onAsk={localIds.has(d.id) ? undefined : () => void ask(d as HubDevice)}
                  onForget={localIds.has(d.id) ? undefined : () => forget(d as HubDevice)}
                />
              ))}
              {peers.map((peer) => {
                const displayName = peerDisplayName(peer);
                const host = peerHost(peer);
                const kind = peerKind(peer);
                const pKey = peer.url || peer.id;
                const isSyncing = syncingPeer === pKey;
                return (
                  <DeviceShell key={pKey}>
                    <div className="flex items-start gap-3.5">
                      <Glyph kind={kind} online={peer.online} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="flex items-center gap-1.5 truncate text-[15px] font-semibold leading-tight">
                            <OnlineDot online={peer.online} />
                            <span className="truncate">{displayName}</span>
                          </p>
                          <span className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted">
                            {kind === "phone" ? t("Phone") : t("Computer")}
                          </span>
                        </div>
                        <p className="mt-0.5 truncate font-mono text-[11.5px] text-muted">{host}</p>
                        <p className="mt-1 text-[12.5px] leading-snug text-muted">
                          {peer.last_error
                            ? t("Not syncing: {error}", { error: peer.last_error })
                            : peer.last_sync_at
                              ? t("Synced · {when}", { when: relativeTime(peer.last_sync_at) })
                              : peer.online
                                ? t("Connected · syncing directly")
                                : t("Paired · catches up automatically when online")}
                        </p>
                        <div className="mt-2.5 flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={isSyncing}
                            onClick={() => void syncNowWithPeer(pKey)}
                            className="inline-flex h-8 items-center gap-1.5 rounded-full bg-accent/12 px-3 text-[12px] font-medium text-accent hover:bg-accent/20 disabled:opacity-50"
                          >
                            <RefreshCw size={12} className={cx(isSyncing && "animate-spin")} />
                            {isSyncing ? t("Syncing…") : t("Sync now")}
                          </button>
                          <button
                            type="button"
                            onClick={() => void unpairPeer(peer.id || peer.url || peer.name)}
                            className="inline-flex h-8 items-center rounded-full border border-border px-3 text-[12px] font-medium text-muted hover:text-fg"
                          >
                            {t("Unpair")}
                          </button>
                        </div>
                      </div>
                    </div>
                  </DeviceShell>
                );
              })}
            </div>
          )}
          <p className="px-1 pt-3 text-[12.5px] leading-[18px] text-muted">{t("A chat addressed to a device runs there; you see every step here and answer its approvals.")}</p>
        </div>

        <div className="px-4 pt-4">
          <SectionLabel>{t("Pair & sync")}</SectionLabel>
          <SyncControls signedIn={signedIn} showCloudControls={false} showPairedList={false} />
        </div>

        <div className="px-4 pt-4 space-y-3">
          <SectionLabel>{t("Chrome Side Panel & Smartwatch")}</SectionLabel>
          <ChromeCompanionCard />
          <WatchPairingCard />
        </div>

        {hands && !hands.reason?.includes("phone") && local?.computer?.kind !== "phone" && (
          <div className="pt-4">
            <div className="px-4">
              <SectionLabel>{t("Hands")}</SectionLabel>
            </div>
            <HandsCard hands={hands} onChange={() => void reload()} />
          </div>
        )}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <p className="px-1 pb-2 text-[12.5px] font-medium uppercase tracking-wide text-muted">{children}</p>;
}

/** The small grey line under a switch row inside a card. */
function Note({ children }: { children: ReactNode }) {
  return <p className="px-4 pb-3 text-[12.5px] leading-[18px] text-muted">{children}</p>;
}

/** The card's shell: 16 px corners, the surface on the page background, a hairline so it holds in both themes. */
function DeviceShell({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("relative rounded-2xl border border-border/70 bg-surface p-4", className)}>{children}</div>;
}

/** The platform glyph on its tile: phone, computer, browser. */
function Glyph({ kind, online }: { kind: string; online: boolean }) {
  return (
    <span className={cx("flex h-11 w-11 shrink-0 items-center justify-center rounded-xl", online ? "bg-accent/10 text-accent" : "bg-surface-2 text-muted")} aria-hidden>
      {kindIcon(kind, 22)}
    </span>
  );
}

function OnlineDot({ online }: { online: boolean }) {
  return <span aria-hidden className={cx("inline-block h-2 w-2 shrink-0 rounded-full", online ? "bg-[#2fb344]" : "bg-muted/35")} />;
}

/** The relay's `last_seen` is Unix seconds (a number, or the digits as a string); older rows carried an ISO time. */
function seenAt(raw: string | number | undefined): string {
  if (raw === undefined || raw === null || raw === "") return "";
  const n = typeof raw === "number" ? raw : /^\d+(\.\d+)?$/.test(raw) ? Number(raw) : NaN;
  return Number.isFinite(n) ? new Date(n * 1000).toISOString() : String(raw);
}

/** "Online · just now" / "Last seen 2 h ago" / "Offline". An online device's `last_seen` is its last hello, which may be hours old: it is shown only while fresh. */
function presence(d: Pick<HubDevice, "online" | "last_seen">, t: typeof T): string {
  const iso = seenAt(d.last_seen);
  const when = relativeTime(iso);
  if (d.online) return iso && Date.now() - new Date(iso).getTime() < 120_000 ? `${t("Online")} · ${t("just now")}` : t("Online");
  return when ? t("Last seen {when}", { when }) : t("Offline");
}

/** What a device can do, from the hub actions it announces — our names, in a fixed order. */
export function capabilities(actions: readonly string[] | undefined, t: (s: string) => string, extra: string[] = []): string[] {
  const set = new Set(actions ?? []);
  const out: string[] = [...extra];
  if (set.has("task")) out.push(t("Tasks"));
  if (set.has("shell")) out.push(t("Commands"));
  if (set.has("files") || set.has("file.get") || set.has("file.put")) out.push(t("Files"));
  if (set.has("screen")) out.push(t("Screenshot"));
  if (set.has("open")) out.push(t("Open links"));
  if ([...set].some((a) => a.startsWith("coding."))) out.push(t("Coding agents"));
  if (set.has("notify")) out.push(t("Notifications"));
  return out;
}

function Chips({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((c) => (
        <span key={c} className="rounded-full bg-surface-2 px-2.5 py-1 text-[12px] font-medium text-fg/80">
          {c}
        </span>
      ))}
    </div>
  );
}

/** The device line under the name: OS · version. */
function osLine(d: { os?: string; version?: string }): string {
  return [d.os || "", d.version || ""].filter(Boolean).join(" · ");
}

function ThisDeviceCard({ hub, hands, onChange }: { hub: HubView; hands?: HandsStatus; onChange: () => void }) {
  const { dispatch, toast } = useStore();
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(hub.device.name);
  const [busy, setBusy] = useState(false);
  const signedIn = hub.account.signed_in;
  const connected = hub.state === "connected";
  // the relay's own row for this device carries the OS and the version
  const self = hub.devices.find((d) => d.this);

  const update = async (body: { enabled?: boolean; remote_control?: boolean; name?: string }) => {
    setBusy(true);
    try {
      const h = await api.updateHub(body);
      dispatch({ type: "ws", msg: { kind: "hub", hub: h } });
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const status = !signedIn
    ? t("Signed out")
    : !hub.enabled
      ? t("Off")
      : connected
        ? t("Online")
        : hub.state === "refused"
          ? t("Refused")
          : t("Connecting…");
  const can = capabilities(hub.device.actions, t, hands?.enabled && hands.available ? [t("Hands")] : []);

  return (
    <DeviceShell>
      <div className="flex items-start gap-3.5">
        <Glyph kind={hub.device.kind} online={connected} />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (!draft.trim()) return;
                void update({ name: draft.trim() }).then(() => setEditing(false));
              }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={60}
                autoFocus
                aria-label={t("This device's name")}
                placeholder={t("A name your other devices will see")}
                className="h-9 min-w-0 flex-1 rounded-xl bg-surface-2 px-3 text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button type="submit" disabled={busy || !draft.trim()} aria-label={t("Save")} className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-fg disabled:opacity-40">
                <Check size={16} />
              </button>
              <button type="button" onClick={() => setEditing(false)} aria-label={t("Cancel")} className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-muted">
                <X size={16} />
              </button>
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <span className="truncate text-[16px] font-semibold leading-[22px]">{hub.device.name}</span>
              <button
                type="button"
                onClick={() => {
                  setDraft(hub.device.name);
                  setEditing(true);
                }}
                aria-label={t("Rename this device")}
                title={t("Rename this device")}
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
              >
                <Pencil size={13} />
              </button>
            </div>
          )}
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] leading-[18px] text-muted">
            <OnlineDot online={connected} />
            <span className="truncate">{[status, kindWord(hub.device.kind, t), osLine(self ?? {})].filter(Boolean).join(" · ")}</span>
          </p>
          {can.length > 0 && (
            <div className="mt-3">
              <Chips items={can} />
            </div>
          )}
        </div>
      </div>

      <div className="-mx-4 -mb-2 mt-4 border-t border-border/60">
        <MuseSwitchRow label={t("Join the hub")} checked={hub.enabled && signedIn} disabled={!signedIn || busy} onChange={(v) => void update({ enabled: v })} />
        <Note>{signedIn ? t("Reachable by your other devices while this runs.") : t("Sign in first.")}</Note>
        <MuseDivider inset={16} />
        <MuseSwitchRow label={t("My other devices may operate it")} checked={hub.remote_control} disabled={busy} onChange={(v) => void update({ remote_control: v })} />
        <Note>{t("They can run commands, read files and see its screen here; each step still goes through the Sentinel.")}</Note>
      </div>
      {(hub.state === "refused" || hub.state === "disconnected") && hub.detail && (
        <div className="mt-3 rounded-xl bg-amber-500/12 px-3 py-2 text-[12.5px] text-amber-700 dark:text-amber-300">{hub.detail}</div>
      )}
    </DeviceShell>
  );
}

/** The other devices on this MiniMuse itself (no account): the phone talking to it directly. */
function LocalComputerCard({ device, onRename }: { device: LocalDevice; onRename?: (name: string) => Promise<void> | void }) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(device.name || t("This computer"));
  const [busy, setBusy] = useState(false);
  const can = capabilities(device.actions, t);
  // the nickname or the type is the name on screen; the machine's own name lives only in
  // the rename box, where it is needed
  const displayName = device.nickname || kindTitle(device.kind, t);
  return (
    <DeviceShell>
      <div className="flex items-start gap-3.5">
        <Glyph kind={device.kind} online />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                void Promise.resolve(onRename?.(draft.trim())).finally(() => {
                  setBusy(false);
                  setEditing(false);
                });
              }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={60}
                autoFocus
                aria-label={t("Nickname")}
                placeholder={device.model || t("This computer")}
                className="h-9 min-w-0 flex-1 rounded-xl bg-surface-2 px-3 text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button type="submit" disabled={busy} aria-label={t("Save")} className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-fg disabled:opacity-40">
                <Check size={16} />
              </button>
              <button type="button" onClick={() => setEditing(false)} aria-label={t("Cancel")} className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-muted">
                <X size={16} />
              </button>
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <p className="truncate text-[16px] font-semibold leading-[22px]">{displayName}</p>
              {onRename && (
                <button
                  type="button"
                  onClick={() => {
                    setDraft(device.nickname || "");
                    setEditing(true);
                  }}
                  aria-label={t("Rename this device")}
                  title={t("Set a nickname")}
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
                >
                  <Pencil size={13} />
                </button>
              )}
            </div>
          )}
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] leading-[18px] text-muted">
            <OnlineDot online />
            <span className="truncate">{[t("On this MiniMuse"), kindWord(device.kind, t), osLine(device)].filter(Boolean).join(" · ")}</span>
          </p>
        </div>
      </div>
      {can.length > 0 && (
        <div className="mt-3">
          <Chips items={can} />
        </div>
      )}
    </DeviceShell>
  );
}

/** One of the other devices: the same anatomy, *Ask* when it is online, *Forget* behind the dots, plus inline nickname editing. */
function DeviceCard({
  device: d,
  onRename,
  onAsk,
  onForget,
}: {
  device: HubDevice | LocalDevice;
  onRename?: (name: string) => Promise<void> | void;
  onAsk?: () => void;
  onForget?: () => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const localDev = d as LocalDevice;
  const [draft, setDraft] = useState(localDev.nickname || d.name);
  const [busy, setBusy] = useState(false);
  const can = capabilities(d.actions, t);
  const takesTasks = d.online && (d.actions ?? []).includes("task");
  // a local device's name on screen is its nickname or its type; the machine's own
  // name lives only in the rename box. A hub device's name is the account's name.
  const displayName = "nickname" in d ? localDev.nickname || kindTitle(d.kind, t) : d.name;
  return (
    <DeviceShell className="flex flex-col gap-3">
      <div className="flex items-start gap-3.5">
        <Glyph kind={d.kind} online={d.online} />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                setBusy(true);
                void Promise.resolve(onRename?.(draft.trim())).finally(() => {
                  setBusy(false);
                  setEditing(false);
                });
              }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={60}
                autoFocus
                aria-label={t("Nickname")}
                placeholder={localDev.model || d.name}
                className="h-9 min-w-0 flex-1 rounded-xl bg-surface-2 px-3 text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button type="submit" disabled={busy} aria-label={t("Save")} className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-fg disabled:opacity-40">
                <Check size={16} />
              </button>
              <button type="button" onClick={() => setEditing(false)} aria-label={t("Cancel")} className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-muted">
                <X size={16} />
              </button>
            </form>
          ) : (
            <div className="flex items-center gap-1.5">
              <p className="truncate text-[16px] font-semibold leading-[22px]">{displayName}</p>
              {onRename && (
                <button
                  type="button"
                  onClick={() => {
                    setDraft(localDev.nickname || ("nickname" in d ? "" : d.name));
                    setEditing(true);
                  }}
                  aria-label={t("Rename this device")}
                  title={t("Set a nickname")}
                  className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
                >
                  <Pencil size={13} />
                </button>
              )}
            </div>
          )}
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] leading-[18px] text-muted">
            <OnlineDot online={d.online} />
            <span className="truncate">{[presence(d, t), kindWord(d.kind, t), osLine(d)].filter(Boolean).join(" · ")}</span>
          </p>
        </div>
        {onForget && <OverflowMenu label={t("More about {name}", { name: d.name })} items={[{ label: t("Forget this device"), onClick: onForget, danger: true }]} />}
      </div>
      <Chips items={can} />
      {takesTasks && <p className="text-[12.5px] leading-[18px] text-muted">{t("Type @{name} in the chat to hand it work.", { name: d.name })}</p>}
      {d.online && onAsk && (
        <button type="button" onClick={onAsk} className="flex h-9 w-fit items-center gap-1.5 rounded-full bg-accent/10 px-3.5 text-[13.5px] font-medium text-accent hover:bg-accent/15">
          <MessageSquare size={14} /> {t("Ask")}
        </button>
      )}
    </DeviceShell>
  );
}

/** The dots at a card's corner and the small menu under them. */
function OverflowMenu({ label, items }: { label: string; items: Array<{ label: string; onClick: () => void; danger?: boolean }> }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useMenuKeys(ref, open, () => setOpen(false));
  return (
    <div ref={ref} className="relative -mr-1.5 -mt-1">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-label={label} aria-haspopup="menu" aria-expanded={open} className="flex h-8 w-8 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg">
        <MoreHorizontal size={18} />
      </button>
      {open && (
        <ul role="menu" className="absolute right-0 top-9 z-30 min-w-[180px] overflow-hidden rounded-2xl border border-border bg-surface py-1 shadow-xl">
          {items.map((it) => (
            <li key={it.label}>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  it.onClick();
                }}
                className={cx("w-full px-4 py-2.5 text-left text-[14px] hover:bg-surface-2", it.danger && "text-red-600 dark:text-red-400")}
              >
                {it.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Only this device: how to add one; signed out or off the hub, why nothing is listed. */
function EmptyDevices({ hub, signedIn, cloudMode }: { hub: HubView | undefined; signedIn: boolean; cloudMode?: string }) {
  const t = useT();
  const ready = signedIn && hub?.state === "connected";
  const inAndroid = !!androidApp();
  return (
    <DeviceShell className="flex flex-col gap-3">
      <div className="flex items-start gap-3.5">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-surface-2 text-muted" aria-hidden>
          <Smartphone size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold leading-[20px]">{ready ? t("Add a device") : t("No other device yet")}</p>
          <p className="mt-1 text-[13px] leading-[18px] text-muted">
            {!signedIn
              ? inAndroid
                ? t("Only this phone is active. Scan a PC's QR code above to pair with your computer.")
                : cloudMode === "local"
                  ? t("MiniMuse Cloud is turned off. Phones paired directly over your local network will appear here.")
                  : t("No other devices paired yet. Connect on your local network or turn on MiniMuse Cloud in Settings.")
              : !ready
                ? hubStateLabel(hub?.state ?? "", t)
                : hub?.account.hint
                  ? t("Install MiniMuse on your phone, Mac or PC and sign in as {hint}; it appears here on its own.", { hint: hub.account.hint })
                  : t("Install MiniMuse on your phone, Mac or PC and sign in with the same account; it appears here on its own.")}
          </p>
        </div>
      </div>
      {ready && (
        <a href={DOWNLOAD_URL} target="_blank" rel="noopener noreferrer" className="flex h-9 w-fit items-center gap-1.5 rounded-full bg-accent/10 px-3.5 text-[13.5px] font-medium text-accent hover:bg-accent/15">
          {t("Get the apps")}
        </a>
      )}
    </DeviceShell>
  );
}

function HandsCard({ hands, onChange }: { hands: HandsStatus; onChange: () => void }) {
  const { dispatch, toast, state } = useStore();
  const t = useT();
  const [busy, setBusy] = useState(false);
  const museName = state.profile?.name ?? "MiniMuse";

  const [confirmHands, setConfirmHands] = useState(false);

  const set = async (body: { enabled?: boolean; backend?: string; mode?: string }) => {
    setBusy(true);
    try {
      const h = await api.setHands(body);
      dispatch({ type: "ws", msg: { kind: "hands_state", hands: h } });
      onChange();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const status = hands.task_active ? t("Working") : hands.enabled ? (hands.available ? t("On") : t("Unavailable")) : t("Off");

  return (
    <>
      <ConfirmDialog
        open={confirmHands}
        title={t("Turn on Hands on this computer?")}
        body={t("This lets MiniMuse take screenshots and control the mouse and keyboard on this machine. Turning it on may also open operating system Accessibility or Screen Recording permission popups.")}
        confirmLabel={t("Turn on Hands")}
        onConfirm={() => {
          setConfirmHands(false);
          void set({ enabled: true });
        }}
        onCancel={() => setConfirmHands(false)}
      />
      <MuseCard className="border border-border/70">
        <MuseSwitchRow
          icon={<Hand size={22} />}
          label={t("Hands on this computer")}
          value={status}
          checked={hands.enabled}
          disabled={busy}
          onChange={(v) => {
            if (v) setConfirmHands(true);
            else void set({ enabled: false });
          }}
        />
        <MuseCaption className="pb-3 pt-0">
          {hands.task_active
            ? hands.task_text || t("On the screen now")
            : hands.available
              ? t("It looks at a screenshot and clicks by position; paying, sending and deleting ask you first.")
              : hands.reason || t("Nothing can drive this screen yet.")}
        </MuseCaption>
        <MuseDivider inset={16} />
        <div className="flex items-center gap-2 px-4 py-3 text-[13px] text-muted">
          <span>{t("Driver")}:</span>
          {(["auto", "desktop", "pyautogui", "xdotool"] as const).map((b) => (
            <button
              key={b}
              type="button"
              disabled={busy}
              aria-pressed={(hands.backend ?? "auto") === b}
              onClick={() => void set({ backend: b })}
              className={cx("rounded-full px-2.5 py-1", (hands.backend ?? "auto") === b ? "bg-fg text-bg" : "bg-surface-2 text-fg")}
            >
              {b === "auto" ? t("Auto") : b === "desktop" ? t("Desktop") : b}
            </button>
          ))}
        </div>
        {(hands.device?.platform === "darwin" || hands.window?.available) && (
          <>
            <MuseDivider inset={16} />
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 text-[13px] text-muted">
              <span>{t("Where")}:</span>
              {(["auto", "window", "screen"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={busy}
                  aria-pressed={(hands.mode ?? "auto") === m}
                  onClick={() => void set({ mode: m })}
                  className={cx("rounded-full px-2.5 py-1", (hands.mode ?? "auto") === m ? "bg-fg text-bg" : "bg-surface-2 text-fg")}
                >
                  {m === "auto" ? t("Auto") : m === "window" ? t("One window") : t("Whole screen")}
                </button>
              ))}
            </div>
            <MuseCaption className="pb-3 pt-0">
              {hands.window?.active && hands.window.app
                ? t("Working in {app}'s window; the mouse stays yours.", { app: hands.window.app })
                : hands.window?.reason && (hands.mode ?? "auto") !== "screen"
                  ? t("On the whole screen for now: {reason}", { reason: hands.window.reason })
                  : t("One window: the hands work inside the app they were given, send clicks and keys to it alone and leave your mouse alone. Whole screen: the system mouse, like other platforms. Auto picks one window as soon as an app is named.")}
            </MuseCaption>
            <BackgroundWindowPicker currentApp={hands.window?.app || ""} onPicked={() => void onChange()} />
          </>
        )}
        {hands.task_active && (
          <>
            <MuseDivider inset={16} />
            <MuseRow icon={<Square size={18} fill="currentColor" />} label={t("Stop the hands")} onClick={() => void api.stopHands().catch((e: Error) => toast(e.message))} chevron={false} />
          </>
        )}
      </MuseCard>
      {hands.enabled && hands.device?.platform === "darwin" && (
        <MuseCaption>{t("On a Mac, allow MiniMuse under System Settings → Privacy & Security → Accessibility and Screen Recording when macOS asks; without them clicks do nothing and the screenshot is black. The first action in each app asks you once (\"Let {name} use Safari?\") and the answer is kept under Permissions.", { name: museName })}</MuseCaption>
      )}
    </>
  );
}

function ChromeCompanionCard() {
  const t = useT();
  const { toast } = useStore();
  const [st, setSt] = useState<ChromeBridgeStatus | null>(null);

  const load = async () => {
    try {
      setSt(await api.chromeStatus());
    } catch {
      /* ignore */
    }
  };
  useEffect(() => {
    void load();
  }, []);

  const detach = async () => {
    try {
      setSt(await api.chromeDetach());
      toast(t("Chrome background tab detached."));
    } catch (e) {
      toast((e as Error).message);
    }
  };

  return (
    <DeviceShell className="space-y-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 rounded-2xl bg-surface-2 p-2.5 text-fg">
            <Globe size={19} />
          </div>
          <div>
            <div className="text-[15px] font-semibold">{t("Chrome Side Panel & Background Tab")}</div>
            <p className="text-[12.5px] text-muted leading-snug mt-0.5">
              {st?.attached
                ? t("Sharing \"{title}\" safely in the background via Chrome Debugger — you can keep browsing other tabs.", { title: st.title || st.url || "Chrome tab" })
                : st?.connected
                  ? t("Chrome extension connected. Click Share Tab in Chrome's Side Panel whenever you want MiniMuse to drive a tab cleanly without other extensions interfering.")
                  : t("Install the unpacked MiniMuse Companion extension in chrome://extensions to use MiniMuse in Chrome's side panel and isolate background tab control.")}
            </p>
          </div>
        </div>
        <span className={cx("shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-semibold", st?.attached ? "bg-accent/15 text-accent" : st?.connected ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" : "bg-surface-2 text-muted")}>
          {st?.attached ? t("Sharing Tab") : st?.connected ? t("Connected") : t("Not installed")}
        </span>
      </div>
      <div className="text-[11.5px] text-muted rounded-xl bg-surface-2/60 px-2.5 py-1.5">
        {t("Private unpacked extension (not published to the public Chrome Web Store). Extract the zip, open chrome://extensions, enable Developer mode, and click Load unpacked.")}
      </div>
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <a
          href={chromeExtensionZipUrl()}
          download="MiniMuse-Chrome-Extension.zip"
          className="inline-flex items-center gap-1.5 rounded-full bg-fg px-3.5 py-1.5 text-[12.5px] font-semibold text-bg hover:opacity-90"
        >
          <Download size={13} /> {t("Download Chrome Extension (.zip)")}
        </a>
        {st?.attached && (
          <button
            type="button"
            onClick={() => void detach()}
            className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-3.5 py-1.5 text-[12.5px] font-medium"
          >
            {t("Stop Sharing Tab")}
          </button>
        )}
        <button
          type="button"
          onClick={() => void load()}
          className="rounded-full px-3 py-1.5 text-[12px] text-muted hover:bg-surface-2 hover:text-fg"
        >
          {t("Refresh")}
        </button>
      </div>
    </DeviceShell>
  );
}

export function WatchPairingCard() {
  const t = useT();
  const { toast } = useStore();
  const [info, setInfo] = useState<WatchCodeInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [adbInfo, setAdbInfo] = useState<WatchDevicesInfo | null>(null);
  const [phoneMdns, setPhoneMdns] = useState<WatchAdbDiscoveryResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [watchIpPort, setWatchIpPort] = useState("");
  const [pairIpPort, setPairIpPort] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [notice, setNotice] = useState<WatchNotice | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const installingRef = useRef(false);
  const autoTried = useRef<Set<string>>(new Set());
  const codeRef = useRef<HTMLInputElement>(null);

  const mint = async () => {
    setBusy(true);
    try {
      setInfo(await api.watchCode());
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const scanAdb = async () => {
    setScanning(true);
    try {
      const [srv, localNsd] = await Promise.all([
        api.watchDevices(),
        Promise.resolve().then(() => discoverWatchAdbFromPhone()),
      ]);
      setAdbInfo(srv);
      if (localNsd) setPhoneMdns(localNsd);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setScanning(false);
    }
  };

  const pushToWatch = async (target?: string, opts: { code?: string } = {}) => {
    if (installingRef.current) return;
    installingRef.current = true;
    setInstalling(true);
    setStatusMsg(t("Installing on your watch…"));
    try {
      const code = cleanPairCode(opts.code ?? pairCode);
      const res = await api.watchInstall({
        target: target || watchIpPort.trim() || undefined,
        connect_host_port: watchIpPort.trim() || undefined,
        pair_host_port: pairIpPort.trim() || undefined,
        pair_code: isCompletePairCode(code) ? code : undefined,
        auto_pair_watch: true,
        grant_permissions: true,
      });
      if (res.ok) {
        const msg = res.auto_paired
          ? t("Watch app automatically downloaded, installed, granted permissions, and paired with {url}!", { url: res.best_url || "MiniMuse" })
          : t("Wear app automatically downloaded and installed onto {dev}!", { dev: res.target || "watch" });
        setStatusMsg(msg);
        setNotice(null);
        setPairCode("");
        toast(msg);
      } else {
        setStatusMsg(res.error || t("Could not install watch APK"));
        setNotice(watchFailureNotice(res, t));
        if (res.code === "pair_failed") setPairCode("");
      }
      void scanAdb();
    } catch (e) {
      const err = (e as Error).message;
      setStatusMsg(err);
      setNotice({ tone: "error", title: t("The install did not finish"), body: err, steps: [], action: "retry" });
    } finally {
      installingRef.current = false;
      setInstalling(false);
    }
  };

  useEffect(() => {
    void scanAdb();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // keep looking while the card is on screen, so a watch is noticed the moment it appears
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.hidden || installingRef.current) return;
      void api.watchDevices().then(setAdbInfo).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(id);
  }, []);

  const phase = watchPhase(adbInfo);

  // a watch is connected and nothing was ever installed on it: do it now, no tap needed
  useEffect(() => {
    if (phase.phase !== "ready" || !phase.watchId) return;
    const dev = adbInfo?.devices.find((d) => d.id === phase.watchId);
    const known = !!dev?.serial && dev.serial === adbInfo?.auto?.saved?.serial;
    if (known || autoTried.current.has(phase.watchId)) return;
    autoTried.current.add(phase.watchId);
    void pushToWatch(phase.watchId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase.phase, phase.watchId]);

  const onCodeChange = (raw: string) => {
    const code = cleanPairCode(raw);
    setPairCode(code);
    // six digits typed: pair, connect and install straight away
    if (code.length === 6 && phase.phase === "pairing") void pushToWatch(undefined, { code });
  };

  const phaseText = (() => {
    switch (phase.phase) {
      case "ready":
        return t("Watch found: {dev}", { dev: phase.watchName ?? "" });
      case "pairing":
        return t("Your watch is showing its pairing screen. Type the 6-digit code it shows.");
      case "phone_only":
        return t("Only a phone is connected. Turn on Wireless debugging on your watch.");
      case "none":
        return t("No watch found yet. MiniMuse keeps looking.");
      default:
        return t("Looking for your watch…");
    }
  })();

  const noticeAction = () => {
    const n = notice;
    setNotice(null);
    if (!n) return;
    if (n.action === "download") window.location.href = "/api/watch/apk";
    else if (n.action === "code") window.setTimeout(() => codeRef.current?.focus(), 50);
    else void pushToWatch(phase.watchId);
  };

  const readyDevices = adbInfo?.devices.filter((d) => d.state === "device") ?? [];
  const discoveredConnect = Array.from(
    new Map(
      [...(adbInfo?.mdns_connect ?? []), ...(phoneMdns?.connect ?? [])].map((ep) => [ep.host_port, ep]),
    ).values(),
  );
  const discoveredPairing = Array.from(
    new Map(
      [...(adbInfo?.mdns_pairing ?? []), ...(phoneMdns?.pairing ?? [])].map((ep) => [ep.host_port, ep]),
    ).values(),
  );

  return (
    <DeviceShell className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 rounded-2xl bg-surface-2 p-2.5 text-fg">
            <Watch size={19} />
          </div>
          <div>
            <div className="text-[15px] font-semibold">{t("Smartwatch App (Wear OS & Wrist Companion)")}</div>
            <p className="text-[12.5px] text-muted leading-snug mt-0.5">
              {t("Downloads and sideloads the Wear OS app directly onto your smartwatch (never your phone), grants watch permissions, and pairs it automatically.")}
            </p>
          </div>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void mint()}
          className="shrink-0 rounded-full border border-border bg-surface-2 px-3.5 py-1.5 text-[12.5px] font-semibold text-fg hover:bg-surface-2/80 disabled:opacity-60"
        >
          {info ? t("New Code") : t("6-Digit Code")}
        </button>
      </div>

      {/* Auto-detect and install: it finds the watch itself; the only thing a person may type is the 6 digits */}
      <div className="rounded-2xl border border-accent/30 bg-accent/5 p-3.5 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[13px] font-semibold text-fg">{t("One-Click Watch Auto-Download & Install")}</div>
          {phase.phase === "ready" ? (
            <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400">
              {t("Watch Ready: {dev}", { dev: phase.watchName ?? "" })}
            </span>
          ) : phase.phase === "pairing" ? (
            <span className="rounded-full bg-accent/15 px-2.5 py-0.5 text-[11px] font-semibold text-accent">{t("Waiting for the code")}</span>
          ) : phase.phase === "phone_only" ? (
            <span className="rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[11px] font-semibold text-amber-700 dark:text-amber-300">
              {t("Only phone ADB connected (enable Wi-Fi debugging on watch)")}
            </span>
          ) : null}
        </div>
        <p className="flex items-center gap-2 text-[12.5px] leading-relaxed text-fg" aria-live="polite">
          {(scanning || installing || phase.phase === "searching") && <Loader2 size={13} className="shrink-0 animate-spin text-muted" />}
          <span>{installing ? t("Installing on your watch…") : phaseText}</span>
        </p>

        {phase.phase === "pairing" && (
          <input
            ref={codeRef}
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={7}
            value={pairCode}
            onChange={(e) => onCodeChange(e.target.value)}
            disabled={installing}
            placeholder={t("6-digit code from the watch")}
            aria-label={t("6-digit code from the watch")}
            className="w-full rounded-xl border border-accent/60 bg-bg px-3 py-2.5 text-center font-mono text-[20px] tracking-[0.35em] outline-none focus:border-accent disabled:opacity-60"
          />
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <button
            type="button"
            disabled={installing}
            onClick={() => void pushToWatch(phase.watchId)}
            className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-[13px] font-semibold text-accent-fg shadow-sm hover:opacity-95 disabled:opacity-60"
          >
            {installing ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            {installing ? t("Downloading & Installing to Watch…") : t("Auto-Download & Install on Watch")}
          </button>

          <button
            type="button"
            disabled={scanning}
            onClick={() => void scanAdb()}
            className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-3 py-1.5 text-[12px] text-muted hover:text-fg"
          >
            {scanning ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {t("Detect Watch")}
          </button>
        </div>

        {readyDevices.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {readyDevices.map((d) => {
              const isPhone = d.is_watch === false;
              const isWatch = d.is_watch === true;
              return isPhone ? (
                <span
                  key={d.id}
                  className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-surface/70 px-3 py-1 text-[11.5px] text-muted"
                >
                  <Smartphone size={11} /> {t("{dev} (Phone · skipped)", { dev: d.model || d.id })}
                </span>
              ) : (
                <button
                  key={d.id}
                  type="button"
                  disabled={installing}
                  onClick={() => void pushToWatch(d.id)}
                  className={cx(
                    "inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11.5px] font-medium transition",
                    isWatch ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 hover:bg-accent hover:text-accent-fg" : "bg-surface-2 text-fg hover:bg-accent hover:text-accent-fg",
                  )}
                >
                  <Watch size={11} /> {t("Install to {dev}", { dev: d.model || d.id })}
                </button>
              );
            })}
          </div>
        )}

        <button
          type="button"
          onClick={() => setAdvancedOpen(!advancedOpen)}
          className="inline-flex items-center gap-1 text-[11.5px] font-medium text-muted hover:text-fg"
        >
          {advancedOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {t("Advanced: type the address yourself")}
        </button>
        {advancedOpen && (
          <div className="space-y-2">
            {(discoveredConnect.length > 0 || discoveredPairing.length > 0) && (
              <div className="flex flex-wrap items-center gap-1.5">
                {discoveredConnect.map((ep) => (
                  <button
                    key={`c-${ep.host_port}`}
                    type="button"
                    onClick={() => setWatchIpPort(ep.host_port)}
                    className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-bg px-2.5 py-1 text-[11.5px] font-medium text-accent hover:bg-accent/10"
                  >
                    <Wifi size={11} /> {t("Use Wi-Fi ADB: {hp}", { hp: ep.host_port })}
                  </button>
                ))}
                {discoveredPairing.map((ep) => (
                  <button
                    key={`p-${ep.host_port}`}
                    type="button"
                    onClick={() => setPairIpPort(ep.host_port)}
                    className="inline-flex items-center gap-1 rounded-full border border-border bg-bg px-2.5 py-1 text-[11.5px] font-medium text-fg hover:bg-surface-2"
                  >
                    <Wifi size={11} /> {t("Use Pair IP: {hp}", { hp: ep.host_port })}
                  </button>
                ))}
              </div>
            )}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <input
                type="text"
                value={watchIpPort}
                onChange={(e) => setWatchIpPort(e.target.value)}
                placeholder={t("Watch IP:Port (optional if USB/connected)")}
                className="rounded-xl border border-border/80 bg-bg px-3 py-1.5 text-[12.5px] outline-none focus:border-accent"
              />
              <input
                type="text"
                value={pairIpPort}
                onChange={(e) => setPairIpPort(e.target.value)}
                placeholder={t("Pair IP:Port (first-time Wi-Fi)")}
                className="rounded-xl border border-border/80 bg-bg px-3 py-1.5 text-[12.5px] outline-none focus:border-accent"
              />
              <input
                type="text"
                value={pairCode}
                onChange={(e) => setPairCode(cleanPairCode(e.target.value))}
                placeholder={t("Wi-Fi 6-digit Pair Code (optional)")}
                className="rounded-xl border border-border/80 bg-bg px-3 py-1.5 text-[12.5px] outline-none focus:border-accent"
              />
            </div>
          </div>
        )}

        {statusMsg && (
          <div className="rounded-xl bg-bg/90 px-3 py-2 text-[12px] text-fg border border-border/60">
            {statusMsg}
          </div>
        )}
      </div>

      <NoticeDialog
        open={!!notice}
        tone={notice?.tone ?? "warning"}
        title={notice?.title ?? ""}
        body={notice?.body}
        steps={notice?.steps}
        primaryLabel={
          notice?.action === "download" ? t("Download the watch app") : notice?.action === "code" ? t("Enter code") : t("Try again")
        }
        onPrimary={noticeAction}
        onClose={() => setNotice(null)}
      />

      {info && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border/70 bg-bg px-3.5 py-2.5">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-wide text-muted">{t("6-Digit Watch Code (valid 10 min)")}</div>
            <div className="mt-0.5 font-mono text-[22px] font-bold tracking-[0.2em] text-accent">{info.code}</div>
          </div>
          <div className="text-right text-[12px] text-muted">
            <div>{t("Server on watch:")}</div>
            <div className="font-mono text-fg/90">{info.best_url}</div>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pt-0.5">
        <button
          type="button"
          onClick={() => setGuideOpen(!guideOpen)}
          className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2/70 px-3 py-1 text-[12px] font-medium hover:bg-surface-2"
        >
          {guideOpen ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {t("Watch Wi-Fi Setup & Manual APK Sideload")}
        </button>
        <a
          href="/api/watch/apk"
          download="MiniMuse-wear.apk"
          className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12px] font-medium text-accent hover:bg-accent/10"
        >
          <Download size={12} /> {t("Download Watch APK (.apk)")}
        </a>
      </div>
      {guideOpen && (
        <div className="rounded-2xl border border-border/70 bg-bg/70 p-3 space-y-2 text-[12px] leading-relaxed">
          <div>
            <div className="font-semibold text-fg">{t("Quick 1-Minute Watch Setup (First Time Only)")}</div>
            <ol className="list-decimal list-inside text-muted space-y-0.5 mt-1">
              <li>{t("On your watch: open Settings → About watch → Software info → tap Build number 7 times to unlock Developer options.")}</li>
              <li>{t("Open Settings → Developer options → turn on ADB debugging and Wireless debugging.")}</li>
              <li>{t("Tap 'Pair new device' in Wireless debugging on your watch, type the Pair IP:Port, 6-digit code, and Watch IP:Port above, and tap 'Auto-Download & Install on Watch'.")}</li>
              <li>{t("MiniMuse will automatically download the watch APK, push it to your watch, grant microphone & notification permissions, and open the watch app already paired!")}</li>
              <li>{t("Prefer sideloading with a phone-to-watch tool like Wear Installer 2 or Bugjaeger? Use the Download Watch APK (.apk) link above (Wear OS APKs do not run on phones directly).")}</li>
            </ol>
          </div>
          {adbInfo && (
            <div className="text-[11px] text-muted pt-1 border-t border-border/50">
              {adbInfo.devices.length === 0
                ? t("No watch currently connected via ADB. Enter your watch's Wireless Debugging IP:Port above or plug in USB.")
                : `${t("Connected ADB devices:")} ${adbInfo.devices.map((d) => `${d.model || d.id} (${d.state})`).join(", ")}`}
            </div>
          )}
        </div>
      )}
    </DeviceShell>
  );
}

/**
 * The runtime's window list comes in two shapes: the macOS/Windows adapters write
 * `{ id, app, bundle_id, title }`, older builds wrote `{ window_id, owner, app_id, title }`.
 * Normalise both so a missing field can never crash the Devices screen.
 */
export function normalizeWindows(raw: unknown): ComputerWindowInfo[] {
  if (!Array.isArray(raw)) return [];
  const out: ComputerWindowInfo[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const w = item as Record<string, unknown>;
    const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
    const owner = str(w.owner) || str(w.app) || str(w.app_name) || str(w.bundle_id);
    if (!owner) continue;
    out.push({
      window_id: Number(w.window_id ?? w.id ?? 0) || 0,
      owner,
      app_id: str(w.app_id) || str(w.bundle_id) || owner,
      title: str(w.title),
    });
  }
  return out;
}

function BackgroundWindowPicker({ currentApp, onPicked }: { currentApp: string; onPicked: () => void }) {
  const t = useT();
  const { toast } = useStore();
  const [windows, setWindows] = useState<ComputerWindowInfo[]>([]);
  const [loading, setLoading] = useState(false);

  const loadWindows = async () => {
    setLoading(true);
    try {
      const res = await api.computerWindows();
      setWindows(normalizeWindows(res.windows));
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadWindows();
  }, []);

  const pick = async (owner: string, title = "") => {
    try {
      await api.computerTarget(owner, title);
      toast(owner ? t("Hands pinned to {app} in background.", { app: owner }) : t("Cleared background window pin."));
      onPicked();
    } catch (e) {
      toast((e as Error).message);
    }
  };

  if (windows.length === 0 && !currentApp) return null;

  return (
    <div className="border-t border-border/70 px-4 py-3 space-y-2">
      <div className="flex items-center justify-between text-[12.5px]">
        <span className="font-medium text-muted">{t("Pin a background window (never steals focus):")}</span>
        <div className="flex items-center gap-2">
          {currentApp && (
            <button type="button" onClick={() => void pick("", "")} className="text-accent hover:underline">
              {t("Clear ({app})", { app: currentApp })}
            </button>
          )}
          <button type="button" onClick={() => void loadWindows()} className="text-muted hover:text-fg">
            {loading ? t("Loading…") : t("Scan windows")}
          </button>
        </div>
      </div>
      {windows.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {windows.slice(0, 12).map((w, i) => {
            const active = sameDeviceName(currentApp, w.owner);
            return (
              <button
                key={`${w.window_id}-${i}`}
                type="button"
                onClick={() => void pick(w.owner, w.title)}
                className={cx(
                  "max-w-[200px] truncate rounded-full border px-2.5 py-1 text-[12px]",
                  active ? "border-accent bg-accent/15 text-accent font-semibold" : "border-border bg-bg text-fg/85 hover:bg-surface-2",
                )}
                title={`${w.owner}: ${w.title}`}
              >
                {w.owner}{w.title ? ` · ${w.title}` : ""}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function kindIcon(kind: string, size = 18): ReactNode {
  switch (kind) {
    case "phone":
      return <Smartphone size={size} />;
    case "computer":
      return <Laptop size={size} />;
    case "web":
      return <Globe size={size} />;
    default:
      return <Wifi size={size} />;
  }
}

export function hubStateLabel(state: string, t: (s: string) => string): string {
  switch (state) {
    case "connected":
      return t("On the hub");
    case "connecting":
    case "disconnected":
      return t("Connecting to the hub…");
    case "refused":
      return t("The hub refused this device. Sign in again.");
    case "signed_out":
      return t("Not signed in.");
    case "off":
    case "stopped":
      return t("The hub is off on this device.");
    default:
      return state;
  }
}
