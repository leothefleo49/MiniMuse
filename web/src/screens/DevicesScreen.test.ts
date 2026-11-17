import { describe, expect, it } from "vitest";
import { normalizeWindows, onlyUnlistedPhones, sameDeviceName } from "./DevicesScreen";

// "Something interrupted the screen — Cannot read properties of undefined (reading 'toLowerCase')"
describe("Devices screen never crashes on a missing name", () => {
  it("compares names case-insensitively", () => {
    expect(sameDeviceName("Pixel", "pixel")).toBe(true);
    expect(sameDeviceName("Pixel", "Galaxy")).toBe(false);
  });

  it("treats a missing name as an empty one instead of throwing", () => {
    expect(() => sameDeviceName(undefined, "x")).not.toThrow();
    expect(() => sameDeviceName(null, undefined)).not.toThrow();
    expect(sameDeviceName(undefined, null)).toBe(true);
    expect(sameDeviceName(undefined, "x")).toBe(false);
  });

  it("lists a directly-connected phone with no name", () => {
    const phones = [{ id: "a", name: undefined as unknown as string }, { id: "b", name: "Galaxy" }];
    const listed = [{ name: undefined as unknown as string }, { name: "galaxy" }];
    // a nameless phone only matches a nameless listed device; a named one matches case-insensitively
    expect(onlyUnlistedPhones(phones, listed)).toEqual([]);
    expect(onlyUnlistedPhones(phones, [{ name: "Other" }]).map((p) => p.id)).toEqual(["a", "b"]);
    expect(onlyUnlistedPhones(phones, [{ name: "GALAXY" }]).map((p) => p.id)).toEqual(["a"]);
  });
});

describe("normalizeWindows", () => {
  it("accepts the runtime's current shape { id, app, bundle_id, title }", () => {
    const out = normalizeWindows([{ id: 42, app: "Safari", bundle_id: "com.apple.Safari", title: "Home" }]);
    expect(out).toEqual([{ window_id: 42, owner: "Safari", app_id: "com.apple.Safari", title: "Home" }]);
  });

  it("accepts the older shape { window_id, owner, app_id, title }", () => {
    const out = normalizeWindows([{ window_id: 7, owner: "Notes", app_id: "com.apple.Notes", title: "Todo" }]);
    expect(out).toEqual([{ window_id: 7, owner: "Notes", app_id: "com.apple.Notes", title: "Todo" }]);
  });

  it("never yields an entry without an owner (the old crash)", () => {
    const out = normalizeWindows([{ id: 1, title: "no app name" }, null, "junk", { owner: "Ok" }]);
    expect(out.map((w) => w.owner)).toEqual(["Ok"]);
    for (const w of out) expect(() => w.owner.toLowerCase()).not.toThrow();
  });

  it("returns an empty list for anything that is not a list", () => {
    expect(normalizeWindows(undefined)).toEqual([]);
    expect(normalizeWindows({})).toEqual([]);
    expect(normalizeWindows("x")).toEqual([]);
  });
});
