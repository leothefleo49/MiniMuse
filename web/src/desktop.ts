/**
 * The desktop app (desktop/) shows this web app in an Electron window and exposes a little of
 * itself as `window.nanomuseDesktop` (desktop/app/src/preload/index.ts). Knowing it is in that
 * shell, the app lays itself out like a desktop program at any window width — the sidebar
 * always, the content across the window — and on a Mac leaves the traffic lights room.
 */
export interface DesktopUpdateState {
  currentVersion: string;
  phase: "idle" | "checking" | "downloading" | "ready" | "up-to-date" | "error";
  version: string | null;
  progress: number | null;
  source: "mirror" | "github" | null;
  failure: "check" | "download" | "restart" | null;
  checkedAt: number | null;
  autoDownload: boolean;
  paused: "disabled" | "metered" | "unknown" | null;
}

export interface DesktopUpdatesBridge {
  snapshot: () => Promise<DesktopUpdateState | null>;
  check: () => Promise<DesktopUpdateState | null>;
  download: () => Promise<DesktopUpdateState | null>;
  restart: () => Promise<DesktopUpdateState | null>;
  setAutoDownload: (enabled: boolean) => Promise<DesktopUpdateState | null>;
  subscribe: (listener: (state: DesktopUpdateState) => void) => () => void;
}

export interface DesktopBridge {
  /** Node's process.platform: "darwin", "win32", "linux" */
  platform: string;
  version: string;
  /** whether the app starts with the computer (Electron's login item); older shells have neither */
  loginItem?: () => Promise<boolean>;
  setLoginItem?: (on: boolean) => void;
  /** the quick-chat shortcut was pressed: the window is up, the composer should take focus */
  onQuickChat?: (cb: () => void) => void | (() => void);
}

declare global {
  interface Window {
    nanomuseDesktop?: DesktopBridge;
    nanomuseHarness?: { updates?: DesktopUpdatesBridge };
  }
}

export function desktopUpdates(): DesktopUpdatesBridge | null {
  return window.nanomuseHarness?.updates ?? null;
}

export function desktopBridge(): DesktopBridge | null {
  return window.nanomuseDesktop ?? null;
}

export function isDesktopApp(): boolean {
  return !!window.nanomuseDesktop || shellFlagged();
}

/** True when the native shell (pywebview) opened us with ?shell=desktop — no preload bridge there. */
function shellFlagged(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("shell") === "desktop";
  } catch {
    return false;
  }
}

/** The desktop title bar is hidden on macOS ("hiddenInset"): the window's controls sit over our page. */
export function hasInsetTitleBar(): boolean {
  return window.nanomuseDesktop?.platform === "darwin";
}

/** Marks <html> so the stylesheet's `wide:` variant and the title-bar spacing apply (index.css). */
export function markDesktopShell(): void {
  const bridge = desktopBridge();
  if (!bridge && !shellFlagged()) return;
  document.documentElement.classList.add("desktop");
  if (bridge) {
    document.documentElement.dataset.platform = bridge.platform;
  }
}
