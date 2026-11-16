import { ArrowRight, CalendarDays, Check, ChevronDown, ChevronRight, Cloud, Info, Loader2, Lock, MapPin, Palette, RefreshCw, Smartphone, Sparkles, Zap } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ACCENTS, BUBBLE_PRESETS, setAccent, setBubble, useAccent, useBubble } from "../accent";
import { androidApp } from "../android";
import { api } from "../api";
import { DRAGON } from "../avatars";
import { Avatar } from "../components/Avatar";
import { NOTICE_URL } from "../components/CommunityNotice";
import { AVATAR_COLORS } from "../components/AvatarPicker";
import { inputCls } from "../components/Form";
import { IdentityForm, identityBody, identityOf, type Identity } from "../components/IdentityForm";
import { SignIn } from "../components/SignIn";
import { getLocale, useT } from "../i18n";
import { ownKeyLine } from "../region";
import { useStore } from "../store";
import { BG_PRESETS, setBg, setUiScale, useBg, useUiScale } from "../theme";
import type { ConnectionsData, NearbyPeer } from "../types";
import { cx } from "../util";
import { ApiIntegrationsCard } from "../components/ApiIntegrationsCard";
import { CalendarCard, ContactsCard, EmailCard, ModelCard, primaryBtn, secondaryBtn, syncPhoneGoogleToServer } from "./ConnectionsScreen";

type Step = "welcome" | "appearance" | "list" | "model" | "connect";
const STEP_STORAGE_KEY = "nanomuse_onboarding_step";
const IS_TEST_MODE = import.meta.env.MODE === "test";

function loadInitialStep(): Step {
  if (IS_TEST_MODE) return "welcome";
  try {
    const raw = sessionStorage.getItem(STEP_STORAGE_KEY);
    if (raw === "welcome" || raw === "appearance" || raw === "list" || raw === "model" || raw === "connect") {
      return raw;
    }
  } catch {
    // ignore storage restriction
  }
  return "welcome";
}

function persistStep(next: Step): void {
  if (IS_TEST_MODE) return;
  try {
    sessionStorage.setItem(STEP_STORAGE_KEY, next);
  } catch {
    // ignore
  }
}

function clearPersistedStep(): void {
  if (IS_TEST_MODE) return;
  try {
    sessionStorage.removeItem(STEP_STORAGE_KEY);
  } catch {
    // ignore
  }
}

/**
 * Local-first first run:
 * 1. **Welcome**: creature preview + core local/privacy capabilities (no solid header bar covering the avatar).
 * 2. **Appearance & AI**: theme/canvas tint, accent colour, scale, creature appearance, AI name/vibe & optional tabs.
 * 3. **Checklist**: quick review of Appearance, Local/API Model (OpenRouter / Ollama / own key hero), Multi-account & API connections, and Start.
 */
