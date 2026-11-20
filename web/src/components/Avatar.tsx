import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { fileUrl } from "../api";
import { dragonClipUrl, dragonUrl, hasCustomClipOverride, isDragon, studioClipUrl, studioUrl } from "../avatars";
import { recordDebugEvent } from "../debugLog";
import { useT } from "../i18n";
import { useMood } from "../mood";
import type { Profile, Status } from "../types";
import { cx } from "../util";

/**
 * The agent's face: the dragon (posed by what the agent is doing), a face drawn in the avatar
 * studio or an emoji on a colour. The dragon and a studio face play a short looping clip per
 * mood where there is one — and show the still otherwise. Supports:
 * - Avatar background: Match theme, Classic disc (original studio), Custom color, or Custom image
 *   (with smooth, aspect-preserving, border-connected studio-white keying on a composited canvas)
 * - Separate Border color & soft radial glow (never a harsh straight ring): Match theme,
 *   Custom border color, or No border, with adjustable glow intensity.
 */

/** Below this the clip is not worth the bytes: the still shows. */
const CLIP_MIN_PX = 40;
/** Short cooldown (8s) for clips that haven't finished syncing yet, so they retry automatically. */
const noClipUntil = new Map<string, number>();
const NO_CLIP_TTL_MS = 8_000;

function isClipTemporarilyMissing(url: string): boolean {
  const until = noClipUntil.get(url);
  if (!until) return false;
  if (Date.now() > until) {
    noClipUntil.delete(url);
    return false;
  }
  return true;
}

