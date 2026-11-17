import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ImportJobView } from "../api";

const toast = vi.fn();
let jobs: ImportJobView[] = [];
vi.mock("../api", () => ({ api: { importJobs: async () => ({ jobs }) } }));
vi.mock("../store", () => ({ useStore: () => ({ toast, refreshSettings: async () => undefined }) }));
vi.mock("../i18n", () => ({
  useT: () => (key: string, vars?: Record<string, string | number>) =>
    Object.entries(vars ?? {}).reduce((out, [k, v]) => out.replace(`{${k}}`, String(v)), key),
}));
vi.mock("./ImportExport", () => ({ summaryOf: () => "10 messages added." }));

import { ImportProgress } from "./ImportProgress";

const job = (over: Partial<ImportJobView>): ImportJobView => ({
  id: "j1",
  name: "export.zip",
  phase: "reading",
  done: 50,
  total: 100,
  percent: 50,
  elapsed_s: 3,
  result: null,
  error: "",
  ...over,
});

const flush = async () => {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
};
const text = (tree: ReactTestRenderer) => JSON.stringify(tree.toJSON());

beforeEach(() => {
  vi.useFakeTimers();
  toast.mockClear();
  const listeners: Record<string, () => void> = {};
  (globalThis as { window?: unknown }).window = {
    addEventListener: (name: string, fn: () => void) => (listeners[name] = fn),
    removeEventListener: () => undefined,
    setTimeout: (fn: () => void, ms: number) => setTimeout(fn, ms),
    clearTimeout: (id: number) => clearTimeout(id),
  };
});
afterEach(() => vi.useRealTimers());

describe("Import progress", () => {
  it("shows a running import with its percent, and says nothing when there is none", async () => {
    jobs = [];
    let tree!: ReactTestRenderer;
    act(() => {
      tree = create(createElement(ImportProgress));
    });
    await flush();
    expect(tree.toJSON()).toBeNull();
    jobs = [job({})];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16000);
    });
    expect(text(tree)).toContain("Importing export.zip");
    expect(text(tree)).toContain("50");
  });

  it("announces a finish once, with what was added", async () => {
    jobs = [job({})];
    let tree!: ReactTestRenderer;
    act(() => {
      tree = create(createElement(ImportProgress));
    });
    await flush();
    jobs = [job({ phase: "done", percent: 100, result: { ok: true } as ImportJobView["result"] })];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast.mock.calls[0][0]).toContain("10 messages added.");
    expect(text(tree)).toContain("Import finished");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(toast).toHaveBeenCalledTimes(1);
  });
});
