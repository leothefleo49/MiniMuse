import { api } from "./api";

/**
 * The look choices (accent, own-bubble colour, theme, bg, scale) ride the runtime's settings,
 * so what is picked on one device saves immediately and propagates to every paired device.
 * Discrete picks flush rapidly, and any pending delta flushes on pagehide/beforeunload.
 */

type UiPatch = {
  accent?: string;
  bubble?: string;
  bg?: string;
  scale?: string;
  theme?: string;
};

let pending: UiPatch = {};
let timer: number | null = null;

function flushUi(): void {
  if (timer !== null) {
    window.clearTimeout(timer);
    timer = null;
  }
  const batch = pending;
  if (Object.keys(batch).length === 0) return;
  pending = {};
  api
    .updateSettings({ ui: batch })
    .catch(() => undefined);
}

if (typeof window !== "undefined") {
  window.addEventListener("pagehide", flushUi);
  window.addEventListener("beforeunload", flushUi);
}

export function pushUi(patch: UiPatch): void {
  pending = { ...pending, ...patch };
  if (timer !== null) window.clearTimeout(timer);
  timer = window.setTimeout(flushUi, 120);
}
