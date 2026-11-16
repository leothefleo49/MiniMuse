import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { refreshSwAndReload, waitForUpdatedServerAndReload } from "./update-reload";

describe("waitForUpdatedServerAndReload", () => {
  const storage = new Map<string, string>();
  beforeEach(() => {
    storage.clear();
    vi.stubGlobal("sessionStorage", {
      getItem: (k: string) => storage.get(k) ?? null,
      setItem: (k: string, v: string) => void storage.set(k, v),
      removeItem: (k: string) => void storage.delete(k),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reloads once the server comes back with a new version", async () => {
    let calls = 0;
    const reloads: number[] = [];
    const err = await waitForUpdatedServerAndReload("1.0.17", {
      initialDelayMs: 0,
      pollMs: 1,
      health: async () => {
        calls += 1;
        if (calls <= 2) throw new Error("restarting");
        return { ok: true, version: "1.0.18" };
      },
      updateStatus: async () => ({}),
      reload: () => reloads.push(1),
      delay: async () => undefined,
    });
    expect(err).toBeNull();
    expect(reloads).toEqual([1]);
  });

  it("reports swap failure when the server restarts back on the old version with last_error", async () => {
    let calls = 0;
    const reloads: number[] = [];
    const err = await waitForUpdatedServerAndReload("1.0.17", {
      initialDelayMs: 0,
      pollMs: 1,
      health: async () => {
        calls += 1;
        if (calls === 1) throw new Error("restarting");
        return { ok: true, version: "1.0.17" };
      },
      updateStatus: async () => ({ last_error: "Could not replace MiniMuse.exe because it stayed locked." }),
      reload: () => reloads.push(1),
      delay: async () => undefined,
    });
    expect(err).toBe("Could not replace MiniMuse.exe because it stayed locked.");
    expect(reloads).toEqual([]);
  });

  it("refreshSwAndReload guards against repeating reloads for the same target version", async () => {
    let count = 0;
    const reload = () => {
      count += 1;
    };
    expect(await refreshSwAndReload("1.0.18", reload)).toBe(true);
    expect(await refreshSwAndReload("1.0.18", reload)).toBe(false);
    expect(count).toBe(1);
  });
});
