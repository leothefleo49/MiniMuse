import { Check, Copy, Download, Laptop, Loader2, QrCode, RefreshCw, Trash2, Unlink, Wifi } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { androidApp } from "../android";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { CloudMe, NearbyPeer, PairCodeInfo, SyncPeerInfo, SyncState } from "../types";
import { cx, relativeTime } from "../util";
import { ConfirmDialog } from "./ConfirmDialog";
import { MuseCaption, MuseCard, MuseDivider, MuseRow, MuseSwitchRow } from "./MuseList";

/** The policy the apps link when the relay has not said where its own is. */
export const PRIVACY_URL = "https://nanomuse.cn/privacy/";

/**
 * Settings → Data controls, in the shape of Muse's: one switch, *Help improve MiniMuse's AI
 * models*, with what it means written under it in full — what the relay keeps while it is
 * on (what you wrote, what the model answered, the tool calls it chose), what it never
 * keeps (the system prompt, tool results, pictures, who you are), the relay's default for
 * new accounts, how many turns are kept so far — and the delete. It applies to the MiniMuse
 * Cloud account only: with your own key nothing passes through the relay, and the switch is
 * shown but cannot be moved until you sign in.
 */
export function DataControls({ me: given, onChanged, flush = false }: { me?: CloudMe | null; onChanged?: () => void; /** the parent already pads 16px: pull the cards out to its edge */ flush?: boolean }) {
  const t = useT();
  const { state, toast } = useStore();
  const signedIn = !!state.hub?.account.signed_in;
  const [loaded, setLoaded] = useState<CloudMe | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const me = given ?? loaded;

  const load = async () => {
    if (!signedIn || given) return;
    try {
      setLoaded(await api.cloudMe());
    } catch {
      /* offline: the switch stays as it was */
    }
  };
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signedIn, given]);

  const ct = me?.contribute ?? { on: false, samples: 0 };
  const samples = ct.samples ?? 0;
  const privacy = ct.privacy_url || PRIVACY_URL;

  const flip = async (on: boolean) => {
    setBusy(true);
    try {
      await api.cloudContribute(on);
      toast(on ? t("On. Turn it off here at any time.") : t("Off. Nothing more is kept."));
      if (given) onChanged?.();
      else await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const wipe = async () => {
    if (!window.confirm(t("The kept conversations are removed from the server. This cannot be undone."))) return;
    setBusy(true);
    try {
      const r = await api.cloudDeleteSamples();
      toast(t("{n} turns deleted", { n: String(r.deleted) }));
      if (given) onChanged?.();
      else await load();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  /** *Download your agent data* (parity note 37): the runtime answers with the zip. */
  const download = async () => {
    setExporting(true);
    try {
      const { blob, name } = await api.exportData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className={cx(flush && "-mx-4")}>
      <SyncControls signedIn={signedIn} />
      <MuseCard>
        <MuseSwitchRow label={t("Help improve MiniMuse's AI models")} checked={signedIn && ct.on} disabled={busy || !signedIn || !me} onChange={(v) => void flip(v)} />
      </MuseCard>
      <MuseCaption>
        {t(
          "While this is on, the text of your chats with the MiniMuse Cloud models (what you wrote, what it answered and the tools it chose to call) is kept on the relay to train the community's own open model. Not your memory or SOUL (the system prompt), not what tools returned, not pictures, and never next to who you are. Your own API key never passes through the relay.",
        )}{" "}
        {!signedIn
          ? t("Sign in to MiniMuse Cloud to use it.")
          : ct.default_on !== undefined && (ct.default_on ? t("New accounts start with it on.") : t("New accounts start with it off."))}{" "}
        {signedIn && samples > 0 && t("{n} turns kept so far.", { n: String(samples) })}{" "}
        <a href={privacy} target="_blank" rel="noopener noreferrer" className="text-accent underline-offset-2 hover:underline">
          {t("Privacy policy")}
        </a>
      </MuseCaption>
      {signedIn && samples > 0 && (
        <MuseCard className="mt-3">
          <MuseRow icon={busy ? <Loader2 size={22} className="animate-spin" /> : <Trash2 size={22} />} label={t("Delete the kept conversations")} value={t("{n} turns", { n: String(samples) })} onClick={() => void wipe()} chevron={false} />
          <MuseDivider inset={16} />
          <MuseCaption className="px-4 pb-2.5 pt-2">{t("Removes every turn kept from this account, whether the switch is on or off now. Turning the switch off keeps what was kept until you delete it here.")}</MuseCaption>
        </MuseCard>
      )}
      <MuseCard className="mt-3">
        <MuseRow icon={exporting ? <Loader2 size={22} className="animate-spin" /> : <Download size={22} />} label={t("Download your agent data")} onClick={() => void download()} chevron={false} />
        <MuseDivider inset={16} />
        <MuseCaption className="px-4 pb-2.5 pt-2">{t("A zip of what this runtime keeps about you: the memory, the goals, the reminders, the triggers, the address book and the chats. It never contains the keys themselves.")}</MuseCaption>
      </MuseCard>
    </div>
  );
}

/**
 * The first card of Data controls (contract C7): *Sync conversations between my devices*, on
 * by default while signed in. Off tells the relay, which deletes what it stores; on pushes
 * this device's chats again. *Delete synced conversations* empties the relay's store and
 * keeps the switch and the chats on every device.
 */
export function SyncControls({
  signedIn,
  showCloudControls = true,
}: {
  signedIn: boolean;
  showCloudControls?: boolean;
}) {
  const t = useT();
  const { state, toast } = useStore();
  const [sync, setSync] = useState<SyncState | null>(null);
  const [nearby, setNearby] = useState<NearbyPeer[]>([]);
  const [pairCode, setPairCode] = useState<PairCodeInfo | null>(null);
  const [target, setTarget] = useState("");
  const [code, setCode] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [syncingNow, setSyncingNow] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [pairing, setPairing] = useState(false);
  const [copiedUri, setCopiedUri] = useState(false);
  const [confirmAction, setConfirmAction] = useState<
    | { kind: "off" }
    | { kind: "wipe" }
    | { kind: "unpair"; peer: SyncPeerInfo }
    | null
  >(null);

  const counts = sync?.relay?.counts ?? { conversations: 0, messages: 0 };
  const stored = signedIn && (counts.conversations > 0 || counts.messages > 0);
  const peers = sync?.peers ?? [];
  const hasPeers = peers.length > 0;
  const canSync = signedIn || hasPeers;
  const syncOn = canSync && !!sync?.enabled;
  const pendingOffline = sync?.pending_offline ?? 0;

  const load = useCallback(async () => {
    try {
      const s = await api.syncState();
      setSync(s);
      if (s.nearby) setNearby(s.nearby);
    } catch {
      /* an older runtime without sync: the card shows the switch as unavailable */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [signedIn, load]);

  useEffect(() => {
    if (!state.sync) return;
    setSync((prev) => ({ ...(prev ?? state.sync!), ...state.sync! }));
    if (state.sync.nearby) setNearby(state.sync.nearby);
  }, [state.sync]);

  const discoverNearby = useCallback(
    async (quiet = false) => {
      setDiscovering(true);
      try {
        const res = await api.syncDiscover();
        setNearby(res.nearby ?? []);
      } catch (e) {
        if (!quiet) toast((e as Error).message);
      } finally {
        setDiscovering(false);
      }
    },
    [toast],
  );

  const claimPair = useCallback(
    async (rawTarget: string, rawCode = "", peerName = "") => {
      const trimmedTarget = rawTarget.trim();
      const trimmedCode = rawCode.trim();
      if (!trimmedTarget) {
        toast(t("Enter the other device's pairing link or local address first."));
        return;
      }
      setPairing(true);
      try {
        const res = await api.syncPairClaim({
          target: trimmedTarget,
          code: trimmedCode,
          name: peerName,
          sync_side_chats: true,
        });
        setSync(res.sync);
        setTarget("");
        setCode("");
        setShowManual(false);
        toast(t("Paired with {peer}.", { peer: res.peer ?? "" }));
      } catch (e) {
        toast((e as Error).message);
      } finally {
        setPairing(false);
      }
    },
    [t, toast],
  );

  useEffect(() => {
    const onQr = (ev: Event) => {
      const detail = (ev as CustomEvent<{ payload?: string } | string>).detail;
      const payload = typeof detail === "string" ? detail : detail?.payload ?? "";
      if (!payload.trim()) return;
      // a payload that carries its code is paired by PairQrListener (app-wide); one without
      // is filled in here for the person to finish
      if (payload.toLowerCase().includes("code=")) return;
      setShowManual(true);
      setTarget(payload.trim());
    };
    const onPaired = (ev: Event) => setSync((ev as CustomEvent<SyncState>).detail);
    window.addEventListener("nanomuse:sync-qr", onQr);
    window.addEventListener("nanomuse:sync-paired", onPaired);
    return () => {
      window.removeEventListener("nanomuse:sync-qr", onQr);
      window.removeEventListener("nanomuse:sync-paired", onPaired);
    };
  }, []);

  const run = async (what: () => Promise<SyncState>, said: string) => {
    setBusy(true);
    try {
      setSync(await what());
      toast(said);
    } catch (e) {
      toast((e as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const flip = (on: boolean) => {
    if (!on && stored) {
      setConfirmAction({ kind: "off" });
      return;
    }
    void run(
      () => api.syncSetState(on),
      on
        ? t("On. Devices sync and merge chats automatically whenever reachable.")
        : t("Off. Changes stay local on this device only."),
    );
  };

  const flipSide = (on: boolean) => {
    void run(
      () => api.syncSetSideChats(on),
      on
        ? t("On. Side chats also sync directly with your paired devices.")
        : t("Off. Side chats stay on this device from now on."),
    );
  };

  const syncNow = async () => {
    setSyncingNow(true);
    try {
      const res = await api.syncNow();
      setSync(res);
      const list = res.peers ?? [];
      const okPeers = list.filter((p) => p.online).length;
      if (list.length > 0 && okPeers === 0) {
        toast(t("Saved locally. No paired devices are reachable right now; changes will merge when online."));
      } else {
        toast(t("Synced and merged."));
      }
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSyncingNow(false);
    }
  };

  const generatePairCode = async () => {
    setPairing(true);
    try {
      const info = await api.syncPairCode();
      setPairCode(info);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setPairing(false);
    }
  };

  const copyPairLink = async () => {
    if (!pairCode?.pair_uri) return;
    try {
      await navigator.clipboard.writeText(pairCode.pair_uri);
      setCopiedUri(true);
      window.setTimeout(() => setCopiedUri(false), 2000);
      toast(t("Pairing link copied."));
    } catch {
      toast(t("Could not copy automatically. Select the link below to copy it."));
    }
  };

  const phone = androidApp();

  return (
    <div className="mb-6 space-y-3">
      {showCloudControls && (
        <>
          <MuseCard>
            <MuseSwitchRow
              label={t("Sync conversations between my devices")}
              checked={syncOn}
              disabled={busy || !sync || !canSync}
              onChange={flip}
            />
          </MuseCard>
          <MuseCaption>
            {hasPeers && !signedIn
              ? t(
                  "Connected directly to {n} paired device(s) without MiniMuse Cloud. Offline chats and edits save locally and merge automatically when online.",
                  { n: String(peers.length) },
                )
              : t(
                  "Sync keeps your chats aligned across your paired devices (directly over Wi-Fi or Tailscale, or via MiniMuse Cloud if signed in). Files stay local.",
                )}{" "}
            {!canSync
              ? t("Pair another device below (no Cloud needed), or sign in to MiniMuse Cloud.")
              : sync?.paused
                ? t("Paused: sign in again to continue.")
                : stored && t("{c} chats, {m} messages kept on Cloud.", { c: String(counts.conversations), m: String(counts.messages) })}
          </MuseCaption>
          {sync && "side_chats" in sync && (
            <>
              <MuseCard>
                <MuseSwitchRow
                  label={t("Also sync side chats")}
                  checked={syncOn && !!sync.side_chats}
                  disabled={busy || !syncOn}
                  onChange={flipSide}
                />
              </MuseCard>
              <MuseCaption>
                {t("Off: side chats stay on this device. On: side chats sync and merge across all paired devices.")}
              </MuseCaption>
            </>
          )}
          {stored && (
            <MuseCard>
              <MuseRow
                icon={busy ? <Loader2 size={22} className="animate-spin" /> : <Trash2 size={22} />}
                label={t("Delete synced conversations")}
                value={t("{n} chats", { n: String(counts.conversations) })}
                onClick={() => setConfirmAction({ kind: "wipe" })}
                chevron={false}
              />
              <MuseDivider inset={16} />
              <MuseCaption className="px-4 pb-2.5 pt-2">
                {t("Removes what MiniMuse Cloud stores for this account; the chats on each device stay, and the switch stays on.")}
              </MuseCaption>
            </MuseCard>
          )}
        </>
      )}

      {/* Direct device-to-device sync (Cloud-free + offline merge) */}
      <MuseCard>
        <div className="px-4 py-3.5 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-[14.5px] font-semibold">
                <Wifi size={16} className="text-accent shrink-0" />
                <span>{t("Direct device sync (no Cloud needed)")}</span>
              </div>
              <p className="mt-1 text-[12.5px] leading-snug text-muted">
                {pendingOffline > 0
                  ? t("{n} offline change(s) saved locally and waiting to merge when a paired device is online.", { n: String(pendingOffline) })
                  : t("Devices sync and merge chronologically whenever both are online. Offline edits stay stored locally until then.")}
              </p>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                disabled={syncingNow || (!hasPeers && !signedIn)}
                onClick={() => void syncNow()}
                className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-[12.5px] font-medium hover:bg-surface-2 disabled:opacity-45"
              >
                <RefreshCw size={13} className={cx(syncingNow && "animate-spin")} />
                {t("Sync & merge now")}
              </button>
            </div>
          </div>

          {sync?.urls && sync.urls.length > 0 && (
            <div className="rounded-xl bg-surface-2 px-3 py-2 text-[12px] text-muted">
              <span className="font-medium text-fg">{sync.device_name || t("This device")}: </span>
              <span className="select-all break-all">{sync.urls[0]}</span>
            </div>
          )}

          {/* Paired peers */}
          {peers.length > 0 && (
            <div className="space-y-2">
              <div className="text-[12px] font-medium uppercase tracking-wide text-muted">{t("Paired devices")}</div>
              <div className="space-y-1.5">
                {peers.map((peer) => (
                  <div key={peer.id} className="flex items-center justify-between gap-2 rounded-2xl border border-border px-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[13.5px] font-medium">
                        <span className={cx("h-2 w-2 rounded-full shrink-0", peer.online ? "bg-emerald-500" : "bg-amber-500")} />
                        <span className="truncate">{peer.name || peer.url}</span>
                        <span className="text-[11.5px] font-normal text-muted">
                          {peer.online ? t("Online") : t("Offline")}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-[12px] text-muted">
                        {peer.pending > 0 ? t("{n} changes queued locally", { n: String(peer.pending) }) : peer.last_sync_at ? t("Merged {when}", { when: relativeTime(peer.last_sync_at) }) : t("Waiting for first sync")}
                        {!peer.online && peer.last_error ? ` · ${peer.last_error}` : ""}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setConfirmAction({ kind: "unpair", peer })}
                      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[12px] text-muted hover:text-rose-600"
                    >
                      <Unlink size={12} /> {t("Unpair")}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Easy pairing actions */}
          <div className="flex flex-wrap gap-2 pt-0.5">
            <button
              type="button"
              disabled={pairing}
              onClick={() => void generatePairCode()}
              className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-fg disabled:opacity-45"
            >
              <QrCode size={14} /> {pairCode ? t("New pairing code") : t("Show pairing code")}
            </button>
            <button
              type="button"
              onClick={() => setShowManual((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-[12.5px] font-medium hover:bg-surface-2"
            >
              <Laptop size={14} /> {t("Enter another device's code")}
            </button>
            <button
              type="button"
              disabled={discovering}
              onClick={() => void discoverNearby(false)}
              className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-[12.5px] font-medium hover:bg-surface-2 disabled:opacity-45"
            >
              <RefreshCw size={13} className={cx(discovering && "animate-spin")} />
              {t("Scan local network")}
            </button>
            {phone && typeof phone.scanToPair === "function" && (
              <button
                type="button"
                onClick={() => phone.scanToPair?.()}
                className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-[12.5px] font-medium hover:bg-surface-2"
              >
                <QrCode size={14} /> {t("Scan QR")}
              </button>
            )}
          </div>

          {/* Active pairing code + QR */}
          {pairCode && (
            <div className="rounded-2xl border border-border bg-surface-2/70 p-3 space-y-2.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-[12px] text-muted">{t("Enter this 6-character code on your other device:")}</div>
                  <div className="mt-1 font-mono text-[22px] font-bold tracking-[0.2em] text-fg select-all">{pairCode.code}</div>
                </div>
                {pairCode.qr_png && (
                  <img
                    src={`data:image/png;base64,${pairCode.qr_png}`}
                    alt={t("Direct pairing QR code")}
                    className="h-28 w-28 rounded-xl bg-white p-1.5 shadow-sm"
                  />
                )}
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted">
                <span className="truncate flex-1 select-all">{pairCode.pair_uri}</span>
                <button
                  type="button"
                  onClick={() => void copyPairLink()}
                  className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-1 text-[12px] font-medium text-fg"
                >
                  {copiedUri ? <Check size={12} /> : <Copy size={12} />}
                  {copiedUri ? t("Copied") : t("Copy pairing link")}
                </button>
              </div>
            </div>
          )}

          {/* Nearby LAN devices */}
          {nearby.length > 0 && (
            <div className="space-y-1.5">
              <div className="text-[12px] font-medium uppercase tracking-wide text-muted">{t("Nearby on this network")}</div>
              {nearby.map((np) => (
                <div key={np.device_id} className="flex items-center justify-between gap-2 rounded-2xl bg-surface-2 px-3 py-2 text-[12.5px]">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate">{np.name || np.device_id}</div>
                    <div className="text-[11.5px] text-muted truncate">{np.urls[0] ?? ""}</div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setShowManual(true);
                      setTarget(np.urls[0] ?? "");
                    }}
                    className="shrink-0 rounded-full bg-accent/12 px-3 py-1 text-[12px] font-medium text-accent hover:bg-accent/20"
                  >
                    {t("Pair")}
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Manual pair / code claim form */}
          {showManual && (
            <form
              className="rounded-2xl border border-border bg-surface-2/60 p-3 space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void claimPair(target, code);
              }}
            >
              <label className="block space-y-1">
                <span className="text-[12px] text-muted">{t("Other device address (http://IP:8787) or pasted pairing link")}</span>
                <input
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                  placeholder="http://192.168.1.25:8787"
                  className="w-full rounded-xl bg-surface px-3 py-2 text-[13px] outline-none focus:ring-2 focus:ring-accent/40"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-[12px] text-muted">{t("6-character pairing code (leave blank if pasted full link)")}</span>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value.toUpperCase())}
                  placeholder="ABC123"
                  maxLength={12}
                  className="w-full rounded-xl bg-surface px-3 py-2 font-mono text-[14px] tracking-widest outline-none focus:ring-2 focus:ring-accent/40"
                />
              </label>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => setShowManual(false)}
                  className="rounded-full border border-border px-3 py-1.5 text-[12.5px] text-muted"
                >
                  {t("Cancel")}
                </button>
                <button
                  type="submit"
                  disabled={pairing || !target.trim()}
                  className="inline-flex items-center gap-1.5 rounded-full bg-accent px-4 py-1.5 text-[12.5px] font-medium text-accent-fg disabled:opacity-45"
                >
                  {pairing && <Loader2 size={13} className="animate-spin" />}
                  {t("Pair devices")}
                </button>
              </div>
            </form>
          )}

          {/* Deeper Optional Setting: Device-specific appearance & default model overrides (disabled by default) */}
          <details className="rounded-2xl border border-border/60 bg-surface-2/40 px-3.5 py-2.5 text-[12.5px]">
            <summary className="cursor-pointer font-medium text-muted hover:text-fg">
              {t("Advanced: Device-specific appearance & default model overrides (optional)")}
            </summary>
            <div className="mt-2.5 space-y-2.5 border-t border-border/50 pt-2.5">
              <p className="text-[11.5px] text-muted">
                {t("Off by default so all paired devices share the exact same setup. Enable only if you want this specific device to keep its own theme/appearance or use a different default AI model when messages are typed from this device.")}
              </p>
              <label className="flex items-center justify-between gap-2">
                <span className="text-fg">{t("Enable device-specific overrides on this device")}</span>
                <input
                  type="checkbox"
                  checked={!!sync?.device_overrides?.enabled}
                  onChange={(e) =>
                    void run(
                      () => api.syncSetDeviceOverrides({ enabled: e.target.checked }),
                      e.target.checked ? t("Device-specific overrides enabled.") : t("Device-specific overrides disabled."),
                    )
                  }
                  className="rounded border-border"
                />
              </label>
              {sync?.device_overrides?.enabled && (
                <>
                  <label className="flex items-center justify-between gap-2">
                    <span className="text-fg">{t("Keep this device's own theme & appearance")}</span>
                    <input
                      type="checkbox"
                      checked={!!sync.device_overrides.keep_local_appearance}
                      onChange={(e) =>
                        void run(
                          () => api.syncSetDeviceOverrides({ keep_local_appearance: e.target.checked }),
                          t("Saved device appearance preference."),
                        )
                      }
                      className="rounded border-border"
                    />
                  </label>
                  <label className="block space-y-1">
                    <span className="text-[11.5px] text-muted">
                      {t("Default AI model when messages are sent from this device (blank = use synced default)")}
                    </span>
                    <div className="flex gap-2">
                      <input
                        defaultValue={sync.device_overrides.default_model || ""}
                        placeholder="e.g. deepseek/deepseek-v4.1-flash-1017 or qwen3.5:9b-32k"
                        onBlur={(e) => {
                          const nextVal = e.target.value.trim();
                          if (nextVal !== (sync.device_overrides?.default_model || "")) {
                            void run(
                              () => api.syncSetDeviceOverrides({ default_model: nextVal }),
                              t("Saved device-specific default model."),
                            );
                          }
                        }}
                        className="flex-1 rounded-xl bg-surface px-3 py-1.5 font-mono text-[12px] outline-none focus:ring-2 focus:ring-accent/40"
                      />
                    </div>
                  </label>
                </>
              )}
            </div>
          </details>
        </div>
      </MuseCard>

      <ConfirmDialog
        open={confirmAction !== null}
        title={
          confirmAction?.kind === "off"
            ? t("Turn off device sync?")
            : confirmAction?.kind === "wipe"
              ? t("Delete synced conversations from Cloud?")
              : t("Unpair {peer}?", { peer: confirmAction?.kind === "unpair" ? confirmAction.peer.name || confirmAction.peer.url : "" })
        }
        body={
          confirmAction?.kind === "off"
            ? t("Turning off deletes the copy on MiniMuse Cloud. Local chats stored on each device stay safe.")
            : confirmAction?.kind === "wipe"
              ? t("Synced conversations are removed from MiniMuse Cloud. The local chats on each device stay.")
              : t("This stops direct syncing with {peer}. Your existing chats stay on both devices.", {
                  peer: confirmAction?.kind === "unpair" ? confirmAction.peer.name || confirmAction.peer.url : "",
                })
        }
        confirmLabel={
          confirmAction?.kind === "off"
            ? t("Turn off")
            : confirmAction?.kind === "wipe"
              ? t("Delete from Cloud")
              : t("Unpair")
        }
        danger={confirmAction?.kind !== "off"}
        onCancel={() => setConfirmAction(null)}
        onConfirm={() => {
          const act = confirmAction;
          setConfirmAction(null);
          if (!act) return;
          if (act.kind === "off") {
            void run(() => api.syncSetState(false), t("Off. Nothing is kept on MiniMuse Cloud any more."));
          } else if (act.kind === "wipe") {
            void run(() => api.syncDelete(), t("Synced conversations deleted."));
          } else {
            void api
              .syncUnpair(act.peer.id || act.peer.url)
              .then((res) => {
                setSync(res);
                toast(t("Unpaired."));
              })
              .catch((e) => toast((e as Error).message));
          }
        }}
      />
    </div>
  );
}
