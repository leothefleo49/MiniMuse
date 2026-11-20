import { useEffect, useState } from "react";
import { androidApp } from "../android";
import { api } from "../api";
import { useT } from "../i18n";
import type { SetupPromptEvent } from "../types";

interface SetupPromptModalProps {
  prompt: SetupPromptEvent | null;
  onClose: () => void;
  onCompleted: (prompt: SetupPromptEvent, resumeText?: string) => void;
}

export function SetupPromptModal({ prompt, onClose, onCompleted }: SetupPromptModalProps) {
  const t = useT();
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const app = androidApp();

  useEffect(() => {
    if (!prompt) return;
    const init: Record<string, string> = {};
    if (prompt.base_url) init.base_url = prompt.base_url;
    if (prompt.title && prompt.preset_id) init.name = prompt.title.replace(/^Connect\s+/i, "");
    if (prompt.zapier_quota?.limit) init.monthly_limit = String(prompt.zapier_quota.limit);
    setValues(init);
    setStatusMsg("");
    setErrorMsg("");
  }, [prompt]);

  if (!prompt) return null;

  const setField = (key: string, val: string) => {
    setValues((prev) => ({ ...prev, [key]: val }));
  };

  const handleGrantNativePermission = async () => {
    setErrorMsg("");
    setStatusMsg("");
    const target = (prompt.target || "").toLowerCase();
    try {
      if (app) {
        if (prompt.kind === "phone_control" || target.includes("accessibility") || target === "phone_control") {
          app.openAccessibilitySettings?.();
          setStatusMsg(t("Opened Android Accessibility Settings. Enable MiniMuse and tap Continue below."));
          return;
        }
        if (prompt.kind === "wireless_debugging" || target.includes("wireless") || target.includes("developer")) {
          app.openDeveloperSettings?.();
          setStatusMsg(t("Opened Android Developer Options. Turn on Wireless Debugging and enter the pairing code below."));
          return;
        }
        if (prompt.kind === "live_notifications" || target.includes("live_notification")) {
          app.setNotificationsEnabled(true);
          app.requestPermission?.("notifications");
          app.openNotificationListenerSettings?.();
          setStatusMsg(t("Enabled MiniMuse Live Notifications and opened Notification Settings."));
          return;
        }
        if (target.includes("calendar") || target.includes("contact")) {
          app.syncPhoneCalendarAndContacts?.();
          setStatusMsg(t("Requested phone Calendar & Contacts sync."));
          return;
        }
        const permKey =
          target.includes("notif") ? "notifications" :
          target.includes("loc") ? "location" :
          target.includes("cam") ? "camera" :
          target.includes("mic") ? "microphone" :
          target.includes("near") || target.includes("watch") ? "nearby" :
          target.includes("install") ? "install" : "notifications";
        app.requestPermission?.(permKey);
        setStatusMsg(t("Requested permission on your phone."));
        return;
      }

      if ((target.includes("notif") || prompt.kind === "live_notifications") && "Notification" in window) {
        const res = await Notification.requestPermission();
        setStatusMsg(
          res === "granted"
            ? t("Browser notifications granted.")
            : t("Notification permission was not granted by the browser."),
        );
        return;
      }
      if (target.includes("loc") && "geolocation" in navigator) {
        navigator.geolocation.getCurrentPosition(
          () => setStatusMsg(t("Location access granted.")),
          (err) => setErrorMsg(err.message),
        );
        return;
      }
      setStatusMsg(t("Permission ready. Click Connect & Continue below."));
    } catch (e) {
      setErrorMsg((e as Error).message);
    }
  };

  const handleGoogleSignIn = async () => {
    setBusy(true);
    setErrorMsg("");
    try {
      const res = await api.googleOAuthStart();
      if (res.auth_url) {
        window.open(res.auth_url, "_blank", "noopener,noreferrer");
        setStatusMsg(t("Opened Google Sign-In in a new tab. Once signed in, click Connect & Continue."));
      }
    } catch (e) {
      setErrorMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleSubmit = async () => {
    setBusy(true);
    setErrorMsg("");
    setStatusMsg("");
    try {
      if (prompt.kind === "wireless_debugging" || prompt.target === "wireless_debugging") {
        const pairHp = (values.pair_host_port || "").trim();
        const pairCode = (values.pair_code || "").trim();
        const connHp = (values.connect_host_port || "").trim();
        if (pairHp || connHp) {
          const res = await api.pairPhoneWirelessAdb({
            pair_host_port: pairHp,
            pair_code: pairCode,
            connect_host_port: connHp,
          });
          if (!res.ok) {
            setErrorMsg(res.error || res.output || t("Wireless ADB pairing failed."));
            setBusy(false);
            return;
          }
          setStatusMsg(res.output);
        }
      } else if (prompt.kind === "zapier" || prompt.target === "zapier") {
        const mcpUrl = (values.mcp_url || values.secret || "").trim();
        const limitNum = parseInt(values.monthly_limit || "100", 10);
        if (!Number.isNaN(limitNum) && limitNum > 0) {
          await api.updateZapierQuota({ limit: limitNum });
        }
        if (mcpUrl) {
          await api.addMCP({ name: "zapier", url: mcpUrl, risk: "moderate" });
        }
      } else if (prompt.kind === "calendar" && (values.calendar_url || "").trim()) {
        await api.addCalendarFeed(
          (values.calendar_name || "Calendar").trim(),
          values.calendar_url.trim(),
        );
      } else if (
        prompt.kind === "api_key" ||
        prompt.kind === "custom_api" ||
        prompt.kind === "health_wearable" ||
        Boolean(prompt.preset_id) ||
        Boolean((values.secret || "").trim()) ||
        Boolean((values.base_url || "").trim())
      ) {
        const secret = (values.secret || "").trim();
        const baseUrl = (values.base_url || prompt.base_url || "").trim();
        if (secret || baseUrl || prompt.preset_id) {
          await api.upsertApi({
            preset_id: prompt.preset_id || "custom",
            name: (values.name || prompt.title.replace(/^Connect\s+/i, "") || prompt.target || "Custom API").trim(),
            base_url: baseUrl || undefined,
            auth_type: prompt.auth_type || "bearer",
            secret: secret || undefined,
          });
        }
      }
      onCompleted(prompt, prompt.resume_prompt);
    } catch (e) {
      setErrorMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const fields = prompt.fields ?? [];
  const steps = prompt.steps ?? [];
  const showGoogleBtn =
    prompt.kind === "oauth" ||
    prompt.target === "google" ||
    ["gmail", "google_calendar", "google_drive", "google_tasks", "google_fit"].includes(
      prompt.preset_id || prompt.target,
    );
  const showPermissionBtn =
    prompt.kind === "permission" ||
    prompt.kind === "live_notifications" ||
    prompt.kind === "phone_control" ||
    prompt.kind === "wireless_debugging" ||
    (prompt.kind === "calendar" && Boolean(app));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card setup-prompt-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 540, width: "94%", padding: 20 }}
      >
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div>
            <span className="chip" style={{ marginBottom: 6, display: "inline-block" }}>
              {t("Quick Setup & Permission")}
            </span>
            <h3 style={{ margin: 0 }}>{prompt.title}</h3>
          </div>
          <button type="button" className="ghost small" onClick={onClose} aria-label={t("Close")}>
            ✕
          </button>
        </div>

        <p className="muted" style={{ marginTop: 8, marginBottom: 12 }}>
          {prompt.why}
        </p>

        {prompt.zapier_quota && (
          <div
            className="card"
            style={{
              padding: 12,
              marginBottom: 12,
              border: "1px solid var(--warn, #e0a800)",
              background: "var(--surface-2, rgba(255,190,0,0.08))",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 13 }}>
              {t("Zapier Monthly Quota:")} {prompt.zapier_quota.used} / {prompt.zapier_quota.limit}{" "}
              ({prompt.zapier_quota.remaining} {t("left")})
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
              {t(
                "Zapier has a temporary monthly task allowance. For services you use frequently, we recommend connecting a Direct API so you never run out of quota.",
              )}
            </div>
          </div>
        )}

        {steps.length > 0 && (
          <div
            style={{
              background: "var(--surface-2, rgba(127,127,127,0.08))",
              borderRadius: 10,
              padding: "10px 14px",
              marginBottom: 14,
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 12, marginBottom: 6 }}>
              {t("How to connect:")}
            </div>
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.5 }}>
              {steps.map((s, idx) => (
                <li key={idx} style={{ marginBottom: 4 }}>
                  {s}
                </li>
              ))}
            </ol>
          </div>
        )}

        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {app && prompt.kind === "phone_control" && (
            <button
              type="button"
              className="primary small"
              onClick={() => {
                app.openAppSettings?.();
                setStatusMsg(t("In App Info, tap the three dots (⋮) at top-right → Allow restricted settings, then come back and tap Step 2 below."));
              }}
            >
              {t("1. Allow Restricted Settings (App Info)")}
            </button>
          )}
          {showPermissionBtn && (
            <button type="button" className="secondary small" onClick={() => void handleGrantNativePermission()}>
              {prompt.kind === "wireless_debugging"
                ? t("Open Developer Options")
                : prompt.kind === "phone_control"
                  ? t("2. Open Accessibility Settings")
                  : prompt.kind === "live_notifications"
                    ? t("Enable Live Notifications")
                    : t("Grant Permission")}
            </button>
          )}
          {showGoogleBtn && (
            <button type="button" className="secondary small" disabled={busy} onClick={() => void handleGoogleSignIn()}>
              {t("Sign in with Google")}
            </button>
          )}
          {prompt.key_url && (
            <a
              href={prompt.key_url}
              target="_blank"
              rel="noopener noreferrer"
              className="button ghost small"
              style={{ textDecoration: "none" }}
            >
              {t("Get API Key ↗")}
            </a>
          )}
          {prompt.docs_url && (
            <a
              href={prompt.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className="button ghost small"
              style={{ textDecoration: "none" }}
            >
              {t("API Docs ↗")}
            </a>
          )}
        </div>

        {fields.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
            {fields.map((f) => (
              <label key={f.key} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
                <span>{f.label}</span>
                <input
                  type={f.secret ? "password" : "text"}
                  placeholder={f.placeholder || ""}
                  value={values[f.key] ?? ""}
                  onChange={(e) => setField(f.key, e.target.value)}
                />
              </label>
            ))}
          </div>
        )}

        {statusMsg && (
          <div style={{ fontSize: 13, color: "var(--ok, #1a9c5b)", marginBottom: 10 }}>{statusMsg}</div>
        )}
        {errorMsg && (
          <div style={{ fontSize: 13, color: "var(--danger, #d94141)", marginBottom: 10 }}>{errorMsg}</div>
        )}

        <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            {t("Later")}
          </button>
          <button type="button" className="primary" onClick={() => void handleSubmit()} disabled={busy}>
            {busy ? t("Connecting…") : t("Connect & Continue Task")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function SetupPromptCard({
  event,
  onOpen,
}: {
  event: SetupPromptEvent;
  onOpen: (ev: SetupPromptEvent) => void;
}) {
  const t = useT();
  const done = event.status === "completed";
  return (
    <div
      className="card setup-prompt-card"
      style={{
        padding: "12px 16px",
        margin: "8px 0",
        borderLeft: "4px solid var(--accent, #6366f1)",
      }}
    >
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>{event.title}</div>
          <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
            {event.why}
          </div>
        </div>
        <button
          type="button"
          className={done ? "ghost small" : "primary small"}
          onClick={() => onOpen(event)}
        >
          {done ? t("Reconfigure") : t("Set Up & Connect")}
        </button>
      </div>
    </div>
  );
}
