import { ArrowUp, Camera, Check, ChevronDown, Clock3, Copy, FileText, Globe, Hand, Loader2, Menu, MessageSquarePlus, Monitor, MonitorSmartphone, Moon, MoreHorizontal, Pencil, Phone, Plus, Reply, Smile, Smartphone, Square, Table2, Trash2, Type, Wand2, X } from "lucide-react";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, fileUrl } from "../api";
import { AllowanceHeadsUp } from "../components/AllowanceWays";
import { cloudRetryTarget } from "../cloud-retry";
import { ActivityStrip } from "../components/ActivityStrip";
import { AvatarOptionsCard } from "../components/AvatarOptionsCard";
import { BrowserViewer } from "../components/BrowserViewer";
import { MicButton, useDictation } from "../components/Dictation";
import { ApprovalCard, ArtifactCard, BrowserCard, HandsCard, HoldCard, ModelSwitchCard, Notice, QuestionCard, ToolChip } from "../components/Cards";
import { IntroLines, NamingCard } from "../components/FirstConversation";
import { Markdown, splitBlocks } from "../components/Markdown";
import { ModelMenu, readModelChoice } from "../components/ModelMenu";
import { useClosable } from "../back";
import { MuseHeader, MuseRoundButton } from "../components/MuseHeader";
import { longPressHandlers, ThreadActionsSheet, type ThreadSheetMode } from "../components/ThreadActionsSheet";
import { MoreMenu } from "../components/TabHeader";
import { stripNamingFence } from "../fences";
import { localDetail, localLabel, useLocale, useT } from "../i18n";
import { mentionSuggestions, mentionTarget } from "../mention";
import { WORKING_TTL_MS } from "../presence";
import { DRAFT_THREAD, useStore, type Stream } from "../store";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { LiveCallModal } from "../components/LiveCallModal";
import { TrashModal } from "../components/TrashModal";
import type { AttachmentInfo, BrowserEvent, HubDevice, SkillInfo, Status, ThreadMeta, TimelineEvent, UserEvent, WorkingPresence } from "../types";
import { cx, timeDivider, timeShort } from "../util";
import { MuseSheet } from "./MuseSheet";
import { countDay, countTask, StarNudgeOnce } from "../components/StarNudge";
import { noteStopped, StopAskCard, Thumbs, useStopAsk } from "../components/Feedback";
import { useShowSteps } from "../steps";

