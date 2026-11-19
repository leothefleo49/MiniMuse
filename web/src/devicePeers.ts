import type { SyncPeerInfo, SyncState } from "./types";

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
