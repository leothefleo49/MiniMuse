import { act } from "react";
import { create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, beforeAll, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import { setLocaleSetting } from "../i18n";
import type { SkillInfo, SkillsData, Status, TimelineEvent, ToolEvent } from "../types";
import { Composer, runningStepTitle, typingHere, WorkingLine } from "./ChatScreen";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
// the composer's only window use is the quick-chat focus listener
(globalThis as Record<string, unknown>).window = {
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
};

const store = vi.hoisted(() => ({
  state: {
    draft: null,
    draftFiles: null,
    settings: { skills: { enabled: false } },
    hub: null,
    skillsVersion: 0,
  },
  draft: () => undefined,
  draftFiles: () => undefined,
  toast: () => undefined,
}));
vi.mock("../store", () => ({ useStore: () => store }));

function discs(r: ReactTestRenderer) {
  return {
    stop: r.root.findAllByProps({ "aria-label": "Stop" })[0],
    send: r.root.findAllByProps({ "aria-label": "Send" })[0],
  };
}

describe("Composer stop disc", () => {
  // the labels looked for are the English ones, whatever the machine running the suite speaks
  beforeAll(() => setLocaleSetting("en"));

  it("swaps the send disc for a stop disc while busy with an empty box", () => {
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Composer name="Muse" busy waiting={false} onSend={vi.fn(async () => undefined)} onStop={vi.fn(async () => undefined)} />);
    });
    const d = discs(r);
    expect(d.stop).toBeDefined();
    expect(d.send).toBeUndefined();
  });

  it("keeps no stop disc without an onStop", () => {
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Composer name="Muse" busy waiting={false} onSend={vi.fn(async () => undefined)} />);
    });
    const d = discs(r);
    expect(d.stop).toBeUndefined();
    expect(d.send).toBeDefined();
    expect(d.send?.props.disabled).toBe(true);
  });

  it("brings the send disc back once there is text", async () => {
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Composer name="Muse" busy waiting={false} onSend={vi.fn(async () => undefined)} onStop={vi.fn(async () => undefined)} />);
    });
    const box = r.root.findByProps({ "aria-label": "Message" });
    await act(async () => {
      box.props.onChange({ target: { value: "接着来" } });
    });
    const d = discs(r);
    expect(d.stop).toBeUndefined();
    expect(d.send).toBeDefined();
    expect(d.send?.props.disabled).toBe(false);
  });

  it("calls onStop once and holds the disc while the stop is under way", async () => {
    const onStop = vi.fn(() => new Promise<void>(() => undefined));
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Composer name="Muse" busy waiting={false} onSend={vi.fn(async () => undefined)} onStop={onStop} />);
    });
    const disc = discs(r).stop;
    await act(async () => {
      disc.props.onClick();
    });
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(discs(r).stop?.props.disabled).toBe(true);
  });
});

// The presence frame from another device says what its run is doing — the line
// under the message reads the step, and a run parked on a card reads as waiting.

function line(who: Parameters<typeof WorkingLine>[0]["who"]) {
  let r!: ReactTestRenderer;
  act(() => {
    r = create(<WorkingLine who={who} />);
  });
  return r.root.findByType("div").props.children[1].props.children;
}

describe("WorkingLine", () => {
  beforeAll(() => setLocaleSetting("en"));

  it("reads as plain working without a state", () => {
    expect(line({ thread: "main", cid: "c", device: "phone-1", device_name: "Pixel 8", at: 1 })).toBe(
      "Pixel 8 is working…",
    );
  });

  it("reads the step the run reported", () => {
    expect(
      line({ thread: "main", cid: "c", device: "phone-1", device_name: "Pixel 8", state: "working", detail: "打开携程网站", at: 1 }),
    ).toBe("Pixel 8 is working: 打开携程网站");
  });

  it("reads as waiting when the run is parked on a card", () => {
    expect(
      line({ thread: "main", cid: "c", device: "phone-1", device_name: "", state: "waiting", at: 1 }),
    ).toBe("phone-1 is waiting for you");
  });
});

// The typing dots say the runtime is writing THIS chat's first line: not one word into a
// new chat yet, or the run belongs to the chat being read. Once anything the run put here
// has landed after the person's request (a step chip, a reply, a background pass), the
// dots stop: work under way or a reply already standing is what the working line says.

