import { describe, expect, it } from "vitest";
import { upsertEvent } from "./store";
import type { TimelineEvent } from "./types";

function user(id: string, ts: string, extra: Partial<TimelineEvent> = {}): TimelineEvent {
  return { id, ts, thread: "main", type: "user", text: id, ...extra } as TimelineEvent;
}

function assistant(id: string, ts: string, extra: Partial<TimelineEvent> = {}): TimelineEvent {
  return { id, ts, thread: "main", type: "assistant", text: id, ...extra } as TimelineEvent;
}

// Ordering guarantee: when a message arrives while the assistant is still typing,
// events land by arrival order so a message written after an earlier bubble stays
// below it; only rows pulled from another device re-sort by their timestamps.
describe("upsertEvent ordering", () => {
  it("puts an optimistic user message at the bottom, below an earlier assistant event", () => {
    const list = [assistant("a1", "2026-11-08T18:11:00.000Z")];
    const next = upsertEvent(list, user("u1", "2026-11-08T18:12:00.000Z"));
    expect(next.map((e) => e.id)).toEqual(["a1", "u1"]);
  });

  it("keeps the reply to the new message below it (arrival order, not timestamps)", () => {
    const list = [assistant("a1", "2026-11-08T18:11:00.000Z")];
    const withUser = upsertEvent(list, user("u1", "2026-11-08T18:12:00.000Z"));
    // the interrupted run's partial text arrives with an older timestamp than the
    // user's message; it is a local event, so it must still land below it
    const partial = assistant("a2", "2026-11-08T18:11:40.000Z");
    const next = upsertEvent(withUser, partial);
    expect(next.map((e) => e.id)).toEqual(["a1", "u1", "a2"]);
  });

  it("replaces the optimistic message when the server echoes the same id", () => {
    const list = [user("u1", "2026-11-08T18:12:00.000Z", { pending: true })];
    const next = upsertEvent(list, user("u1", "2026-11-08T18:12:00.000Z"));
    expect(next).toHaveLength(1);
    expect(next[0].pending).toBeUndefined();
  });

  it("inserts a synced row by its timestamp when it is older than what is on screen", () => {
    const list = [assistant("a1", "2026-11-08T18:13:00.000Z")];
    const pulled = user("u0", "2026-11-08T17:00:00.000Z", { synced: true });
    const next = upsertEvent(list, pulled);
    expect(next.map((e) => e.id)).toEqual(["u0", "a1"]);
  });
});
