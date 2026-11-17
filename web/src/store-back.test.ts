import { describe, expect, it } from "vitest";
import { initial, reducer } from "./store";

describe("the back arrow", () => {
  it("returns to the screen before, not to the chat", () => {
    let s = reducer(initial, { type: "tab", tab: "goals" });
    s = reducer(s, { type: "tab", tab: "library" });
    s = reducer(s, { type: "tabBack" });
    expect(s.tab).toBe("goals");
    s = reducer(s, { type: "tabBack" });
    expect(s.tab).toBe("chat");
    s = reducer(s, { type: "tabBack" });
    expect(s.tab).toBe("chat");
  });

  it("does not stack the same screen twice and keeps the history short", () => {
    let s = reducer(initial, { type: "tab", tab: "feed" });
    s = reducer(s, { type: "tab", tab: "feed" });
    expect(s.tabHistory).toEqual(["chat"]);
    for (let i = 0; i < 60; i++) s = reducer(s, { type: "tab", tab: i % 2 ? "feed" : "ideas" });
    expect(s.tabHistory.length).toBeLessThanOrEqual(20);
  });
});

describe("setup", () => {
  it("can be opened again after it was finished, and closes when done", () => {
    let s = reducer(initial, { type: "onboardingDismissed" });
    s = reducer(s, { type: "setupReopen" });
    expect(s.setupReopened).toBe(true);
    expect(s.onboardingDismissed).toBe(false);
    s = reducer(s, { type: "onboardingDismissed" });
    expect(s.setupReopened).toBe(false);
  });
});