export function ChatScreen() {
  const { state, send, sendViaCloud, decide, loadEvents, openFile, deleteEvent, openThread, toast, setDrawer, setTab } = useStore();
  const t = useT();
  const [replyQuote, setReplyQuote] = useState<string | null>(null);
  const [pendingDeleteEvent, setPendingDeleteEvent] = useState<string | null>(null);
  const { profile, status, activeThread, threads } = state;
  // undefined until the first fetch for this thread has returned — don't flash the empty state
  const loaded = state.events[activeThread];
  const thread = threads.find((t) => t.id === activeThread);
  // a tool step left "running" by a turn that ended (an interruption, a crash, a restart)
  // reads as finished once the thread is idle, so its chip cannot claim work forever
  const idle = status.state === "idle" && !thread?.busy;
  const events = useMemo(
    () =>
      loaded?.map((e) =>
        e.type === "tool" && e.status === "running" && idle ? { ...e, status: "ok" as const, output: e.output || "interrupted" } : e,
      ) ?? [],
    [loaded, idle],
  );
  const eventsLoaded = activeThread === DRAFT_THREAD || loaded !== undefined;
  const stream = state.streams[activeThread];
  const [activityOpen, setActivityOpen] = useState(false);
  const [callOpen, setCallOpen] = useState(false);
  // the browser card being watched (or driven) full-screen
  const [browserView, setBrowserView] = useState<string | null>(null);
  const name = profile?.name ?? "MiniMuse";
  // a chat addressed to another device: is that device still on the hub?
  const deviceOnline = thread?.device ? (state.hub?.devices.find((d) => d.id === thread.device)?.online ?? null) : null;
  // another device of the account is working on this chat (C9): the quiet line under its message
  const working = useLiveWorking(state.working[activeThread]);

  const listRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);
  // the conversation passes under the header: the top edge fades into the canvas
  const [scrolledUp, setScrolledUp] = useState(false);

  // The header lies over the list as a scrim rather than above it, so the conversation scrolls
  // under the face and dissolves. The list keeps the header's height clear at its top, measured
  // here because the header grows with the chip under the name tag and the safe area.
  const headerRef = useRef<HTMLDivElement>(null);
  const [headerHeight, setHeaderHeight] = useState(0);
  const headerHeightRef = useRef(0);
  useLayoutEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    setHeaderHeight(el.offsetHeight);
    headerHeightRef.current = el.offsetHeight;
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      setHeaderHeight(el.offsetHeight);
      headerHeightRef.current = el.offsetHeight;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // The first conversation (contract C4), when it is bound to this chat: the runtime's state,
  // the opening lines it speaks, the chooser. `fr` is null on an older runtime.
  const fr = state.firstrun;
  const bound = !!fr && fr.phase !== "none" && fr.session_id === activeThread;
  const firstRunning = bound && fr.running;
  const locale = useLocale();
  const lang = locale === "zh-CN" ? "zh" : "en";
  const [intro, setIntro] = useState<{ lang: string; lines: string[] } | null>(null);
  useEffect(() => {
    if (!bound || intro?.lang === lang) return;
    let live = true;
    api
      .firstrun(lang)
      .then((v) => live && v.intro && setIntro({ lang, lines: v.intro }))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [bound, lang, intro?.lang]);
  const pickName = (picked: string) => {
    api
      .firstrunPick(picked)
      .then(() => send(activeThread, picked).then(() => jumpToBottom()))
      .catch((e: Error) => toast(e.message || t("Could not send")));
  };
  // the stop the activity sheet offers, from the composer: end the run in progress and
  // drop what was queued behind it; ok:false means the race went to the run finishing
  const stopActive = async () => {
    try {
      const r = await api.stopThread(activeThread);
      if (!r.ok) toast(t("Nothing to stop"));
      else noteStopped(activeThread); // one question, why, where the chat's cards go; Skip is an answer
    } catch (e) {
      toast((e as Error).message);
    }
  };
  const stopAsk = useStopAsk(activeThread);
  // The newest finished reply, the one the thumbs go under (a reply voted on earlier keeps
  // its mark): the last bubble with words, with nothing of the person's after it, once the
  // run has ended. A quiet background line is not a reply.
  const newestReplyId = useMemo(() => {
    if (thread?.busy || stream) return null;
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "assistant") return e.text && !e.quiet ? e.id : null;
      if (e.type === "user") return null;
    }
    return null;
  }, [events, thread?.busy, stream]);

  // A task this browser saw through (contract C1): busy → idle, the turn started by the person
  // here (a `user` bubble, not a background notice or another device's ask) and ended with a
  // reply. The first conversation never counts: a turn that began while it was running is
  // its, whatever the phase says by the time the reply lands. The count reached is the
  // moment for a word about a star when the policy names it.
  const [taskCount, setTaskCount] = useState<number | null>(null);
  const onboarded = state.settings?.onboarded === true;
  const sawBusy = useRef(false);
  const firstRunAtStart = useRef(false);
  useEffect(() => {
    if (thread?.busy) {
      if (!sawBusy.current) firstRunAtStart.current = firstRunning;
      sawBusy.current = true;
      return;
    }
    if (!sawBusy.current) return;
    sawBusy.current = false;
    if (onboarded && !firstRunAtStart.current && !firstRunning && personStartedTurn(events)) setTaskCount(countTask());
  }, [thread?.busy, events, onboarded, firstRunning]);
  // the app was opened today: the 7th and the 30th day are moments too
  const [dayCount, setDayCount] = useState<number | null>(null);
  useEffect(() => {
    const { days, fresh } = countDay();
    if (fresh) setDayCount(days);
  }, []);

  // Pinned to the bottom while the person is there (within 120px of it); the *Latest* pill
  // only once they have scrolled more than 400px away. The pin is redone whenever the list
  // or its content changes size (a bubble finding its height, an image or a code block
  // arriving after the render, the window resized): the first version pinned once per
  // event and left the gap that opened afterwards, so the view sat short of the bottom and
  // the pill showed with nobody having scrolled.
  const measure = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottom.current = gap < 120;
    setShowJump(gap > 400);
    // The fade is about the top edge: how far the eye is from the latest message is the
    // gap's business, but the gap lies while the keyboard has shrunk the viewport (a
    // pinned view still shows a gap then). Distance from the top of the list does not.
    setScrolledUp(el.scrollTop > 8);
  }, []);

  const pin = useCallback(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    setShowJump(false);
    measure();
  }, [measure]);

  // a chat opens at its latest message whatever the previous chat's position was; the pill
  // of the previous chat never carries over
  const shownThread = useRef<string | null>(null);
  useLayoutEffect(() => {
    if (shownThread.current !== activeThread) {
      shownThread.current = activeThread;
      stickToBottom.current = true;
    }
    if (stickToBottom.current) pin();
  }, [events, stream?.text, activeThread, pin]);

  useEffect(() => {
    const el = listRef.current;
    const content = contentRef.current;
    if (!el || !content || typeof ResizeObserver === "undefined") return;
    let raf = 0;
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (stickToBottom.current) pin();
        else measure();
      });
    };
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    ro.observe(content);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [pin, measure]);

  const handleDecideEvent = useCallback(
    (id: string, approved: boolean, scope: string) => {
      void decide(id, approved, scope).catch((e: Error) => toast(e.message || t("Could not send decision")));
    },
    [decide, toast, t],
  );
  const handleDeleteEvent = useCallback((id: string) => setPendingDeleteEvent(id), []);

  const jumpToBottom = () => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  };

  // back to a step's card in the timeline, from the activity strip above the composer
  const jumpToEvent = (id: string) => {
    const el = listRef.current?.querySelector(`[data-event-id="${CSS.escape(id)}"]`);
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  };

  const queued = state.pendingApprovals.length;
  const waitingHere = state.pendingApprovals.filter((a) => a.thread === activeThread).length;
  // The status under the name is about this chat; other chats show on the avatar badge.
  const statusLine = useMemo(() => {
    const here = !status.thread || status.thread === activeThread;
    if (waitingHere > 0 && !(here && status.state === "working")) {
      return waitingHere > 1 ? t("{n} approvals waiting for you", { n: waitingHere }) : t("1 approval waiting for you");
    }
    // At rest the line under the name is the tagline the person wrote in setup (Muse's "Your day,
    // sorted."), or nothing when there is none; while it works the line says what it is doing.
    const tagline = profile?.tagline?.trim() || undefined;
    if (!here) return thread?.busy ? t("On it") : tagline;
    if (status.state === "idle" && !thread?.busy) return tagline;
    if (status.detail) return status.detail;
    if (status.state === "waiting") return t("Waiting for you");
    // The line names the step running right now, or says we are thinking: the person's
    // own words are never echoed back under the name (that read as "blah blah blah").
    const running = runningStepTitle(events);
    if (running) return t("On it: {request}", { request: briefCut(running, 48) });
    return t("Thinking");
  }, [status, thread, activeThread, waitingHere, events, profile?.tagline, t]);

  const pendingApprovals = events.filter((e) => e.type === "approval" && e.status === "pending").length;
  // the steps (tool chips) stay out of the chat unless asked for; everything else always shows
  const steps = useShowSteps();
  const shown = useMemo(() => (steps ? events : events.filter((e) => e.type !== "tool")), [events, steps]);
  // *Use MiniMuse Cloud this time* under a failed turn of one's own model (see cloud-retry.ts)
  const cloudRetryCtx = { signedIn: Boolean(state.hub?.account.signed_in), chatOnCloud: Boolean(state.settings?.llm.cloud), deviceChat: Boolean(thread?.device) };
  const cloudRetryFor = (i: number): (() => Promise<void>) | undefined => {
    const target = cloudRetryTarget(shown, i, cloudRetryCtx);
    return target ? () => sendViaCloud(activeThread, target.text, target.files) : undefined;
  };
  // files made in this chat: a reply that names one ("saved to `plan.md`") opens it on tap
  const files = useMemo(
    () => Array.from(new Set(events.flatMap((e) => (e.type === "artifact" ? [e.path] : [])))),
    [events],
  );

  return (
    <div className="relative flex h-full flex-col">
      {/* Muse's header: the face on its disc, the name tag with what it is doing, the round hamburger
          and menu. A scrim over the list, which scrolls under it; no bar and no line below it. */}
      <div ref={headerRef} className="pointer-events-none absolute inset-x-0 top-0 z-10">
        <MuseHeader
          scrim
          profile={profile}
          status={status}
          name={name}
          statusLine={statusLine}
          statusTone={status.state === "idle" && !thread?.busy ? "muted" : "accent"}
          spinning={((status.state === "working" && status.thread === activeThread) || !!thread?.busy) && waitingHere === 0}
          badge={queued}
          onAvatar={() => setActivityOpen(true)}
          leading={
            <MuseRoundButton onClick={() => setDrawer(true)} label={t("Chats")} dot={threads.length > 1 && activeThread !== "main"} className="wide:invisible">
              <Menu size={22} />
            </MuseRoundButton>
          }
          trailing={
            <div className="flex items-center gap-1.5">
              <MuseRoundButton onClick={() => setCallOpen(true)} label={t("Live Call")}>
                <Phone size={18} />
              </MuseRoundButton>
              <MoreMenu
                actions={[
                  { label: t("New chat"), onClick: () => openThread(DRAFT_THREAD) },
                  { label: t("Live Call"), onClick: () => setCallOpen(true) },
                  { label: t("Stop now"), danger: true, onClick: () => void stopActive() },
                  { label: t("Coding agents"), onClick: () => setTab("coding") },
                ]}
              />
            </div>
          }
          under={
            activeThread === DRAFT_THREAD ? (
              <div className="flex flex-col items-center gap-1">
                <span className="truncate rounded-full bg-accent/12 px-2.5 py-0.5 text-[11.5px] font-medium text-accent">
                  {state.draftThread?.title || t("New chat")}
                </span>
                <ModelMenu />
              </div>
            ) : thread && thread.id !== "main" ? (
              thread.device ? (
                <span className="flex max-w-full items-center gap-1 rounded-full bg-accent/12 px-2.5 py-0.5 text-[11.5px] font-medium text-accent">
                  {deviceOnline === false ? <span className="h-1.5 w-1.5 rounded-full bg-border" /> : <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />}
                  <span className="truncate">{t("on {device}", { device: thread.device_name || thread.device })}</span>
                </span>
              ) : thread.remote_from ? (
                <span className="flex max-w-full items-center gap-1 rounded-full bg-surface-2 px-2.5 py-0.5 text-[11.5px] font-medium text-fg/80">
                  <MonitorSmartphone size={11} /> <span className="truncate">{t("from {device}", { device: thread.remote_from.name })}</span>
                </span>
              ) : (
                <span className="truncate rounded-full bg-surface-2 px-2.5 py-0.5 text-[11.5px] font-medium text-fg/80">{thread.title}</span>
              )
            ) : (
              // the model picker lives by the agent's name: the pick is sticky until changed
              <ModelMenu />
            )
          }
        />
      </div>
      {/* while reading older messages the top edge fades fully into the canvas, under the header */}
      <div
        aria-hidden
        className="list-fade-top pointer-events-none absolute inset-x-0 top-0 z-[5]"
        style={{ height: Math.max(headerHeight + 24, 156), opacity: scrolledUp ? 1 : 0 }}
      />

      {/* Timeline */}
      <div ref={listRef} onScroll={measure} className="relative flex-1 overflow-y-auto px-3 pb-3">
        <div ref={contentRef} className="space-y-2.5">
          {/* the room the header takes; the first message starts under it, not behind it */}
          <div aria-hidden style={{ height: headerHeight }} />
          {state.hasMore[activeThread] && events.length > 0 && (
            <div className="flex justify-center">
              <button
                type="button"
                className="text-[12.5px] text-accent px-3 py-1 rounded-full bg-surface-2"
                onClick={() => void loadEvents(activeThread, events[0]?.id)}
              >
                {t("Load earlier messages")}
              </button>
            </div>
          )}
          {/* The first conversation's opening, where the history begins: the app speaks first */}
          {bound && eventsLoaded && !state.hasMore[activeThread] && intro && <IntroLines lines={intro.lines} />}
          {eventsLoaded && events.length === 0 && !stream && !bound && (
            <EmptyChat
              name={name}
              device={thread?.device ? thread.device_name || thread.device : undefined}
              onSend={(text) => send(activeThread, text).catch((e: Error) => toast(e.message || t("Could not send")))}
            />
          )}
          {/* the live reply renders at its timeline position: after the event it anchored
              to, so a message typed mid-stream stays BELOW the reply that began first */}
          {(() => {
            const anchorIdx = stream?.anchor ? shown.findIndex((e) => e.id === stream.anchor) : -1;
            const before = anchorIdx >= 0 ? shown.slice(0, anchorIdx + 1) : [];
            const after = anchorIdx >= 0 ? shown.slice(anchorIdx + 1) : shown;
            return (
              <>
                {before.map((ev, i) => (
                  <EventView
                    key={ev.id}
                    event={ev}
                    prev={before[i - 1]}
                    name={name}
                    onDecide={handleDecideEvent}
                    onOpenFile={openFile}
                    onOpenBrowser={setBrowserView}
                    files={files}
                    onCloudRetry={cloudRetryFor(i)}
                    thread={activeThread}
                    newestReplyId={newestReplyId}
                    onReply={setReplyQuote}
                    onDeleteEvent={handleDeleteEvent}
                  />
                ))}
                {stream && (stream.text || stream.reasoning) && (
                  <AssistantBubble
                    text={stream.text}
                    reasoning={stream.reasoning}
                    streaming
                    files={files}
                    onOpenFile={openFile}
                  />
                )}
                {after.map((ev, i) => (
                  <EventView
                    key={ev.id}
                    event={ev}
                    prev={after[i - 1]}
                    name={name}
                    onDecide={handleDecideEvent}
                    onOpenFile={openFile}
                    onOpenBrowser={setBrowserView}
                    files={files}
                    onCloudRetry={cloudRetryFor(before.length + i)}
                    thread={activeThread}
                    newestReplyId={newestReplyId}
                    onReply={setReplyQuote}
                    onDeleteEvent={handleDeleteEvent}
                  />
                ))}
              </>
            );
          })()}
          <LiveReply stream={stream} name={name} />
          {typingHere(thread?.busy, status, activeThread, events) && !(stream?.text || stream?.reasoning) && (
            <TypingIndicator />
          )}
          {/* the chooser for the agent's name, under the latest reply, until a pick or a typed name */}
          {bound && fr.phase === "ask_agent_name" && !thread?.busy && (
            <NamingCard chips={fr.chips} chosen={fr.chosen} onPick={pickName} />
          )}
          {/* A message written on another device is never this device's unfinished turn (C9):
              nothing here ever offers to continue it. While that device works, this says so. */}
          {working && !thread?.busy && <WorkingLine who={working} />}
          {/* the person pressed Stop a moment ago: one question, why, until answered, skipped or stale */}
          {stopAsk && !thread?.busy && <StopAskCard ask={stopAsk} className="mx-1 my-2" />}
          {!thread?.busy && status.state === "idle" && taskCount !== null && <StarNudgeOnce moment="tasks" n={taskCount} className="mx-1 my-2" />}
          {!thread?.busy && status.state === "idle" && dayCount !== null && <StarNudgeOnce moment="days_used" n={dayCount} className="mx-1 my-2" />}
        </div>
        {showJump && (
          <button
            type="button"
            onClick={jumpToBottom}
            className="sticky bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-ink text-ink-fg shadow px-3 py-1.5 text-[12.5px] font-medium flex items-center gap-1"
          >
            <ChevronDown size={14} /> {t("Latest")}
            {pendingApprovals ? ` · ${pendingApprovals > 1 ? t("{n} approvals", { n: pendingApprovals }) : t("1 approval")}` : ""}
          </button>
        )}
      </div>

      <AllowanceHeadsUp />
      {(() => {
        const browserHold = state.holds.find((h) => h.thread === activeThread && h.tool === "browser");
        const liveBrowser = [...events].reverse().find(
          (e): e is BrowserEvent => e.type === "browser" && (e.status === "live" || !!browserHold),
        );
        if (!liveBrowser) return null;
        let host = liveBrowser.url;
        try {
          host = new URL(liveBrowser.url).host || liveBrowser.url;
        } catch {
          /* raw url */
        }
        return (
          <div className="rise mx-3 mb-1 flex items-center gap-2 rounded-2xl border border-border/70 bg-surface-2/85 px-2.5 py-1.5 text-[12px]">
            <span className="flex items-center gap-1.5 min-w-0 flex-1">
              <span className={cx("h-2 w-2 shrink-0 rounded-full animate-pulse", browserHold ? "bg-accent" : "bg-rose-500")} />
              <Globe size={13} className="shrink-0 text-muted" />
              <span className="font-semibold shrink-0">{browserHold ? t("You control browser") : t("Live browser")}</span>
              <span className="truncate text-muted">{liveBrowser.title || host}</span>
            </span>
            <button
              type="button"
              onClick={() => setBrowserView(liveBrowser.id)}
              className="shrink-0 rounded-full bg-surface px-2.5 py-1 font-medium text-fg shadow-xs hover:bg-bg transition"
            >
              {t("Watch")}
            </button>
            <button
              type="button"
              onClick={() => {
                const action = browserHold ? "handed_back" : "take_over";
                void api
                  .browserControl(activeThread, { action })
                  .then(() => {
                    if (!browserHold) setBrowserView(liveBrowser.id);
                  })
                  .catch((err: Error) => toast(err.message || t("Could not update browser control")));
              }}
              className={cx(
                "flex shrink-0 items-center gap-1 rounded-full px-2.5 py-1 font-medium transition",
                browserHold ? "bg-fg text-bg" : "bg-accent text-accent-fg",
              )}
            >
              {browserHold ? <Check size={12} /> : <Hand size={12} />}
              {browserHold ? t("Done") : t("Take over")}
            </button>
          </div>
        );
      })()}
      {/* the steps under way right now, one tap from their cards in the timeline */}
      {!steps && (
        <ActivityStrip
          events={events}
          onJump={jumpToEvent}
          busy={(status.state === "working" || !!thread?.busy) && waitingHere === 0}
        />
      )}
      <Composer
        name={name}
        activeThread={activeThread}
        busy={!!thread?.busy && pendingApprovals === 0}
        waiting={events.some((e) => e.type === "question" && e.status === "pending")}
        replyQuote={replyQuote}
        onClearReplyQuote={() => setReplyQuote(null)}
        onStop={stopActive}
        onSend={(text, files, model) =>
          send(activeThread, text, files, model)
            .then(() => jumpToBottom())
            .catch((e: Error) => {
              toast(e.message || t("Could not send"));
              throw e;
            })
        }
      />

      <MuseSheet open={activityOpen} onClose={() => setActivityOpen(false)} />
      <ConfirmDialog
        open={pendingDeleteEvent !== null}
        title={t("Delete this message?")}
        body={t("This removes the message from this conversation and from synced devices.")}
        confirmLabel={t("Delete message")}
        danger
        onConfirm={() => {
          const eid = pendingDeleteEvent;
          setPendingDeleteEvent(null);
          if (!eid) return;
          void deleteEvent(activeThread, eid).catch((e: Error) => toast(e.message));
        }}
        onCancel={() => setPendingDeleteEvent(null)}
      />
      {browserView && <BrowserViewer thread={activeThread} eventId={browserView} onClose={() => setBrowserView(null)} />}
      <LiveCallModal
        open={callOpen}
        threadId={activeThread === DRAFT_THREAD ? "main" : activeThread}
        agentName={name}
        onClose={() => setCallOpen(false)}
      />
    </div>
  );
}

