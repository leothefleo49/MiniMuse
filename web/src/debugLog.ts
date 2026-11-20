import { useSyncExternalStore } from "react";

export type DebugCategory = "ui" | "api" | "ws" | "ai" | "nav" | "error" | "system";

export interface DebugLogEntry {
  id: number;
  ts: string;
  cat: DebugCategory;
  summary: string;
  detail?: Record<string, unknown> | string;
}

const STORAGE_FLAG = "nm.fullDebugLog";
const STORAGE_ENTRIES = "nm.fullDebugLog.entries";
const MAX_ENTRIES = 2500;
const SESSION_PERSIST_LIMIT = 400;

let enabled = false;
let entries: DebugLogEntry[] = [];
let nextId = 1;
let installed = false;
const listeners = new Set<() => void>();

function notify() {
  for (const fn of listeners) {
    try {
      fn();
    } catch {
      /* ignore listener errors */
    }
  }
}

function loadInitial() {
  if (typeof window === "undefined") return;
  try {
    enabled = window.localStorage.getItem(STORAGE_FLAG) === "1";
    const saved = window.sessionStorage.getItem(STORAGE_ENTRIES);
    if (saved) {
      const parsed = JSON.parse(saved) as DebugLogEntry[];
      if (Array.isArray(parsed)) {
        entries = parsed.slice(-MAX_ENTRIES);
        nextId = (entries[entries.length - 1]?.id ?? 0) + 1;
      }
    }
  } catch {
    /* ignore storage restrictions */
  }
}

loadInitial();

let persistTimer: number | null = null;
function schedulePersist() {
  if (typeof window === "undefined" || persistTimer !== null) return;
  persistTimer = window.setTimeout(() => {
    persistTimer = null;
    try {
      window.sessionStorage.setItem(STORAGE_ENTRIES, JSON.stringify(entries.slice(-SESSION_PERSIST_LIMIT)));
    } catch {
      /* ignore quota errors */
    }
  }, 400);
}

export function isFullDebugLogEnabled(): boolean {
  return enabled;
}

export function setFullDebugLog(on: boolean): void {
  const next = Boolean(on);
  if (next === enabled) return;
  enabled = next;
  try {
    window.localStorage.setItem(STORAGE_FLAG, next ? "1" : "0");
  } catch {
    /* ignore */
  }
  if (next) {
    initDebugLogCapture();
    recordDebugEvent("system", "Full diagnostic logging enabled", {
      url: typeof window !== "undefined" ? window.location.href : "",
      ua: typeof navigator !== "undefined" ? navigator.userAgent : "",
    });
  } else {
    notify();
  }
}

export function recordDebugEvent(
  cat: DebugCategory,
  summary: string,
  detail?: Record<string, unknown> | string,
): void {
  if (!enabled && cat !== "error") return;
  if (!enabled) return;
  const entry: DebugLogEntry = {
    id: nextId++,
    ts: new Date().toISOString(),
    cat,
    summary: String(summary).slice(0, 320),
    ...(detail !== undefined ? { detail } : {}),
  };
  entries = entries.length >= MAX_ENTRIES ? [...entries.slice(-(MAX_ENTRIES - 1)), entry] : [...entries, entry];
  schedulePersist();
  notify();
}

export function clearDebugLog(): void {
  entries = [];
  try {
    window.sessionStorage.removeItem(STORAGE_ENTRIES);
  } catch {
    /* ignore */
  }
  notify();
}

export function getDebugLogEntries(): DebugLogEntry[] {
  return entries;
}

export function useFullDebugLog(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => enabled,
    () => false,
  );
}

export function useDebugLogEntries(): DebugLogEntry[] {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => entries,
    () => [],
  );
}

function describeElement(el: Element | null): string {
  if (!el) return "unknown";
  const interactive = el.closest("button, a, input, select, textarea, [role='button'], [role='switch'], [role='tab'], summary") || el;
  const tag = interactive.tagName.toLowerCase();
  const role = interactive.getAttribute("role");
  const aria = interactive.getAttribute("aria-label") || interactive.getAttribute("title") || "";
  const testid = interactive.getAttribute("data-testid") || "";
  const id = interactive.id ? `#${interactive.id}` : "";
  let label = aria.trim();
  if (!label && tag !== "input" && tag !== "textarea") {
    label = (interactive.textContent || "").replace(/\s+/g, " ").trim().slice(0, 64);
  }
  if (!label && (tag === "input" || tag === "textarea")) {
    label = interactive.getAttribute("placeholder") || interactive.getAttribute("name") || interactive.getAttribute("type") || "";
  }
  const bits = [tag + id];
  if (role) bits.push(`[role=${role}]`);
  if (testid) bits.push(`[testid=${testid}]`);
  if (label) bits.push(`"${label}"`);
  return bits.join(" ");
}

