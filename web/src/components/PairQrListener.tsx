import { useEffect } from "react";
import { androidApp } from "../android";
import { api } from "../api";
import { useStore } from "../store";
import { useT } from "../i18n";

/**
 * The phone scanned a pairing QR (the Android app hands the payload to the page as
 * `nanomuse:sync-qr`): pair at once, from whichever screen was open, and say how it went.
 * Before this the payload only worked while one particular settings panel happened to be
 * mounted, so a scan from anywhere else did nothing at all.
 *
 * A scan on the phone's start screen takes the other road: that screen has no server to claim
 * with, so the app starts the local one and parks the payload in native prefs. This picks it
 * up on load (once - taking clears it) and claims it the same way.
 */
export function PairQrListener() {
  const { toast } = useStore();
  const t = useT();
  useEffect(() => {
    let busy = false;
    const claim = (raw: string) => {
      const payload = raw.trim();
      if (!payload || busy) return;
      busy = true;
      toast(t("Pairing..."));
      api
        .syncPairClaim({ target: payload, code: "", name: "", sync_side_chats: true })
        .then((res) => {
          toast(t("Paired with {peer}.", { peer: res.peer ?? "" }));
          window.dispatchEvent(new CustomEvent("nanomuse:sync-paired", { detail: res.sync }));
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
    return () => window.removeEventListener("nanomuse:sync-qr", onQr);
  }, [toast, t]);
  return null;
}
