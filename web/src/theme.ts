/**
 * The look: light or dark follows the system, or a picked canvas tint (the background
 * presets, one pick for the account) sets it — a dark canvas turns the UI dark, a light
 * one keeps it light. index.html applies the resolved theme before the first paint; this
 * module keeps <html data-theme>, the theme-color meta, the installed app's manifest
 * colours, the canvas tint and the UI scale in step afterwards.
 */
import { useSyncExternalStore } from "react";
import { applyAccent } from "./accent";
import { pushUi } from "./ui-sync";

export type Theme = "light" | "dark";

const BG_KEY = "nanomuse_bg";
const SCALE_KEY = "nanomuse_ui_scale";
const COLORS: Record<Theme, string> = { light: "#ffffff", dark: "#181819" };
const HEX = /^#[0-9a-fA-F]{6}$/;

/** The canvas tints: an empty value (White) is the theme's own colour — white in the
 *  light look, near-black in the dark one — and the named ones pin a colour. The boxes
 *  always stay a step lighter or darker than the canvas. */
export const BG_PRESETS: Array<{ value: string; label: string; color: string }> = [
  { value: "", label: "White", color: "#ffffff" },
  { value: "#181819", label: "Dark", color: "#181819" },
  { value: "#f4efe8", label: "Paper", color: "#f4efe8" },
  { value: "#eef4f8", label: "Frost", color: "#eef4f8" },
  { value: "#f3eef8", label: "Lavender", color: "#f3eef8" },
  { value: "#eaf5ee", label: "Mint", color: "#eaf5ee" },
  { value: "#1c1a18", label: "Coal", color: "#1c1a18" },
  { value: "#16202c", label: "Night", color: "#16202c" },
];

function loadBg(): string {
  try {
    const srv = (window as unknown as { __NM_SERVER_UI__?: { bg?: string } }).__NM_SERVER_UI__;
    if (srv && typeof srv.bg === "string") {
      if (HEX.test(srv.bg) && srv.bg.toLowerCase() !== "#000000") {
        const clean = srv.bg.toLowerCase();
        localStorage.setItem(BG_KEY, clean);
        return clean;
      }
      if (srv.bg === "") {
        localStorage.removeItem(BG_KEY);
        return "";
      }
    }
    const v = localStorage.getItem(BG_KEY) ?? "";
    if (v.toLowerCase() === "#000000") {
      localStorage.removeItem(BG_KEY);
      return "";
    }
    return HEX.test(v) ? v.toLowerCase() : "";
  } catch {
    return "";
  }
}

function loadScale(): number {
  try {
    // a test hook: ?ui_scale=1.05 opens at that scale (and so do I check it headless)
    const q = new URLSearchParams(window.location.search).get("ui_scale");
    if (q) {
      const v = Number(q);
      if (v >= 0.8 && v <= 1.3) return v;
    }
    const v = Number(localStorage.getItem(SCALE_KEY));
    if (v >= 0.8 && v <= 1.3) return v;
  } catch {
    // no storage: 100%
  }
  return 1;
}

let bg = loadBg();
let scale = loadScale();
const listeners = new Set<() => void>();
const media = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;

// ------------------------------------------------------------------ colour helpers
function channel(hex: string, first: number): number {
  return parseInt(hex.slice(first, first + 2), 16);
}

function luminance(hex: string): number {
  return [0, 2, 4]
    .map((i) => {
      const raw = channel(hex, 1 + i) / 255;
      return raw <= 0.03928 ? raw / 12.92 : ((raw + 0.055) / 1.055) ** 2.4;
    })
    .reduce((sum, x, i) => sum + x * [0.2126, 0.7152, 0.0722][i], 0);
}

function mix(a: string, b: string, k: number): string {
  const out = [0, 2, 4].map((i) => {
    const x = channel(a, 1 + i);
    const y = channel(b, 1 + i);
    return Math.round(x + (y - x) * k)
      .toString(16)
      .padStart(2, "0");
  });
  return `#${out.join("")}`;
}

// ------------------------------------------------------------------ values
/** The resolved look: a picked canvas decides; empty ("") is explicitly the White preset. */
export function getTheme(): Theme {
  return HEX.test(bg) ? (luminance(bg) < 0.4 ? "dark" : "light") : "light";
}

