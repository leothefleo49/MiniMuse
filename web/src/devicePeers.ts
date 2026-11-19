import type { HubDevice, HubView, LocalDevice, LocalDevicesView, SyncPeerInfo, SyncState } from "./types";

/**
 * Devices paired straight to this one, without an account.
 *
 * They used to live only inside the sync controls, so the rest of the app looked unpaired:
 * the Devices page counted "1 device", the sidebar had no devices dot, while Data controls
 * happily listed the peer. Every screen that talks about devices now asks this.
 */
export function directPeers(sync: SyncState | null | undefined): SyncPeerInfo[] {
  return (sync?.peers ?? []).filter((peer) => !!peer.url);
}

/** How many of them answer right now. */
export function directPeersOnline(sync: SyncState | null | undefined): number {
  return directPeers(sync).filter((peer) => peer.online).length;
}

/** "192.168.1.20:8787", short enough for a card or a watch line. */
export function peerHost(peer: SyncPeerInfo): string {
  return String(peer.url || "")
    .replace(/^https?:\/\//, "")
    .replace(/\/$/, "");
}

/** Infer whether a directly paired peer is a phone or a computer for its card glyph. */
export function peerKind(peer: SyncPeerInfo): "phone" | "computer" {
  const k = String(peer.kind || "").trim().toLowerCase();
  if (k === "phone" || k === "tablet") return "phone";
  if (k === "computer") return "computer";
  const id = String(peer.id || "").toLowerCase();
  const name = String(peer.name || "").toLowerCase();
  if (id.startsWith("phone-") || /phone|android|pixel|galaxy|samsung|iphone|oneplus|xiaomi/.test(name)) {
    return "phone";
  }
  return "computer";
}

/** Clean display name for a directly paired peer (never raw "localhost"). */
export function peerDisplayName(peer: SyncPeerInfo): string {
  const raw = String(peer.name || "").trim();
  if (raw && raw.toLowerCase() !== "localhost") return raw;
  return peerHost(peer) || (peerKind(peer) === "phone" ? "Paired phone" : "Paired computer");
}

const GENERIC_PHONE_NAMES = new Set(["phone", "android phone", "paired phone", "android", "localhost"]);

/**
 * Filter local.phones so a phone that is already in hubOthers or in sync.peers (including when
 * the browser User-Agent reported a generic "Phone" / "Android phone" label for the same paired
 * phone) never shows up as a duplicate second phone entry.
 */
export function dedupeLocalPhones(
  localPhones: ReadonlyArray<LocalDevice>,
  hubOthers: ReadonlyArray<HubDevice>,
  sync: SyncState | null | undefined,
): LocalDevice[] {
  const hubNames = new Set(
    hubOthers.map((d) => String(d.name ?? "").trim().toLowerCase()).filter(Boolean),
  );
  const pairedPhones = directPeers(sync).filter((p) => peerKind(p) === "phone");
  const pairedPhoneNames = new Set(
    pairedPhones.map((p) => peerDisplayName(p).trim().toLowerCase()).filter(Boolean),
  );
  const hasPairedPhone = pairedPhones.length > 0;
  return localPhones.filter((p) => {
    const nm = String(p.name ?? "").trim().toLowerCase();
    const mdl = String(p.model ?? "").trim().toLowerCase();
    if (nm && hubNames.has(nm)) return false;
    if (mdl && hubNames.has(mdl)) return false;
    if (nm && pairedPhoneNames.has(nm)) return false;
    if (mdl && pairedPhoneNames.has(mdl)) return false;
    if (hasPairedPhone && (GENERIC_PHONE_NAMES.has(nm) || GENERIC_PHONE_NAMES.has(mdl))) {
      return false;
    }
    return true;
  });
}

/**
 * Directly paired peers that are not already represented in hub or local device lists,
 * so the same physical phone or PC never shows up twice under different labels.
 */
export function uniqueDirectPeers(
  sync: SyncState | null | undefined,
  listed: ReadonlyArray<Pick<HubDevice | LocalDevice, "id" | "name"> & { kind?: string }>,
): SyncPeerInfo[] {
  const listedNames = new Set(
    listed
      .map((d) => String(d.name || "").trim().toLowerCase())
      .filter((n) => n && n !== "localhost"),
  );
  const listedIds = new Set(listed.map((d) => String(d.id || "").trim()).filter(Boolean));
  return directPeers(sync).filter((peer) => {
    const pid = String(peer.id || "").trim();
    const pname = peerDisplayName(peer).toLowerCase();
    if (pid && listedIds.has(pid)) return false;
    if (pname && listedNames.has(pname)) return false;
    return true;
  });
}

/** Unified device counts shared across DevicesScreen, SettingsScreen, and Sidebar. */
export function countDevices(
  hub: HubView | null | undefined,
  local: LocalDevicesView | null | undefined,
  sync: SyncState | null | undefined,
): { total: number; online: number } {
  const hubOthers = (hub?.devices ?? []).filter((d) => !d.this && d.kind !== "web");
  const localPhones = dedupeLocalPhones(local?.phones ?? [], hubOthers, sync);
  const others = [...hubOthers, ...localPhones];
  const extraPeers = uniqueDirectPeers(sync, others);
  const connected = hub?.state === "connected";
  const hasThisDevice = Boolean((hub && connected) || local?.computer || sync?.device_id);
  const total = others.length + extraPeers.length + (hasThisDevice ? 1 : 0);
  const online =
    others.filter((d) => d.online).length +
    extraPeers.filter((p) => p.online).length +
    (hasThisDevice ? 1 : 0);
  return { total, online };
}