describe("typingHere", () => {
  const thread = { thread: "main" };
  const busy = { state: "working", detail: "On it: book the table", thread: "main" } as Status;
  const waiting = { state: "waiting", detail: "", thread: "main" } as Status;

  const user = (text: string, extra: Record<string, unknown> = {}): TimelineEvent =>
    ({ id: "u_1", type: "user", text, ts: "", ...thread, ...extra }) as TimelineEvent;
  const step = (extra: Record<string, unknown> = {}): TimelineEvent =>
    ({ id: "e_1", type: "tool", tool: "tracker", summary: "Logging dinner", args: {}, status: "ok", ts: "", ...thread, ...extra }) as TimelineEvent;
  const reply = (extra: Record<string, unknown> = {}): TimelineEvent =>
    ({ id: "a_1", type: "assistant", text: "Done.", ts: "", ...thread, ...extra }) as TimelineEvent;

  it("shows while this thread runs on a bare request", () => {
    expect(typingHere(true, busy, "t_new", [user("Book the table")])).toBe(true);
  });

  it("shows when the running status names this thread", () => {
    expect(typingHere(false, busy, "main", [])).toBe(true);
  });

  it("stays off in a chat the run does not belong to, new ones included", () => {
    expect(typingHere(false, busy, "t_new", [])).toBe(false);
  });

  it("counts an unnamed working status as this chat", () => {
    const here = { ...busy, thread: "" } as Status;
    expect(typingHere(false, here, "t_new", [])).toBe(true);
  });

  it("stays off while the run waits on the person", () => {
    expect(typingHere(false, waiting, "main", [user("Book the table")])).toBe(false);
  });

  it("stops once a step has come in after the request, even one already done", () => {
    expect(typingHere(true, busy, "main", [user("Book the table"), step({ status: "running" })])).toBe(false);
    expect(typingHere(true, busy, "main", [user("Book the table"), step()])).toBe(false);
  });

  it("stops once a reply is on the screen even when the run keeps going", () => {
    expect(typingHere(true, busy, "main", [user("Book the table"), step(), reply()])).toBe(false);
  });

  it("stops while a background pass works in this chat", () => {
    expect(typingHere(true, busy, "main", [user("Book the table"), step({ source: "background" })])).toBe(false);
  });

  it("shows again from the moment the next request is the last word", () => {
    expect(typingHere(true, busy, "main", [reply(), user("And book a taxi too")])).toBe(true);
  });
});

// The "On it" line under the name names the tool step running right now, or says
// "Thinking" between steps. It never echoes the person's own words back at them.

describe("runningStepTitle", () => {
  const user = (text: string): TimelineEvent => ({ id: "u_1", type: "user", text, ts: "", thread: "main" }) as TimelineEvent;
  const step = (over: Partial<ToolEvent>): TimelineEvent =>
    ({ id: "e_1", type: "tool", tool: "tracker", summary: "", args: {}, status: "ok", ts: "", thread: "main", ...over }) as TimelineEvent;

  it("names the running step, in its own words", () => {
    expect(runningStepTitle([user("log my dinner"), step({ status: "running", title: "Logging dinner in the tracker" })])).toBe("Logging dinner in the tracker");
  });

  it("is empty while the model thinks, with no tool running", () => {
    expect(runningStepTitle([user("log my dinner"), step({ summary: "Updated the food log for today" })])).toBe("");
  });

  it("is empty when the newest step finished", () => {
    expect(runningStepTitle([user("book a table"), step({ status: "ok", title: "Booked the table" })])).toBe("");
  });

  it("only counts steps after the last request", () => {
    expect(runningStepTitle([step({ status: "running", title: "Old step" }), user("book a table")])).toBe("");
  });

  it("uses the tool's name when a running step has no words", () => {
    expect(runningStepTitle([user("log it"), step({ status: "running", title: "" })])).toBe("tracker");
  });
});

// The "/" menu lists the skills; its refresh rides the version the server bumps on
// every save, so two answers can be in flight at once and a refresh can also fail.

