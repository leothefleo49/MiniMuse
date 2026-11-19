import { Mic, MicOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "../i18n";
import { cx } from "../util";

/* Voice input for the composer: the browser's own speech recognition (Web Speech API),
 * where it exists — Chrome, Edge, Safari, the Android WebView; nothing is sent by us and no
 * model is involved. One tap starts listening, the words land in the box as they are
 * recognised, a second tap (or silence) stops. Browsers without it show no button. */

interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error?: string }) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

function recognitionClass(): (new () => RecognitionLike) | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: new () => RecognitionLike; webkitSpeechRecognition?: new () => RecognitionLike };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function dictationSupported(): boolean {
  return recognitionClass() !== null;
}

/**
 * What the engine replays gets trimmed. The Android engine (and Chrome's) re-emits a
 * result it already settled, and the live guess repeats the settled words until it moves
 * on, so a phrase can arrive two or three times over. `settled` is what the box already
 * shows; `addition` is the text that may begin with those same words. The overlap is cut
 * at a word boundary — the addition starts with a run of whole words the settled text
 * already ends with — so "I said when I'm doing" then "I said when I'm doing speech to
 * text" keeps only "speech to text". A single repeated word is left alone unless it is
 * the whole addition, because "it" then "it went well" is a real sentence.
 */
function stripOverlap(settled: string, addition: string): string {
  const add = addition.trimStart();
  if (!add) return "";
  const tail = settled.trimEnd().slice(-600);
  if (!tail) return add;
  const words = tail.split(/\s+/);
  const addWords = add.split(/\s+/);
  // longest run of whole words at the head of the addition that the tail ends with
  const maxRun = Math.min(words.length, addWords.length);
  for (let n = maxRun; n >= 1; n--) {
    const head = addWords.slice(0, n).join(" ");
    const tailRun = words.slice(words.length - n).join(" ");
    const same = head.replace(/[.,!?;:]+$/, "").toLowerCase() === tailRun.replace(/[.,!?;:]+$/, "").toLowerCase();
    if (!same) continue;
    // a lone repeated word only counts when it is all there is (the engine said nothing new)
    if (n === 1 && addWords.length > 1) continue;
    const rest = addWords.slice(n).join(" ");
    return rest ? rest + (/\s$/.test(add) ? " " : "") : "";
  }
  // no word-boundary overlap: the engine may still repeat a partial word ("speech to te" | "text")
  if (tail.endsWith(add.trimEnd())) return "";
  return add;
}

function joinSpoken(base: string, chunk: string): string {
  const b = base.trimEnd();
  const c = chunk.trimStart();
  if (!c) return b;
  if (!b) return c;
  // Don't insert a space before punctuation or CJK characters
  if (/^[.,!?;:)\]]/.test(c) || /[\u4e00-\u9fff\u3040-\u30ff]$/.test(b)) {
    return `${b}${c}`;
  }
  return `${b} ${c}`;
}

/** `onText(final, interim)`: the words recognised so far in this session — `final` is settled,
 * `interim` is the browser's current guess for the phrase still being spoken. */
export function useDictation(onText: (final: string, interim: string) => void, onError?: (message: string) => void) {
  const [listening, setListening] = useState(false);
  const rec = useRef<RecognitionLike | null>(null);
  // the settled words: `carried` holds what earlier passes of a listening session heard
  // (the engine restarts itself), `finals` the settled results of the current pass, kept
  // by index so a re-emitted result overwrites its own entry instead of doubling it
  const carriedRef = useRef("");
  const finalsRef = useRef<string[]>([]);
  // the mic is still wanted: the engine's own `onend` picks the recognition back up
  const wantedRef = useRef(false);
  const restartTimerRef = useRef<number | null>(null);
  const startRef = useRef<() => void>(() => {});
  const t = useT();

  const clearRestartTimer = useCallback(() => {
    if (restartTimerRef.current !== null) {
      window.clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    wantedRef.current = false;
    clearRestartTimer();
    try {
      rec.current?.stop();
    } catch {
      /* already stopped */
    }
    setListening(false);
  }, [clearRestartTimer]);

  const start = useCallback(() => {
    const Cls = recognitionClass();
    if (!Cls) return;
    clearRestartTimer();
    const r = new Cls();
    r.lang = navigator.language || "en-US";
    r.continuous = true;
    r.interimResults = true;
    finalsRef.current = [];
    r.onresult = (e) => {
      if (!wantedRef.current) return;
      let interim = "";
      for (let i = 0; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res?.[0]?.transcript ?? "";
        if (res?.isFinal) finalsRef.current[i] = text;
        else if (text) interim = joinSpoken(interim, text);
      }
      // Build settled text deterministically from `carried` + this pass's `finalsRef`,
      // stripping only true overlap against the text built so far (never against a stale
      // snapshot of itself, which would drop previously finalized words).
      let settled = carriedRef.current;
      for (const f of finalsRef.current) {
        if (!f) continue;
        const clean = stripOverlap(settled, f);
        if (clean) settled = joinSpoken(settled, clean);
      }
      const cleanInterim = stripOverlap(settled, interim);
      onText(settled, cleanInterim);
    };
    r.onerror = (e) => {
      const code = e.error || "";
      if (code === "aborted" || code === "no-speech") return;
      // a permission denial or a dead service would restart into the same wall: stop
      wantedRef.current = false;
      clearRestartTimer();
      setListening(false);
      onError?.(
        code === "not-allowed" || code === "service-not-allowed"
          ? t("The microphone is not allowed. Allow it in the browser and try again.")
          : code === "audio-capture"
            ? t("No microphone was found. Check your mic connection and try again.")
            : code === "network"
              ? t("Voice input needs an internet connection for speech recognition.")
              : t("Voice input stopped ({code}).", { code }),
      );
    };
    r.onend = () => {
      rec.current = null;
      let text = carriedRef.current;
      for (const f of finalsRef.current) {
        if (!f) continue;
        const clean = stripOverlap(text, f);
        if (clean) text = joinSpoken(text, clean);
      }
      carriedRef.current = text;
      finalsRef.current = [];
      if (!wantedRef.current) {
        setListening(false);
        return;
      }
      restartTimerRef.current = window.setTimeout(() => {
        restartTimerRef.current = null;
        if (wantedRef.current) startRef.current?.();
      }, 160);
    };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      rec.current = null;
      setListening(false);
    }
  }, [clearRestartTimer, onText, onError, t]);

  useEffect(() => {
    startRef.current = start;
  }, [start]);

  const toggle = useCallback(() => {
    if (wantedRef.current) stop();
    else {
      wantedRef.current = true;
      carriedRef.current = "";
      finalsRef.current = [];
      start();
    }
  }, [start, stop]);

  useEffect(
    () => () => {
      wantedRef.current = false;
      clearRestartTimer();
      rec.current?.abort();
    },
    [clearRestartTimer],
  );

  return { supported: dictationSupported(), listening, toggle, stop };
}

export function MicButton({ listening, onClick, className }: { listening: boolean; onClick: () => void; className?: string }) {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={listening ? t("Stop voice input") : t("Voice input")}
      aria-pressed={listening}
      title={listening ? t("Stop voice input") : t("Voice input")}
      className={cx(
        "relative flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition active:scale-95",
        listening ? "bg-rose-500/15 text-rose-500 ring-2 ring-rose-500/40" : "text-fg/70 hover:bg-surface-2",
        className,
      )}
    >
      {listening && <span className="pointer-events-none absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-rose-500 animate-ping" />}
      {listening ? <MicOff size={19} /> : <Mic size={19} />}
    </button>
  );
}