export function Onboarding() {
  const { state, setTab, dismissOnboarding, refreshSettings, toast } = useStore();
  const [step, setStepRaw] = useState<Step>(loadInitialStep);
  const setStep = (next: Step) => {
    setStepRaw(next);
    persistStep(next);
  };
  const [conn, setConn] = useState<ConnectionsData | null>(null);
  const [starting, setStarting] = useState(false);
  const [savingAppearance, setSavingAppearance] = useState(false);
  const [cloudOpen, setCloudOpen] = useState(false);
  const [cloudBusy, setCloudBusy] = useState(false);
  const [pairOpen, setPairOpen] = useState(false);
  const [nearby, setNearby] = useState<NearbyPeer[]>([]);
  const [pairTarget, setPairTarget] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [bundleText, setBundleText] = useState("");
  const [pairing, setPairing] = useState(false);
  const [keepLocalLookOnPair, setKeepLocalLookOnPair] = useState(false);
  const [connectTab, setConnectTab] = useState<"all" | "google" | "calendar" | "apis">("all");
  const [quickBusy, setQuickBusy] = useState<"phone" | "free" | null>(null);
  const [identity, setIdentity] = useState<Identity>(() => identityOf(state.profile, DRAGON, AVATAR_COLORS[0]));
  const [identityDirty, setIdentityDirty] = useState(false);

  const t = useT();
  const bg = useBg();
  const accent = useAccent();
  const bubble = useBubble();
  const scale = useUiScale();

  useEffect(() => {
    if (!pairOpen) return;
    api
      .syncDiscover()
      .then((res) => setNearby(res.nearby ?? []))
      .catch(() => undefined);
  }, [pairOpen]);

  const applyIncomingUiPrefs = (prefs?: Record<string, string> | null) => {
    if (!prefs || keepLocalLookOnPair) return;
    if (typeof prefs.bg === "string") setBg(prefs.bg);
    if (typeof prefs.accent === "string") setAccent(prefs.accent);
    if (typeof prefs.bubble === "string") setBubble(prefs.bubble);
    if (prefs.scale) {
      const n = Number(prefs.scale);
      if (Number.isFinite(n) && n >= 0.85 && n <= 1.2) setUiScale(n);
    }
  };

  const handlePairAndSyncSetup = async () => {
    setPairing(true);
    try {
      if (keepLocalLookOnPair) {
        await api.syncSetDeviceOverrides({ enabled: true, keep_local_appearance: true }).catch(() => undefined);
      }
      if (bundleText.trim().startsWith("{")) {
        const parsed = JSON.parse(bundleText.trim()) as Record<string, unknown>;
        const res = await api.applySetupBundle(parsed);
        applyIncomingUiPrefs(res.ui_preferences);
        await loadConn();
        await refreshSettings();
        toast(t("Synced setup, profile, and keys from transfer bundle!"));
        setPairOpen(false);
        return;
      }
      if (!pairTarget.trim() && !pairCode.trim()) {
        toast(t("Enter the other device's address + 6-character code or pick a nearby device."));
        return;
      }
      const res = await api.syncPairClaim({
        target: pairTarget.trim(),
        code: pairCode.trim(),
        sync_side_chats: true,
      });
      applyIncomingUiPrefs(res.setup_synced?.ui_preferences);
      await loadConn();
      await refreshSettings();
      toast(t("Paired with {peer} and synced setup!", { peer: res.peer ?? "device" }));
      setPairOpen(false);
    } catch (e) {
      toast((e as Error).message || t("Could not pair with that device."));
    } finally {
      setPairing(false);
    }
  };

  const showCalendarTab = state.settings?.ui?.show_calendar !== "false";
  const showLocationTab = state.settings?.ui?.show_location !== "false";
  const mapApp = state.settings?.ui?.map_app || "google";

  useEffect(() => {
    if (!identityDirty && state.profile) {
      setIdentity(identityOf(state.profile, DRAGON, AVATAR_COLORS[0]));
    }
  }, [state.profile, identityDirty]);

  const toggleUiSetting = async (patch: Record<string, string>) => {
    try {
      await api.updateSettings({ ui: { ...(state.settings?.ui ?? {}), ...patch } });
      await refreshSettings();
    } catch (e) {
      toast((e as Error).message || t("Could not save setting"));
    }
  };

  const pickCloudModel = async () => {
    setCloudBusy(true);
    try {
      await api.cloudUseAsModel();
      toast(t("The Cloud model is in use."));
      await loadConn();
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setCloudBusy(false);
    }
  };

  const [connError, setConnError] = useState<string | null>(null);
  const connGen = useRef(0);
  const active = useRef(false);
  const loadConn = async () => {
    if (!active.current) return;
    const mine = ++connGen.current;
    try {
      setConnError(null);
      const next = await api.connections();
      if (connGen.current !== mine) return;
      setConn(next);
    } catch (e) {
      if (connGen.current !== mine) return;
      setConnError(t((e as Error).message) || t("Could not load the connections."));
    }
  };
  useEffect(() => {
    active.current = true;
    void loadConn();
    return () => {
      active.current = false;
      connGen.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when the server says connections changed
  }, [state.connectionsVersion]);

  const saveAppearanceIfDirty = async () => {
    if (!identityDirty) return;
    setSavingAppearance(true);
    try {
      await api.updateSettings({ profile: identityBody(identity) });
      setIdentityDirty(false);
      await refreshSettings();
    } catch (e) {
      toast((e as Error).message || t("Could not save appearance"));
    } finally {
      setSavingAppearance(false);
    }
  };

  const finish = async () => {
    clearPersistedStep();
    await saveAppearanceIfDirty();
    try {
      await api.onboarded(true);
      await refreshSettings();
    } catch {
      /* server remembers next time */
    }
    dismissOnboarding();
    setTab("chat");
  };

  const start = async () => {
    clearPersistedStep();
    setStarting(true);
    await saveAppearanceIfDirty();
    try {
      await api.firstrunStart(getLocale() === "zh-CN" ? "zh" : "en");
    } catch {
      await api.onboarded(true).catch(() => undefined);
    }
    try {
      await refreshSettings();
    } catch {
      /* snapshot carries it */
    }
    setStarting(false);
    dismissOnboarding();
    setTab("chat");
  };

  const preview = {
    ...identity,
    name: identity.name.trim() || "MiniMuse",
    proactivity: "default" as const,
    proactive: true,
    goal_interval_minutes: 60,
    quiet_hours: "",
  };

  const modelReady = conn
    ? conn.llm.key_source === "vault" ||
      conn.llm.key_source === "config" ||
      !!conn.providers[presetOf(conn)]?.no_key ||
      (!!conn.llm.endpoint && conn.llm.key_source === "none")
    : false;
  const emailAccountsCount = conn?.email_accounts?.length ?? (conn?.email.configured ? 1 : 0);
  const googleCount = conn?.google?.accounts?.length ?? (conn?.google?.signed_in ? 1 : 0);
  const apiCount = conn?.apis?.filter((a) => a.enabled).length ?? 0;
  const connected = !!conn && (
    emailAccountsCount > 0 ||
    googleCount > 0 ||
    apiCount > 0 ||
    conn.calendar.feeds.length > 0 ||
    conn.contacts.sources.length > 0
  );

  const progress: Step[] = ["welcome", "appearance", "list"];
  const idx = step === "welcome" ? 0 : step === "appearance" ? 1 : 2;

  const adjustLaterNotice = (
    <div className="inline-flex items-center gap-1.5 rounded-full bg-surface-2/80 px-3 py-1 text-[11.5px] text-muted">
      <Info size={13} className="text-accent shrink-0" />
      <span>{t("Everything you choose during setup can be changed or adjusted anytime later in Settings.")}</span>
    </div>
  );

  const pairDeviceSetupCard = (
    <div className="w-full rounded-3xl border border-border/70 bg-surface p-3.5 text-left">
      <button
        type="button"
        onClick={() => setPairOpen((v) => !v)}
        aria-expanded={pairOpen}
        className="flex w-full items-center justify-between gap-2 text-left"
      >
        <span className="flex items-center gap-2 text-[13px] font-semibold text-fg">
          <Smartphone size={15} className="text-accent shrink-0" />
          {t("Sync setup from another device or transfer Muse data")}
        </span>
        <ChevronDown size={15} className={cx("text-muted transition-transform", pairOpen && "rotate-180")} />
      </button>
      {pairOpen && (
        <div className="mt-3 space-y-2.5 border-t border-border/60 pt-3">
          <p className="text-[12px] text-muted">
            {t("Pair with your PC or phone right now to copy your models, API keys, profile, AI avatar/animations, and chats — no need to enter them twice.")}
          </p>
          {nearby.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {nearby.map((peer) => (
                <button
                  key={peer.device_id}
                  type="button"
                  onClick={() => setPairTarget(peer.urls[0] ?? "")}
                  className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11.5px] font-medium text-accent"
                >
                  <RefreshCw size={11} /> {peer.name} ({peer.urls[0]})
                </button>
              ))}
            </div>
          )}
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input
              value={pairTarget}
              onChange={(e) => setPairTarget(e.target.value)}
              placeholder={t("Other device URL (e.g. http://192.168.1.25:8787)")}
              className="w-full rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[12.5px] outline-none focus:border-accent"
            />
            <input
              value={pairCode}
              onChange={(e) => setPairCode(e.target.value)}
              placeholder={t("6-character pair code (e.g. A7K-9M2)")}
              className="w-full rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[12.5px] font-mono outline-none focus:border-accent"
            />
          </div>
          <textarea
            value={bundleText}
            onChange={(e) => setBundleText(e.target.value)}
            rows={2}
            placeholder={t("Or paste a Setup & Profile Transfer JSON bundle here")}
            className="w-full rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[12px] font-mono outline-none focus:border-accent resize-none"
          />
          <label className="flex items-center gap-2 text-[12px] text-muted cursor-pointer">
            <input
              type="checkbox"
              checked={keepLocalLookOnPair}
              onChange={(e) => setKeepLocalLookOnPair(e.target.checked)}
              className="rounded border-border"
            />
            <span>{t("Keep this device's own theme & appearance (optional device-specific override)")}</span>
          </label>
          <button
            type="button"
            disabled={pairing}
            onClick={() => void handlePairAndSyncSetup()}
            className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-accent px-4 py-2 text-[13px] font-semibold text-accent-fg disabled:opacity-50"
          >
            {pairing ? <Loader2 size={14} className="animate-spin" /> : null}
            {t("Pair & Sync Setup")}
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div className="relative mx-auto flex h-full w-full max-w-[760px] flex-col bg-bg sm:border-x sm:border-border wide:max-w-[1080px] wide:border-x-0">
      {/* Keep safe-area clearance outside the bar so tall phone cutouts do not bloat the top bar */}
      <div className="shrink-0 safe-top bg-bg" />
      <header
        data-testid="onboarding-topbar"
        className="shrink-0 border-b border-border/50 bg-surface/85 backdrop-blur-md"
      >
        <div className="flex h-11 items-center justify-between px-6 wide:px-10">
          <div className="flex h-7 items-center gap-1.5">
            {progress.map((s, i) => (
              <span key={s} className={cx("h-1.5 rounded-full transition-all", i <= idx ? "w-5 bg-accent" : "w-1.5 bg-border")} />
            ))}
          </div>
          <button
            type="button"
            onClick={() => void finish()}
            className="inline-flex h-7 items-center justify-center rounded-full px-2.5 text-[12.5px] leading-none font-medium text-muted hover:bg-surface-2/80 hover:text-fg"
          >
            {t("Skip setup")}
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-5 pb-6 wide:px-10">
        {step === "welcome" && (
          <div className="flex min-h-full flex-col items-center justify-start pt-4 pb-4 text-center sm:justify-center">
            <Avatar profile={preview} size={96} />
            <span className="mt-2.5 rounded-full bg-surface-2 px-3 py-1 text-[12px] font-medium text-fg/80">
              {preview.name} · {t("Local-first assistant")}
            </span>
            <h1 className="mt-2.5 text-[28px] font-bold tracking-tight">{t("Meet {name}", { name: preview.name })}</h1>
            <p className="mt-1 max-w-sm text-[14.5px] leading-relaxed text-muted wide:max-w-lg">
              {t("Your local-first AI that automates real work, connects your accounts, and keeps keys encrypted on your own device.")}
            </p>
            <div className="mt-2.5">{adjustLaterNotice}</div>
            <div className="mt-3.5 w-full max-w-sm wide:max-w-xl">{pairDeviceSetupCard}</div>
            <ul className="mt-3.5 w-full max-w-sm space-y-2.5 text-left wide:max-w-xl">
              <Point n={1} title={t("Local & OpenRouter ready.")} body={t("Run local Ollama models or your own OpenRouter, OpenAI, Anthropic, or Gemini keys without vendor lock-in.")} />
              <Point n={2} title={t("Multi-account & 36+ APIs.")} body={t("Connect multiple Google and Email accounts plus custom REST/OpenAPI tools the AI learns automatically.")} />
              <Point n={3} title={t("It asks first where it matters.")} body={t("Sentinel reviews sensitive actions, and your secrets stay inside a local encrypted vault.")} />
            </ul>
            <p className="mt-3.5 max-w-sm text-[13px] text-muted wide:max-w-lg">
              {t("Next: customize your theme, creature appearance, name, and optional Calendar & Location tabs.")}
            </p>
            <p className="mt-2 max-w-sm text-[12.5px] text-muted wide:max-w-lg">
              <a href={NOTICE_URL} target="_blank" rel="noreferrer" className="underline decoration-dotted underline-offset-2 hover:text-accent">
                {t("MiniMuse is a non-profit open-source community project, free forever.")}
              </a>
            </p>
          </div>
        )}

        {step === "appearance" && (
          <div className="pt-4 space-y-4">
            <div className="flex items-center gap-3.5">
              <Avatar profile={preview} size={72} />
              <div className="min-w-0">
                <h1 className="text-[24px] font-bold tracking-tight">{t("Theme & AI appearance")}</h1>
                <p className="text-[13.5px] text-muted">{t("Pick your canvas style, creature look, AI name, and navigation tabs.")}</p>
              </div>
            </div>
            <div>{adjustLaterNotice}</div>

            <section className="rounded-3xl border border-border/70 bg-surface p-4 space-y-4">
              <div>
                <div className="flex items-center gap-1.5 text-[13px] font-semibold">
                  <Palette size={15} className="text-accent" /> {t("Theme & canvas background")}
                </div>
                <div className="mt-2.5 grid grid-cols-4 gap-2 sm:grid-cols-8">
                  {BG_PRESETS.map((p) => {
                    const activePreset = bg === p.value;
                    return (
                      <button
                        key={p.label}
                        type="button"
                        onClick={() => setBg(p.value)}
                        className={cx(
                          "flex flex-col items-center gap-1 rounded-2xl border p-2 text-[11px] transition-colors",
                          activePreset ? "border-accent bg-accent/10 font-medium text-accent" : "border-border/70 text-muted hover:text-fg",
                        )}
                      >
                        <span className="h-6 w-6 rounded-full border border-border/80 shadow-xs" style={{ backgroundColor: p.color }} />
                        <span className="truncate w-full text-center">{t(p.label)}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="mt-2.5 flex flex-wrap items-center gap-2">
                  <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-border/70 bg-surface-2/60 px-3 py-1.5 text-[12px] font-medium text-fg hover:border-accent">
                    <span
                      className="h-4 w-4 shrink-0 rounded-full border border-border"
                      style={{ background: bg.startsWith("#") ? bg : "conic-gradient(#ff5c5c, #ffb84d, #48c774, #0095d4, #9b6dff, #ff5c5c)" }}
                    />
                    <span>{t("Custom colour")}</span>
                    <input
                      type="color"
                      aria-label={t("Custom canvas colour")}
                      value={bg.startsWith("#") ? bg : "#ffffff"}
                      onChange={(e) => setBg(e.target.value)}
                      className="h-5 w-6 cursor-pointer border-0 bg-transparent p-0"
                    />
                  </label>
                  {bg.startsWith("#") && (
                    <span className="rounded-full bg-accent/10 px-2.5 py-0.5 font-mono text-[11px] text-accent">{bg}</span>
                  )}
                </div>
              </div>

              <div>
                <div className="text-[12.5px] font-medium text-muted">{t("Accent colour")}</div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {ACCENTS.map((a) => {
                    const activeAccent = accent === a.value;
                    return (
                      <button
                        key={a.label}
                        type="button"
                        onClick={() => setAccent(a.value)}
                        className={cx(
                          "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px]",
                          activeAccent ? "border-accent bg-accent/10 font-medium text-fg" : "border-border/70 text-muted hover:text-fg",
                        )}
                      >
                        <span className="h-3.5 w-3.5 rounded-full" style={{ backgroundColor: a.color }} />
                        {t(a.label)}
                      </button>
                    );
                  })}
                  <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-border/70 bg-surface-2/60 px-3 py-1.5 text-[12.5px] font-medium text-fg hover:border-accent">
                    <span
                      className="h-3.5 w-3.5 shrink-0 rounded-full border border-border"
                      style={{ background: accent.startsWith("#") ? accent : "conic-gradient(#ff5c5c, #ffb84d, #48c774, #0064d4, #9b6dff, #ff5c5c)" }}
                    />
                    <span>{t("Custom")}</span>
                    <input
                      type="color"
                      aria-label={t("Custom accent colour")}
                      value={accent.startsWith("#") ? accent : "#0064d4"}
                      onChange={(e) => setAccent(e.target.value)}
                      className="h-5 w-6 cursor-pointer border-0 bg-transparent p-0"
                    />
                  </label>
                  {accent.startsWith("#") && (
                    <span className="rounded-full bg-accent/10 px-2.5 py-0.5 font-mono text-[11px] text-accent">{accent}</span>
                  )}
                </div>
              </div>

              <div>
                <div className="text-[12.5px] font-medium text-muted">{t("Chat bubble colour")}</div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setBubble("")}
                    className={cx(
                      "rounded-full border px-3 py-1 text-[12px]",
                      !bubble ? "border-accent bg-accent/10 font-medium text-accent" : "border-border/70 text-muted",
                    )}
                  >
                    {t("Follow accent")}
                  </button>
                  {BUBBLE_PRESETS.map((b) => {
                    const activeBubble = bubble === b.value;
                    return (
                      <button
                        key={b.label}
                        type="button"
                        onClick={() => setBubble(b.value)}
                        className={cx(
                          "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px]",
                          activeBubble ? "border-accent bg-accent/10 font-medium text-fg" : "border-border/70 text-muted hover:text-fg",
                        )}
                      >
                        <span className="h-3 w-3 rounded-full" style={{ backgroundColor: b.color }} />
                        {t(b.label)}
                      </button>
                    );
                  })}
                  <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-border/70 bg-surface-2/60 px-3 py-1 text-[12px] font-medium text-fg hover:border-accent">
                    <span
                      className="h-3.5 w-3.5 shrink-0 rounded-full border border-border"
                      style={{ background: bubble.startsWith("#") ? bubble : accent.startsWith("#") ? accent : "#0064d4" }}
                    />
                    <span>{t("Custom")}</span>
                    <input
                      type="color"
                      aria-label={t("Custom bubble colour")}
                      value={bubble.startsWith("#") ? bubble : accent.startsWith("#") ? accent : "#0064d4"}
                      onChange={(e) => setBubble(e.target.value)}
                      className="h-5 w-6 cursor-pointer border-0 bg-transparent p-0"
                    />
                  </label>
                </div>
              </div>

              <div>
                <div className="text-[12.5px] font-medium text-muted">{t("Display size")}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  {[
                    { v: 0.9, l: "Compact" },
                    { v: 1, l: "Default" },
                    { v: 1.1, l: "Large" },
                  ].map((s) => (
                    <button
                      key={s.l}
                      type="button"
                      onClick={() => setUiScale(s.v)}
                      className={cx(
                        "rounded-full border px-3 py-1 text-[12.5px]",
                        Math.abs(scale - s.v) < 0.04 ? "border-accent bg-accent/10 font-medium text-accent" : "border-border/70 text-muted",
                      )}
                    >
                      {t(s.l)}
                    </button>
                  ))}
                </div>
              </div>
            </section>

            <section className="rounded-3xl border border-border/70 bg-surface p-4 space-y-3">
              <div className="flex items-center gap-1.5 text-[13px] font-semibold">
                <Sparkles size={15} className="text-accent" /> {t("AI name, avatar & personality")}
              </div>
              <IdentityForm
                value={identity}
                onChange={(next) => {
                  setIdentity(next);
                  setIdentityDirty(true);
                }}
                inputCls={inputCls}
              />
            </section>

            <section className="rounded-3xl border border-border/70 bg-surface p-4 space-y-3">
              <div className="text-[13px] font-semibold">{t("Optional tabs & default maps")}</div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => void toggleUiSetting({ show_calendar: showCalendarTab ? "false" : "true" })}
                  className={cx(
                    "flex items-center justify-between rounded-2xl border px-3.5 py-2.5 text-left text-[13px]",
                    showCalendarTab ? "border-accent/40 bg-accent/8" : "border-border/70",
                  )}
                >
                  <span className="flex items-center gap-2 font-medium">
                    <CalendarDays size={15} className="text-accent" /> {t("Calendar tab")}
                  </span>
                  <span className={cx("rounded-full px-2 py-0.5 text-[11px]", showCalendarTab ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted")}>
                    {showCalendarTab ? t("On") : t("Off")}
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => void toggleUiSetting({ show_location: showLocationTab ? "false" : "true" })}
                  className={cx(
                    "flex items-center justify-between rounded-2xl border px-3.5 py-2.5 text-left text-[13px]",
                    showLocationTab ? "border-accent/40 bg-accent/8" : "border-border/70",
                  )}
                >
                  <span className="flex items-center gap-2 font-medium">
                    <MapPin size={15} className="text-accent" /> {t("Location tab")}
                  </span>
                  <span className={cx("rounded-full px-2 py-0.5 text-[11px]", showLocationTab ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted")}>
                    {showLocationTab ? t("On") : t("Off")}
                  </span>
                </button>
              </div>
              {showLocationTab && (
                <div className="flex flex-wrap items-center gap-1.5 pt-1">
                  <span className="text-[12px] text-muted mr-1">{t("Open addresses in:")}</span>
                  {[
                    { id: "google", label: "Google Maps" },
                    { id: "apple", label: "Apple Maps" },
                    { id: "waze", label: "Waze" },
                    { id: "osm", label: "OpenStreetMap" },
                  ].map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => void toggleUiSetting({ map_app: m.id })}
                      className={cx(
                        "rounded-full border px-2.5 py-1 text-[11.5px]",
                        mapApp === m.id ? "border-accent bg-accent/10 font-medium text-accent" : "border-border/70 text-muted",
                      )}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {step === "list" && (
          <div className="pt-4 space-y-4">
            <div className="flex items-center gap-3">
              <Avatar profile={preview} size={56} />
              <div className="min-w-0">
                <h1 className="text-[24px] font-bold tracking-tight truncate">{t("Set it up")}</h1>
                <p className="text-[13.5px] text-muted truncate">{t("Local-first setup for {name}. Ready in a minute.", { name: preview.name })}</p>
              </div>
            </div>
            <div>{adjustLaterNotice}</div>
            {pairDeviceSetupCard}
            <ol className="space-y-2">
              <Item
                done
                title={t("Appearance & identity")}
                body={`${preview.name} · ${BG_PRESETS.find((b) => b.value === bg)?.label ?? "Custom"}`}
                onClick={() => setStep("appearance")}
              />
              <Item
                done={modelReady}
                title={t("Add a local or API model")}
                body={
                  modelReady
                    ? conn?.llm.cloud
                      ? `MiniMuse Cloud · ${conn.llm.model}`
                      : `${conn?.llm.model} · ${hostOf(conn?.llm.base_url ?? "")}`
                    : t("OpenRouter, Ollama, LM Studio, or your own API key (recommended).")
                }
                onClick={() => setStep("model")}
              />
              <Item
                done={connected}
                optional
                title={t("Connect Google, Email, Calendar & APIs")}
                body={
                  connected
                    ? t("{accounts} accounts & {apis} API integrations active. Tap to add more.", {
                        accounts: emailAccountsCount + googleCount,
                        apis: apiCount,
                      })
                    : t("Add multiple Google/Email accounts, Calendar feeds, Contacts, or 36+ APIs.")
                }
                onClick={() => setStep("connect")}
              />
              <Item
                locked={!modelReady}
                done={false}
                title={t("Start chatting")}
                body={modelReady ? t("Open chat with {name}.", { name: preview.name }) : t("Add a model above to unlock.")}
                onClick={() => modelReady && !starting && void start()}
              />
            </ol>
          </div>
        )}

        {step === "model" && (
          <div className="pt-4 space-y-4">
            <div>
              <h1 className="text-[26px] font-bold tracking-tight">{t("Choose your local or API model")}</h1>
              <p className="mt-1 text-[14px] text-muted">
                {modelReady
                  ? conn?.llm.cloud
                    ? t("MiniMuse Cloud is currently active. Add your own key or local Ollama below anytime.")
                    : t("Model ready ({model}). Add additional OpenRouter / provider keys or switch below.", { model: conn?.llm.model ?? "" })
                  : t("OpenRouter (many models, one key), Ollama / LM Studio (offline), or direct Anthropic / OpenAI / Gemini keys. Stored encrypted in your local vault.")}
              </p>
              <div className="mt-2">{adjustLaterNotice}</div>
            </div>
            {conn ? (
              <>
                {!modelReady && (
                  <p className="px-1 text-[12.5px] text-muted">
                    <span className="font-medium text-fg">{t("Local & BYOK")}</span> · {ownKeyLine(t, state.hub?.account)}
                  </p>
                )}
                <ModelCard
                  data={conn}
                  onChange={() => void loadConn()}
                  onSaved={() => {
                    void loadConn();
                    setStep("connect");
                  }}
                  compact
                />

                {/* Cloud tucked cleanly into a compact optional accordion below local providers */}
                {!modelReady && state.settings?.cloud_mode !== "local" && (
                  <div className="rounded-3xl border border-border/60 bg-surface-2/40 p-4">
                    {state.hub?.account.signed_in ? (
                      <button
                        type="button"
                        disabled={cloudBusy}
                        onClick={() => void pickCloudModel()}
                        className="flex w-full items-center gap-3 text-left"
                      >
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface text-accent">
                          {cloudBusy ? <Loader2 size={17} className="animate-spin" /> : <Cloud size={17} />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[13.5px] font-medium">{t("Or use MiniMuse Cloud model")}</span>
                          <span className="block text-[12px] text-muted">{t("Signed in as {hint}", { hint: state.hub.account.hint })}</span>
                        </span>
                        <ChevronRight size={15} className="shrink-0 text-muted" />
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => setCloudOpen((v) => !v)}
                          className="flex w-full items-center justify-between gap-2 text-left"
                        >
                          <div className="flex items-center gap-2.5">
                            <Cloud size={16} className="text-muted" />
                            <div>
                              <div className="text-[13.5px] font-medium">{t("Optional: MiniMuse Cloud")}</div>
                              <div className="text-[11.5px] text-muted">{t("Prefer not to use your own key yet? Sign in for the free starter allowance.")}</div>
                            </div>
                          </div>
                          <ChevronDown size={15} className={cx("text-muted transition-transform", cloudOpen && "rotate-180")} />
                        </button>
                        {cloudOpen && (
                          <div className="mt-3 border-t border-border/50 pt-3">
                            <SignIn onSignedIn={() => void loadConn()} useAsModel />
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </>
            ) : connError ? (
              <div className="rounded-3xl border border-border/70 bg-surface p-4 text-center">
                <p className="text-[13.5px] text-muted">{connError}</p>
                <button type="button" onClick={() => void loadConn()} className="mt-3 rounded-full bg-surface-2 px-4 py-1.5 text-[13px] font-medium">
                  {t("Retry")}
                </button>
              </div>
            ) : (
              <div className="flex justify-center py-8 text-muted">
                <Loader2 className="animate-spin" size={20} />
              </div>
            )}
          </div>
        )}

        {step === "connect" && (
          <div className="pt-4 space-y-4">
            <div>
              <h1 className="text-[26px] font-bold tracking-tight">{t("Connect multiple accounts & APIs")}</h1>
              <p className="mt-1 text-[14px] text-muted">
                {t("Optional. Sync Google Calendar & Contacts in 1 click, connect Gmail with App Password or OAuth, or enable 36+ APIs cleanly.")}
              </p>
              <div className="mt-2">{adjustLaterNotice}</div>
            </div>
            {conn ? (
              <>
                <div className="rounded-3xl border border-accent/35 bg-accent/6 p-3.5 space-y-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 text-[13px] font-semibold text-fg">
                      <Zap size={14} className="text-accent" />
                      {t("1-Click Quick Sync")}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {(
                        [
                          { id: "all", label: t("All") },
                          { id: "google", label: t("Google & Mail") },
                          { id: "calendar", label: t("Calendar & Contacts") },
                          { id: "apis", label: t("36+ APIs") },
                        ] as const
                      ).map((tab) => (
                        <button
                          key={tab.id}
                          type="button"
                          onClick={() => setConnectTab(tab.id)}
                          className={cx(
                            "rounded-full px-2.5 py-0.5 text-[11.5px] font-medium border",
                            connectTab === tab.id
                              ? "border-accent bg-accent text-accent-fg"
                              : "border-border/70 bg-surface text-muted hover:text-fg",
                          )}
                        >
                          {tab.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {!!androidApp()?.syncPhoneCalendarAndContacts && (
                      <button
                        type="button"
                        disabled={quickBusy !== null}
                        onClick={async () => {
                          setQuickBusy("phone");
                          try {
                            const res = await syncPhoneGoogleToServer();
                            toast(
                              t("Synced from phone: {cal} calendar events, {con} contacts", {
                                cal: res.events ?? 0,
                                con: res.contacts ?? 0,
                              }),
                            );
                            await loadConn();
                          } catch (err) {
                            toast((err as Error).message);
                          } finally {
                            setQuickBusy(null);
                          }
                        }}
                        className={primaryBtn}
                      >
                        {quickBusy === "phone" ? <Loader2 size={15} className="animate-spin" /> : <Zap size={15} />}{" "}
                        {t("Sync Google Calendar & Contacts from Phone (1 Tap)")}
                      </button>
                    )}
                    <button
                      type="button"
                      disabled={quickBusy !== null}
                      onClick={async () => {
                        setQuickBusy("free");
                        try {
                          await api.enableFreeApis();
                          toast(t("Enabled free Weather & Maps/Routing APIs (no key required)!"));
                          await loadConn();
                        } catch (err) {
                          toast((err as Error).message);
                        } finally {
                          setQuickBusy(null);
                        }
                      }}
                      className={secondaryBtn}
                    >
                      {quickBusy === "free" ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}{" "}
                      {t("Enable Free Weather & Maps (No Key)")}
                    </button>
                  </div>
                </div>
                {(connectTab === "all" || connectTab === "google") && <EmailCard data={conn} onChange={() => void loadConn()} compact />}
                {(connectTab === "all" || connectTab === "calendar") && <CalendarCard data={conn} onChange={() => void loadConn()} compact />}
                {(connectTab === "all" || connectTab === "calendar") && <ContactsCard data={conn} onChange={() => void loadConn()} compact />}
                {(connectTab === "all" || connectTab === "apis") && <ApiIntegrationsCard data={conn} onChange={() => void loadConn()} compact />}
              </>
            ) : connError ? (
              <div className="rounded-3xl border border-border/70 bg-surface p-4 text-center">
                <p className="text-[13.5px] text-muted">{connError}</p>
                <button type="button" onClick={() => void loadConn()} className="mt-3 rounded-full bg-surface-2 px-4 py-1.5 text-[13px] font-medium">
                  {t("Retry")}
                </button>
              </div>
            ) : (
              <div className="flex justify-center py-8 text-muted">
                <Loader2 className="animate-spin" size={20} />
              </div>
            )}
          </div>
        )}
      </div>

      {/* The safe-area strip is its own element: a plain `safe-bottom` on the footer would clobber
          the footer's own bottom padding (source order) and pin the buttons to the window edge. */}
      <div className="safe-bottom shrink-0">
        <footer className="flex gap-2 px-6 pb-7 pt-3 wide:px-10 wide:pb-8">
        {step === "appearance" && (
          <button type="button" onClick={() => setStep("welcome")} className={secondaryBtn}>
            {t("Back")}
          </button>
        )}
        {(step === "model" || step === "connect") && (
          <button type="button" onClick={() => setStep("list")} className={secondaryBtn}>
            {t("Back")}
          </button>
        )}
        {step === "list" && (
          <button type="button" onClick={() => setStep("appearance")} className={secondaryBtn}>
            {t("Look & AI")}
          </button>
        )}
        {step === "welcome" && (
          <button type="button" onClick={() => setStep("appearance")} className={cx(primaryBtn, "flex-1 py-3")}>
            {t("Get started")} <ArrowRight size={16} />
          </button>
        )}
        {step === "appearance" && (
          <button
            type="button"
            disabled={savingAppearance}
            onClick={() => void saveAppearanceIfDirty().then(() => setStep("list"))}
            className={cx(primaryBtn, "flex-1 py-3")}
          >
            {savingAppearance ? <Loader2 size={16} className="animate-spin" /> : null}
            {t("Next: Model & Accounts")} <ArrowRight size={16} />
          </button>
        )}
        {step === "list" && (
          <button type="button" disabled={!modelReady || starting} onClick={() => void start()} className={cx(primaryBtn, "flex-1 py-3")}>
            {starting ? <Loader2 size={16} className="animate-spin" /> : null} {modelReady ? t("Start") : t("Add a model to start")} <ArrowRight size={16} />
          </button>
        )}
        {step === "model" && (
          <button type="button" disabled={!modelReady} onClick={() => setStep("connect")} className={cx(primaryBtn, "flex-1 py-3")}>
            {modelReady ? t("Continue to Connect Apps") : t("Save a model to continue")} <ArrowRight size={16} />
          </button>
        )}
        {step === "connect" && (
          <button
            type="button"
            disabled={starting}
            onClick={() => (modelReady ? void start() : setStep("list"))}
            className={cx(primaryBtn, "flex-1 py-3")}
          >
            {starting ? <Loader2 size={16} className="animate-spin" /> : null}
            {modelReady ? t("Start") : connected ? t("Done") : t("Skip for now")} <ArrowRight size={16} />
          </button>
        )}
        </footer>
      </div>
    </div>
  );
}

function Point({ n, title, body }: { n: number; title: string; body: string }) {
  return (
    <li className="flex gap-3 rounded-3xl bg-surface border border-border/70 p-4">
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent/12 text-[13px] font-semibold text-accent">{n}</span>
      <div className="text-[13.5px] leading-relaxed">
        <div className="font-medium text-fg">{title}</div>
        <div className="text-muted">{body}</div>
      </div>
    </li>
  );
}

function Item({ done, locked, optional, title, body, onClick }: { done: boolean; locked?: boolean; optional?: boolean; title: string; body: ReactNode; onClick: () => void }) {
  const t = useT();
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        disabled={locked}
        aria-disabled={locked}
        className={cx("w-full flex items-center gap-3 rounded-3xl border px-4 py-3.5 text-left transition-colors", done ? "border-accent/30 bg-accent/5" : "border-border/70 bg-surface", locked && "opacity-50")}
      >
        <span className={cx("flex h-7 w-7 shrink-0 items-center justify-center rounded-full", done ? "bg-accent text-white" : locked ? "bg-surface-2 text-muted" : "border border-border text-muted")}>
          {done ? <Check size={15} strokeWidth={3} /> : locked ? <Lock size={13} /> : null}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-[15px] font-medium">
            <span className="truncate">{title}</span>
            {optional && <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[10.5px] font-medium text-muted">{t("optional")}</span>}
          </span>
          <span className="block truncate text-[12.5px] text-muted">{body}</span>
        </span>
        {!locked && <ChevronRight size={16} className="shrink-0 text-muted" />}
      </button>
    </li>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function presetOf(conn: ConnectionsData): string {
  const hit = Object.entries(conn.providers).find(([id, p]) => id !== "custom" && p.base_url && conn.llm.base_url.startsWith(p.base_url));
  return hit?.[0] ?? "custom";
}