export function bgNow(): string {
  return bg;
}

export function uiScaleNow(): number {
  return scale;
}

// ------------------------------------------------------------------ setters
export function setBg(next: string): void {
  bg = HEX.test(next) && next.toLowerCase() !== "#000000" ? next.toLowerCase() : "";
  try {
    if (bg) localStorage.setItem(BG_KEY, bg);
    else localStorage.removeItem(BG_KEY);
  } catch {
    // fine — the choice lasts for this page then
  }
  applyTheme();
  listeners.forEach((fn) => fn());
  pushUi({ bg, theme: getTheme() });
  try {
    void fetch("/manifest.webmanifest", { credentials: "same-origin", cache: "no-store" }).catch(
      () => {},
    );
  } catch {
    // ignore
  }
}

/** 0.8 to 1.3: the whole UI scales, text, icons and boxes together. */
export function setUiScale(next: number): void {
  scale = Math.max(0.8, Math.min(1.3, Math.round(next * 100) / 100));
  try {
    localStorage.setItem(SCALE_KEY, String(scale));
  } catch {
    // fine — the choice lasts for this page then
  }
  applyZoom();
  listeners.forEach((fn) => fn());
  pushUi({ scale: String(scale) });
}

// ------------------------------------------------------------------ applying
function applyZoom(): void {
  if (typeof document === "undefined") return;
  const root = document.getElementById("root");
  if (!root) return;
  const el = root as HTMLElement & { style: CSSStyleDeclaration };
  // Chrome on Android breaks under CSS `zoom` on the root (the viewport derails), so
  // the scale is a transform on the app root: both width and height are laid out at
  // 100%/scale and the transform brings them back to the exact viewport dimensions,
  // keeping fixed bars, right-aligned buttons and drawers 100% centered and aligned.
  if (scale === 1) {
    el.style.transformOrigin = "";
    el.style.transform = "";
    el.style.width = "";
    el.style.height = "";
  } else {
    el.style.transformOrigin = "top left";
    el.style.transform = `scale(${scale})`;
    el.style.width = `calc(100% / ${scale})`;
    el.style.height = `calc(100% / ${scale})`;
  }
}

/** The canvas tint: a picked colour repaints the background and re-derives the boxes
 *  around it (a step lighter on a dark canvas, darker on a light one, always a visible
 *  edge). The accent, the ink and the bubble stay theirs. */
const CANVAS_TOKENS = [
  "--om-bg",
  "--om-surface",
  "--om-surface-2",
  "--om-border",
  "--om-fg",
  "--om-muted",
  "--om-ink",
  "--om-ink-fg",
  "--om-bubble-user",
  "--om-bubble-user-fg",
  "--om-bubble-agent",
];

export function applyBg(): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (!HEX.test(bg)) {
    CANVAS_TOKENS.forEach((k) => root.style.removeProperty(k));
    root.style.setProperty("--om-bg", COLORS[getTheme()]);
    return;
  }
  const dark = luminance(bg) < 0.4;
  const surface = dark ? mix(bg, "#ffffff", 0.035) : bg;
  const surface2 = dark ? mix(bg, "#ffffff", 0.09) : mix(bg, "#000000", 0.05);
  const border = dark ? mix(bg, "#ffffff", 0.16) : mix(bg, "#000000", 0.1);
  const fg = dark ? "#ffffff" : mix(bg, "#000000", 0.93);
  const muted = mix(fg, bg, 0.42);
  const ink = fg;
  const inkFg = dark ? mix(bg, "#000000", 0.82) : "#ffffff";
  const overrides: Record<string, string> = {
    "--om-bg": bg,
    "--om-surface": surface,
    "--om-surface-2": surface2,
    "--om-border": border,
    "--om-fg": fg,
    "--om-muted": muted,
    "--om-ink": ink,
    "--om-ink-fg": inkFg,
    "--om-bubble-user": ink,
    "--om-bubble-user-fg": inkFg,
    "--om-bubble-agent": surface2,
  };
  for (const [k, v] of Object.entries(overrides)) root.style.setProperty(k, v);
}

