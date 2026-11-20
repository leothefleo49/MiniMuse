/**
 * The agent's face. Muse gives its agent a plush doll; MiniMuse has its dragon — five stills,
 * one per mood (public/avatars/dragon-*.webp) and four short clips that move them (idle,
 * working, waiting, happy — dragon-*.mp4, the same clips the Android app plays) — or a face the
 * person had drawn for them in the avatar studio (served from the runtime's files, stills and,
 * where the video model made them, clips), or an emoji on a colour for anyone who prefers it.
 * `profile.avatar` names the face; "" means the emoji.
 */

import { fileUrl } from "./api";

/** The dragon: the default face. */
export const DRAGON = "dragon";
export type DragonMood = "idle" | "working" | "waiting" | "happy" | "error";

/**
 * Faces from before 0.1.23 — the red panda drawn live and the six knitted dolls — are gone;
 * a profile still naming one wears the dragon.
 */
const RETIRED = new Set(["panda", "sunny", "moss", "sky", "fox", "bolt", "plum"]);

export function isDragon(id: string | undefined | null): boolean {
  return !id || id === DRAGON || RETIRED.has(id);
}

function withCacheBust(url: string, updatedAt?: number): string {
  if (!updatedAt || updatedAt <= 0) return url;
  const v = Math.floor(updatedAt);
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}v=${v}`;
}

/**
 * A face from the avatar studio: `profile.avatar` is its id, the stills live in the workspace
 * under `avatar/<id>/` (idle · working · waiting · happy · error, webp) and come through the
 * files API — the same five moods the dragon has.
 * Every device always uses the canonical pose file so camera angles and animations match 100%.
 */
export function studioUrl(id: string, mood: string, _dark = false, updatedAt?: number): string {
  const still: DragonMood = mood === "working" || mood === "waiting" || mood === "happy" || mood === "error" ? mood : "idle";
  return withCacheBust(fileUrl(`avatar/${id}/${still}.webp`), updatedAt);
}

export function dragonUrl(mood: string): string {
  const still: DragonMood = mood === "working" || mood === "waiting" || mood === "happy" || mood === "error" ? mood : "idle";
  return `/avatars/dragon-${still}.webp`;
}

/** The moods that move; "error" is a still, on the phone too. */
export type ClipMood = "idle" | "working" | "waiting" | "happy";

function clipMood(mood: string): ClipMood {
  return mood === "working" || mood === "waiting" || mood === "happy" ? mood : "idle";
}

/** Built-in Dragon 3D animation clips so users can preview & switch Dragon animations per situation too. */
export const DRAGON_CLIPS: ReadonlyArray<{ file: string; path: string; kind: "video" | "image"; label: string }> = [
  { file: "dragon-idle.mp4", path: "/avatars/dragon-idle.mp4", kind: "video", label: "Idle · Sway" },
  { file: "dragon-working.mp4", path: "/avatars/dragon-working.mp4", kind: "video", label: "Working · Laptop" },
  { file: "dragon-waiting.mp4", path: "/avatars/dragon-waiting.mp4", kind: "video", label: "Waiting · Crystal Ball" },
  { file: "dragon-happy.mp4", path: "/avatars/dragon-happy.mp4", kind: "video", label: "Happy · Golden Star" },
  { file: "dragon-error.webp", path: "/avatars/dragon-error.webp", kind: "image", label: "Sorry / Alert (still)" },
];

/** The dragon's clip for a mood — a few seconds, looping, back in the starting pose at the end. */
export function dragonClipUrl(mood: string, situationClips?: Record<string, string>): string | null {
  const custom = situationClips?.[mood] || (mood !== "error" ? situationClips?.[clipMood(mood)] : undefined);
  if (custom) {
    const clean = custom.trim();
    const hit = DRAGON_CLIPS.find((c) => c.file === clean || c.path === clean);
    if (hit) return hit.kind === "video" ? hit.path : null;
    if (clean.startsWith("/avatars/") && clean.endsWith(".mp4")) return clean;
    if (clean.endsWith(".webp") || clean.endsWith(".png")) return null;
  }
  if (mood === "error") return null;
  return `/avatars/dragon-${clipMood(mood)}.mp4`;
}

/**
 * A studio face's clip for a mood, `avatar/<id>/<mood>.mp4` through the files API. The studio
 * makes them after the stills when its endpoint has a video model; a face may have none, in
 * which case the request fails and the still stays (see Avatar).
 */
export function studioClipUrl(
  id: string,
  mood: string,
  dark = false,
  updatedAt?: number,
  situationClips?: Record<string, string>,
): string | null {
  const customRaw = situationClips?.[mood] || (mood !== "error" && mood !== "call" ? situationClips?.[clipMood(mood)] : undefined);
  // Guard against stale polluted 7x12 portrait call clips saved on non-call moods by older builds
  const isAccidentalCall =
    mood !== "call" &&
    !!customRaw &&
    customRaw.toLowerCase().includes("call-") &&
    customRaw.toLowerCase().includes("7x12");
  const custom = isAccidentalCall ? undefined : customRaw;
  if (custom) {
    const lower = custom.toLowerCase();
    if (lower.endsWith(".mp4") || lower.endsWith(".webm")) {
      return withCacheBust(fileUrl(`avatar/${id}/${custom}`), updatedAt);
    }
    if (lower.endsWith(".webp") || lower.endsWith(".png") || lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".gif")) {
      return null;
    }
  }
  if (mood === "error") return null;
  if (mood === "call") {
    return withCacheBust(fileUrl(`avatar/${id}/${dark ? "dark-call.mp4" : "call.mp4"}`), updatedAt);
  }
  return withCacheBust(fileUrl(`avatar/${id}/${clipMood(mood)}.mp4`), updatedAt);
}
