import { LockKeyhole, ShieldCheck } from "lucide-react";
import { useId, useRef } from "react";
import { useT } from "../i18n";
import { useFocusTrap } from "./useFocusTrap";
import { cx } from "../util";
import { primaryBtn, secondaryBtn } from "./Form";

const LOCK_PROMPT_KEY = "nanomuse_lock_prompt_dismissed";

export function lockPromptDismissed(): boolean {
  try {
    return localStorage.getItem(LOCK_PROMPT_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissLockPrompt(): void {
  try {
    localStorage.setItem(LOCK_PROMPT_KEY, "1");
  } catch {
    /* ignore storage failures */
  }
}

/**
 * Startup prompt when App Lock is off on this device: keeps MiniMuse open by default,
 * while encouraging the user at the beginning to protect stored passwords and data.
 */
export function AppLockPrompt({ onSetup, onDismiss }: { onSetup: () => void; onDismiss: () => void }) {
  const t = useT();
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useFocusTrap(panel, true);

  const chooseLater = () => {
    dismissLockPrompt();
    onDismiss();
  };

  const chooseSetup = () => {
    dismissLockPrompt();
    onSetup();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-[2px] sm:items-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div ref={panel} className="safe-bottom w-full max-w-[460px] rounded-t-[28px] bg-bg p-6 shadow-2xl sm:rounded-[28px]">
        <div className="space-y-4">
          <div className="flex items-start gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent/12 text-accent">
              <LockKeyhole size={20} />
            </span>
            <div>
              <h2 id={titleId} className="text-[20px] font-bold tracking-tight">
                {t("Protect MiniMuse on this device?")}
              </h2>
              <p className="mt-1 text-[13.5px] leading-relaxed text-muted">
                {t("App Lock is disabled by default on this device. Because MiniMuse can hold passwords, personal memories, and device permissions, adding a PIN, fingerprint, Face ID, or passkey keeps your chats private.")}
              </p>
            </div>
          </div>

          <div className="flex items-start gap-2.5 rounded-2xl bg-surface-2 px-3.5 py-2.5 text-[12.5px] leading-snug text-muted">
            <ShieldCheck size={16} className="mt-0.5 shrink-0 text-emerald-500" />
            <span>{t("Locks stay per device, and you can change or turn them off anytime in Settings.")}</span>
          </div>

          <div className="flex gap-2 pt-1">
            <button type="button" onClick={chooseLater} className={secondaryBtn}>
              {t("Not now")}
            </button>
            <button type="button" onClick={chooseSetup} className={cx(primaryBtn, "flex-1 py-3")}>
              {t("Set up App Lock")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
