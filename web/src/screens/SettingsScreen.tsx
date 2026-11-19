import {
  BarChart3,
  BatteryFull,
  Bell,
  Bot,
  Brain,
  Check,
  ChevronRight,
  Clapperboard,
  Cloud,
  Code2,
  Copy,
  DatabaseZap,
  Download,
  Fingerprint,
  Hand,
  Info,
  KeyRound,
  Loader2,
  LockKeyhole,
  LogOut,
  MessageSquare,
  MessageSquareWarning,
  Monitor,
  Moon,
  Palette,
  Plug,
  Plus,
  Puzzle,
  QrCode,
  RefreshCw,
  Search,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Smile,
  Sparkles,
  TerminalSquare,
  Trash2,
  Upload,
  Watch,
  X,
  Zap,
} from "lucide-react";
import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { androidApp, authenticateNativeBiometric, keepRunningStatus, nativeBiometricInfo, type AppUpdateView, type KeepRunningStatus } from "../android";
import { WatchPairingCard } from "./DevicesScreen";
import { UsageScreen } from "./UsageScreen";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { DefaultAssistantCard } from "../components/DefaultAssistantCard";
import { DevicePermissions } from "../components/DevicePermissions";
import { ActiveModelList } from "../components/ActiveModelList";
import { PhoneControlGuide } from "../components/PhoneControlGuide";
import { SwitchModeConfirm } from "../components/SwitchModeConfirm";
import { EmergencyStop, RelatedSafety } from "../components/SafetyControls";
import { api, setToken } from "../api";
import { DRAGON } from "../avatars";
import { desktopBridge, desktopUpdates, isDesktopApp, setDesktopFullscreen, useDesktopFullscreen } from "../desktop";
import { thisDeviceKind } from "../device";
import { countDevices } from "../devicePeers";
import { setDeveloperTools, useDeveloperTools } from "../devtools";
import { setShowSteps, useShowSteps } from "../steps";
import { clearMarks, markMatches, queryWords, searchSettingsCatalog, splitAround, type SettingsCatalogEntry } from "../settings-search";
import { openOwnKeySetup } from "../components/AllowanceWays";
import { dismissLockPrompt, lockPromptDismissed as readLockPromptDismissed } from "../components/AppLockPrompt";
import { AVATAR_COLORS } from "../components/AvatarPicker";
import { IdentityForm, identityBody, identityOf, type Identity } from "../components/IdentityForm";
import { PageBar } from "../components/BackBar";
import { CommunityNotice } from "../components/CommunityNotice";
import { DataControls, PRIVACY_URL } from "../components/DataControls";
import { ImportExport } from "../components/ImportExport";
import { getLocale, LOCALES, setLocaleSetting, useLocaleSetting, useT } from "../i18n";
import { has, regionOf, unavailableLine, useProviders } from "../providers";
import { isMainland, ownKeyWay } from "../region";
import { BG_PRESETS, bgNow, setBg, setPwaFullscreen, setUiScale, uiScaleNow, useBg, usePwaFullscreen, useTheme, useUiScale } from "../theme";
import { ACCENTS, BUBBLE_PRESETS, accentNow, bubbleNow, setAccent, setBubble, useAccent, useBubble } from "../accent";
import { pushUi } from "../ui-sync";
import { disablePush, enablePush, pushState, type PushState } from "../push";
import { useStore } from "../store";
import type { LockView, PasswordEntry, Proactivity, PushInfo, UpdateView } from "../types";
import { MuseCaption, MuseCard, MuseDivider, MuseRow } from "../components/MuseList";
import { useWide } from "../useWide";
import { cx, relativeTime } from "../util";
import { Toggle } from "../components/Form";
import { waitForUpdatedServerAndReload } from "../update-reload";

const settingsInput = "w-full rounded-2xl bg-surface-2 px-3.5 py-2.5 outline-none focus:ring-2 focus:ring-accent/40";

const MODES: Array<{ id: "ask" | "task" | "strict" | "auto"; title: string; text: string; icon: ReactNode }> = [
  {
    id: "task",
    title: "Locked in",
    text: "Everything runs freely except what could pop up, redirect, stop a program or reach another person: those ask once, and a “For this task” answer carries the rest of the task.",
    icon: <ShieldCheck size={18} />,
  },
  {
    id: "ask",
    title: "Balanced",
    text: "Browse, read and write files freely; stop for anything hard to undo: email, purchases, shell commands.",
    icon: <ShieldCheck size={18} />,
  },
  {
    id: "strict",
    title: "Cautious",
    text: "Also ask before moderate actions like fetching web pages or writing files.",
    icon: <Shield size={18} />,
  },
  {
    id: "auto",
    title: "Hands-off",
    text: "Approve everything automatically except explicit deny rules. For trusted, unattended runs only.",
    icon: <ShieldAlert size={18} />,
  },
];

