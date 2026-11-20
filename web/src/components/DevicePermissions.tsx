import { Check, Loader2, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { accessibilityState, androidApp, androidPermissionsStatus, keepRunningStatus } from "../android";
import { useT } from "../i18n";
import {
  grantedCount,
  missingEssentials,
  permissionRows,
  type BrowserPermission,
  type PermAction,
  type PermRow,
  type PermState,
} from "../permissions";
import { cx } from "../util";

const BROWSER_NAMES: Record<BrowserPermission, string> = {
  notifications: "notifications",
  location: "geolocation",
  microphone: "microphone",
  camera: "camera",
};

/** What this browser (or the desktop app's window) says about each permission. */
export async function readBrowserPermissions(): Promise<Partial<Record<BrowserPermission, PermState>>> {
  const out: Partial<Record<BrowserPermission, PermState>> = {};
  if (typeof Notification !== "undefined") {
    out.notifications = Notification.permission === "granted" ? "granted" : Notification.permission === "denied" ? "denied" : "unknown";
  }
  const perms = typeof navigator !== "undefined" ? navigator.permissions : undefined;
  if (perms?.query) {
    for (const api of ["location", "microphone", "camera"] as const) {
      try {
        const r = await perms.query({ name: BROWSER_NAMES[api] as PermissionName });
        out[api] = r.state === "granted" ? "granted" : r.state === "denied" ? "denied" : "unknown";
      } catch {
        /* this browser does not know that name */
      }
    }
  }
  return out;
}

/** Show the browser's own prompt for one permission. Resolves once the person has answered. */
export async function requestBrowserPermission(api: BrowserPermission): Promise<void> {
  if (api === "notifications") {
    if (typeof Notification !== "undefined") await Notification.requestPermission();
    return;
  }
  if (api === "location") {
    await new Promise<void>((resolve) => {
      if (!navigator.geolocation) return resolve();
      navigator.geolocation.getCurrentPosition(
        () => resolve(),
        () => resolve(),
        { timeout: 15000 },
      );
    });
    return;
  }
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) return;
  try {
    const stream = await media.getUserMedia(api === "microphone" ? { audio: true } : { video: true });
    stream.getTracks().forEach((track) => track.stop());
  } catch {
    /* refused: the state read afterwards says so */
  }
}

function perform(action: PermAction): Promise<void> {
  const app = androidApp();
  switch (action.kind) {
    case "native":
      if (app?.requestPermission) app.requestPermission(action.key);
      else if (action.key === "install") app?.requestInstallPermission?.();
      else app?.requestAllPermissions?.();
      return Promise.resolve();
    case "keep":
      app?.openKeepRunning?.(action.what);
      return Promise.resolve();
    case "accessibility":
      app?.openAccessibilitySettings?.();
      return Promise.resolve();
    case "browser":
      return requestBrowserPermission(action.api);
    default:
      return Promise.resolve();
  }
}

/**
 * Every permission the operating system gates, with what it is for and a real button. Used in the
 * first-run setup (`compact`: just the basics, one button for all of them) and in Settings.
 */
export function DevicePermissions({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const [rows, setRows] = useState<PermRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const app = androidApp();
    const browser = app ? {} : await readBrowserPermissions();
    setRows(
      permissionRows(
        {
          android: app
            ? { status: androidPermissionsStatus(), keep: keepRunningStatus(), accessibility: accessibilityState() }
            : null,
          browser,
        },
        t,
      ),
    );
  }, [t]);

  useEffect(() => {
    void refresh();
    // the Android app tells the page when a dialog closes; coming back from Settings refocuses it
    const again = () => void refresh();
    window.addEventListener("nanomuse:permissions-changed", again);
    window.addEventListener("focus", again);
    return () => {
      window.removeEventListener("nanomuse:permissions-changed", again);
      window.removeEventListener("focus", again);
    };
  }, [refresh]);

  const run = async (row: PermRow) => {
    setBusy(row.id);
    try {
      await perform(row.action);
    } finally {
      setBusy(null);
      void refresh();
    }
  };

  const allowBasics = async () => {
    const app = androidApp();
    setBusy("basics");
    try {
      if (app?.requestAllPermissions) {
        app.requestAllPermissions();
      } else {
        for (const row of missingEssentials(rows)) await perform(row.action);
      }
    } finally {
      setBusy(null);
      void refresh();
    }
  };

  const shown = compact ? rows.filter((r) => r.essential) : rows;
  const missing = missingEssentials(rows).length;
  const { granted, total } = grantedCount(rows);
  if (rows.length === 0) return null;

  return (
    <div className="space-y-2.5" data-testid="device-permissions">
      {!compact && (
        <p className="px-1 text-[12.5px] text-muted">{t("{n} of {total} allowed", { n: granted, total })}</p>
      )}
      <ul className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/70 bg-surface">
        {shown.map((row) => (
          <li key={row.id} className="flex items-center gap-3 px-3.5 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-medium leading-[19px] text-fg">{row.label}</div>
              <div className="mt-0.5 text-[12.5px] leading-[17px] text-muted">{row.why}</div>
            </div>
            {row.state === "granted" ? (
              <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-500/12 px-2.5 py-1 text-[12px] font-semibold text-emerald-600 dark:text-emerald-400">
                <Check size={13} strokeWidth={3} /> {t("Allowed")}
              </span>
            ) : row.action.kind === "accessibility" && androidApp() ? (
              <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => androidApp()?.openAppSettings?.()}
                  className="inline-flex shrink-0 items-center gap-1 rounded-full bg-accent px-3 py-1.5 text-[12px] font-semibold text-accent-fg hover:opacity-95 disabled:opacity-60"
                  title={t("App Info → ⋮ (three dots) → Allow restricted settings")}
                >
                  {t("1. Unrestricted")}
                </button>
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void run(row)}
                  className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border px-3 py-1.5 text-[12px] font-semibold text-fg hover:bg-surface-2 disabled:opacity-60"
                >
                  {busy === row.id && <Loader2 size={13} className="animate-spin" />}
                  {t("2. Accessibility")}
                </button>
              </div>
            ) : (
              <button
                type="button"
                disabled={busy !== null}
                onClick={() => void run(row)}
                className={cx(
                  "inline-flex shrink-0 items-center gap-1 rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold transition disabled:opacity-60",
                  "bg-accent text-accent-fg hover:opacity-95",
                )}
              >
                {busy === row.id && <Loader2 size={13} className="animate-spin" />}
                {row.action.kind === "keep" || row.action.kind === "accessibility" ? t("Open settings") : t("Allow")}
              </button>
            )}
          </li>
        ))}
      </ul>
      {compact && missing > 0 && (
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => void allowBasics()}
          className="flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 text-[14px] font-semibold text-accent-fg shadow-sm hover:opacity-95 disabled:opacity-60"
        >
          {busy === "basics" ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={16} />}
          {t("Allow the basics")}
        </button>
      )}
      {compact && <p className="px-1 text-[11.5px] text-muted">{t("You can change any of these later in Settings, under Permissions.")}</p>}
    </div>
  );
}
