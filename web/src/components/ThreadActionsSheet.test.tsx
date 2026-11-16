import { act } from "react";
import type { ReactTestInstance } from "react-test-renderer";
import { create, type ReactTestRenderer } from "react-test-renderer";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { setLocaleSetting } from "../i18n";
import type { ThreadMeta } from "../types";
import { longPressHandlers, ThreadActionsSheet } from "./ThreadActionsSheet";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// the sheet's own window use is the Escape close of the Sheet base; the hold helpers run
// on timers this suite hands out and collects, so a hold stays a hold here
const held: Array<{ cb: () => void; ms: number }> = [];
const cleared: number[] = [];
(globalThis as Record<string, unknown>).window = {
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  setTimeout: (cb: () => void, ms: number) => {
    held.push({ cb, ms });
    return held.length;
  },
  // a cleared timer is gone for good, as in a browser: it can never fire afterwards
  clearTimeout: (id: number) => {
    const i = id - 1;
    if (held[i]) {
      held.splice(i, 1);
      cleared.push(id);
    }
  },
};
(globalThis as Record<string, unknown>).document = {
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  visibilityState: "visible",
  // applyLang writes the <html lang> here when the locale changes
  documentElement: {},
};

const sideChat: ThreadMeta = {
  id: "t_1",
  title: "Trip to Kyoto",
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-09T00:00:00Z",
  events: 4,
} as ThreadMeta;
const mainChat: ThreadMeta = { ...sideChat, id: "main", title: "Main chat" };

function sheet(thread: ThreadMeta, mode: Parameters<typeof ThreadActionsSheet>[0]["initialMode"]) {
  let r!: ReactTestRenderer;
  const props = { onRename: vi.fn(), onClear: vi.fn(), onDelete: vi.fn(), onClose: vi.fn() };
  act(() => {
    r = create(<ThreadActionsSheet thread={thread} initialMode={mode} {...props} />);
  });
  return { r, ...props };
}

function button(r: ReactTestRenderer, label: string) {
  return r.root
    .findAllByType("button")
    .find((b) => textOf(b).includes(label));
}

/** The visible text of a button, however deep the label element sits. */
function textOf(node: ReactTestInstance): string {
  return node.children
    .map((c) => (typeof c === "string" ? c : typeof c === "object" && "props" in c ? textOf(c as ReactTestInstance) : ""))
    .join("");
}

// Hold the name of a chat and the chat's actions open, with the name on the sheet; both
// destructive steps ask once more before they do anything.

describe("ThreadActionsSheet", () => {
  beforeAll(() => setLocaleSetting("en"));

  it("opens on a rename, a clear and a move-to-trash for a side chat, and the trash step is not a tap there", () => {
    const { r, onDelete } = sheet(sideChat, "menu");
    for (const label of ["Rename", "Clear the history", "Move to Trash"]) {
      expect(button(r, label)).toBeTruthy();
    }
    act(() => {
      button(r, "Move to Trash")!.props.onClick();
    });
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("moves to trash at the second step only, naming the chat and 365-day recovery", () => {
    const { r, onDelete } = sheet(sideChat, "delete");
    const text = String(r.root.findAllByType("p")[0].props.children);
    expect(text).toContain("Trip to Kyoto");
    expect(text).toContain("365 days");
    act(() => {
      button(r, "Move to Trash")!.props.onClick();
    });
    expect(onDelete).toHaveBeenCalledTimes(1);
  });

  it("leaves a way back out of the destructive steps", () => {
    const { r, onClear, onDelete } = sheet(sideChat, "clear");
    expect(button(r, "Keep it")).toBeTruthy();
    act(() => {
      button(r, "Keep it")!.props.onClick();
    });
    expect(onClear).not.toHaveBeenCalled();
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("clear also asks again, naming the chat", () => {
    const { r, onClear } = sheet(sideChat, "clear");
    expect(String(r.root.findAllByType("p")[0].props.children)).toContain("Trip to Kyoto");
    act(() => {
      button(r, "Clear the history")!.props.onClick();
    });
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("offers main chat rename and clear, not move to trash", () => {
    const { r, onDelete } = sheet(mainChat, "menu");
    expect(button(r, "Rename")).toBeTruthy();
    expect(button(r, "Move to Trash")).toBeFalsy();
    expect(String(r.root.findAllByType("p")[0].props.children)).toContain("rename it");
    act(() => {
      button(r, "Clear the history")!.props.onClick();
      button(r, "Clear the history")!.props.onClick();
    });
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("saves a typed name, trimmed", () => {
    const { r, onRename } = sheet(sideChat, "rename");
    const input = r.root.findByType("input");
    expect(input.props.value).toBe("Trip to Kyoto");
    act(() => {
      input.props.onChange({ target: { value: "  Kyoto week  " } });
    });
    act(() => {
      r.root.findByType("form").props.onSubmit({ preventDefault: () => undefined });
    });
    expect(onRename).toHaveBeenCalledWith("Kyoto week");
  });

  it("will not save an erased name", () => {
    const { r, onRename, onClose } = sheet(sideChat, "rename");
    const input = r.root.findByType("input");
    act(() => {
      input.props.onChange({ target: { value: "   " } });
    });
    act(() => {
      r.root.findByType("form").props.onSubmit({ preventDefault: () => undefined });
    });
    expect(onRename).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("longPressHandlers", () => {
  const onLongPress = vi.fn();
  const onClick = vi.fn();

  function fresh() {
    held.length = 0;
    cleared.length = 0;
    onLongPress.mockClear();
    onClick.mockClear();
    return longPressHandlers(onLongPress, onClick);
  }

  const ev = (x: number, y: number) => ({ clientX: x, clientY: y }) as never;

  it("opens the actions after the hold and swallows the tap right behind it", () => {
    const h = fresh();
    h.onPointerDown(ev(10, 10));
    expect(held[0].ms).toBe(450);
    held[0].cb();
    expect(onLongPress).toHaveBeenCalledTimes(1);
    const after = { preventDefault: vi.fn(), stopPropagation: vi.fn() };
    h.onClick(after as never);
    expect(after.preventDefault).toHaveBeenCalled();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("keeps the plain tap a plain tap", () => {
    const h = fresh();
    h.onPointerDown(ev(10, 10));
    const sept = { preventDefault: vi.fn(), stopPropagation: vi.fn() };
    h.onClick(sept as never);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(sept.preventDefault).not.toHaveBeenCalled();
    expect(cleared).toHaveLength(1);
  });

  it("cancels when the finger slides away, so a scroll never opens a menu", () => {
    const h = fresh();
    h.onPointerDown(ev(10, 10));
    h.onPointerMove(ev(60, 60));
    expect(cleared.length).toBe(1);
    h.onPointerUp();
    // the tap that ends the canceled press opens the chat, not the menu
    const sept = { preventDefault: vi.fn(), stopPropagation: vi.fn() };
    h.onClick(sept as never);
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(sept.preventDefault).not.toHaveBeenCalled();
  });
});
