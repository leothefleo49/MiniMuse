import { CalendarDays, Download, LayoutGrid, Lightbulb, Loader2, MapPin, MessageCircle, Newspaper, RefreshCw, SquareCheckBig, WifiOff, X } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { androidApp, type AndroidAppUpdateEvent } from "./android";
import { api, setToken } from "./api";
import { desktopBridge, desktopUpdates, type DesktopUpdateState } from "./desktop";
import { AppLockPrompt, dismissLockPrompt, lockPromptDismissed } from "./components/AppLockPrompt";
import { BrandMark } from "./components/BrandMark";
import { DesktopRemoteHint } from "./components/DesktopRemoteHint";
import { ChatsDrawer } from "./components/ChatsDrawer";
import { ImportProgress } from "./components/ImportProgress";
import { PairQrListener } from "./components/PairQrListener";
import { FileViewer } from "./components/FileViewer";
import { FirstSignInSteps, useFirstSignIn } from "./components/FirstSignInSteps";
import { Sidebar } from "./components/Sidebar";
import { WhatsNewModal } from "./components/WhatsNewModal";
import type { ReleaseNotesEntry } from "./types";
import { ChatScreen } from "./screens/ChatScreen";
import { FeedScreen } from "./screens/FeedScreen";
import { SignInGate } from "./screens/SignInGate";
import { signInDoorNeeded } from "./gate";
import { watchForUpdate } from "./sw-update";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { refreshSwAndReload, waitForUpdatedServerAndReload } from "./update-reload";
import { useStore, type Tab } from "./store";
import { useT } from "./i18n";
import { initBackHandler, useClosable } from "./back";
import { useKeyboard } from "./useKeyboard";
import { LockGate, stillUnlocked, useAppLock } from "./components/LockGate";
import { accentNow, bubbleNow, setAccent, setBubble } from "./accent";
import { thisDeviceKind } from "./device";
import { bgNow, setBg, setUiScale, uiScaleNow } from "./theme";
import { pushUi } from "./ui-sync";
import { cx } from "./util";

// The chat and the feed are what opens first; every other screen arrives as its own chunk
// the first time it is shown, so the first paint does not carry the provider form or the
// coding console.
const AccountScreen = lazy(() => import("./screens/AccountScreen").then((m) => ({ default: m.AccountScreen })));
const AvatarStudioScreen = lazy(() => import("./screens/AvatarStudioScreen").then((m) => ({ default: m.AvatarStudioScreen })));
const CalendarScreen = lazy(() => import("./screens/CalendarScreen").then((m) => ({ default: m.CalendarScreen })));
const ChannelsScreen = lazy(() => import("./screens/ChannelsScreen").then((m) => ({ default: m.ChannelsScreen })));
const CodingScreen = lazy(() => import("./screens/CodingScreen").then((m) => ({ default: m.CodingScreen })));
const ConnectionsScreen = lazy(() => import("./screens/ConnectionsScreen").then((m) => ({ default: m.ConnectionsScreen })));
const DevicesScreen = lazy(() => import("./screens/DevicesScreen").then((m) => ({ default: m.DevicesScreen })));
const GoalsScreen = lazy(() => import("./screens/GoalsScreen").then((m) => ({ default: m.GoalsScreen })));
const IdeasScreen = lazy(() => import("./screens/IdeasScreen").then((m) => ({ default: m.IdeasScreen })));
const LibraryScreen = lazy(() => import("./screens/LibraryScreen").then((m) => ({ default: m.LibraryScreen })));
const LocationScreen = lazy(() => import("./screens/LocationScreen").then((m) => ({ default: m.LocationScreen })));
const MemoryScreen = lazy(() => import("./screens/MemoryScreen").then((m) => ({ default: m.MemoryScreen })));
const Onboarding = lazy(() => import("./screens/Onboarding").then((m) => ({ default: m.Onboarding })));
const SettingsScreen = lazy(() => import("./screens/SettingsScreen").then((m) => ({ default: m.SettingsScreen })));
const SkillsScreen = lazy(() => import("./screens/SkillsScreen").then((m) => ({ default: m.SkillsScreen })));
const UsageScreen = lazy(() => import("./screens/UsageScreen").then((m) => ({ default: m.UsageScreen })));

