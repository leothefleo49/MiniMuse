import {
  Camera,
  CameraOff,
  Check,
  Eye,
  Loader2,
  Mic,
  MicOff,
  Monitor,
  MonitorOff,
  PhoneOff,
  Send,
  Settings2,
  SwitchCamera,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  canAndroidScreenShare,
  captureAndroidScreenFrame,
  startAndroidScreenShare,
  stopAndroidScreenShare,
} from "../android";
import { api, getToken } from "../api";
import { dragonClipUrl, dragonUrl, isDragon, studioClipUrl, studioUrl } from "../avatars";
import { useT } from "../i18n";
import type { CallCapabilities, Profile } from "../types";
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

type ShareMode = "none" | "camera" | "screen";
type ScreenBackend = "webrtc" | "android" | "desktop" | null;

function readIsDarkTheme(): boolean {
  if (typeof document === "undefined") return false;
  return (
    document.documentElement.dataset.theme === "dark" ||
    document.documentElement.classList.contains("dark")
  );
}

export function LiveCallModal({
  open,
  threadId,
  agentName,
  profile = null,
  onClose,
}: {
  open: boolean;
  threadId: string;
  agentName: string;
  profile?: Profile | null;
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

  // Camera / Screen Share state
  const [shareMode, setShareMode] = useState<ShareMode>("none");
  const [facingMode, setFacingMode] = useState<"user" | "environment">("environment");
  const [screenBackend, setScreenBackend] = useState<ScreenBackend>(null);
  const [nativePreviewSrc, setNativePreviewSrc] = useState("");
  const [shareBusy, setShareBusy] = useState(false);
  const [shareNotice, setShareNotice] = useState("");

  // Live Avatar & Audio-Reactive Talking Energy state
  const [isDark, setIsDark] = useState<boolean>(readIsDarkTheme);
  const [avatarClipStage, setAvatarClipStage] = useState<0 | 1 | 2>(0); // 0=call clip, 1=idle clip, 2=still
  const [talkLevel, setTalkLevel] = useState(0);

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
  const analyserRef = useRef<AnalyserNode | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const nextPlayTimeRef = useRef(0);
  const ttsPulseRef = useRef(0);

  const callVideoRef = useRef<HTMLVideoElement | null>(null);
  const shareVideoRef = useRef<HTMLVideoElement | null>(null);
  const shareStreamRef = useRef<MediaStream | null>(null);
  const shareCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const shareTimerRef = useRef<number | null>(null);
  const shareModeRef = useRef<ShareMode>("none");
  const screenBackendRef = useRef<ScreenBackend>(null);
  const latestJpegRef = useRef<string>("");
  const nativeRealtimeRef = useRef(false);

  useEffect(() => {
    mutedRef.current = muted;
    speakerOffRef.current = speakerOff;
    patienceRef.current = patienceMs;
    statusRef.current = status;
  }, [muted, speakerOff, patienceMs, status]);

  useEffect(() => {
    shareModeRef.current = shareMode;
    screenBackendRef.current = screenBackend;
  }, [shareMode, screenBackend]);

  useEffect(() => {
    nativeRealtimeRef.current = !!caps?.native_realtime;
  }, [caps?.native_realtime]);

  useEffect(() => {
    if (!open || typeof document === "undefined" || typeof MutationObserver === "undefined") return;
    const root = document.documentElement;
    const update = () => setIsDark(readIsDarkTheme());
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => obs.disconnect();
  }, [open]);

  // Reset avatar clip fallback stage when profile or theme changes
  useEffect(() => {
    setAvatarClipStage(0);
  }, [profile?.avatar, profile?.updated_at, isDark, open]);

  // Attach call avatar <video> attributes synchronously so Android WebView always autoplays
  const attachCallVideoRef = useCallback((el: HTMLVideoElement | null) => {
    callVideoRef.current = el;
    if (el) {
      el.defaultMuted = true;
      el.muted = true;
      el.playsInline = true;
      el.setAttribute("muted", "");
      el.setAttribute("playsinline", "");
      el.setAttribute("webkit-playsinline", "");
    }
  }, []);

  const attachShareVideoRef = useCallback((el: HTMLVideoElement | null) => {
    shareVideoRef.current = el;
    if (el && shareStreamRef.current && el.srcObject !== shareStreamRef.current) {
      el.srcObject = shareStreamRef.current;
      void el.play().catch(() => undefined);
    }
  }, []);

  // Audio-reactive speaking envelope (reads real PCM16 AnalyserNode + TTS syllable pulse)
  useEffect(() => {
    if (!open || typeof window === "undefined" || typeof window.requestAnimationFrame !== "function") return;
    let alive = true;
    let rafId = 0;
    let lastTick = 0;
    const timeBuf = new Uint8Array(128);

    const loop = (now: number) => {
      if (!alive) return;
      if (now - lastTick >= 55) {
        lastTick = now;
        const st = statusRef.current;
        let level = 0;
        if (st === "speaking") {
          const analyser = analyserRef.current;
          if (analyser) {
            analyser.getByteTimeDomainData(timeBuf);
            let sum = 0;
            for (let i = 0; i < timeBuf.length; i++) {
              const v = (timeBuf[i] - 128) / 128;
              sum += v * v;
            }
            const rms = Math.sqrt(sum / timeBuf.length);
            level = Math.min(1, rms * 3.6);
          }
          if (level < 0.08) {
            // Smooth voice-cadence envelope for browser TTS (or quiet pauses between words)
            ttsPulseRef.current = Math.max(0.18, ttsPulseRef.current * 0.86);
            const wave =
              0.38 +
              0.24 * Math.sin(now * 0.014) +
              0.18 * Math.sin(now * 0.029 + 1.2) +
              0.2 * ttsPulseRef.current;
            level = Math.max(0.18, Math.min(1, wave));
          }
        }
        setTalkLevel(level);
        const cv = callVideoRef.current;
        if (cv && !cv.paused) {
          const targetRate = st === "speaking" ? 1.12 + level * 0.16 : st === "thinking" ? 0.92 : 1.0;
          if (Math.abs(cv.playbackRate - targetRate) > 0.04) {
            try {
              cv.playbackRate = targetRate;
            } catch {
              /* ignore unsupported playbackRate */
            }
          }
        }
      }
      rafId = window.requestAnimationFrame(loop);
    };
    rafId = window.requestAnimationFrame(loop);
    return () => {
      alive = false;
      window.cancelAnimationFrame(rafId);
    };
  }, [open]);

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

  /** Grab a compressed JPEG (base64 without data: prefix) from the active camera or screen share. */
  const captureShareFrame = useCallback(async (): Promise<string> => {
    const mode = shareModeRef.current;
    if (mode === "none") return "";

    if (mode === "screen" && screenBackendRef.current === "android") {
      const b64 = await captureAndroidScreenFrame();
      if (b64) {
        latestJpegRef.current = b64;
        setNativePreviewSrc(`data:image/jpeg;base64,${b64}`);
        return b64;
      }
      return latestJpegRef.current;
    }

    if (mode === "screen" && screenBackendRef.current === "desktop") {
      try {
        const res = await api.callScreenFrame();
        if (res.ok && res.jpeg) {
          latestJpegRef.current = res.jpeg;
          setNativePreviewSrc(`data:image/jpeg;base64,${res.jpeg}`);
          return res.jpeg;
        }
      } catch {
        /* keep last frame */
      }
      return latestJpegRef.current;
    }

    const vid = shareVideoRef.current;
    if (!vid || vid.readyState < 2 || !vid.videoWidth || !vid.videoHeight) {
      return latestJpegRef.current;
    }
    if (!shareCanvasRef.current && typeof document !== "undefined") {
      shareCanvasRef.current = document.createElement("canvas");
    }
    const canvas = shareCanvasRef.current;
    if (!canvas) return latestJpegRef.current;
    const maxW = mode === "screen" ? 800 : 640;
    const scale = Math.min(1, maxW / vid.videoWidth);
    const w = Math.max(160, Math.round(vid.videoWidth * scale));
    const h = Math.max(120, Math.round(vid.videoHeight * scale));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return latestJpegRef.current;
    try {
      ctx.drawImage(vid, 0, 0, w, h);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.72);
      const comma = dataUrl.indexOf(",");
      const b64 = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
      latestJpegRef.current = b64;
      return b64;
    } catch {
      return latestJpegRef.current;
    }
  }, []);

  const stopVisualShare = useCallback((notifyServer = true) => {
    if (shareTimerRef.current !== null) {
      window.clearInterval(shareTimerRef.current);
      shareTimerRef.current = null;
    }
    try {
      shareStreamRef.current?.getTracks().forEach((tr) => tr.stop());
    } catch {
      /* noop */
    }
    shareStreamRef.current = null;
    if (shareVideoRef.current) {
      shareVideoRef.current.srcObject = null;
    }
    if (screenBackendRef.current === "android") {
      stopAndroidScreenShare();
    }
    latestJpegRef.current = "";
    setNativePreviewSrc("");
    setScreenBackend(null);
    setShareMode("none");
    setShareNotice("");
    if (notifyServer && wsRef.current?.readyState === WebSocket.OPEN) {
      try {
        wsRef.current.send(JSON.stringify({ kind: "video_stop" }));
      } catch {
        /* noop */
      }
    }
  }, []);

  const startFramePump = useCallback(
    (source: "camera" | "screen") => {
      if (shareTimerRef.current !== null) {
        window.clearInterval(shareTimerRef.current);
      }
      const sendOne = async () => {
        if (shareModeRef.current !== source) return;
        const jpeg = await captureShareFrame();
        if (jpeg && wsRef.current?.readyState === WebSocket.OPEN) {
          try {
            wsRef.current.send(JSON.stringify({ kind: "video_frame", source, jpeg }));
          } catch {
            /* ignore transient ws error */
          }
        }
      };
      // Send an initial frame right after video warms up, then keep streaming smoothly
      window.setTimeout(() => void sendOne(), 450);
      const intervalMs = nativeRealtimeRef.current ? 1500 : 2200;
      shareTimerRef.current = window.setInterval(() => {
        void sendOne();
      }, intervalMs);
    },
    [captureShareFrame],
  );

  const startCamera = useCallback(
    async (facing: "user" | "environment") => {
      stopVisualShare(false);
      setShareBusy(true);
      setShareNotice("");
      setErrorText("");
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error(t("Camera is unavailable on this device."));
        }
        let stream: MediaStream;
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: facing }, width: { ideal: 640 }, height: { ideal: 480 } },
            audio: false,
          });
        } catch {
          stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        }
        shareStreamRef.current = stream;
        setFacingMode(facing);
        setScreenBackend("webrtc");
        setShareMode("camera");
        shareModeRef.current = "camera";
        screenBackendRef.current = "webrtc";
        stream.getVideoTracks().forEach((tr) => {
          tr.onended = () => stopVisualShare(true);
        });
        if (shareVideoRef.current) {
          shareVideoRef.current.srcObject = stream;
          void shareVideoRef.current.play().catch(() => undefined);
        }
        startFramePump("camera");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setErrorText(t("Could not open camera: {error}", { error: msg }));
      } finally {
        setShareBusy(false);
      }
    },
    [startFramePump, stopVisualShare, t],
  );

  const startScreenShare = useCallback(async () => {
    stopVisualShare(false);
    setShareBusy(true);
    setShareNotice("");
    setErrorText("");
    try {
      // 1. Native Android phone screen sharing (Accessibility fast path or Android MediaProjection)
      if (canAndroidScreenShare()) {
        const res = await startAndroidScreenShare();
        if (!res.active) {
          throw new Error(res.error || t("Screen sharing was cancelled."));
        }
        setScreenBackend("android");
        setShareMode("screen");
        shareModeRef.current = "screen";
        screenBackendRef.current = "android";
        setShareNotice(
          res.mode === "accessibility"
            ? t("Sharing your phone screen (even when you switch apps)")
            : t("Sharing your phone screen"),
        );
        startFramePump("screen");
        return;
      }

      // 2. Standard browser / WebView getDisplayMedia
      if (navigator.mediaDevices && typeof navigator.mediaDevices.getDisplayMedia === "function") {
        try {
          const stream = await navigator.mediaDevices.getDisplayMedia({
            video: { frameRate: { ideal: 2, max: 5 } },
            audio: false,
          });
          shareStreamRef.current = stream;
          setScreenBackend("webrtc");
          setShareMode("screen");
          shareModeRef.current = "screen";
          screenBackendRef.current = "webrtc";
          stream.getVideoTracks().forEach((tr) => {
            tr.onended = () => stopVisualShare(true);
          });
          if (shareVideoRef.current) {
            shareVideoRef.current.srcObject = stream;
            void shareVideoRef.current.play().catch(() => undefined);
          }
          startFramePump("screen");
          return;
        } catch (err) {
          const name = err instanceof Error ? err.name : "";
          if (name === "NotAllowedError" || name === "AbortError") {
            throw err;
          }
          // Fall through to desktop backend screen capture if WebView2 lacks getDisplayMedia
        }
      }

      // 3. Desktop app fallback: local host screen capture (/api/call/screen-frame)
      const probe = await api.callScreenFrame();
      if (!probe.ok || !probe.jpeg) {
        throw new Error(probe.error || t("Screen sharing is not supported in this browser."));
      }
      latestJpegRef.current = probe.jpeg;
      setNativePreviewSrc(`data:image/jpeg;base64,${probe.jpeg}`);
      setScreenBackend("desktop");
      setShareMode("screen");
      shareModeRef.current = "screen";
      screenBackendRef.current = "desktop";
      setShareNotice(t("Sharing your desktop screen"));
      startFramePump("screen");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorText(t("Screen share unavailable: {error}", { error: msg }));
    } finally {
      setShareBusy(false);
    }
  }, [startFramePump, stopVisualShare, t]);

  // Listen if Android stops screen share from the system notification bar
  useEffect(() => {
    if (!open || typeof window === "undefined") return;
    const onState = (ev: Event) => {
      const detail = (ev as CustomEvent<{ active?: boolean; error?: string }>).detail;
      if (detail && detail.active === false && shareModeRef.current === "screen" && screenBackendRef.current === "android") {
        stopVisualShare(true);
        if (detail.error) setErrorText(detail.error);
      }
    };
    window.addEventListener("nanomuse:screen-share-state", onState);
    return () => window.removeEventListener("nanomuse:screen-share-state", onState);
  }, [open, stopVisualShare]);

  const commitSpeech = useCallback(
    async (forceText?: string) => {
      clearFlushTimer();
      const text = (forceText ?? spokenBufRef.current).trim();
      spokenBufRef.current = "";
      setDraftSpeech("");
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

      const activeShare = shareModeRef.current;
      let jpeg = "";
      if (activeShare !== "none") {
        jpeg = await captureShareFrame();
      }
      if (!text && !jpeg) return;

      const display = text || (activeShare === "screen" ? t("Look at my screen") : t("Look at my camera"));
      appendLine("user", jpeg ? `${display} 📷` : display);
      setStatus("thinking");
      wsRef.current.send(
        JSON.stringify({
          kind: "user_text",
          text,
          language: navigator.language || "en",
          ...(jpeg ? { jpeg, source: activeShare } : {}),
        }),
      );
    },
    [appendLine, captureShareFrame, t],
  );

  const scheduleFlush = useCallback(() => {
    clearFlushTimer();
    const wait = patienceRef.current;
    if (wait <= 0) return; // Manual Done mode: wait for user tap
    flushTimerRef.current = window.setTimeout(() => {
      void commitSpeech();
    }, wait);
  }, [commitSpeech]);

  const extendPatience = () => {
    clearFlushTimer();
    if (patienceRef.current > 0) {
      flushTimerRef.current = window.setTimeout(() => {
        void commitSpeech();
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
      u.onboundary = () => {
        ttsPulseRef.current = 1.0;
      };
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
      if (!analyserRef.current) {
        const an = ctx.createAnalyser();
        an.fftSize = 256;
        an.connect(ctx.destination);
        analyserRef.current = an;
      }
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
      src.connect(analyserRef.current);
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
    analyserRef.current = null;
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
      stopVisualShare(false);
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

  // Resolve the AI avatar media to display on the Live Call stage
  const isEmoji = profile?.avatar === "";
  const dragon = !profile || isDragon(profile.avatar);
  const studioFace = !isEmoji && !dragon && profile?.avatar ? profile.avatar : null;
  const animationsOn = profile?.avatar_animated !== false;

  const activeCallVideoUrl = (() => {
    if (isEmoji || !animationsOn || avatarClipStage === 2) return null;
    if (dragon) return dragonClipUrl("idle", profile?.avatar_situation_clips);
    if (studioFace) {
      if (avatarClipStage === 0) {
        return studioClipUrl(studioFace, "call", isDark, profile?.updated_at, profile?.avatar_situation_clips);
      }
      return studioClipUrl(studioFace, "idle", false, profile?.updated_at, undefined);
    }
    return null;
  })();

  const activeCallStillUrl = dragon
    ? dragonUrl("idle")
    : studioFace
      ? studioUrl(studioFace, "idle", false, profile?.updated_at)
      : "";

  const isPortraitStage = !!studioFace && avatarClipStage === 0;
  const accentColor =
    profile?.avatar_border_mode === "color" && profile?.avatar_border_color
      ? profile.avatar_border_color
      : profile?.color || "var(--accent, #6366f1)";
  const speakingGlowPx = status === "speaking" ? Math.round(18 + talkLevel * 28) : status === "listening" ? 16 : 10;
  const speakingScale = status === "speaking" ? 1 + talkLevel * 0.025 : 1;

  return (
    <div
      className="fixed inset-0 z-[75] flex flex-col bg-bg/98 backdrop-blur-md"
      style={{ paddingTop: "var(--nm-safe-top, 0px)" }}
      role="dialog"
      aria-modal="true"
      aria-label={t("Live Call")}
    >
      <header className="flex items-center justify-between border-b border-border/70 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[16px] font-semibold truncate">{agentName || "MiniMuse"}</span>
            <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted shrink-0">
              {caps?.native_realtime ? t("Realtime Voice") : t("Hands-Free Call")}
            </span>
          </div>
          <div className="text-[11.5px] text-muted truncate">
            {caps?.model || t("Current model")}
            {wakeActive ? ` · ${t("Screen stays on")}` : ""}
            {shareMode === "camera" ? ` · ${t("Camera on")}` : ""}
            {shareMode === "screen" ? ` · ${t("Screen shared")}` : ""}
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
          <p className="text-[11.5px] text-muted">
            {t("Avatar uses your dedicated Call animation with voice-reactive speaking motion. Turn on Camera or Screen Share below so MiniMuse can see what you're looking at.")}
          </p>
        </div>
      )}

      <div className="relative flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {/* Live Camera / Screen Share PiP Card */}
        {shareMode !== "none" && (
          <div className="mx-auto max-w-md overflow-hidden rounded-2xl border border-border/80 bg-surface shadow-lg">
            <div className="relative aspect-video w-full bg-black/90 flex items-center justify-center overflow-hidden">
              {screenBackend === "webrtc" ? (
                <video
                  ref={attachShareVideoRef}
                  autoPlay
                  muted
                  playsInline
                  className={cx(
                    "h-full w-full",
                    shareMode === "camera" ? "object-cover" : "object-contain",
                    shareMode === "camera" && facingMode === "user" && "-scale-x-100",
                  )}
                />
              ) : nativePreviewSrc ? (
                <img
                  src={nativePreviewSrc}
                  alt={t("Shared screen preview")}
                  className="h-full w-full object-contain"
                />
              ) : (
                <div className="flex flex-col items-center gap-1.5 text-white/75 text-[12.5px]">
                  <Loader2 size={20} className="animate-spin" />
                  <span>{t("Capturing screen…")}</span>
                </div>
              )}

              <div className="absolute left-2.5 top-2.5 flex items-center gap-1.5 rounded-full bg-black/65 px-2.5 py-1 text-[11px] font-semibold text-white backdrop-blur-sm">
                <span className="h-2 w-2 rounded-full bg-rose-500 animate-pulse" />
                {shareMode === "camera" ? t("LIVE CAMERA") : t("SHARING SCREEN")}
              </div>

              <div className="absolute right-2.5 top-2.5 flex items-center gap-1.5">
                {shareMode === "camera" && (
                  <button
                    type="button"
                    onClick={() => void startCamera(facingMode === "user" ? "environment" : "user")}
                    aria-label={t("Flip camera")}
                    title={t("Flip camera")}
                    className="flex h-8 w-8 items-center justify-center rounded-full bg-black/65 text-white hover:bg-black/80 backdrop-blur-sm"
                  >
                    <SwitchCamera size={15} />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => stopVisualShare(true)}
                  aria-label={t("Stop sharing")}
                  title={t("Stop sharing")}
                  className="flex h-8 w-8 items-center justify-center rounded-full bg-black/65 text-white hover:bg-rose-600 backdrop-blur-sm"
                >
                  <X size={15} />
                </button>
              </div>
            </div>
            <div className="flex items-center justify-between gap-2 px-3 py-2 text-[12px]">
              <span className="text-muted truncate">
                {shareNotice || (shareMode === "camera" ? t("MiniMuse can see your camera") : t("MiniMuse can see your screen"))}
              </span>
              <button
                type="button"
                onClick={() => void commitSpeech("")}
                className="inline-flex shrink-0 items-center gap-1 rounded-full bg-accent/15 px-2.5 py-1 font-medium text-accent hover:bg-accent/25"
              >
                <Eye size={13} /> {t("Look now")}
              </button>
            </div>
          </div>
        )}

        {/* AI Avatar Live Call Stage */}
        <div className="flex flex-col items-center py-2">
          <div
            className={cx(
              "relative overflow-hidden transition-transform duration-150",
              isPortraitStage
                ? shareMode !== "none"
                  ? "h-[196px] w-[134px] rounded-[24px]"
                  : "h-[264px] w-[178px] sm:h-[310px] sm:w-[208px] rounded-[28px]"
                : shareMode !== "none"
                  ? "h-32 w-32 rounded-full"
                  : "h-44 w-44 sm:h-52 sm:w-52 rounded-full",
            )}
            style={{
              transform: `scale(${speakingScale.toFixed(3)})`,
              background: isDark
                ? "radial-gradient(circle at 50% 35%, #232632, #0d0e13)"
                : "radial-gradient(circle at 50% 35%, #ffffff, #ebe7e0)",
              boxShadow:
                status === "speaking"
                  ? `0 0 ${speakingGlowPx}px ${Math.round(3 + talkLevel * 6)}px color-mix(in srgb, ${accentColor} ${Math.round(45 + talkLevel * 40)}%, transparent)`
                  : status === "listening"
                    ? "0 0 18px 2px rgba(16, 185, 129, 0.35)"
                    : status === "thinking"
                      ? "0 0 18px 2px rgba(245, 158, 11, 0.35)"
                      : "0 8px 24px rgba(0,0,0,0.16)",
              border: `2px solid ${
                status === "speaking"
                  ? `color-mix(in srgb, ${accentColor} 85%, white)`
                  : status === "listening"
                    ? "rgba(16, 185, 129, 0.7)"
                    : status === "thinking"
                      ? "rgba(245, 158, 11, 0.7)"
                      : "var(--border, rgba(148,163,184,0.35))"
              }`,
            }}
          >
            {activeCallVideoUrl ? (
              <video
                ref={attachCallVideoRef}
                key={activeCallVideoUrl}
                src={activeCallVideoUrl}
                poster={activeCallStillUrl}
                autoPlay
                loop
                muted
                playsInline
                preload="auto"
                disablePictureInPicture
                disableRemotePlayback
                onLoadedMetadata={(e) => {
                  const v = e.currentTarget;
                  v.defaultMuted = true;
                  v.muted = true;
                  void v.play().catch(() => undefined);
                }}
                onError={() => {
                  setAvatarClipStage((prev) => (prev === 0 ? 1 : 2));
                }}
                className="h-full w-full object-cover"
              />
            ) : !isEmoji && activeCallStillUrl ? (
              <img
                src={activeCallStillUrl}
                alt={agentName || "MiniMuse"}
                className="h-full w-full object-cover"
                draggable={false}
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center text-[64px]">
                {profile?.emoji || "✨"}
              </div>
            )}

            {/* Subtle voice-reactive audio bars overlaid cleanly at the bottom edge of the stage */}
            <div className="pointer-events-none absolute inset-x-0 bottom-2.5 flex justify-center">
              <div className="flex items-end gap-1 rounded-full bg-black/50 px-2.5 py-1.5 backdrop-blur-sm">
                {status === "thinking" ? (
                  <Loader2 size={14} className="animate-spin text-amber-300" />
                ) : status === "muted" ? (
                  <MicOff size={14} className="text-amber-300" />
                ) : (
                  [0.55, 0.9, 1.0, 0.85, 0.5].map((weight, i) => {
                    const activeEnergy =
                      status === "speaking"
                        ? Math.max(0.25, talkLevel * weight)
                        : status === "listening"
                          ? 0.28 + 0.14 * Math.sin((i + 1) * 1.4)
                          : 0.2;
                    const hPx = Math.round(4 + activeEnergy * 14);
                    return (
                      <span
                        key={i}
                        className={cx(
                          "w-1 rounded-full transition-all duration-75",
                          status === "speaking"
                            ? "bg-white"
                            : status === "listening"
                              ? "bg-emerald-400"
                              : "bg-white/50",
                        )}
                        style={{ height: `${hPx}px` }}
                      />
                    );
                  })
                )}
              </div>
            </div>
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
                  onClick={() => void commitSpeech()}
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
            void commitSpeech(typedDraft);
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

        <div className="flex flex-wrap items-center justify-center gap-3">
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
            title={muted ? t("Unmute") : t("Mute")}
          >
            {muted ? <MicOff size={20} /> : <Mic size={20} />}
          </button>

          <button
            type="button"
            disabled={shareBusy}
            onClick={() => {
              if (shareMode === "camera") {
                stopVisualShare(true);
              } else {
                void startCamera(facingMode);
              }
            }}
            className={cx(
              "flex h-12 w-12 items-center justify-center rounded-full transition disabled:opacity-50",
              shareMode === "camera" ? "bg-accent text-white shadow-md" : "bg-surface-2 text-fg",
            )}
            aria-label={shareMode === "camera" ? t("Turn off camera") : t("Turn on camera")}
            title={shareMode === "camera" ? t("Turn off camera") : t("Turn on camera")}
          >
            {shareMode === "camera" ? <CameraOff size={20} /> : <Camera size={20} />}
          </button>

          <button
            type="button"
            disabled={shareBusy}
            onClick={() => {
              if (shareMode === "screen") {
                stopVisualShare(true);
              } else {
                void startScreenShare();
              }
            }}
            className={cx(
              "flex h-12 w-12 items-center justify-center rounded-full transition disabled:opacity-50",
              shareMode === "screen" ? "bg-accent text-white shadow-md" : "bg-surface-2 text-fg",
            )}
            aria-label={shareMode === "screen" ? t("Stop screen share") : t("Share screen")}
            title={shareMode === "screen" ? t("Stop screen share") : t("Share screen")}
          >
            {shareMode === "screen" ? <MonitorOff size={20} /> : <Monitor size={20} />}
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
            title={speakerOff ? t("Enable speaker") : t("Mute speaker")}
          >
            {speakerOff ? <VolumeX size={20} /> : <Volume2 size={20} />}
          </button>

          <button
            type="button"
            onClick={onClose}
            className="flex h-12 items-center gap-2 rounded-full bg-rose-600 px-5 font-semibold text-white shadow-md transition active:scale-95"
          >
            <PhoneOff size={18} /> {t("End Call")}
          </button>
        </div>
      </footer>
    </div>
  );
}
