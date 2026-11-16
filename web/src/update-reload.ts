import { api } from "./api";

const RELOAD_GUARD_KEY = "nanomuse_version_reloaded";

export interface WaitReloadDeps {
  health?: () => Promise<{ ok: boolean; version?: string }>;
  updateStatus?: () => Promise<{ last_error?: string }>;
  reload?: () => void;
  delay?: (ms: number) => Promise<void>;
  now?: () => number;
  maxWaitMs?: number;
  pollMs?: number;
  initialDelayMs?: number;
}

/** Update any registered service worker and reload the page once per target version. */
export async function refreshSwAndReload(
  targetVersion?: string,
  reload: () => void = () => window.location.reload(),
): Promise<boolean> {
  if (targetVersion && typeof sessionStorage !== "undefined") {
    try {
      if (sessionStorage.getItem(RELOAD_GUARD_KEY) === targetVersion) {
        return false;
      }
      sessionStorage.setItem(RELOAD_GUARD_KEY, targetVersion);
    } catch {
      /* ignore sessionStorage restrictions */
    }
  }
  if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      await reg?.update();
    } catch {
      /* ignore SW update errors during reload */
    }
  }
  reload();
  return true;
}

/**
 * Wait for a self-updating backend to restart onto a different version, then reload.
 * Returns null when the reload was triggered, or a human-readable error if the server
 * restarted without changing version or timed out.
 */
export async function waitForUpdatedServerAndReload(
  previousVersion: string,
  deps: WaitReloadDeps = {},
): Promise<string | null> {
  const health = deps.health ?? (() => api.health());
  const updateStatus = deps.updateStatus ?? (() => api.update(false));
  const reload = deps.reload ?? (() => void refreshSwAndReload(undefined));
  const delay = deps.delay ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = deps.now ?? (() => Date.now());
  const maxWaitMs = deps.maxWaitMs ?? 45_000;
  const pollMs = deps.pollMs ?? 800;
  const initialDelayMs = deps.initialDelayMs ?? 1500;

  if (initialDelayMs > 0) await delay(initialDelayMs);
  const start = now();
  let sawRestarting = false;
  let sameVersionCount = 0;

  while (now() - start < maxWaitMs) {
    try {
      const res = await health();
      if (res.ok && res.version && res.version !== previousVersion) {
        await refreshSwAndReload(res.version, reload);
        return null;
      }
      sameVersionCount += 1;
      if (sawRestarting || sameVersionCount >= 6) {
        try {
          const upd = await updateStatus();
          if (upd.last_error) return upd.last_error;
        } catch {
          /* ignore transient check errors while restarting */
        }
        if (sawRestarting && sameVersionCount >= 4) {
          return "The server restarted, but is still on the previous version.";
        }
      }
    } catch {
      sawRestarting = true;
      sameVersionCount = 0;
    }
    await delay(pollMs);
  }
  try {
    const upd = await updateStatus();
    if (upd.last_error) return upd.last_error;
  } catch {
    /* ignore */
  }
  return "Timed out waiting for MiniMuse to finish restarting.";
}
