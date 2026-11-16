import { useEffect, useRef, useState } from "react";
import { Fingerprint, KeyRound, Loader2, LockKeyhole, ShieldCheck } from "lucide-react";
import { authenticateNativeBiometric, nativeBiometricInfo } from "../android";
import { api } from "../api";
import type { LockView } from "../types";
import { BrandMark } from "./BrandMark";
import { readStorage, writeStorage } from "../util";
import { useT } from "../i18n";

/**
 * The lock screen: a PIN, Device Fingerprint / Face ID, or Passkey (NordPass / password manager)
 * over the whole app, for the device someone else might pick up.
 */

export const UNLOCKED_KEY = "nanomuse_unlocked_at";
export const LOCAL_BIO_CRED_KEY = "nanomuse_bio_cred_id";
export const LOCAL_PASSKEY_CRED_KEY = "nanomuse_passkey_cred_id";
const SESSION_UNLOCK_KEY = "nanomuse_unlocked_session";

export function stillUnlocked(timeoutSeconds = 3600): boolean {
  try {
    if (timeoutSeconds === 0) {
      return sessionStorage.getItem(SESSION_UNLOCK_KEY) === "1";
    }
  } catch {
    // ignore sessionStorage errors
  }
  const at = Number(readStorage(UNLOCKED_KEY) || 0);
  const windowMs = Math.max(1, timeoutSeconds) * 1000;
  return at > 0 && Date.now() - at < windowMs;
}

export function markUnlockedNow(): void {
  writeStorage(UNLOCKED_KEY, String(Date.now()));
  try {
    sessionStorage.setItem(SESSION_UNLOCK_KEY, "1");
  } catch {
    // ignore
  }
}

export function clearUnlockedNow(): void {
  writeStorage(UNLOCKED_KEY, "");
  try {
    sessionStorage.removeItem(SESSION_UNLOCK_KEY);
  } catch {
    // ignore
  }
}

export function useAppLock(): boolean | null {
  // null: the runtime has not answered yet
  const [locked, setLocked] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    let lastTimeout = 3600;
    let lastLockOnLeave = false;

    const refresh = () => {
      api
        .lockView()
        .then((view) => {
          if (!live) return;
          lastTimeout = typeof view.timeout_seconds === "number" ? view.timeout_seconds : 3600;
          lastLockOnLeave = !!view.lock_on_leave;
          setLocked(view.enabled && !stillUnlocked(lastTimeout));
        })
        .catch(() => {
          if (live) setLocked(false);
        });
    };

    refresh();

    const onLockNow = () => {
      clearUnlockedNow();
      if (live) setLocked(true);
    };
    const onLockChanged = () => {
      refresh();
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        // "lock when I switch away": leaving the app ends the unlock right there
        if (lastLockOnLeave) {
          clearUnlockedNow();
          if (live) setLocked(true);
        } else {
          try {
            sessionStorage.removeItem(SESSION_UNLOCK_KEY);
          } catch {
            // ignore
          }
        }
      } else if (document.visibilityState === "visible") {
        refresh();
      }
    };

    window.addEventListener("nanomuse:lock-now", onLockNow);
    window.addEventListener("nanomuse:lock-changed", onLockChanged);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      live = false;
      window.removeEventListener("nanomuse:lock-now", onLockNow);
      window.removeEventListener("nanomuse:lock-changed", onLockChanged);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);
  return locked;
}

