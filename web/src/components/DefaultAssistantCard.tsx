import { Monitor, Smartphone, Watch } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { androidApp } from "../android";
import { useT } from "../i18n";
import { cx } from "../util";

type State = "on" | "off" | "unknown";

/**
 * Making MiniMuse the assistant a device opens when you hold the home button, press and hold the
 * power or side button, or use the assist gesture. Each device lets a person choose that in its own
 * settings and nowhere else, so this says what to tap and, on the phone, takes you there and shows
 * whether it worked.
 */
export function DefaultAssistantCard() {
  const t = useT();
  const app = androidApp();
  const [state, setState] = useState<State>(() => readState());

  useEffect(() => {
    // coming back from Android's page: look again
    const again = () => setState(readState());
    window.addEventListener("focus", again);
    window.addEventListener("nanomuse:permissions-changed", again);
    return () => {
      window.removeEventListener("focus", again);
      window.removeEventListener("nanomuse:permissions-changed", again);
    };
  }, []);

  const row = (icon: ReactNode, title: string, body: string, extra?: ReactNode) => (
    <div className="flex items-start gap-3 px-3.5 py-3">
      <span className="mt-0.5 text-muted">{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="text-[14px] font-medium text-fg">{title}</div>
          {extra}
        </div>
        <p className="mt-0.5 text-[12.5px] leading-[17px] text-muted">{body}</p>
      </div>
    </div>
  );

  return (
    <div className="space-y-2.5" data-testid="default-assistant">
      <p className="px-1 text-[12.5px] leading-[17px] text-muted">
        {t("Pick MiniMuse as the assistant a device opens from its home button, its power or side button, or its assist gesture. Each device only lets you choose that in its own settings.")}
      </p>
      <div className="divide-y divide-border/60 overflow-hidden rounded-2xl border border-border/70 bg-surface">
        {app &&
          row(
            <Smartphone size={18} />,
            t("This phone"),
            state === "on"
              ? t("MiniMuse is your default assistant. Hold the home or power button, or use the assist gesture, to open it.")
              : t("Open Android's page, choose Default digital assistant app, then pick MiniMuse. The wording differs a little between phone makers."),
            <span className="flex items-center gap-2">
              <span
                className={cx(
                  "rounded-full px-2.5 py-0.5 text-[11.5px] font-semibold",
                  state === "on" && "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
                  state === "off" && "bg-amber-500/15 text-amber-700 dark:text-amber-300",
                  state === "unknown" && "bg-surface-2 text-muted",
                )}
              >
                {state === "on" ? t("Default") : state === "off" ? t("Not yet") : t("Not sure")}
              </span>
              {state !== "on" && (
                <button
                  type="button"
                  onClick={() => app.openAssistantSettings?.()}
                  className="rounded-full bg-accent px-3.5 py-1.5 text-[12.5px] font-semibold text-accent-fg"
                >
                  {t("Choose MiniMuse")}
                </button>
              )}
            </span>,
          )}
        {row(
          <Watch size={18} />,
          t("Your watch"),
          t("Install the watch app first (Settings, Smartwatch). Then open the watch's own Settings and look for Assistant or Default apps, and choose MiniMuse. What the side button opens is decided there."),
        )}
        {!app &&
          row(
            <Monitor size={18} />,
            t("This computer"),
            t("Windows does not let an app replace its built-in assistant. A shortcut to summon MiniMuse from anywhere is not built yet."),
          )}
      </div>
    </div>
  );
}

function readState(): State {
  const raw = androidApp()?.assistantState?.();
  return raw === "on" || raw === "off" ? raw : "unknown";
}
