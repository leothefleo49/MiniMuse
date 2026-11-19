import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { fileUrl } from "../api";
import { dragonClipUrl, dragonUrl, isDragon, studioClipUrl, studioUrl } from "../avatars";
import { useT } from "../i18n";
import { useMood } from "../mood";
import type { Profile, Status } from "../types";
import { cx } from "../util";

/**
 * The agent's face: the dragon (posed by what the agent is doing), a face drawn in the avatar
 * studio or an emoji on a colour. The dragon and a studio face play a short looping clip per
 * mood where there is one — a head shake at rest, headphones and a laptop while working, a
 * crystal ball while waiting, a star when pleased, the same clips the phone plays — and show
 * the still otherwise: small sizes, lists and pickers, a face without clips, or a clip that is
 * still syncing. Supports matching the active app theme (light/dark/custom), a custom background
 * colour, or a custom background image (with real-time border-connected flood-fill keying of
 * the studio backdrop), plus a separate border color & glow setting.
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
  const [dark, setDark] = useState<boolean>(() =>
    typeof document !== "undefined" ? document.documentElement.classList.contains("dark") : false,
  );
  useEffect(() => {
    if (typeof document === "undefined" || typeof MutationObserver === "undefined") return;
    const root = document.documentElement;
    const update = () => setDark(root.classList.contains("dark"));
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ["class", "style"] });
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

/**
 * Border-connected flood-fill background keyer: removes the contiguous studio backdrop
 * connected to the outer perimeter of the frame while keeping interior white/light details
 * (such as chest emblems, eyes, and highlights) 100% opaque.
 */
function keyStudioBackdrop(ctx: CanvasRenderingContext2D, dim: number): boolean {
  let imgData: ImageData;
  try {
    imgData = ctx.getImageData(0, 0, dim, dim);
  } catch {
    return false;
  }
  const data = imgData.data;
  const sampleCoords = [
    [2, 2],
    [dim - 3, 2],
    [2, dim - 3],
    [dim - 3, dim - 3],
    [dim >> 1, 2],
    [2, dim >> 1],
    [dim - 3, dim >> 1],
  ];
  let sumR = 0;
  let sumG = 0;
  let sumB = 0;
  let count = 0;
  for (const [sx, sy] of sampleCoords) {
    const idx = (sy * dim + sx) * 4;
    if (data[idx + 3] < 40) continue;
    sumR += data[idx];
    sumG += data[idx + 1];
    sumB += data[idx + 2];
    count++;
  }
  if (count === 0) return true; // already transparent
  const bgR = Math.round(sumR / count);
  const bgG = Math.round(sumG / count);
  const bgB = Math.round(sumB / count);
  const isBrightWhiteBg = bgR > 205 && bgG > 205 && bgB > 205;

  const total = dim * dim;
  const visited = new Uint8Array(total);
  const queue = new Int32Array(total);
  let head = 0;
  let tail = 0;

  const isBackdropPixel = (p: number): boolean => {
    const i = p * 4;
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const dist = Math.abs(r - bgR) + Math.abs(g - bgG) + Math.abs(b - bgB);
    if (dist <= 76) return true;
    if (isBrightWhiteBg && r > 192 && g > 192 && b > 192) {
      const spread = Math.max(r, g, b) - Math.min(r, g, b);
      if (spread <= 28) return true;
    }
    return false;
  };

  const tryEnqueue = (p: number) => {
    if (visited[p] !== 0) return;
    if (isBackdropPixel(p)) {
      visited[p] = 1;
      queue[tail++] = p;
    } else {
      visited[p] = 2; // silhouette boundary pixel
    }
  };

  // Seed perimeter pixels
  for (let x = 0; x < dim; x++) {
    tryEnqueue(x);
    tryEnqueue((dim - 1) * dim + x);
  }
  for (let y = 1; y < dim - 1; y++) {
    tryEnqueue(y * dim);
    tryEnqueue(y * dim + (dim - 1));
  }

  while (head < tail) {
    const p = queue[head++];
    data[p * 4 + 3] = 0;
    const x = p % dim;
    const y = (p - x) / dim;
    if (x > 0) tryEnqueue(p - 1);
    if (x < dim - 1) tryEnqueue(p + 1);
    if (y > 0) tryEnqueue(p - dim);
    if (y < dim - 1) tryEnqueue(p + dim);
  }

  // Soften 1px silhouette boundary pixels to eliminate any white halo fringe
  for (let p = 0; p < total; p++) {
    if (visited[p] === 2) {
      const i = p * 4;
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      const dist = Math.abs(r - bgR) + Math.abs(g - bgG) + Math.abs(b - bgB);
      if (dist < 135) {
        const factor = Math.max(0.15, dist / 135);
        data[i + 3] = Math.round(data[i + 3] * factor);
      }
    }
  }

  ctx.putImageData(imgData, 0, 0);
  return true;
}

