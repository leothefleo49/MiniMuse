import { CheckCircle2, ChevronDown, ChevronRight, Cloud, Cpu, Globe, KeyRound, Laptop, Loader2, RefreshCw, ShieldCheck, Smartphone } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { setAccent, setBubble } from "../accent";
import { api } from "../api";
import { BrandMark } from "../components/BrandMark";
import { CommunityNotice } from "../components/CommunityNotice";
import { PRIVACY_URL } from "../components/DataControls";
import { SignIn } from "../components/SignIn";
import { LOCALES, agentLanguageForLocale, setLocaleSetting, useLocaleSetting, useT, type LocaleSetting } from "../i18n";
import { useStore } from "../store";
import { setBg, setUiScale } from "../theme";
import type { NearbyPeer } from "../types";
import { cx } from "../util";

export const STARTUP_LANG_SEEN_KEY = "minimuse_startup_lang_seen";

/**
 * Opening door: Local setup is the primary hero choice (OpenRouter, own API keys, Ollama /
 * LM Studio, encrypted local vault, theme & avatar customization). nanoMuse Cloud and
 * Sync from Another Device sit neatly alongside so first-time users can either set up locally
 * or clone an existing device's setup in one tap.
 */
export function SignInGate() {
  const { state, refreshSettings, refreshHub, dismissOnboarding, setTab, toast, skipSignIn } = useStore();
  const [cloudOpen, setCloudOpen] = useState(false);
  const [pairOpen, setPairOpen] = useState(false);
  const [nearby, setNearby] = useState<NearbyPeer[]>([]);
  const [pairTarget, setPairTarget] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [bundleText, setBundleText] = useState("");
  const [pairing, setPairing] = useState(false);
  const localeSetting = useLocaleSetting();
  const t = useT();
  const name = state.profile?.name ?? "MiniMuse";

  useEffect(() => {
    try {
      sessionStorage.setItem(STARTUP_LANG_SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (!pairOpen) return;
    api
      .syncDiscover()
      .then((res) => setNearby(res.nearby ?? []))
      .catch(() => undefined);
  }, [pairOpen]);

  const changeLocale = (next: LocaleSetting) => {
    setLocaleSetting(next);
    try {
      sessionStorage.setItem(STARTUP_LANG_SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
    void api
      .updateSettings({ agent: { language: agentLanguageForLocale(next) } })
      .catch(() => undefined);
  };

  const applyIncomingUiPrefs = (prefs?: Record<string, string> | null) => {
    if (!prefs) return;
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
      if (bundleText.trim().startsWith("{")) {
        const parsed = JSON.parse(bundleText.trim()) as Record<string, unknown>;
        const res = await api.applySetupBundle(parsed);
        applyIncomingUiPrefs(res.ui_preferences);
        await api.onboarded(true).catch(() => undefined);
        await Promise.all([refreshSettings(), refreshHub()]);
        toast(t("Synced setup, profile, and keys from transfer bundle!"));
        skipSignIn();
        dismissOnboarding();
        setTab("chat");
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
      await api.onboarded(true).catch(() => undefined);
      await Promise.all([refreshSettings(), refreshHub()]);
      toast(t("Paired with {peer} and synced setup!", { peer: res.peer ?? "device" }));
      skipSignIn();
      dismissOnboarding();
      setTab("chat");
    } catch (e) {
      toast((e as Error).message || t("Could not pair with that device."));
    } finally {
      setPairing(false);
    }
  };

  const chooseLocal = () => {
    try {
      sessionStorage.setItem(STARTUP_LANG_SEEN_KEY, "1");
    } catch {
      /* ignore */
    }
    void api
      .updateSettings({ cloud_mode: "local", cloud_prompts: false })
      .catch(() => undefined)
      .finally(() => {
        skipSignIn();
        void refreshSettings();
        void refreshHub();
      });
  };

  const perks = (
    <ul className="grid grid-cols-1 gap-2 text-[12.5px] sm:grid-cols-2 wide:grid-cols-1 wide:gap-1.5 wide:text-[13.5px]">
      <Perk icon={<KeyRound size={15} />} text={t("OpenRouter, Anthropic, OpenAI, Gemini, or local Ollama/LM Studio")} />
      <Perk icon={<ShieldCheck size={15} />} text={t("Keys stored encrypted in your own vault; prompts stay off any relay")} />
      <Perk icon={<Cpu size={15} />} text={t("36+ built-in APIs plus custom AI API discovery & multi-account sync")} />
      <Perk icon={<Laptop size={15} />} text={t("Personalize theme, avatar, name, Calendar & Location tabs during setup")} />
    </ul>
  );

  return (
    <div className="relative mx-auto flex h-full w-full max-w-[760px] flex-col overflow-hidden bg-bg sm:border-x sm:border-border wide:max-w-none wide:border-0">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[42vh] bg-[radial-gradient(ellipse_at_top,rgba(0,100,212,0.12),transparent_65%)] dark:bg-[radial-gradient(ellipse_at_top,rgba(23,147,255,0.18),transparent_65%)] wide:inset-y-0 wide:h-auto wide:bg-[radial-gradient(ellipse_at_left,rgba(0,100,212,0.1),transparent_60%)]" />
      <div className="titlebar-room relative" />
      {/* safe-top on its own element: on the same element it would clobber the row's own
          padding-top (source order) and pin the bar to the window's top edge on desktop. */}
      <div className="safe-top relative">
        <div className="flex flex-col gap-2 px-5 pt-4 pb-2 wide:flex-row wide:items-center wide:justify-between wide:gap-4 wide:px-10">
        <div
          data-testid="startup-language-picker"
          className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-2xl border border-accent/35 bg-surface/95 px-3.5 py-2 text-[12px] text-fg shadow-xs"
        >
          <span className="inline-flex items-center gap-1.5 font-semibold text-fg">
            <Globe size={14} className="text-accent shrink-0" /> {t("Language")}:
          </span>
          <div className="flex flex-wrap gap-1.5">
            {LOCALES.map((loc) => {
              const selected = localeSetting === loc.value;
              return (
                <button
                  key={loc.value}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => changeLocale(loc.value)}
                  className={cx(
                    "rounded-full px-3 py-1 text-[12px] transition-colors",
                    selected
                      ? "bg-accent font-medium text-accent-fg"
                      : "bg-surface-2/80 text-muted hover:text-fg",
                  )}
                >
                  {loc.label}
                </button>
              );
            })}
          </div>
        </div>
        <span className="pl-1 text-[11.5px] text-muted wide:pl-0 wide:text-right">
          {t("Everything you choose during setup can be changed later in Settings.")}
        </span>
        </div>
      </div>
      <div className="relative flex-1 overflow-y-auto px-5 pb-8 wide:flex wide:items-center wide:px-10">
        <div className="mx-auto w-full wide:grid wide:max-w-[1040px] wide:grid-cols-[1.25fr_0.95fr] wide:items-start wide:gap-10">
          {/* Primary: Local Setup Hero */}
          <div className="safe-top">
          <div className="pt-4 wide:pt-2">
            <div className="flex flex-col items-center text-center wide:items-start wide:text-left">
              <BrandMark size={80} className="drop-shadow-[0_18px_36px_rgba(0,100,212,0.24)]" />
              <div className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-accent/10 px-3 py-1 text-[11.5px] font-semibold text-accent">
                <CheckCircle2 size={13} />
                {t("Recommended · Local & Private")}
              </div>
              <h1 className="mt-3 text-[28px] font-bold tracking-tight wide:text-[34px]">
                {t("Set up {name} locally", { name })}
              </h1>
              <p className="mt-2 max-w-lg text-[14.5px] leading-relaxed text-muted wide:text-[15.5px]">
                {t("Run with your own API key (OpenRouter, OpenAI, Anthropic, Gemini) or a local model on your machine. Connect multiple Google & Email accounts, customize your look, and keep every secret encrypted locally.")}
              </p>
            </div>

            <div className="mt-5 rounded-[28px] border-2 border-accent/35 bg-surface p-5 shadow-md">
              <div className="mb-4">{perks}</div>
              <button
                type="button"
                onClick={skipSignIn}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-accent px-5 py-3.5 text-[15px] font-semibold text-accent-fg shadow-sm transition hover:opacity-95"
                data-testid="sign-in-skip"
              >
                {t("Continue with Local Setup")}
                <ChevronRight size={17} />
              </button>
              <button
                type="button"
                onClick={chooseLocal}
                className="mt-2.5 w-full rounded-2xl border border-border/70 px-4 py-2.5 text-[13px] font-medium text-fg/80 transition hover:bg-surface-2"
                data-testid="sign-in-local-only"
              >
                {t("Run strictly local (keep nanoMuse Cloud turned off)")}
              </button>
            </div>

            <CommunityNotice compact className="mt-5 hidden wide:block" />
          </div>
          </div>

          {/* Secondary Side Options: Pair & Sync Another Device + nanoMuse Cloud */}
          <aside className="mt-6 space-y-3 wide:mt-16">
            {/* Pair & Sync from another device right during setup */}
            <div className="rounded-[24px] border border-border/70 bg-surface-2/40 p-4 wide:p-5">
              <button
                type="button"
                onClick={() => setPairOpen((v) => !v)}
                aria-expanded={pairOpen}
                className="flex w-full items-center justify-between gap-3 text-left"
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface text-accent shadow-xs">
                    <Smartphone size={17} />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[14px] font-semibold text-fg">{t("Sync from another device")}</div>
                    <div className="truncate text-[12px] text-muted">{t("Already set up on PC or phone? Pair to copy everything")}</div>
                  </div>
                </div>
                <ChevronDown size={16} className={cx("shrink-0 text-muted transition-transform", pairOpen && "rotate-180")} />
              </button>

              {pairOpen && (
                <div className="mt-3.5 space-y-2.5 border-t border-border/60 pt-3.5">
                  <p className="text-[12px] text-muted">
                    {t("Pair with your other MiniMuse device (Settings → Data controls → Pair device) or paste a Setup Transfer Bundle to copy your models, API keys, profile, AI avatar/animations, and chats.")}
                  </p>
                  {nearby.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {nearby.map((peer) => (
                        <button
                          key={peer.device_id}
                          type="button"
                          onClick={() => {
                            const u = peer.urls[0] ?? "";
                            setPairTarget(u);
                            void api
                              .syncPairRequest({ target: u, urls: peer.urls })
                              .then((res) => {
                                if (res.prompted) {
                                  toast(
                                    t("Code & QR popped up on {name} — enter the 6-character code or scan its QR.", {
                                      name: res.device_name || peer.name,
                                    }),
                                  );
                                }
                              })
                              .catch(() => undefined);
                          }}
                          className="inline-flex items-center gap-1 rounded-full border border-accent/40 bg-accent/10 px-2.5 py-1 text-[11.5px] font-medium text-accent"
                        >
                          <RefreshCw size={11} /> {peer.name} ({peer.urls[0]})
                        </button>
                      ))}
                    </div>
                  )}
                  <input
                    value={pairTarget}
                    onChange={(e) => setPairTarget(e.target.value)}
                    placeholder={t("Other device URL or pair link (e.g. http://192.168.1.25:8787)")}
                    className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-[12.5px] outline-none focus:border-accent"
                  />
                  <input
                    value={pairCode}
                    onChange={(e) => setPairCode(e.target.value)}
                    placeholder={t("6-character pairing code (e.g. A7K-9M2)")}
                    className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-[12.5px] font-mono outline-none focus:border-accent"
                  />
                  <textarea
                    value={bundleText}
                    onChange={(e) => setBundleText(e.target.value)}
                    rows={2}
                    placeholder={t("Or paste a Setup & Profile Transfer JSON bundle here")}
                    className="w-full rounded-xl border border-border bg-surface px-3 py-2 text-[12px] font-mono outline-none focus:border-accent resize-none"
                  />
                  <button
                    type="button"
                    disabled={pairing}
                    onClick={() => void handlePairAndSyncSetup()}
                    className="flex w-full items-center justify-center gap-1.5 rounded-xl bg-accent px-4 py-2.5 text-[13px] font-semibold text-accent-fg disabled:opacity-50"
                  >
                    {pairing ? <Loader2 size={14} className="animate-spin" /> : null}
                    {t("Pair & Copy Setup Now")}
                  </button>
                </div>
              )}
            </div>

            <div className="rounded-[24px] border border-border/70 bg-surface-2/40 p-4 wide:p-5">
              <button
                type="button"
                onClick={() => setCloudOpen((v) => !v)}
                aria-expanded={cloudOpen}
                className="flex w-full items-center justify-between gap-3 text-left"
              >
                <div className="flex items-center gap-2.5 min-w-0">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-surface text-accent shadow-xs">
                    <Cloud size={17} />
                  </span>
                  <div className="min-w-0">
                    <div className="text-[14px] font-semibold text-fg">{t("nanoMuse Cloud (optional)")}</div>
                    <div className="truncate text-[12px] text-muted">{t("Cross-device relay & starter model allowance")}</div>
                  </div>
                </div>
                <ChevronDown size={16} className={cx("shrink-0 text-muted transition-transform", cloudOpen && "rotate-180")} />
              </button>

              {cloudOpen && (
                <div className="mt-4 border-t border-border/60 pt-4">
                  <SignIn
                    onSignedIn={async () => {
                      toast(t("Signed in to nanoMuse Cloud."));
                      await Promise.all([refreshSettings(), refreshHub()]);
                    }}
                  />
                  <p className="mt-3 text-[11px] leading-relaxed text-muted">
                    {t("The relay stores only your account id, masked contact, usage counters, and appearance preferences; see the")}{" "}
                    <a href={PRIVACY_URL} target="_blank" rel="noopener noreferrer" className="underline decoration-dotted underline-offset-2 hover:text-fg">
                      {t("privacy policy")}
                    </a>
                    .
                  </p>
                </div>
              )}
            </div>
            <p className="mt-2.5 px-1 text-center text-[11.5px] leading-relaxed text-muted wide:text-left">
              {t("You can enable, switch, or turn off nanoMuse Cloud anytime in Settings.")}
            </p>
            <CommunityNotice compact className="mt-5 wide:hidden" />
          </aside>
        </div>
      </div>
    </div>
  );
}

function Perk({ icon, text }: { icon: ReactNode; text: string }) {
  return (
    <li className="flex items-start gap-2 rounded-2xl bg-surface-2/50 px-3 py-2 text-fg/85 wide:bg-transparent wide:px-0 wide:py-1">
      <span className="mt-0.5 shrink-0 text-accent">{icon}</span>
      <span className="leading-snug">{text}</span>
    </li>
  );
}
