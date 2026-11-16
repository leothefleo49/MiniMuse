/**
 * The colours the person picks for this device, like the theme (index.css). Two of them:
 * the **accent** moves buttons, links, the working dot and the ink surfaces (the send
 * button, the active tab pill, the scroll disc) together; the **user messages** colour
 * moves the chat's own side. The app's blue is the start and monochrome chat ink, as the
 * stylesheet drew it: no choice means the original look. A picked colour gets its soft
 * pairs computed on the spot — the stylesheet's own tones are only the first draft
 * anyone should inherit — and theme.ts re-applies on every theme change.
 */
import { useSyncExternalStore } from "react";
import { pushUi } from "./ui-sync";

const ACCENT_KEY = "nanomuse_accent";
const BUBBLE_KEY = "nanomuse_bubble";

export const ACCENTS: Array<{ value: string; label: string; color: string }> = [
  { value: "", label: "Blue", color: "#0064d4" },
  { value: "#0d9488", label: "Teal", color: "#0d9488" },
  { value: "#2f9e44", label: "Green", color: "#2f9e44" },
  { value: "#7048e8", label: "Violet", color: "#7048e8" },
  { value: "#e64980", label: "Rose", color: "#e64980" },
  { value: "#e8590c", label: "Orange", color: "#e8590c" },
];

export const BUBBLE_PRESETS: Array<{ value: string; label: string; color: string }> = [
  { value: "#0064d4", label: "Blue", color: "#0064d4" },
  { value: "#0d9488", label: "Teal", color: "#0d9488" },
  { value: "#2f9e44", label: "Green", color: "#2f9e44" },
  { value: "#7048e8", label: "Violet", color: "#7048e8" },
  { value: "#e64980", label: "Rose", color: "#e64980" },
  { value: "#e8590c", label: "Orange", color: "#e8590c" },
  { value: "#334155", label: "Slate", color: "#334155" },
];

const ACCENT_OVERRIDES = [
  "--om-accent",
  "--om-accent-soft",
  "--om-accent-soft-fg",
  "--color-accent-fg",
  // the ink surfaces take the accent too: the send button, the active tab pill and the
  // scroll disc share the token with what used to be black (light) or white (dark)
  "--om-ink",
  "--om-ink-fg",
];

const BUBBLE_OVERRIDES = ["--om-bubble-user", "--om-bubble-user-fg", "--om-bubble-user-selection"];

const HEX = /^#[0-9a-fA-F]{6}$/;

function load(key: string): string {
  try {
    const v = localStorage.getItem(key) ?? "";
    return HEX.test(v) ? v.toLowerCase() : "";
  } catch {
    return "";
  }
}

let accent = load(ACCENT_KEY);
let bubble = load(BUBBLE_KEY);
const listeners = new Set<() => void>();

function getAccent(): string {
  return accent;
}

function getBubble(): string {
  return bubble;
}

/** The current picks, read by the settings sync when the runtime pushes another device's. */
export function accentNow(): string {
  return accent;
}

export function bubbleNow(): string {
  return bubble;
}

/** A preset's stored word (an empty string is the app's own blue) or a picked hex colour. */
export function setAccent(next: string): void {
  accent = HEX.test(next) ? next.toLowerCase() : "";
  try {
    if (accent) localStorage.setItem(ACCENT_KEY, accent);
    else localStorage.removeItem(ACCENT_KEY);
  } catch {
    // fine — the choice lasts for this page then
  }
  applyAccent();
  listeners.forEach((fn) => fn());
  pushUi({ accent });
}

/** An empty string means "the side of my messages follows the accent" (or the ink). */
export function setBubble(next: string): void {
  bubble = HEX.test(next) ? next.toLowerCase() : "";
  try {
    if (bubble) localStorage.setItem(BUBBLE_KEY, bubble);
    else localStorage.removeItem(BUBBLE_KEY);
  } catch {
    // fine — the choice lasts for this page then
  }
  applyAccent();
  listeners.forEach((fn) => fn());
  pushUi({ bubble });
}

function channelOf(hex: string, first: number): number {
  return parseInt(hex.slice(first, first + 2), 16);
}

/** the colour between ``a`` and ``b``: k is the share of ``b`` */
function mix(a: string, b: string, k: number): string {
  const out = [0, 2, 4].map((i) => {
    const x = channelOf(a, 1 + i);
    const y = channelOf(b, 1 + i);
    return Math.round(x + (y - x) * k)
      .toString(16)
      .padStart(2, "0");
  });
  return `#${out.join("")}`;
}

/** a 0..1 luminance, for the dark-or-light ink that reads on the colour */
function luminance(hex: string): number {
  return [0, 2, 4].map((i) => {
    const raw = channelOf(hex, 1 + i) / 255;
    return raw <= 0.03928 ? raw / 12.92 : ((raw + 0.055) / 1.055) ** 2.4;
  })
    .reduce((sum, x, i) => sum + x * [0.2126, 0.7152, 0.0722][i], 0);
}

/** The ink that reads on the colour, whatever the theme around it is. */
function inkFor(color: string): string {
  return luminance(color) > 0.62 ? "#1a1a1c" : "#ffffff";
}

/** The tokens follow the resolved theme, so a dark page gets a dark-suited pair. theme.ts
    applies it again on every theme change, right after <html data-theme> is set. */
export function applyAccent(): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (!HEX.test(accent)) {
    ACCENT_OVERRIDES.forEach((k) => root.style.removeProperty(k));
  } else {
    const dark = root.dataset.theme === "dark";
    const soft = dark ? mix(accent, "#000000", 0.75) : mix(accent, "#ffffff", 0.85);
    const softFg = dark ? mix(accent, "#ffffff", 0.85) : mix(accent, "#000000", 0.42);
    const fg = inkFor(accent);
    root.style.setProperty("--om-accent", accent);
    root.style.setProperty("--om-accent-soft", soft);
    root.style.setProperty("--om-accent-soft-fg", softFg);
    root.style.setProperty("--color-accent-fg", fg);
    root.style.setProperty("--om-ink", accent);
    root.style.setProperty("--om-ink-fg", fg);
  }
  if (!HEX.test(bubble)) {
    BUBBLE_OVERRIDES.forEach((k) => root.style.removeProperty(k));
  } else {
    const fg = inkFor(bubble);
    root.style.setProperty("--om-bubble-user", bubble);
    root.style.setProperty("--om-bubble-user-fg", fg);
    root.style.setProperty("--om-bubble-user-selection", `color-mix(in srgb, ${fg} 30%, transparent)`);
  }
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useAccent(): string {
  return useSyncExternalStore(subscribe, getAccent, getAccent);
}

export function useBubble(): string {
  return useSyncExternalStore(subscribe, getBubble, getBubble);
}
