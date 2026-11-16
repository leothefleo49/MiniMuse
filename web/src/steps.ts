/**
 * "Show the agent's steps": every tool the agent uses becomes a chip in the chat. Off
 * by default since the Nov 9, 2026 request; on shows them — approvals, questions,
 * files and the browser view are not steps and always show. Kept on this device
 * like the theme (`nm.show_steps`, "1"/"0"); only an explicit "1" turns them on.
 */
import { useSyncExternalStore } from "react";

const STORAGE_KEY = "nm.show_steps";

function load(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

let on = load();
const listeners = new Set<() => void>();

export function showSteps(): boolean {
  return on;
}

export function setShowSteps(next: boolean): void {
  on = next;
  try {
    localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
  } catch {
    // fine — the choice lasts for this page then
  }
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useShowSteps(): boolean {
  return useSyncExternalStore(subscribe, showSteps, showSteps);
}