export function SettingsScreen() {
  const { state, refreshSettings, setTab, toast, reopenSetup } = useStore();
  const s = state.settings;
  const [identity, setIdentity] = useState<Identity>(() => identityOf(state.profile, DRAGON, AVATAR_COLORS[0]));
  const [saving, setSaving] = useState(false);
  const [confirmAutoSentinel, setConfirmAutoSentinel] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const t = useT();
  const localeSetting = useLocaleSetting();
  const theme = useTheme();
  const accent = useAccent();
  const bubble = useBubble();
  const bgSetting = useBg();
  // the swatch that shows selected: the pinned background, or the theme's own (White in
  // the light look, Dark in the dark one) when nothing is pinned
  const effBg = bgSetting || (theme === "dark" ? "#181819" : "");
  const [release, setRelease] = useState<UpdateView | null>(null);
  const [checking, setChecking] = useState(false);
  // the selects and the labels beside them: one id per pair, so the label names the control
  const ids = useId();
  const intervalId = `${ids}-interval`;
  const appLangId = `${ids}-app-language`;
  const replyLangId = `${ids}-reply-language`;
  const bgModelId = `${ids}-background-model`;

  useEffect(() => {
    if (!s) void refreshSettings();
  }, [s, refreshSettings]);

  // the runtime asks nanomuse.cn/dl (then GitHub) at most once a day; "Check now" asks again
  const checkRelease = useCallback(async (refresh = false) => {
    setChecking(true);
    try {
      setRelease(await api.update(refresh));
    } catch {
      setRelease(null);
    } finally {
      setChecking(false);
    }
  }, []);
  useEffect(() => {
    void checkRelease();
  }, [checkRelease, state.version]);

  useEffect(() => {
    if (state.profile) setIdentity(identityOf(state.profile, "", AVATAR_COLORS[0]));
  }, [state.profile]);

  const update = async (body: Record<string, unknown>, msg?: string) => {
    setSaving(true);
    try {
      await api.updateSettings(body);
      await refreshSettings();
      if (msg) toast(msg);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const saved = identityOf(state.profile, "", AVATAR_COLORS[0]);
  const dirty = !!state.profile && (Object.keys(identity) as (keyof Identity)[]).some((k) => identity[k] !== saved[k]);
  const name = state.profile?.name ?? "MiniMuse";
  const developer = useDeveloperTools();
  const steps = useShowSteps();
  const wide = useWide() && !state.lite;
  const [page, setPage] = useState<SectionId | null>(null);
  const [query, setQuery] = useState("");
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const navRef = useRef<HTMLElement | null>(null);

  const sections: Array<{ id: SectionId; title: string; group: string; icon: ReactNode }> = useMemo(
    () => [
      { id: "who", title: name, group: t("General & Usage"), icon: <Sparkles size={15} /> },
      { id: "usage", title: t("Usage"), group: t("General & Usage"), icon: <BarChart3 size={15} /> },
      { id: "appearance", title: t("Appearance"), group: t("General & Usage"), icon: <Palette size={15} /> },
      { id: "proactivity", title: t("Proactivity"), group: t("General & Usage"), icon: <Zap size={15} /> },
      { id: "behavior", title: t("Agent behavior"), group: t("General & Usage"), icon: <Bot size={15} /> },

      { id: "model", title: t("Model"), group: t("Models & Devices"), icon: <TerminalSquare size={15} /> },
      { id: "cloud", title: t("MiniMuse Cloud"), group: t("Models & Devices"), icon: <Cloud size={15} /> },
      { id: "notifications", title: t("Notifications"), group: t("Models & Devices"), icon: <Bell size={15} /> },
      { id: "watch", title: t("Smartwatch (Wear OS)"), group: t("Models & Devices"), icon: <Watch size={15} /> },
      { id: "assistant", title: t("Default assistant"), group: t("Models & Devices"), icon: <Sparkles size={15} /> },
      ...(keepRunningStatus() ? [{ id: "keep-running" as const, title: t("Keep it running"), group: t("Models & Devices"), icon: <BatteryFull size={15} /> }] : []),
      ...(isDesktopApp() ? [{ id: "desktop" as const, title: t("Desktop app"), group: t("Models & Devices"), icon: <Monitor size={15} /> }] : []),

      { id: "sentinel", title: t("Safety · Sentinel"), group: t("Security & Access"), icon: <ShieldCheck size={15} /> },
      { id: "app-lock", title: t("App lock"), group: t("Security & Access"), icon: <LockKeyhole size={15} /> },
      { id: "passwords", title: t("Passwords & logins"), group: t("Security & Access"), icon: <KeyRound size={15} /> },
      { id: "permissions", title: t("Permissions"), group: t("Security & Access"), icon: <Shield size={15} /> },

      { id: "data", title: t("Data controls"), group: t("Data & System"), icon: <DatabaseZap size={15} /> },
      { id: "import", title: t("Import & export"), group: t("Data & System"), icon: <Upload size={15} /> },
      { id: "developer", title: t("Developer"), group: t("Data & System"), icon: <Code2 size={15} /> },
      { id: "about", title: t("About"), group: t("Data & System"), icon: <Info size={15} /> },
    ],
    [name, t],
  );

  const catalog: SettingsCatalogEntry<SectionId>[] = useMemo(
    () => [
      { id: "who-identity", sectionId: "who", sectionTitle: name, label: t("Name & personality"), description: t("Customize assistant name, avatar, color swatch, and bio."), keywords: ["profile", "avatar", "identity"] },
      { id: "usage-overview", sectionId: "usage", sectionTitle: t("Usage"), label: t("Token & cost usage"), description: t("Turns, cloud cost estimate, cached tokens, Ollama VRAM, and spending caps."), keywords: ["tokens", "cost", "limits", "budget", "ollama", "vram", "spending", "activity"] },
      { id: "sentinel-mode", sectionId: "sentinel", sectionTitle: t("Safety · Sentinel"), label: t("Approval mode & sandbox"), description: t("Locked in, Balanced, Cautious, Hands-off, and sandbox isolation rules."), keywords: ["safety", "sentinel", "sandbox", "hands-off", "auto", "approvals"] },
      { id: "app-lock-main", sectionId: "app-lock", sectionTitle: t("App lock"), label: t("Biometrics, passkey & PIN lock"), description: t("Lock the app with Fingerprint, Face ID, Passkey, Password/PIN, and auto-lock timeout."), keywords: ["pin", "password", "fingerprint", "face id", "passkey", "timeout", "lock"] },
      { id: "passwords-main", sectionId: "passwords", sectionTitle: t("Passwords & logins"), label: t("Saved logins & 2FA vault"), description: t("Import CSV/JSON from Chrome, Bitwarden, 1Password, Apple Passwords, or add logins and TOTP codes."), keywords: ["vault", "2fa", "totp", "csv", "import", "export", "passwords"] },
      { id: "proactivity-dial", sectionId: "proactivity", sectionTitle: t("Proactivity"), label: t("Proactivity level & check-in interval"), description: t("Control how often the agent works on goals and speaks up on schedule."), keywords: ["schedule", "interval", "goals"] },
      { id: "proactivity-quiet", sectionId: "proactivity", sectionTitle: t("Proactivity"), label: t("Quiet hours"), description: t("Pause background check-ins and reminders during your sleeping window."), keywords: ["sleep", "night", "dnd"] },
      { id: "behavior-steps", sectionId: "behavior", sectionTitle: t("Agent behavior"), label: t("Show the agent's steps"), description: t("Show tool call chips in chat as the agent works."), keywords: ["tools", "chips", "steps"] },
      { id: "behavior-thinking", sectionId: "behavior", sectionTitle: t("Agent behavior"), label: t("Show thinking"), description: t("Reveal model reasoning blocks when supported by the provider."), keywords: ["reasoning", "cot"] },
      { id: "behavior-self-improve", sectionId: "behavior", sectionTitle: t("Agent behavior"), label: t("Self-improving integrations"), description: t("Allow the agent to add connectors and APIs automatically when needed."), keywords: ["integrations", "mcp", "api"] },
      { id: "behavior-lang", sectionId: "behavior", sectionTitle: t("Agent behavior"), label: t("Reply language"), description: t("Choose the language the assistant writes replies in."), keywords: ["language", "english", "chinese"] },
      { id: "behavior-bg-model", sectionId: "behavior", sectionTitle: t("Agent behavior"), label: t("Background model"), description: t("Pick which model runs scheduled routines, reminders, and background goals."), keywords: ["flash", "pro", "local", "gemini"] },
      { id: "permissions-main", sectionId: "permissions", sectionTitle: t("Permissions"), label: t("Device permissions"), description: t("What this device's system lets MiniMuse use: notifications, updates, location, microphone, camera, calendar, contacts and more."), keywords: ["camera", "microphone", "location", "install", "notifications", "calendar", "contacts", "nearby", "accessibility", "battery", "allow"] },
      { id: "sentinel-rules", sectionId: "sentinel", sectionTitle: t("Safety · Sentinel"), label: t("What it may do without asking"), description: t("Always, ask first or never, per kind of action: commands, email, browser, files, passwords, location and more."), keywords: ["always", "ask", "never", "approve", "permission", "email", "shell", "commands", "browser", "files", "passwords", "location", "install"] },
      { id: "sentinel-more", sectionId: "sentinel", sectionTitle: t("Safety · Sentinel"), label: t("Emergency stop, quiet hours and limits"), description: t("Stop everything now, jump to quiet hours, or set spending and usage limits."), keywords: ["emergency", "stop", "panic", "quiet", "limit", "spending", "budget"] },
      { id: "assistant-main", sectionId: "assistant", sectionTitle: t("Default assistant"), label: t("Make MiniMuse your default assistant"), description: t("Choose MiniMuse as the assistant a phone or watch opens from its home, power or side button or assist gesture."), keywords: ["assistant", "default", "digital assistant", "home button", "power button", "side button", "gesture", "voice", "gemini", "google assistant"] },
      { id: "watch-main", sectionId: "watch", sectionTitle: t("Smartwatch (Wear OS)"), label: t("Watch APK install & wireless ADB pairing"), description: t("Discover Wear OS watches over Wi-Fi, install MiniMuse-wear.apk, and generate 6-digit pairing codes."), keywords: ["wear", "watch", "apk", "adb", "pair", "sideload"] },
      { id: "notifications-main", sectionId: "notifications", sectionTitle: t("Notifications"), label: t("Push & background notifications"), description: t("Enable alerts for approvals, questions, and finished background tasks."), keywords: ["push", "alerts", "test notification"] },
      ...(keepRunningStatus() ? [{ id: "keep-running-main", sectionId: "keep-running" as const, sectionTitle: t("Keep it running"), label: t("Battery optimization, overlay & boot start"), description: t("Keep Android background services alive, configure exact alarms, and export crash logs."), keywords: ["battery", "alarms", "boot", "logs"] }] : []),
      { id: "cloud-main", sectionId: "cloud", sectionTitle: t("MiniMuse Cloud"), label: t("Local / Hybrid / Cloud routing"), description: t("Switch between strictly local mode, custom slot routing, or MiniMuse Cloud."), keywords: ["relay", "local", "hybrid", "sync"] },
      { id: "model-main", sectionId: "model", sectionTitle: t("Model"), label: t("Provider, active model & API keys"), description: t("Inspect your active LLM, configure OpenRouter/DeepSeek/Gemini/Ollama, and manage keys."), keywords: ["llm", "api key", "provider", "ollama", "openrouter"] },
      { id: "appearance-bg", sectionId: "appearance", sectionTitle: t("Appearance"), label: t("Background & theme"), description: t("Choose white, dark, or custom canvas colors."), keywords: ["theme", "dark mode", "white", "background"] },
      { id: "appearance-scale", sectionId: "appearance", sectionTitle: t("Appearance"), label: t("UI scale"), description: t("Resize interface text, icons, and spacing cleanly on this device."), keywords: ["zoom", "font size", "scale"] },
      { id: "appearance-accent", sectionId: "appearance", sectionTitle: t("Appearance"), label: t("Accent & message bubble color"), description: t("Customize buttons, highlights, and user message bubble colors."), keywords: ["color", "accent", "bubbles"] },
      { id: "appearance-lang", sectionId: "appearance", sectionTitle: t("Appearance"), label: t("App language"), description: t("Change UI language on this device."), keywords: ["locale", "i18n", "translation"] },
      { id: "appearance-tabs", sectionId: "appearance", sectionTitle: t("Appearance"), label: t("Navigation tabs & Maps"), description: t("Show or hide Calendar and Location tabs and pick your default map app."), keywords: ["calendar", "location", "maps", "google maps", "waze"] },
      ...(isDesktopApp() ? [{ id: "desktop-main", sectionId: "desktop" as const, sectionTitle: t("Desktop app"), label: t("Startup login item & global shortcuts"), description: t("Start at computer login, inspect tray behavior, and view keyboard shortcuts."), keywords: ["tray", "shortcuts", "electron", "startup"] }] : []),
      { id: "data-main", sectionId: "data", sectionTitle: t("Data controls"), label: t("Device sync, backup & cloud data controls"), description: t("Pair devices over P2P sync, download full agent zip backup, or manage cloud training settings."), keywords: ["sync", "backup", "privacy", "pair"] },
      { id: "import-main", sectionId: "import", sectionTitle: t("Import & export"), label: t("Import from Muse & export archive"), description: t("Copy the single versatile Muse migration prompt and upload or download .zip archives."), keywords: ["muse", "transcripts", "migrate", "zip", "prompt", "export"] },
      { id: "developer-main", sectionId: "developer", sectionTitle: t("Developer"), label: t("Developer tools, PWA fullscreen & diagnostics"), description: t("Toggle coding agent tools, edge-to-edge PWA display, cache reset, and runtime health."), keywords: ["pwa", "caches", "service worker", "coding", "health", "ping"] },
      { id: "about-main", sectionId: "about", sectionTitle: t("About"), label: t("Version, updates & sign out"), description: t("Check latest MiniMuse release, inspect data/workspace directories, or disconnect this device."), keywords: ["version", "update", "workspace", "disconnect", "logout"] },
    ],
    [name, t],
  );

  const activeWidePage: SectionId = page ?? "who";
  const show = (id: SectionId) => (wide ? activeWidePage === id : page === id);
  const searching = queryWords(query).length > 0;
  const catalogHits = useMemo(() => searchSettingsCatalog(catalog, query), [catalog, query]);

  const openSection = useCallback((secId: SectionId, settingId?: string) => {
    setPage(secId);
    setHighlightedId(settingId ?? null);
    requestAnimationFrame(() => {
      if (settingId) {
        const el = bodyRef.current?.querySelector(`[data-setting-id="${settingId}"]`) as HTMLElement | null;
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
          return;
        }
      }
      bodyRef.current?.scrollTo({ top: 0 });
    });
  }, []);

  useEffect(() => {
    if (!highlightedId) return;
    const timer = window.setTimeout(() => setHighlightedId(null), 5000);
    return () => window.clearTimeout(timer);
  }, [highlightedId]);

  useEffect(() => {
    const root = bodyRef.current;
    if (root === null) return;
    markMatches(root, query);
    return () => {
      clearMarks(root);
    };
  }, [query, s, wide, page]);

  const onSearchKey = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      if (query.trim() === "") return;
      event.preventDefault();
      setQuery("");
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    const first = catalogHits[0];
    if (first) openSection(first.entry.sectionId, first.entry.id);
  };

  // `#developer`, `#usage`, `#who`, etc. open that exact tab on both desktop and mobile.
  const [wanted, setWanted] = useState<SectionId | null>(() => {
    const id = (window.location?.hash ?? "").slice(1);
    return SECTION_IDS.includes(id as SectionId) ? (id as SectionId) : null;
  });
  useEffect(() => {
    const onHash = () => {
      const id = (window.location?.hash ?? "").slice(1);
      if (SECTION_IDS.includes(id as SectionId)) setWanted(id as SectionId);
    };
    const onSection = (e: Event) => {
      const id = (e as CustomEvent<string>).detail;
      if (SECTION_IDS.includes(id as SectionId)) setWanted(id as SectionId);
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("nanomuse:settings-section", onSection);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("nanomuse:settings-section", onSection);
    };
  }, []);
  useEffect(() => {
    if (!wanted || !s) return;
    const target = wanted;
    setWanted(null);
    if (typeof history !== "undefined" && typeof history.replaceState === "function") {
      history.replaceState(null, "", (window.location?.pathname ?? "") + (window.location?.search ?? ""));
    }
    openSection(target);
  }, [wanted, s, openSection]);

  const groupedSections = useMemo(() => {
    const groups: Array<{ label: string; items: typeof sections }> = [];
    for (const sec of sections) {
      let grp = groups.find((g) => g.label === sec.group);
      if (!grp) {
        grp = { label: sec.group, items: [] };
        groups.push(grp);
      }
      grp.items.push(sec);
    }
    return groups;
  }, [sections]);

  if (!wide && page === null) {
    return (
      <SettingsHome
        release={release}
        onOpen={(id, settingId) => openSection(id, settingId)}
        catalog={catalog}
        query={query}
        onQuery={setQuery}
      />
    );
  }
  const current = sections.find((x) => x.id === (wide ? activeWidePage : page));

  return (
    <div className="flex h-full flex-col">
      {wide ? (
        <PageBar title={t("Settings")} description={t("Choose a category on the left or search to jump straight to any setting.")} />
      ) : (
        <PageBar title={current?.title ?? t("Settings")} onBack={() => setPage(null)} backLabel={t("Settings")} />
      )}

      <div className="flex min-h-0 flex-1">
        {wide && (
          <nav ref={navRef} className="flex max-h-full w-[240px] shrink-0 flex-col gap-1 self-start overflow-y-auto pl-4 pr-3 pt-1 pb-6" aria-label={t("Settings")}>
            <div className="pb-1.5">
              <div className="flex items-center gap-2 rounded-xl border border-border/60 bg-surface-2 px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-accent/35">
                <Search size={14} className="shrink-0 text-muted" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.currentTarget.value)}
                  onKeyDown={onSearchKey}
                  placeholder={t("Search settings")}
                  aria-label={t("Search settings")}
                  className="min-w-0 flex-1 bg-transparent text-[13px] outline-none placeholder:text-muted"
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => setQuery("")}
                    aria-label={t("Clear search")}
                    className="rounded-full p-0.5 text-muted hover:bg-surface hover:text-fg"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            </div>
            {searching && (
              <div className="mb-2 rounded-2xl border border-border/70 bg-surface p-2 space-y-1">
                <div className="px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {catalogHits.length === 0
                    ? t("No results found")
                    : catalogHits.length === 1
                      ? t("1 result")
                      : t("{n} results", { n: catalogHits.length })}
                </div>
                {catalogHits.length === 0 ? (
                  <div className="px-1.5 pb-1 text-[12px] text-muted">
                    {t("Try a term like usage, theme, lock, watch, or export.")}
                  </div>
                ) : (
                  catalogHits.map((hit) => (
                    <button
                      key={`${hit.entry.sectionId}-${hit.entry.id}`}
                      type="button"
                      onClick={() => openSection(hit.entry.sectionId, hit.entry.id)}
                      className="flex w-full flex-col rounded-xl px-2 py-1.5 text-left transition hover:bg-surface-2"
                    >
                      <span className="truncate text-[12.5px] font-medium text-fg">
                        <HighlightedText text={hit.entry.label} query={query} />
                      </span>
                      <span className="truncate text-[11px] text-accent">{hit.entry.sectionTitle}</span>
                    </button>
                  ))
                )}
              </div>
            )}
            {groupedSections.map((group) => (
              <div key={group.label} className="space-y-0.5 pt-2 first:pt-0">
                <div className="px-2.5 pb-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-muted/85">
                  {group.label}
                </div>
                {group.items.map((sec) => {
                  const active = activeWidePage === sec.id;
                  return (
                    <button
                      key={sec.id}
                      type="button"
                      onClick={() => openSection(sec.id)}
                      aria-current={active ? "page" : undefined}
                      className={cx(
                        "flex w-full items-center gap-2 rounded-xl px-2.5 py-1.5 text-left text-[13px] transition",
                        active
                          ? "bg-accent/12 font-semibold text-accent"
                          : "font-medium text-fg/75 hover:bg-surface-2 hover:text-fg",
                      )}
                    >
                      <span className={cx("shrink-0", active ? "text-accent" : "text-muted")}>{sec.icon}</span>
                      <span className="truncate">{sec.title}</span>
                    </button>
                  );
                })}
              </div>
            ))}
          </nav>
        )}
      <div ref={bodyRef} className="flex-1 overflow-y-auto px-4 pb-8 wide:px-6 wide:pb-10">
        {/* A desktop window used to centre a 740px column, which left a 16:9 monitor mostly
            empty. The sections now use the width: one column on a phone or a narrow window,
            two on a wide one, three on an ultrawide. */}
        <div
          className={cx(
            wide &&
              "mx-auto w-full max-w-none 2xl:grid 2xl:grid-cols-2 2xl:items-start 2xl:gap-5 min-[1900px]:grid-cols-3",
          )}
        >
        {/* who it is */}
        {show("who") && (
        <Section title={name} id="who" plain={!wide}>
          <SettingBlock id="who-identity" highlightedId={highlightedId}>
            <IdentityForm
              value={identity}
              onChange={setIdentity}
              suggestions={false}
              inputCls={settingsInput}
              onStudio={() => setTab("avatar")}
            />
            <button
              type="button"
              disabled={!dirty || saving}
              onClick={() => void update({ profile: identityBody(identity) }, t("Saved"))}
              className="mt-3 w-full rounded-2xl bg-accent text-accent-fg py-2.5 font-medium disabled:opacity-40"
            >
              {t("Save")}
            </button>
          </SettingBlock>
        </Section>
        )}

        {/* Usage: restored inside Settings as its own clean tab */}
        {show("usage") && (
        <Section title={t("Usage")} id="usage" plain={!wide}>
          <SettingBlock id="usage-overview" highlightedId={highlightedId}>
            <UsageScreen embedded />
          </SettingBlock>
        </Section>
        )}

        {/* Sentinel */}
        {show("sentinel") && (
        <Section title={t("Safety · Sentinel")} id="sentinel" plain={!wide}>
          <SettingBlock id="sentinel-mode" highlightedId={highlightedId}>
            <div className="flex items-center gap-2">
              <span className="text-[14px] font-medium">{t("Approval mode")}</span>
              <SettingInfo
                forceOpen={highlightedId === "sentinel-mode"}
                text={t("A separate gatekeeper reviews every action before it runs. Pick how often it should check in with you.")}
              />
            </div>
            <div className="space-y-2 pt-2">
              {MODES.map((m) => {
                const selected = s?.sentinel.mode === m.id;
                return (
                  <div
                    key={m.id}
                    className={cx(
                      "rounded-2xl border px-3.5 py-2.5 transition",
                      selected ? "border-accent bg-accent/8" : "border-border",
                    )}
                  >
                    <div className="flex items-center gap-3">
                      <button
                        type="button"
                        onClick={() => {
                          if (m.id === "auto" && s?.sentinel.mode !== "auto") {
                            setConfirmAutoSentinel(true);
                            return;
                          }
                          void update({ sentinel_mode: m.id });
                        }}
                        aria-pressed={selected}
                        className="flex flex-1 items-center gap-3 text-left"
                      >
                        <div className={cx(m.id === "auto" ? "text-rose-500" : "text-accent")}>{m.icon}</div>
                        <div className="flex-1 font-medium text-[14px]">{t(m.title)}</div>
                        {selected && <Check size={17} className="text-accent" />}
                      </button>
                      <SettingInfo forceOpen={highlightedId === "sentinel-mode" && selected} text={t(m.text)} />
                    </div>
                  </div>
                );
              })}
            </div>
            {s && (
              <div className="pt-2">
                <SettingInfo
                  forceOpen={highlightedId === "sentinel-mode"}
                  pillLabel={t("Gatekeeper & sandbox details")}
                  text={[
                    s
                      ? `${t("Always asks before: {tools}.", { tools: s.sentinel.always_ask_tools.map((tool) => t(tool)).join(", ") || t("none") })} ${s.sentinel.taint_tracking ? t("After reading private data, new network destinations need approval.") : ""}`.trim()
                      : "",
                    s?.sandbox
                      ? `${s.sandbox.active ? t("Commands run in a sandbox") : t("Commands run without a sandbox")} (${s.sandbox.status}): ${s.sandbox.active ? t("Each shell or Python call gets its own namespace: only the workspace is writable, your home directory is not there, and there is no network unless the command needs it.") : s.sandbox.status.includes("container") ? t("Each shell or Python call runs in the workspace with a scrubbed environment; the container is the boundary.") : t("Each shell or Python call runs in the workspace with a scrubbed environment. On Linux, installing bubblewrap gives each one its own namespace.")}`
                      : "",
                  ]
                    .filter(Boolean)
                    .join("\n\n")}
                />
              </div>
            )}
          </SettingBlock>
          <SettingBlock id="sentinel-rules" highlightedId={highlightedId}>
            <div className="mb-2 flex items-center gap-2">
              <span className="text-[14px] font-medium">{t("What it may do without asking")}</span>
              <SettingInfo
                forceOpen={highlightedId === "sentinel-rules"}
                text={t("Per kind of action: Always lets it go ahead, Ask first checks with you each time, Never blocks it.")}
              />
            </div>
            <ApprovalRules forceOpen={highlightedId === "sentinel-rules"} />
          </SettingBlock>
          <SettingBlock id="sentinel-more" highlightedId={highlightedId} className="space-y-3">
            <RelatedSafety
              onQuietHours={() => openSection("proactivity", "proactivity-quiet")}
              onLimits={() => openSection("usage")}
            />
            <EmergencyStop />
          </SettingBlock>
        </Section>
        )}

        {/* App lock: a PIN over the page, and an optional fingerprint or face */}
        {show("app-lock") && (
        <Section title={t("App lock")} id="app-lock" plain={!wide}>
          <SettingBlock id="app-lock-main" highlightedId={highlightedId}>
            <AppLockSection forceOpen={highlightedId === "app-lock-main"} />
          </SettingBlock>
        </Section>
        )}

        {/* Passwords & logins: import/export CSV/JSON, sync with password manager, encrypted vault */}
        {show("passwords") && (
        <Section title={t("Passwords & logins")} id="passwords" plain={!wide}>
          <SettingBlock id="passwords-main" highlightedId={highlightedId}>
            <PasswordsSection forceOpen={highlightedId === "passwords-main"} />
          </SettingBlock>
        </Section>
        )}

        {/* Proactivity */}
        {show("proactivity") && (
        <Section title={t("Proactivity")} id="proactivity" plain={!wide}>
          <SettingBlock id="proactivity-dial" highlightedId={highlightedId} className="space-y-3">
            <ProactivityDial value={state.profile?.proactivity ?? "default"} onChange={(v) => void update({ profile: { proactivity: v } })} forceOpen={highlightedId === "proactivity-dial"} />
            <div className="flex items-center gap-3">
              <div className="flex flex-1 items-center gap-1.5">
                <label htmlFor={intervalId} className="text-[13.5px]">
                  {t("Check-in interval")}
                </label>
                {state.profile && state.profile.proactivity !== "default" && state.profile.proactivity !== "off" && (
                  <SettingInfo
                    forceOpen={highlightedId === "proactivity-dial"}
                    text={state.profile.proactivity === "low" ? t("Doubled at this level") : t("Halved at this level")}
                  />
                )}
              </div>
              <select
                id={intervalId}
                value={state.profile?.goal_interval_minutes ?? 60}
                onChange={(e) => void update({ profile: { goal_interval_minutes: Number(e.target.value) } })}
                className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
              >
                {[...new Set([15, 30, 60, 120, 240, 480, 1440, state.profile?.goal_interval_minutes ?? 60])]
                  .sort((a, b) => a - b)
                  .map((m) => (
                    <option key={m} value={m}>
                      {m < 60 ? t("{n} min", { n: m }) : m % 60 ? `${t("{n} h", { n: Math.floor(m / 60) })} ${t("{n} min", { n: m % 60 })}` : t("{n} h", { n: m / 60 })}
                    </option>
                  ))}
              </select>
            </div>
          </SettingBlock>
          <SettingBlock id="proactivity-quiet" highlightedId={highlightedId}>
            <QuietHours value={state.profile?.quiet_hours ?? ""} onChange={(v) => void update({ profile: { quiet_hours: v } })} forceOpen={highlightedId === "proactivity-quiet"} />
          </SettingBlock>
        </Section>
        )}

        {/* Notifications */}
        {show("notifications") && (
        <Section title={t("Notifications")} id="notifications" plain={!wide}>
          <SettingBlock id="notifications-main" highlightedId={highlightedId}>
            {androidApp() ? <PhoneAppSettings name={state.profile?.name ?? "MiniMuse"} forceOpen={highlightedId === "notifications-main"} /> : <PushSettings name={state.profile?.name ?? "MiniMuse"} forceOpen={highlightedId === "notifications-main"} />}
          </SettingBlock>
        </Section>
        )}

        {/* Keep it running: only the Android app has anything to say here */}
        {keepRunningStatus() && show("keep-running") && (
          <Section title={t("Keep it running")} id="keep-running" plain={!wide}>
            <SettingBlock id="keep-running-main" highlightedId={highlightedId}>
              <KeepRunningSettings name={state.profile?.name ?? "MiniMuse"} forceOpen={highlightedId === "keep-running-main"} />
            </SettingBlock>
          </Section>
        )}

        {/* MiniMuse Cloud */}
        {show("cloud") && (
        <Section title={t("MiniMuse Cloud")} id="cloud" plain={!wide}>
          <SettingBlock id="cloud-main" highlightedId={highlightedId}>
            <CloudModeSelector
              cloudMode={s?.cloud_mode ?? "hybrid"}
              cloudSlots={s?.cloud_slots}
              cloudPrompts={s?.cloud_prompts}
              onChange={async (patch) => {
                await update(patch);
              }}
            />
            {s && (s.cloud_mode ?? "hybrid") !== "local" && (
              <button type="button" onClick={() => setTab("account")} className="w-full text-[13.5px] flex items-center justify-between pt-2">
                <span className="text-muted">{t("MiniMuse Cloud account")}</span>
                <span className="text-[12.5px] text-accent flex items-center gap-1">
                  {state.hub?.account.signed_in ? (state.hub.account.hint || t("Signed in")) : t("Sign in")} <ChevronRight size={14} />
                </span>
              </button>
            )}
          </SettingBlock>
        </Section>
        )}

        {/* Model */}
        {show("model") && (
        <Section title={t("Model")} id="model" plain={!wide}>
          <SettingBlock id="model-main" highlightedId={highlightedId} className="space-y-3">
            <ActiveModelList />
            {s && (
              <button
                type="button"
                onClick={() => openSection("cloud")}
                className="w-full text-[13.5px] flex items-center justify-between"
              >
                <span className="text-muted">{t("MiniMuse Cloud")}</span>
                <span className="text-[12.5px] flex items-center gap-1">
                  {s.cloud_mode === "local"
                    ? t("Off (Nothing)")
                    : s.cloud_mode === "cloud"
                      ? t("Everything")
                      : t("Custom")}
                  <ChevronRight size={14} className="text-muted" />
                </span>
              </button>
            )}
            {s && (
              <button type="button" onClick={() => setTab("connections")} className="w-full text-[13.5px] flex items-center justify-between">
                <span className="text-muted">{t("Provider / model")}</span>
                <span className="font-mono text-[12.5px] flex items-center gap-1">
                  {s.llm.model} <ChevronRight size={14} className="text-muted" />
                </span>
              </button>
            )}
            {s?.llm.cloud && (s.cloud_mode ?? "hybrid") !== "local" && (
              <button type="button" onClick={() => openOwnKeySetup(setTab, ownKeyWay(state.hub?.account).preset)} className="w-full text-[13.5px] flex items-center justify-between">
                <span className="text-muted">{t("Use my own API key or a plan I pay for")}</span>
                <span className="text-[12.5px] text-muted flex items-center gap-1">
                  {t("{first} first, then {others}", { first: ownKeyWay(state.hub?.account).label, others: isMainland(state.hub?.account) ? t("DeepSeek, Kimi, OpenAI, ChatGPT…") : t("OpenAI, ChatGPT, Gemini, DeepSeek…") })} <ChevronRight size={14} />
                </span>
              </button>
            )}
            <button type="button" onClick={() => setTab("connections")} className="w-full text-[13.5px] flex items-center justify-between">
              <span className="text-muted">{t("Connections")}</span>
              <span className="text-[12.5px] text-muted flex items-center gap-1">
                {t("E-mail, calendar, browser, MCP servers")} <ChevronRight size={14} />
              </span>
            </button>
          </SettingBlock>
        </Section>
        )}

        {/* Agent behavior */}
        {show("behavior") && (
        <Section title={t("Agent behavior")} id="behavior" plain={!wide}>
          <SettingBlock id="behavior-steps" highlightedId={highlightedId}>
            <Toggle
              label={t("Show the agent's steps")}
              hint={t("Every tool it uses becomes a chip in the chat. Off, the chat keeps to the conversation and the line under the name says what it is on. This device only.")}
              checked={steps}
              onChange={setShowSteps}
              collapsibleHint
              forceHintOpen={highlightedId === "behavior-steps"}
            />
          </SettingBlock>
          <SettingBlock id="behavior-thinking" highlightedId={highlightedId}>
            <Toggle
              label={t("Show thinking")}
              hint={t("Reveal the model's reasoning under each reply when the provider exposes it.")}
              checked={!!s?.agent.show_thinking}
              onChange={(v) => void update({ show_thinking: v })}
              collapsibleHint
              forceHintOpen={highlightedId === "behavior-thinking"}
            />
          </SettingBlock>
          <SettingBlock id="behavior-self-improve" highlightedId={highlightedId}>
            <Toggle
              label={t("Self-improving integrations")}
              hint={t("Let the agent add its own connectors and APIs when a gap appears, and report what it added. Off, it only suggests.")}
              checked={s?.agent.allow_self_improve !== false}
              onChange={(v) => void update({ allow_self_improve: v })}
              collapsibleHint
              forceHintOpen={highlightedId === "behavior-self-improve"}
            />
          </SettingBlock>
          <SettingBlock id="behavior-lang" highlightedId={highlightedId}>
            <div className="flex items-center gap-3">
              <div className="flex flex-1 items-center gap-1.5">
                <label htmlFor={replyLangId} className="text-[13.5px]">
                  {t("Reply language")}
                </label>
                <SettingInfo
                  forceOpen={highlightedId === "behavior-lang"}
                  text={t("What {name} writes in", { name: state.profile?.name ?? "MiniMuse" })}
                />
              </div>
              <select
                id={replyLangId}
                value={s?.agent.language ?? "auto"}
                onChange={(e) => void update({ language: e.target.value })}
                className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
              >
                <option value="auto">{t("Match mine")}</option>
                <option value="English">English</option>
                <option value="中文">中文</option>
                <option value="日本語">日本語</option>
                <option value="Español">Español</option>
                <option value="Deutsch">Deutsch</option>
                <option value="Français">Français</option>
              </select>
            </div>
          </SettingBlock>
          <SettingBlock id="behavior-bg-model" highlightedId={highlightedId}>
            <div className="flex items-center gap-3">
              <div className="flex flex-1 items-center gap-1.5">
                <label htmlFor={bgModelId} className="text-[13.5px]">
                  {t("Background model")}
                </label>
                <SettingInfo
                  forceOpen={highlightedId === "behavior-bg-model"}
                  text={t("What runs the quiet work: routines, reminders and goal passes. Your own messages still use the model picker.")}
                />
              </div>
              <select
                id={bgModelId}
                value={s?.llm?.background_model ?? ""}
                onChange={(e) => void update({ background_model: e.target.value })}
                className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
              >
                <option value="">{t("Default")}</option>
                <option value="flash">{t("Flash")}</option>
                <option value="pro">{t("Pro")}</option>
                <option value="local">{t("Local")}</option>
                <option value="gemini">{t("Gemini")}</option>
              </select>
            </div>
          </SettingBlock>
        </Section>
        )}

        {/* Permissions */}
        {show("permissions") && (
        <Section title={t("Permissions")} id="permissions" plain={!wide}>
          <SettingBlock id="permissions-main" highlightedId={highlightedId}>
            <p className="mb-2.5 px-1 text-[12.5px] leading-[17px] text-muted">
              {t("What this device's system lets MiniMuse use. Whether it may act without asking is a separate choice, under Safety.")}
            </p>
            <DevicePermissions />
          </SettingBlock>
          {androidApp() && (
            <SettingBlock id="permissions-control" highlightedId={highlightedId}>
              <PhoneControlGuide />
            </SettingBlock>
          )}
        </Section>
        )}

        {/* Smartwatch (Wear OS) */}
        {show("watch") && (
        <Section title={t("Smartwatch (Wear OS)")} id="watch" plain={!wide}>
          <SettingBlock id="watch-main" highlightedId={highlightedId}>
            <WatchPairingCard />
          </SettingBlock>
        </Section>
        )}

        {/* Default assistant: phone, watch and computer */}
        {show("assistant") && (
        <Section title={t("Default assistant")} id="assistant" plain={!wide}>
          <SettingBlock id="assistant-main" highlightedId={highlightedId}>
            <DefaultAssistantCard />
          </SettingBlock>
        </Section>
        )}

        {/* Appearance */}
        {show("appearance") && (
        <Section title={t("Appearance")} id="appearance" plain={!wide}>
          <SettingBlock id="appearance-bg" highlightedId={highlightedId}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <div className="flex min-w-[150px] flex-1 items-center gap-1.5 text-[13.5px]">
                <span>{t("Background")}</span>
                <SettingInfo
                  forceOpen={highlightedId === "appearance-bg"}
                  text={t("The canvas colour. White follows your light or dark; the rest pin one. The boxes always stay a step lighter or darker than it.")}
                />
              </div>
              <div className="flex flex-1 flex-wrap items-center justify-end gap-1.5 min-w-[180px]">
                {BG_PRESETS.map((o) => (
                  <Swatch
                    key={o.label}
                    label={t(o.label)}
                    color={o.color}
                    selected={effBg === o.value}
                    onClick={() => setBg(o.value)}
                  />
                ))}
                <label
                  aria-label={t("Custom")}
                  className={cx(
                    "relative h-7 w-7 shrink-0 cursor-pointer rounded-full",
                  )}
                  style={{
                    background: "conic-gradient(from 90deg, #e8590c, #e64980, #7048e8, #0d9488, #e8590c)",
                    boxShadow: bgSetting.startsWith("#")
                      ? "0 0 0 2.5px color-mix(in srgb, var(--om-fg) 70%, var(--om-bg) 30%), 0 0 0 4.5px color-mix(in srgb, var(--om-fg) 12%, var(--om-bg) 88%)"
                      : "0 0 0 1.5px color-mix(in srgb, var(--om-fg) 35%, var(--om-bg) 65%)",
                  }}
                >
                  <input
                    type="color"
                    value={bgSetting.startsWith("#") ? bgSetting : "#ffffff"}
                    onChange={(e) => setBg(e.target.value)}
                    className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                  />
                </label>
              </div>
            </div>
          </SettingBlock>
          <SettingBlock id="appearance-scale" highlightedId={highlightedId}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <div className="flex min-w-[150px] flex-1 items-center gap-1.5 text-[13.5px]">
                <span>{t("UI scale")}</span>
                <SettingInfo
                  forceOpen={highlightedId === "appearance-scale"}
                  text={t("Text, icons and boxes together, smaller or bigger on this device.")}
                />
              </div>
              <ScaleSlider />
            </div>
          </SettingBlock>
          {!androidApp() && <DesktopFullscreenSetting highlightedId={highlightedId} />}
          <SettingBlock id="appearance-accent" highlightedId={highlightedId} className="space-y-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <div className="flex min-w-[150px] flex-1 items-center gap-1.5 text-[13.5px]">
                <span>{t("Accent")}</span>
                <SettingInfo
                  forceOpen={highlightedId === "appearance-accent"}
                  text={t("The colour of buttons, links and the working dot. The app's blue is the start; pick one or mix your own.")}
                />
              </div>
              <div className="flex flex-1 flex-wrap items-center justify-end gap-1.5 min-w-[180px]">
                {ACCENTS.map((o) => (
                  <Swatch key={o.label} label={t(o.label)} color={o.color} selected={accent === o.value} onClick={() => setAccent(o.value)} />
                ))}
                <label
                  aria-label={t("Custom")}
                  className="relative h-7 w-7 shrink-0 cursor-pointer rounded-full"
                  style={{
                    background: "conic-gradient(from 90deg, #e8590c, #e64980, #7048e8, #0d9488, #e8590c)",
                    boxShadow: accent.startsWith("#")
                      ? "0 0 0 2.5px color-mix(in srgb, var(--om-fg) 70%, var(--om-bg) 30%), 0 0 0 4.5px color-mix(in srgb, var(--om-fg) 12%, var(--om-bg) 88%)"
                      : "0 0 0 1.5px color-mix(in srgb, var(--om-fg) 35%, var(--om-bg) 65%)",
                  }}
                >
                  <input
                    type="color"
                    value={accent.startsWith("#") ? accent : "#0064d4"}
                    onChange={(e) => setAccent(e.target.value)}
                    className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                  />
                </label>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <div className="flex min-w-[150px] flex-1 items-center gap-1.5 text-[13.5px]">
                <span>{t("User messages")}</span>
                <SettingInfo
                  forceOpen={highlightedId === "appearance-accent"}
                  text={t("The colour of your own message bubbles. They follow the accent until you pick a colour here.")}
                />
              </div>
              <div className="flex flex-1 flex-wrap items-center justify-end gap-1.5 min-w-[180px]">
                <button
                  type="button"
                  onClick={() => setBubble("")}
                  aria-label={t("Follow the accent")}
                  aria-pressed={!bubble}
                  className="h-7 w-7 shrink-0 rounded-full text-muted"
                  style={{
                    background: "linear-gradient(135deg, transparent 44%, currentColor 44%, currentColor 56%, transparent 56%)",
                    boxShadow: !bubble
                      ? "0 0 0 2.5px color-mix(in srgb, var(--om-fg) 70%, var(--om-bg) 30%), 0 0 0 4.5px color-mix(in srgb, var(--om-fg) 12%, var(--om-bg) 88%)"
                      : "0 0 0 1.5px color-mix(in srgb, var(--om-fg) 35%, var(--om-bg) 65%)",
                  }}
                />
                {BUBBLE_PRESETS.map((o) => (
                  <Swatch key={o.label} label={t(o.label)} color={o.color} selected={bubble === o.value} onClick={() => setBubble(o.value)} />
                ))}
                <label
                  aria-label={t("Custom")}
                  className="relative h-7 w-7 shrink-0 cursor-pointer rounded-full"
                  style={{
                    background: "conic-gradient(from 90deg, #e8590c, #e64980, #7048e8, #0d9488, #e8590c)",
                    boxShadow: bubble.startsWith("#")
                      ? "0 0 0 2.5px color-mix(in srgb, var(--om-fg) 70%, var(--om-bg) 30%), 0 0 0 4.5px color-mix(in srgb, var(--om-fg) 12%, var(--om-bg) 88%)"
                      : "0 0 0 1.5px color-mix(in srgb, var(--om-fg) 35%, var(--om-bg) 65%)",
                  }}
                >
                  <input
                    type="color"
                    value={bubble.startsWith("#") ? bubble : accent || "#0064d4"}
                    onChange={(e) => setBubble(e.target.value)}
                    className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                  />
                </label>
              </div>
            </div>
          </SettingBlock>
          <SettingBlock id="appearance-lang" highlightedId={highlightedId}>
            <div className="flex items-center gap-3">
              <div className="flex flex-1 items-center gap-1.5">
                <label htmlFor={appLangId} className="text-[13.5px]">
                  {t("App language")}
                </label>
                <SettingInfo forceOpen={highlightedId === "appearance-lang"} text={t("This device only")} />
              </div>
              <select
                id={appLangId}
                value={localeSetting}
                onChange={(e) => setLocaleSetting(e.target.value as typeof localeSetting)}
                className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
              >
                {LOCALES.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.value === "auto" ? t("Auto") : l.label}
                  </option>
                ))}
              </select>
            </div>
          </SettingBlock>
          <SettingBlock id="appearance-tabs" highlightedId={highlightedId}>
            <div className="space-y-2.5 border-t border-border/60 pt-3">
              <div className="text-[12.5px] font-semibold uppercase tracking-wide text-muted">{t("Navigation tabs & Maps")}</div>
              <Toggle
                label={t("Show Calendar tab")}
                hint={t("Agenda, week & month views, local events, and Google / .ics calendar feeds.")}
                checked={s?.ui?.show_calendar !== "false"}
                onChange={(on) => {
                  void api
                    .updateSettings({ ui: { ...(s?.ui ?? {}), show_calendar: on ? "true" : "false" } })
                    .then(() => refreshSettings());
                }}
                collapsibleHint
                forceHintOpen={highlightedId === "appearance-tabs"}
              />
              <Toggle
                label={t("Show Location & Maps tab")}
                hint={t("Common addresses, frequent places, friend addresses, driving speed/traffic & one-tap Google/Apple/Waze launch.")}
                checked={s?.ui?.show_location !== "false"}
                onChange={(on) => {
                  void api
                    .updateSettings({ ui: { ...(s?.ui ?? {}), show_location: on ? "true" : "false" } })
                    .then(() => refreshSettings());
                }}
                collapsibleHint
                forceHintOpen={highlightedId === "appearance-tabs"}
              />
              <div className="flex items-center gap-3 pt-1">
                <div className="flex flex-1 items-center gap-1.5">
                  <span className="text-[13.5px]">{t("Preferred map app")}</span>
                  <SettingInfo
                    forceOpen={highlightedId === "appearance-tabs"}
                    text={t("Opened when tapping an address, event location, or contact.")}
                  />
                </div>
                <select
                  value={s?.ui?.map_app || "google"}
                  onChange={(e) => {
                    const val = e.target.value;
                    void api
                      .updateSettings({ ui: { ...(s?.ui ?? {}), map_app: val } })
                      .then(() => refreshSettings());
                  }}
                  className="rounded-2xl bg-surface-2 px-3 py-2 text-[13.5px] outline-none"
                >
                  <option value="google">Google Maps</option>
                  <option value="apple">Apple Maps</option>
                  <option value="waze">Waze</option>
                  <option value="osm">OpenStreetMap</option>
                  <option value="geo">System geo:</option>
                </select>
              </div>
            </div>
          </SettingBlock>

          <button
            type="button"
            onClick={() => {
              pushUi({ accent: accentNow(), bubble: bubbleNow(), bg: bgNow(), scale: String(uiScaleNow()) });
              toast(t("The look now matches on every device."));
            }}
            className="w-full rounded-2xl border border-border bg-surface px-3 py-2.5 text-[13.5px] text-fg/85 transition active:bg-surface-2"
          >
            {t("Use this device's look everywhere")}
          </button>
        </Section>
        )}

        {/* The desktop app's own switches */}
        {isDesktopApp() && show("desktop") && (
          <Section title={t("Desktop app")} id="desktop" plain={!wide}>
            <SettingBlock id="desktop-main" highlightedId={highlightedId}>
              <DesktopAppSettings forceOpen={highlightedId === "desktop-main"} />
            </SettingBlock>
          </Section>
        )}

        {/* Data controls */}
        {show("data") && (
        <Section title={t("Data controls")} id="data" plain={!wide}>
          <SettingBlock id="data-main" highlightedId={highlightedId}>
            <DataControls flush />
          </SettingBlock>
        </Section>
        )}

        {/* Import & export */}
        {show("import") && (
        <Section title={t("Import & export")} id="import" plain={!wide}>
          <SettingBlock id="import-main" highlightedId={highlightedId}>
            <ImportExport />
          </SettingBlock>
          <SettingBlock id="setup-again" highlightedId={highlightedId}>
            <MuseCard>
              <MuseRow
                icon={<Sparkles size={22} />}
                label={t("Run setup again")}
                value={t("Pick up where setup left off, or change what you chose")}
                onClick={() => reopenSetup()}
              />
            </MuseCard>
          </SettingBlock>
        </Section>
        )}

        {/* Developer */}
        {show("developer") && (
        <Section title={t("Developer")} id="developer" plain={!wide}>
          <SettingBlock id="developer-main" highlightedId={highlightedId}>
            <Toggle
              label={t("Developer tools")}
              hint={t("The Coding screen (Cursor, Codex and the other coding agents on this computer) in the sidebar, diagnostics, PWA controls, and the runtime address below.")}
              checked={developer}
              onChange={setDeveloperTools}
              collapsibleHint
              forceHintOpen={highlightedId === "developer-main"}
            />
            {developer && <DeveloperToolsSection forceOpen={highlightedId === "developer-main"} />}
          </SettingBlock>
        </Section>
        )}

        {/* About */}
        {show("about") && (
        <Section title={t("About")} id="about" plain={!wide}>
          <SettingBlock id="about-main" highlightedId={highlightedId} className="space-y-3">
            <CommunityNotice />
            <VersionRows installed={state.version} release={release} checking={checking} onCheck={() => void checkRelease(true)} />
            <MuseCard>
              <div className="px-4 py-3 text-[13.5px] leading-[20px]">
                <p>
                  {t("This MiniMuse is a fork of the original nanoMuse, built with more features, fixes and upgrades.")}
                </p>
                <p className="mt-1.5 text-muted">
                  {t("Free software under the GPL-3.0-or-later license; the code lives on GitHub.")}
                </p>
                <a
                  href="https://github.com/leothefleo49/MiniMuse"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 inline-flex items-center gap-1.5 text-[13.5px] font-medium text-accent"
                >
                  <Code2 size={15} /> {t("Source on GitHub")}
                </a>
              </div>
            </MuseCard>
            <div className="text-[13px] text-muted space-y-1">
              {s && <div className="break-all">{t("Data:")} {s.data_dir}</div>}
              {s && <div className="break-all">{t("Workspace:")} {s.agent.workspace}</div>}
              <div>{state.connected ? t("Connected") : t("Reconnecting…")}</div>
            </div>
            <button
              type="button"
              onClick={() => {
                const phone = androidApp();
                if (phone) {
                  setDisconnectOpen(true);
                  return;
                }
                setToken("");
                window.location.reload();
              }}
              className="w-full rounded-2xl border border-border py-2.5 text-[14px] font-medium flex items-center justify-center gap-2 text-muted"
            >
              <LogOut size={16} /> {androidApp() ? t("Disconnect from this server") : t("Forget this device's access token")}
            </button>
            <SwitchModeConfirm open={disconnectOpen} onCancel={() => setDisconnectOpen(false)} />
          </SettingBlock>
        </Section>
        )}
        </div>
      </div>
      </div>
      <ConfirmDialog
        open={confirmAutoSentinel}
        title={t("Turn on Hands-off mode?")}
        body={t(
          "In Hands-off mode, the Sentinel approves actions automatically unless an explicit deny rule matches, and it may trigger system or screen popups without stopping to ask.",
        )}
        confirmLabel={t("Switch to Hands-off")}
        danger
        onCancel={() => setConfirmAutoSentinel(false)}
        onConfirm={() => {
          setConfirmAutoSentinel(false);
          void update({ sentinel_mode: "auto" });
        }}
      />
    </div>
  );
}

