import { useEffect, useState, type CSSProperties } from "react";
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
 * colour, or a custom background image.
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
  const [wiggle, setWiggle] = useState(false);
  const [, bump] = useState(0);
  const isDark = useIsDarkTheme();

  useEffect(() => {
    if (!wiggle) return;
    const id = window.setTimeout(() => setWiggle(false), 900);
    return () => window.clearTimeout(id);
  }, [wiggle]);

  const mood = useMood(status);
  const state = status?.state ?? "idle";
  const color = profile?.color ?? "#0064d4";
  const bgMode = profile?.avatar_bg_mode || "theme";
  const bgColor = profile?.avatar_bg_color || color;
  const bgImageUrl = resolveBgImageUrl(profile?.avatar_bg_image);

  // the dragon is the default: no profile yet, or a profile that names it (or a retired face)
  const dragon = !profile || isDragon(profile.avatar);
  // a face from the studio: anything else that is not the emoji
  const studio = !dragon && profile?.avatar ? profile.avatar : null;
  const pose = wiggle ? "happy" : still ? "idle" : mood;
  const useDarkVariant = bgMode === "theme" && isDark;

  const clipCandidate =
    still || size < CLIP_MIN_PX
      ? null
      : dragon
        ? dragonClipUrl(pose)
        : studio
          ? studioClipUrl(studio, pose, useDarkVariant)
          : null;
  const clip = clipCandidate && !isClipTemporarilyMissing(clipCandidate) ? clipCandidate : null;
  const motion = wiggle
    ? "avatar-wiggle"
    : still || clip
      ? ""
      : state === "working"
        ? "avatar-working"
        : state === "waiting"
          ? "avatar-waiting"
          : "avatar-idle";
  const stillUrl = dragon ? dragonUrl(pose) : studio ? studioUrl(studio, pose, useDarkVariant) : "";

  const discStyle: CSSProperties = (() => {
    if (!dragon && !studio) {
      if (bgMode === "image" && bgImageUrl) {
        return {
          backgroundImage: `url("${bgImageUrl}")`,
          backgroundSize: "cover",
          backgroundPosition: "center",
        };
      }
      if (bgMode === "theme") {
        return { background: "var(--surface-2, #f1efeb)" };
      }
      const c = bgMode === "color" ? bgColor : color;
      return { background: `linear-gradient(135deg, ${c}, color-mix(in srgb, ${c} 60%, #ffffff))` };
    }
    if (bgMode === "image" && bgImageUrl) {
      return {
        backgroundImage: `url("${bgImageUrl}")`,
        backgroundSize: "cover",
        backgroundPosition: "center",
      };
    }
    if (bgMode === "color" && bgColor) {
      return {
        background: `linear-gradient(135deg, ${bgColor}, color-mix(in srgb, ${bgColor} 65%, #ffffff))`,
        boxShadow: `inset 0 0 0 2.5px ${bgColor}`,
      };
    }
    if (bgMode === "theme") {
      return { background: isDark ? "var(--surface-2, #18191c)" : "var(--bg, #f1efeb)" };
    }
    return { background: "#f1efeb" };
  })();

  // When a custom background color or image is active on a studio/dragon face (whose video frames
  // carry a baked studio backdrop), feather the outer ring and apply a gentle tint/vignette so the
  // custom background color or image is always visible around the live character.
  const mediaStyle: CSSProperties | undefined =
    (dragon || studio) && ((bgMode === "color" && bgColor) || (bgMode === "image" && bgImageUrl))
      ? {
          WebkitMaskImage: "radial-gradient(circle, #000 54%, rgba(0,0,0,0.72) 72%, transparent 95%)",
          maskImage: "radial-gradient(circle, #000 54%, rgba(0,0,0,0.72) 72%, transparent 95%)",
        }
      : undefined;

  const label = t("{name}'s avatar", { name: profile?.name ?? "MiniMuse" });
  const box = cx("relative inline-block shrink-0 select-none rounded-full", className);
  const face = (
    <>
      <span
        className={cx("relative block h-full w-full overflow-hidden rounded-full will-change-transform", motion)}
        style={discStyle}
      >
        {clip ? (
          <video
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
              void v.play().catch(() => undefined);
            }}
            onError={() => {
              noClipUntil.set(clip, Date.now() + NO_CLIP_TTL_MS);
              bump((n) => n + 1);
              window.setTimeout(() => bump((n) => n + 1), NO_CLIP_TTL_MS + 100);
            }}
            style={mediaStyle}
            className="h-full w-full object-cover"
          />
        ) : dragon || studio ? (
          <img src={stillUrl} alt="" draggable={false} style={mediaStyle} className="h-full w-full object-cover" />
        ) : (
          <span className="flex h-full w-full items-center justify-center leading-none drop-shadow-sm" style={{ fontSize: size * 0.5 }}>
            {profile?.emoji ?? "✨"}
          </span>
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

  // A button of its own only when it does something; inside another button (the chat header)
  // it is plain markup that still wiggles when tapped.
  if (onClick) {
    return (
      <button
        type="button"
        onClick={() => {
          setWiggle(true);
          onClick();
        }}
        aria-label={label}
        className={cx(box, "transition active:scale-95")}
        style={{ width: size, height: size }}
      >
        {face}
      </button>
    );
  }
  return (
    <span role="img" aria-label={label} onClick={() => setWiggle(true)} className={box} style={{ width: size, height: size }}>
      {face}
    </span>
  );
}
