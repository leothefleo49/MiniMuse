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
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(source, sx, sy, side, side, 0, 0, dim, dim);
  return true;
}

/** Reusable scratch buffers per canvas dimension so 30fps video keying allocates zero garbage. */
const keyScratchCache = new Map<
  number,
  {
    distMap: Uint8Array;
    queue: Int32Array;
    band1: Int32Array;
    band2: Int32Array;
    orig: Uint8ClampedArray;
  }
>();

function getKeyScratch(dim: number) {
  let buf = keyScratchCache.get(dim);
  if (!buf) {
    const total = dim * dim;
    buf = {
      distMap: new Uint8Array(total),
      queue: new Int32Array(total),
      band1: new Int32Array(total),
      band2: new Int32Array(total),
      orig: new Uint8ClampedArray(total * 4),
    };
    if (keyScratchCache.size > 6) keyScratchCache.clear();
    keyScratchCache.set(dim, buf);
  }
  return buf;
}

/**
 * Studio-grade VFX backdrop keyer with analytical alpha & inward color decontamination.
 * - Samples 13 exterior border points to detect white studio, black/dark studio,
 *   green-screen, blue-screen, or uniform solid studio backdrops via median RGB.
 * - Uses 4-edge exterior flood-fill (including studio floor shadows in the bottom zone)
 *   so interior character details (white belly, white eyes, clothes) are NEVER punched out.
 * - Solves C = alpha * F + (1 - alpha) * B along the 2px silhouette boundary using clean
 *   interior foreground color F, replacing halo RGB with F so white/green/black edge fringes
 *   are completely eliminated even on 1x DPR desktop monitors.
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

  // Sample 13 exterior ring points (top, upper/mid/lower sides, bottom corners)
  const pad = Math.max(2, Math.min(5, Math.floor(dim * 0.02)));
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
    [pad, q3],
    [dim - 1 - pad, q3],
    [pad, dim - 1 - pad],
    [dim - 1 - pad, dim - 1 - pad],
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
  if (transparentBorder >= 4) {
    return true; // already alpha-transparent PNG/WebP
  }
  if (rs.length < 6) {
    return false;
  }
  const median = (arr: number[]) => {
    const s = [...arr].sort((a, b) => a - b);
    return s[s.length >> 1];
  };
  const bgR = median(rs);
  const bgG = median(gs);
  const bgB = median(bs);

  const colorDist = (r: number, g: number, b: number): number => {
    const dr = r - bgR;
    const dg = g - bgG;
    const db = b - bgB;
    const w = Math.sqrt(0.30 * dr * dr + 0.59 * dg * dg + 0.11 * db * db);
    const maxDiff = Math.max(Math.abs(dr), Math.abs(dg), Math.abs(db));
    return w * 0.70 + maxDiff * 0.30;
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
  if (!isWhiteStudio && !isBlackStudio && !isGreenScreen && !isBlueScreen && uniformCount < rs.length - 1) {
    return false;
  }

  const total = dim * dim;
  const { distMap, queue, band1, band2, orig } = getKeyScratch(dim);
  distMap.fill(255); // 0=exterior bg, 1=silhouette edge-1, 2=edge-2, 255=deep interior
  let head = 0;
  let tail = 0;
  const floorY = Math.floor(dim * 0.78);

  const isClearBg = (p: number): boolean => {
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const d = colorDist(r, g, b);
    if (isWhiteStudio) {
      const ch = Math.max(r, g, b) - Math.min(r, g, b);
      if (d <= 20 && ch <= 20) return true;
      if (d <= 52 && ch <= 14 && Math.min(r, g, b) >= bgMin - 65) return true;
      const y = (p / dim) | 0;
      if (y >= floorY && d <= 102 && ch <= 20 && Math.min(r, g, b) >= 118) return true;
      return false;
    }
    if (isBlackStudio) {
      const ch = Math.max(r, g, b) - Math.min(r, g, b);
      return d <= 22 && ch <= 20;
    }
    if (isGreenScreen) {
      return d <= 52 && g >= Math.max(r, b) + 14;
    }
    if (isBlueScreen) {
      return d <= 52 && b >= Math.max(r, g) + 14;
    }
    return d <= 26;
  };

  const trySeed = (p: number) => {
    if (distMap[p] === 0) return;
    if (isClearBg(p)) {
      distMap[p] = 0;
      queue[tail++] = p;
    }
  };

  // Seed all 4 edges wherever border pixels clearly match the studio backdrop
  const lastRow = (dim - 1) * dim;
  for (let x = 0; x < dim; x++) {
    trySeed(x);
    trySeed(lastRow + x);
  }
  for (let y = 1; y < dim - 1; y++) {
    trySeed(y * dim);
    trySeed(y * dim + (dim - 1));
  }

  // Pass 1: Exterior flood-fill
  while (head < tail) {
    const p = queue[head++];
    const x = p % dim;
    const y = (p - x) / dim;
    if (x > 0 && distMap[p - 1] === 255 && isClearBg(p - 1)) {
      distMap[p - 1] = 0;
      queue[tail++] = p - 1;
    }
    if (x < dim - 1 && distMap[p + 1] === 255 && isClearBg(p + 1)) {
      distMap[p + 1] = 0;
      queue[tail++] = p + 1;
    }
    if (y > 0 && distMap[p - dim] === 255 && isClearBg(p - dim)) {
      distMap[p - dim] = 0;
      queue[tail++] = p - dim;
    }
    if (y < dim - 1 && distMap[p + dim] === 255 && isClearBg(p + dim)) {
      distMap[p + dim] = 0;
      queue[tail++] = p + dim;
    }
  }

  // Clear exterior background & identify 8-connected silhouette band 1
  let b1Len = 0;
  for (let p = 0; p < total; p++) {
    if (distMap[p] === 0) {
      data[p * 4 + 3] = 0;
      continue;
    }
    const x = p % dim;
    const y = (p - x) / dim;
    let touchesBg = false;
    const y0 = y > 0 ? y - 1 : 0;
    const y1 = y < dim - 1 ? y + 1 : dim - 1;
    const x0 = x > 0 ? x - 1 : 0;
    const x1 = x < dim - 1 ? x + 1 : dim - 1;
    for (let ny = y0; ny <= y1 && !touchesBg; ny++) {
      const row = ny * dim;
      for (let nx = x0; nx <= x1; nx++) {
        if (distMap[row + nx] === 0) {
          touchesBg = true;
          break;
        }
      }
    }
    if (touchesBg) {
      distMap[p] = 1;
      band1[b1Len++] = p;
    }
  }

  // Identify band 2 (1px deeper into foreground — catches H.264 4:2:0 / bilinear fringe)
  let b2Len = 0;
  for (let k = 0; k < b1Len; k++) {
    const p = band1[k];
    const x = p % dim;
    const y = (p - x) / dim;
    const y0 = y > 0 ? y - 1 : 0;
    const y1 = y < dim - 1 ? y + 1 : dim - 1;
    const x0 = x > 0 ? x - 1 : 0;
    const x1 = x < dim - 1 ? x + 1 : dim - 1;
    for (let ny = y0; ny <= y1; ny++) {
      const row = ny * dim;
      for (let nx = x0; nx <= x1; nx++) {
        const np = row + nx;
        if (distMap[np] === 255) {
          distMap[np] = 2;
          band2[b2Len++] = np;
        }
      }
    }
  }

  // Snapshot original RGB so neighbor foreground sampling is clean and order-independent
  orig.set(data);

  const smoothstep = (t: number) => {
    const c = t < 0 ? 0 : t > 1 ? 1 : t;
    return c * c * (3 - 2 * c);
  };

  let fgR = 0;
  let fgG = 0;
  let fgB = 0;
  const sampleInteriorFg = (p: number, minDist: number): boolean => {
    const x = p % dim;
    const y = (p - x) / dim;
    let sr = 0;
    let sg = 0;
    let sb = 0;
    let cnt = 0;
    for (let rad = 2; rad <= 3; rad++) {
      const y0 = y - rad > 0 ? y - rad : 0;
      const y1 = y + rad < dim ? y + rad : dim - 1;
      const x0 = x - rad > 0 ? x - rad : 0;
      const x1 = x + rad < dim ? x + rad : dim - 1;
      for (let ny = y0; ny <= y1; ny++) {
        const row = ny * dim;
        for (let nx = x0; nx <= x1; nx++) {
          const np = row + nx;
          if (distMap[np] >= minDist) {
            const ni = np * 4;
            sr += orig[ni];
            sg += orig[ni + 1];
            sb += orig[ni + 2];
            cnt++;
          }
        }
      }
      if (cnt >= 3) break;
    }
    if (cnt === 0) return false;
    fgR = sr / cnt;
    fgG = sg / cnt;
    fgB = sb / cnt;
    return true;
  };

  const despillChroma = (r: number, g: number, b: number, strength: number): [number, number, number] => {
    if (isGreenScreen) {
      const rbMax = r > b ? r : b;
      if (g > rbMax) g = Math.round(g - (g - rbMax) * strength);
    } else if (isBlueScreen) {
      const rgMax = r > g ? r : g;
      if (b > rgMax) b = Math.round(b - (b - rgMax) * strength);
    }
    return [r, g, b];
  };

  // Pass 2: Decontaminate band 2 pixels pulled toward backdrop (H.264 / bilinear bleed)
  for (let k = 0; k < b2Len; k++) {
    const p = band2[k];
    if (!sampleInteriorFg(p, 255)) continue;
    const i = p * 4;
    const r = orig[i];
    const g = orig[i + 1];
    const b = orig[i + 2];
    const vx = fgR - bgR;
    const vy = fgG - bgG;
    const vz = fgB - bgB;
    const denom = vx * vx + vy * vy + vz * vz;
    if (denom < 180) continue;
    const proj = ((r - bgR) * vx + (g - bgG) * vy + (b - bgB) * vz) / denom;
    if (proj < 0.88) {
      const a = smoothstep((proj - 0.12) / 0.78);
      if (a < 0.06) {
        data[i + 3] = 0;
      } else {
        const mixFg = Math.min(1, (1 - proj) * 1.15);
        const nr = Math.max(0, Math.min(255, Math.round(r + (fgR - r) * mixFg)));
        const ng = Math.max(0, Math.min(255, Math.round(g + (fgG - g) * mixFg)));
        const nb = Math.max(0, Math.min(255, Math.round(b + (fgB - b) * mixFg)));
        const [dr, dg, db] = despillChroma(nr, ng, nb, 0.75);
        data[i] = dr;
        data[i + 1] = dg;
        data[i + 2] = db;
        data[i + 3] = Math.round(Math.max(0.25, a) * 255);
      }
    } else if (isGreenScreen || isBlueScreen) {
      const [dr, dg, db] = despillChroma(r, g, b, 0.65);
      data[i] = dr;
      data[i + 1] = dg;
      data[i + 2] = db;
    }
  }

  // Pass 3: Analytical alpha + full color decontamination along band 1 silhouette
  for (let k = 0; k < b1Len; k++) {
    const p = band1[k];
    const i = p * 4;
    const r = orig[i];
    const g = orig[i + 1];
    const b = orig[i + 2];
    if (!sampleInteriorFg(p, 255) && !sampleInteriorFg(p, 2)) {
      data[i + 3] = 0;
      continue;
    }
    const vx = fgR - bgR;
    const vy = fgG - bgG;
    const vz = fgB - bgB;
    const denom = vx * vx + vy * vy + vz * vz;
    if (denom < 140) {
      const d = colorDist(r, g, b);
      const a = smoothstep((d - 14) / 30);
      data[i + 3] = Math.round(a * 255);
      continue;
    }
    const proj = ((r - bgR) * vx + (g - bgG) * vy + (b - bgB) * vz) / denom;
    const a = smoothstep((proj - 0.14) / 0.78);
    if (a < 0.05) {
      data[i + 3] = 0;
    } else {
      const [dr, dg, db] = despillChroma(
        Math.max(0, Math.min(255, Math.round(fgR))),
        Math.max(0, Math.min(255, Math.round(fgG))),
        Math.max(0, Math.min(255, Math.round(fgB))),
        0.92,
      );
      data[i] = dr;
      data[i + 1] = dg;
      data[i + 2] = db;
      data[i + 3] = Math.round(a * 255);
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
  const dpr = typeof window !== "undefined" ? Math.min(3, Math.max(1, window.devicePixelRatio || 1)) : 2;
  // Supersample keying on 1x/1.25x PC monitors so edges are razor-sharp and never blocky
  const canvasDim = clip
    ? Math.min(384, Math.max(224, Math.round(size * Math.max(2, dpr))))
    : Math.min(512, Math.max(256, Math.round(size * Math.max(2.5, dpr))));

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
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = "high";
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
    if (shouldKeyBackdrop && canvasReady) {
      // Canvas paints its own full-bleed backdrop; keep container transparent so Chromium/Windows
      // subpixel border-radius antialiasing never bleeds a white rim around the circle.
      base = { background: "transparent" };
    } else if (!dragon && !studio) {
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
      base = { background: isDark ? "#121318" : "#f1efeb" };
    }
    return {
      ...base,
      // Force GPU-composited circular clipping without subpixel white-edge bleed on Windows DPI scaling
      clipPath: "circle(50% at 50% 50%)",
      WebkitMaskImage: "-webkit-radial-gradient(white, black)",
      transform: "translateZ(0)",
    };
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
            className="pointer-events-none absolute -inset-[1px] z-0 h-[calc(100%+2px)] w-[calc(100%+2px)] max-w-none object-cover"
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
            className="absolute -inset-[1px] z-0 h-[calc(100%+2px)] w-[calc(100%+2px)] max-w-none object-cover"
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
            className="pointer-events-none absolute -inset-[1px] z-10 h-[calc(100%+2px)] w-[calc(100%+2px)] max-w-none"
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
        style={{ width: size, height: size, ...outerGlowStyle }}
      >
        {face}
      </button>
    );
  }
  return (
    <span role="img" aria-label={label} className={box} style={{ width: size, height: size, ...outerGlowStyle }}>
      {face}
    </span>
  );
}
