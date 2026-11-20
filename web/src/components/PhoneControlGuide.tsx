import { useEffect, useState } from "react";
import { accessibilityState, androidApp } from "../android";
import { useT } from "../i18n";
import { cx } from "../util";

/**
 * How to let MiniMuse operate the whole phone: Android's accessibility service is what reads the
 * screen and taps for it. Android 13+ greys that switch out for apps installed from a download, so
 * the guide says where "Allow restricted settings" is. Only shown inside the Android app.
 */
export function PhoneControlGuide() {
  const t = useT();
  const app = androidApp();
  const [state, setState] = useState(accessibilityState);

  useEffect(() => {
    const again = () => setState(accessibilityState());
    window.addEventListener("focus", again);
    window.addEventListener("nanomuse:permissions-changed", again);
    return () => {
      window.removeEventListener("focus", again);
      window.removeEventListener("nanomuse:permissions-changed", again);
    };
  }, []);

  if (!app || state === null) return null;
  const steps = [
    t("First, allow restricted settings: tap 1. Allow restricted settings (App Info) below, tap the three dots (⋮) at the top-right, and tap Allow restricted settings."),
    t("Next, come back and tap 2. Open Accessibility settings below."),
    t("Find MiniMuse (under Installed apps) and turn it on."),
    t("Come back here: this page says Ready when phone control is active."),
  ];
  return (
    <div className="space-y-2.5 rounded-2xl border border-border/70 bg-surface px-3.5 py-3" data-testid="phone-control-guide">
      <div className="flex items-center justify-between gap-3">
        <div className="text-[14px] font-medium text-fg">{t("Full control of your phone")}</div>
        <span
          className={cx(
            "rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold",
            state === "on" && "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
            state === "off" && "bg-amber-500/15 text-amber-700 dark:text-amber-300",
            state === "unsupported" && "bg-surface-2 text-muted",
          )}
        >
          {state === "on" ? t("Ready") : state === "off" ? t("Not yet") : t("Not on this Android")}
        </span>
      </div>
      <p className="text-[12.5px] leading-[17px] text-muted">
        {t("Android blocks phone control for downloaded apps until you allow restricted settings in App Info first. Once allowed, turn on MiniMuse in Accessibility so it can read the screen and tap for you (always with a Stop button on screen).")}
      </p>
      {state === "unsupported" ? (
        <p className="text-[12.5px] leading-[17px] text-muted">{t("Operating the screen needs Android 11 or newer; this phone can still be used for everything else.")}</p>
      ) : (
        <>
          <ol className="space-y-1.5">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-2.5 text-[13px] leading-[18px] text-fg">
                <span className="mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[11.5px] font-semibold text-muted">
                  {i + 1}
                </span>
                <span>{s}</span>
              </li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-2 pt-0.5">
            <button
              type="button"
              onClick={() => app.openAppSettings?.()}
              className="rounded-full bg-accent px-3.5 py-1.5 text-[12.5px] font-semibold text-accent-fg"
            >
              {t("1. Allow restricted settings (App Info)")}
            </button>
            <button
              type="button"
              onClick={() => app.openAccessibilitySettings?.()}
              className="rounded-full border border-border px-3.5 py-1.5 text-[12.5px] font-medium text-fg hover:bg-surface-2"
            >
              {t("2. Open Accessibility settings")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
