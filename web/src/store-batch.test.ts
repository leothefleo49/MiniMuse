import { describe, expect, it } from "vitest";
import { initial, reducer } from "./store";
import type { WsMessage } from "./types";

describe("reducer wsBatch delta coalescing", () => {
  it("applies batched text and reasoning deltas across threads in order", () => {
    const msgs: WsMessage[] = [
      { kind: "delta", id: "s1", thread: "main", text: "Hello" },
      { kind: "delta", id: "s1", thread: "main", text: " world!" },
      { kind: "reasoning_delta", id: "s1", thread: "main", text: "step 1; " },
      { kind: "reasoning_delta", id: "s1", thread: "main", text: "step 2" },
      { kind: "delta", id: "s2", thread: "side", text: "Side reply" },
    ];
    const next = reducer(initial, { type: "wsBatch", msgs });
    expect(next.streams.main).toEqual({
      id: "s1",
      text: "Hello world!",
      reasoning: "step 1; step 2",
      ended: false,
    });
    expect(next.streams.side).toEqual({
      id: "s2",
      text: "Side reply",
      reasoning: "",
      ended: false,
    });
  });

  it("handles empty wsBatch as a no-op and resets stream buffer on new stream id", () => {
    const first = reducer(initial, {
      type: "wsBatch",
      msgs: [{ kind: "delta", id: "a", thread: "main", text: "old" }],
    });
    expect(reducer(first, { type: "wsBatch", msgs: [] })).toBe(first);

    const second = reducer(first, {
      type: "wsBatch",
      msgs: [
        { kind: "delta", id: "b", thread: "main", text: "new-" },
        { kind: "delta", id: "b", thread: "main", text: "stream" },
      ],
    });
    expect(second.streams.main?.id).toBe("b");
    expect(second.streams.main?.text).toBe("new-stream");
  });
});
