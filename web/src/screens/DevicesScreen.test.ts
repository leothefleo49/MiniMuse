import { describe, expect, it } from "vitest";
import { countDevices, peerDisplayName, peerKind, uniqueDirectPeers } from "../devicePeers";
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

describe("unified device counts and peer deduplication", () => {
  it("counts this phone + paired PC as 2 devices across all screens", () => {
    const local = {
      computer: { id: "local:computer", name: "Galaxy S24", kind: "phone" as const, online: true, this: true },
      phones: [],
    };
    const sync = {
      enabled: true,
      available: true,
      paused: false,
      cursor: 0,
      last_pull_at: null,
      last_push_at: null,
      error: "",
      device_id: "phone-abc123",
      peers: [
        {
          id: "pc-ceff25c24442",
          name: "DESKTOP-F71C82T",
          kind: "computer",
          url: "http://192.168.1.223:8787",
          online: true,
          last_sync_at: null,
          last_error: "",
          pending: 0,
        },
      ],
    };
    expect(countDevices(null, local, sync)).toEqual({ total: 2, online: 2 });
  });

  it("deduplicates a direct peer already listed as a local phone and cleans 'localhost'", () => {
    const listed = [{ id: "local:phone:1", name: "Galaxy S24", kind: "phone" }];
    const sync = {
      enabled: true,
      available: true,
      paused: false,
      cursor: 0,
      last_pull_at: null,
      last_push_at: null,
      error: "",
      peers: [
        {
          id: "phone-1",
          name: "Galaxy S24",
          kind: "phone",
          url: "http://192.168.1.129:8787",
          online: true,
          last_sync_at: null,
          last_error: "",
          pending: 0,
        },
        {
          id: "pc-2",
          name: "localhost",
          kind: "computer",
          url: "http://192.168.1.223:8787",
          online: false,
          last_sync_at: null,
          last_error: "",
          pending: 0,
        },
      ],
    };
    const unique = uniqueDirectPeers(sync, listed);
    expect(unique.map((p) => p.id)).toEqual(["pc-2"]);
    expect(peerDisplayName(unique[0])).toBe("192.168.1.223:8787");
    expect(peerKind(unique[0])).toBe("computer");
  });
});
