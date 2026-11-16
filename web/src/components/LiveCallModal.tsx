import { Check, Loader2, Mic, MicOff, Phone, PhoneOff, Send, Settings2, Volume2, VolumeX, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken } from "../api";
import { useT } from "../i18n";
import type { CallCapabilities } from "../types";
import { cx } from "../util";

interface SpeechRecLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((ev: { resultIndex: number; results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((ev: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}

type SpeechCtor = new () => SpeechRecLike;

type TranscriptLine = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
};

export function LiveCallModal({
  open,
  threadId,
  agentName,
  onClose,
}: {
  open: boolean;
  threadId: string;
  agentName: string;
  onClose: () => void;
}) {
  const t = useT();
  const [caps, setCaps] = useState<CallCapabilities | null>(null);
  const [status, setStatus] = useState<"connecting" | "listening" | "thinking" | "speaking" | "muted" | "error">("connecting");
  const [muted, setMuted] = useState(false);
  const [speakerOff, setSpeakerOff] = useState(false);
  const [patienceMs, setPatienceMs] = useState<number>(3500); // Patient default; 0 = Manual Done
  const [draftSpeech, setDraftSpeech] = useState("");
  const [typedDraft, setTypedDraft] = useState("");
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [showConfig, setShowConfig] = useState(false);
  const [errorText, setErrorText] = useState("");
  const [wakeActive, setWakeActive] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const recRef = useRef<SpeechRecLike | null>(null);
  const flushTimerRef = useRef<number | null>(null);
  const spokenBufRef = useRef("");
  const mutedRef = useRef(false);
  const speakerOffRef = useRef(false);
  const patienceRef = useRef(patienceMs);
  const statusRef = useRef(status);
  const wakeLockRef = useRef<{ release: () => Promise<void> } | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const nextPlayTimeRef = useRef(0);

  useEffect(() => {
    mutedRef.current = muted;
    speakerOffRef.current = speakerOff;
    patienceRef.current = patienceMs;
    statusRef.current = status;
  }, [muted, speakerOff, patienceMs, status]);

  const appendLine = useCallback((role: TranscriptLine["role"], text: string, streamAppend = false) => {
    const clean = text;
    if (!clean.trim() && !streamAppend) return;
    setLines((prev) => {
      if (streamAppend && prev.length > 0 && prev[prev.length - 1].role === role) {
        const next = [...prev];
        next[next.length - 1] = { ...next[next.length - 1], text: next[next.length - 1].text + clean };
        return next;
      }
      return [...prev.slice(-24), { id: `${Date.now()}-${Math.random()}`, role, text: clean }];
    });
  }, []);

  const clearFlushTimer = () => {
    if (flushTimerRef.current !== null) {
      window.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
  };

  const commitSpeech = useCallback(
    (forceText?: string) => {
      clearFlushTimer();
      const text = (forceText ?? spokenBufRef.current).trim();
      spokenBufRef.current = "";
      setDraftSpeech("");
      if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
      appendLine("user", text);
      setStatus("thinking");
      wsRef.current.send(
        JSON.stringify({
          kind: "user_text",
          text,
          language: navigator.language || "en",
        }),
      );
    },
    [appendLine],
  );

  const scheduleFlush = useCallback(() => {
    clearFlushTimer();
    const wait = patienceRef.current;
    if (wait <= 0) return; // Manual Done mode: wait for user tap
    flushTimerRef.current = window.setTimeout(() => {
      commitSpeech();
    }, wait);
  }, [commitSpeech]);

  const extendPatience = () => {
    clearFlushTimer();
    if (patienceRef.current > 0) {
      flushTimerRef.current = window.setTimeout(() => {
        commitSpeech();
      }, 7000);
    }
  };

  const speakText = useCallback((text: string) => {
    if (speakerOffRef.current || typeof window === "undefined" || !("speechSynthesis" in window)) {
      setStatus(mutedRef.current ? "muted" : "listening");
      return;
    }
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.04;
      setStatus("speaking");
      u.onend = () => setStatus(mutedRef.current ? "muted" : "listening");
      u.onerror = () => setStatus(mutedRef.current ? "muted" : "listening");
      window.speechSynthesis.speak(u);
    } catch {
      setStatus(mutedRef.current ? "muted" : "listening");
    }
  }, []);

  const playPcm16 = useCallback((b64: string, rate = 24000) => {
    if (speakerOffRef.current) return;
    try {
      const ctx = audioCtxRef.current || new AudioContext();
      audioCtxRef.current = ctx;
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const view = new DataView(bytes.buffer);
      const samples = new Float32Array(bytes.byteLength / 2);
      for (let i = 0; i < samples.length; i++) {
        samples[i] = view.getInt16(i * 2, true) / 32768;
      }
      const buf = ctx.createBuffer(1, samples.length, rate);
      buf.getChannelData(0).set(samples);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const now = ctx.currentTime;
      const startAt = Math.max(now, nextPlayTimeRef.current);
      src.start(startAt);
      nextPlayTimeRef.current = startAt + buf.duration;
      setStatus("speaking");
      src.onended = () => {
        if (ctx.currentTime >= nextPlayTimeRef.current - 0.05) {
          setStatus(mutedRef.current ? "muted" : "listening");
        }
      };
    } catch {
      /* ignore audio playback glitch */
    }
  }, []);

  const stopAudioPipeline = useCallback(() => {
    clearFlushTimer();
    try {
      recRef.current?.stop();
    } catch {
      /* noop */
    }
    recRef.current = null;
    try {
      mediaStreamRef.current?.getTracks().forEach((tr) => tr.stop());
    } catch {
      /* noop */
    }
    mediaStreamRef.current = null;
    try {
      window.speechSynthesis?.cancel?.();
    } catch {
      /* noop */
    }
    try {
      void audioCtxRef.current?.close();
    } catch {
      /* noop */
    }
    audioCtxRef.current = null;
    nextPlayTimeRef.current = 0;
  }, []);

  const startSpeechRecognition = useCallback(() => {
    const w = window as unknown as {
      SpeechRecognition?: SpeechCtor;
      webkitSpeechRecognition?: SpeechCtor;
    };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) return false;
    const rec = new Ctor();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = navigator.language || "en-US";
    rec.onresult = (ev) => {
      if (mutedRef.current) return;
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const res = ev.results[i];
        const seg = res[0]?.transcript || "";
        if (res.isFinal) {
          spokenBufRef.current = (spokenBufRef.current + " " + seg).trim();
        } else {
          interim += seg;
        }
      }
      setDraftSpeech((spokenBufRef.current + " " + interim).trim());
      if (spokenBufRef.current || interim.trim()) {
        scheduleFlush();
      }
    };
    rec.onerror = (ev) => {
      if (ev.error === "not-allowed") {
        setErrorText(t("Microphone permission was denied. You can still type below."));
      }
    };
    rec.onend = () => {
      // Keep live call continuous while dialog remains open & unmuted
      if (wsRef.current?.readyState === WebSocket.OPEN && !mutedRef.current) {
        window.setTimeout(() => {
          try {
            rec.start();
          } catch {
            /* ignore if already started */
          }
        }, 250);
      }
    };
    try {
      rec.start();
      recRef.current = rec;
      return true;
    } catch {
      return false;
    }
  }, [scheduleFlush, t]);

  const startNativeAudioCapture = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;
      const ctx = new AudioContext({ sampleRate: 16000 });
      audioCtxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      proc.onaudioprocess = (ev) => {
        if (mutedRef.current || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        const f32 = ev.inputBuffer.getChannelData(0);
        const i16 = new Int16Array(f32.length);
        for (let i = 0; i < f32.length; i++) {
          const s = Math.max(-1, Math.min(1, f32[i]));
          i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        const u8 = new Uint8Array(i16.buffer);
        let bin = "";
        for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
        wsRef.current.send(JSON.stringify({ kind: "audio_chunk", pcm16: btoa(bin) }));
      };
      src.connect(proc);
      proc.connect(ctx.destination);
    } catch {
      // Fall back to browser SpeechRecognition if direct AudioContext capture fails
      startSpeechRecognition();
    }
  }, [startSpeechRecognition]);

  const connectCall = useCallback(
    (patience: number) => {
      stopAudioPipeline();
      try {
        wsRef.current?.close();
      } catch {
        /* noop */
      }
      setStatus("connecting");
      setErrorText("");
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${window.location.host}/ws/call?thread=${encodeURIComponent(threadId)}&patience_ms=${Math.max(1200, patience || 3500)}`);
      wsRef.current = ws;

      ws.onopen = () => {
        const token = getToken();
        if (token) {
          ws.send(JSON.stringify({ kind: "auth", token }));
        }
      };

      ws.onmessage = (ev) => {
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(String(ev.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        const kind = String(msg.kind || "");
        if (kind === "call_ready") {
          setStatus(mutedRef.current ? "muted" : "listening");
          if (msg.native_realtime) {
            void startNativeAudioCapture();
          } else {
            startSpeechRecognition();
          }
        } else if (kind === "audio_delta" && typeof msg.pcm16 === "string") {
          playPcm16(msg.pcm16, typeof msg.rate === "number" ? msg.rate : 24000);
        } else if (kind === "assistant_start") {
          setStatus("thinking");
          appendLine("assistant", "", false);
        } else if (kind === "assistant_delta" && typeof msg.delta === "string") {
          appendLine("assistant", msg.delta, true);
        } else if (kind === "assistant_done" && typeof msg.text === "string") {
          setLines((prev) => {
            const copy = [...prev];
            if (copy.length > 0 && copy[copy.length - 1].role === "assistant") {
              copy[copy.length - 1] = { ...copy[copy.length - 1], text: msg.text as string };
              return copy;
            }
            return [...copy.slice(-24), { id: `${Date.now()}`, role: "assistant", text: msg.text as string }];
          });
          if (msg.text.trim()) speakText(msg.text);
        } else if (kind === "user_transcript" && typeof msg.text === "string") {
          appendLine("user", msg.text);
        } else if (kind === "call_notice" && typeof msg.message === "string") {
          appendLine("system", msg.message);
        } else if (kind === "call_error" && typeof msg.message === "string") {
          setErrorText(msg.message);
          setStatus("error");
        }
      };

      ws.onerror = () => {
        setErrorText(t("Live call connection dropped."));
        setStatus("error");
      };
      ws.onclose = () => {
        if (statusRef.current !== "error") {
          setStatus("connecting");
        }
      };
    },
    [appendLine, playPcm16, speakText, startNativeAudioCapture, startSpeechRecognition, stopAudioPipeline, t, threadId],
  );

  useEffect(() => {
    if (!open) return;
    let alive = true;
    void api
      .callCapabilities()
      .then((c) => {
        if (alive) setCaps(c);
      })
      .catch(() => undefined);

    // Keep screen awake during live call
    const nav = navigator as unknown as { wakeLock?: { request: (type: "screen") => Promise<{ release: () => Promise<void> }> } };
    if (nav.wakeLock?.request) {
      void nav.wakeLock
        .request("screen")
        .then((wl) => {
          if (alive) {
            wakeLockRef.current = wl;
            setWakeActive(true);
          } else {
            void wl.release();
          }
        })
        .catch(() => setWakeActive(false));
    }

    connectCall(patienceMs);

    return () => {
      alive = false;
      stopAudioPipeline();
      try {
        wsRef.current?.send(JSON.stringify({ kind: "end_call" }));
        wsRef.current?.close();
      } catch {
        /* noop */
      }
      wsRef.current = null;
      if (wakeLockRef.current) {
        void wakeLockRef.current.release().catch(() => undefined);
        wakeLockRef.current = null;
      }
      setWakeActive(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, threadId]);

  if (!open) return null;

  const chooseModel = async (provider: string, model: string) => {
    try {
      const next = await api.setCallConfig(provider, model);
      setCaps(next);
      setShowConfig(false);
      connectCall(patienceMs);
    } catch {
      /* ignore */
    }
  };

  return (
    <div
      className="fixed inset-0 z-[75] flex flex-col bg-bg/98 backdrop-blur-md"
      role="dialog"
      aria-modal="true"
      aria-label={t("Live Call")}
    >
      <header className="flex items-center justify-between border-b border-border/70 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[16px] font-semibold">{agentName || "MiniMuse"}</span>
            <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted">
              {caps?.native_realtime ? t("Realtime Voice") : t("Hands-Free Call")}
            </span>
          </div>
          <div className="text-[11.5px] text-muted truncate">
            {caps?.model || t("Current model")}
            {wakeActive ? ` · ${t("Screen stays on")}` : ""}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowConfig((s) => !s)}
            aria-label={t("Call settings")}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-fg/80 hover:text-fg"
          >
            <Settings2 size={17} />
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("Close")}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-fg/80 hover:text-fg"
          >
            <X size={18} />
          </button>
        </div>
      </header>

      {showConfig && (
        <div className="border-b border-border/70 bg-surface px-4 py-3 space-y-2.5 text-[13px]">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted font-medium">{t("Turn pause patience:")}</span>
            {[
              { ms: 3500, label: t("Patient (3.5s)") },
              { ms: 2000, label: t("Balanced (2s)") },
              { ms: 0, label: t("Manual Done") },
            ].map((opt) => (
              <button
                key={opt.ms}
                type="button"
                onClick={() => setPatienceMs(opt.ms)}
                className={cx(
                  "rounded-full px-3 py-1 font-medium",
                  patienceMs === opt.ms ? "bg-fg text-bg" : "bg-surface-2 text-fg/80",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted font-medium">{t("Call model:")}</span>
            <button
              type="button"
              onClick={() => void chooseModel("", "")}
              className={cx(
                "rounded-full px-3 py-1 font-medium",
                !caps?.override_active ? "bg-accent text-white" : "bg-surface-2 text-fg/80",
              )}
            >
              {t("Follow Chat Model")}
            </button>
            {(caps?.available_realtime_options ?? []).map((opt) => (
              <button
                key={opt.model}
                type="button"
                onClick={() => void chooseModel(opt.provider, opt.model)}
                className={cx(
                  "rounded-full px-3 py-1 font-medium",
                  caps?.override_active && caps?.model === opt.model ? "bg-accent text-white" : "bg-surface-2 text-fg/80",
                )}
              >
                {opt.model}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        <div className="flex flex-col items-center py-4">
          <div
            className={cx(
              "flex h-24 w-24 items-center justify-center rounded-full border-4 transition-all",
              status === "listening" && "border-emerald-500 bg-emerald-500/12 text-emerald-500 scale-105",
              status === "speaking" && "border-accent bg-accent/15 text-accent scale-110",
              status === "thinking" && "border-amber-500 bg-amber-500/12 text-amber-500",
              (status === "muted" || status === "connecting" || status === "error") && "border-border bg-surface text-muted",
            )}
          >
            {status === "thinking" ? (
              <Loader2 size={34} className="animate-spin" />
            ) : muted ? (
              <MicOff size={34} />
            ) : (
              <Phone size={34} />
            )}
          </div>
          <div className="mt-3 text-[15px] font-semibold">
            {status === "connecting" && t("Connecting live call…")}
            {status === "listening" && t("Listening — take your time")}
            {status === "thinking" && t("Thinking…")}
            {status === "speaking" && t("Speaking…")}
            {status === "muted" && t("Microphone muted")}
            {status === "error" && t("Call interrupted")}
          </div>
          {errorText && <p className="mt-1 text-[12.5px] text-rose-500 text-center max-w-md">{errorText}</p>}
          {draftSpeech && (
            <div className="mt-3 max-w-md rounded-2xl border border-accent/30 bg-accent/8 px-3.5 py-2 text-[13.5px] text-center">
              <div>{draftSpeech}</div>
              <div className="mt-2 flex items-center justify-center gap-2">
                <button
                  type="button"
                  onClick={extendPatience}
                  className="rounded-full bg-surface-2 px-3 py-1 text-[12px] font-medium"
                >
                  {t("Still talking (+7s)")}
                </button>
                <button
                  type="button"
                  onClick={() => commitSpeech()}
                  className="inline-flex items-center gap-1 rounded-full bg-accent px-3 py-1 text-[12px] font-semibold text-white"
                >
                  <Check size={12} /> {t("Send now")}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="mx-auto max-w-xl space-y-2">
          {lines.map((ln) => (
            <div
              key={ln.id}
              className={cx(
                "rounded-2xl px-3.5 py-2 text-[13.5px] leading-snug",
                ln.role === "user" && "ml-8 bg-fg text-bg",
                ln.role === "assistant" && "mr-8 bg-surface border border-border/70",
                ln.role === "system" && "mx-auto text-center text-[12px] text-muted",
              )}
            >
              {ln.text}
            </div>
          ))}
        </div>
      </div>

      <footer className="border-t border-border/70 bg-surface px-4 py-3 space-y-2.5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!typedDraft.trim()) return;
            commitSpeech(typedDraft);
            setTypedDraft("");
          }}
          className="mx-auto flex max-w-xl items-center gap-2"
        >
          <input
            type="text"
            value={typedDraft}
            onChange={(e) => setTypedDraft(e.target.value)}
            placeholder={t("Or type a follow-up during the call…")}
            className="flex-1 rounded-full border border-border bg-bg px-3.5 py-2 text-[13.5px] outline-none focus:border-accent"
          />
          <button
            type="submit"
            disabled={!typedDraft.trim()}
            aria-label={t("Send")}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-white disabled:opacity-40"
          >
            <Send size={15} />
          </button>
        </form>

        <div className="flex items-center justify-center gap-4">
          <button
            type="button"
            onClick={() => {
              const next = !muted;
              setMuted(next);
              setStatus(next ? "muted" : "listening");
            }}
            className={cx(
              "flex h-12 w-12 items-center justify-center rounded-full transition",
              muted ? "bg-amber-500 text-white" : "bg-surface-2 text-fg",
            )}
            aria-label={muted ? t("Unmute") : t("Mute")}
          >
            {muted ? <MicOff size={20} /> : <Mic size={20} />}
          </button>

          <button
            type="button"
            onClick={() => {
              const next = !speakerOff;
              setSpeakerOff(next);
              if (next) window.speechSynthesis?.cancel?.();
            }}
            className={cx(
              "flex h-12 w-12 items-center justify-center rounded-full transition",
              speakerOff ? "bg-surface-2 text-muted" : "bg-surface-2 text-fg",
            )}
            aria-label={speakerOff ? t("Enable speaker") : t("Mute speaker")}
          >
            {speakerOff ? <VolumeX size={20} /> : <Volume2 size={20} />}
          </button>

          <button
            type="button"
            onClick={onClose}
            className="flex h-12 items-center gap-2 rounded-full bg-rose-600 px-6 font-semibold text-white shadow-md transition active:scale-95"
          >
            <PhoneOff size={18} /> {t("End Call")}
          </button>
        </div>
      </footer>
    </div>
  );
}