function useIsDarkTheme(): boolean {
  const read = () =>
    typeof document !== "undefined"
      ? document.documentElement.dataset.theme === "dark" || document.documentElement.classList.contains("dark")
      : false;
  const [dark, setDark] = useState<boolean>(read);
  useEffect(() => {
    if (typeof document === "undefined" || typeof MutationObserver === "undefined") return;
    const root = document.documentElement;
    const update = () => setDark(read());
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

function resolveBgImageUrl(raw: string | undefined): string {
  const s = (raw || "").trim();
  if (!s) return "";
  if (s.startsWith("http://") || s.startsWith("https://") || s.startsWith("data:") || s.startsWith("/")) {
    return s;
  }
  return fileUrl(s);
}

/** Draw an image or video onto a square canvas with true CSS `object-cover` center-cropping (never squished). */
function drawObjectCover(
  ctx: CanvasRenderingContext2D,
  source: HTMLVideoElement | HTMLImageElement,
  dim: number,
): boolean {
  const sw = source instanceof HTMLVideoElement ? source.videoWidth : source.naturalWidth;
  const sh = source instanceof HTMLVideoElement ? source.videoHeight : source.naturalHeight;
  if (!sw || !sh) return false;
  const side = Math.min(sw, sh);
  const sx = (sw - side) / 2;
  const sy = (sh - side) / 2;
  ctx.drawImage(source, sx, sy, side, side, 0, 0, dim, dim);
  return true;
}

/**
 * High-quality, flicker-free studio & chroma backdrop keyer.
 * - Samples a 9-point exterior border ring to detect white studio, black/dark studio,
 *   green-screen, blue-screen, or any solid uniform studio backdrop via median RGB.
 * - Uses connected exterior flood-fill (from top/left/right border edges) so interior character
 *   details (white eyes, white kanji badge, dark belt, green garments) are NEVER punched out.
 * - Computes perceptual color distance with a smoothstep alpha matte + 2px edge feathering
 *   + color despill / fringe un-premultiplication so there is zero jagged halo or color bleed.
 */
function keyStudioBackdrop(ctx: CanvasRenderingContext2D, dim: number): boolean {
  let imgData: ImageData;
  try {
    imgData = ctx.getImageData(0, 0, dim, dim);
  } catch {
    return false;
  }
  const data = imgData.data;

  // Never treat an undecoded/all-transparent video frame as a valid keyed frame
  const centerIdx = ((dim >> 1) * dim + (dim >> 1)) * 4;
  if (data[centerIdx + 3] < 16) {
    return false;
  }

  // Sample 9 exterior border points (top arc + upper/mid sides; avoid bottom body edge)
  const pad = Math.max(2, Math.min(4, Math.floor(dim * 0.025)));
  const q1 = Math.floor(dim * 0.25);
  const mid = dim >> 1;
  const q3 = Math.floor(dim * 0.75);
  const sampleCoords: Array<[number, number]> = [
    [pad, pad],
    [q1, pad],
    [mid, pad],
    [q3, pad],
    [dim - 1 - pad, pad],
    [pad, q1],
    [dim - 1 - pad, q1],
    [pad, mid],
    [dim - 1 - pad, mid],
  ];

  const rs: number[] = [];
  const gs: number[] = [];
  const bs: number[] = [];
  let transparentBorder = 0;
  for (const [sx, sy] of sampleCoords) {
    const idx = (sy * dim + sx) * 4;
    if (data[idx + 3] < 40) {
      transparentBorder++;
      continue;
    }
    rs.push(data[idx]);
    gs.push(data[idx + 1]);
    bs.push(data[idx + 2]);
  }
  if (transparentBorder >= 3) {
    return true; // already alpha-transparent PNG/WebP
  }
  if (rs.length < 5) {
    return false;
  }
  const median = (arr: number[]) => {
    const s = [...arr].sort((a, b) => a - b);
    return s[s.length >> 1];
  };
  const bgR = median(rs);
  const bgG = median(gs);
  const bgB = median(bs);

  // Perceptual RGB distance (weighted luma + chroma sensitivity)
  const colorDist = (r: number, g: number, b: number): number => {
    const dr = r - bgR;
    const dg = g - bgG;
    const db = b - bgB;
    const w = Math.sqrt(0.30 * dr * dr + 0.59 * dg * dg + 0.11 * db * db);
    // Also factor max-channel diff so saturated aura strokes against neutral studio aren't eaten
    const maxDiff = Math.max(Math.abs(dr), Math.abs(dg), Math.abs(db));
    return w * 0.72 + maxDiff * 0.28;
  };

  // Verify the border really is a uniform studio / chroma backdrop (not a rich photo/scene)
  let uniformCount = 0;
  for (let k = 0; k < rs.length; k++) {
    if (colorDist(rs[k], gs[k], bs[k]) <= 28) uniformCount++;
  }
  if (uniformCount < Math.ceil(rs.length * 0.6)) {
    return false;
  }

  const bgMin = Math.min(bgR, bgG, bgB);
  const bgMax = Math.max(bgR, bgG, bgB);
  const bgChroma = bgMax - bgMin;
  const isWhiteStudio = bgMin >= 205 && bgChroma <= 26;
  const isBlackStudio = bgMax <= 34 && bgChroma <= 24;
  const isGreenScreen = bgG >= 95 && bgG - Math.max(bgR, bgB) >= 28;
  const isBlueScreen = bgB >= 95 && bgB - Math.max(bgR, bgG) >= 28;
  // Require white studio, black studio, green/blue screen, or very clean solid studio backdrop
  if (!isWhiteStudio && !isBlackStudio && !isGreenScreen && !isBlueScreen && uniformCount < rs.length - 1) {
    return false;
  }

  // Tune tolerance thresholds by backdrop family (tight inner clear, soft feathered ramp)
  const innerTol = isGreenScreen || isBlueScreen ? 28 : isBlackStudio ? 16 : 18;
  const outerTol = isGreenScreen || isBlueScreen ? 76 : isBlackStudio ? 46 : 54;
  const chromaGuard = isWhiteStudio ? 34 : isBlackStudio ? 28 : 999;

  const total = dim * dim;
  const visited = new Uint8Array(total); // 0=unvisited, 1=exterior bg, 2=silhouette edge-1, 3=edge-2
  const queue = new Int32Array(total);
  let head = 0;
  let tail = 0;

  const isBgCandidate = (p: number): boolean => {
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const d = colorDist(r, g, b);
    if (d > outerTol) return false;
    if (isWhiteStudio || isBlackStudio) {
      const ch = Math.max(r, g, b) - Math.min(r, g, b);
      if (ch > chromaGuard + (d < innerTol ? 10 : 0)) return false;
    } else if (isGreenScreen) {
      if (g < Math.max(r, b) + 6) return false;
    } else if (isBlueScreen) {
      if (b < Math.max(r, g) + 6) return false;
    }
    return true;
  };

  const tryEnqueue = (p: number) => {
    if (visited[p] !== 0) return;
    if (isBgCandidate(p)) {
      visited[p] = 1;
      queue[tail++] = p;
    } else {
      visited[p] = 2;
    }
  };

  // Seed top, left, and right edges (plus bottom-left/bottom-right corners, never bottom center)
  for (let x = 0; x < dim; x++) {
    tryEnqueue(x);
  }
  for (let y = 1; y < dim; y++) {
    tryEnqueue(y * dim);
    tryEnqueue(y * dim + (dim - 1));
  }
  const bottomEdgeSpan = Math.max(4, Math.floor(dim * 0.14));
  const lastRow = (dim - 1) * dim;
  for (let x = 0; x < bottomEdgeSpan; x++) {
    tryEnqueue(lastRow + x);
    tryEnqueue(lastRow + (dim - 1 - x));
  }

  const smoothstep = (t: number) => {
    const c = Math.max(0, Math.min(1, t));
    return c * c * (3 - 2 * c);
  };

  // Despill & un-premultiply backdrop tint from semi-transparent edge pixels
  const cleanEdgeColor = (i: number, keepAlpha: number) => {
    let r = data[i];
    let g = data[i + 1];
    let b = data[i + 2];
    if (isGreenScreen) {
      const rbMax = Math.max(r, b);
      if (g > rbMax) {
        const spill = g - rbMax;
        g = Math.round(g - spill * (1 - keepAlpha * 0.35));
      }
    } else if (isBlueScreen) {
      const rgMax = Math.max(r, g);
      if (b > rgMax) {
        const spill = b - rgMax;
        b = Math.round(b - spill * (1 - keepAlpha * 0.35));
      }
    } else if (keepAlpha > 0.08 && keepAlpha < 0.96) {
      // Un-blend studio white/black contamination: C_fg = (C - (1 - a) * C_bg) / a
      const bgMix = (1 - keepAlpha) * 0.78;
      const denom = Math.max(0.22, 1 - bgMix);
      r = Math.max(0, Math.min(255, Math.round((r - bgMix * bgR) / denom)));
      g = Math.max(0, Math.min(255, Math.round((g - bgMix * bgG) / denom)));
      b = Math.max(0, Math.min(255, Math.round((b - bgMix * bgB) / denom)));
    }
    data[i] = r;
    data[i + 1] = g;
    data[i + 2] = b;
  };

  while (head < tail) {
    const p = queue[head++];
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const d = colorDist(r, g, b);

    let keep = smoothstep((d - innerTol) / Math.max(1, outerTol - innerTol));
    if (isWhiteStudio || isBlackStudio) {
      const ch = Math.max(r, g, b) - Math.min(r, g, b);
      const chKeep = smoothstep((ch - 14) / 24);
      keep = Math.max(keep, chKeep * 0.9);
    }
    if (keep < 0.04) {
      data[i + 3] = 0;
    } else {
      cleanEdgeColor(i, keep);
      data[i + 3] = Math.round(keep * 255);
    }

    const x = p % dim;
    const y = (p - x) / dim;
    if (x > 0) tryEnqueue(p - 1);
    if (x < dim - 1) tryEnqueue(p + 1);
    if (y > 0) tryEnqueue(p - dim);
    if (y < dim - 1) tryEnqueue(p + dim);
  }

  // Pass 2: Gently feather & despill 1px immediate silhouette boundary so transitions are silky smooth
  for (let p = 0; p < total; p++) {
    if (visited[p] !== 2) continue;
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const d = colorDist(r, g, b);
    // Count how many 4-neighbors were keyed background
    const x = p % dim;
    const y = (p - x) / dim;
    let bgNeighbors = 0;
    if (x > 0 && visited[p - 1] === 1) bgNeighbors++;
    if (x < dim - 1 && visited[p + 1] === 1) bgNeighbors++;
    if (y > 0 && visited[p - dim] === 1) bgNeighbors++;
    if (y < dim - 1 && visited[p + dim] === 1) bgNeighbors++;

    if (d < outerTol * 1.45) {
      const distFactor = smoothstep((d - innerTol * 0.9) / Math.max(1, outerTol * 1.35 - innerTol * 0.9));
      const feather = Math.max(0.45, Math.min(1, distFactor * (bgNeighbors >= 2 ? 0.88 : 0.96)));
      cleanEdgeColor(i, feather);
      data[i + 3] = Math.round(data[i + 3] * feather);
    } else if (isGreenScreen || isBlueScreen) {
      cleanEdgeColor(i, 0.85);
    }
  }

  ctx.putImageData(imgData, 0, 0);
  return true;
}

/** Paint the chosen opaque backdrop (theme, custom color, or custom image) onto the composite canvas. */
function paintBackdrop(
  ctx: CanvasRenderingContext2D,
  dim: number,
  bgMode: string,
  bgColor: string,
  isDark: boolean,
  bgImg: HTMLImageElement | null,
): void {
  if (bgMode === "image" && bgImg && bgImg.complete && bgImg.naturalWidth > 0) {
    drawObjectCover(ctx, bgImg, dim);
    return;
  }
  if (bgMode === "color" && bgColor) {
    const grad = ctx.createLinearGradient(0, 0, dim, dim);
    grad.addColorStop(0, bgColor);
    grad.addColorStop(1, isDark ? "#0f172a" : "#ffffff");
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, dim, dim);
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, dim, dim);
    ctx.globalAlpha = 1;
    return;
  }
  // Match theme
  const grad = ctx.createRadialGradient(dim * 0.5, dim * 0.38, dim * 0.08, dim * 0.5, dim * 0.5, dim * 0.72);
  if (isDark) {
    grad.addColorStop(0, "#262933");
    grad.addColorStop(1, "#121318");
  } else {
    grad.addColorStop(0, "#ffffff");
    grad.addColorStop(1, "#eae6df");
  }
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, dim, dim);
}