export function initDebugLogCapture(): void {
  if (installed || typeof window === "undefined" || typeof document === "undefined") return;
  installed = true;

  document.addEventListener(
    "click",
    (e) => {
      if (!enabled) return;
      const target = e.target instanceof Element ? e.target : null;
      const desc = describeElement(target);
      const checked = target?.closest("[role='switch']")?.getAttribute("aria-checked");
      recordDebugEvent("ui", `click ${desc}`, checked !== null && checked !== undefined ? { aria_checked: checked } : undefined);
    },
    true,
  );

  document.addEventListener(
    "change",
    (e) => {
      if (!enabled) return;
      const target = e.target instanceof Element ? e.target : null;
      const desc = describeElement(target);
      let val: string | boolean | undefined;
      if (target instanceof HTMLInputElement) {
        if (target.type === "password") val = "[redacted]";
        else if (target.type === "checkbox" || target.type === "radio") val = target.checked;
        else if (target.type === "file") val = target.files?.[0]?.name || "file";
        else val = target.value.slice(0, 80);
      } else if (target instanceof HTMLSelectElement) {
        val = target.value.slice(0, 80);
      } else if (target instanceof HTMLTextAreaElement) {
        val = `(${target.value.length} chars)`;
      }
      recordDebugEvent("ui", `change ${desc}`, val !== undefined ? { value: val } : undefined);
    },
    true,
  );

  window.addEventListener("hashchange", () => {
    if (!enabled) return;
    recordDebugEvent("nav", `hash -> ${window.location.hash || "(empty)"}`);
  });

  window.addEventListener("error", (ev) => {
    if (!enabled) return;
    recordDebugEvent("error", `window.error: ${ev.message || "Uncaught error"}`, {
      file: ev.filename,
      line: ev.lineno,
      col: ev.colno,
    });
  });

  window.addEventListener("unhandledrejection", (ev) => {
    if (!enabled) return;
    const reason = ev.reason instanceof Error ? `${ev.reason.name}: ${ev.reason.message}` : String(ev.reason);
    recordDebugEvent("error", `unhandledrejection: ${reason}`);
  });

  const origError = console.error.bind(console);
  console.error = (...args: unknown[]) => {
    if (enabled) {
      recordDebugEvent(
        "error",
        `console.error: ${args.map((a) => (a instanceof Error ? a.message : typeof a === "string" ? a : JSON.stringify(a) || String(a))).join(" ").slice(0, 260)}`,
      );
    }
    origError(...args);
  };

  const origWarn = console.warn.bind(console);
  console.warn = (...args: unknown[]) => {
    if (enabled) {
      recordDebugEvent(
        "error",
        `console.warn: ${args.map((a) => (typeof a === "string" ? a : JSON.stringify(a) || String(a))).join(" ").slice(0, 260)}`,
      );
    }
    origWarn(...args);
  };
}

export function exportDebugLogJson(backendSummary?: string): string {
  const payload = {
    exported_at: new Date().toISOString(),
    origin: typeof window !== "undefined" ? window.location.origin : "",
    user_agent: typeof navigator !== "undefined" ? navigator.userAgent : "",
    viewport: typeof window !== "undefined" ? `${window.innerWidth}x${window.innerHeight}` : "",
    total_events: entries.length,
    events: entries,
    ...(backendSummary ? { backend_diagnostics: backendSummary } : {}),
  };
  return JSON.stringify(payload, null, 2);
}

export function exportDebugLogText(backendSummary?: string): string {
  const lines = [
    `# MiniMuse Full Diagnostic Log`,
    `Exported: ${new Date().toISOString()}`,
    `Origin: ${typeof window !== "undefined" ? window.location.origin : ""}`,
    `User-Agent: ${typeof navigator !== "undefined" ? navigator.userAgent : ""}`,
    `Events: ${entries.length}`,
    "",
    ...entries.map((e) => {
      const det = e.detail ? ` | ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}` : "";
      return `[${e.ts}] [${e.cat.toUpperCase()}] ${e.summary}${det}`;
    }),
  ];
  if (backendSummary) {
    lines.push("", "=== BACKEND DIAGNOSTICS ===", backendSummary);
  }
  return lines.join("\n");
}