export function LockGate({ onUnlock }: { onUnlock: () => void }) {
  const t = useT();
  const [view, setView] = useState<LockView | null>(null);
  const [nativeBio] = useState(() => nativeBiometricInfo());
  const [bioAvailable, setBioAvailable] = useState(false);
  const [platformChecked, setPlatformChecked] = useState(false);
  const [webauthnSupported, setWebauthnSupported] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [bioBusy, setBioBusy] = useState<"biometric" | "passkey" | null>(null);
  // the credential ids, fetched up front: WebAuthn demands the tap's own activation,
  // so nothing may be awaited between the tap and navigator.credentials.get
  const bioCidRef = useRef("");
  const passkeyCidRef = useRef("");
  const ref = useRef<HTMLInputElement>(null);

  const nativeBioReady = nativeBio.available && nativeBio.enrolled;

  const pass = () => {
    markUnlockedNow();
    onUnlock();
  };

  const unlockWithWebAuthn = async (kind: "biometric" | "passkey") => {
    if (bioBusy) return;
    setBioBusy(kind);
    setError("");
    try {
      if (kind === "biometric" && nativeBioReady) {
        await authenticateNativeBiometric(
          t("Unlock MiniMuse"),
          t("Confirm your fingerprint or face to continue"),
        );
        pass();
        return;
      }

      const localCid = readStorage(kind === "passkey" ? LOCAL_PASSKEY_CRED_KEY : LOCAL_BIO_CRED_KEY);
      const serverCid = kind === "passkey" ? passkeyCidRef.current : bioCidRef.current;
      const cidStr = (localCid || serverCid || "").trim().replace(/^"|"$/g, "");
      const credentialId = cidStr ? base64UrlBytes(cidStr) : null;

      const pubKeyOptions: PublicKeyCredentialRequestOptions & { hints?: string[] } = {
        challenge: crypto.getRandomValues(new Uint8Array(32)),
        userVerification: "required",
        timeout: 60000,
      };
      if (kind === "biometric") {
        pubKeyOptions.hints = ["client-device"];
        if (credentialId) {
          pubKeyOptions.allowCredentials = [
            { id: credentialId, type: "public-key", transports: ["internal"] },
          ];
        }
      } else if (credentialId) {
        pubKeyOptions.allowCredentials = [{ id: credentialId, type: "public-key" }];
      }

      await navigator.credentials.get({ publicKey: pubKeyOptions });
      pass();
    } catch (e) {
      const err = e as Error;
      if (err?.name === "NotAllowedError" || err?.name === "AbortError") {
        setError(t("Verification was cancelled or unavailable. Try again, or use Reset / turn off App Lock below."));
      } else {
        setError(err?.message || t("The fingerprint did not go through."));
      }
    } finally {
      setBioBusy(null);
    }
  };

  useEffect(() => {
    const hasWebAuthn = typeof PublicKeyCredential !== "undefined" && !!navigator.credentials?.get;
    setWebauthnSupported(hasWebAuthn);
    if (hasWebAuthn && PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
      PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
        .then(setBioAvailable)
        .catch(() => setBioAvailable(false))
        .finally(() => setPlatformChecked(true));
    } else {
      setPlatformChecked(true);
    }
    api
      .lockView()
      .then((v) => {
        setView(v);
        // the ids ride the same fetch so the tap below has nothing left to wait for
        if (v.biometric_credential_id) bioCidRef.current = v.biometric_credential_id;
        if (v.passkey_credential_id) passkeyCidRef.current = v.passkey_credential_id;
      })
      .catch(() => setView({ enabled: false, biometric: false, passkey: false, pin_enabled: false }));
    if (hasWebAuthn) {
      api
        .lockCredential("biometric")
        .then((cid) => {
          if (cid) bioCidRef.current = cid;
        })
        .catch(() => undefined);
      api
        .lockCredential("passkey")
        .then((cid) => {
          if (cid) passkeyCidRef.current = cid;
        })
        .catch(() => undefined);
    }
  }, []);

  useEffect(() => {
    // the PIN is the one method that always works: focus it when it is the only one
    if (view?.pin_enabled && !view.biometric && !view.passkey) {
      ref.current?.focus();
    }
  }, [view]);

  const verify = async () => {
    if (!pin.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.lockVerify(pin.trim());
      if (r.ok) pass();
      else {
        setError(t("Wrong password or PIN. Try again."));
        setPin("");
        ref.current?.focus();
      }
    } catch (e) {
      setError((e as Error).message || t("Could not check the password."));
    } finally {
      setBusy(false);
    }
  };

  // The rescue hatch: any active lock (PIN/password, fingerprint, or passkey) offers
  // a code-verified reset OR one-tap turn-off so a device is never stranded.
  const [reset, setReset] = useState<{ sent_to: string; detail?: string } | null>(null);
  const [resetCode, setResetCode] = useState("");
  const [newPin, setNewPin] = useState("");
  const forgotPin = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.lockResetRequest();
      setReset({ sent_to: r.sent_to, detail: r.detail });
    } catch (e) {
      setError((e as Error).message || t("Could not start the reset."));
    } finally {
      setBusy(false);
    }
  };
  const applyReset = async () => {
    if (busy || !resetCode.trim() || newPin.trim().length < 4) return;
    setBusy(true);
    setError("");
    try {
      await api.lockResetApply(resetCode.trim(), newPin.trim());
      setReset(null);
      setResetCode("");
      setNewPin("");
      pass();
    } catch (e) {
      setError((e as Error).message || t("Could not reset the lock."));
    } finally {
      setBusy(false);
    }
  };
  const disableWithReset = async () => {
    if (busy || !resetCode.trim()) return;
    setBusy(true);
    setError("");
    try {
      await api.lockResetDisable(resetCode.trim());
      writeStorage(LOCAL_BIO_CRED_KEY, "");
      writeStorage(LOCAL_PASSKEY_CRED_KEY, "");
      setReset(null);
      setResetCode("");
      setNewPin("");
      pass();
    } catch (e) {
      setError((e as Error).message || t("Could not turn off the lock."));
    } finally {
      setBusy(false);
    }
  };

  const hasBio = !!(view?.biometric && (nativeBioReady || (view?.biometric_credential_id && bioAvailable)));
  const hasPasskey = !!(view?.passkey && view?.passkey_credential_id && webauthnSupported);
  const hasAnyWebAuthn = hasBio || hasPasskey;
  const hasPin = !!view?.pin_enabled;

  // A lock with no PIN and nothing this device can answer (no platform fingerprint/face,
  // no passkey support) is not a lock on this device: let the person in instead of a
  // dead screen with no buttons.  Waits for the platform check, so a real fingerprint
  // that is still loading is never skipped past.
  useEffect(() => {
    if (view && platformChecked && view.enabled && !hasPin && !hasAnyWebAuthn) {
      markUnlockedNow();
      onUnlock();
    }
  }, [view, platformChecked, hasPin, hasAnyWebAuthn, onUnlock]);

  return (
    <div className="mx-auto flex h-full max-w-sm flex-col items-center justify-center px-8 text-center">
      <BrandMark size={72} />
      <div className="mt-6 flex items-center gap-2 text-[17px] font-semibold">
        <LockKeyhole size={18} className="text-muted" /> {t("Locked")}
      </div>
      <p className="mt-1.5 text-[13.5px] text-muted">
        {hasPin
          ? t("Enter your password or PIN to open {app}.", { app: "MiniMuse" })
          : hasBio && hasPasskey
            ? t("Use Fingerprint, Face ID, or Passkey to open {app}.", { app: "MiniMuse" })
            : hasBio
              ? t("Use Fingerprint or Face ID to open {app}.", { app: "MiniMuse" })
              : t("Use your Passkey to open {app}.", { app: "MiniMuse" })}
      </p>

      {hasBio && (
        <button
          type="button"
          onClick={() => void unlockWithWebAuthn("biometric")}
          disabled={bioBusy !== null}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 text-[15px] font-medium text-accent-fg shadow-sm transition active:scale-[0.98] disabled:opacity-60"
        >
          {bioBusy === "biometric" ? (
            <Loader2 size={18} className="animate-spin" />
          ) : (
            <Fingerprint size={18} />
          )}{" "}
          {t("Unlock with Fingerprint / Face ID")}
        </button>
      )}

      {hasPasskey && (
        <button
          type="button"
          onClick={() => void unlockWithWebAuthn("passkey")}
          disabled={bioBusy !== null}
          className={
            hasBio
              ? "mt-2.5 flex w-full items-center justify-center gap-2 rounded-2xl border border-border bg-surface-2 px-4 py-3 text-[14.5px] font-medium text-fg transition active:scale-[0.98] disabled:opacity-60"
              : "mt-5 flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-3 text-[15px] font-medium text-accent-fg shadow-sm transition active:scale-[0.98] disabled:opacity-60"
          }
        >
          {bioBusy === "passkey" ? (
            <Loader2 size={18} className="animate-spin" />
          ) : (
            <ShieldCheck size={18} />
          )}{" "}
          {t("Unlock with Passkey (NordPass)")}
        </button>
      )}

      {hasPin && (
        <form
          className="mt-4 w-full"
          onSubmit={(e) => {
            e.preventDefault();
            void verify();
          }}
        >
          <input
            ref={ref}
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            autoComplete="off"
            type="password"
            aria-label={t("Password or PIN")}
            placeholder={t("Password or PIN")}
            className="w-full rounded-2xl bg-surface-2 px-4 py-3 text-center text-[17px] outline-none focus:ring-2 focus:ring-accent/40"
          />
          <button
            type="submit"
            disabled={!pin.trim() || busy}
            className={
              hasAnyWebAuthn
                ? "mt-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-border px-4 py-2.5 text-[14px] font-medium text-fg/85 disabled:opacity-60"
                : "mt-4 flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-2.5 font-medium text-accent-fg disabled:opacity-60"
            }
          >
            {busy ? <Loader2 size={16} className="animate-spin" /> : <KeyRound size={16} />} {t("Unlock")}
          </button>
        </form>
      )}

      {view?.enabled && (
        <>
          {reset === null ? (
            <button
              type="button"
              onClick={() => void forgotPin()}
              disabled={busy}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-2xl border border-border px-4 py-2.5 text-[13.5px] font-medium text-muted hover:text-fg transition disabled:opacity-60"
            >
              {busy ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}{" "}
              {hasPin ? t("Forgot password or PIN?") : t("Reset or turn off App Lock")}
            </button>
          ) : (
            <form
              className="mt-4 w-full space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (newPin.trim().length >= 4) {
                  void applyReset();
                } else if (resetCode.trim()) {
                  void disableWithReset();
                }
              }}
            >
              <p className="text-[12.5px] leading-[18px] text-muted">
                {reset.sent_to === "email" && t("The reset code was sent to your e-mail. It is valid for 30 minutes.")}
                {reset.sent_to === "sms" && t("The reset code was sent to your phone. It is valid for 30 minutes.")}
                {reset.sent_to === "terminal" &&
                  (reset.detail ||
                    t("The reset code was saved in your MiniMuse data folder and printed in the terminal."))}
              </p>
              <input
                value={resetCode}
                onChange={(e) => setResetCode(e.target.value)}
                autoComplete="off"
                aria-label={t("Reset code")}
                placeholder={t("Reset code")}
                className="w-full rounded-2xl bg-surface-2 px-4 py-3 text-center text-[16px] tracking-[0.2em] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button
                type="button"
                onClick={() => void disableWithReset()}
                disabled={busy || !resetCode.trim()}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-rose-600 px-4 py-2.5 font-medium text-white disabled:opacity-60"
              >
                {busy ? <Loader2 size={16} className="animate-spin" /> : null} {t("Turn off App Lock")}
              </button>
              <input
                value={newPin}
                onChange={(e) => setNewPin(e.target.value)}
                type="password"
                autoComplete="off"
                aria-label={t("Or set a new password or PIN")}
                placeholder={t("Or set a new password / PIN (4+ chars)")}
                className="w-full rounded-2xl bg-surface-2 px-4 py-3 text-center text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
              />
              <button
                type="submit"
                disabled={busy || !resetCode.trim() || newPin.trim().length < 4}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-4 py-2.5 font-medium text-accent-fg disabled:opacity-60"
              >
                {busy ? <Loader2 size={16} className="animate-spin" /> : <KeyRound size={16} />} {t("Set new password / PIN")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setReset(null);
                  setResetCode("");
                  setNewPin("");
                  setError("");
                }}
                disabled={busy}
                className="flex w-full items-center justify-center gap-2 rounded-2xl border border-border px-4 py-2.5 text-[13.5px] font-medium text-muted hover:text-fg transition disabled:opacity-60"
              >
                {t("Back")}
              </button>
            </form>
          )}
        </>
      )}

      {error && <p className="mt-3 text-[12.5px] text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}

function base64UrlBytes(value: string): Uint8Array<ArrayBuffer> {
  const pad = value.length % 4 === 0 ? "" : "=".repeat(4 - (value.length % 4));
  const b64 = value.replace(/-/g, "+").replace(/_/g, "/") + pad;
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