export function Avatar({
  profile,
  status,
  size = 40,
  onClick,
  still = false,
  className,
}: {
  profile: Profile | null;
  status?: Status;
  size?: number;
  onClick?: () => void;
  /** no idle animation (lists, pickers) */
  still?: boolean;
  className?: string;
}) {
  const t = useT();
  const [, bump] = useState(0);
  const isDark = useIsDarkTheme();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [canvasReady, setCanvasReady] = useState(false);

  const mood = useMood(status);
  const state = status?.state ?? "idle";
  const color = profile?.color ?? "#0064d4";
  const bgMode = profile?.avatar_bg_mode || "theme";
  const bgColor = profile?.avatar_bg_color || color;
  const bgImageUrl = resolveBgImageUrl(profile?.avatar_bg_image);

  const borderMode = profile?.avatar_border_mode ?? "theme";
  const borderColor =
    borderMode === "color" ? profile?.avatar_border_color || color : "var(--accent, #6366f1)";
  const borderGlowPct = Math.max(0, Math.min(100, profile?.avatar_border_glow ?? 55));

  // the dragon is the default: no profile yet, or a profile that names it (or a retired face)
  const dragon = !profile || isDragon(profile.avatar);
  // a face from the studio: anything else that is not the emoji
  const studio = !dragon && profile?.avatar ? profile.avatar : null;
  // Never switch pose on click/tap so the avatar never flashes a different camera angle
  const pose = still ? "idle" : mood;
  const updatedAt = profile?.updated_at;
  const situationClips = profile?.avatar_situation_clips;

  const clipCandidate =
    still || size < CLIP_MIN_PX
      ? null
      : dragon
        ? dragonClipUrl(pose)
        : studio
          ? studioClipUrl(studio, pose, false, updatedAt, situationClips)
          : null;
  const clip = clipCandidate && !isClipTemporarilyMissing(clipCandidate) ? clipCandidate : null;

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
  const stillUrl = dragon
    ? dragonUrl(pose)
    : studio
      ? customStillOverride || studioUrl(studio, pose, false, updatedAt)
      : "";

  const shouldKeyBackdrop = (dragon || studio) && bgMode !== "default";
  const canvasDim = Math.min(168, Math.max(72, Math.round(size * 1.6)));

  const paintKeyedSource = useCallback(
    (source: HTMLVideoElement | HTMLImageElement | null) => {
      if (!shouldKeyBackdrop || !source) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return;
      try {
        ctx.clearRect(0, 0, canvasDim, canvasDim);
        ctx.drawImage(source, 0, 0, canvasDim, canvasDim);
        if (keyStudioBackdrop(ctx, canvasDim)) {
          setCanvasReady(true);
        }
      } catch {
        /* ignore cross-origin or incomplete frame */
      }
    },
    [shouldKeyBackdrop, canvasDim],
  );

  useEffect(() => {
    setCanvasReady(false);
  }, [clip, stillUrl, shouldKeyBackdrop]);

  useEffect(() => {
    if (!shouldKeyBackdrop || !clip) return;
    if (
      typeof window === "undefined" ||
      typeof window.requestAnimationFrame !== "function" ||
      typeof window.cancelAnimationFrame !== "function"
    ) {
      return;
    }
    let rafId = 0;
    let lastDraw = 0;
    const tick = (now: number) => {
      const v = videoRef.current;
      if (v && v.readyState >= 2 && now - lastDraw >= 32) {
        lastDraw = now;
        paintKeyedSource(v);
      }
      rafId = window.requestAnimationFrame(tick);
    };
    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [shouldKeyBackdrop, clip, paintKeyedSource]);

  const borderStyles: CSSProperties = (() => {
    if (borderMode === "none") return {};
    const g = borderGlowPct / 100;
    const outerBlur = Math.max(4, Math.round(size * 0.22 * (0.45 + g)));
    const outerSpread = Math.max(1, Math.round(size * 0.045 * (0.4 + g)));
    const innerBlur = Math.max(3, Math.round(size * 0.14 * (0.4 + g)));
    const outerAlpha = Math.round(28 + g * 54);
    const innerAlpha = Math.round(24 + g * 48);
    const ringAlpha = Math.round(45 + g * 50);
    return {
      border: `${size >= 56 ? 2 : 1.5}px solid color-mix(in srgb, ${borderColor} ${ringAlpha}%, transparent)`,
      boxShadow:
        g > 0
          ? `0 0 ${outerBlur}px ${outerSpread}px color-mix(in srgb, ${borderColor} ${outerAlpha}%, transparent), inset 0 0 ${innerBlur}px color-mix(in srgb, ${borderColor} ${innerAlpha}%, transparent)`
          : undefined,
    };
  })();

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
          ? "radial-gradient(circle at 50% 38%, color-mix(in srgb, var(--surface-2, #23252b) 88%, #334155), var(--bg, #111215))"
          : "radial-gradient(circle at 50% 38%, #ffffff, var(--surface-2, #ebe8e2))",
      };
    } else {
      base = { background: "#f1efeb" };
    }
    return { ...base, ...borderStyles };
  })();

  const label = t("{name}'s avatar", { name: profile?.name ?? "MiniMuse" });
  const box = cx("relative inline-block shrink-0 select-none rounded-full", className);
  const face = (
    <>
      <span
        className={cx("relative block h-full w-full overflow-hidden rounded-full", motion)}
        style={discStyle}
      >
        {clip ? (
          <video
            ref={videoRef}
            key={clip}
            src={clip}
            poster={stillUrl}
            autoPlay
            loop
            muted
            playsInline
            disablePictureInPicture
            onLoadedData={(e) => {
              const v = e.currentTarget;
              v.muted = true;
              paintKeyedSource(v);
              void v.play().catch(() => undefined);
            }}
            onError={() => {
              noClipUntil.set(clip, Date.now() + NO_CLIP_TTL_MS);
              bump((n) => n + 1);
              window.setTimeout(() => bump((n) => n + 1), NO_CLIP_TTL_MS + 100);
            }}
            className={cx("h-full w-full object-cover", shouldKeyBackdrop && canvasReady && "opacity-0")}
          />
        ) : dragon || studio ? (
          <img
            ref={imgRef}
            src={stillUrl}
            alt=""
            draggable={false}
            onLoad={(e) => paintKeyedSource(e.currentTarget)}
            className={cx("h-full w-full object-cover", shouldKeyBackdrop && canvasReady && "opacity-0")}
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
            className={cx(
              "pointer-events-none absolute inset-0 h-full w-full object-cover transition-opacity duration-75",
              canvasReady ? "opacity-100" : "opacity-0",
            )}
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
