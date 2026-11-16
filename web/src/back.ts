import { useEffect, useRef } from "react";

/**
 * The back gesture behaves like a phone app's: it closes what is open on top first — a
 * sheet, the chats drawer, the file viewer, a menu — and only a second press with nothing
 * open leaves the app. Built on history: one guard entry is pushed at startup, so the
 * first back press is ours to consume, and a fresh guard is pushed every time one is.
 *
 * Screens register a "closable" while they have something open; the topmost one answers
 * true when it closed something. Nothing here traps the person: with nothing registered,
 * back behaves as the browser would.
 */

interface Entry {
  id: string;
  close: () => boolean;
}

const stack: Entry[] = [];
let armed = false;
let exitArmedUntil = 0;
let lastClosedAt = 0;
let guards = 0;

export function registerClosable(id: string, close: () => boolean): () => void {
  const entry: Entry = { id, close };
  // Opening any menu/sheet/subscreen disarms any pending exit prompt
  exitArmedUntil = 0;
  stack.push(entry);
  return () => {
    const i = stack.indexOf(entry);
    if (i >= 0) {
      stack.splice(i, 1);
      // Closing a menu/sheet via UI tap also disarms any pending exit prompt
      exitArmedUntil = 0;
      lastClosedAt = Date.now();
    }
  };
}

/** Registers while `active`: the hook the open-on-top screens use. */
export function useClosable(id: string, close: () => boolean, active: boolean): void {
  const latest = useRef(close);
  useEffect(() => {
    latest.current = close;
  }, [close]);
  useEffect(() => {
    if (!active) return;
    return registerClosable(id, () => latest.current());
  }, [id, active]);
}

export function initBackHandler(): void {
  if (armed || typeof history === "undefined") return;
  armed = true;
  // Two guard entries at startup: even if a stray back event slips through (a gesture
  // finishing while the page is busy), one entry is left and the app does not close.
  try {
    history.replaceState({ nm: "root" }, "");
    history.pushState({ nm: "guard" }, "");
    history.pushState({ nm: "guard" }, "");
    guards = 2;
  } catch {
    return;
  }
  window.addEventListener("popstate", () => {
    guards = Math.max(0, guards - 1);
    let handled = false;
    for (let i = stack.length - 1; i >= 0; i--) {
      try {
        if (stack[i].close() === true) {
          handled = true;
          break;
        }
      } catch {
        // continue to parent closable
      }
    }
    if (handled) {
      // something closed: back behaved like a phone's. Explicitly disarm exit so a swipe
      // from inside a menu/popup NEVER counts toward the 2-swipe exit gesture.
      exitArmedUntil = 0;
      lastClosedAt = Date.now();
      pushGuard(false);
      return;
    }
    // If a menu/popup just closed within the last 400ms (e.g. rapid double-swipe while
    // dismissing a modal), absorb without arming exit.
    if (Date.now() - lastClosedAt < 400) {
      exitArmedUntil = 0;
      pushGuard(false);
      return;
    }
    if (!exitGuardArmed()) {
      // nothing open, first press at root: ask once and stay put
      pushGuard(true);
      return;
    }
    // nothing open and this is the second press at root inside two seconds: leave for real,
    // spending the guard entries we pushed (the app closes, or the browser goes back)
    exitArmedUntil = 0;
    try {
      history.go(-(guards + 1));
    } catch {
      /* a shorter history than we thought: the press stays a normal one */
    }
    guards = 0;
  });

  function exitGuardArmed(): boolean {
    const now = Date.now();
    if (now >= exitArmedUntil) return false;
    exitArmedUntil = 0;
    window.dispatchEvent(new CustomEvent("nanomuse:back-exit"));
    return true;
  }

  function pushGuard(arm: boolean): void {
    if (arm) {
      exitArmedUntil = Date.now() + 2000;
      // one press with nothing open only asks: the app says so, and leaves on the second
      window.dispatchEvent(new CustomEvent("nanomuse:back-hint"));
    } else {
      exitArmedUntil = 0;
    }
    if (guards >= 3) return; // history stays shallow whatever the press rate
    try {
      history.pushState({ nm: "guard" }, "");
      guards += 1;
    } catch {
      /* no history budget left: the next press is the browser's own */
    }
  }
}