// ------------------------------------------------------------------ pieces
const EventView = memo(function EventView({
  event,
  prev,
  name,
  onDecide,
  onOpenFile,
  onOpenBrowser,
  files,
  onCloudRetry,
  thread,
  newestReplyId,
  onReply,
  onDeleteEvent,
}: {
  event: TimelineEvent;
  prev?: TimelineEvent;
  name: string;
  onDecide: (id: string, approved: boolean, scope: string) => void | Promise<void>;
  onOpenFile: (path: string) => void;
  onOpenBrowser: (id: string) => void;
  files: readonly string[];
  /** a failed turn's *Use MiniMuse Cloud this time*, when it applies */
  onCloudRetry?: () => Promise<void>;
  /** the chat this list is of, and the reply the thumbs go under (none while a run is on) */
  thread?: string;
  newestReplyId?: string | null;
  onReply?: (text: string) => void;
  onDeleteEvent?: (eventId: string) => void;
}) {
  const onDecideThis = useCallback(
    (approved: boolean, scope: string) => onDecide(event.id, approved, scope),
    [onDecide, event.id],
  );
  const onDeleteThis = useMemo(
    () => (onDeleteEvent ? () => onDeleteEvent(event.id) : undefined),
    [onDeleteEvent, event.id],
  );
  const assistantVote = event.type === "assistant" ? (event.feedback ?? "") : "";
  const thumbs = useMemo(
    () =>
      thread
        ? {
            thread,
            eventId: event.id,
            vote: assistantVote,
            newest: event.id === newestReplyId,
          }
        : undefined,
    [thread, event.id, assistantVote, newestReplyId],
  );
  // a small centred time, iMessage style, when the conversation pauses for a while
  const divider = needsDivider(prev, event) ? <TimeDivider ts={event.ts} /> : null;
  const body = (() => {
    switch (event.type) {
      case "user":
        return <UserBubble event={event} onOpenFile={onOpenFile} onReply={onReply} onDelete={onDeleteThis} />;
      case "assistant":
        if (event.quiet) return <QuietLine text={event.text} about={event.about} ts={event.ts} />;
        return (
          <AssistantBubble
            eventId={event.id}
            ts={event.ts}
            text={event.text}
            reasoning={event.reasoning}
            continued={prev?.type === "assistant"}
            files={files}
            onOpenFile={onOpenFile}
            spoken={event.via === "call"}
            modelUsed={event.model_used}
            thumbs={thumbs}
            onReply={onReply}
            onDelete={onDeleteThis}
          />
        );
      case "tool":
        return <ToolChip event={event} />;
      case "approval":
        return <ApprovalCard event={event} name={name} onDecide={onDecideThis} />;
      case "question":
        return <QuestionCard event={event} name={name} />;
      case "model_switch":
        return <ModelSwitchCard event={event} thread={thread} />;
      case "notice":
        return <Notice event={event} onCloudRetry={onCloudRetry} />;
      case "artifact":
        return <ArtifactCard event={event} onOpen={onOpenFile} />;
      case "browser":
        return <BrowserCard event={event} onOpen={onOpenBrowser} />;
      case "hands":
        return <HandsCard event={event} name={name} />;
      case "hold":
        return <HoldCard event={event} name={name} />;
      case "avatar":
        return <AvatarOptionsCard event={event} name={name} />;
      default:
        return null;
    }
  })();
  if (!body) return null;
  return (
    <>
      {divider}
      {body}
    </>
  );
});

const DIVIDER_GAP_MS = 10 * 60 * 1000;

function needsDivider(prev: TimelineEvent | undefined, event: TimelineEvent): boolean {
  if (!event.ts) return false;
  if (!prev?.ts) return true;
  return new Date(event.ts).getTime() - new Date(prev.ts).getTime() >= DIVIDER_GAP_MS;
}

function TimeDivider({ ts }: { ts?: string }) {
  const label = timeDivider(ts);
  if (!label) return null;
  return <div className="pt-2 pb-0.5 text-center text-[11.5px] font-medium text-muted">{label}</div>;
}