const skill = (name: string): SkillInfo => ({
  name,
  description: `What ${name} does`,
  source: "built-in",
  enabled: true,
  path: `skills/${name}`,
  files: [],
  allowed_tools: [],
  metadata: {},
  updated_at: null,
});

const skillsData = (names: string[]): SkillsData => ({
  count: names.length,
  built_in: names.length,
  yours: 0,
  dir: "skills",
  errors: {},
  skills: names.map(skill),
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function allText(r: ReactTestRenderer): string {
  const walk = (node: unknown): string => {
    if (typeof node === "string") return node;
    if (Array.isArray(node)) return node.map(walk).join(" ");
    if (node && typeof node === "object" && "children" in node) {
      return walk((node as { children: unknown }).children);
    }
    return "";
  };
  return walk(r.toJSON());
}

async function menuAfterTyping(r: ReactTestRenderer, value: string) {
  const box = r.root.findByProps({ "aria-label": "Message" });
  await act(async () => {
    box.props.onChange({ target: { value } });
  });
  return allText(r);
}

describe("Composer slash menu skills", () => {
  let menu: ReactTestRenderer | undefined;
  beforeEach(() => {
    setLocaleSetting("en");
    store.state = {
      ...store.state,
      settings: { skills: { enabled: true } },
      skillsVersion: 0,
    };
  });

  afterEach(() => {
    if (menu) act(() => menu?.unmount());
    menu = undefined;
    store.state = {
      ...store.state,
      settings: { skills: { enabled: false } },
      skillsVersion: 0,
    };
    vi.restoreAllMocks();
  });

  it("a slower earlier answer does not land over a newer one", async () => {
    const first = deferred<SkillsData>();
    const second = deferred<SkillsData>();
    vi.spyOn(api, "skills")
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
      menu = r;
    });
    store.state = { ...store.state, skillsVersion: 1 };
    await act(async () => {
      r.update(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
    });
    await act(async () => {
      second.resolve(skillsData(["fresh-skill"]));
    });
    expect(await menuAfterTyping(r, "/")).toContain("fresh-skill");
    await act(async () => {
      first.resolve(skillsData(["stale-skill"]));
    });
    const text = await menuAfterTyping(r, "/");
    expect(text).toContain("fresh-skill");
    expect(text).not.toContain("stale-skill");
    expect(api.skills).toHaveBeenCalledTimes(2);
  });

  it("keeps its list when a refresh fails", async () => {
    vi.spyOn(api, "skills")
      .mockResolvedValueOnce(skillsData(["kept-skill"]))
      .mockRejectedValueOnce(new Error("offline"));
    let r!: ReactTestRenderer;
    act(() => {
      r = create(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
      menu = r;
    });
    expect(await menuAfterTyping(r, "/")).toContain("kept-skill");
    store.state = { ...store.state, skillsVersion: 1 };
    await act(async () => {
      r.update(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
    });
    const text = await menuAfterTyping(r, "/");
    expect(text).toContain("kept-skill");
  });

  it("hides cached skills immediately when disabled and ignores a late refresh", async () => {
    const pending = deferred<SkillsData>();
    vi.spyOn(api, "skills")
      .mockResolvedValueOnce(skillsData(["cached-skill"]))
      .mockImplementationOnce(() => pending.promise);
    await act(async () => {
      menu = create(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
    });
    expect(await menuAfterTyping(menu!, "/")).toContain("cached-skill");
    store.state = { ...store.state, skillsVersion: 1 };
    await act(async () => {
      menu!.update(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
    });
    store.state = { ...store.state, settings: { skills: { enabled: false } } };
    await act(async () => {
      menu!.update(<Composer name="Muse" busy={false} waiting={false} onSend={vi.fn(async () => undefined)} />);
    });
    expect(allText(menu!)).not.toContain("cached-skill");
    await act(async () => { pending.resolve(skillsData(["late-skill"])); });
    expect(allText(menu!)).not.toContain("late-skill");
    expect(menu!.root.findAllByProps({ role: "listbox", "aria-label": "Skills" })).toHaveLength(0);
    const preventDefault = vi.fn();
    act(() => menu!.root.findByProps({ "aria-label": "Message" }).props.onKeyDown({ key: "Tab", preventDefault }));
    expect(preventDefault).not.toHaveBeenCalled();
  });
});
