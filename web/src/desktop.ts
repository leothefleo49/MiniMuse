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

import { useEffect, useState } from "react";

export interface PyWebViewApi {
  is_fullscreen?: () => Promise<boolean>;
  set_fullscreen?: (enabled: boolean) => Promise<boolean>;
  toggle_fullscreen?: () => Promise<boolean>;
}

export interface DesktopBridge {
  /** Node's process.platform: "darwin", "win32", "linux" */
  platform: string;
  version: string;
  /** whether the app starts with the computer (Electron's login item); older shells have neither */
  loginItem?: () => Promise<boolean>;
  setLoginItem?: (on: boolean) => void;
  /** optional fullscreen control when running inside Electron */
  isFullscreen?: () => Promise<boolean>;
  setFullscreen?: (on: boolean) => Promise<boolean>;
  /** the quick-chat shortcut was pressed: the window is up, the composer should take focus */
  onQuickChat?: (cb: () => void) => void | (() => void);
}

declare global {
  interface Window {
    nanomuseDesktop?: DesktopBridge;
    nanomuseHarness?: { updates?: DesktopUpdatesBridge };
    pywebview?: { api?: PyWebViewApi };
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

const FS_EVENT = "nanomuse:desktop-fullscreen";
let desktopFsCache = false;
let f11Bound = false;

function emitFs(on: boolean): void {
  desktopFsCache = on;
  try {
    document.documentElement.classList.toggle("desktop-fullscreen", on);
    window.dispatchEvent(new CustomEvent<boolean>(FS_EVENT, { detail: on }));
  } catch {
    /* headless */
  }
}

export function isDesktopFullscreen(): boolean {
  if (typeof document !== "undefined" && document.fullscreenElement) return true;
  return desktopFsCache;
}

export async function syncDesktopFullscreenState(): Promise<boolean> {
  try {
    const pyApi = window.pywebview?.api;
    if (pyApi?.is_fullscreen) {
      const on = !!(await pyApi.is_fullscreen());
      emitFs(on);
      return on;
    }
    const bridge = desktopBridge();
    if (bridge?.isFullscreen) {
      const on = !!(await bridge.isFullscreen());
      emitFs(on);
      return on;
    }
  } catch {
    /* ignore */
  }
  const domFs = typeof document !== "undefined" && !!document.fullscreenElement;
  emitFs(domFs);
  return domFs;
}

export async function setDesktopFullscreen(on: boolean): Promise<boolean> {
  try {
    const pyApi = window.pywebview?.api;
    if (pyApi?.set_fullscreen) {
      const res = !!(await pyApi.set_fullscreen(on));
      emitFs(res);
      return res;
    }
    const bridge = desktopBridge();
    if (bridge?.setFullscreen) {
      const res = !!(await bridge.setFullscreen(on));
      emitFs(res);
      return res;
    }
    if (typeof document !== "undefined") {
      if (on && !document.fullscreenElement && document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
        emitFs(true);
        return true;
      }
      if (!on && document.fullscreenElement && document.exitFullscreen) {
        await document.exitFullscreen();
        emitFs(false);
        return false;
      }
    }
  } catch {
    /* browser blocked or unsupported */
  }
  return isDesktopFullscreen();
}

export async function toggleDesktopFullscreen(): Promise<boolean> {
  return setDesktopFullscreen(!isDesktopFullscreen());
}

export function useDesktopFullscreen(): boolean {
  const [on, setOn] = useState<boolean>(() => isDesktopFullscreen());
  useEffect(() => {
    void syncDesktopFullscreenState();
    const onCustom = (e: Event) => setOn(!!(e as CustomEvent<boolean>).detail);
    const onDom = () => {
      if (!window.pywebview?.api?.set_fullscreen && !desktopBridge()?.setFullscreen) {
        emitFs(!!document.fullscreenElement);
      }
    };
    const onPyReady = () => void syncDesktopFullscreenState();
    window.addEventListener(FS_EVENT, onCustom);
    window.addEventListener("pywebviewready", onPyReady);
    document.addEventListener("fullscreenchange", onDom);
    return () => {
      window.removeEventListener(FS_EVENT, onCustom);
      window.removeEventListener("pywebviewready", onPyReady);
      document.removeEventListener("fullscreenchange", onDom);
    };
  }, []);
  return on;
}

/** Marks <html> so the stylesheet's `wide:` variant and the title-bar spacing apply (index.css). */
export function markDesktopShell(): void {
  if (!f11Bound && typeof window !== "undefined") {
    f11Bound = true;
    window.addEventListener("keydown", (ev) => {
      if (ev.key === "F11") {
        ev.preventDefault();
        void toggleDesktopFullscreen();
      }
    });
  }
  const bridge = desktopBridge();
  if (!bridge && !shellFlagged()) return;
  document.documentElement.classList.add("desktop");
  if (bridge) {
    document.documentElement.dataset.platform = bridge.platform;
  }
}
