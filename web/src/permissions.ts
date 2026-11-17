import type { AndroidPermissionsStatus, KeepRunningStatus } from "./android";
import type { t as T } from "./i18n";

/**
 * The operating-system permissions MiniMuse can use, in one list: what each one is for, whether it
 * is on, and what its button does. These are the real hardware / software permissions (the OS
 * decides), not the "ask me first" rules, which live under Safety.
 */
export type PermState = "granted" | "denied" | "unknown";

export type PermAction =
  /** Android's own dialog for one permission, by the name the app bridge knows */
  | { kind: "native"; key: string }
  /** a settings page only the person can switch on: battery, overlay, alarms, autostart */
  | { kind: "keep"; what: "battery" | "overlay" | "alarms" | "autostart" }
  | { kind: "accessibility" }
  /** the browser or desktop app's own prompt */
  | { kind: "browser"; api: BrowserPermission }
  | { kind: "none" };

export type BrowserPermission = "notifications" | "location" | "microphone" | "camera";

export interface PermRow {
  id: string;
  label: string;
  /** one plain sentence on what it is for */
  why: string;
  state: PermState;
  action: PermAction;
  /** needed for the basics (asked at the start of setup) */
  essential: boolean;
}

export interface PermInputs {
  /** what the Android app says; null when this is not the Android app */
  android: {
    status: AndroidPermissionsStatus | null;
    keep: KeepRunningStatus | null;
    accessibility: "on" | "off" | "unsupported" | null;
  } | null;
  /** what the browser or desktop shell says */
  browser: Partial<Record<BrowserPermission, PermState>>;
}

const yes = (v: boolean | undefined): PermState => (v === undefined ? "unknown" : v ? "granted" : "denied");

export function permissionRows(input: PermInputs, t: typeof T): PermRow[] {
  if (input.android) return androidRows(input.android, t);
  const b = input.browser;
  const row = (api: BrowserPermission, label: string, why: string, essential: boolean): PermRow => ({
    id: api,
    label,
    why,
    state: b[api] ?? "unknown",
    action: { kind: "browser", api },
    essential,
  });
  return [
    row("notifications", t("Notifications"), t("So MiniMuse can tell you when something finishes or needs you."), true),
    row("location", t("Location"), t("For the map, your places and your trips."), false),
    row("microphone", t("Microphone"), t("For talking to MiniMuse and for live calls."), false),
    row("camera", t("Camera"), t("To scan a QR code or attach a photo."), false),
  ];
}

function androidRows(a: NonNullable<PermInputs["android"]>, t: typeof T): PermRow[] {
  const s = a.status;
  const k = a.keep;
  const rows: PermRow[] = [
    {
      id: "notifications",
      label: t("Notifications"),
      why: t("So MiniMuse can tell you when something finishes or needs you."),
      state: yes(s?.notifications),
      action: { kind: "native", key: "notifications" },
      essential: true,
    },
    {
      id: "install",
      label: t("Install updates by itself"),
      why: t("Lets MiniMuse update itself and put the watch app on your watch, without you downloading anything."),
      state: yes(s?.installPackages),
      action: { kind: "native", key: "install" },
      essential: true,
    },
    {
      id: "nearby",
      label: t("Find devices on your Wi-Fi"),
      why: t("Needed to spot your watch and your other devices on the network."),
      state: yes(s?.nearby),
      action: { kind: "native", key: "nearby" },
      essential: true,
    },
    {
      id: "location",
      label: t("Location"),
      why: t("For the map, your places and your trips."),
      state: yes(s?.location),
      action: { kind: "native", key: "location" },
      essential: false,
    },
    {
      id: "microphone",
      label: t("Microphone"),
      why: t("For talking to MiniMuse and for live calls."),
      state: yes(s?.microphone),
      action: { kind: "native", key: "microphone" },
      essential: false,
    },
    {
      id: "camera",
      label: t("Camera"),
      why: t("To scan a QR code or attach a photo."),
      state: yes(s?.camera),
      action: { kind: "native", key: "camera" },
      essential: false,
    },
    {
      id: "calendar",
      label: t("Calendar"),
      why: t("To read and add events on this phone."),
      state: yes(s?.calendar),
      action: { kind: "native", key: "calendar" },
      essential: false,
    },
    {
      id: "contacts",
      label: t("Contacts"),
      why: t("So it can find the people you mention."),
      state: yes(s?.contacts),
      action: { kind: "native", key: "contacts" },
      essential: false,
    },
  ];
  if (k) {
    rows.push(
      {
        id: "battery",
        label: t("Keep running in the background"),
        why: t("Stops Android from putting MiniMuse to sleep, so reminders and tasks still happen."),
        state: yes(k.battery_unrestricted),
        action: { kind: "keep", what: "battery" },
        essential: false,
      },
      {
        id: "overlay",
        label: t("Show over other apps"),
        why: t("For quick actions that appear on top of whatever you are doing."),
        state: yes(k.overlay),
        action: { kind: "keep", what: "overlay" },
        essential: false,
      },
    );
    if (k.exact_alarms !== "n/a") {
      rows.push({
        id: "alarms",
        label: t("Exact reminders"),
        why: t("So a reminder goes off at the minute you set, not a few minutes late."),
        state: k.exact_alarms === "granted" ? "granted" : "denied",
        action: { kind: "keep", what: "alarms" },
        essential: false,
      });
    }
  }
  if (a.accessibility && a.accessibility !== "unsupported") {
    rows.push({
      id: "accessibility",
      label: t("Control this phone"),
      why: t("Lets MiniMuse read and tap the screen to do things for you. You switch this on yourself in Android Settings."),
      state: a.accessibility === "on" ? "granted" : "denied",
      action: { kind: "accessibility" },
      essential: false,
    });
  }
  return rows;
}

/** The basics that are still off: what the setup step asks for. */
export function missingEssentials(rows: PermRow[]): PermRow[] {
  return rows.filter((r) => r.essential && r.state !== "granted");
}

/** How many of the rows are on, for a short summary ("5 of 11 allowed"). */
export function grantedCount(rows: PermRow[]): { granted: number; total: number } {
  return { granted: rows.filter((r) => r.state === "granted").length, total: rows.length };
}
