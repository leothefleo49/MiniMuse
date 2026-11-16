import { api } from "./api";

/**
 * The look choices (accent, own-bubble colour, theme) ride the runtime's settings, so
 * what is picked on one device is what every device of the account shows. Each module
 * pushes its own field after a pick; a short debounce folds rapid picks into one PUT.
 */

let last: { accent?: string; bubble?: string; bg?: string; scale?: string; theme?: string } = {};
let timer: number | null = null;

export function pushUi(patch: {
  accent?: string;
  bubble?: string;
  bg?: string;
  scale?: string;
  theme?: string;
}): void {
  last = { ...last, ...patch };
  if (timer !== null) window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    timer = null;
    api
      .updateSettings({ ui: last })
      .catch(() => undefined); // the look stays on this device; it just does not reach the others
  }, 250);
}