function Loading() {
  return (
    <div className="flex h-full min-h-[40vh] items-center justify-center text-muted">
      <Loader2 className="animate-spin" size={20} />
    </div>
  );
}

/** The five glyphs: outlined at rest, filled with the button's own ink when selected. The fill is the state. */
const glyph = (active: boolean) => ({ size: 26, strokeWidth: active ? 2 : 1.7, fill: active ? "currentColor" : "none", fillOpacity: active ? 0.18 : 0 });
const TABS: Array<{ id: Tab; label: string; icon: (active: boolean) => ReactNode }> = [
  { id: "chat", label: "Chat", icon: (a) => <MessageCircle {...glyph(a)} /> },
  { id: "feed", label: "Feed", icon: (a) => <Newspaper {...glyph(a)} /> },
  { id: "ideas", label: "Ideas", icon: (a) => <Lightbulb {...glyph(a)} /> },
  { id: "goals", label: "Goals", icon: (a) => <SquareCheckBig {...glyph(a)} /> },
  { id: "calendar", label: "Calendar", icon: (a) => <CalendarDays {...glyph(a)} /> },
  { id: "location", label: "Location", icon: (a) => <MapPin {...glyph(a)} /> },
  { id: "library", label: "Library", icon: (a) => <LayoutGrid {...glyph(a)} /> },
];

