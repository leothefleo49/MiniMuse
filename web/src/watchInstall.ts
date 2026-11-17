import type { t as T } from "./i18n";
import type { WatchDevicesInfo, WatchEndpoint, WatchInstallResult } from "./types";

/** What the watch card should be doing right now, from what the runtime last saw. */
export type WatchPhase =
  /** nothing looked at yet */
  | "searching"
  /** a watch is connected and can be installed on */
  | "ready"
  /** a watch is showing its pairing screen: the person only has to type the 6 digits */
  | "pairing"
  /** only a phone is attached */
  | "phone_only"
  /** nothing found */
  | "none";

export interface WatchPhaseInfo {
  phase: WatchPhase;
  /** the connected watch, when `phase` is "ready" */
  watchId?: string;
  watchName?: string;
  /** the pairing screens found, when `phase` is "pairing" */
  pairing: WatchEndpoint[];
}

export function watchPhase(info: WatchDevicesInfo | null): WatchPhaseInfo {
  if (!info) return { phase: "searching", pairing: [] };
  const ready = info.devices.filter((d) => d.state === "device");
  const watch = ready.find((d) => d.is_watch === true);
  if (watch) return { phase: "ready", watchId: watch.id, watchName: watch.model || watch.id, pairing: [] };
  // a pairing screen that belongs to a phone we are already connected to is not the watch
  const phoneSerials = new Set(ready.filter((d) => d.is_watch === false).map((d) => d.serial).filter(Boolean));
  const pairing = (info.mdns_pairing ?? []).filter((ep) => !(ep.serial && phoneSerials.has(ep.serial)));
  if (pairing.length > 0) return { phase: "pairing", pairing };
  if (ready.some((d) => d.is_watch === false)) return { phase: "phone_only", pairing: [] };
  return { phase: "none", pairing: [] };
}

/** Digits only, at most six: what the person typed or pasted from the watch's pairing screen. */
export function cleanPairCode(raw: string): string {
  return raw.replace(/\D/g, "").slice(0, 6);
}

export function isCompletePairCode(raw: string): boolean {
  return /^\d{6}$/.test(cleanPairCode(raw));
}

export type WatchNoticeAction = "retry" | "code" | "download" | "close";

export interface WatchNotice {
  tone: "error" | "warning";
  title: string;
  body: string;
  steps: string[];
  /** what the popup's main button does */
  action: WatchNoticeAction;
}

/**
 * The popup for an install that did not happen. One per reason, each saying what happened and what
 * to do next, so a person who is not looking at the card still cannot miss it.
 */
export function watchFailureNotice(res: WatchInstallResult | null | undefined, t: typeof T): WatchNotice | null {
  if (!res || res.ok) return null;
  const turnOn = [
    t("On the watch open Settings, About watch, Software info, and tap Build number 7 times."),
    t("Open Settings, Developer options, and turn on ADB debugging and Wireless debugging."),
    t("Make sure the watch is on the same Wi-Fi as this device, then try again."),
  ];
  switch (res.code) {
    case "no_watch":
      return {
        tone: "warning",
        title: t("No watch found"),
        body: t("MiniMuse looked on your Wi-Fi and over USB and could not find a watch."),
        steps: turnOn,
        action: "retry",
      };
    case "phone_only":
      return {
        tone: "warning",
        title: t("Only a phone was found"),
        body: t("MiniMuse never installs the watch app on a phone, so nothing was installed."),
        steps: turnOn,
        action: "retry",
      };
    case "needs_pairing":
      return {
        tone: "warning",
        title: t("Pair your watch once"),
        body: t("A watch with Wireless debugging is on your network, but this device is not paired with it yet."),
        steps: [
          t("On the watch open Developer options, Wireless debugging, then Pair new device with pairing code."),
          t("Leave that screen open. MiniMuse sees it and asks only for the 6 digits."),
        ],
        action: "retry",
      };
    case "need_pairing_code":
      return {
        tone: "warning",
        title: t("Type the code from your watch"),
        body: t("Your watch is showing its pairing screen."),
        steps: [t("Type the 6-digit pairing code that the watch shows.")],
        action: "code",
      };
    case "choose_pairing":
      return {
        tone: "warning",
        title: t("More than one pairing screen is open"),
        body: t("More than one device is showing a pairing code."),
        steps: [t("Close the pairing screen on the devices you do not want to use, then try again.")],
        action: "retry",
      };
    case "no_pairing_service":
      return {
        tone: "warning",
        title: t("The pairing screen is not open"),
        body: t("MiniMuse could not see a watch waiting for a pairing code."),
        steps: [
          t("If Developer options is missing on the watch: Settings, About watch, Software info, tap Build number 7 times."),
          t("On the watch open Developer options, Wireless debugging, then Pair new device with pairing code."),
          t("Type the new 6-digit code here before it expires."),
        ],
        action: "retry",
      };
    case "pair_failed":
      return {
        tone: "error",
        title: t("That code did not work"),
        body: t("A pairing code expires after about a minute."),
        steps: [
          t("On the watch tap Pair new device with pairing code again."),
          t("Type the new 6 digits."),
        ],
        action: "code",
      };
    case "unauthorized":
      return {
        tone: "warning",
        title: t("Allow this device on your watch"),
        body: t("Your watch is waiting for you to approve this connection."),
        steps: [t("Look at the watch screen and tap Always allow from this computer, then OK."), t("Then try again.")],
        action: "retry",
      };
    case "adb_missing":
      return {
        tone: "error",
        title: t("A small tool is missing"),
        body: t("MiniMuse could not set up the Android tool (ADB) it needs on this device."),
        steps: [t("Check your internet connection and try again."), t("Or download the watch app and install it with another tool.")],
        action: "download",
      };
    case "apk_missing":
      return {
        tone: "error",
        title: t("Could not download the watch app"),
        body: t("MiniMuse could not fetch the watch app from GitHub."),
        steps: [t("Check your internet connection and try again.")],
        action: "retry",
      };
    default:
      return {
        tone: "error",
        title: t("The install did not finish"),
        body: res.error || t("Could not install watch APK"),
        steps: [],
        action: "retry",
      };
  }
}