function MessageActions({
  text,
  eventId,
  onReply,
  onDelete,
  align = "left",
  open = false,
}: {
  text: string;
  eventId?: string;
  onReply?: (text: string) => void;
  onDelete?: () => void;
  align?: "left" | "right";
  /** shown after a hold (touch) or while hovered (mouse); hidden the rest of the time */
  open?: boolean;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const [reactions, setReactions] = useState<Record<string, number>>(() => {
    if (!eventId) return {};
    try {
      return JSON.parse(localStorage.getItem(`nanomuse_reactions_${eventId}`) || "{}");
    } catch {
      return {};
    }
  });

  // the picker closes on a press anywhere outside it, the bubble it rides on included
  // (the row's reveal handler ignores presses inside `data-reveal`, so the picker does
  // its own closing); a press on the picker itself stays, so the buttons keep their clicks
  useEffect(() => {
    if (!showPicker) return;
    const onDown = (ev: PointerEvent) => {
      const target = ev.target as HTMLElement | null;
      if (target && typeof target.closest === "function" && target.closest("[data-picker]")) return;
      setShowPicker(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [showPicker]);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      return;
    } catch {
      /* insecure context (plain http on the LAN): fall back below */
    }
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      document.body.removeChild(area);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* nothing more we can do */
    }
  };

  const toggleReaction = (emoji: string) => {
    if (!eventId) return;
    setReactions((prev) => {
      const next = { ...prev };
      if (next[emoji]) {
        delete next[emoji];
      } else {
        next[emoji] = 1;
      }
      try {
        localStorage.setItem(`nanomuse_reactions_${eventId}`, JSON.stringify(next));
      } catch { /* storage unavailable */ }
      return next;
    });
    setShowPicker(false);
  };

  const emojis = ["❤️", "👍", "👎", "😂", "🔥", "🎉"];

  const pill = "flex items-center gap-1 rounded-full bg-surface px-2.5 py-1 text-[12px] text-muted shadow-sm border border-border/60 hover:text-fg hover:bg-surface-2 transition cursor-pointer active:scale-95";

  return (
    <div data-reveal className={cx("flex flex-col gap-1 mt-1", align === "right" ? "items-end" : "items-start")}>
      <div className={cx("flex items-center gap-1.5 transition-opacity duration-150", open ? "opacity-100" : "opacity-0 pointer-events-none")}>
        <button type="button" onClick={onCopy} title={copied ? t("Copied") : t("Copy message")} className={pill}>
          {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
          <span>{copied ? t("Copied") : t("Copy")}</span>
        </button>
        {onReply && (
          <button type="button" onClick={() => onReply(text)} title={t("Reply / Quote")} className={pill}>
            <Reply size={13} />
            <span>{t("Reply")}</span>
          </button>
        )}
        <div className="relative" data-picker>
          <button
            type="button"
            onClick={() => setShowPicker((s) => !s)}
            title={t("React with emoji")}
            aria-expanded={showPicker}
            className={cx(pill, "px-2")}
          >
            <Smile size={13} />
          </button>
          {showPicker && (
            <div className={cx("absolute bottom-9 z-20 flex gap-1 rounded-full bg-surface border border-border p-1.5 shadow-lg", align === "right" ? "right-0" : "left-0")}>
              {emojis.map((e) => (
                <button
                  key={e}
                  type="button"
                  onClick={() => toggleReaction(e)}
                  className="rounded-full p-1.5 text-[19px] leading-none transition-transform hover:scale-125 active:scale-95 cursor-pointer"
                >
                  {e}
                </button>
              ))}
            </div>
          )}
        </div>
        {onDelete && (
          <button
            type="button"
            onClick={onDelete}
            title={t("Delete message")}
            aria-label={t("Delete message")}
            className="flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium text-muted hover:bg-rose-500/12 hover:text-rose-500 transition cursor-pointer active:scale-95"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>
      {Object.keys(reactions).length > 0 && (
        <div className="flex flex-wrap gap-1 mt-0.5">
          {Object.entries(reactions).map(([emoji, count]) => (
            <button
              key={emoji}
              type="button"
              onClick={() => toggleReaction(emoji)}
              title={t("React with emoji")}
              className="flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-1 shadow-sm text-[15px] leading-none hover:border-accent/60 hover:bg-surface-2 transition cursor-pointer active:scale-95"
            >
              <span>{emoji}</span>
              {count > 1 && <span className="text-[12px] font-semibold text-fg">{count}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Hold (touch) or hover (mouse) a message to get at its actions. On a mouse the copy /
 *  reply / react row fades in beside the bubble and follows the cursor; on a touch screen
 *  a hold opens the message's action sheet instead (the row would sit under the finger).
 *  A pointer-down outside the row (marked `data-reveal`) closes it; a tap on the row
 *  itself does not, so the buttons keep their clicks. */
function useHoldReveal() {
  const [revealed, setRevealed] = useState(false);
  const [sheet, setSheet] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const clear = () => {
    if (timer.current !== undefined) window.clearTimeout(timer.current);
    timer.current = undefined;
  };
  useEffect(() => {
    if (!revealed) return;
    const onDown = (ev: PointerEvent) => {
      const target = ev.target as HTMLElement | null;
      if (target && typeof target.closest === "function" && target.closest("[data-reveal]")) return;
      setRevealed(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [revealed]);
  useEffect(() => {
    if (!sheet) return;
    const onDown = (ev: PointerEvent) => {
      const target = ev.target as HTMLElement | null;
      if (target && typeof target.closest === "function" && target.closest("[data-action-sheet]")) return;
      setSheet(false);
    };
    window.addEventListener("pointerdown", onDown);
    return () => window.removeEventListener("pointerdown", onDown);
  }, [sheet]);
  // the hover row is a mouse's nicety; on a touch screen a tap fires mouseenter too
  const hoverable = typeof matchMedia === "undefined" || matchMedia("(hover: hover)").matches;
  return {
    revealed,
    sheetOpen: sheet,
    setSheetOpen: setSheet,
    handlers: {
      onTouchStart: () => {
        clear();
        // the hold opens the action sheet, not the row: the row would sit under the finger
        timer.current = window.setTimeout(() => setSheet(true), 400);
      },
      onTouchEnd: clear,
      onTouchMove: clear,
      onMouseEnter: () => hoverable && setRevealed(true),
      onMouseLeave: () => hoverable && setRevealed(false),
    },
  };
}

/**
 * Swipe a message like a messenger: rightwards quotes it into the composer (quick reply),
 * leftwards shows its time for a moment. A sideways finger also cancels the long-press,
 * so a scroll that starts on a message never opens the sheet. Only horizontal intent
 * engages the drag — a finger that means vertical keeps scrolling the list.
 */
function useMessageGestures(onReply?: () => void) {
  const hold = useHoldReveal();
  const [dx, setDx] = useState(0);
  const [showTime, setShowTime] = useState(false);
  const drag = useRef<{ x: number; y: number; locked: boolean } | null>(null);
  const dxRef = useRef(0);
  const timeTimer = useRef<number | undefined>(undefined);
  const MAX = 72;
  const TRIGGER = 56;

  useEffect(() => () => {
    if (timeTimer.current !== undefined) window.clearTimeout(timeTimer.current);
  }, []);

  const onTouchStart = (ev: React.TouchEvent) => {
    hold.handlers.onTouchStart();
    if (ev.touches.length !== 1) return;
    drag.current = { x: ev.touches[0].clientX, y: ev.touches[0].clientY, locked: false };
  };
  const onTouchMove = (ev: React.TouchEvent) => {
    hold.handlers.onTouchMove();
    const d = drag.current;
    if (!d) return;
    const mx = ev.touches[0].clientX - d.x;
    const my = ev.touches[0].clientY - d.y;
    if (!d.locked) {
      if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
      if (Math.abs(mx) <= Math.abs(my) * 1.2) {
        drag.current = null; // a scroll, not a swipe
        setDx(0);
        return;
      }
      d.locked = true;
    }
    dxRef.current = Math.max(-MAX, Math.min(MAX, mx));
    setDx(dxRef.current);
  };
  const onTouchEnd = () => {
    hold.handlers.onTouchEnd();
    const moved = dxRef.current;
    drag.current = null;
    dxRef.current = 0;
    setDx(0);
    if (moved > TRIGGER && onReply) onReply();
    if (moved < -TRIGGER) {
      setShowTime(true);
      if (timeTimer.current !== undefined) window.clearTimeout(timeTimer.current);
      timeTimer.current = window.setTimeout(() => setShowTime(false), 2000);
    }
  };
  const onTouchCancel = () => {
    hold.handlers.onTouchEnd();
    drag.current = null;
    dxRef.current = 0;
    setDx(0);
  };

  return {
    dx,
    showTime,
    revealed: hold.revealed,
    sheetOpen: hold.sheetOpen,
    setSheetOpen: hold.setSheetOpen,
    handlers: {
      ...hold.handlers,
      onTouchStart,
      onTouchMove,
      onTouchEnd,
      onTouchCancel,
    },
  };
}

/** The two hints a dragged bubble shows at its edges: quote it (right) or read the time (left). */
function SwipeHints({ dx }: { dx: number }) {
  const t = useT();
  const strength = Math.min(1, Math.abs(dx) / 56);
  return (
    <>
      <span
        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 flex items-center gap-1 text-[11.5px] font-medium text-accent"
        style={{ opacity: dx > 12 ? strength : 0 }}
      >
        <Reply size={13} /> {t("Release to reply")}
      </span>
      <span
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1 text-[11.5px] font-medium text-muted"
        style={{ opacity: dx < -12 ? strength : 0 }}
      >
        <Clock3 size={13} /> {t("Release for time")}
      </span>
    </>
  );
}

/** The message's action sheet, where the phone's hold lands: copy, reply, react, and
 *  select the words. Fixed to the bottom edge so it is under the thumb, not the bubble. */
function MessageActionSheet({
  open,
  onClose,
  text,
  eventId,
  onReply,
  onDelete,
}: {
  open: boolean;
  onClose: () => void;
  text: string;
  eventId?: string;
  onReply?: (text: string) => void;
  onDelete?: () => void;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  // the sheet only toggles reactions; the bubble's row reads the same storage to show them
  const [, setReactions] = useState<Record<string, number>>(() => {
    if (!eventId) return {};
    try {
      return JSON.parse(localStorage.getItem(`nanomuse_reactions_${eventId}`) || "{}");
    } catch {
      return {};
    }
  });
  useClosable("message-actions", () => {
    onClose();
    return true;
  }, open);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      try {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
      } catch {
        /* nothing more we can do */
      }
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  const toggleReaction = (emoji: string) => {
    if (!eventId) return;
    setReactions((prev) => {
      const next = { ...prev };
      if (next[emoji]) delete next[emoji];
      else next[emoji] = 1;
      try {
        localStorage.setItem(`nanomuse_reactions_${eventId}`, JSON.stringify(next));
      } catch { /* storage unavailable */ }
      return next;
    });
    onClose();
  };
  // "Select text": the bubbles keep their words unselectable on touch (a long-press is
  // the action sheet's), so selecting means turning it on for one pass, over this bubble
  const selectText = () => {
    onClose();
    document.documentElement.classList.add("nm-selecting");
    requestAnimationFrame(() => {
      const host = eventId ? document.querySelector(`[data-msg="${CSS.escape(eventId)}"]`) : null;
      const target = host?.querySelector(".md") ?? host;
      if (target) {
        const range = document.createRange();
        range.selectNodeContents(target);
        const sel = window.getSelection();
        sel?.removeAllRanges();
        sel?.addRange(range);
      }
    });
    const off = () => {
      document.documentElement.classList.remove("nm-selecting");
      window.getSelection()?.removeAllRanges();
      window.removeEventListener("pointerdown", off);
    };
    window.addEventListener("pointerdown", off);
  };
  const row = "flex w-full items-center gap-3 px-4 py-3 text-left text-[14.5px] hover:bg-surface-2/70 active:bg-surface-2";
  if (!open) return null;
  return (
    <div data-action-sheet className="fixed inset-x-0 bottom-0 z-[65] flex flex-col items-center px-3 pb-[calc(var(--nm-safe-bottom)+10px)] pt-10">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <div className="rise relative w-full max-w-[420px] rounded-3xl border border-border bg-surface py-1 shadow-xl" role="menu">
        <button type="button" role="menuitem" onClick={() => void onCopy()} className={row}>
          {copied ? <Check size={17} className="text-emerald-500" /> : <Copy size={17} className="text-muted" />}
          {copied ? t("Copied") : t("Copy")}
        </button>
        {onReply && (
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onClose();
              onReply(text);
            }}
            className={row}
          >
            <Reply size={17} className="text-muted" /> {t("Reply")}
          </button>
        )}
        <div className="relative">
          <button type="button" role="menuitem" onClick={() => setShowPicker((s) => !s)} className={row} aria-expanded={showPicker}>
            <Smile size={17} className="text-muted" /> {t("React")}
          </button>
          {showPicker && (
            <div className="absolute inset-x-4 top-full z-10 flex justify-center gap-1 rounded-full border border-border bg-surface p-1.5 shadow-lg">
              {["❤️", "👍", "👎", "😂", "🔥", "🎉"].map((e) => (
                <button key={e} type="button" onClick={() => toggleReaction(e)} className="rounded-full p-1.5 text-[19px] leading-none transition-transform hover:scale-125 active:scale-95">
                  {e}
                </button>
              ))}
            </div>
          )}
        </div>
        {eventId && (
          <button type="button" role="menuitem" onClick={selectText} className={cx(row, "border-t border-border/60")}>
            <Type size={17} className="text-muted" /> {t("Select text")}
          </button>
        )}
        {onDelete && (
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              onClose();
              onDelete();
            }}
            className={`${row} text-rose-500`}
          >
            <Trash2 size={17} /> {t("Delete message")}
          </button>
        )}
      </div>
      <button type="button" onClick={onClose} className="rise relative mt-2 w-full max-w-[420px] rounded-3xl bg-surface py-3 text-[14.5px] font-medium text-fg/85 shadow-xl">
        {t("Cancel")}
      </button>
    </div>
  );
}

function splitReplyQuote(raw: string): { quote: string | null; body: string } {
  const m = /^>\s*"([\s\S]+?)"\r?\n\r?\n([\s\S]*)$/.exec(raw) || /^>\s*([^\r\n]+)\r?\n\r?\n([\s\S]*)$/.exec(raw);
  if (!m) return { quote: null, body: raw };
  return { quote: m[1].trim(), body: m[2] };
}

export const UserBubble = memo(function UserBubble({
  event,
  onOpenFile,
  onReply,
  onDelete,
}: {
  event: UserEvent;
  onOpenFile: (path: string) => void;
  onReply?: (text: string) => void;
  onDelete?: () => void;
}) {
  const t = useT();
  const gesture = useMessageGestures(onReply ? () => onReply(event.text) : undefined);
  const files = event.files ?? [];
  const pictures = files.filter((f) => f.kind === "image");
  const others = files.filter((f) => f.kind !== "image");
  const parsed = event.text ? splitReplyQuote(event.text) : { quote: null, body: "" };
  return (
    <div
      {...gesture.handlers}
      data-msg={event.id}
      className="rise relative flex flex-col items-end pl-12"
      style={{ transform: `translateX(${gesture.dx}px)`, transition: gesture.dx === 0 ? "transform 180ms ease-out" : "none" }}
    >
      {gesture.dx !== 0 && <SwipeHints dx={gesture.dx} />}
      {gesture.showTime && (
        <span className="absolute -top-1 right-2 z-10 rounded-full border border-border bg-surface px-2.5 py-1 text-[11.5px] text-muted shadow-sm">
          {timeDivider(event.ts)}
        </span>
      )}
      {event.via === "call" ? (
        <span className="mb-1 flex items-center gap-1 text-[11.5px] font-medium text-muted">
          <Phone size={11} /> {t("said on a call")}
        </span>
      ) : event.via ? (
        <span className="mb-1 text-[11.5px] font-medium text-muted">{t("asked from {device}", { device: event.via })}</span>
      ) : event.to_device_name || event.to_device ? (
        <span className="mb-1 flex items-center gap-1 text-[11.5px] font-medium text-accent">
          <MonitorSmartphone size={11} /> {t("to {device}", { device: event.to_device_name || event.to_device || "" })}
        </span>
      ) : (
        (event.via_device_name || event.via_device) && (
          <span className="mb-1 text-[11.5px] font-medium text-muted">{t("From {device}", { device: event.via_device_name || event.via_device || "" })}</span>
        )
      )}
      {pictures.length > 0 && (
        <div className="mb-1 flex max-w-full flex-wrap justify-end gap-1.5">
          {pictures.map((f) => (
            <button
              key={f.path}
              type="button"
              onClick={() => onOpenFile(f.path)}
              aria-label={f.name}
              className={cx("overflow-hidden rounded-2xl border border-border/60 bg-surface-2 shadow-sm active:scale-[0.98] transition", pictures.length === 1 ? "max-h-64 max-w-[240px]" : "h-28 w-28")}
            >
              <img src={fileUrl(f.path)} alt={f.name} loading="lazy" className={cx("object-cover", pictures.length === 1 ? "max-h-64 w-auto max-w-full" : "h-full w-full")} />
            </button>
          ))}
        </div>
      )}
      {others.map((f) => (
        <button
          key={f.path}
          type="button"
          onClick={() => onOpenFile(f.path)}
          className="mb-1 flex max-w-full items-center gap-2.5 rounded-2xl border border-border bg-surface px-3 py-2 text-left shadow-sm hover:bg-surface-2 active:scale-[0.98] transition"
        >
          <span className="rounded-xl bg-accent/12 p-2 text-accent">{f.kind === "data" ? <Table2 size={17} /> : <FileText size={17} />}</span>
          <span className="min-w-0">
            <span className="block truncate text-[14px] font-medium">{f.name}</span>
            <span className="block text-[11.5px] text-muted">{fileSize(f.size)}</span>
          </span>
        </button>
      ))}
      {event.text && (
        <div className="bubble-user max-w-full rounded-[20px] bg-bubble-user px-4 py-2.5 text-bubble-user-fg">
          {parsed.quote && (
            <div className="mb-2 flex items-start gap-2 rounded-xl border-l-[3px] border-bubble-user-fg/60 bg-black/15 px-2.5 py-1.5 text-[12.5px] leading-snug text-bubble-user-fg/90">
              <Reply size={13} className="mt-0.5 shrink-0 opacity-80" />
              <span className="line-clamp-3 break-words">{parsed.quote}</span>
            </div>
          )}
          <div className="md text-[15px] leading-[1.45] whitespace-pre-wrap break-words">{parsed.body}</div>
          {event.pending && (
            <div className="mt-1 flex items-center justify-end gap-1 text-[11px] text-bubble-user-fg/70">
              <Clock3 size={11} /> {t("sending when back online")}
            </div>
          )}
        </div>
      )}
      {event.reactions && event.reactions.length > 0 && (
        <div
          className="mt-1 flex max-w-full flex-wrap items-center justify-end gap-1 pr-1"
          title={t("MiniMuse reacted")}
          aria-label={`${t("MiniMuse reacted")}: ${event.reactions.join(" ")}`}
        >
          {event.reactions.slice(0, 3).map((emoji, i) => (
            <span
              key={`${emoji}-${i}`}
              className="react-chip rounded-full border border-border bg-surface px-1.5 py-0.5 text-[13px] leading-none shadow-sm"
              aria-hidden="true"
            >
              {emoji}
            </span>
          ))}
        </div>
      )}
      {(event.text || onDelete) && (
        <MessageActions text={event.text ?? ""} eventId={event.id} onReply={onReply} onDelete={onDelete} align="right" open={gesture.revealed} />
      )}
      <MessageActionSheet open={gesture.sheetOpen} onClose={() => gesture.setSheetOpen(false)} text={event.text ?? ""} eventId={event.id} onReply={onReply} onDelete={onDelete} />
    </div>
  );
});

function fileSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** A file picked in the composer: uploading, uploaded, or failed. */
interface Pending {
  key: string;
  file: File;
  preview: string | null;
  info: AttachmentInfo | null;
  error: string | null;
}

function chipKind(name: string): "data" | "other" {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  return ["csv", "tsv", "json", "xlsx", "xls"].includes(ext) ? "data" : "other";
}

/** Photos ride a relay (the phone's peer is not always on a direct path), and a 50-megapixel
    shot is tens of megabytes for data the model reads at 1568 px anyway: anything bigger is
    redrawn at 2048 px before it uploads. A GIF keeps its animation and small files keep
    their bytes. */
const PICTURE_SIDE = 2048;
const PICTURE_BYTES = 1_500_000;

async function shrinkPicture(file: File): Promise<File> {
  if (!file.type.startsWith("image/") || file.type === "image/gif") return file;
  if (file.size <= PICTURE_BYTES) return file;
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await new Promise<void>((ok, bad) => {
      img.onload = () => ok();
      img.onerror = () => bad(new Error("decode failed"));
    });
    const longest = Math.max(img.naturalWidth, img.naturalHeight);
    if (longest <= PICTURE_SIDE && file.size <= PICTURE_BYTES) return file;
    const scale = Math.min(1, PICTURE_SIDE / longest);
    const w = Math.max(1, Math.round(img.naturalWidth * scale));
    const h = Math.max(1, Math.round(img.naturalHeight * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    canvas.getContext("2d")?.drawImage(img, 0, 0, w, h);
    const type = file.type === "image/png" ? "image/png" : "image/jpeg";
    const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, type, type === "image/jpeg" ? 0.85 : undefined));
    if (!blob || blob.size >= file.size) return file;
    const keep = type === file.type;
    const suffix = type === "image/jpeg" ? ".jpg" : ".png";
    const name = keep ? file.name : file.name.replace(/\.[^.]*$/, "") + suffix;
    return new File([blob], name, { type });
  } catch {
    return file; // an undecodable picture ships as it is
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** The composer's picks stay with their thread: switch a tab, come back, they are here. A
    browser reload loses the File objects themselves (nothing keeps those), the same way a
    half-typed message survives a reload but an attachment chip does not. */
const keptFiles = new Map<string, Pending[]>();

/** previews are object URLs; they are revoked whenever the chip leaves the screen, so the
    ones that come back out of the map are minted again from the File they carry. */
function withFreshPreviews(items: Pending[]): Pending[] {
  return items.map((p) => ({
    ...p,
    preview: p.file.type.startsWith("image/") && p.file.size > 0 ? URL.createObjectURL(p.file) : p.info && p.info.kind === "image" ? fileUrl(p.info.path) : null,
  }));
}

const AssistantBubble = memo(function AssistantBubble({
  eventId,
  ts,
  text,
  reasoning,
  streaming,
  continued,
  files,
  onOpenFile,
  spoken,
  modelUsed,
  thumbs,
  onReply,
  onDelete,
}: {
  eventId?: string;
  /** when it arrived; the swipe-left time hint reads it */
  ts?: string;
  text: string;
  reasoning?: string;
  streaming?: boolean;
  continued?: boolean;
  files?: readonly string[];
  /** said aloud on a call rather than written */
  spoken?: boolean;
  /** *Use MiniMuse Cloud this time*: the account's model this reply came from */
  modelUsed?: string;
  /** the lightest feedback: two thumbs under the newest reply, the mark kept on one voted on */
  thumbs?: { thread: string; eventId: string; vote: "up" | "down" | ""; newest: boolean };
  onOpenFile?: (path: string) => void;
  onReply?: (text: string) => void;
  onDelete?: () => void;
}) {
  // live stream: thinking is open while it streams; a finished bubble keeps it folded
  const [showReasoning, setShowReasoning] = useState(!!streaming);
  const t = useT();
  const gesture = useMessageGestures(onReply ? () => onReply(text) : undefined);
  // the model's block for the app (the first conversation's names) is never shown — not even
  // the half of it a stream has delivered so far
  const blocks = useMemo(() => splitBlocks(stripNamingFence(text, !!streaming)), [text, streaming]);
  if (blocks.length === 0 && !streaming && !reasoning) return null;
  return (
    <div
      {...gesture.handlers}
      data-msg={eventId}
      className={cx("rise relative flex items-end gap-2 pr-10", continued && "-mt-1")}
      style={{ transform: `translateX(${gesture.dx}px)`, transition: gesture.dx === 0 ? "transform 180ms ease-out" : "none" }}
    >
      {gesture.dx !== 0 && <SwipeHints dx={gesture.dx} />}
      {gesture.showTime && ts && (
        <span className="absolute -top-1 left-2 z-10 rounded-full border border-border bg-surface px-2.5 py-1 text-[11.5px] text-muted shadow-sm">
          {timeShort(ts)}
        </span>
      )}
      <div className="min-w-0 max-w-full">
        {reasoning && (
          <button type="button" className="mb-1 ml-1 text-[12px] text-muted" onClick={() => setShowReasoning((s) => !s)}>
            {showReasoning ? t("Hide thinking") : t("Show thinking")}
          </button>
        )}
        {reasoning && showReasoning && (
          <div className="mb-1.5 rounded-2xl bg-surface-2/70 px-3 py-2 text-[13px] text-muted whitespace-pre-wrap break-words">
            {reasoning}
          </div>
        )}
        {/* the agent's pills, a step off the canvas: one per block of the reply; code and tables carry their own frame */}
        {blocks.map((b, i) =>
          b.bare ? (
            <div key={i} className="my-1 max-w-full">
              <Markdown text={b.text} files={files} onOpenFile={onOpenFile} />
            </div>
          ) : (
            <div
              key={i}
              className={cx(
                "my-1 max-w-[340px] rounded-[20px] px-3.5 py-2.5 wide:max-w-[600px]",
                b.text.includes("⏰") || b.text.includes("Reminder from earlier")
                  ? "border border-amber-500/40 bg-amber-500/10 shadow-xs ring-1 ring-amber-500/20 text-foreground"
                  : "bg-bubble-agent",
                spoken && "border border-accent/15"
              )}
            >
              {spoken && i === 0 && (
                <div className="mb-0.5 flex items-center gap-1 text-[11px] font-medium text-accent/80">
                  <Phone size={10} /> {t("said on a call")}
                </div>
              )}
              <Markdown text={b.text} files={files} onOpenFile={onOpenFile} />
              {streaming && i === blocks.length - 1 && <span className="inline-block w-1.5 h-4 ml-0.5 align-middle bg-accent/70 animate-pulse rounded-sm" />}
            </div>
          ),
        )}
        {blocks.length === 0 && streaming && (
          <div className="my-1 rounded-[20px] bg-bubble-agent px-3.5 py-2.5">
            <span className="inline-block w-1.5 h-4 align-middle bg-accent/70 animate-pulse rounded-sm" />
          </div>
        )}
        {modelUsed && !streaming && <div className="ml-1 text-[11px] text-muted">{t("Answered by MiniMuse Cloud this time ({model})", { model: modelUsed })}</div>}
        {!streaming && (text || onDelete) && (
          <div className="flex items-center justify-between gap-2 mt-1">
            <MessageActions text={text} eventId={eventId} onReply={onReply} onDelete={onDelete} align="left" open={gesture.revealed} />
            {thumbs && blocks.length > 0 && <Thumbs {...thumbs} />}
          </div>
        )}
      </div>
      <MessageActionSheet open={gesture.sheetOpen} onClose={() => gesture.setSheetOpen(false)} text={text} eventId={eventId} onReply={onReply} onDelete={onDelete} />
    </div>
  );
});

/** A background pass that found nothing worth interrupting you for: one muted line, not a bubble. */
function QuietLine({ text, about, ts }: { text: string; about?: string; ts?: string }) {
  const [open, setOpen] = useState(false);
  const t = useT();
  const label = about ? localLabel(about.replace(/^Working on your goal: /, "")) : t("background check");
  return (
    <div className="rise flex justify-center px-6">
      <button type="button" onClick={() => setOpen((o) => !o)} className="max-w-full rounded-2xl px-3 py-1.5 text-[12px] text-muted text-center leading-snug">
        <span className="inline-flex items-center gap-1.5">
          <Moon size={12} /> {t("Checked on {label}: nothing new", { label })}{ts ? ` · ${timeShort(ts)}` : ""}
        </span>
        {open && <span className="mt-1 block text-left whitespace-pre-wrap break-words text-[12.5px]">{text}</span>}
      </button>
    </div>
  );
}

/**
 * Whether the typing dots show in this chat: the runtime's work has to belong to this
 * thread, and it has to be the moment right after the person asked, with nothing of a
 * reply on the screen yet. A chat with not a word in it shows none. Once a step, a reply
 * or anything from a background pass has come in after their last message, the dots would
 * claim a first line is being written when work is under way or a reply already stands
 * there; the working line under the name carries that instead. Waiting on the person is
 * never typing.
 */
export function typingHere(threadBusy: boolean | undefined, status: Status, activeThread: string, events: TimelineEvent[] = []): boolean {
  if (status.state === "waiting") return false;
  if (!threadBusy && !(status.state === "working" && (status.thread === activeThread || !status.thread))) return false;
  // the only thing allowed to be the last word here is the request itself
  const last = events[events.length - 1];
  return !last || last.type === "user";
}

function TypingIndicator({ label }: { label?: string }) {
  const t = useT();
  return (
    <div className="flex items-center gap-2" role="status">
      <div aria-hidden className="rounded-[20px] rounded-bl-md bg-bubble-agent px-3.5 py-2.5 flex items-center gap-1">
        <span className="typing-dot h-2 w-2 rounded-full bg-muted" />
        <span className="typing-dot h-2 w-2 rounded-full bg-muted" />
        <span className="typing-dot h-2 w-2 rounded-full bg-muted" />
      </div>
      <span className="sr-only">{t("On it")}</span>
      {label && <span className="text-[12px] text-muted">{label}</span>}
    </div>
  );
}

/**
 * What a screen reader hears of a streamed reply: one line when the agent starts writing,
 * and the whole reply once it is complete. The tokens in between are not read out; a
 * live region that changed on every token would talk over itself. Sighted readers see the
 * bubble fill as before.
 */
function LiveReply({ stream, name }: { stream: Stream | undefined; name: string }) {
  const t = useT();
  const [said, setSaid] = useState("");
  // which announcement was made last, so a delta does not repeat it
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (!stream) {
      // a stream dropped without a reply (discarded or empty) leaves nothing to read
      if (last.current?.endsWith(":start")) setSaid("");
      last.current = null;
      return;
    }
    const key = `${stream.id}:${stream.ended ? "end" : "start"}`;
    if (last.current === key) return;
    last.current = key;
    setSaid(stream.ended ? stripNamingFence(stream.text) : t("{name} is writing a reply", { name }));
  }, [stream, name, t]);
  return (
    <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
      {said}
    </div>
  );
}

/**
 * The presence line for this chat while it is worth showing (C9): the entry as the store
 * holds it, or null once it is ten minutes old. Re-renders at the moment it expires, so the
 * line goes on its own when the other device never said it was done.
 */
function useLiveWorking(who: WorkingPresence | undefined): WorkingPresence | null {
  const expiresAt = who ? who.at * 1000 + WORKING_TTL_MS : 0;
  // the moment this entry went stale (0 while it is live); set by the timer below
  const [expired, setExpired] = useState(0);
  useEffect(() => {
    if (!expiresAt) return;
    const left = expiresAt - Date.now();
    if (left <= 0) {
      setExpired(expiresAt);
      return;
    }
    const timer = window.setTimeout(() => setExpired(expiresAt), left + 50);
    return () => window.clearTimeout(timer);
  }, [expiresAt]);
  if (!who || expired === expiresAt) return null;
  return who;
}

/** "Pixel 8 is working…" — quiet, under the message the other device is answering. With
 * 0.31 the frame says what the run is doing: the step's own words ride the presence, and
 * a run paused on a question or an approval reads as waiting rather than working. */
export function WorkingLine({ who }: { who: WorkingPresence }) {
  const t = useT();
  const device = who.device_name || who.device;
  const label =
    who.state === "waiting"
      ? t("{device} is waiting for you", { device })
      : who.detail
        ? t("{device} is working: {detail}", { device, detail: who.detail })
        : t("{device} is working…", { device });
  return (
    <div className="flex items-center gap-1.5 px-1 text-[12px] text-muted">
      <Loader2 size={12} className="animate-spin" />
      <span>{label}</span>
    </div>
  );
}

function EmptyChat({ name, device, onSend }: { name: string; device?: string; onSend: (text: string) => void }) {
  const t = useT();
  const starters = device
    ? [t("What is on your screen right now?"), t("Which folder are you in, and what is in it?"), t("Check for updates and tell me what needs a restart")]
    : [
        t("What can you do for me?"),
        t("Plan my week: ask me what's on my plate"),
        t("Research and compare two options for me"),
        t("Set up a long-term goal and track it"),
      ];
  // The face is already in the header above; here the agent speaks, as it does on the phone.
  return (
    <div className="flex flex-col items-center text-center px-6 pt-10 pb-6 gap-3">
      <div className="text-[20px] font-semibold">{device ? t("This chat goes to {device}.", { device }) : t("Hi, I'm {name}.", { name })}</div>
      <p className="text-muted text-[14.5px] leading-snug max-w-sm">
        {device
          ? t("Whatever you ask here, the {name} on {device} does where it is: its shell, its files, its screen. Every step shows up here, and anything that needs an approval asks you here.", { name, device })
          : t("I don't just answer; I get things done: research, plans, files, code, email, long-running goals. Everything I do shows up here, and anything hard to undo waits for your approval.")}
      </p>
      <div className="mt-2 flex flex-wrap justify-center gap-2">
        {starters.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onSend(s)}
            className="rounded-full bg-surface-2 px-3.5 py-1.5 text-[13.5px] hover:bg-border/60"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

export function Composer({
  name,
  busy,
  waiting,
  replyQuote,
  onClearReplyQuote,
  onSend,
  onStop,
  activeThread = "main",
}: {
  name: string;
  busy: boolean;
  waiting: boolean;
  replyQuote?: string | null;
  onClearReplyQuote?: () => void;
  onSend: (text: string, files: string[], model?: string) => Promise<void>;
  onStop?: () => Promise<void>;
  activeThread?: string;
}) {
  const { state, draft, draftFiles, toast } = useStore();
  const draftKey = `nanomuse_composer_draft_${activeThread}`;
  const [text, setText] = useState<string>(() => {
    try {
      return localStorage.getItem(`nanomuse_composer_draft_${activeThread}`) || "";
    } catch {
      return "";
    }
  });

  // Preserve text across tab switches, screen changes, and app refreshes
  useEffect(() => {
    try {
      if (text) {
        localStorage.setItem(draftKey, text);
      } else {
        localStorage.removeItem(draftKey);
      }
    } catch { /* storage unavailable */ }
  }, [text, draftKey]);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(draftKey) || "";
      if (saved) setText(saved);
    } catch { /* storage unavailable */ }
  }, [draftKey]);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  // the model pick lives by the agent's name in the header (ModelMenu) and is sticky:
  // every message goes out on it until it is changed, not just the next one
  // voice input: the words recognised so far replace what dictation put in the box, after
  // whatever was typed before the mic was tapped
  const typedBefore = useRef("");
  const textRef = useRef(text);
  useEffect(() => {
    textRef.current = text;
  }, [text]);
  const dictation = useDictation(
    useCallback((final: string, interim: string) => {
      const lead = typedBefore.current;
      const sep = lead && !/\s$/.test(lead) ? " " : "";
      const next = lead + sep + final + interim;
      // the box is only rewritten when the words actually changed: the engine fires an
      // event per word and a blind setText on each one fights the caret and the keyboard
      if (next !== textRef.current) setText(next);
    }, []),
    useCallback((message: string) => toast(message), [toast]),
  );
  const toggleDictation = () => {
    if (!dictation.listening) typedBefore.current = text;
    dictation.toggle();
  };
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [pending, setPending] = useState<Pending[]>(() =>
    withFreshPreviews(keptFiles.get(activeThread) ?? []),
  );
  const mounted = useRef(false);

  // files shared from another app on the phone: uploaded already, they only need chips
  useEffect(() => {
    if (!state.draftFiles) return;
    const items: Pending[] = state.draftFiles.map((info) => ({
      key: `shared-${info.path}`,
      file: new File([], info.name),
      preview: info.kind === "image" ? fileUrl(info.path) : null,
      info,
      error: null,
    }));
    setPending((cur) => [...cur.filter((p) => !items.some((i) => i.key === p.key)), ...items].slice(0, 10));
    draftFiles(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.draftFiles]);
  // the thread keeps its unsent attachments while the screen is under another tab; the
  // first of the two effects has to run first on a thread switch inside this screen
  useEffect(() => {
    if (mounted.current) setPending(withFreshPreviews(keptFiles.get(activeThread) ?? []));
    mounted.current = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeThread]);
  useEffect(() => {
    if (mounted.current) keptFiles.set(activeThread, pending);
  }, [activeThread, pending]);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<HTMLInputElement>(null);
  const t = useT();

  useEffect(() => {
    if (replyQuote) {
      ref.current?.focus();
    }
  }, [replyQuote]);

  // previews are object URLs; let them go when the chip goes (remove, a sent message) and
  // whatever is still waiting when the composer goes away
  const revoke = (items: Pending[]) =>
    items.forEach((p) => p.preview?.startsWith("blob:") && URL.revokeObjectURL(p.preview));
  const pendingRef = useRef<Pending[]>([]);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);
  useEffect(() => () => revoke(pendingRef.current), []);

  // the desktop's quick-chat shortcut: the window is up, the cursor goes here
  useEffect(() => {
    const focus = () => ref.current?.focus();
    window.addEventListener("nanomuse:quick-chat", focus);
    return () => window.removeEventListener("nanomuse:quick-chat", focus);
  }, []);

  // An incoming message re-renders the timeline, and on Android Chrome the software
  // keyboard sometimes drops with it even though the box never lost its focus on
  // purpose. If the box held focus across a render, hand it straight back (a no-op
  // when it still has it, and nothing when the person tapped away themselves).
  const hadFocus = useRef(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onFocus = () => (hadFocus.current = true);
    const onBlur = () => (hadFocus.current = false);
    el.addEventListener("focus", onFocus);
    el.addEventListener("blur", onBlur);
    return () => {
      el.removeEventListener("focus", onFocus);
      el.removeEventListener("blur", onBlur);
    };
  }, []);
  useEffect(() => {
    if (!hadFocus.current || document.activeElement === ref.current) return;
    requestAnimationFrame(() => {
      if (hadFocus.current && document.activeElement !== ref.current) {
        ref.current?.focus({ preventScroll: true });
      }
    });
  });

  const uploadOf = (key: string, file: File) =>
    api.upload(file)
      .then((info) => setPending((cur) => cur.map((p) => (p.key === key ? { ...p, info } : p))))
      .catch((e: Error) => {
        const why = e.message ? localDetail(e.message) : t("Upload failed");
        setPending((cur) => cur.map((p) => (p.key === key ? { ...p, error: why } : p)));
        toast(why);
      });
  const addFiles = (list: FileList | File[]) => {
    const files = Array.from(list).slice(0, Math.max(0, 10 - pending.length));
    if (files.length === 0) return;
    const items: Pending[] = files.map((file) => ({
      key: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 7)}`,
      file,
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : null,
      info: null,
      error: null,
    }));
    setPending((cur) => [...cur, ...items]);
    for (const item of items) {
      // a big picture is redrawn before it uploads: the phone's path to the server is
      // often a relay, and seconds-versus-minutes is decided by the bytes sent
      shrinkPicture(item.file)
        .then((shrunk) => {
          if (shrunk !== item.file) {
            // the chip keeps its preview (the same picture at full size) and shows the
            // smaller size; the old object URL goes when the chip does
            setPending((cur) => cur.map((p) => (p.key === item.key ? { ...p, file: shrunk } : p)));
          }
          return uploadOf(item.key, shrunk);
        })
        .catch(() => uploadOf(item.key, item.file)); // never expected; ship it as-is
    }
  };
  const remove = (key: string) => {
    revoke(pending.filter((p) => p.key === key));
    setPending((cur) => cur.filter((p) => p.key !== key));
  };
  const uploading = pending.some((p) => !p.info && !p.error);
  const attached = pending.flatMap((p) => (p.info ? [p.info.path] : []));

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    el.scrollTop = el.scrollHeight;
  }, [text]);

  // text handed over from another screen (a skill's "Use" button)
  useEffect(() => {
    if (state.draft === null) return;
    setText(state.draft);
    draft(null);
    const el = ref.current;
    if (el) {
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.draft]);

  // "/" at the start of the message offers the skills; the list is small and cached
  const skillsOn = state.settings?.skills?.enabled !== false;
  useEffect(() => {
    if (!skillsOn) return;
    // the version moves when a skill is saved: a slower earlier answer must not land
    // over a newer one's list, and a failed refresh keeps the list it has — an empty
    // menu would say the skills are gone when they are not
    let alive = true;
    api.skills()
      .then((d) => {
        if (alive) setSkills(d.skills.filter((sk) => sk.enabled));
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [skillsOn, state.skillsVersion]);
  const slash = /^\/([a-z0-9-]*)$/i.exec(text);
  const matches = skillsOn && slash ? skills.filter((sk) => sk.name.startsWith(slash[1].toLowerCase())).slice(0, 6) : [];
  const pick = (sk: SkillInfo) => {
    setText(`/${sk.name} `);
    ref.current?.focus();
  };
  // "@" at the start names another device of the account: the turn runs there (contract C7 rule 8)
  const hubDevices = state.hub?.devices ?? [];
  const mentions = mentionSuggestions(text, hubDevices);
  const target = mentions.length === 0 ? mentionTarget(text, hubDevices) : null;
  const pickDevice = (d: HubDevice) => {
    setText(`@${d.name} `);
    ref.current?.focus();
  };

  // while the agent works with an empty box, the send disc becomes a stop disc: the same
  // stop the activity sheet offers, one tap closer. Text in the box keeps send — what is
  // sent while busy is queued and picked up, not lost.
  const canStop = busy && !!onStop && !text.trim() && attached.length === 0 && !uploading && !sending;
  const stop = () => {
    if (!onStop || stopping) return;
    setStopping(true);
    onStop().finally(() => setStopping(false));
  };

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    const trimmed = text.trim();
    if ((!trimmed && attached.length === 0) || uploading || sending) return;
    if (dictation.listening) dictation.stop();
    const activeQuote = replyQuote ? replyQuote.trim() : "";
    const quoteSnippet = activeQuote.length > 140 ? activeQuote.slice(0, 140) + "..." : activeQuote;
    const payloadText = quoteSnippet ? `> "${quoteSnippet.replace(/\r?\n/g, " ")}"\n\n${trimmed}` : trimmed;
    if (replyQuote) onClearReplyQuote?.();
    // the draft leaves the box at once (it feels sent) and comes back if sending failed
    const files = pending;
    setSending(true);
    try {
      localStorage.removeItem(draftKey);
    } catch { /* storage unavailable */ }
    setText("");
    setPending([]);
    ref.current?.focus();
    onSend(payloadText, attached, state.settings?.model_choice ?? readModelChoice())
      .then(() => {
        revoke(files);
      })
      .catch(() => {
        try {
          localStorage.setItem(draftKey, trimmed);
        } catch { /* storage unavailable */ }
        setText((cur) => cur || trimmed);
        setPending((cur) => (cur.length ? cur : files));
      })
      .finally(() => setSending(false));
  };

  return (
    <form onSubmit={submit} className="shrink-0 bg-bg px-3 pt-1.5 pb-1">
      {busy && !waiting && (
        <div className="px-2 pb-1 text-[12px] text-muted">{t("{name} is working; anything you send now is picked up right away.", { name })}</div>
      )}
      {replyQuote && (
        <div className="mb-1.5 flex items-start justify-between gap-2 rounded-2xl border border-border/80 bg-surface-2/80 px-3.5 py-2 text-[12.5px] shadow-sm">
          <div className="flex min-w-0 flex-1 items-start gap-2">
            <Reply size={14} className="mt-0.5 shrink-0 text-accent" />
            <div className="min-w-0 flex-1 border-l-2 border-accent pl-2">
              <div className="text-[11px] font-semibold text-accent">{t("Replying to message")}</div>
              <div className="line-clamp-2 break-words text-fg/85">{replyQuote.trim()}</div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => onClearReplyQuote?.()}
            aria-label={t("Cancel reply")}
            className="rounded-full p-1 text-muted hover:bg-surface hover:text-fg"
          >
            <X size={14} />
          </button>
        </div>
      )}
      {matches.length > 0 && (
        <ul className="mb-2 max-h-72 overflow-y-auto rounded-3xl border border-border/70 bg-surface shadow-lg divide-y divide-border/70" role="listbox" aria-label={t("Skills")}>
          {matches.map((sk) => (
            <li key={sk.name}>
              <button type="button" onClick={() => pick(sk)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2/70 active:bg-surface-2">
                <Wand2 size={16} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[14px] font-medium">/{sk.name}</span>
                  <span className="block truncate text-[12.5px] text-muted">{sk.description}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {mentions.length > 0 && (
        <ul className="mb-2 max-h-72 overflow-y-auto rounded-3xl border border-border/70 bg-surface shadow-lg divide-y divide-border/70" role="listbox" aria-label={t("Devices")}>
          {mentions.map((d) => (
            <li key={d.id}>
              <button type="button" onClick={() => pickDevice(d)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-surface-2/70 active:bg-surface-2">
                <span className="shrink-0 text-accent">{d.kind === "computer" ? <Monitor size={16} /> : <Smartphone size={16} />}</span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[14px] font-medium">@{d.name}</span>
                  <span className="block truncate text-[12.5px] text-muted">{d.online ? t("Runs this there and reports back") : t("Offline")}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {target && (
        <div className="flex items-center gap-1.5 px-2 pb-1 text-[12px] text-muted">
          <MonitorSmartphone size={12} /> {t("This goes to {device}.", { device: target.name })}
        </div>
      )}
      {pending.length > 0 && (
        <div className="mb-1 flex gap-2.5 overflow-x-auto px-1 pt-2.5 pb-1 pr-3" role="list" aria-label={t("Attachments")}>
          {pending.map((p) => (
            <div key={p.key} role="listitem" className={cx("relative shrink-0 rounded-2xl border bg-surface-2", p.error ? "border-danger/60" : "border-border/70")}>
              {p.preview ? (
                <img src={p.preview} alt={p.file.name} className="h-16 w-16 rounded-2xl object-cover" />
              ) : (
                <div className="flex h-16 w-40 items-center gap-2 px-2.5">
                  <span className="rounded-xl bg-accent/12 p-1.5 text-accent">{chipKind(p.file.name) === "data" ? <Table2 size={16} /> : <FileText size={16} />}</span>
                  <span className="min-w-0">
                    <span className="block truncate text-[12.5px] font-medium">{p.file.name}</span>
                    <span className="block text-[11px] text-muted">{p.error ? t("Upload failed") : fileSize(p.info?.size ?? p.file.size)}</span>
                  </span>
                </div>
              )}
              {!p.info && !p.error && (
                <div className="absolute inset-0 flex items-center justify-center rounded-2xl bg-bg/50">
                  <Loader2 size={18} className="animate-spin text-accent" />
                </div>
              )}
              <button
                type="button"
                onClick={() => remove(p.key)}
                aria-label={t("Remove")}
                className="absolute -right-1.5 -top-1.5 flex h-6 w-6 items-center justify-center rounded-full border border-border bg-surface text-muted shadow-sm"
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2 rounded-[26px] border border-border bg-surface px-1.5 py-1.5 shadow-[0_2px_12px_-6px_rgba(0,0,0,0.12)] focus-within:border-fg/20">
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        {/* `capture` opens the camera directly on the phone and the photo goes to the
            chat without being saved to the gallery. */}
        <input
          ref={camRef}
          type="file"
          accept="image/*"
          capture="environment"
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          aria-label={t("Attach a file")}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-fg/70 transition hover:bg-surface-2 active:scale-95"
        >
          <Plus size={22} />
        </button>
        <button
          type="button"
          onClick={() => camRef.current?.click()}
          aria-label={t("Take a photo")}
          title={t("Take a photo")}
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-fg/70 transition hover:bg-surface-2 active:scale-95"
        >
          <Camera size={20} />
        </button>
        <textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            const files = Array.from(e.clipboardData?.files ?? []);
            if (files.length > 0) {
              e.preventDefault();
              addFiles(files);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Tab" && matches.length > 0 && matches[0]) {
              e.preventDefault();
              pick(matches[0]);
              return;
            }
            if (e.key === "Tab" && mentions.length > 0 && mentions[0]) {
              e.preventDefault();
              pickDevice(mentions[0]);
              return;
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder={waiting ? t("Answer {name}…", { name }) : pending.length > 0 ? t("Say what to do with it…") : t("Message")}
          aria-label={t("Message")}
          className="flex-1 resize-none bg-transparent px-1 py-2 text-[15px] leading-[1.4] outline-none placeholder:text-muted"
        />
        {dictation.supported && <MicButton listening={dictation.listening} onClick={toggleDictation} />}
        {canStop ? (
          <button
            type="button"
            onClick={stop}
            disabled={stopping}
            aria-label={t("Stop")}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-ink text-ink-fg transition active:scale-95 disabled:opacity-60"
          >
            <Square size={15} fill="currentColor" strokeWidth={0} />
          </button>
        ) : (
          <button
            type="submit"
            disabled={(!text.trim() && attached.length === 0) || uploading}
            aria-label={t("Send")}
            className={cx(
              // the ink disc of the phone: black with a white arrow in the light theme, inverted in the dark
              "flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition active:scale-95",
              text.trim() || attached.length > 0 ? "bg-ink text-ink-fg" : "bg-surface-2 text-muted",
              "disabled:opacity-60",
            )}
          >
            <ArrowUp size={19} strokeWidth={2.5} />
          </button>
        )}
      </div>
    </form>
  );
}

/** The list of chats with the devices a chat can be addressed to and the new-chat row; the sheet on the phone, the sidebar on a wide screen. */
export function ThreadList({
  threads,
  active,
  onPick,
  onCleared,
  compact = false,
  grouped = false,
}: {
  threads: ThreadMeta[];
  active: string;
  onPick: (id: string) => void;
  onCleared?: () => void;
  compact?: boolean;
  /** the main chat on its own, then the side chats under their own heading with a + (the desktop's chats column) */
  grouped?: boolean;
}) {
  const { state, toast, dispatch } = useStore();
  const t = useT();
  const [title, setTitle] = useState("");
  const [sheet, setSheet] = useState<{ thread: ThreadMeta; mode: ThreadSheetMode } | null>(null);
  const [trashOpen, setTrashOpen] = useState(false);
  const newInput = useRef<HTMLInputElement>(null);
  // devices of yours on the hub that a chat can be addressed to
  const devices: HubDevice[] = (state.hub?.devices ?? []).filter((d) => !d.this && d.kind !== "web" && d.online);
  const kindOf = (id: string) => state.hub?.devices.find((d) => d.id === id)?.kind ?? "phone";

  const ask = async (d: HubDevice) => {
    try {
      const r = await api.askDevice(d.id);
      dispatch({ type: "ws", msg: { kind: "thread", thread: r.thread } });
      onPick(r.thread.id);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  // New opens a page-only chat (the drawer does the same): the record and the title are
  // made by the first message, so an emptied one never sits in the list.
  const create = () => {
    dispatch({ type: "draftThread", title: title.trim() || t("Side chat") });
    setTitle("");
    onPick(DRAFT_THREAD);
  };

  const rename = async (id: string, next: string) => {
    try {
      const th = await api.renameThread(id, next);
      dispatch({ type: "ws", msg: { kind: "thread", thread: th } });
      setSheet(null);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deleteThread(id);
      dispatch({ type: "ws", msg: { kind: "thread_deleted", thread: id } });
      setSheet(null);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const clear = async (id: string) => {
    try {
      await api.clearThread(id);
      dispatch({ type: "ws", msg: { kind: "thread_cleared", thread: id } });
      onCleared?.();
      setSheet(null);
    } catch (e) {
      toast((e as Error).message);
    }
  };

  const row = (th: ThreadMeta) => (
          <li
            key={th.id}
            onContextMenu={(e) => {
              e.preventDefault();
              setSheet({ thread: th, mode: "menu" });
            }}
            className={cx("group flex items-center gap-1 px-3", compact ? "py-2 rounded-xl hover:bg-surface-2/60" : "py-2.5", th.id === active && "bg-surface-2/60")}
          >
            <button
              type="button"
              {...longPressHandlers(
                () => setSheet({ thread: th, mode: "menu" }),
                () => onPick(th.id),
              )}
              className="flex-1 text-left min-w-0"
            >
              <div className={cx("font-medium truncate flex items-center gap-2", compact ? "text-[13.5px]" : "text-[15px]")}>
                {th.device ? (
                  <span className="text-accent">{kindOf(th.device) === "computer" ? <Monitor size={14} /> : <Smartphone size={14} />}</span>
                ) : th.remote_from ? (
                  <MonitorSmartphone size={14} className="text-muted" />
                ) : null}
                <span className="truncate">{th.title || (th.id === "main" ? t("Main chat") : t("Side chat"))}</span>
                {th.busy && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400 animate-pulse" />}
              </div>
              <div className={cx("text-muted", compact ? "text-[11.5px]" : "text-[12px]")}>
                {t("{n} events", { n: th.events })} · {timeShort(th.updated_at)}
              </div>
            </button>
            <button
              type="button"
              aria-label={t("Rename")}
              onClick={() => setSheet({ thread: th, mode: "rename" })}
              className={cx("p-1.5 text-muted hover:text-fg", compact && "opacity-0 group-hover:opacity-100 focus:opacity-100")}
            >
              <Pencil size={14} />
            </button>
            <button
              type="button"
              aria-label={t("Clear")}
              onClick={() => setSheet({ thread: th, mode: "clear" })}
              className={cx("p-1.5 text-muted hover:text-fg", compact && "opacity-0 group-hover:opacity-100 focus:opacity-100")}
            >
              <X size={15} />
            </button>
            {th.id !== "main" && (
              <button
                type="button"
                aria-label={t("Move to Trash")}
                onClick={() => setSheet({ thread: th, mode: "delete" })}
                className={cx("p-1.5 text-muted hover:text-rose-500", compact && "opacity-0 group-hover:opacity-100 focus:opacity-100")}
              >
                <Trash2 size={15} />
              </button>
            )}
            <button
              type="button"
              aria-label={t("Chat options")}
              onClick={() => setSheet({ thread: th, mode: "menu" })}
              className={cx("p-1.5 text-muted hover:text-fg", compact && "opacity-0 group-hover:opacity-100 focus:opacity-100")}
            >
              <MoreHorizontal size={14} />
            </button>
          </li>
  );
  const listCls = cx("divide-y divide-border overflow-hidden", compact ? "rounded-xl" : "rounded-2xl border border-border");
  const heading = "px-3 pb-1 pt-3 text-[11.5px] font-semibold uppercase tracking-wide text-muted";

  return (
    <div>
      {grouped ? (
        <>
          <div className={heading}>{t("Main chat")}</div>
          <ul className={listCls}>{threads.filter((th) => th.id === "main").map(row)}</ul>
          <div className={cx(heading, "flex items-center justify-between pr-1")}>
            <span>{t("Side chats")}</span>
            <button
              type="button"
              aria-label={t("New side chat")}
              onClick={create}
              className="flex h-6 w-6 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
            >
              <Plus size={15} />
            </button>
          </div>
          <ul className={listCls}>
            {active === DRAFT_THREAD && (
              <li className={cx("flex items-center gap-2 px-3 bg-surface-2/60", compact ? "py-2 rounded-xl" : "py-2.5")}>
                <button type="button" onClick={() => onPick(DRAFT_THREAD)} className="flex-1 text-left min-w-0">
                  <div className={cx("font-medium truncate text-accent", compact ? "text-[13.5px]" : "text-[15px]")}>
                    {state.draftThread?.title || t("New chat")}
                  </div>
                  <div className={cx("text-muted", compact ? "text-[11.5px]" : "text-[12px]")}>{t("Draft · send a message to start")}</div>
                </button>
              </li>
            )}
            {threads.filter((th) => th.id !== "main").map(row)}
          </ul>
          {threads.every((th) => th.id === "main") && active !== DRAFT_THREAD && <p className="px-3 py-2 text-[12px] text-muted">{t("A side chat keeps one topic apart from the rest.")}</p>}
        </>
      ) : (
        <ul className={listCls}>{threads.map(row)}</ul>
      )}
      {devices.length > 0 && (
        <div className="mt-4">
          <div className="mb-1.5 text-[12px] font-medium text-muted">{t("Ask one of your devices")}</div>
          <div className="flex flex-wrap gap-2">
            {devices.map((d) => (
              <button
                key={d.id}
                type="button"
                onClick={() => void ask(d)}
                className="flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-[13px] font-medium hover:bg-surface-2"
              >
                {d.kind === "phone" ? <Smartphone size={14} className="text-accent" /> : <Monitor size={14} className="text-accent" />}
                {d.name}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className={cx("flex gap-2", compact ? "mt-3" : "mt-4")}>
        <input
          ref={newInput}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") create();
          }}
          placeholder={compact ? t("New side chat") : t("New side chat, e.g. “Trip to Kyoto”")}
          aria-label={t("New side chat")}
          className={cx("min-w-0 flex-1 bg-surface-2 outline-none focus:ring-2 focus:ring-accent/40", compact ? "rounded-xl px-3 py-2 text-[13px]" : "rounded-2xl px-3.5 py-2.5 text-[14px]")}
        />
        <button
          type="button"
          onClick={create}
          aria-label={t("New")}
          className={cx("flex items-center gap-1.5 bg-accent text-accent-fg font-medium disabled:opacity-50", compact ? "rounded-xl px-2.5" : "rounded-2xl px-3.5 py-2.5")}
        >
          <MessageSquarePlus size={18} /> {!compact && t("New")}
        </button>
      </div>
      <div className="mt-3 pt-2 border-t border-border/60">
        <button
          type="button"
          onClick={() => setTrashOpen(true)}
          className="flex w-full items-center justify-between rounded-xl px-3 py-2 text-[13px] font-medium text-muted hover:bg-surface-2/60 hover:text-fg transition"
        >
          <span className="flex items-center gap-2">
            <Trash2 size={14} /> {t("Trash")}
          </span>
          {state.trashCount > 0 && (
            <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-fg/80">{state.trashCount}</span>
          )}
        </button>
      </div>
      {sheet && (
        <ThreadActionsSheet
          thread={sheet.thread}
          initialMode={sheet.mode}
          onClose={() => setSheet(null)}
          onRename={(next) => void rename(sheet.thread.id, next)}
          onClear={() => void clear(sheet.thread.id)}
          onDelete={() => void remove(sheet.thread.id)}
        />
      )}
      <TrashModal open={trashOpen} onClose={() => setTrashOpen(false)} onRestored={(id) => onPick(id)} />
    </div>
  );
}

/** One line for under the name, folded and cut at a word. */
function briefCut(text: string, max: number): string {
  const folded = (text || "").replace(/\s+/g, " ");
  if (folded.length <= max) return folded;
  const cut = folded.slice(0, max);
  const at = cut.lastIndexOf(" ");
  return (at > 16 ? cut.slice(0, at) : cut).trimEnd() + "…";
}

/**
 * The words after "On it" while this chat's turn works: the step it is on, in the model's
 * own words for that step, or, before any step has landed, the request with its discourse
 * words dropped. So "So yeah I had a sausage and ramen for dinner" names the dinner, and a
 * logging step names the step, unique to this ask instead of the same old prefix.
 */
export function runningStepTitle(events: TimelineEvent[]): string {
  // The name of the tool step running right now, for the line under the name and the
  // activity strip. Empty means the run is between steps, inside a model call: "Thinking".
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "user") break;
    if (ev.type === "tool" && ev.status === "running") {
      const words = (ev.title || ev.summary || ev.tool || "").trim();
      if (words) return words;
    }
  }
  return "";
}

/**
 * The turn that just ended was the person's: walking back from the end, a reply with words,
 * then the bubble that started it — `user` (typed, dictated, an idea tapped) counts; a
 * background notice (a routine, the feed, a goal check-in) or an ask from another device
 * does not.
 */
export function personStartedTurn(events: TimelineEvent[]): boolean {
  let replied = false;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type === "assistant") {
      if (!e.text || e.quiet) return false;
      replied = true;
      continue;
    }
    if (e.type === "user") return replied && !e.via;
    if (e.type === "notice") return false;
  }
  return false;
}
