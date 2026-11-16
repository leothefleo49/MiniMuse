import { act } from "react";
import { create, type ReactTestRenderer } from "react-test-renderer";
import { beforeAll, describe, expect, it } from "vitest";
import { setLocaleSetting } from "../i18n";
import type { UserEvent } from "../types";
import { UserBubble } from "./ChatScreen";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const user = (extra: Partial<UserEvent> = {}): UserEvent =>
  ({ id: "u_1", type: "user", ts: "2026-01-01T00:00:00Z", thread: "main", text: "I got the job!", ...extra }) as UserEvent;

function render(event: UserEvent): ReactTestRenderer {
  let r!: ReactTestRenderer;
  act(() => {
    r = create(<UserBubble event={event} onOpenFile={() => undefined} />);
  });
  return r;
}

/** The chips only: their class is what marks them apart from the bubble's text. */
function chips(r: ReactTestRenderer): string[] {
  return r.root
    .findAll((n) => typeof n.props.className === "string" && n.props.className.includes("react-chip"), { deep: false })
    .map((n) => String(n.children.join("")));
}

describe("the agent's reactions on a message", () => {
  beforeAll(() => setLocaleSetting("en"));

  it("shows no chips without a reaction", () => {
    expect(chips(render(user()))).toEqual([]);
    expect(chips(render(user({ reactions: [] })))).toEqual([]);
  });

  it("shows each emoji as its own chip", () => {
    expect(chips(render(user({ reactions: ["🔥", "✨", "💜"] })))).toEqual(["🔥", "✨", "💜"]);
  });

  it("never shows more than three, whatever arrives", () => {
    const many = render(user({ reactions: ["🔥", "✨", "💜", "🎉"] }));
    expect(chips(many)).toHaveLength(3);
  });

  it("labels the row for screen readers", () => {
    const r = render(user({ reactions: ["🔥"] }));
    const row = r.root.findAll((n) => typeof n.props["aria-label"] === "string" && String(n.props["aria-label"]).startsWith("MiniMuse reacted"))[0];
    expect(row).toBeDefined();
    expect(row.props.title).toBe("MiniMuse reacted");
  });
});