/**
 * The installed app's bars bake their colours from the manifest once, so a theme change
 * would leave them dark. The manifest is now served by the runtime reading a `nm_look`
 * cookie (theme:bg), so the next launch paints both bars right; the cookie is set here
 * on every change, and the tab's theme-color meta follows live.
 */
export function updateManifest(): void {
  if (typeof document === "undefined") return;
  const theme = getTheme();
  const isHex = HEX.test(bg);
  const barColor = isHex ? bg : COLORS[theme];
  const look = `${theme}:${isHex ? bg : ""}`;
  document.documentElement.style.colorScheme = theme;
  document.documentElement.style.setProperty("--om-bg", barColor);
  document.documentElement.style.backgroundColor = barColor;
  if (document.body) {
    document.body.style.setProperty("--om-bg", barColor);
    document.body.style.backgroundColor = barColor;
  }
  const appRoot = document.getElementById("root");
  if (appRoot) {
    appRoot.style.backgroundColor = barColor;
  }
  try {
    document.cookie = `nm_look=${look}; path=/; max-age=31536000; samesite=lax`;
    const manifestLink = document.querySelector('link[rel="manifest"]');
    if (manifestLink) {
      manifestLink.setAttribute("crossorigin", "use-credentials");
      // Keep canonical /manifest.webmanifest URL so Chrome Android WebApkUpdateDataFetcher
      // recognizes the manifest URL and updates the installed PWA's bottom navigation bar.
      if (manifestLink.getAttribute("href") !== "/manifest.webmanifest") {
        manifestLink.setAttribute("href", "/manifest.webmanifest");
      }
    }
  } catch {
    // no cookies: the runtime serves the light manifest and the meta still follows
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", barColor);
  const appleMeta = document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]');
  if (appleMeta) appleMeta.setAttribute("content", theme === "dark" ? "black-translucent" : "default");
}

const PWA_FS_KEY = "nanomuse_pwa_fullscreen";
let pwaFullscreen: boolean = (() => {
  try {
    const v = localStorage.getItem(PWA_FS_KEY);
    // Default OFF: an immersive fullscreen request from the page fights the system's
    // gesture bar, and Android answers it by bringing the navigation bar back in its
    // baked colour whenever the keyboard opens — the strip people reported. The
    // installed app already runs fullscreen through the manifest, whose bars now carry
    // the canvas colour, so there is no fight to pick.
    return v === "1";
  } catch {
    return false;
  }
})();

export function pwaFullscreenNow(): boolean {
  return pwaFullscreen;
}

export function setPwaFullscreen(on: boolean): void {
  pwaFullscreen = on;
  try {
    localStorage.setItem(PWA_FS_KEY, on ? "1" : "0");
  } catch {
    // private mode
  }
  if (typeof document !== "undefined") {
    if (on && !document.fullscreenElement) {
      void document.documentElement.requestFullscreen?.({ navigationUI: "hide" }).catch(() => {});
    } else if (!on && document.fullscreenElement) {
      void document.exitFullscreen?.().catch(() => {});
    }
  }
  listeners.forEach((fn) => fn());
}

export function usePwaFullscreen(): boolean {
  return useSyncExternalStore(subscribe, pwaFullscreenNow, pwaFullscreenNow);
}

/** <html data-theme>, the browser chrome colours and the derived canvas follow the
 *  resolved look and the picked background. */
export function applyTheme(): void {
  if (typeof document === "undefined") return;
  const theme = getTheme();
  document.documentElement.dataset.theme = theme;
  applyZoom();
  applyBg();
  updateManifest();
  // the accent's soft pairs follow the theme (a custom colour wants a darker companion)
  applyAccent();
}

media?.addEventListener?.("change", () => {
  if (!HEX.test(bg)) {
    applyTheme();
    listeners.forEach((fn) => fn());
  }
});

// the first paint already has the theme (index.html); the canvas tint, the zoom and the
// manifest colours join it now
applyTheme();

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme, getTheme);
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useBg(): string {
  return useSyncExternalStore(subscribe, bgNow, bgNow);
}

export function useUiScale(): number {
  return useSyncExternalStore(subscribe, uiScaleNow, uiScaleNow);
}
