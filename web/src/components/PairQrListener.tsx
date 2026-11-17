import { useEffect } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { useT } from "../i18n";

/**
 * The phone scanned a pairing QR (the Android app hands the payload to the page as
 * `nanomuse:sync-qr`): pair at once, from whichever screen was open, and say how it went.
 * Before this the payload only worked while one particular settings panel happened to be
 * mounted, so a scan from anywhere else did nothing at all.
 */
export function PairQrListener() {
  const { toast } = useStore();
  const t = useT();
  useEffect(() => {
    let busy = false;
    const onQr = (ev: Event) => {
      const detail = (ev as CustomEvent<{ payload?: string } | string>).detail;
      const payload = (typeof detail === "string" ? detail : detail?.payload ?? "").trim();
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
    window.addEventListener("nanomuse:sync-qr", onQr);
    return () => window.removeEventListener("nanomuse:sync-qr", onQr);
  }, [toast, t]);
  return null;
}
