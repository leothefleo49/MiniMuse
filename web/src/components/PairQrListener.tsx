import { useEffect } from "react";
import { androidApp } from "../android";
import { api } from "../api";
import { useStore } from "../store";
import { useT } from "../i18n";

/** How long the follow-up "syncing" line keeps watching before it stops waiting. */
const SYNC_WATCH_MS = 90_000;

/**
 * The phone scanned a pairing QR (the Android app hands the payload to the page as
 * `nanomuse:sync-qr`): pair at once, from whichever screen was open, and say how it went.
 * Before this the payload only worked while one particular settings panel happened to be
 * mounted, so a scan from anywhere else did nothing at all.
 *
 * A scan on the phone's start screen takes the other road: that screen has no server to claim
 * with, so the app starts the local one and parks the payload in native prefs. This picks it
 * up on load (once - taking clears it) and claims it the same way.
 *
 * Pairing answers as soon as the other device accepts the code; copying a history over
 * happens after that. So the screen says what is happening in three steps instead of going
 * quiet: pairing, paired, synced.
 */
export function PairQrListener() {
  const { toast } = useStore();
  const t = useT();
  useEffect(() => {
    let busy = false;
    let stopped = false;

    const watchSync = async (peer: string) => {
      const started = Date.now();
      while (!stopped && Date.now() - started < SYNC_WATCH_MS) {
        await new Promise((r) => window.setTimeout(r, 1200));
        if (stopped) return;
        const state = await api.syncState().catch(() => null);
        if (state && (state.working?.length ?? 0) === 0) {
          toast(t("Synced with {peer}.", { peer }));
          return;
        }
      }
    };

    const claim = (raw: string) => {
      const payload = raw.trim();
      if (!payload || busy) return;
      busy = true;
      toast(t("Pairing..."));
      api
        .syncPairClaim({ target: payload, code: "", name: "", sync_side_chats: true })
        .then((res) => {
          const peer = res.peer ?? "";
          toast(t("Paired with {peer}. Syncing...", { peer }));
          window.dispatchEvent(new CustomEvent("nanomuse:sync-paired", { detail: res.sync }));
          void watchSync(peer);
        })
        .catch((e: Error) => toast(t("Could not pair: {error}", { error: e.message })))
        .finally(() => {
          busy = false;
        });
    };
    const onQr = (ev: Event) => {
      const detail = (ev as CustomEvent<{ payload?: string } | string>).detail;
      claim(typeof detail === "string" ? detail : detail?.payload ?? "");
    };
    window.addEventListener("nanomuse:sync-qr", onQr);
    const pending = androidApp()?.takePendingPairPayload?.() ?? "";
    if (pending) claim(pending);
    return () => {
      stopped = true;
      window.removeEventListener("nanomuse:sync-qr", onQr);
    };
  }, [toast, t]);
  return null;
}