type SectionId = "who" | "usage" | "sentinel" | "app-lock" | "passwords" | "proactivity" | "behavior" | "permissions" | "watch" | "assistant" | "notifications" | "keep-running" | "cloud" | "model" | "appearance" | "desktop" | "data" | "import" | "developer" | "about";
const SECTION_IDS: SectionId[] = ["who", "usage", "sentinel", "app-lock", "passwords", "proactivity", "behavior", "permissions", "watch", "assistant", "notifications", "keep-running", "cloud", "model", "appearance", "desktop", "data", "import", "developer", "about"];

function CloudModeSelector({
  cloudMode,
  cloudSlots,
  cloudPrompts,
  onChange,
}: {
  cloudMode: "local" | "hybrid" | "cloud";
  cloudSlots?: { chat?: "local" | "cloud"; media?: "local" | "cloud"; sync?: "local" | "cloud" };
  cloudPrompts?: boolean;
  onChange: (patch: { cloud_mode?: string; cloud_slots?: Record<string, string>; cloud_prompts?: boolean }) => Promise<void>;
}) {
  const t = useT();
  const modes: Array<{ id: "local" | "hybrid" | "cloud"; title: string; desc: string }> = [
    {
      id: "local",
      title: "Nothing (MiniMuse Cloud off)",
      desc: "Use nothing from MiniMuse Cloud. Runs locally without cloud models, cloud sync, or sign-in prompts.",
    },
    {
      id: "hybrid",
      title: "Custom (Choose manually)",
      desc: "Choose individually what can and cannot use MiniMuse Cloud, including whether sign-in prompts appear.",
    },
    {
      id: "cloud",
      title: "Everything (All MiniMuse Cloud)",
      desc: "Enable MiniMuse Cloud for chat intelligence, vision & media, and multi-device relay sync.",
    },
  ];

  return (
    <div className="space-y-3 pb-2">
      <div className="space-y-2">
        {modes.map((m) => {
          const active = cloudMode === m.id;
          return (
            <button
              key={m.id}
              type="button"
              onClick={() => void onChange({ cloud_mode: m.id })}
              className={cx(
                "flex w-full items-start gap-3 rounded-2xl border p-3 text-left transition active:scale-[0.99]",
                active ? "border-accent bg-accent/8" : "border-border hover:bg-surface-2/60",
              )}
            >
              <div className={cx("mt-0.5", active ? "text-accent" : "text-muted")}>
                {m.id === "local" ? <LockKeyhole size={18} /> : m.id === "cloud" ? <Cloud size={18} /> : <Zap size={18} />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between font-medium text-[13.5px]">
                  <span>{t(m.title)}</span>
                  {active && <Check size={16} className="text-accent" />}
                </div>
                <p className="mt-0.5 text-[12px] leading-snug text-muted">{t(m.desc)}</p>
              </div>
            </button>
          );
        })}
      </div>

      {cloudMode === "hybrid" && (
        <div className="rounded-2xl border border-border/80 bg-surface-2/50 p-3 space-y-2.5">
          <div className="text-[12px] font-medium uppercase tracking-wide text-muted">
            {t("Hybrid Slot Routing")}
          </div>
          {(["chat", "media", "sync"] as const).map((slot) => {
            const current = cloudSlots?.[slot] ?? "cloud";
            const slotLabels = {
              chat: { title: t("Chat intelligence"), desc: t("Text generation and agent tools") },
              media: { title: t("Vision & media"), desc: t("Photo analysis and image/video models") },
              sync: { title: t("Multi-device sync"), desc: t("Relay conversations across devices") },
            };
            return (
              <div key={slot} className="flex items-center justify-between gap-3 text-[13px]">
                <div className="min-w-0 flex-1">
                  <div className="font-medium text-[12.5px]">{slotLabels[slot].title}</div>
                  <div className="text-[11px] text-muted">{slotLabels[slot].desc}</div>
                </div>
                <div className="flex rounded-xl bg-surface p-1 border border-border/60">
                  <button
                    type="button"
                    onClick={() => void onChange({ cloud_slots: { ...cloudSlots, [slot]: "local" } })}
                    className={cx(
                      "rounded-lg px-2.5 py-1 text-[11.5px] font-medium transition",
                      current === "local" ? "bg-accent text-accent-fg shadow-sm" : "text-muted hover:text-fg",
                    )}
                  >
                    {t("Local")}
                  </button>
                  <button
                    type="button"
                    onClick={() => void onChange({ cloud_slots: { ...cloudSlots, [slot]: "cloud" } })}
                    className={cx(
                      "rounded-lg px-2.5 py-1 text-[11.5px] font-medium transition",
                      current === "cloud" ? "bg-accent text-accent-fg shadow-sm" : "text-muted hover:text-fg",
                    )}
                  >
                    {t("Cloud")}
                  </button>
                </div>
              </div>
            );
          })}
          <div className="flex items-center justify-between gap-3 pt-2 border-t border-border/50 text-[13px]">
            <div className="min-w-0 flex-1">
              <div className="font-medium text-[12.5px]">{t("Sign-in prompts & banners")}</div>
              <div className="text-[11px] text-muted">{t("Show MiniMuse Cloud sign-in cards when signed out")}</div>
            </div>
            <div className="flex rounded-xl bg-surface p-1 border border-border/60">
              <button
                type="button"
                onClick={() => void onChange({ cloud_prompts: false })}
                className={cx(
                  "rounded-lg px-2.5 py-1 text-[11.5px] font-medium transition",
                  !cloudPrompts ? "bg-accent text-accent-fg shadow-sm" : "text-muted hover:text-fg",
                )}
              >
                {t("Off")}
              </button>
              <button
                type="button"
                onClick={() => void onChange({ cloud_prompts: true })}
                className={cx(
                  "rounded-lg px-2.5 py-1 text-[11.5px] font-medium transition",
                  cloudPrompts ? "bg-accent text-accent-fg shadow-sm" : "text-muted hover:text-fg",
                )}
              >
                {t("On")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DeveloperToolsSection({ forceOpen = false }: { forceOpen?: boolean } = {}) {
  const t = useT();
  const { toast } = useStore();
  const fullscreen = usePwaFullscreen();
  const [pingMs, setPingMs] = useState<number | null>(null);
  const [pingBusy, setPingBusy] = useState(false);
  const [cacheCleared, setCacheCleared] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const clearCacheAndReload = async () => {
    try {
      if (typeof caches !== "undefined") {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
      if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister()));
      }
      setCacheCleared(true);
      toast(t("Caches cleared. Reloading…"));
      setTimeout(() => window.location.reload(), 600);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const measurePing = async () => {
    setPingBusy(true);
    const start = performance.now();
    try {
      await api.health();
      const elapsed = Math.round(performance.now() - start);
      setPingMs(elapsed);
    } catch {
      setPingMs(-1);
    } finally {
      setPingBusy(false);
    }
  };

  const isStandalone = typeof window !== "undefined" && window.matchMedia?.("(display-mode: standalone)").matches;

  return (
    <div className="space-y-4 pt-2">
      <div className="text-[13px] text-muted space-y-1">
        <div className="break-all">
          <span className="font-semibold text-fg/80">{t("Runtime:")}</span> {window.location.origin}
        </div>
        <div className="text-[12px]">
          {t("Access token stored in data/server_token file.")}
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-surface-2/40 p-3.5 space-y-3">
        <div className="text-[12.5px] font-medium uppercase tracking-wide text-muted">
          {t("PWA & Display Tools")}
        </div>
        <Toggle
          label={t("Full-bleed PWA mode (hide system bottom bar)")}
          hint={t("Automatically immerses the Android navigation bar so --om-bg fills 100% of the display edge-to-edge.")}
          checked={fullscreen}
          onChange={setPwaFullscreen}
          collapsibleHint
          forceHintOpen={forceOpen}
        />
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1 border-t border-border/40">
          <div>
            <div className="text-[13.5px] font-medium">{t("PWA Caches & Service Worker")}</div>
            <div className="text-[12px] text-muted">{t("Wipe CacheStorage and unregister service workers.")}</div>
          </div>
          <button
            type="button"
            onClick={() => void clearCacheAndReload()}
            className="rounded-xl border border-border bg-surface px-3 py-1.5 text-[12.5px] font-medium text-fg shadow-sm hover:bg-surface-2 transition active:scale-[0.98]"
          >
            {cacheCleared ? t("Cleared!") : t("Clear & Reload")}
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-border bg-surface-2/40 p-3.5 space-y-3">
        <div className="text-[12.5px] font-medium uppercase tracking-wide text-muted">
          {t("Live Diagnostics & Latency")}
        </div>
        <div className="flex items-center justify-between gap-2">
          <div>
            <div className="text-[13.5px] font-medium">{t("Server Latency Check")}</div>
            <div className="text-[12px] text-muted">
              {pingMs === null ? t("Not measured yet") : pingMs === -1 ? t("Request failed") : `${pingMs} ms roundtrip`}
            </div>
          </div>
          <button
            type="button"
            disabled={pingBusy}
            onClick={() => void measurePing()}
            className="flex items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-[12.5px] font-medium text-accent-fg shadow-sm disabled:opacity-60 active:scale-[0.98]"
          >
            {pingBusy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} {t("Ping")}
          </button>
        </div>

        <div className="pt-2 border-t border-border/40">
          <button
            type="button"
            onClick={() => setInspectorOpen(!inspectorOpen)}
            className="text-[12.5px] font-medium text-accent flex items-center gap-1 hover:underline"
          >
            {inspectorOpen ? t("Hide Viewport & Theme Inspector") : t("Show Viewport & Theme Inspector")}
          </button>
          {inspectorOpen && (
            <div className="mt-2 rounded-xl bg-surface p-3 font-mono text-[11.5px] leading-relaxed text-muted space-y-1 select-all">
              <div>viewport: {typeof window !== "undefined" ? `${window.innerWidth} x ${window.innerHeight}` : "n/a"}</div>
              <div>pixelRatio: {typeof window !== "undefined" ? window.devicePixelRatio : 1}</div>
              <div>displayMode: {isStandalone ? "standalone" : "browser"}</div>
              <div>fullscreenElement: {typeof document !== "undefined" && document.fullscreenElement ? "active" : "none"}</div>
              <div>theme: {typeof document !== "undefined" ? document.documentElement.dataset.theme : "light"}</div>
              <div>bgColor: {typeof document !== "undefined" ? getComputedStyle(document.documentElement).getPropertyValue("--om-bg").trim() : "n/a"}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The Version card (contract C2): the installed release, and the latest one as the runtime
 * found it on nanomuse.cn/dl or GitHub — "you have it", "x is out" with Update, or "could
 * not check" with a try again. Permanent: both lines are there whatever the answer. A phone
 * shell checks on its own, so the latest line says so instead.
 */
function VersionRows({ installed, release, checking, onCheck }: { installed: string; release: UpdateView | null; checking: boolean; onCheck: () => void }) {
  const t = useT();
  const { state, refreshSettings, toast } = useStore();
  const phone = Boolean(androidApp());
  const dUpdates = desktopUpdates();
  // self-update: the runtime installs the new release into its own environment and
  // re-executes itself; the page reconnects. The desktop bundle and the phone app update
  // by being replaced whole, so they keep their own ways.
  const [updating, setUpdating] = useState(false);
  const [checkingPhone, setCheckingPhone] = useState(false);
  const [phoneInstalling, setPhoneInstalling] = useState(false);
  const [updateNote, setUpdateNote] = useState<string | null>(null);
  const [phoneView, setPhoneView] = useState<AppUpdateView | null>(null);
  const [phoneAuto, setPhoneAuto] = useState(() => {
    const app = androidApp();
    return typeof app?.autoUpdateEnabled === "function" ? app.autoUpdateEnabled() : true;
  });
  const [savingAuto, setSavingAuto] = useState(false);
  const autoRuntime = release?.auto_update ?? state.settings?.auto_update ?? true;

  useEffect(() => {
    if (!phone) return;
    const app = androidApp();
    if (typeof app?.checkAppUpdate === "function") {
      try {
        setPhoneView(JSON.parse(app.checkAppUpdate()) as AppUpdateView);
      } catch {
        /* older APK bridge */
      }
      app.refreshAppUpdate?.();
    }
    const onUpdateEvent = (ev: Event) => {
      const detail = (ev as CustomEvent<AppUpdateView>).detail;
      if (detail) {
        setPhoneView(detail);
        setCheckingPhone(false);
        if (!detail.downloading) setPhoneInstalling(false);
        return;
      }
      if (typeof app?.checkAppUpdate !== "function") return;
      try {
        const next = JSON.parse(app.checkAppUpdate()) as AppUpdateView;
        setPhoneView(next);
        setCheckingPhone(false);
        if (!next.downloading) setPhoneInstalling(false);
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("nanomuse:app-update", onUpdateEvent);
    return () => window.removeEventListener("nanomuse:app-update", onUpdateEvent);
  }, [phone]);

  const applyUpdate = async () => {
    if (updating) return;
    setUpdating(true);
    setUpdateNote(null);
    try {
      const result = await api.applyUpdate();
      if (result.ok) {
        setUpdateNote(t("Installed {version}. Restarting…", { version: result.to ?? "" }));
        const err = await waitForUpdatedServerAndReload(installed);
        if (err) {
          setUpdateNote(err);
          setUpdating(false);
        }
      } else {
        setUpdateNote(result.error ?? t("Could not update"));
        setUpdating(false);
      }
    } catch {
      setUpdateNote(t("Could not update"));
      setUpdating(false);
    }
  };
  const toggleRuntimeAuto = async (on: boolean) => {
    setSavingAuto(true);
    try {
      await api.updateAutoSettings(on);
      if (dUpdates?.setAutoDownload) await dUpdates.setAutoDownload(on);
      await refreshSettings();
      if (on) onCheck();
      toast(on ? t("Automatic updates enabled.") : t("Automatic updates turned off."));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setSavingAuto(false);
    }
  };
  const togglePhoneAuto = (on: boolean) => {
    const app = androidApp();
    if (typeof app?.setAutoUpdateEnabled === "function") {
      app.setAutoUpdateEnabled(on);
    }
    setPhoneAuto(on);
    toast(on ? t("Automatic updates enabled.") : t("Automatic updates turned off."));
  };
  const checkNow = (
    <button type="button" onClick={onCheck} disabled={checking} className="text-accent underline-offset-2 hover:underline disabled:opacity-60">
      {checking ? t("Checking…") : t("Check now")}
    </button>
  );
  // Check both runtime/web bundle and Android shell
  const checkPhoneUpdate = async () => {
    const app = androidApp();
    onCheck();
    if (typeof app?.refreshAppUpdate === "function") {
      setCheckingPhone(true);
      setUpdateNote(t("Checking for updates…"));
      app.refreshAppUpdate();
      return;
    }
    if (typeof app?.checkAppUpdate !== "function") return;
    try {
      const view = JSON.parse(app.checkAppUpdate()) as AppUpdateView;
      setPhoneView(view);
      setUpdateNote(view.latest ? t("{version} is out", { version: view.latest }) : t("You have the newest MiniMuse"));
    } catch {
      setUpdateNote(t("Could not check"));
    }
  };
  const installPhoneUpdate = async () => {
    if (phoneInstalling || phoneView?.downloading) return;
    const app = androidApp();
    if (typeof app?.installAppUpdate !== "function") return;
    setPhoneInstalling(true);
    setUpdateNote(t("Starting single-flight download…"));
    app.installAppUpdate();
  };
  const live = state.appUpdate;
  const phoneBusy = phoneInstalling || !!phoneView?.downloading;
  let latest: ReactNode;
  if (live && (live.status === "applying" || live.status === "restarting"))
    latest =
      live.status === "restarting"
        ? t("Installed {version}. Restarting…", { version: live.latest ?? "" })
        : t("Installing {version}…", { version: live.latest ?? "" });
  else if (updating) latest = t("Updating in place…");
  else if (phone && release?.latest && release.newer && release.auto_supported !== false)
    latest = (
      <>
        <span className="font-medium text-fg">{t("{version} is out", { version: release.latest })}</span>
        {" · "}
        <button type="button" onClick={() => void applyUpdate()} disabled={updating} className="font-medium text-accent underline-offset-2 hover:underline disabled:opacity-60">
          {t("Update in place")}
        </button>
        {phoneView?.shellRequired && phoneView?.latest ? (
          <>
            {" · "}
            <button type="button" onClick={() => void installPhoneUpdate()} disabled={phoneBusy} className="text-accent underline-offset-2 hover:underline disabled:opacity-60">
              {phoneBusy
                ? t("Downloading… {pct}%", { pct: String(phoneView?.progress ?? 0) })
                : phoneView?.downloaded
                  ? t("Install Shell APK")
                  : t("Update Shell APK")}
            </button>
          </>
        ) : null}
      </>
    );
  else if (phone)
    latest =
      typeof androidApp()?.checkAppUpdate === "function" ? (
        <>
          {phoneBusy ? (
            <span className="font-medium text-fg">
              {t("Downloading {version}… {pct}%", {
                version: phoneView?.downloadingVersion || phoneView?.latest || "",
                pct: String(phoneView?.progress ?? 0),
              })}
              {" · "}
            </span>
          ) : phoneView?.downloaded && phoneView?.downloadedVersion ? (
            <span className="font-medium text-fg">
              {t("{version} downloaded and ready to install", { version: phoneView.downloadedVersion })}
              {" · "}
            </span>
          ) : phoneView?.shellRequired && phoneView?.latest ? (
            <span className="font-medium text-fg">
              {t("Shell update {version} is out", { version: phoneView.latest })}
              {" · "}
            </span>
          ) : release?.latest ? (
            <span>
              {t("Latest {version}; you have it", { version: release.latest })}
              {" · "}
            </span>
          ) : null}
          <button type="button" onClick={() => void checkPhoneUpdate()} disabled={checkingPhone || checking} className="text-accent underline-offset-2 hover:underline disabled:opacity-60">
            {checkingPhone || checking ? t("Checking…") : t("Check for updates")}
          </button>
          {(phoneView?.shellRequired || phoneView?.downloaded || phoneBusy) && (
            <>
              {" · "}
              <button type="button" onClick={() => void installPhoneUpdate()} disabled={phoneBusy} className="font-medium text-accent underline-offset-2 hover:underline disabled:opacity-60">
                {phoneBusy
                  ? t("Downloading… {pct}%", { pct: String(phoneView?.progress ?? 0) })
                  : phoneView?.downloaded
                    ? t("Install Update")
                    : t("Update Shell APK")}
              </button>
            </>
          )}
        </>
      ) : (
        t("The phone app updates itself automatically in the background.")
      );
  else if (release && !release.enabled) latest = t("Update checks are off here.");
  else if (checking && !release) latest = t("Checking…");
  else if (release?.latest && release.newer)
    latest = (
      <>
        {t("{version} is out", { version: release.latest })}
        {release.auto_supported !== false && (
          <>
            {" · "}
            <button type="button" onClick={() => void applyUpdate()} disabled={updating} className="font-medium text-accent underline-offset-2 hover:underline disabled:opacity-60">
              {t("Update in place")}
            </button>
          </>
        )}
        {" · "}
        <a href={release.download_url || release.url} target="_blank" rel="noreferrer" className="text-accent underline-offset-2 hover:underline">
          {t("Download")}
        </a>
      </>
    );
  else if (release?.latest) latest = <>{t("Latest {version}; you have it", { version: release.latest })} · {checkNow}</>;
  else latest = <>{t("Could not check")} · {checkNow}</>;

  const runtimeError = !phone ? updateNote || (live?.status === "error" ? live.error : null) || release?.last_error || null : updateNote;

  return (
    <div className="space-y-3">
      <div className="rounded-2xl bg-surface-2/60 px-3.5 py-2.5 text-[13px]">
        <div className="font-medium">{t("MiniMuse {version}", { version: installed })}</div>
        <div className="mt-0.5 text-muted">{latest}</div>
        {runtimeError && <div className="mt-1 text-[12px] text-accent">{runtimeError}</div>}
        {!phone && release?.latest && release.newer && release.auto_supported === false && !runtimeError && (
          <div className="mt-1 text-[12px] text-muted">{t("Editable source checkout: update with git pull or pip, or install a packaged release for automatic updates.")}</div>
        )}
        {!phone && release?.latest && release.newer && release.auto_supported !== false && !runtimeError && (
          <div className="mt-1 text-[12px] text-muted">{t("The runtime swaps itself and comes back on the same port. The previous release is kept for a rollback.")}</div>
        )}
        {!phone && release?.checked_at && <div className="mt-1 text-[12px] text-muted">{t("Checked {when}", { when: relativeTime(release.checked_at) })}</div>}
      </div>
      <Toggle
        label={t("Automatically check and install updates")}
        hint={
          phone
            ? t("Checks GitHub every 4 hours, downloads new APKs automatically, and prompts to install.")
            : t("Checks on startup and every 4 hours, and installs packaged updates automatically in the background.")
        }
        checked={phone ? phoneAuto : autoRuntime}
        disabled={savingAuto}
        onChange={(v) => {
          if (phone) togglePhoneAuto(v);
          else void toggleRuntimeAuto(v);
        }}
      />
    </div>
  );
}

/**
 * The phone's Settings: Muse's bar, then white cards of outlined-glyph rows on the grey
 * canvas, grouped the way the Android app groups them — the model, the agent, how it
 * behaves, the app, about — each row opening its own page or screen.
 */
function SettingsHome({
  release,
  onOpen,
  catalog,
  query,
  onQuery,
}: {
  release: UpdateView | null;
  onOpen: (id: SectionId, settingId?: string) => void;
  catalog: SettingsCatalogEntry<SectionId>[];
  query: string;
  onQuery: (value: string) => void;
}) {
  const { state, dispatch, setTab } = useStore();
  const t = useT();
  const s = state.settings;
  const hub = state.hub;
  useEffect(() => {
    if (state.sync) return;
    void api
      .syncState()
      .then((sync) => dispatch({ type: "ws", msg: { kind: "sync", sync } }))
      .catch(() => {});
  }, [dispatch, state.sync]);
  // what the configured providers cover (contract C11), from the runtime; null on an older one
  const providers = useProviders();
  const name = state.profile?.name ?? "MiniMuse";
  const theme = useTheme();
  const phone = androidApp();
  const [lockPromptDismissed, setLockPromptDismissed] = useState(() => readLockPromptDismissed());
  const [confirmExternal, setConfirmExternal] = useState<{ label: string; url: string } | null>(null);

  const hubOthers = hub?.devices.filter((d) => !d.this && d.kind !== "web") ?? [];
  const { total: totalDevs } = countDevices(hub, state.devices ?? s?.local_devices, state.sync);
  const coding = hubOthers.filter((d) => d.online && d.actions?.includes("coding.sessions"));
  const mode = s?.sentinel.mode;
  const modeLabel = mode === "ask" ? t("Balanced") : mode === "strict" ? t("Cautious") : mode === "auto" ? t("Hands-off") : "";
  const level = state.profile?.proactivity ?? "default";
  const levelLabel = level === "off" ? t("Off") : level === "low" ? t("Low") : level === "high" ? t("High") : t("Default");
  const themeLabel = theme === "dark" ? t("Dark") : t("White");
  const none = t("none");

  const devicesSubtitle = totalDevs > 0 ? (totalDevs === 1 ? t("1 device") : t("{n} devices", { n: totalDevs })) : none;

  const appLock = s?.app_lock;
  const appLockSubtitle = appLock?.enabled
    ? appLock.biometric
      ? t("Fingerprint / Face ID")
      : appLock.passkey
        ? t("Passkey")
        : t("Password / PIN")
    : t("Not set up");

  const passwordsCount = s?.passwords_count ?? 0;
  const passwordsSubtitle = passwordsCount > 0 ? (passwordsCount === 1 ? t("1 login") : t("{n} logins", { n: passwordsCount })) : t("None");

  const scalePct = Math.round(uiScaleNow() * 100);
  const appearanceSubtitle = `${themeLabel} · ${scalePct}%`;

  const cloudMode = s?.cloud_mode ?? "hybrid";
  const cloudModeLabel = cloudMode === "local" ? t("Off (Nothing)") : cloudMode === "cloud" ? t("Everything") : t("Custom");
  const cloudValue = cloudMode === "local" ? t("Off (Nothing)") : hub?.account.signed_in ? (hub.account.hint || t("Signed in")) : cloudModeLabel;
  const modelSubtitle = s ? `${cloudModeLabel} · ${s.llm.cloud && cloudMode !== "local" ? "MiniMuse Cloud" : s.llm.provider}` : t("Add a provider and pick its models");
  const homeSearching = queryWords(query).length > 0;
  const catalogHits = searchSettingsCatalog(catalog, query);
  const onHomeSearchKey = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      if (query.trim() === "") return;
      event.preventDefault();
      onQuery("");
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    const first = catalogHits[0];
    if (first) onOpen(first.entry.sectionId, first.entry.id);
  };
  const searchBox = (
    <div className="px-4 pb-1 pt-1">
      <div className="flex items-center gap-2 rounded-xl border border-border/60 bg-surface-2 px-2.5 py-2 focus-within:ring-2 focus-within:ring-accent/35">
        <Search size={15} className="shrink-0 text-muted" />
        <input
          type="text"
          value={query}
          onChange={(e) => onQuery(e.currentTarget.value)}
          onKeyDown={onHomeSearchKey}
          placeholder={t("Search settings")}
          aria-label={t("Search settings")}
          className="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-muted"
        />
        {query && (
          <button
            type="button"
            onClick={() => onQuery("")}
            aria-label={t("Clear search")}
            className="rounded-full p-0.5 text-muted hover:bg-surface hover:text-fg"
          >
            <X size={14} />
          </button>
        )}
      </div>
    </div>
  );
  if (homeSearching) {
    return (
      <div className="flex h-full flex-col">
        <PageBar title={t("Settings")} />
        {searchBox}
        <div className="flex-1 overflow-y-auto px-4 pb-8 pt-2">
          <div className="mb-2 px-1 text-[12px] font-semibold uppercase tracking-wide text-muted">
            {catalogHits.length === 0
              ? t("No results found")
              : catalogHits.length === 1
                ? t("1 result")
                : t("{n} results", { n: catalogHits.length })}
          </div>
          {catalogHits.length > 0 ? (
            <MuseCard>
              {catalogHits.map((hit, index) => (
                <Fragment key={`${hit.entry.sectionId}-${hit.entry.id}`}>
                  {index > 0 && <MuseDivider />}
                  <button
                    type="button"
                    onClick={() => onOpen(hit.entry.sectionId, hit.entry.id)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-surface-2/60"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[14.5px] font-medium text-fg">
                        <HighlightedText text={hit.entry.label} query={query} />
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[12px]">
                        <span className="rounded-full bg-accent/12 px-2 py-0.5 font-medium text-accent">
                          {hit.entry.sectionTitle}
                        </span>
                        {hit.entry.description && (
                          <span className="truncate text-muted">{hit.entry.description}</span>
                        )}
                      </div>
                    </div>
                    <ChevronRight size={16} className="shrink-0 text-muted" />
                  </button>
                </Fragment>
              ))}
            </MuseCard>
          ) : (
            <div className="rounded-2xl border border-border/70 bg-surface px-4 py-4 text-[13.5px] text-muted">
              {t("Try a term like usage, theme, lock, watch, or export.")}
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <PageBar title={t("Settings")} />
      {searchBox}
      <div className="flex-1 overflow-y-auto pb-8 pt-2">
        {!appLock?.enabled && !lockPromptDismissed && (
          <div className="mx-4 mb-3 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-left text-fg">
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2 font-medium text-[13.5px] text-amber-700 dark:text-amber-300">
                <ShieldAlert size={16} /> {t("Safety Notice: App Lock Recommended")}
              </div>
              <button
                type="button"
                onClick={() => {
                  dismissLockPrompt();
                  setLockPromptDismissed(true);
                }}
                className="text-muted hover:text-fg p-0.5"
                aria-label={t("Dismiss")}
              >
                <X size={15} />
              </button>
            </div>
            <p className="mt-1 text-[12px] leading-relaxed text-muted">
              {t("MiniMuse can hold sensitive logins, stored passwords, and personal memories. Set up an App Lock (Fingerprint, Face ID, Passkey, or Password / PIN) to secure your data.")}
            </p>
            <button
              type="button"
              onClick={() => onOpen("app-lock")}
              className="mt-2.5 inline-flex items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-[12px] font-medium text-accent-fg shadow-sm active:scale-[0.98]"
            >
              <LockKeyhole size={13} /> {t("Set up App Lock")}
            </button>
          </div>
        )}

        {/* the model: where Muse's plan card stands */}
        <MuseCard className="mb-3">
          <button type="button" onClick={() => setTab("connections")} className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-surface-2/60">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[16px] font-medium leading-[21px]">{s?.llm.model || t("No model yet")}</span>
              <span className="mt-0.5 block truncate text-[12.5px] leading-4 text-muted">
                {modelSubtitle}
              </span>
            </span>
            <span className="text-[14px] font-medium text-accent">{t("Change")}</span>
          </button>
          <MuseDivider />
          <MuseRow icon={<Cloud size={22} />} label="MiniMuse Cloud" value={cloudValue} onClick={() => onOpen("cloud")} />
          <MuseDivider />
          <MuseRow
            icon={<Clapperboard size={22} />}
            label={t("Image & video models")}
            value={
              has(providers, "image") === false
                ? unavailableLine(t, "image", regionOf(hub?.account), getLocale())
                : has(providers, "video") === false
                  ? unavailableLine(t, "video", regionOf(hub?.account), getLocale())
                  : undefined
            }
            onClick={() => setTab("connections")}
          />
          <MuseDivider />
          <MuseRow icon={<BarChart3 size={22} />} label={t("Usage")} onClick={() => onOpen("usage")} />
        </MuseCard>

        {/* the agent */}
        <MuseCard className="mb-3">
          <MuseRow icon={<Sparkles size={22} />} label={t("Name & personality")} value={name} onClick={() => onOpen("who")} />
          <MuseDivider />
          <MuseRow icon={<Smile size={22} />} label={t("Avatar")} onClick={() => setTab("avatar")} />
          <MuseDivider />
          <MuseRow icon={<Brain size={22} />} label={t("Memory")} onClick={() => setTab("memory")} />
          {s?.skills?.enabled !== false && (
            <>
              <MuseDivider />
              <MuseRow icon={<Puzzle size={22} />} label={t("Skills")} value={s ? String(s.skills.count) : undefined} onClick={() => setTab("skills")} />
            </>
          )}
          <MuseDivider />
          <MuseRow icon={<Plug size={22} />} label={t("Connections")} value={s?.connectors.mcp.length ? `${s.connectors.mcp.length} MCP` : undefined} onClick={() => setTab("connections")} />
          <MuseDivider />
          <MuseRow icon={<MessageSquare size={22} />} label={t("Chat apps")} onClick={() => setTab("channels")} />
          <MuseDivider />
          <MuseRow icon={<Monitor size={22} />} label={t("Devices")} value={devicesSubtitle} onClick={() => setTab("devices")} />
          <MuseDivider />
          <MuseRow icon={<Watch size={22} />} label={t("Smartwatch (Wear OS)")} value={t("Auto-install")} onClick={() => onOpen("watch")} />
          {phone && typeof phone.scanToPair === "function" && (
            <>
              <MuseDivider />
              <MuseRow
                icon={<QrCode size={22} />}
                label={t("Scan QR code to pair computer")}
                value={typeof phone.mode === "function" && phone.mode() === "remote" ? t("Paired") : undefined}
                onClick={() => phone.scanToPair?.()}
              />
            </>
          )}
          <MuseDivider />
          <MuseRow icon={<TerminalSquare size={22} />} label={t("Coding agents")} value={coding.length ? String(coding.length) : none} onClick={() => setTab("coding")} />
        </MuseCard>

        {/* how it behaves */}
        <MuseCard className="mb-3">
          <MuseRow icon={<ShieldCheck size={22} />} label={t("Safety · Sentinel")} value={modeLabel} onClick={() => onOpen("sentinel")} />
          <MuseDivider />
          <MuseRow icon={<LockKeyhole size={22} />} label={t("App lock")} value={appLockSubtitle} onClick={() => onOpen("app-lock")} />
          <MuseDivider />
          <MuseRow icon={<KeyRound size={22} />} label={t("Passwords & logins")} value={passwordsSubtitle} onClick={() => onOpen("passwords")} />
          <MuseDivider />
          <MuseRow icon={<Shield size={22} />} label={t("Permissions")} onClick={() => onOpen("permissions")} />
          <MuseDivider />
          <MuseRow icon={<Zap size={22} />} label={t("Proactivity")} value={levelLabel} onClick={() => onOpen("proactivity")} />
          <MuseDivider />
          <MuseRow icon={<Bot size={22} />} label={t("Agent behavior")} onClick={() => onOpen("behavior")} />
          <MuseDivider />
          <MuseRow icon={<Bell size={22} />} label={t("Notifications")} onClick={() => onOpen("notifications")} />
          {keepRunningStatus() && (
            <>
              <MuseDivider />
              <MuseRow icon={<BatteryFull size={22} />} label={t("Keep it running")} onClick={() => onOpen("keep-running")} />
            </>
          )}
        </MuseCard>

        {/* the app */}
        <MuseCard className="mb-3">
          <MuseRow icon={<Palette size={22} />} label={t("Appearance")} value={appearanceSubtitle} onClick={() => onOpen("appearance")} />
          {isDesktopApp() && (
            <>
              <MuseDivider />
              <MuseRow icon={<Monitor size={22} />} label={t("Desktop app")} onClick={() => onOpen("desktop")} />
            </>
          )}
          <MuseDivider />
          <MuseRow icon={<DatabaseZap size={22} />} label={t("Data controls")} onClick={() => onOpen("data")} />
          <MuseDivider />
          <MuseRow icon={<Upload size={22} />} label={t("Import & export")} onClick={() => onOpen("import")} />
          <MuseDivider />
          <MuseRow icon={<Code2 size={22} />} label={t("Developer")} onClick={() => onOpen("developer")} />
        </MuseCard>

        {/* about */}
        <MuseCard className="mb-3">
          <MuseRow icon={<Info size={22} />} label={t("About MiniMuse")} value={release?.newer && release.latest ? t("{version} is out", { version: release.latest }) : undefined} onClick={() => onOpen("about")} />
          <MuseDivider />
          <MuseRow icon={<Hand size={22} />} label={t("Privacy policy")} onClick={() => setConfirmExternal({ label: t("Privacy policy"), url: PRIVACY_URL })} external />
          <MuseDivider />
          <MuseRow icon={<MessageSquareWarning size={22} />} label={t("Feedback")} onClick={() => setConfirmExternal({ label: t("Feedback"), url: "https://github.com/leothefleo49/MiniMuse/issues/new/choose" })} external />
        </MuseCard>
        <MuseCaption>MiniMuse {state.version}</MuseCaption>
      </div>
      <ConfirmDialog
        open={confirmExternal !== null}
        title={t("Open external page in your browser?")}
        body={t("This opens {label} ({url}) outside MiniMuse in a new browser window.", {
          label: confirmExternal?.label ?? "",
          url: confirmExternal?.url ?? "",
        })}
        confirmLabel={t("Open link")}
        onCancel={() => setConfirmExternal(null)}
        onConfirm={() => {
          const next = confirmExternal;
          setConfirmExternal(null);
          if (next) window.open(next.url, "_blank", "noopener");
        }}
      />
    </div>
  );
}

/** Inside the Android app: the app's own background connection stands in for Web Push. */
function PhoneAppSettings({ name, forceOpen = false }: { name: string; forceOpen?: boolean }) {
  const t = useT();
  const phone = androidApp();
  const [on, setOn] = useState(() => phone?.notificationsEnabled() ?? false);
  const [switchOpen, setSwitchOpen] = useState(false);
  if (!phone) return null;
  const mode = typeof phone.mode === "function" ? phone.mode() : "remote";
  const serverUrl = typeof phone.serverUrl === "function" ? phone.serverUrl() : "";
  return (
    <>
      <Toggle
        label={t("Let {name} notify this phone", { name })}
        hint={t("The app stays connected to your server in the background. Approvals, questions and finished background work arrive as notifications and open the right chat.")}
        checked={on}
        onChange={(v) => {
          phone.setNotificationsEnabled(v);
          setOn(v);
        }}
        collapsibleHint
        forceHintOpen={forceOpen}
      />
      <div className="rounded-2xl bg-surface-2/60 px-3.5 py-2.5 text-[12.5px] space-y-2">
        <div className="font-medium text-fg">
          {mode === "local" ? t("Mode: Running locally on this phone") : t("Mode: Connected to a computer or server")}
        </div>
        {mode !== "local" && serverUrl && <div className="break-all text-muted">{serverUrl}</div>}
        <div className="flex flex-wrap gap-2">
          {typeof phone.scanToPair === "function" && (
            <button
              type="button"
              onClick={() => phone.scanToPair?.()}
              className="inline-flex items-center gap-1.5 rounded-xl bg-accent px-3 py-1.5 text-[12.5px] font-medium text-accent-fg hover:opacity-95 transition"
            >
              <QrCode size={14} /> {t("Scan QR code to pair PC")}
            </button>
          )}
          <button
            type="button"
            onClick={() => setSwitchOpen(true)}
            className="rounded-xl border border-border bg-bg px-3 py-1.5 text-[12.5px] font-medium hover:bg-surface-2 transition"
          >
            {t("Switch between phone and computer / server")}
          </button>
          <SwitchModeConfirm open={switchOpen} onCancel={() => setSwitchOpen(false)} />
        </div>
      </div>
      <div className="text-[12.5px] text-muted">{t("MiniMuse for Android {version}", { version: phone.version() })}</div>
    </>
  );
}

/**
 * The Android switches that decide whether the agent keeps running with the screen off —
 * battery, overlay, exact alarms, start on boot — plus the vendor's own, and the log export.
 */
function KeepRunningSettings({ name, forceOpen = false }: { name: string; forceOpen?: boolean }) {
  const t = useT();
  const app = androidApp();
  const [st, setSt] = useState<KeepRunningStatus | null>(keepRunningStatus);
  const [confirmOpen, setConfirmOpen] = useState<string | null>(null);
  useEffect(() => {
    // the user comes back from a settings page: read again
    const again = () => setSt(keepRunningStatus());
    document.addEventListener("visibilitychange", again);
    window.addEventListener("focus", again);
    return () => {
      document.removeEventListener("visibilitychange", again);
      window.removeEventListener("focus", again);
    };
  }, []);
  if (!app || !st) return null;
  const open = (what: string) => setConfirmOpen(what);
  const openLabel = (what: string | null) =>
    what === "battery"
      ? t("Android battery optimization settings")
      : what === "overlay"
        ? t("Display over other apps settings")
        : what === "alarms"
          ? t("Alarms and reminders permission settings")
          : t("Phone auto-start settings");
  const vendorTips: Record<string, string> = {
    xiaomi: t("Xiaomi / Redmi / POCO: Settings → Apps → Manage apps → {name} → Autostart on; Battery saver → No restrictions; Other permissions → Display pop-up windows while running in the background on."),
    huawei: t("Huawei: Settings → Battery → App launch → {name} → Manage manually, all three on (auto-launch, secondary launch, run in background); Settings → Apps → {name} → Battery → Launch: manage manually."),
    honor: t("Honor: Settings → Battery → App launch → {name} → Manage manually, all three on; Battery → More battery settings → Stay connected while asleep on."),
    oppo: t("OPPO / realme / OnePlus: Settings → Battery → More settings → Optimise battery use → {name} → Don't optimise; Settings → Apps → {name} → Battery usage → Allow background activity, Allow auto launch."),
    vivo: t("vivo / iQOO: i Manager → App manager → Autostart → {name} on; Settings → Battery → Background power consumption management → {name} → Allow high background power consumption."),
    samsung: t("Samsung: Settings → Battery → Background usage limits → {name} not in Sleeping or Deep sleeping apps (add it to Never sleeping apps); turn off Adaptive battery if it keeps stopping."),
    meizu: t("Meizu: Settings → Apps → {name} → Background management → Keep running in the background; Battery → App power management → Allow."),
  };
  const tip = vendorTips[st.vendor];
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[13.5px] font-medium">{t("Background keep-alive")}</span>
        <SettingInfo
          forceOpen={forceOpen}
          text={t("Android stops apps that seem idle. {name} runs as a foreground service and holds the phone awake only while a task runs; these switches let it keep that promise on this phone.", { name })}
        />
      </div>
      <Row
        label={t("Battery")}
        value={st.battery_unrestricted ? t("Unrestricted") : t("Optimised")}
        ok={st.battery_unrestricted}
        hint={t("Optimised means Android may freeze the agent after a while with the screen off; routines and check-ins then wait until the phone wakes.")}
        forceOpen={forceOpen}
        action={st.battery_unrestricted ? undefined : { label: t("Allow"), onClick: () => open("battery") }}
      />
      <Row
        label={t("Display over other apps")}
        value={st.overlay ? t("Allowed") : t("Not allowed")}
        ok={st.overlay}
        hint={t("Lets the agent bring an app to the front from the background and show its status capsule while it works in other apps.")}
        forceOpen={forceOpen}
        action={st.overlay ? undefined : { label: t("Allow"), onClick: () => open("overlay") }}
      />
      {st.exact_alarms !== "n/a" && st.local && (
        <Row
          label={t("Alarms & reminders")}
          value={st.exact_alarms === "granted" ? t("Exact") : t("Approximate")}
          ok={st.exact_alarms === "granted"}
          hint={t("With exact alarms a reminder fires at the minute you named even when the phone is asleep; without them Android may deliver it up to a quarter of an hour late.")}
          forceOpen={forceOpen}
          action={st.exact_alarms === "granted" ? undefined : { label: t("Allow"), onClick: () => open("alarms") }}
        />
      )}
      <Toggle
        label={t("Start after a reboot")}
        hint={st.local ? t("Bring the runtime back when the phone restarts, so routines and check-ins do not stop.") : t("Reconnect to your computer when the phone restarts, so approvals keep arriving.")}
        checked={st.boot_start}
        collapsibleHint
        forceHintOpen={forceOpen}
        onChange={(v) => {
          app.setStartOnBoot?.(v);
          setSt({ ...st, boot_start: v });
        }}
      />
      {tip && (
        <div className="rounded-2xl bg-surface-2 px-3 py-2.5 text-[12.5px] leading-snug space-y-2">
          <div className="flex items-center gap-1.5 font-medium">
            <span>{t("On this phone's Android")}</span>
            <SettingInfo forceOpen={forceOpen} text={tip} />
          </div>
          {st.autostart_settings && (
            <button type="button" onClick={() => open("autostart")} className="rounded-full border border-border px-3 py-1.5 text-[12.5px]">
              {t("Open the auto-start settings")}
            </button>
          )}
        </div>
      )}
      <div className="flex items-center justify-between gap-3 pt-1">
        <div className="flex items-center gap-1.5 text-[13.5px]">
          <span>{t("Diagnostics & crash logs")}</span>
          <SettingInfo
            forceOpen={forceOpen}
            text={t("Crashes are written to a file on this phone and nowhere else. Export bundles them with the runtime's log for someone you choose.")}
          />
        </div>
        <button type="button" onClick={() => app.exportLogs?.()} className="shrink-0 rounded-full border border-border px-3 py-1.5 text-[12.5px]">
          {t("Export logs")}
        </button>
      </div>
      <ConfirmDialog
        open={confirmOpen !== null}
        title={t("Leave MiniMuse for Android settings?")}
        body={t("This opens {screen} in a system popup so you can grant MiniMuse permission to stay active in the background.", {
          screen: openLabel(confirmOpen),
        })}
        confirmLabel={t("Open settings")}
        onCancel={() => setConfirmOpen(null)}
        onConfirm={() => {
          const what = confirmOpen;
          setConfirmOpen(null);
          if (what) app.openKeepRunning?.(what);
        }}
      />
    </div>
  );
}

function Row({
  label,
  value,
  ok,
  hint,
  forceOpen = false,
  action,
}: {
  label: string;
  value: string;
  ok: boolean;
  hint?: string;
  forceOpen?: boolean;
  action?: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex items-start gap-3">
      <div className="flex-1">
        <div className="flex items-center gap-2 text-[14px]">
          <span>{label}</span>
          <span className={cx("rounded-full px-2 py-0.5 text-[11px] font-medium", ok ? "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300" : "bg-amber-500/15 text-amber-700 dark:text-amber-300")}>{value}</span>
          {hint && <SettingInfo forceOpen={forceOpen} text={hint} />}
        </div>
      </div>
      {action && (
        <button type="button" onClick={action.onClick} className="shrink-0 rounded-full bg-accent px-3 py-1.5 text-[12.5px] font-medium text-white">
          {action.label}
        </button>
      )}
    </div>
  );
}

/** Web Push: on / off, what stands in the way, and a test button. */
function PushSettings({ name, forceOpen = false }: { name: string; forceOpen?: boolean }) {
  const { toast } = useStore();
  const [info, setInfo] = useState<PushInfo | null>(null);
  const [status, setStatus] = useState<PushState>("off");
  const [busy, setBusy] = useState(false);
  const t = useT();

  const refresh = async () => {
    setStatus(await pushState());
    try {
      setInfo(await api.push());
    } catch {
      /* older server */
    }
  };
  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = async () => {
    if (!info) return;
    setBusy(true);
    try {
      const next = status === "on" ? await disablePush() : await enablePush(info.public_key);
      setStatus(next);
      if (next === "denied") toast(t("Notifications are blocked for this site in the browser settings"));
      await refresh();
    } catch (e) {
      const err = e as Error;
      toast(
        err.name === "AbortError"
          ? t("This browser has no push service (embedded browsers often don't). Use Chrome, Edge, Firefox or Safari 16.4+ on the phone.")
          : err.message,
      );
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    try {
      const r = await api.pushTest();
      if (!r.ok) {
        toast(r.error ?? t("Could not send"));
      } else if (r.failed) {
        toast(t("Sent, but {n} device(s) could not be reached", { n: r.failed }) + (r.error ? `: ${r.error}` : ""));
      } else {
        toast(r.sent && r.sent > 1 ? t("Sent to {n} devices. It should arrive in a moment", { n: r.sent }) : t("Sent. It should arrive in a moment"));
      }
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const blocked =
    status === "unsupported"
      ? t("This browser cannot receive push notifications.")
      : status === "insecure"
        ? t("Notifications need https:// (or localhost). Over plain http on your LAN the app works, this part stays off; see docs/deployment.md.")
        : status === "denied"
          ? t("Blocked for this site. Allow notifications in the browser's site settings, then try again.")
          : info && !info.available
            ? t("The server was installed without pywebpush.")
            : "";

  return (
    <div className="space-y-3">
      <Toggle
        label={t("Let {name} notify this device", { name })}
        hint={t("Approvals, questions, finished background work and check-ins; nothing while the app is on screen.")}
        checked={status === "on"}
        onChange={() => void toggle()}
        disabled={busy || !!blocked}
        collapsibleHint
        forceHintOpen={forceOpen}
      />
      {blocked && <div className="text-[12.5px] text-muted">{blocked}</div>}
      {info && info.subscriptions > 0 && (
        <div className="flex items-center justify-between text-[12.5px] text-muted">
          <span>
            {info.subscriptions === 1 ? t("1 device subscribed") : t("{n} devices subscribed", { n: info.subscriptions })}
          </span>
          <button type="button" onClick={() => void test()} className="text-accent font-medium">
            {t("Send a test")}
          </button>
        </div>
      )}
      {!isDesktopApp() && (
        <SettingInfo
          forceOpen={forceOpen}
          pillLabel={t("Mobile notification tip")}
          text={t("On a phone, add the app to the home screen first; the icon then shows a badge and notifications open the right chat.")}
        />
      )}
    </div>
  );
}

const LEVELS: Array<{ id: Proactivity; title: string; text: string }> = [
  { id: "off", title: "Off", text: "Only works when you ask." },
  { id: "low", title: "Low", text: "Checks in half as often; speaks up only when a step is done or it needs you." },
  { id: "default", title: "Default", text: "Works on goals on schedule; reports real progress, stays quiet otherwise." },
  { id: "high", title: "High", text: "Checks in twice as often and always reports, even 'still on track'." },
  { id: "auto", title: "Auto", text: "Learns from you: checks in more when you answer, and backs off when you do not. Starts like Default." },
];

export function ProactivityDial({ value, onChange, forceOpen = false }: { value: Proactivity; onChange: (v: Proactivity) => void; forceOpen?: boolean }) {
  const t = useT();
  const current = LEVELS.find((l) => l.id === value) ?? LEVELS[2];
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <span className="text-[13.5px] font-medium">{t("Level")}: {t(current.title)}</span>
        <SettingInfo forceOpen={forceOpen} text={t(current.text)} />
      </div>
      <div className="mt-2 grid grid-cols-5 gap-1 rounded-2xl bg-surface-2 p-1">
        {LEVELS.map((l) => (
          <button
            key={l.id}
            type="button"
            onClick={() => onChange(l.id)}
            aria-pressed={value === l.id}
            className={cx(
              "rounded-xl py-1.5 text-[13px] font-medium transition",
              value === l.id ? "bg-surface shadow-sm text-accent" : "text-muted",
            )}
          >
            {t(l.title)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function QuietHours({ value, onChange, forceOpen = false }: { value: string; onChange: (v: string) => void; forceOpen?: boolean }) {
  const [start, end] = value ? value.split("-") : ["", ""];
  const on = !!value;
  const t = useT();
  const set = (s: string, e: string) => onChange(s && e ? `${s}-${e}` : "");
  return (
    <div className="space-y-1.5">
      <div className="flex items-start gap-3">
        <div className="flex flex-1 items-center gap-1.5">
          <button
            type="button"
            onClick={() => set(on ? "" : "22:00", on ? "" : "08:00")}
            className="text-[14px] flex items-center gap-1.5 cursor-pointer"
          >
            <Moon size={15} className="text-muted" /> {t("Quiet hours")}
          </button>
          <SettingInfo
            forceOpen={forceOpen}
            text={t("No background work in this window; anything due waits until it ends.")}
          />
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={on}
          aria-label={t("Quiet hours")}
          onClick={() => set(on ? "" : "22:00", on ? "" : "08:00")}
          className={cx("relative mt-0.5 h-7 w-12 shrink-0 rounded-full transition", on ? "bg-accent" : "bg-surface-2 border border-border")}
        >
          <span className={cx("absolute top-0.5 h-6 w-6 rounded-full bg-white shadow transition", on ? "left-[22px]" : "left-0.5")} />
        </button>
      </div>
      {on && (
        <div className="mt-2 flex items-center gap-2 text-[13.5px]">
          <span className="text-muted">{t("From")}</span>
          <input type="time" value={start} onChange={(e) => set(e.target.value, end)} aria-label={t("Quiet hours start")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 outline-none" />
          <span className="text-muted">{t("to")}</span>
          <input type="time" value={end} onChange={(e) => set(start, e.target.value)} aria-label={t("Quiet hours end")} className="rounded-xl bg-surface-2 px-2.5 py-1.5 outline-none" />
        </div>
      )}
    </div>
  );
}

/** The Electron shell's switches (desktop/app/src/main/index.ts): start with the computer, the shortcuts. */
function DesktopAppSettings({ forceOpen = false }: { forceOpen?: boolean }) {
  const t = useT();
  const bridge = desktopBridge();
  const [openAtLogin, setOpenAtLogin] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    void bridge?.loginItem?.().then((v) => {
      if (live) setOpenAtLogin(v);
    });
    return () => {
      live = false;
    };
  }, [bridge]);
  const mac = bridge?.platform === "darwin";
  const shortcuts: Array<[string, string]> = [
    [t("Quick chat"), mac ? "⌥ Space" : "Ctrl+Shift+Space"],
    [t("Show the window"), mac ? "⌘⇧M" : "Ctrl+Shift+M"],
    [t("Stop the hands"), mac ? "⌘⇧Esc" : "Ctrl+Shift+Esc"],
  ];
  return (
    <div className="space-y-3">
      {bridge?.setLoginItem && openAtLogin !== null && bridge.platform !== "linux" && (
        <Toggle
          label={t("Start with the computer")}
          hint={t("Opens in the tray at sign-in; the agent's goals and check-ins run from then on.")}
          checked={openAtLogin}
          collapsibleHint
          forceHintOpen={forceOpen}
          onChange={(v) => {
            setOpenAtLogin(v);
            bridge.setLoginItem?.(v);
          }}
        />
      )}
      <div className="flex items-center gap-1.5 text-[13.5px]">
        <span>{t("In the tray")}</span>
        <SettingInfo
          forceOpen={forceOpen}
          text={t("The icon by the clock shows what the hands are doing and has Stop; closing the window keeps the agent running.")}
        />
      </div>
      <div className="space-y-1">
        <div className="text-[13.5px]">{t("Shortcuts")}</div>
        {shortcuts.map(([label, keys]) => (
          <div key={label} className="flex items-center justify-between text-[13px]">
            <span className="text-muted">{label}</span>
            <kbd className="rounded-md border border-border bg-surface-2 px-1.5 py-0.5 font-sans text-[12px] text-fg/80">{keys}</kbd>
          </div>
        ))}
      </div>
    </div>
  );
}

/** A settings card; `plain` is the phone's page, where the bar above already names it. */
function Section({ title, children, id, plain = false }: { title: string; children: ReactNode; id?: string; plain?: boolean }) {
  return (
    <section id={id} className={cx("space-y-3.5 scroll-mt-4", plain ? "pt-1" : "rounded-3xl bg-surface border border-border/70 shadow-sm p-5")}>
      {!plain && <h2 className="text-[13px] font-semibold uppercase tracking-wide text-muted">{title}</h2>}
      {children}
    </section>
  );
}

function SettingBlock({ id, highlightedId, className, children }: { id: string; highlightedId: string | null; className?: string; children: ReactNode }) {
  const active = highlightedId === id;
  return (
    <div
      data-setting-id={id}
      className={cx(
        "rounded-2xl transition-all",
        active && "bg-accent/6 ring-2 ring-accent/45 p-2.5 -mx-2.5",
        className,
      )}
    >
      {children}
    </div>
  );
}

function SettingInfo({
  text,
  forceOpen = false,
  pillLabel,
}: {
  text: string;
  forceOpen?: boolean;
  pillLabel?: string;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const shown = open || forceOpen;
  if (!text) return null;
  return (
    <div className={pillLabel ? "space-y-1.5" : "contents"}>
      <button
        type="button"
        aria-expanded={shown}
        aria-label={pillLabel || t("Show details")}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={cx(
          "inline-flex items-center gap-1 rounded-full transition",
          pillLabel
            ? cx("border px-2.5 py-1 text-[12px] font-medium", shown ? "border-accent/40 bg-accent/10 text-accent" : "border-border bg-surface-2/60 text-muted hover:text-fg")
            : cx("h-5 w-5 shrink-0 justify-center", shown ? "bg-accent/15 text-accent" : "text-muted hover:bg-surface-2 hover:text-fg"),
        )}
      >
        <Info size={13} />
        {pillLabel && <span>{pillLabel}</span>}
      </button>
      {shown && (
        <div
          className={cx(
            "basis-full whitespace-pre-line rounded-xl border border-border/60 bg-surface-2/65 px-3 py-2 text-[12.5px] leading-relaxed text-fg/85",
            !pillLabel && "mt-1.5",
          )}
        >
          {text}
        </div>
      )}
    </div>
  );
}

function HighlightedText({ text, query }: { text: string; query: string }) {
  const parts = splitAround(text, query);
  return (
    <>
      {parts.map((part, idx) =>
        part.hit ? (
          <mark key={idx} className="rounded bg-accent/20 px-0.5 text-fg">
            {part.text}
          </mark>
        ) : (
          <Fragment key={idx}>{part.text}</Fragment>
        ),
      )}
    </>
  );
}

/** A colour swatch with a ring that contrasts the canvas itself (like every box in the
 *  app), so it stays visible whatever the swatch colour and whatever the background: a
 *  thin ring normally, a thicker, stronger one when it is the pick, with a gap ring
 *  outside it so neighbours never touch. */
function Swatch({ label, color, selected, onClick }: { label: string; color: string; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      aria-pressed={selected}
      className="h-7 w-7 shrink-0 rounded-full transition"
      style={{
        background: color,
        boxShadow: selected
          ? "0 0 0 2.5px color-mix(in srgb, var(--om-fg) 70%, var(--om-bg) 30%), 0 0 0 4.5px color-mix(in srgb, var(--om-fg) 12%, var(--om-bg) 88%)"
          : "0 0 0 1.5px color-mix(in srgb, var(--om-fg) 35%, var(--om-bg) 65%)",
      }}
    />
  );
}

function DesktopFullscreenSetting({ highlightedId }: { highlightedId?: string | null }) {
  const t = useT();
  const fs = useDesktopFullscreen();
  return (
    <SettingBlock id="appearance-fullscreen" highlightedId={highlightedId ?? null}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 text-[13.5px]">
          <span>{t("Fullscreen mode")}</span>
          <SettingInfo
            forceOpen={highlightedId === "appearance-fullscreen"}
            text={t("Fills the entire monitor without window borders or title bar. Press F11 anytime to toggle.")}
          />
        </div>
        <Toggle
          checked={fs}
          label={t("Fullscreen mode")}
          onChange={(v) => void setDesktopFullscreen(v)}
        />
      </div>
    </SettingBlock>
  );
}

/** The UI scale: preset chips plus fine 5% step buttons, so the pick is exact and nothing moves while
 *  choosing. The page rescales when a chip or step button is tapped. */
const SCALE_STEPS = [85, 95, 100, 105, 115];

function ScaleSlider() {
  const uiScale = useUiScale();
  const currentPct = Math.round(uiScale * 100);
  const stepBy = (delta: number) => {
    const next = Math.max(80, Math.min(130, Math.round((currentPct + delta) / 5) * 5));
    setUiScale(next / 100);
  };
  return (
    <div className="flex flex-1 min-w-[220px] flex-wrap items-center justify-end gap-1">
      <button
        type="button"
        aria-label="Decrease UI scale"
        disabled={currentPct <= 80}
        onClick={() => stepBy(-5)}
        className="h-7 w-7 rounded-full bg-surface-2 text-[14px] font-semibold text-muted hover:text-fg disabled:opacity-40 transition"
      >
        −
      </button>
      {SCALE_STEPS.map((pct) => {
        const active = currentPct === pct;
        return (
          <button
            key={pct}
            type="button"
            aria-pressed={active}
            onClick={() => setUiScale(pct / 100)}
            className={cx(
              "rounded-full px-2.5 py-1.5 text-[12.5px] font-medium transition",
              active ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-fg",
            )}
          >
            {pct}%
          </button>
        );
      })}
      {!SCALE_STEPS.includes(currentPct) && (
        <span className="rounded-full bg-accent px-2.5 py-1.5 text-[12.5px] font-medium text-accent-fg">
          {currentPct}%
        </span>
      )}
      <button
        type="button"
        aria-label="Increase UI scale"
        disabled={currentPct >= 130}
        onClick={() => stepBy(5)}
        className="h-7 w-7 rounded-full bg-surface-2 text-[14px] font-semibold text-muted hover:text-fg disabled:opacity-40 transition"
      >
        +
      </button>
    </div>
  );
}

const COMPUTER_CAPS: Array<[string, string]> = [
  ["files_read", "Files: read"],
  ["files_write", "Files: write"],
  ["downloads", "Downloads"],
  ["device_settings", "Device settings"],
  ["microphone", "Microphone"],
  ["run_commands", "Run commands"],
  ["send_email", "Send email"],
  ["browse_web", "Use the web browser"],
  ["control_screen", "Control this computer's screen"],
  ["passwords", "Use saved passwords"],
  ["install_software", "Install software & system changes"],
  ["share_location", "Share location"],
  ["other_devices", "Act on your other devices"],
];

const PHONE_CAPS: Array<[string, string]> = [
  ["location", "Location"],
  ["camera", "Camera"],
  ["microphone", "Microphone"],
  ["calls", "Phone calls"],
  ["texts", "Text messages"],
  ["call_logs", "Call logs"],
  ["downloads", "Downloads"],
  ["device_settings", "Device settings"],
];

/**
 * When MiniMuse may act with or without asking, per kind of action: Always, Ask first or Never.
 * One policy for the account: the computer's rows land on the sentinel's tool lists server-side
 * (and are enforced from the first moment after a restart); the phone's ride the same settings
 * push and are read by the phone app. These are *rules*; the operating-system permissions
 * (notifications, location, microphone ...) are a different list, under Permissions.
 */
function ApprovalRules({ forceOpen = false }: { forceOpen?: boolean }) {
  const { state, toast } = useStore();
  const t = useT();
  const perms = state.settings?.permissions;
  const [busy, setBusy] = useState(false);

  const set = async (device: "computer" | "phone", cap: string, mode: string) => {
    if (busy) return;
    setBusy(true);
    const next = {
      computer: { ...(perms?.computer ?? {}) },
      phone: { ...(perms?.phone ?? {}) },
    };
    next[device][cap] = mode;
    try {
      await api.updateSettings({ permissions: next });
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const MODES = [
    ["always", t("Always")],
    ["ask", t("Ask first")],
    ["never", t("Never")],
  ] as const;
  const rows = (device: "computer" | "phone", caps: Array<[string, string]>) => (
    <MuseCard>
      {caps.map(([cap, label], i) => {
        const mode = perms?.[device]?.[cap] ?? "ask";
        return (
          <div key={cap}>
            {i > 0 && <MuseDivider inset={16} />}
            <div className="flex items-center justify-between gap-2 px-4 py-2.5">
              <span className="min-w-0 flex-1 truncate text-[14px] leading-[20px]">{t(label)}</span>
              <div className="flex shrink-0 rounded-full bg-surface-2 p-0.5" role="group" aria-label={t(label)}>
                {MODES.map(([m, mlabel]) => (
                  <button
                    key={m}
                    type="button"
                    aria-pressed={mode === m}
                    onClick={() => void set(device, cap, m)}
                    className={cx("rounded-full px-2.5 py-1 text-[12px] font-medium transition", mode === m ? "bg-fg text-bg" : "text-muted hover:text-fg")}
                  >
                    {mlabel}
                  </button>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </MuseCard>
  );
  const kind = thisDeviceKind();
  const computer = state.devices?.computer;
  // the device's own rows say "This …" only on the device itself; elsewhere each policy
  // row is named by the device's nickname and its type, the official model name stays
  // out of the way (it shows in the rename box on the Devices screen)
  const computerLabel = computer?.nickname
    ? `${computer.nickname} · ${t("computer")}`
    : kind === "computer"
      ? t("This computer")
      : t("Computer");
  const phoneLabel = kind === "phone" ? t("This phone") : kind === "tablet" ? t("This tablet") : t("Phone");
  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1.5 px-1 text-[13.5px] font-medium">{computerLabel}</p>
        {rows("computer", COMPUTER_CAPS)}
      </div>
      <div>
        <p className="mb-1.5 px-1 text-[13.5px] font-medium">{phoneLabel}</p>
        {rows("phone", PHONE_CAPS)}
      </div>
      <div className="pt-1">
        <SettingInfo
          forceOpen={forceOpen}
          pillLabel={t("How these rules work")}
          text={`${t("One policy for every device; a change here lands on the other device on its own.")}\n${t("The phone's app still asks the system the first time a capability is used; Always keeps using it, Never stops trying.")}`}
        />
      </div>
    </div>
  );
}

/**
 * The app lock: unlock with Fingerprint / Face ID / Passkey (WebAuthn platform authenticator),
 * an optional PIN code, or both. Users can enable Fingerprint / Face ID without a PIN code,
 * or disable the PIN code while keeping Fingerprint / Face ID active.
 */
function AppLockSection({ forceOpen = false }: { forceOpen?: boolean }) {
  const { toast } = useStore();
  const t = useT();
  const [view, setView] = useState<LockView | null>(null);
  const [pin, setPin] = useState("");
  const [setting, setSetting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [bioReady, setBioReady] = useState(false);
  const [webauthnReady, setWebauthnReady] = useState(false);

  useEffect(() => {
    api
      .lockView()
      .then(setView)
      .catch(() => setView({ enabled: false, biometric: false, passkey: false, pin_enabled: false }));
    const native = nativeBiometricInfo();
    const hasWebAuthn = typeof PublicKeyCredential !== "undefined" && !!navigator.credentials?.create;
    setWebauthnReady(hasWebAuthn);
    if (native.available) {
      setBioReady(true);
    } else if (hasWebAuthn && PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) {
      PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()
        .then(setBioReady)
        .catch(() => setBioReady(false));
    }
  }, []);

  const markLocalUnlocked = () => {
    try {
      localStorage.setItem("nanomuse_unlocked_at", String(Date.now()));
      sessionStorage.setItem("nanomuse_unlocked_session", "1");
    } catch {
      // ignore
    }
    window.dispatchEvent(new Event("nanomuse:lock-changed"));
  };

  const save = async (value: string, message: string, keepBiometric = false) => {
    setBusy(true);
    try {
      const v = await api.lockSet(value, keepBiometric, view?.timeout_seconds, view?.lock_on_leave);
      setView(v);
      setSetting(false);
      setPin("");
      if (!value && !keepBiometric) {
        try {
          localStorage.removeItem("nanomuse_bio_cred_id");
          localStorage.removeItem("nanomuse_passkey_cred_id");
        } catch {
          // ignore
        }
      }
      markLocalUnlocked();
      toast(message);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const registerCredential = async (kind: "biometric" | "passkey") => {
    setBusy(true);
    try {
      const native = kind === "biometric" ? nativeBiometricInfo() : null;
      if (native?.available) {
        if (!native.enrolled) {
          toast(
            native.reason ||
              t("No fingerprint or face is enrolled on this phone yet. Add one in Android Settings -> Security, then tap again."),
          );
          return;
        }
        await authenticateNativeBiometric(
          t("Set up Fingerprint / Face ID"),
          t("Confirm your fingerprint or face to turn on App Lock"),
        );
        const id = "android-native-biometric";
        try {
          localStorage.setItem("nanomuse_bio_cred_id", id);
        } catch {
          // ignore
        }
        const v = await api.lockBiometric(id, "biometric");
        setView(v);
        markLocalUnlocked();
        toast(t("Device Fingerprint / Face ID now opens the lock on this device."));
        return;
      }

      const publicKey: PublicKeyCredentialCreationOptions & { hints?: string[] } =
        kind === "biometric"
          ? {
              challenge: crypto.getRandomValues(new Uint8Array(32)),
              rp: { name: "MiniMuse" },
              user: {
                id: crypto.getRandomValues(new Uint8Array(16)),
                name: "nanomuse-device-biometric",
                displayName: "Device Fingerprint / Face ID",
              },
              pubKeyCredParams: [
                { type: "public-key", alg: -7 },
                { type: "public-key", alg: -257 },
              ],
              authenticatorSelection: {
                authenticatorAttachment: "platform",
                residentKey: "discouraged",
                requireResidentKey: false,
                userVerification: "required",
              },
              hints: ["client-device"],
              timeout: 60000,
            }
          : {
              challenge: crypto.getRandomValues(new Uint8Array(32)),
              rp: { name: "MiniMuse" },
              user: {
                id: crypto.getRandomValues(new Uint8Array(16)),
                name: "nanomuse-passkey",
                displayName: "MiniMuse Passkey",
              },
              pubKeyCredParams: [
                { type: "public-key", alg: -7 },
                { type: "public-key", alg: -257 },
              ],
              authenticatorSelection: {
                residentKey: "required",
                requireResidentKey: true,
                userVerification: "required",
              },
              timeout: 60000,
            };

      const cred = await navigator.credentials.create({ publicKey });
      if (!cred || !(cred instanceof PublicKeyCredential)) {
        throw new Error(t("The fingerprint did not register."));
      }
      const bytes = new Uint8Array(cred.rawId);
      let b64 = "";
      for (const b of bytes) b64 += String.fromCharCode(b);
      const id = btoa(b64).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
      try {
        localStorage.setItem(
          kind === "passkey" ? "nanomuse_passkey_cred_id" : "nanomuse_bio_cred_id",
          id,
        );
      } catch {
        // ignore
      }
      const v = await api.lockBiometric(id, kind);
      setView(v);
      markLocalUnlocked();
      toast(
        kind === "passkey"
          ? t("Passkey (NordPass / password manager) now opens the lock.")
          : t("Device Fingerprint / Face ID now opens the lock on this device."),
      );
    } catch (e) {
      const err = e as Error;
      if (err?.name === "NotAllowedError" || err?.name === "AbortError") {
        toast(t("Registration was cancelled."));
      } else {
        toast(err?.message || t("The fingerprint did not register."));
      }
    } finally {
      setBusy(false);
    }
  };

  const removeCredential = async (kind: "biometric" | "passkey") => {
    setBusy(true);
    try {
      try {
        localStorage.removeItem(
          kind === "passkey" ? "nanomuse_passkey_cred_id" : "nanomuse_bio_cred_id",
        );
      } catch {
        // ignore
      }
      const v = await api.lockBiometric("", kind);
      setView(v);
      markLocalUnlocked();
      toast(
        kind === "passkey"
          ? t("Passkey removed.")
          : t("Fingerprint / Face ID removed."),
      );
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const changeTimeout = async (seconds: number) => {
    setBusy(true);
    try {
      const v = await api.lockSetTimeout(seconds);
      setView(v);
      window.dispatchEvent(new Event("nanomuse:lock-changed"));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const changeLockOnLeave = async (on: boolean) => {
    setBusy(true);
    try {
      const v = await api.lockSetTimeout(view?.timeout_seconds ?? 3600, on);
      setView(v);
      window.dispatchEvent(new Event("nanomuse:lock-changed"));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (view === null) {
    return (
      <div className="flex justify-center py-3 text-muted">
        <Zap size={16} className="animate-pulse" />
      </div>
    );
  }

  const hasPin = !!view.pin_enabled;
  const hasBio = !!view.biometric;
  const hasPasskey = !!view.passkey;
  const hasAnyWebAuthn = hasBio || hasPasskey;
  const curTimeout = typeof view.timeout_seconds === "number" ? view.timeout_seconds : 3600;
  const lockOnLeave = !!view.lock_on_leave;

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2.5">
        <LockKeyhole size={17} className={cx("mt-0.5 shrink-0", view.enabled ? "text-emerald-500" : "text-muted")} />
        <div className="min-w-0 flex-1 text-[13.5px] leading-snug">
          {view.enabled
            ? hasAnyWebAuthn && !hasPin
              ? t("Protected by Fingerprint / Face ID or Passkey only (Password / PIN disabled).")
              : hasAnyWebAuthn && hasPin
                ? t("Protected by Fingerprint / Face ID or Passkey, with a backup Password / PIN.")
                : t("Protected by a Password or PIN.")
            : t("Lock the app with Device Fingerprint / Face ID, a Passkey (NordPass), or an optional Password / PIN.")}
        </div>
      </div>

      {!view.enabled && (
        <SettingInfo
          forceOpen={forceOpen}
          pillLabel={t("Safety Recommendation")}
          text={t("MiniMuse can access sensitive logins, stored passwords, and personal memories. Setting up an App Lock protects your data from unauthorized access if your device is unlocked.")}
        />
      )}

      {view.enabled && (
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
          {hasBio && (
            <span className="inline-flex items-center gap-1 rounded-lg bg-emerald-500/10 px-2 py-0.5 font-medium text-emerald-600 dark:text-emerald-400">
              <Fingerprint size={13} /> {t("Device Fingerprint / Face ID active")}
            </span>
          )}
          {hasPasskey && (
            <span className="inline-flex items-center gap-1 rounded-lg bg-accent/10 px-2 py-0.5 font-medium text-accent">
              <ShieldCheck size={13} /> {t("Passkey (NordPass) active")}
            </span>
          )}
          {hasPin && (
            <span className="inline-flex items-center gap-1 rounded-lg bg-surface-2 px-2 py-0.5 font-medium text-muted">
              <KeyRound size={13} /> {t("Password / PIN active")}
            </span>
          )}
        </div>
      )}

      {setting ? (
        <div className="flex gap-2">
          <input
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !busy && pin.trim().length >= 4) {
                void save(pin.trim(), t("The lock is on."), true);
              }
            }}
            type="password"
            autoComplete="new-password"
            placeholder={t("Password or PIN (at least 4 chars)")}
            aria-label={t("New password or PIN")}
            className="min-w-0 flex-1 rounded-xl border border-border bg-bg px-3 py-2 text-[14px] outline-none focus:ring-2 focus:ring-accent/30"
          />
          <button
            type="button"
            disabled={busy || pin.trim().length < 4}
            onClick={() => void save(pin.trim(), t("The lock is on."), true)}
            className="shrink-0 rounded-xl bg-accent px-3.5 py-2 text-[13.5px] font-medium text-accent-fg disabled:opacity-60"
          >
            {t("Save")}
          </button>
          <button type="button" onClick={() => setSetting(false)} className="shrink-0 rounded-xl px-3 py-2 text-[13.5px] text-muted hover:text-fg">
            {t("Cancel")}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {bioReady && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void registerCredential("biometric")}
              className="flex items-center gap-2 rounded-xl bg-accent px-3.5 py-2 text-[13.5px] font-medium text-accent-fg active:scale-[0.98] transition disabled:opacity-60"
            >
              <Fingerprint size={16} />
              {hasBio ? t("Update Device Fingerprint / Face ID") : t("Use Device Fingerprint / Face ID")}
            </button>
          )}
          {webauthnReady && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void registerCredential("passkey")}
              className="flex items-center gap-2 rounded-xl border border-border bg-surface-2 px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface active:scale-[0.98] transition disabled:opacity-60"
            >
              <ShieldCheck size={16} />
              {hasPasskey ? t("Update Passkey (NordPass)") : t("Use Passkey (NordPass)")}
            </button>
          )}
          <button
            type="button"
            onClick={() => setSetting(true)}
            className="rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface-2 active:scale-[0.98] transition"
          >
            {hasPin
              ? t("Change password / PIN")
              : view.enabled
                ? t("Add backup password / PIN (optional)")
                : t("Set a password or PIN")}
          </button>
          {hasPin && hasAnyWebAuthn && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void save("", t("Password / PIN disabled. Fingerprint / Passkey stays active."), true)}
              className="rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface-2 active:scale-[0.98] transition disabled:opacity-60"
            >
              {t("Disable password / PIN (Fingerprint / Passkey only)")}
            </button>
          )}
          {hasBio && (hasPin || hasPasskey) && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void removeCredential("biometric")}
              className="rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium text-muted hover:bg-surface-2 hover:text-fg transition disabled:opacity-60"
            >
              {t("Remove Fingerprint / Face ID")}
            </button>
          )}
          {hasPasskey && (hasPin || hasBio) && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void removeCredential("passkey")}
              className="rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium text-muted hover:bg-surface-2 hover:text-fg transition disabled:opacity-60"
            >
              {t("Remove Passkey")}
            </button>
          )}
          {view.enabled && (
            <>
              <button
                type="button"
                onClick={() => {
                  window.dispatchEvent(new Event("nanomuse:lock-now"));
                }}
                className="rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface-2 active:scale-[0.98] transition"
              >
                {t("Lock now")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void save("", t("The lock is off."), false)}
                className="rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium text-rose-600 hover:bg-surface-2 dark:text-rose-400 disabled:opacity-60"
              >
                {t("Turn off lock")}
              </button>
            </>
          )}
        </div>
      )}

      {view.enabled && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 pt-1 text-[12.5px] text-muted">
            <span>{t("Ask to unlock after")}</span>
            <div className="inline-flex max-w-full flex-wrap rounded-xl bg-surface-2 p-0.5">
              {(
                [
                  [0, t("Every open")],
                  [30, t("30 s")],
                  [60, t("1 min")],
                  [180, t("3 min")],
                  [300, t("5 min")],
                  [900, t("15 min")],
                  [3600, t("1 hour")],
                  [86400, t("24 hours")],
                ] as const
              ).map(([secs, label]) => (
                <button
                  key={secs}
                  type="button"
                  disabled={busy}
                  onClick={() => void changeTimeout(secs)}
                  className={cx(
                    "rounded-[10px] px-2.5 py-1 text-[12px] font-medium transition",
                    curTimeout === secs ? "bg-surface text-fg shadow-xs" : "text-muted hover:text-fg",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-2 pt-2 text-[12.5px] text-muted">
            <span>{t("Lock when I switch away")}</span>
            <button
              type="button"
              role="switch"
              aria-checked={lockOnLeave}
              disabled={busy}
              onClick={() => void changeLockOnLeave(!lockOnLeave)}
              className={cx(
                "relative h-[22px] w-[38px] shrink-0 rounded-full transition",
                lockOnLeave ? "bg-accent" : "bg-surface-2",
              )}
            >
              <span
                className={cx(
                  "absolute top-[3px] h-4 w-4 rounded-full bg-white transition-all",
                  lockOnLeave ? "left-[19px]" : "left-[3px]",
                )}
              />
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Encrypted Passwords & Logins Manager:
 * - Import CSV or JSON from Chrome, Bitwarden, 1Password, Apple Passwords, Firefox, NordPass, LastPass
 * - Sync with browser/device password manager via Credential Management API & standard autofill
 * - Syncs every saved password into the encrypted CredentialVault as {{vault:LOGIN_<ID>}} so the
 *   AI can sign in to websites via `browser(action="login")` or `passwords` tool without exposing secrets.
 */
function PasswordsSection({ forceOpen = false }: { forceOpen?: boolean }) {
  const { toast } = useStore();
  const t = useT();
  const [items, setItems] = useState<PasswordEntry[] | null>(null);
  const [search, setSearch] = useState("");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [notes, setNotes] = useState("");
  const [totpCodes, setTotpCodes] = useState<Record<string, { code: string; rem: number }>>({});
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async (q = "") => {
    try {
      const res = await api.passwords(q);
      setItems(res.items);
    } catch {
      setItems([]);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onImportFile = async (file: File) => {
    setBusy(true);
    try {
      const text = await file.text();
      const res = await api.passwordsImport(text, file.name);
      setItems(res.items);
      toast(
        t("Imported {added} new and updated {updated} logins ({total} total).", {
          added: res.added,
          updated: res.updated,
          total: res.total,
        }),
      );
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onSaveLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() && !url.trim()) return;
    setBusy(true);
    try {
      await api.passwordAdd({
        title: title.trim(),
        url: url.trim(),
        username: username.trim(),
        password,
        totp: totp.trim(),
        notes: notes.trim(),
      });
      // Offer to sync to the browser/device password manager when supported
      try {
        const W = window as unknown as { PasswordCredential?: new (data: { id: string; password: string; name?: string }) => Credential };
        if (W.PasswordCredential && navigator.credentials?.store && username.trim() && password) {
          const cred = new W.PasswordCredential({
            id: username.trim(),
            password,
            name: title.trim() || url.trim(),
          });
          await navigator.credentials.store(cred);
        }
      } catch {
        /* browser credential store optional */
      }
      setTitle("");
      setUrl("");
      setUsername("");
      setPassword("");
      setTotp("");
      setNotes("");
      setAdding(false);
      await load(search);
      toast(t("Login saved to encrypted vault."));
    } catch (err) {
      toast((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const syncFromBrowserManager = async () => {
    try {
      const nav = navigator.credentials as CredentialsContainer & {
        get: (opts?: Record<string, unknown>) => Promise<(Credential & { name?: string; password?: string }) | null>;
      };
      if (nav && typeof nav.get === "function") {
        const cred = await nav.get({ password: true, mediation: "optional" });
        if (cred && cred.id && cred.password) {
          setTitle(cred.name || "");
          setUsername(cred.id);
          setPassword(cred.password);
          setAdding(true);
          toast(t("Loaded login from your device password manager — enter the website URL and tap Save."));
          return;
        }
      }
    } catch {
      /* fallback to autofill form */
    }
    setAdding(true);
    toast(t("Tap the username or password field below and select a login from your keyboard / password manager autofill bar."));
  };

  const copyTotp = async (id: string) => {
    try {
      const res = await api.passwordTotp(id);
      setTotpCodes((prev) => ({ ...prev, [id]: { code: res.code, rem: res.remaining_seconds } }));
      await navigator.clipboard.writeText(res.code);
      toast(t("Copied 2FA code: {code} ({rem}s left)", { code: res.code, rem: res.remaining_seconds }));
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const exportCsv = async () => {
    try {
      const { blob, name } = await api.passwordsExportCsv();
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = name;
      a.click();
      URL.revokeObjectURL(href);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const filtered = (items ?? []).filter((it) => {
    const q = search.trim().toLowerCase();
    if (!q) return true;
    return [it.title, it.domain, it.url, it.username, it.notes].join(" ").toLowerCase().includes(q);
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-[13.5px] font-medium">{t("Encrypted login vault")}</span>
        <SettingInfo
          forceOpen={forceOpen}
          text={t("Import passwords from Chrome, Bitwarden, 1Password, Apple Passwords, NordPass, Firefox or LastPass (CSV / JSON), or sync from your device password manager. Logins are encrypted in your local vault so MiniMuse can sign in to websites for you.")}
        />
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".csv,.json,text/csv,application/json"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void onImportFile(f);
        }}
      />

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
          className="flex items-center gap-2 rounded-xl bg-accent px-3.5 py-2 text-[13.5px] font-medium text-accent-fg active:scale-[0.98] transition disabled:opacity-60"
        >
          <Upload size={15} />
          {t("Import Passwords (CSV / JSON)")}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void syncFromBrowserManager()}
          className="flex items-center gap-2 rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface-2 active:scale-[0.98] transition"
        >
          <KeyRound size={15} className="text-accent" />
          {t("Sync from Password Manager")}
        </button>
        <button
          type="button"
          onClick={() => setAdding((v) => !v)}
          className="flex items-center gap-2 rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface-2 active:scale-[0.98] transition"
        >
          <Plus size={15} />
          {t("Add login")}
        </button>
        {(items?.length ?? 0) > 0 && (
          <button
            type="button"
            onClick={() => void exportCsv()}
            className="flex items-center gap-2 rounded-xl border border-border px-3.5 py-2 text-[13.5px] font-medium hover:bg-surface-2 active:scale-[0.98] transition"
          >
            <Download size={15} />
            {t("Export CSV")}
          </button>
        )}
      </div>

      {adding && (
        <form onSubmit={(e) => void onSaveLogin(e)} className="space-y-2.5 rounded-2xl border border-border bg-surface-2/50 p-3.5">
          <div className="text-[13px] font-medium">{t("Save a login (supports device password manager autofill)")}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("Site name (e.g. GitHub, Google, Amazon)")}
              className="rounded-xl border border-border bg-bg px-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/30"
            />
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={t("Website URL (e.g. https://github.com/login)")}
              className="rounded-xl border border-border bg-bg px-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/30"
            />
            <input
              type="text"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder={t("Username or email")}
              className="rounded-xl border border-border bg-bg px-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/30"
            />
            <input
              type="password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("Password")}
              className="rounded-xl border border-border bg-bg px-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/30"
            />
            <input
              type="text"
              value={totp}
              onChange={(e) => setTotp(e.target.value)}
              placeholder={t("2FA TOTP secret or otpauth:// (optional)")}
              className="rounded-xl border border-border bg-bg px-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/30"
            />
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={t("Notes (optional)")}
              className="rounded-xl border border-border bg-bg px-3 py-2 text-[13.5px] outline-none focus:ring-2 focus:ring-accent/30"
            />
          </div>
          <div className="flex gap-2 pt-1">
            <button
              type="submit"
              disabled={busy || (!title.trim() && !url.trim()) || (!username.trim() && !password)}
              className="rounded-xl bg-accent px-4 py-2 text-[13.5px] font-medium text-accent-fg disabled:opacity-50"
            >
              {t("Save login")}
            </button>
            <button
              type="button"
              onClick={() => setAdding(false)}
              className="rounded-xl px-3 py-2 text-[13.5px] text-muted hover:text-fg"
            >
              {t("Cancel")}
            </button>
          </div>
        </form>
      )}

      {(items?.length ?? 0) > 3 && (
        <div className="flex items-center gap-2 rounded-xl bg-surface-2 px-3 py-2">
          <Search size={14} className="shrink-0 text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("Search saved logins…")}
            className="w-full bg-transparent text-[13.5px] outline-none placeholder:text-muted"
          />
        </div>
      )}

      {items === null ? (
        <div className="flex justify-center py-3 text-muted">
          <Zap size={16} className="animate-pulse" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-border/60 bg-surface-2/30 px-4 py-3 text-[13px] text-muted">
          {search.trim()
            ? t("No saved logins match your search.")
            : t("No saved logins yet. Import a CSV/JSON export from your password manager or add one above.")}
        </div>
      ) : (
        <MuseCard>
          {filtered.slice(0, 60).map((it, idx) => (
            <div key={it.id}>
              {idx > 0 && <MuseDivider inset={16} />}
              <div className="flex items-center justify-between gap-2 px-4 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[14px] font-medium">{it.title}</span>
                    {it.domain && <span className="truncate text-[12px] text-muted">({it.domain})</span>}
                  </div>
                  <div className="truncate text-[12.5px] text-muted">
                    {it.username || t("No username")} · <code className="text-[11px] opacity-75">{`{{vault:${it.vault_key}}}`}</code>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {it.has_totp && (
                    <button
                      type="button"
                      onClick={() => void copyTotp(it.id)}
                      className="rounded-lg bg-accent/15 px-2.5 py-1 text-[12px] font-medium text-accent hover:bg-accent/25 transition"
                      title={t("Copy 6-digit 2FA code")}
                    >
                      {totpCodes[it.id] ? `${totpCodes[it.id].code}` : t("2FA Code")}
                    </button>
                  )}
                  {it.username && (
                    <button
                      type="button"
                      onClick={() => {
                        void navigator.clipboard.writeText(it.username);
                        toast(t("Copied username"));
                      }}
                      className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-fg"
                      title={t("Copy username")}
                      aria-label={t("Copy username")}
                    >
                      <Copy size={14} />
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => {
                      void api
                        .passwordDelete(it.id)
                        .then((r) => {
                          setItems(r.items);
                          toast(t("Login removed."));
                        })
                        .catch((e) => toast((e as Error).message));
                    }}
                    className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-rose-500"
                    title={t("Delete login")}
                    aria-label={t("Delete login")}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </MuseCard>
      )}
    </div>
  );
}


