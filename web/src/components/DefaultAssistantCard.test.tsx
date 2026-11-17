import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { beforeEach, describe, expect, it, vi } from "vitest";

type Bridge = { assistantState?: () => string; openAssistantSettings?: () => void } | null;
let bridge: Bridge = null;
vi.mock("../android", () => ({ androidApp: () => bridge }));

import { DefaultAssistantCard } from "./DefaultAssistantCard";

const render = (): ReactTestRenderer => {
  let tree!: ReactTestRenderer;
  act(() => {
    tree = create(createElement(DefaultAssistantCard));
  });
  return tree;
};
const text = (tree: ReactTestRenderer) => JSON.stringify(tree.toJSON());
const buttons = (tree: ReactTestRenderer) => tree.root.findAll((n) => n.type === "button");

beforeEach(() => {
  bridge = null;
  // the card listens for the window coming back into focus
  (globalThis as { window?: unknown }).window = { addEventListener: () => undefined, removeEventListener: () => undefined };
});

describe("Default assistant card", () => {
  it("on a phone that has not chosen MiniMuse: says so and takes the person to Android's page", () => {
    const open = vi.fn();
    bridge = { assistantState: () => "off", openAssistantSettings: open };
    const tree = render();
    const shown = text(tree);
    expect(shown).toContain("This phone");
    expect(shown).toContain("Not yet");
    expect(shown).toContain("Default digital assistant app");
    const choose = buttons(tree).find((b) => JSON.stringify(b.children).includes("Choose MiniMuse"));
    expect(choose).toBeDefined();
    act(() => choose!.props.onClick());
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("on a phone where MiniMuse is already the assistant: says so and offers no button", () => {
    bridge = { assistantState: () => "on", openAssistantSettings: vi.fn() };
    const tree = render();
    expect(text(tree)).toContain("MiniMuse is your default assistant");
    expect(buttons(tree)).toHaveLength(0);
  });

  it("does not claim a state it cannot read", () => {
    bridge = { assistantState: () => "unknown", openAssistantSettings: vi.fn() };
    expect(text(render())).toContain("Not sure");
  });

  it("always tells how to set the watch up, and is honest about the computer", () => {
    bridge = null;
    const shown = text(render());
    expect(shown).toContain("Your watch");
    expect(shown).toContain("Assistant or Default apps");
    expect(shown).toContain("Windows does not let an app replace its built-in assistant");
    expect(shown).not.toContain("This phone");
  });
});
