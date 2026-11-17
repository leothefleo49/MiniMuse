import { describe, expect, it } from "vitest";
import type { AndroidPermissionsStatus, KeepRunningStatus } from "./android";
import { t } from "./i18n";
import { grantedCount, missingEssentials, permissionRows, type PermInputs } from "./permissions";

const allOn: AndroidPermissionsStatus = {
  notifications: true,
  installPackages: true,
  camera: true,
  location: true,
  microphone: true,
  calendar: true,
  contacts: true,
  nearby: true,
};
const keep: KeepRunningStatus = {
  battery_unrestricted: true,
  overlay: true,
  exact_alarms: "granted",
  boot_start: true,
  vendor: "",
  autostart_settings: false,
  android: 35,
  local: true,
};
const android = (over: Partial<NonNullable<PermInputs["android"]>> = {}): PermInputs => ({
  android: { status: allOn, keep, accessibility: "on", ...over },
  browser: {},
});
const ids = (rows: { id: string }[]) => rows.map((r) => r.id);

describe("Android permissions list", () => {
  it("lists what the app can really ask for, each with a reason a person can read", () => {
    const rows = permissionRows(android(), t);
    expect(ids(rows)).toEqual([
      "notifications",
      "install",
      "nearby",
      "location",
      "microphone",
      "camera",
      "calendar",
      "contacts",
      "battery",
      "overlay",
      "alarms",
      "accessibility",
    ]);
    for (const r of rows) {
      expect(r.label.length, r.id).toBeGreaterThan(3);
      expect(r.why.length, r.id).toBeGreaterThan(15);
    }
  });

  it("puts the update permission in plain words, not as a fact that looks like subtext", () => {
    const install = permissionRows(android(), t).find((r) => r.id === "install");
    expect(install?.label).toBe("Install updates by itself");
    expect(install?.action).toEqual({ kind: "native", key: "install" });
    expect(install?.essential).toBe(true);
  });

  it("marks the basics: notifications, updates and finding devices", () => {
    const basics = permissionRows(android(), t).filter((r) => r.essential);
    expect(ids(basics)).toEqual(["notifications", "install", "nearby"]);
  });

  it("reports what is on and what is off", () => {
    const rows = permissionRows(android({ status: { ...allOn, notifications: false, camera: false } }), t);
    const state = (id: string) => rows.find((r) => r.id === id)?.state;
    expect(state("notifications")).toBe("denied");
    expect(state("camera")).toBe("denied");
    expect(state("location")).toBe("granted");
    expect(ids(missingEssentials(rows))).toEqual(["notifications"]);
    expect(grantedCount(rows)).toEqual({ granted: rows.length - 2, total: rows.length });
  });

  it("does not claim a permission is off when an older app simply does not report it", () => {
    const old = { notifications: true, installPackages: true, camera: true, location: true } as AndroidPermissionsStatus;
    const rows = permissionRows(android({ status: old }), t);
    for (const id of ["microphone", "calendar", "contacts", "nearby"]) {
      expect(rows.find((r) => r.id === id)?.state, id).toBe("unknown");
    }
  });

  it("leaves out what this phone cannot do", () => {
    const rows = permissionRows(android({ keep: { ...keep, exact_alarms: "n/a" }, accessibility: "unsupported" }), t);
    expect(ids(rows)).not.toContain("alarms");
    expect(ids(rows)).not.toContain("accessibility");
  });

  it("opens a settings page for the ones only the person can switch on", () => {
    const rows = permissionRows(android({ keep: { ...keep, battery_unrestricted: false }, accessibility: "off" }), t);
    expect(rows.find((r) => r.id === "battery")?.action).toEqual({ kind: "keep", what: "battery" });
    expect(rows.find((r) => r.id === "accessibility")).toMatchObject({ action: { kind: "accessibility" }, state: "denied" });
  });

  it("still lists the runtime permissions when the keep-running and accessibility details are missing", () => {
    const rows = permissionRows(android({ keep: null, accessibility: null }), t);
    expect(ids(rows)).toEqual(["notifications", "install", "nearby", "location", "microphone", "camera", "calendar", "contacts"]);
  });
});

describe("browser and desktop permissions list", () => {
  it("has the four the page can ask for, with notifications as the basic one", () => {
    const rows = permissionRows({ android: null, browser: { notifications: "granted", location: "denied" } }, t);
    expect(ids(rows)).toEqual(["notifications", "location", "microphone", "camera"]);
    expect(rows.map((r) => r.state)).toEqual(["granted", "denied", "unknown", "unknown"]);
    expect(ids(missingEssentials(rows))).toEqual([]);
    expect(ids(missingEssentials(permissionRows({ android: null, browser: {} }, t)))).toEqual(["notifications"]);
    expect(rows.every((r) => r.action.kind === "browser")).toBe(true);
  });
});