export default function App() {
  const { state, setTab, openFile, toast } = useStore();
  const firstSignIn = useFirstSignIn();
  const t = useT();
  const [updateReady, setUpdateReady] = useState(false);
  const [androidUpdate, setAndroidUpdate] = useState<AndroidAppUpdateEvent | null>(null);
  const [desktopUpdate, setDesktopUpdate] = useState<DesktopUpdateState | null>(null);
  const [applyingUpdate, setApplyingUpdate] = useState(false);
  const [dismissedUpdateKey, setDismissedUpdateKey] = useState<string | null>(null);
  const [whatsNew, setWhatsNew] = useState<ReleaseNotesEntry | null>(null);
  const whatsNewCheckedRef = useRef(false);
  const bootVersionRef = useRef<string | null>(null);

  useEffect(() => {
    if (!state.loaded || !state.version) return;
    if (bootVersionRef.current === null) {
      bootVersionRef.current = state.version;
      return;
    }
    if (state.version !== bootVersionRef.current) {
      void refreshSwAndReload(state.version);
    }
  }, [state.loaded, state.version]);

  useEffect(() => {
    if (!state.loaded || whatsNewCheckedRef.current) return;
    whatsNewCheckedRef.current = true;
    void api
      .update(false)
      .then((u) => {
        if (u.whats_new) setWhatsNew(u.whats_new);
      })
      .catch(() => undefined);
  }, [state.loaded]);
  // The reconnect banner waits a beat before it shows: a blink of the network must
  // not strobe "Reconnecting to your MiniMuse…" at the top of the page while the
  // socket retries every half second. Two seconds off is a real outage.
  const [reconnecting, setReconnecting] = useState(false);
  useEffect(() => {
    if (state.connected) {
      setReconnecting(false);
      return;
    }
    const timer = window.setTimeout(() => setReconnecting(true), 2000);
    return () => window.clearTimeout(timer);
  }, [state.connected]);
  // the notification's Stop now action: stop the running turn from anywhere
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  useEffect(() => {
    const onStopNow = () => void api.stopThread(stateRef.current.activeThread).catch(() => undefined);
    window.addEventListener("nanomuse:stop-now", onStopNow);
    return () => window.removeEventListener("nanomuse:stop-now", onStopNow);
  }, []);

  // the lock screen: the runtime says whether a PIN/biometric is set; a pass lasts for the configured timeout
  const lockOn = useAppLock();
  const [lockPassed, setLockPassed] = useState(false);
  const [lockPromptGone, setLockPromptGone] = useState(() => lockPromptDismissed());
  useEffect(() => {
    if (lockOn === true) {
      dismissLockPrompt();
      setLockPromptGone(true);
    }
  }, [lockOn]);
  useEffect(() => {
    const onLockNow = () => setLockPassed(false);
    window.addEventListener("nanomuse:lock-now", onLockNow);
    return () => window.removeEventListener("nanomuse:lock-now", onLockNow);
  }, []);
  const locked = lockOn === true && !lockPassed;
  // the software keyboard hides the tab bar and shrinks the viewport (useKeyboard.ts)
  useKeyboard();
  // the phone's back gesture closes what is open first, then asks before leaving (back.ts)
  useEffect(() => {
    initBackHandler();
  }, []);
  useClosable(
    "non-chat-tab",
    () => {
      setTab("chat");
      return true;
    },
    state.tab !== "chat",
  );
  useEffect(() => {
    const hint = () => toast("Tap back again to leave.");
    window.addEventListener("nanomuse:back-hint", hint);
    return () => window.removeEventListener("nanomuse:back-hint", hint);
  }, [toast]);

  // The look choices are one value for the account. A device that still holds a local
  // pick while the runtime has none pushes it up (first pick wins); once the runtime
  // has a value, every device follows it, and an explicit clear ("" ) propagates too.
  useEffect(() => {
    const ui = state.settings?.ui;
    if (ui === undefined) return;
    if (ui === null) {
      if (accentNow()) pushUi({ accent: accentNow() });
      if (bubbleNow()) pushUi({ bubble: bubbleNow() });
      if (bgNow()) pushUi({ bg: bgNow() });
      if (uiScaleNow() !== 1) pushUi({ scale: String(uiScaleNow()) });
      return;
    }
    if (typeof ui.accent === "string" && ui.accent !== accentNow()) setAccent(ui.accent);
    if (typeof ui.bubble === "string" && ui.bubble !== bubbleNow()) setBubble(ui.bubble);
    if (typeof ui.bg === "string" && ui.bg !== bgNow()) setBg(ui.bg);
    if (typeof ui.scale === "string" && Number(ui.scale) !== uiScaleNow() && Number(ui.scale) >= 0.8 && Number(ui.scale) <= 1.3) setUiScale(Number(ui.scale));
  }, [state.settings?.ui]);

  // an app that sits open for days still meets the newest build at its next return to it
  useEffect(() => {
    if (state.lite) return;
    watchForUpdate(() => setUpdateReady(true));
  }, [state.lite]);

  // Listen for Android APK & Electron desktop self-updates alongside runtime WebSocket updates
  useEffect(() => {
    const onPhoneUpdate = (ev: Event) => {
      const detail = (ev as CustomEvent<AndroidAppUpdateEvent>).detail;
      if (detail && (detail.version || detail.latest || detail.downloading || detail.downloaded)) {
        setAndroidUpdate(detail);
      }
    };
    window.addEventListener("nanomuse:app-update", onPhoneUpdate);
    const app = androidApp();
    if (typeof app?.checkAppUpdate === "function") {
      try {
        const view = JSON.parse(app.checkAppUpdate()) as AndroidAppUpdateEvent & { latest?: string | null };
        if (view.latest || view.downloading || view.downloaded) {
          setAndroidUpdate({
            ...view,
            version: view.version ?? view.latest ?? null,
          });
        }
      } catch {
        /* ignore malformed bridge payload */
      }
      app.refreshAppUpdate?.();
    }
    const du = desktopUpdates();
    let unsub: (() => void) | undefined;
    if (du) {
      void du.snapshot().then((snap) => {
        if (snap) setDesktopUpdate(snap);
      });
      unsub = du.subscribe((next) => setDesktopUpdate(next));
    }
    return () => {
      window.removeEventListener("nanomuse:app-update", onPhoneUpdate);
      unsub?.();
    };
  }, []);

  // The document title follows the agent's name.
  useEffect(() => {
    const name = state.profile?.name?.trim();
    document.title = name && name.toLowerCase() !== "nanomuse" ? `${name} · MiniMuse` : "MiniMuse";
  }, [state.profile?.name]);

  // the desktop's quick-chat shortcut: to the chat, with the composer focused
  useEffect(() => {
    const off = desktopBridge()?.onQuickChat?.(() => {
      setTab("chat");
      requestAnimationFrame(() => window.dispatchEvent(new Event("nanomuse:quick-chat")));
    });
    return () => {
      if (typeof off === "function") off();
    };
  }, [setTab]);

  // `#devices` or `#app-lock` opens that screen/section straight away.
  useEffect(() => {
    const jump = () => {
      const hash = window.location.hash.slice(1);
      if (hash && TABS.some((x) => x.id === hash)) setTab(hash as Tab);
      else if (hash === "account") {
        setTab("you");
        window.dispatchEvent(new CustomEvent("nanomuse:settings-section", { detail: "account" }));
      } else if (hash === "watch") {
        setTab("you");
        window.dispatchEvent(new CustomEvent("nanomuse:settings-section", { detail: "devices" }));
      } else if (hash === "devices" || hash === "you" || hash === "memory" || hash === "connections" || hash === "channels" || hash === "skills" || hash === "coding" || hash === "avatar") setTab(hash as Tab);
      else if (hash === "usage" || hash === "cloud" || hash === "app-lock" || hash === "passwords" || hash === "permissions" || hash === "proactivity" || hash === "behavior" || hash === "notifications" || hash === "keep-running" || hash === "model" || hash === "appearance" || hash === "desktop" || hash === "data" || hash === "import" || hash === "about" || hash === "vr" || hash === "assistant" || hash === "who" || hash === "sentinel" || hash === "developer") {
        setTab("you");
        window.dispatchEvent(new CustomEvent("nanomuse:settings-section", { detail: hash }));
      }
    };
    jump();
    window.addEventListener("hashchange", jump);
    return () => window.removeEventListener("hashchange", jump);
  }, [setTab]);

  // A freshly created account is owed two short steps (a password, which model
  // answers); they float over whatever comes next — setup or the chat.
  const firstSteps = firstSignIn && state.hub?.account.signed_in ? <FirstSignInSteps /> : null;
  const showSignInDoor =
    state.loaded &&
    state.settings?.cloud_mode !== "local" &&
    signInDoorNeeded(state.hub, state.settings, state.signInSkipped);
  const showOnboarding =
    state.loaded &&
    !showSignInDoor &&
    !state.lite &&
    !!state.settings &&
    !state.settings.onboarded &&
    !state.onboardingDismissed &&
    !state.threads.some((t) => t.events > 0);
  const lockPrompt =
    !state.lite &&
    state.loaded &&
    !locked &&
    !showSignInDoor &&
    !showOnboarding &&
    lockOn === false &&
    !lockPromptGone &&
    !firstSteps ? (
      <AppLockPrompt
        onDismiss={() => setLockPromptGone(true)}
        onSetup={() => {
          setLockPromptGone(true);
          window.location.hash = "#app-lock";
          window.dispatchEvent(new CustomEvent("nanomuse:settings-section", { detail: "app-lock" }));
          setTab("you");
        }}
      />
    ) : null;

  // Lite (inside the showcase's phone): the runtime there is set up by its host, so the
  // first-run setup has nothing to ask; mark it done and go straight to the chat.
  const liteSetup = state.lite && state.settings !== null && !state.settings.onboarded;
  useEffect(() => {
    if (liteSetup) void api.onboarded(true).catch(() => undefined);
  }, [liteSetup]);

  const handleApplyRuntime = () => {
    setApplyingUpdate(true);
    const prevVersion = state.version;
    void api
      .applyUpdate()
      .then(async (res) => {
        if (!res.ok) {
          setApplyingUpdate(false);
          toast(res.error ?? t("Could not update"));
          return;
        }
        const err = await waitForUpdatedServerAndReload(prevVersion);
        if (err) {
          setApplyingUpdate(false);
          toast(err);
        }
      })
      .catch((e) => {
        setApplyingUpdate(false);
        toast((e as Error).message);
      });
  };

  if (state.authError) return <TokenGate />;
  // while checking whether the app lock is enabled on a fresh open, wait for the answer so unlocked content never flashes
  if (lockOn === null && !stillUnlocked()) return <Connecting error={state.error} />;
  // the lock screen sits over everything once the page can talk to the runtime
  if (locked) {
    return (
      <>
        <LockGate
          onUnlock={() => {
            setLockPassed(true);
            window.dispatchEvent(new Event("nanomuse:lock-changed"));
          }}
        />
        <AppUpdateBanner
          updateReady={updateReady}
          runtimeUpdate={state.appUpdate}
          androidUpdate={androidUpdate}
          desktopUpdate={desktopUpdate}
          applying={applyingUpdate}
          dismissedKey={dismissedUpdateKey}
          onDismiss={setDismissedUpdateKey}
          onApplyRuntime={handleApplyRuntime}
          bottomClass="bottom-[calc(16px+var(--nm-safe-bottom))]"
        />
      </>
    );
  }
  // Nothing has arrived from the runtime yet: say so, instead of an empty chat.
  if (!state.loaded) return <Connecting error={state.error} />;
  // A home-screen shortcut lands here: one workspace file — an app the agent built — alone
  // on the page, with no chrome around it. (?view=file&path=…, written by shortcut pinning)
  const standaloneParams = new URLSearchParams(window.location.search);
  const standaloneFile = standaloneParams.get("view") === "file" ? standaloneParams.get("path") : null;
  if (standaloneFile) {
    return (
      <ErrorBoundary scope="file-viewer">
        <Suspense fallback={<Loading />}>
          <FileViewer
            path={standaloneFile}
            onClose={() => {
              if (window.history.length > 1) window.history.back();
              else window.close();
            }}
          />
        </Suspense>
      </ErrorBoundary>
    );
  }
  // The account comes first when nothing else could answer: this runtime asks for one
  // (cloud.required), none is signed in, no model of one's own is ready, and the person has not
  // stepped past the door for a key of their own. The sign-in stays an invitation under Connections.
  if (showSignInDoor) {
    return (
      <ErrorBoundary scope="sign-in">
        <SignInGate />
      </ErrorBoundary>
    );
  }
  // First run: the server has not seen setup finish and nothing has been said yet.
  if (showOnboarding) {
    return (
      <ErrorBoundary scope="onboarding">
        <Suspense fallback={<Loading />}>
          <Onboarding />
          {firstSteps}
        </Suspense>
      </ErrorBoundary>
    );
  }

  const pendingApprovals = state.pendingApprovals.length;
  // a plan change waiting for your answer is worth a red badge; the count of goals is not
  const proposals = state.goals.filter((g) => g.proposal && g.status !== "cancelled").length;
  const feedUnseen = state.pendingApprovals.filter((a) => a.ts > state.feedSeenAt).length;
  // the disconnect lines name both halves: the computer the runtime runs on and the
  // device this page is on — they are the same machine only in the browser on the PC
  const onPhone = thisDeviceKind() !== "computer";

  // One column on the phone; on a wide screen (a full browser) and in the desktop app at any
  // width, a sidebar takes over from the tab bar and the chats sheet — same screens either side.
  return (
    <div className={cx("mx-auto flex h-full max-w-[760px] flex-col bg-bg sm:border-x sm:border-border", !state.lite && "wide:max-w-none wide:flex-row wide:border-x-0")}>
      {!state.lite && (
        <div className="hidden wide:block wide:h-full">
          <Sidebar />
        </div>
      )}
      <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
        {state.gone && (
          <div className="flex items-center justify-center gap-2 bg-surface-2 text-muted text-[12.5px] py-1">
            <WifiOff size={14} /> {t(state.lite ? "This demo has ended." : onPhone ? "MiniMuse has stopped on your computer. Start it there, then reload this page." : "This MiniMuse has stopped. Reload the page once it is running again.")}
          </div>
        )}
        {!state.gone && !state.connected && state.loaded && reconnecting && (
          <div className="flex items-center justify-center gap-2 bg-amber-500/15 text-amber-700 dark:text-amber-300 text-[12.5px] py-1">
            <WifiOff size={14} /> {t(onPhone ? "Reconnecting to your computer…" : "Reconnecting to your MiniMuse…")}
          </div>
        )}
        {!state.lite && <DesktopRemoteHint />}
        {/* `tabbar-room` keeps the screens, the chat's composer included, above the floating bar;
            a wide window has the sidebar instead and gives the room back. Nothing is capped
            here any more: a 16:9 monitor and an ultrawide fill with the app, and each screen
            decides its own readable width (the chat centres its conversation, settings go
            two-column, lists use their cards' own grids). */}
        <main
          className={cx(
            "tabbar-room mx-auto min-h-0 w-full flex-1 wide:max-w-none wide:px-6 2xl:px-10",
            !state.lite && "wide:pb-0",
          )}
        >
          <ErrorBoundary scope="main-tab">
            <Suspense fallback={<Loading />}>
              {state.tab === "chat" && <ChatScreen />}
              {state.tab === "feed" && <FeedScreen />}
              {state.tab === "ideas" && <IdeasScreen />}
              {state.tab === "goals" && <GoalsScreen />}
              {state.tab === "calendar" && <CalendarScreen />}
              {state.tab === "location" && <LocationScreen />}
              {state.tab === "library" && <LibraryScreen />}
              {state.tab === "memory" && <MemoryScreen />}
              {state.tab === "skills" && <SkillsScreen />}
              {state.tab === "connections" && <ConnectionsScreen />}
              {state.tab === "channels" && <ChannelsScreen />}
              {state.tab === "devices" && <DevicesScreen />}
              {state.tab === "you" && <SettingsScreen />}
              {state.tab === "account" && <AccountScreen />}
              {state.tab === "usage" && <UsageScreen />}
              {state.tab === "coding" && <CodingScreen />}
              {state.tab === "avatar" && <AvatarStudioScreen />}
            </Suspense>
          </ErrorBoundary>
        </main>
        {/* Muse's bottom bar, as the Android app draws it: a floating frosted-glass pill, five glyphs,
            icons only. The pill sits 6px above the bottom edge, blurs what is behind it and carries
            a hairline edge; the current tab shows its filled glyph on an ink indicator (black in the
            light theme, white in the dark one, the glyph in the opposite colour), the others stay
            outlined in the muted grey. No labels: the fill and the indicator are the
            state. The pill is as wide as the chat's composer above it (the same 12px from the sides,
            `px-3` on the Composer's form), 420px at most and centred on a wider screen; the five
            slots share it equally and the indicator disc keeps its 60 by 46 inside the slot. In lite
            (the phone in the showcase) it shows whatever the width; otherwise a wide window has the
            sidebar instead. The nav itself lets taps through to the screen; only the pill takes them. */}
        <nav className={cx("tabbar-float pointer-events-none absolute inset-x-0 z-20 flex justify-center px-3", !state.lite && "wide:hidden")}>
          {(() => {
            const visibleTabs = TABS.filter(
              (tb) =>
                (tb.id !== "calendar" || state.settings?.ui?.show_calendar !== "false") &&
                (tb.id !== "location" || state.settings?.ui?.show_location !== "false"),
            );
            return (
              <ul
                className={cx(
                  "pointer-events-auto flex w-full items-center gap-0.5 rounded-full bg-surface-2/55 py-1.5 ring-1 ring-border/50 backdrop-blur-2xl",
                  visibleTabs.length > 5 ? "max-w-[500px] px-2.5" : "max-w-[420px] px-3.5",
                )}
              >
                {visibleTabs.map((tab) => {
                  const active = state.tab === tab.id;
                  const badge = tab.id === "chat" ? pendingApprovals : tab.id === "feed" ? feedUnseen : tab.id === "goals" ? proposals : 0;
                  return (
                    <li key={tab.id} className="min-w-0 flex-1">
                      <button
                        type="button"
                        onClick={() => setTab(tab.id)}
                        aria-label={t(tab.label)}
                        aria-current={active ? "page" : undefined}
                        className={cx("flex h-[46px] w-full items-center justify-center", active ? "text-ink-fg" : "text-muted hover:text-fg")}
                      >
                        <span className={cx("relative flex h-[46px] w-full max-w-[60px] items-center justify-center rounded-full transition-colors duration-300", active && "bg-ink")}>
                          {tab.icon(active)}
                          {badge > 0 && (
                            <span className="absolute right-2.5 top-1 flex h-[17px] min-w-[17px] items-center justify-center rounded-full bg-rose-500 px-1 text-[10px] font-bold text-white">
                              {badge}
                            </span>
                          )}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            );
          })()}
        </nav>
      </div>
      <ChatsDrawer />
      <ImportProgress />
      <PairQrListener />
      <FileViewer path={state.viewer} onClose={() => openFile(null)} />
      {firstSteps}
      {lockPrompt}
      {!showOnboarding && !showSignInDoor && !firstSteps && !locked && (
        <WhatsNewModal notes={whatsNew} onClose={() => setWhatsNew(null)} />
      )}
      <AppUpdateBanner
        updateReady={updateReady}
        runtimeUpdate={state.appUpdate}
        androidUpdate={androidUpdate}
        desktopUpdate={desktopUpdate}
        applying={applyingUpdate}
        dismissedKey={dismissedUpdateKey}
        onDismiss={setDismissedUpdateKey}
        onApplyRuntime={handleApplyRuntime}
        bottomClass="bottom-[calc(6rem+var(--nm-safe-bottom))]"
      />
      {state.toast && (
        <div className="pointer-events-none fixed inset-x-0 bottom-[calc(6rem+var(--nm-safe-bottom))] z-[70] flex justify-center px-4">
          <div role="status" className="rise rounded-2xl bg-fg text-bg px-4 py-2 text-[13.5px] shadow-lg max-w-sm text-center">{state.toast}</div>
        </div>
      )}
    </div>
  );
}

function AppUpdateBanner({
  updateReady,
  runtimeUpdate,
  androidUpdate,
  desktopUpdate,
  applying,
  dismissedKey,
  onDismiss,
  onApplyRuntime,
  bottomClass,
}: {
  updateReady: boolean;
  runtimeUpdate: ReturnType<typeof useStore>["state"]["appUpdate"];
  androidUpdate: AndroidAppUpdateEvent | null;
  desktopUpdate: DesktopUpdateState | null;
  applying: boolean;
  dismissedKey: string | null;
  onDismiss: (key: string) => void;
  onApplyRuntime: () => void;
  bottomClass: string;
}) {
  const t = useT();

  let key = "";
  let label = "";
  let action: (() => void) | null = null;
  let busy = false;
  let dismissible = true;

  if (runtimeUpdate?.status === "restarting") {
    key = `rt-restart-${runtimeUpdate.latest ?? ""}`;
    label = t("Installed {version}. Restarting…", { version: runtimeUpdate.latest ?? "" });
    busy = true;
    dismissible = false;
  } else if (runtimeUpdate?.status === "applying" || applying) {
    key = `rt-apply-${runtimeUpdate?.latest ?? ""}`;
    label = t("Installing update {version}…", { version: runtimeUpdate?.latest ?? "" });
    busy = true;
    dismissible = false;
  } else if (runtimeUpdate?.status === "available" && runtimeUpdate.latest && runtimeUpdate.auto_supported !== false) {
    key = `rt-avail-${runtimeUpdate.latest}`;
    label = t("MiniMuse {version} is out · Update now", { version: runtimeUpdate.latest });
    action = onApplyRuntime;
  } else if (desktopUpdate?.phase === "ready") {
    key = `desk-ready-${desktopUpdate.version ?? ""}`;
    label = t("Update {version} is ready · Restart to install", { version: desktopUpdate.version ?? "" });
    action = () => void desktopUpdates()?.restart();
  } else if (androidUpdate?.downloading) {
    const ver = androidUpdate.downloadingVersion || androidUpdate.version || androidUpdate.latest || "";
    const pct = typeof androidUpdate.progress === "number" && androidUpdate.progress > 0 ? ` ${androidUpdate.progress}%` : "";
    key = `apk-dl-${ver}`;
    label = t("Downloading MiniMuse {version}…{pct}", { version: ver, pct });
    busy = true;
    dismissible = false;
  } else if (androidUpdate?.version && (androidUpdate.shellRequired || androidUpdate.downloaded)) {
    key = `apk-${androidUpdate.version}`;
    label = androidUpdate.downloaded
      ? t("MiniMuse {version} downloaded · Tap to install", { version: androidUpdate.version })
      : t("MiniMuse shell {version} is ready · Tap to install", { version: androidUpdate.version });
    action = () => androidApp()?.installAppUpdate?.();
  } else if (updateReady) {
    key = "sw-ready";
    label = t("A new build of the app is ready. Tap to reload.");
    action = () => window.location.reload();
  } else if (runtimeUpdate?.status === "error" && runtimeUpdate.error) {
    key = `rt-err-${runtimeUpdate.error}`;
    label = t("Auto-update failed: {error}", { error: runtimeUpdate.error });
  }

  if (!key || dismissedKey === key) return null;

  return (
    <div className={cx("pointer-events-none fixed inset-x-0 z-[70] flex justify-center px-4", bottomClass)}>
      <div className="rise pointer-events-auto flex max-w-md items-center gap-2 rounded-full bg-fg px-4 py-2 text-[13px] font-medium text-bg shadow-lg">
        <button
          type="button"
          onClick={() => action?.()}
          disabled={!action || busy}
          className="flex min-w-0 items-center gap-2 text-left disabled:cursor-default"
        >
          {busy ? <Loader2 size={14} className="shrink-0 animate-spin" /> : action === onApplyRuntime || androidUpdate?.version ? <Download size={14} className="shrink-0" /> : <RefreshCw size={14} className="shrink-0" />}
          <span className="truncate">{label}</span>
        </button>
        {dismissible && (
          <button
            type="button"
            onClick={() => onDismiss(key)}
            aria-label={t("Dismiss")}
            className="ml-1 shrink-0 rounded-full p-0.5 opacity-75 hover:opacity-100"
          >
            <X size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

/** The first seconds: the socket is on its way, or the runtime cannot be reached at all. */
function Connecting({ error }: { error: string | null }) {
  const t = useT();
  const kind = thisDeviceKind();
  // the explanation names the two halves: the computer the runtime runs on, and the
  // device this page is on — they are the same machine only in the browser on the PC
  const where = kind === "computer" ? t("The MiniMuse server runs on this computer. Start it with minimuse serve, then try again.") : t("The MiniMuse server runs on your computer. Check it is on, and that both are on the same network.");
  return (
    <div className="mx-auto flex h-full max-w-sm flex-col items-center justify-center px-6 text-center">
      {/* the web's splash: the app's mark, no face (the face is the Muse, who is not here yet) */}
      <BrandMark size={72} />
      {error ? (
        <>
          <p className="mt-6 text-[15px] font-medium">{t("Could not reach your MiniMuse.")}</p>
          <p className="mt-1.5 text-[13px] text-muted">{t(error)}</p>
          <p className="mt-2 text-[13px] text-muted">{where}</p>
          <button type="button" onClick={() => window.location.reload()} className="mt-5 rounded-full bg-fg px-5 py-2 text-[14px] font-medium text-bg">
            {t("Try again")}
          </button>
        </>
      ) : (
        <p className="mt-6 flex items-center gap-2 text-[14px] text-muted">
          <Loader2 className="animate-spin" size={16} /> {t("Connecting to your MiniMuse…")}
        </p>
      )}
    </div>
  );
}

function TokenGate() {
  const [value, setValue] = useState("");
  const t = useT();
  return (
    <div className="mx-auto flex h-full max-w-md flex-col items-center justify-center px-6 text-center">
      <BrandMark size={88} />
      <h1 className="mt-4 text-[22px] font-bold">{t("Connect to your MiniMuse")}</h1>
      <p className="mt-2 text-[14px] text-muted">
        {t("This app talks to the MiniMuse server you run yourself. Scan the QR code printed by")}{" "}
        <code className="rounded bg-surface-2 px-1">minimuse serve</code>{t(", or paste the access token below.")}
      </p>
      <form
        className="mt-5 w-full flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!value.trim()) return;
          setToken(value.trim());
          window.location.reload();
        }}
      >
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label={t("Access token")}
          placeholder={t("Access token")}
          className="flex-1 rounded-2xl bg-surface-2 px-4 py-2.5 text-[15px] outline-none focus:ring-2 focus:ring-accent/40"
        />
        <button type="submit" className="rounded-2xl bg-accent text-accent-fg px-4 font-medium">
          {t("Connect")}
        </button>
      </form>
    </div>
  );
}
