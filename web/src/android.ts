/**
 * The Android app (android/) shows this web app in a WebView and exposes the phone-side bits
 * as `window.NanoMuseAndroid`. Web Push does not work there — the app keeps its own connection
 * to the server and posts notifications itself — so the settings offer that instead.
 */
export interface AndroidBridge {
  version(): string;
  serverUrl(): string;
  /** "local" when the server runs on this phone, "remote" when it is the user's computer. */
  mode?(): string;
  notificationsEnabled(): boolean;
  setNotificationsEnabled(on: boolean): void;
  disconnect(): void;
  /** Whether this app hosts the agent's browser itself (a WebView the user can take over in place). */
  hasBrowser?(): boolean;
  /**
   * Show the agent's browser in the app for the user to drive; when done the app dispatches
   * `nanomuse:browser-handed-back` with `{thread}` on window.
   */
  takeOverBrowser?(thread: string): void;
  /**
   * The accessibility service that operates the phone's screen: "on", "off", or "unsupported"
   * (Android 10 and older cannot take screenshots for it).
   */
  accessibilityState?(): string;
  /** Open Android Settings → Accessibility, where the service is switched on. */
  openAccessibilitySettings?(): void;
  /** Open this app's page in Android Settings (the "Allow restricted settings" menu lives there). */
  openAppSettings?(): void;
  /** JSON: what stands in the way of running in the background (see KeepRunningStatus). */
  keepRunning?(): string;
  /** Open the settings page for one of them: battery, overlay, alarms, autostart, app. */
  openKeepRunning?(what: string): void;
  setStartOnBoot?(on: boolean): void;
  /** Zip the crash files, the runtime log and the app's logcat lines; open the share sheet. */
  exportLogs?(): void;
  /**
   * Pin a workspace file — an app the agent built — to Android's home screen. Android shows
   * its own confirm; the shortcut opens the file alone, fullscreen, in this app.
   */
  addHomeShortcut?(name: string, path: string): void;
  /** JSON from the shell's self-updater: cached version, active download state, and shell flag. */
  checkAppUpdate?(): string;
  /** Refresh GitHub release metadata asynchronously; emits `nanomuse:app-update`. */
  refreshAppUpdate?(): void;
  /** Download the newer APK (single-flight deduplicated) and open Android's install sheet. */
  installAppUpdate?(): void;
  /** Whether background update checks + downloads are enabled on Android. */
  autoUpdateEnabled?(): boolean;
  setAutoUpdateEnabled?(on: boolean): void;
  /** Open the native QR scanner directly to pair with a computer or server. */
  scanToPair?(): void;
  /**
   * A pairing QR scanned on the phone's start screen: the app started its local server for it
   * and parked the payload here for the page to claim. Taken (and cleared) once, on load.
   */
  takePendingPairPayload?(): string;
  /** JSON readiness for native BiometricPrompt on HTTP local / LAN WebView pages. */
  biometricStatus?(): string;
  /** Prompt native Fingerprint / Face ID; emits `nanomuse:native-biometric`. */
  authenticateBiometric?(requestId: string, title?: string, subtitle?: string): void;
  /** Check whether Android's "Install unknown apps" permission is granted for MiniMuse. */
  canInstallPackages?(): boolean;
  /** Open Android's "Install unknown apps" screen for MiniMuse. */
  requestInstallPermission?(): void;
  /** Request all missing runtime + install permissions on Android. */
  requestAllPermissions?(): void;
  /** Is MiniMuse the phone's default digital assistant? "on", "off" or "unknown". */
  assistantState?(): string;
  /** Open Android's Default digital assistant app page, the only place one can be chosen. */
  openAssistantSettings?(): void;
  /** Ask for one permission by name: notifications, camera, microphone, location, calendar, contacts, nearby, install. */
  requestPermission?(key: string): void;
  /** Return JSON summarizing granted permissions on this phone. */
  permissionsStatus?(): string;
  /** JSON array of Google/calendar emails signed in on this phone (when Calendar permission is granted). */
  deviceGoogleEmails?(): string;
  /** Request Calendar/Contacts permissions if needed and export phone Google Calendar & Contacts via `nanomuse:phone-sync-ready`. */
  syncPhoneCalendarAndContacts?(): void;
  /** Discover Wear OS watches advertising Wireless Debugging mDNS (`_adb-tls-connect` / `_adb-tls-pairing`). */
  discoverWatchAdb?(): string;
}

