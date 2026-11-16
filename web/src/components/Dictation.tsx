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
  // the settled text as it was last handed to the box, so a repeat of the same phrase is
  // recognised even when the engine re-sends it under a different index
  const settledRef = useRef("");
  // the mic is still wanted: the engine's own `onend` picks the recognition back up
  const wantedRef = useRef(false);
  const startRef = useRef<() => void>(() => {});
  const t = useT();

  const stop = useCallback(() => {
    wantedRef.current = false;
    rec.current?.stop();
  }, []);

  const start = useCallback(() => {
    const Cls = recognitionClass();
    if (!Cls) return;
    const r = new Cls();
    r.lang = navigator.language || "en-US";
    r.continuous = true;
    r.interimResults = true;
    finalsRef.current = [];
    r.onresult = (e) => {
      if (!wantedRef.current) return;
      let interim = "";
      // Every event walks the whole results array: a settled result is stored under its
      // own index, so the Android engine re-firing finalized results rewrites them where
      // they are instead of appending them twice. Each settled result still gets one
      // overlap pass (the engine repeats the last phrase when it restarts itself), and
      // what is still unsettled is the interim guess, trimmed of the words it repeats
      // from the settled text.
      for (let i = 0; i < e.results.length; i++) {
        const res = e.results[i];
        const text = res?.[0]?.transcript ?? "";
        if (res?.isFinal) finalsRef.current[i] = text;
        else interim = text;
      }
      const carried = carriedRef.current;
      let finalsText = "";
      for (const f of finalsRef.current) if (f) finalsText += stripOverlap(settledRef.current + finalsText, f);
      const settled = carried + finalsText;
      settledRef.current = settled;
      onText(settled, stripOverlap(settled, interim));
    };
    r.onerror = (e) => {
      const code = e.error || "";
      if (code === "aborted" || code === "no-speech") return;
      // a permission denial or a dead service would restart into the same wall: stop
      wantedRef.current = false;
      onError?.(
        code === "not-allowed" || code === "service-not-allowed"
          ? t("The microphone is not allowed. Allow it in the browser and try again.")
          : t("Voice input stopped ({code}).", { code }),
      );
    };
    r.onend = () => {
      rec.current = null;
      // the settled words of this pass join what the passes before it heard, each one
      // trimmed of what the passes before it already said
      let text = carriedRef.current;
      for (const f of finalsRef.current) if (f) text += stripOverlap(text, f);
      carriedRef.current = text;
      settledRef.current = text;
      finalsRef.current = [];
      if (!wantedRef.current) {
        setListening(false);
        return;
      }
      // the engine ended its session on a pause or after one phrase: pick it back up a
      // beat later (starting again in the same tick is rejected by some engines)
      setTimeout(() => {
        if (wantedRef.current) startRef.current?.();
      }, 150);
    };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      rec.current = null;
      setListening(false);
    }
  }, [onText, onError, t]);

  useEffect(() => {
    startRef.current = start;
  }, [start]);

  const toggle = useCallback(() => {
    if (wantedRef.current) stop();
    else {
      wantedRef.current = true;
      carriedRef.current = "";
      settledRef.current = "";
      finalsRef.current = [];
      start();
    }
  }, [start, stop]);

  useEffect(
    () => () => {
      wantedRef.current = false;
      rec.current?.abort();
    },
    [],
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
      className={cx(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition active:scale-95",
        listening ? "bg-accent/15 text-accent animate-pulse" : "text-fg/70 hover:bg-surface-2",
        className,
      )}
    >
      {listening ? <MicOff size={19} /> : <Mic size={19} />}
    </button>
  );
}
