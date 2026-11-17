import { describe, expect, it } from "vitest";
import { t } from "./i18n";
import type { WatchDevicesInfo, WatchInstallCode, WatchInstallResult } from "./types";
import { cleanPairCode, isCompletePairCode, watchFailureNotice, watchPhase } from "./watchInstall";

const base: WatchDevicesInfo = { adb_available: true, devices: [], apk_path: null, apk_ready: true, download_url: "/api/watch/apk" };
const phone = { id: "adb-PHONE1-x._adb-tls-connect._tcp", state: "device", model: "SM_F766U", is_watch: false, serial: "PHONE1" };
const watch = { id: "192.168.1.77:38291", state: "device", model: "SM_R895F", is_watch: true, serial: "WATCH1" };
const pairing = { name: "adb-WATCH1-x", host: "192.168.1.77", port: 40123, host_port: "192.168.1.77:40123", kind: "pairing" as const, serial: "WATCH1" };

describe("watchPhase", () => {
  it("is searching until the runtime has answered", () => {
    expect(watchPhase(null).phase).toBe("searching");
  });

  it("is none when nothing is connected or advertised", () => {
    expect(watchPhase(base).phase).toBe("none");
  });

  it("is ready, with the watch's name, once a watch is connected", () => {
    const p = watchPhase({ ...base, devices: [phone, watch] });
    expect(p.phase).toBe("ready");
    expect(p.watchId).toBe("192.168.1.77:38291");
    expect(p.watchName).toBe("SM_R895F");
  });

  it("is pairing when a watch shows its pairing screen, and carries where it is", () => {
    const p = watchPhase({ ...base, mdns_pairing: [pairing] });
    expect(p.phase).toBe("pairing");
    expect(p.pairing.map((e) => e.host_port)).toEqual(["192.168.1.77:40123"]);
  });

  it("does not mistake the phone's own pairing screen for a watch", () => {
    const own = { ...pairing, serial: "PHONE1" };
    expect(watchPhase({ ...base, devices: [phone], mdns_pairing: [own] }).phase).toBe("phone_only");
  });

  it("is phone_only when only a phone is attached", () => {
    expect(watchPhase({ ...base, devices: [phone] }).phase).toBe("phone_only");
  });

  it("ignores a device that is not authorised yet", () => {
    expect(watchPhase({ ...base, devices: [{ ...watch, state: "unauthorized" }] }).phase).toBe("none");
  });
});

describe("pairing code", () => {
  it("keeps digits only, at most six", () => {
    expect(cleanPairCode("123 456")).toBe("123456");
    expect(cleanPairCode("12-34-56-78")).toBe("123456");
    expect(cleanPairCode("abc")).toBe("");
  });

  it("is complete at exactly six digits", () => {
    expect(isCompletePairCode("123456")).toBe(true);
    expect(isCompletePairCode("123 456")).toBe(true);
    expect(isCompletePairCode("12345")).toBe(false);
    expect(isCompletePairCode("")).toBe(false);
  });
});

describe("watchFailureNotice", () => {
  const codes: WatchInstallCode[] = [
    "adb_missing",
    "apk_missing",
    "no_watch",
    "phone_only",
    "needs_pairing",
    "need_pairing_code",
    "choose_pairing",
    "no_pairing_service",
    "pair_failed",
    "unauthorized",
    "install_failed",
  ];

  it("has a popup with a title and a next step for every reason the server can give", () => {
    for (const code of codes) {
      const n = watchFailureNotice({ ok: false, code, error: "boom" }, t);
      expect(n, code).not.toBeNull();
      expect(n?.title.length, code).toBeGreaterThan(3);
      expect((n?.steps.length ?? 0) > 0 || code === "install_failed", code).toBe(true);
    }
  });

  it("makes no popup for a success", () => {
    expect(watchFailureNotice({ ok: true }, t)).toBeNull();
    expect(watchFailureNotice(undefined, t)).toBeNull();
  });

  it("tells a person with no watch found how to turn on Developer options", () => {
    const n = watchFailureNotice({ ok: false, code: "no_watch" }, t);
    expect(n?.steps.join(" ")).toContain("Build number");
    expect(n?.steps.join(" ")).toContain("Wireless debugging");
  });

  it("asks for the code when the pairing screen is open or the code failed", () => {
    expect(watchFailureNotice({ ok: false, code: "need_pairing_code" }, t)?.action).toBe("code");
    expect(watchFailureNotice({ ok: false, code: "pair_failed" }, t)?.action).toBe("code");
  });

  it("offers the APK download when the setup tool is missing", () => {
    expect(watchFailureNotice({ ok: false, code: "adb_missing" }, t)?.action).toBe("download");
  });

  it("shows the server's own words for an unknown failure", () => {
    const res = { ok: false, error: "INSTALL_FAILED_OLDER_SDK" } as WatchInstallResult;
    const n = watchFailureNotice(res, t);
    expect(n?.body).toBe("INSTALL_FAILED_OLDER_SDK");
    expect(n?.tone).toBe("error");
  });
});