export interface WatchAdbEndpoint {
  name: string;
  host: string;
  port: number;
  host_port: string;
}

export interface WatchAdbDiscoveryResult {
  connect: WatchAdbEndpoint[];
  pairing: WatchAdbEndpoint[];
}

export function discoverWatchAdbFromPhone(): WatchAdbDiscoveryResult | null {
  const app = androidApp();
  if (!app || typeof app.discoverWatchAdb !== "function") return null;
  try {
    const parsed = JSON.parse(app.discoverWatchAdb()) as Partial<WatchAdbDiscoveryResult>;
    return {
      connect: Array.isArray(parsed.connect) ? parsed.connect : [],
      pairing: Array.isArray(parsed.pairing) ? parsed.pairing : [],
    };
  } catch {
    return null;
  }
}

export interface PhoneSyncPayload {
  ok: boolean;
  error?: string;
  calendar_name?: string;
  ics?: string;
  events?: number;
  contacts_name?: string;
  vcf?: string;
  contacts?: number;
  emails?: string[];
}

export function deviceGoogleEmails(): string[] {
  const app = androidApp();
  if (!app || typeof app.deviceGoogleEmails !== "function") return [];
  try {
    const parsed = JSON.parse(app.deviceGoogleEmails());
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string" && x.includes("@")) : [];
  } catch {
    return [];
  }
}

export function requestPhoneCalendarAndContactsSync(): Promise<PhoneSyncPayload> {
  const app = androidApp();
  if (!app || typeof app.syncPhoneCalendarAndContacts !== "function") {
    return Promise.reject(new Error("Phone sync is only available inside the Android app."));
  }
  return new Promise<PhoneSyncPayload>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      window.removeEventListener("nanomuse:phone-sync-ready", onEvent as EventListener);
      reject(new Error("Timed out waiting for Android phone sync"));
    }, 60_000);
    const onEvent = (ev: CustomEvent<PhoneSyncPayload>) => {
      window.clearTimeout(timeout);
      window.removeEventListener("nanomuse:phone-sync-ready", onEvent as EventListener);
      const d = ev.detail;
      if (!d || !d.ok) {
        reject(new Error(d?.error || "Phone sync failed"));
      } else {
        resolve(d);
      }
    };
    window.addEventListener("nanomuse:phone-sync-ready", onEvent as EventListener);
    app.syncPhoneCalendarAndContacts!();
  });
}

export interface AndroidPermissionsStatus {
  notifications: boolean;
  installPackages: boolean;
  camera: boolean;
  location: boolean;
  /** newer builds report these too; an older app leaves them out */
  microphone?: boolean;
  calendar?: boolean;
  contacts?: boolean;
  nearby?: boolean;
}

export function androidPermissionsStatus(): AndroidPermissionsStatus | null {
  const app = androidApp();
  if (!app || typeof app.permissionsStatus !== "function") return null;
  try {
    return JSON.parse(app.permissionsStatus()) as AndroidPermissionsStatus;
  } catch {
    return null;
  }
}

/** The shell's answer about a newer release; null when the shell cannot check. */
export interface AppUpdateView {
  current: string;
  shellVersion?: string;
  latest: string | null;
  apkUrl: string | null;
  notesUrl?: string | null;
  shellRequired?: boolean;
  autoUpdate?: boolean;
  downloadedVersion?: string | null;
  downloaded?: boolean;
  downloading?: boolean;
  downloadingVersion?: string | null;
  progress?: number;
  canInstallPackages?: boolean;
}

export interface AndroidAppUpdateEvent {
  current: string;
  shellVersion?: string;
  version: string | null;
  latest?: string | null;
  apkUrl: string | null;
  notes?: string | null;
  shellRequired?: boolean;
  downloaded?: boolean;
  downloadedVersion?: string | null;
  downloading?: boolean;
  downloadingVersion?: string | null;
  progress?: number;
  autoUpdate?: boolean;
  canInstallPackages?: boolean;
}

