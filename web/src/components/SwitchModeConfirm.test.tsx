import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { beforeEach, describe, expect, it, vi } from "vitest";

const disconnect = vi.fn();
vi.mock("../android", () => ({ androidApp: () => ({ disconnect }) }));
// the real dialog needs a DOM for its focus trap; this stand-in exposes the props it was given
vi.mock("./ConfirmDialog", () => ({
  ConfirmDialog: (p: { open: boolean; title: string; body: string; confirmLabel?: string; onConfirm: () => void; onCancel: () => void }) =>
    p.open
      ? createElement(
          "div",
          null,
          createElement("h3", null, p.title),
          createElement("p", null, p.body),
          createElement("button", { onClick: p.onCancel }, "Cancel"),
          createElement("button", { onClick: p.onConfirm }, p.confirmLabel),
        )
      : null,
}));

import { SwitchModeConfirm } from "./SwitchModeConfirm";

const text = (tree: ReactTestRenderer) => JSON.stringify(tree.toJSON());
const button = (tree: ReactTestRenderer, label: string) =>
  tree.root.findAll((n) => n.type === "button" && JSON.stringify(n.children).includes(label))[0];

beforeEach(() => disconnect.mockClear());

describe("Switch mode asks before it does anything", () => {
  it("shows nothing and does nothing while closed", () => {
    let tree!: ReactTestRenderer;
    act(() => {
      tree = create(createElement(SwitchModeConfirm, { open: false, onCancel: () => undefined }));
    });
    expect(text(tree)).toBe("null");
    expect(disconnect).not.toHaveBeenCalled();
  });

  it("says what will happen, and leaves the connection alone until it is confirmed", () => {
    const onCancel = vi.fn();
    let tree!: ReactTestRenderer;
    act(() => {
      tree = create(createElement(SwitchModeConfirm, { open: true, onCancel }));
    });
    const shown = text(tree);
    expect(shown).toContain("Switch between phone and computer?");
    expect(shown).toContain("go back to its start screen");
    expect(shown).toContain("Nothing is deleted");
    expect(disconnect).not.toHaveBeenCalled();

    act(() => {
      button(tree, "Cancel").props.onClick();
    });
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(disconnect).not.toHaveBeenCalled();
  });

  it("switches only after the person confirms, and closes the dialog as it does", () => {
    const onCancel = vi.fn();
    let tree!: ReactTestRenderer;
    act(() => {
      tree = create(createElement(SwitchModeConfirm, { open: true, onCancel }));
    });
    act(() => {
      button(tree, "Switch mode").props.onClick();
    });
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
