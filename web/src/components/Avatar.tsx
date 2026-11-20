import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { fileUrl } from "../api";
import { dragonClipUrl, dragonUrl, isDragon, studioClipUrl, studioUrl } from "../avatars";
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
 * Smooth, flicker-free studio backdrop keyer (supports BOTH bright white AND solid black studio backdrops).
 * - Checks ONLY the top studio backdrop points (top-left, top-center, top-right) so the moving
 *   character/aura near the bottom/sides never disturbs backdrop detection.
 * - Detects either bright studio white (min >= 204, chroma <= 28) OR solid studio black
 *   (max <= 34, chroma <= 24, e.g. darkmode Muse clips on mobile).
 * - Uses a continuous smooth alpha ramp over lightness & chroma (no binary threshold cliff) so
 *   H.264 MP4 compression noise never flickers grainy black/white pixels.
 */
function keyStudioBackdrop(ctx: CanvasRenderingContext2D, dim: number): boolean {
  let imgData: ImageData;
  try {
    imgData = ctx.getImageData(0, 0, dim, dim);
  } catch {
    return false;
  }
  const data = imgData.data;

  const topSamples = [
    (2 * dim + 2) * 4,
    (2 * dim + (dim >> 1)) * 4,
    (2 * dim + (dim - 3)) * 4,
  ];
  // Never treat an undecoded/all-transparent video frame as a valid keyed frame
  const centerIdx = ((dim >> 1) * dim + (dim >> 1)) * 4;
  if (data[centerIdx + 3] < 16) {
    return false;
  }
  let brightWhiteCount = 0;
  let solidBlackCount = 0;
  for (const idx of topSamples) {
    const r = data[idx];
    const g = data[idx + 1];
    const b = data[idx + 2];
    const a = data[idx + 3];
    if (a < 40) return true; // already transparent PNG/WebP with opaque center character
    const mn = Math.min(r, g, b);
    const mx = Math.max(r, g, b);
    if (mn >= 204 && mx - mn <= 28) brightWhiteCount++;
    else if (mx <= 34 && mx - mn <= 24) solidBlackCount++;
  }
  const mode: "white" | "black" | null =
    brightWhiteCount >= 2 ? "white" : solidBlackCount >= 2 ? "black" : null;
  if (!mode) {
    // Rich scenic backdrop — do not key
    return false;
  }

  const total = dim * dim;
  const visited = new Uint8Array(total);
  const queue = new Int32Array(total);
  let head = 0;
  let tail = 0;

  const isStudioCandidate = (p: number): boolean => {
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const lum = (r + g + b) * 0.333333;
    const chroma = Math.max(r, g, b) - Math.min(r, g, b);
    if (mode === "white") {
      return lum >= 190 && chroma <= 42;
    }
    return lum <= 46 && chroma <= 32;
  };

  const tryEnqueue = (p: number) => {
    if (visited[p] !== 0) return;
    if (isStudioCandidate(p)) {
      visited[p] = 1;
      queue[tail++] = p;
    } else {
      visited[p] = 2; // silhouette boundary pixel
    }
  };

  // Seed top edge, plus left & right edges (avoiding bottom edge where body/desk sits)
  for (let x = 0; x < dim; x++) {
    tryEnqueue(x);
  }
  for (let y = 1; y < dim; y++) {
    tryEnqueue(y * dim);
    tryEnqueue(y * dim + (dim - 1));
  }

  while (head < tail) {
    const p = queue[head++];
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const lum = (r + g + b) * 0.333333;
    const chroma = Math.max(r, g, b) - Math.min(r, g, b);

    let keep: number;
    if (mode === "white") {
      const lumT = Math.max(0, Math.min(1, (234 - lum) / 44));
      const chT = Math.max(0, Math.min(1, (chroma - 16) / 26));
      keep = Math.max(lumT, chT);
    } else {
      const lumT = Math.max(0, Math.min(1, (lum - 14) / 32));
      const chT = Math.max(0, Math.min(1, (chroma - 10) / 22));
      keep = Math.max(lumT, chT);
    }
    data[i + 3] = Math.round(keep * keep * 255);

    const x = p % dim;
    const y = (p - x) / dim;
    if (x > 0) tryEnqueue(p - 1);
    if (x < dim - 1) tryEnqueue(p + 1);
    if (y > 0) tryEnqueue(p - dim);
    if (y < dim - 1) tryEnqueue(p + dim);
  }

  // Soften 1px immediate boundary around character silhouette so edges blend smoothly
  for (let p = 0; p < total; p++) {
    if (visited[p] === 2) {
      const i = p * 4;
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      const lum = (r + g + b) * 0.333333;
      const chroma = Math.max(r, g, b) - Math.min(r, g, b);
      if (mode === "white" && lum > 170 && chroma < 54) {
        const factor = Math.max(0.35, Math.min(1, (225 - lum) / 55));
        data[i + 3] = Math.round(data[i + 3] * factor);
      } else if (mode === "black" && lum < 64 && chroma < 42) {
        const factor = Math.max(0.35, Math.min(1, (lum - 12) / 52));
        data[i + 3] = Math.round(data[i + 3] * factor);
      }
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
  const stillUrl = previewIsImage && previewClipUrl
    ? previewClipUrl
    : dragon
      ? dragonUrl(pose)
      : studio
        ? customStillOverride || studioUrl(studio, pose, false, updatedAt)
        : "";

  const shouldKeyBackdrop = (dragon || studio) && bgMode !== "default" && !canvasDisabled;
  const canvasDim = Math.min(144, Math.max(72, Math.round(size * 1.45)));

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

  // Paint the keyed still immediately while the video buffers so background colors show at frame 0
  useEffect(() => {
    if (!shouldKeyBackdrop || !clip || !stillUrl || typeof Image === "undefined") return;
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
  }, [shouldKeyBackdrop, clip, stillUrl, paintKeyedSource]);

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

  const label = t("{name}'s avatar", { name: profile?.name ?? "MiniMuse" });
  const box = cx("relative inline-block shrink-0 select-none rounded-full", className);
  const face = (
    <>
      <span
        className={cx("relative block h-full w-full overflow-hidden rounded-full", motion)}
        style={discStyle}
      >
        {shouldKeyBackdrop && (
          <canvas
            ref={canvasRef}
            width={canvasDim}
            height={canvasDim}
            style={{ opacity: canvasReady ? 1 : 0 }}
            className="pointer-events-none absolute inset-0 z-0 h-full w-full rounded-full"
          />
        )}
        {clip ? (
          <video
            ref={attachVideoRef}
            key={clip}
            src={clip}
            poster={stillUrl}
            autoPlay
            loop
            muted
            playsInline
            preload="auto"
            disablePictureInPicture
            disableRemotePlayback
            // Keep <video> ABOVE the canvas at opacity 0.01 when keyed so Chromium/Android WebView's
            // occlusion tracker ALWAYS sees the video as visible & decodes every frame at full FPS!
            style={{
              transform: "translateZ(0)",
              opacity: shouldKeyBackdrop && canvasReady ? 0.01 : 1,
            }}
            onLoadedMetadata={(e) => {
              const v = e.currentTarget;
              v.defaultMuted = true;
              v.muted = true;
              void v.play().catch(() => undefined);
            }}
            onCanPlay={(e) => {
              const v = e.currentTarget;
              v.muted = true;
              if (v.paused) void v.play().catch(() => undefined);
            }}
            onLoadedData={(e) => {
              const v = e.currentTarget;
              v.muted = true;
              paintKeyedSource(v);
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
            className="pointer-events-none relative z-10 h-full w-full object-cover"
          />
        ) : dragon || studio ? (
          <img
            ref={imgRef}
            src={stillUrl}
            alt=""
            draggable={false}
            onLoad={(e) => paintKeyedSource(e.currentTarget)}
            style={{ opacity: shouldKeyBackdrop && canvasReady ? 0 : 1 }}
            className="relative z-10 h-full w-full object-cover"
          />
        ) : (
          <span className="flex h-full w-full items-center justify-center leading-none drop-shadow-sm" style={{ fontSize: size * 0.5 }}>
            {profile?.emoji ?? "✨"}
          </span>
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
            "absolute -bottom-0.5 -right-0.5 rounded-full border-2 border-bg",
            state === "working" ? "bg-amber-400" : "bg-rose-500",
          )}
          style={{ width: Math.max(10, size * 0.28), height: Math.max(10, size * 0.28) }}
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