export function checkAppUpdate(): AppUpdateView | null {
  const app = androidApp();
  if (!app || typeof app.checkAppUpdate !== "function") return null;
  try {
    return JSON.parse(app.checkAppUpdate()) as AppUpdateView;
  } catch {
    return null;
  }
}

/** True when the Android app can pin a workspace file to the home screen. */
export function canPinShortcuts(): boolean {
  const app = androidApp();
  return typeof app?.addHomeShortcut === "function";
}

/** Pin a workspace file to the home screen; a no-op outside the Android app. */
export function pinHomeShortcut(name: string, path: string): void {
  androidApp()?.addHomeShortcut?.(name, path);
}

export interface KeepRunningStatus {
  battery_unrestricted: boolean;
  overlay: boolean;
  exact_alarms: "granted" | "denied" | "n/a";
  boot_start: boolean;
  /** xiaomi · huawei · honor · oppo · vivo · samsung · meizu · "" */
  vendor: string;
  /** whether this phone has an auto-start / background manager page the app can open */
  autostart_settings: boolean;
  android: number;
  /** the runtime runs on this phone (local mode) */
  local: boolean;
}

export function keepRunningStatus(): KeepRunningStatus | null {
  const app = androidApp();
  if (!app || typeof app.keepRunning !== "function") return null;
  try {
    return JSON.parse(app.keepRunning()) as KeepRunningStatus;
  } catch {
    return null;
  }
}

export function androidApp(): AndroidBridge | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { NanoMuseAndroid?: AndroidBridge };
  return w.NanoMuseAndroid ?? null;
}

/** True when the app can hand the agent's own browser to the user in place (no screenshots in between). */
export function nativeTakeOver(): boolean {
  const app = androidApp();
  return !!app && typeof app.takeOverBrowser === "function" && (typeof app.hasBrowser !== "function" || app.hasBrowser());
}

/** What the app says about its accessibility service; null when this is not the Android app. */
export function accessibilityState(): "on" | "off" | "unsupported" | null {
  const app = androidApp();
  if (!app || typeof app.accessibilityState !== "function") return null;
  const state = app.accessibilityState();
  return state === "on" || state === "off" || state === "unsupported" ? state : null;
}

export interface NativeBiometricStatus {
  available: boolean;
  enrolled: boolean;
  reason: string;
}

/** Hardware fingerprint / face readiness from Android's BiometricManager. */
export function nativeBiometricInfo(): NativeBiometricStatus {
  const app = androidApp();
  if (!app || typeof app.biometricStatus !== "function") {
    return { available: false, enrolled: false, reason: "" };
  }
  try {
    return JSON.parse(app.biometricStatus()) as NativeBiometricStatus;
  } catch {
    return { available: false, enrolled: false, reason: "" };
  }
}

/** Prompt native Android Fingerprint / Face ID (works over HTTP 127.0.0.1 and LAN). */
export function authenticateNativeBiometric(title: string, subtitle = ""): Promise<void> {
  const app = androidApp();
  if (!app || typeof app.authenticateBiometric !== "function") {
    return Promise.reject(new Error("Biometric prompt is unavailable on this device."));
  }
  return new Promise((resolve, reject) => {
    const requestId =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `bio-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const timer = window.setTimeout(() => {
      window.removeEventListener("nanomuse:native-biometric", onResult);
      const err = new Error("Verification timed out. Tap to try again.");
      err.name = "AbortError";
      reject(err);
    }, 65000);
    const onResult = (ev: Event) => {
      const detail = (
        ev as CustomEvent<{ requestId?: string; ok?: boolean; cancelled?: boolean; error?: string }>
      ).detail;
      if (!detail || detail.requestId !== requestId) return;
      window.clearTimeout(timer);
      window.removeEventListener("nanomuse:native-biometric", onResult);
      if (detail.ok) {
        resolve();
      } else {
        const err = new Error(detail.error || "Biometric verification did not go through.");
        if (detail.cancelled) err.name = "NotAllowedError";
        reject(err);
      }
    };
    window.addEventListener("nanomuse:native-biometric", onResult);
    app.authenticateBiometric?.(requestId, title, subtitle);
  });
}