export function Avatar({
  profile,
  status,
  size = 40,
  onClick,
  still = false,
  moodOverride = null,
  previewClipUrl = null,
  className,
}: {
  profile: Profile | null;
  status?: Status;
  size?: number;
  onClick?: () => void;
  /** no idle animation (lists, pickers) */
  still?: boolean;
  /** Optional explicit mood override (e.g. "call" inside LiveCallModal) */
  moodOverride?: "idle" | "working" | "waiting" | "happy" | "error" | "call" | null;
  /** Optional explicit animation/still URL to live-preview inside Avatar Studio */
  previewClipUrl?: string | null;
  className?: string;
}) {
  const t = useT();
  const [, bump] = useState(0);
  const isDark = useIsDarkTheme();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const offCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const bgImgRef = useRef<HTMLImageElement | null>(null);
  const lastVideoTimeRef = useRef<number>(-1);
  const videoAdvanceCountRef = useRef<number>(0);
  const [canvasReady, setCanvasReady] = useState(false);
  const [canvasDisabled, setCanvasDisabled] = useState(false);

  // Synchronously ensure muted/playsinline HTML attributes exist on mount (fixes React's missing
  // `muted` attribute bug that can block autoplay on Android Chrome / WebView)
  const attachVideoRef = useCallback((el: HTMLVideoElement | null) => {
    videoRef.current = el;
    if (el) {
      el.defaultMuted = true;
      el.muted = true;
      el.playsInline = true;
      el.setAttribute("muted", "");
      el.setAttribute("playsinline", "");
      el.setAttribute("webkit-playsinline", "");
    }
  }, []);

  const mood = useMood(status);
  const state = status?.state ?? "idle";
  const color = profile?.color ?? "#0064d4";
  const bgMode = profile?.avatar_bg_mode || "theme";
  const bgColor = profile?.avatar_bg_color || color;
  const bgImageUrl = resolveBgImageUrl(profile?.avatar_bg_image);

  const borderMode = profile?.avatar_border_mode ?? "theme";
  const borderColor =
    borderMode === "color" ? profile?.avatar_border_color || color : "var(--accent, #6366f1)";
  const borderGlowPct = Math.max(0, Math.min(100, profile?.avatar_border_glow ?? 60));

  // the dragon is the default: no profile yet, or a profile that names it (or a retired face)
  const dragon = !profile || isDragon(profile.avatar);
  // a face from the studio: anything else that is not the emoji
  const studio = !dragon && profile?.avatar ? profile.avatar : null;
  // Never switch pose on click/tap so the avatar never flashes a different camera angle
  const pose = moodOverride ?? (still ? "idle" : mood);
  const updatedAt = profile?.updated_at;
  const situationClips = profile?.avatar_situation_clips;
  const animationsEnabled = profile?.avatar_animated !== false;

  const previewIsVideo = !!previewClipUrl && /\.(mp4|webm)(\?|$)/i.test(previewClipUrl);
  const previewIsImage = !!previewClipUrl && !previewIsVideo;

  const primaryClipCandidate = previewIsVideo
    ? previewClipUrl
    : previewIsImage || still || !animationsEnabled || size < CLIP_MIN_PX
      ? null
      : dragon
        ? dragonClipUrl(pose, situationClips)
        : studio
          ? studioClipUrl(studio, pose, pose === "call" ? isDark : false, updatedAt, situationClips)
          : null;
  // If a custom situation clip filename isn't synced yet, fall back cleanly to canonical <pose>.mp4 before still
  const canonicalFallbackClip =
    !previewIsVideo && !previewIsImage && !still && animationsEnabled && size >= CLIP_MIN_PX && studio
      ? studioClipUrl(studio, pose === "call" ? "idle" : pose, false, updatedAt, undefined)
      : null;
  const clip =
    primaryClipCandidate && !isClipTemporarilyMissing(primaryClipCandidate)
      ? primaryClipCandidate
      : canonicalFallbackClip &&
          canonicalFallbackClip !== primaryClipCandidate &&
          !isClipTemporarilyMissing(canonicalFallbackClip)
        ? canonicalFallbackClip
        : null;

  // Never move the circle container itself for 3D dragon/studio avatars so touching or idle
  // never shifts or clips the circle.
  const motion =
    dragon || studio || still || clip
      ? ""
      : state === "working"
        ? "avatar-working"
        : state === "waiting"
          ? "avatar-waiting"
          : "avatar-idle";

  const customStillOverride =
    studio && situationClips?.[pose] && /\.(webp|png|jpg|jpeg|gif)$/i.test(situationClips[pose])
      ? fileUrl(`avatar/${studio}/${situationClips[pose]}`)
      : null;
  const customClipActive = !previewIsVideo && !previewIsImage && hasCustomClipOverride(pose, situationClips);
  const stillUrl = previewIsImage && previewClipUrl
    ? previewClipUrl
    : dragon
      ? dragonUrl(pose)
      : studio
        ? customStillOverride || studioUrl(studio, pose, false, updatedAt)
        : "";
  // Avoid flashing idle.webp for 1ms before a different custom situation clip decodes
  const videoPoster = customClipActive ? undefined : stillUrl || undefined;

  const shouldKeyBackdrop = (dragon || studio) && bgMode !== "default" && !canvasDisabled;
  const dpr = typeof window !== "undefined" ? Math.min(2, window.devicePixelRatio || 1.5) : 1.5;
  const canvasDim = Math.min(288, Math.max(96, Math.round(size * dpr)));

  // Preload custom background image when bgMode === "image"
  useEffect(() => {
    if (bgMode !== "image" || !bgImageUrl || typeof Image === "undefined") {
      bgImgRef.current = null;
      return;
    }
    const im = new Image();
    im.crossOrigin = "anonymous";
    im.src = bgImageUrl;
    bgImgRef.current = im;
  }, [bgMode, bgImageUrl]);

  const paintKeyedSource = useCallback(
    (source: HTMLVideoElement | HTMLImageElement | null) => {
      if (!shouldKeyBackdrop || !source) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      if (!offCanvasRef.current && typeof document !== "undefined") {
        offCanvasRef.current = document.createElement("canvas");
      }
      const off = offCanvasRef.current;
      if (!off) return;
      if (off.width !== canvasDim || off.height !== canvasDim) {
        off.width = canvasDim;
        off.height = canvasDim;
      }
      const offCtx = off.getContext("2d", { willReadFrequently: true });
      if (!offCtx) return;

      try {
        offCtx.clearRect(0, 0, canvasDim, canvasDim);
        if (!drawObjectCover(offCtx, source, canvasDim)) return;
        const keyed = keyStudioBackdrop(offCtx, canvasDim);
        if (!keyed) {
          // Scenic non-studio video/image: show original directly without flicker
          setCanvasReady(false);
          return;
        }
        // Composite chosen opaque backdrop + keyed character onto the visible canvas in one shot
        ctx.clearRect(0, 0, canvasDim, canvasDim);
        paintBackdrop(ctx, canvasDim, bgMode, bgColor, isDark, bgImgRef.current);
        ctx.drawImage(off, 0, 0);
        if (source instanceof HTMLVideoElement) {
          if (source.currentTime !== lastVideoTimeRef.current) {
            lastVideoTimeRef.current = source.currentTime;
            videoAdvanceCountRef.current += 1;
          }
        }
        setCanvasReady(true);
      } catch {
        /* ignore incomplete frame or security error */
      }
    },
    [shouldKeyBackdrop, canvasDim, bgMode, bgColor, isDark],
  );

  useEffect(() => {
    setCanvasDisabled(false);
    lastVideoTimeRef.current = -1;
    videoAdvanceCountRef.current = 0;
    if (!shouldKeyBackdrop) setCanvasReady(false);
  }, [shouldKeyBackdrop, studio, dragon, clip, bgMode]);

  // Paint the keyed still immediately while the video buffers so background colors show at frame 0,
  // but skip preloading a mismatched idle.webp when a custom situation clip is selected (prevents 1ms icon flash!)
  useEffect(() => {
    if (!shouldKeyBackdrop || !clip || !stillUrl || customClipActive || typeof Image === "undefined") return;
    let alive = true;
    const pre = new Image();
    pre.onload = () => {
      if (!alive) return;
      const v = videoRef.current;
      if (!v || v.readyState < 2 || v.paused) paintKeyedSource(pre);
    };
    pre.src = stillUrl;
    return () => {
      alive = false;
    };
  }, [shouldKeyBackdrop, clip, stillUrl, customClipActive, paintKeyedSource]);

  // Ensure Android WebView & mobile browsers ALWAYS autoplay & resume muted inline avatar clips reliably
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !clip) return;
    v.defaultMuted = true;
    v.muted = true;
    v.playsInline = true;
    v.setAttribute("muted", "");
    v.setAttribute("playsinline", "");
    v.setAttribute("webkit-playsinline", "");
    let loggedPlay = false;
    const tryPlay = () => {
      v.muted = true;
      const p = v.play();
      if (p && typeof p.then === "function") {
        p.then(() => {
          if (!loggedPlay) {
            loggedPlay = true;
            recordDebugEvent("ui", `avatar video playing (${size}px)`, {
              clip: clip.split("?")[0],
              w: v.videoWidth,
              h: v.videoHeight,
              ready: v.readyState,
              keyed: shouldKeyBackdrop,
            });
          }
        }).catch((err: unknown) => {
          recordDebugEvent("ui", `avatar video play rejected (${size}px)`, {
            clip: clip.split("?")[0],
            reason: err instanceof Error ? err.message : String(err),
            ready: v.readyState,
          });
        });
      }
    };
    tryPlay();
    const onGesture = () => {
      if (v.paused) tryPlay();
    };
    const onVis = () => {
      if (typeof document !== "undefined" && !document.hidden && v.paused) tryPlay();
    };
    const kick = typeof window !== "undefined" ? window.setInterval(() => {
      if (v.readyState >= 2 && v.paused) {
        tryPlay();
      } else if (shouldKeyBackdrop && !v.paused && v.currentTime > 0.35 && videoAdvanceCountRef.current < 2) {
        // Watchdog: if video IS playing but canvas readback failed to advance frames on this GPU,
        // drop the canvas overlay automatically so native hardware <video> always animates!
        recordDebugEvent("ui", `avatar canvas watchdog fallback to native video (${size}px)`, {
          clip: clip.split("?")[0],
          t: v.currentTime,
        });
        setCanvasDisabled(true);
        setCanvasReady(false);
      }
    }, 1200) : 0;
    if (typeof document !== "undefined") {
      document.addEventListener("pointerdown", onGesture, { passive: true });
      document.addEventListener("touchstart", onGesture, { passive: true });
      document.addEventListener("visibilitychange", onVis);
    }
    return () => {
      if (kick) window.clearInterval(kick);
      if (typeof document !== "undefined") {
        document.removeEventListener("pointerdown", onGesture);
        document.removeEventListener("touchstart", onGesture);
        document.removeEventListener("visibilitychange", onVis);
      }
    };
  }, [clip, size, shouldKeyBackdrop]);

  useEffect(() => {
    if (!shouldKeyBackdrop || !clip) return;
    if (
      typeof window === "undefined" ||
      typeof window.requestAnimationFrame !== "function" ||
      typeof window.cancelAnimationFrame !== "function"
    ) {
      return;
    }
    let alive = true;
    let rafId = 0;
    let vfcId: number | null = null;
    let lastDraw = 0;
    type VideoWithVfc = HTMLVideoElement & {
      requestVideoFrameCallback?: (cb: () => void) => number;
      cancelVideoFrameCallback?: (id: number) => void;
    };
    const v = videoRef.current as VideoWithVfc | null;

    if (v && typeof v.requestVideoFrameCallback === "function") {
      const onFrame = () => {
        if (!alive) return;
        const cur = videoRef.current as VideoWithVfc | null;
        if (cur && cur.readyState >= 2 && cur.videoWidth > 0) {
          paintKeyedSource(cur);
        }
        if (cur && typeof cur.requestVideoFrameCallback === "function") {
          vfcId = cur.requestVideoFrameCallback(onFrame);
        }
      };
      vfcId = v.requestVideoFrameCallback(onFrame);
    }

    // Also keep a lightweight rAF safety pump so frame-0 & browsers without rVFC always animate
    const tick = (now: number) => {
      if (!alive) return;
      const cur = videoRef.current;
      if (cur && cur.readyState >= 2 && cur.videoWidth > 0 && now - lastDraw >= 40) {
        lastDraw = now;
        paintKeyedSource(cur);
      }
      rafId = window.requestAnimationFrame(tick);
    };
    rafId = window.requestAnimationFrame(tick);
    return () => {
      alive = false;
      window.cancelAnimationFrame(rafId);
      if (vfcId !== null && v && typeof v.cancelVideoFrameCallback === "function") {
        v.cancelVideoFrameCallback(vfcId);
      }
    };
  }, [shouldKeyBackdrop, clip, paintKeyedSource]);

  // Also repaint immediately when still image is shown and theme/bg changes
  useEffect(() => {
    if (!shouldKeyBackdrop || clip) return;
    if (imgRef.current && imgRef.current.complete && imgRef.current.naturalWidth > 0) {
      paintKeyedSource(imgRef.current);
    }
  }, [shouldKeyBackdrop, clip, paintKeyedSource]);

  // Soft radial inner border glow + gentle outer ambient glow (matching v1.2.14's feathered glow,
  // slightly stronger, NEVER a hard straight ring!)
  const hasGlow = borderMode !== "none" && borderGlowPct > 0;
  const g = borderGlowPct / 100;
  const outerGlowStyle: CSSProperties = hasGlow
    ? {
        boxShadow: `0 0 ${Math.max(8, Math.round(size * (0.14 + g * 0.22)))}px ${Math.max(1, Math.round(size * 0.03 * g))}px color-mix(in srgb, ${borderColor} ${Math.round(28 + g * 46)}%, transparent)`,
      }
    : {};
  const innerFeatherGlowStyle: CSSProperties | null = hasGlow
    ? {
        background: `radial-gradient(circle, transparent ${Math.round(62 - g * 12)}%, color-mix(in srgb, ${borderColor} ${Math.round(18 + g * 28)}%, transparent) ${Math.round(80 - g * 6)}%, color-mix(in srgb, ${borderColor} ${Math.round(52 + g * 42)}%, transparent) 100%)`,
        boxShadow: `inset 0 0 ${Math.max(6, Math.round(size * (0.11 + g * 0.14)))}px color-mix(in srgb, ${borderColor} ${Math.round(40 + g * 48)}%, transparent)`,
      }
    : null;

  const discStyle: CSSProperties = (() => {
    let base: CSSProperties;
    if (!dragon && !studio) {
      if (bgMode === "image" && bgImageUrl) {
        base = {
          backgroundImage: `url("${bgImageUrl}")`,
          backgroundSize: "cover",
          backgroundPosition: "center",
        };
      } else if (bgMode === "theme") {
        base = { background: isDark ? "var(--surface-2, #18191c)" : "var(--surface-2, #f1efeb)" };
      } else {
        const c = bgMode === "color" ? bgColor : color;
        base = { background: `linear-gradient(135deg, ${c}, color-mix(in srgb, ${c} 60%, #ffffff))` };
      }
    } else if (bgMode === "image" && bgImageUrl) {
      base = {
        backgroundImage: `url("${bgImageUrl}")`,
        backgroundSize: "cover",
        backgroundPosition: "center",
      };
    } else if (bgMode === "color" && bgColor) {
      base = {
        background: `linear-gradient(135deg, ${bgColor}, color-mix(in srgb, ${bgColor} 68%, ${isDark ? "#0f172a" : "#ffffff"}))`,
      };
    } else if (bgMode === "theme") {
      base = {
        background: isDark
          ? "radial-gradient(circle at 50% 38%, #262933, #121318)"
          : "radial-gradient(circle at 50% 38%, #ffffff, #eae6df)",
      };
    } else {
      base = { background: "#f1efeb" };
    }
    return { ...base, ...outerGlowStyle };
  })();

  const rejectPortraitIfNonCall = useCallback(
    (v: HTMLVideoElement): boolean => {
      if (pose !== "call" && !previewIsVideo && v.videoWidth > 0 && v.videoHeight > v.videoWidth * 1.15 && clip) {
        recordDebugEvent("ui", `avatar rejected portrait call clip in ${pose} (${size}px)`, {
          clip: clip.split("?")[0],
          w: v.videoWidth,
          h: v.videoHeight,
        });
        noClipUntil.set(clip, Date.now() + 60_000);
        bump((n) => n + 1);
        return true;
      }
      return false;
    },
    [pose, previewIsVideo, clip, size],
  );

  const label = t("{name}'s avatar", { name: profile?.name ?? "MiniMuse" });
  const box = cx("relative inline-block shrink-0 select-none rounded-full", className);
  const face = (
    <>
      <span
        className={cx("relative isolate z-0 block h-full w-full overflow-hidden rounded-full", motion)}
        style={discStyle}
      >
        {clip ? (
          <video
            ref={attachVideoRef}
            key={clip}
            src={clip}
            poster={videoPoster}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            disablePictureInPicture
            disableRemotePlayback
            // Keep <video> BEHIND <canvas> inside the isolated disc while keyed (opacity 0.004 keeps
            // Chromium/Android WebView decoding at full FPS without ever painting over canvas or sibling buttons!)
            style={{
              opacity: shouldKeyBackdrop && canvasReady ? 0.004 : 1,
            }}
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              if (rejectPortraitIfNonCall(v)) return;
              v.defaultMuted = true;
              v.muted = true;
              void v.play().catch(() => undefined);
            }}
            onCanPlay={(e) => {
              const v = e.currentTarget;
              if (rejectPortraitIfNonCall(v)) return;
              v.muted = true;
              if (v.paused) void v.play().catch(() => undefined);
            }}
            onLoadedData={(e) => {
              const v = e.currentTarget;
              if (rejectPortraitIfNonCall(v)) return;
              v.muted = true;
              if (shouldKeyBackdrop) paintKeyedSource(v);
              void v.play().catch(() => undefined);
            }}
            onTimeUpdate={(e) => {
              if (shouldKeyBackdrop) {
                const v = e.currentTarget;
                if (v.readyState >= 2 && v.videoWidth > 0) paintKeyedSource(v);
              }
            }}
            onError={(e) => {
              const mediaErr = e.currentTarget.error;
              recordDebugEvent("error", `avatar video error (${size}px)`, {
                clip: clip.split("?")[0],
                code: mediaErr?.code,
                msg: mediaErr?.message,
              });
              noClipUntil.set(clip, Date.now() + NO_CLIP_TTL_MS);
              bump((n) => n + 1);
              if (typeof window !== "undefined" && typeof window.setTimeout === "function") {
                window.setTimeout(() => bump((n) => n + 1), NO_CLIP_TTL_MS + 100);
              }
            }}
            className="pointer-events-none relative z-0 h-full w-full object-cover"
          />
        ) : dragon || studio ? (
          <img
            ref={imgRef}
            src={stillUrl}
            alt=""
            draggable={false}
            onLoad={(e) => {
              if (shouldKeyBackdrop) paintKeyedSource(e.currentTarget);
            }}
            style={{ opacity: shouldKeyBackdrop && canvasReady ? 0 : 1 }}
            className="relative z-0 h-full w-full object-cover"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center leading-none drop-shadow-sm" style={{ fontSize: size * 0.5 }}>
            {profile?.emoji ?? "✨"}
          </span>
        )}
        {shouldKeyBackdrop && (
          <canvas
            ref={canvasRef}
            width={canvasDim}
            height={canvasDim}
            style={{ opacity: canvasReady ? 1 : 0 }}
            className="pointer-events-none absolute inset-0 z-10 h-full w-full rounded-full"
          />
        )}
        {innerFeatherGlowStyle && (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 z-20 rounded-full"
            style={innerFeatherGlowStyle}
          />
        )}
      </span>
      {status && state !== "idle" && (
        <span
          className={cx(
            "absolute -bottom-0.5 -right-0.5 z-30 rounded-full border-2 border-bg",
            state === "working" ? "bg-amber-400" : "bg-rose-500",
          )}
          style={{
            width: Math.max(10, size * 0.28),
            height: Math.max(10, size * 0.28),
            transform: "translateZ(2px)",
          }}
        />
      )}
    </>
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-label={label}
        className={box}
        style={{ width: size, height: size }}
      >
        {face}
      </button>
    );
  }
  return (
    <span role="img" aria-label={label} className={box} style={{ width: size, height: size }}>
      {face}
    </span>
  );
}
