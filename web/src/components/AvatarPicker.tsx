import { Check, Film, ImagePlus, Loader2, Play, Plus, RefreshCw, Sparkles, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fileUrl } from "../api";
import { DRAGON, DRAGON_CLIPS, dragonUrl, isDragon, studioUrl } from "../avatars";
import { useT } from "../i18n";
import type { AvatarLibraryView } from "../types";
import { cx } from "../util";

const ANIMATION_PRESETS: ReadonlyArray<{ label: string; prompt: string }> = [
  { label: "Wave hello", prompt: "The character smiles warmly and waves hello enthusiastically with one hand while gently bobbing" },
  { label: "Victory dance", prompt: "The character does a fun energetic victory dance, pumping both fists and bouncing happily" },
  { label: "Power up aura", prompt: "The character powers up with dramatic glowing energy swirling around it, hair and clothes rippling" },
  { label: "Meditating", prompt: "The character floats slightly and meditates peacefully with eyes closed and calm breathing" },
  { label: "Coffee break", prompt: "The character holds a warm mug with both hands, takes a cozy sip, and nods contentedly" },
  { label: "Thumbs up", prompt: "The character gives an encouraging thumbs up with a confident grin and slight bounce" },
];

const EMOJI = ["✨", "🌙", "🪐", "🌿", "🔥", "🌊", "🦉", "🦊", "🐙", "🎯", "🧭", "💎", "🍀", "🎈", "🤖", "🧠"];
export const AVATAR_COLORS = ["#0064d4", "#7c3aed", "#0891b2", "#059669", "#d97706", "#dc2626", "#db2777", "#4b5563"];

export interface AvatarChoice {
  avatar: string;
  emoji: string;
  color: string;
  avatar_bg_mode?: string;
  avatar_bg_color?: string;
  avatar_bg_image?: string;
  avatar_border_mode?: string;
  avatar_border_color?: string;
  avatar_border_glow?: number;
  avatar_animated?: boolean;
  avatar_situation_clips?: Record<string, string>;
}

/** The looks a face can be drawn in — the phone's list, same ids; the runtime knows the words. */
export const AVATAR_STYLES: { id: string; label: string }[] = [
  { id: "muse", label: "3D toy (Muse)" },
  { id: "flat", label: "Flat" },
  { id: "clay", label: "3D clay" },
  { id: "watercolor", label: "Watercolour" },
  { id: "pixel", label: "Pixel" },
  { id: "line", label: "Line" },
  { id: "sticker", label: "Sticker" },
];

const SITUATION_ROWS: { id: string; label: string }[] = [
  { id: "idle", label: "Idle / At rest" },
  { id: "working", label: "Working / Thinking" },
  { id: "waiting", label: "Waiting / Making something" },
  { id: "happy", label: "Happy / Milestone" },
  { id: "call", label: "Live Voice Call" },
  { id: "error", label: "Error / Alert" },
];

/**
 * The dragon, every imported or drawn 3D/studio face in `workspace/avatar/`, a tile that opens
 * the studio, an emoji on a colour, separate avatar background and border color/glow controls,
 * and per-situation animation customization + custom animation / 3D model `.zip` upload.
 */
export function AvatarPicker({
  value,
  onChange,
  onGlowPreview,
  onPreviewClip,
  previewClipUrl = null,
  onRedrawMoods,
  onStudio,
}: {
  value: AvatarChoice;
  onChange: (v: AvatarChoice) => void;
  /** Optional 60fps local preview callback while dragging the glow slider so no network call fires mid-drag */
  onGlowPreview?: (glow: number | null) => void;
  /** Optional callback to live-preview any animation clip in the Avatar Studio large preview */
  onPreviewClip?: (clip: { url: string; label: string } | null) => void;
  previewClipUrl?: string | null;
  /** Optional callback to redraw/generate standard mood poses & clips */
  onRedrawMoods?: () => void;
  /** the "draw a new one" tile: opens the avatar studio screen */
  onStudio?: () => void;
}) {
  const t = useT();
  const [uploadingBg, setUploadingBg] = useState(false);
  const [uploadingAsset, setUploadingAsset] = useState(false);
  const [savingSit, setSavingSit] = useState(false);
  const [library, setLibrary] = useState<AvatarLibraryView | null>(null);
  const [showMakeAnim, setShowMakeAnim] = useState(false);
  const [animPrompt, setAnimPrompt] = useState("");
  const [animLabel, setAnimLabel] = useState("");
  const [animSituation, setAnimSituation] = useState("idle");
  const [makingAnim, setMakingAnim] = useState(false);
  const [animError, setAnimError] = useState("");
  const [animSuccess, setAnimSuccess] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const assetFileRef = useRef<HTMLInputElement>(null);

  const serverGlow = typeof value.avatar_border_glow === "number" ? value.avatar_border_glow : 55;
  const [draftGlow, setDraftGlow] = useState<number>(serverGlow);
  const draggingGlowRef = useRef(false);
  const glowCommitTimer = useRef<number | null>(null);
  const latestValueRef = useRef(value);
  useEffect(() => {
    latestValueRef.current = value;
  }, [value]);

  useEffect(() => {
    if (!draggingGlowRef.current) setDraftGlow(serverGlow);
  }, [serverGlow]);

  useEffect(() => {
    return () => {
      if (glowCommitTimer.current !== null) window.clearTimeout(glowCommitTimer.current);
    };
  }, []);

  const commitGlow = (nextGlow: number) => {
    if (glowCommitTimer.current !== null) {
      window.clearTimeout(glowCommitTimer.current);
      glowCommitTimer.current = null;
    }
    draggingGlowRef.current = false;
    onGlowPreview?.(null);
    if (nextGlow !== (typeof latestValueRef.current.avatar_border_glow === "number" ? latestValueRef.current.avatar_border_glow : 55)) {
      onChange({ ...latestValueRef.current, avatar_border_glow: nextGlow });
    }
  };

  const handleGlowInput = (nextGlow: number) => {
    draggingGlowRef.current = true;
    setDraftGlow(nextGlow);
    onGlowPreview?.(nextGlow);
    if (glowCommitTimer.current !== null) window.clearTimeout(glowCommitTimer.current);
    glowCommitTimer.current = window.setTimeout(() => {
      commitGlow(nextGlow);
    }, 320);
  };

  const loadLibrary = useCallback(async () => {
    try {
      const lib = await api.avatarLibrary();
      setLibrary(lib);
    } catch {
      /* offline or older runtime */
    }
  }, []);

  useEffect(() => {
    void loadLibrary();
  }, [loadLibrary]);

  const emojiMode = value.avatar === "";
  const isDragonSelected = value.avatar === DRAGON;
  const otherStudio = !emojiMode && !isDragon(value.avatar) ? value.avatar : null;
  const bgMode = value.avatar_bg_mode || "theme";
  const bgColor = value.avatar_bg_color || value.color || "#0064d4";
  const borderMode = value.avatar_border_mode || "theme";
  const borderColor = value.avatar_border_color || value.color || "#0064d4";
  const borderGlow = draftGlow;
  const animationsOn = value.avatar_animated !== false;
  const ring = "ring-[2.5px] ring-accent ring-offset-2 ring-offset-bg";
  const tile = "relative h-[72px] w-[72px] shrink-0 overflow-hidden rounded-full transition cursor-pointer";

  const libraryFaces = library?.faces ?? [];
  const knownFaceIds = new Set(libraryFaces.map((f) => f.id));
  const activeStudioFace = otherStudio ? (libraryFaces.find((f) => f.id === otherStudio) ?? null) : null;
  // Build unified assets for BOTH custom 3D faces AND the built-in Dragon so users can preview & switch animations for every avatar
  const activeFaceAssets = (() => {
    if (isDragonSelected) {
      return DRAGON_CLIPS.map((c) => ({
        file: c.file,
        path: c.path,
        url: c.path,
        kind: c.kind,
        label: t(c.label),
        size: 0,
        canonical: true,
      }));
    }
    if (!activeStudioFace) return [];
    if (Array.isArray(activeStudioFace.assets) && activeStudioFace.assets.length > 0) {
      return activeStudioFace.assets.map((a) => ({
        ...a,
        url: fileUrl(a.path),
      }));
    }
    const fallback: Array<{ file: string; path: string; url: string; kind: "video" | "image"; label: string; size: number; canonical: boolean }> = [];
    for (const c of activeStudioFace.clips ?? []) {
      if (c?.file) {
        const p = `avatar/${activeStudioFace.id}/${c.file}`;
        fallback.push({
          file: c.file,
          path: p,
          url: fileUrl(p),
          kind: "video",
          label: c.label || c.file,
          size: 0,
          canonical: false,
        });
      }
    }
    for (const s of activeStudioFace.stills ?? []) {
      if (s) {
        const p = `avatar/${activeStudioFace.id}/${s}`;
        fallback.push({
          file: s,
          path: p,
          url: fileUrl(p),
          kind: "image",
          label: s,
          size: 0,
          canonical: false,
        });
      }
    }
    return fallback;
  })();

  const videoAssets = activeFaceAssets.filter((a) => a.kind === "video");
  const activeFaceName = isDragonSelected
    ? t("The dragon")
    : activeStudioFace?.name || otherStudio || t("Avatar");

  const handleUploadBg = async (file: File) => {
    setUploadingBg(true);
    try {
      const uploaded = await api.upload(file);
      onChange({
        ...value,
        avatar_bg_mode: "image",
        avatar_bg_image: uploaded.path,
      });
    } catch {
      /* ignore upload error */
    } finally {
      setUploadingBg(false);
    }
  };

  const handleUploadAvatarAsset = async (file: File) => {
    setUploadingAsset(true);
    setAnimError("");
    try {
      const targetFace = otherStudio || "custom";
      const res = await api.avatarUploadAsset(targetFace, file);
      await loadLibrary();
      const nextFaceId = res.face_id || res.face?.id;
      if (nextFaceId) {
        onChange({
          ...value,
          avatar: nextFaceId,
          avatar_situation_clips: res.profile?.avatar_situation_clips ?? value.avatar_situation_clips,
        });
      }
    } catch (e) {
      setAnimError((e as Error).message);
    } finally {
      setUploadingAsset(false);
    }
  };

  const handleSituationChange = async (sitId: string, chosenFile: string) => {
    if (isDragonSelected) {
      const nextMap = { ...(value.avatar_situation_clips ?? {}), [sitId]: chosenFile };
      onChange({
        ...value,
        avatar: DRAGON,
        avatar_situation_clips: nextMap,
      });
      return;
    }
    const faceId = otherStudio || activeStudioFace?.id;
    if (!faceId) return;
    const nextMap = { ...(value.avatar_situation_clips ?? activeStudioFace?.situations ?? {}), [sitId]: chosenFile };
    onChange({
      ...value,
      avatar: faceId,
      avatar_situation_clips: nextMap,
    });
    setSavingSit(true);
    try {
      const res = await api.avatarSetSituations(faceId, { [sitId]: chosenFile });
      await loadLibrary();
      onChange({
        ...value,
        avatar: faceId,
        avatar_situation_clips: res.situations,
      });
    } catch {
      /* ignore error */
    } finally {
      setSavingSit(false);
    }
  };

  const handleGenerateAnimation = async () => {
    const prompt = animPrompt.trim();
    if (!prompt) return;
    setMakingAnim(true);
    setAnimError("");
    setAnimSuccess("");
    try {
      const targetFace = isDragonSelected ? DRAGON : otherStudio || activeStudioFace?.id || DRAGON;
      const res = await api.avatarAnimate(targetFace, {
        prompt,
        label: animLabel.trim() || undefined,
        situation: animSituation === "library" ? "" : animSituation,
      });
      await loadLibrary();
      if (res.face_id) {
        onChange({
          ...value,
          avatar: res.face_id,
          avatar_animated: true,
          avatar_situation_clips: res.profile?.avatar_situation_clips ?? value.avatar_situation_clips,
        });
      }
      if (res.path) {
        onPreviewClip?.({ url: fileUrl(res.path), label: res.label || prompt });
      }
      setAnimPrompt("");
      setAnimLabel("");
      setAnimSuccess(t("New live 3D animation created: {label}", { label: res.label || res.file }));
    } catch (e) {
      setAnimError((e as Error).message);
    } finally {
      setMakingAnim(false);
    }
  };

  const handleDeleteCustomAsset = async (file: string) => {
    const faceId = otherStudio || activeStudioFace?.id;
    if (!faceId) return;
    try {
      await api.avatarDeleteAsset(faceId, file);
      await loadLibrary();
    } catch (e) {
      setAnimError((e as Error).message);
    }
  };

  return (
    <div className="space-y-3.5">
      <div className="flex flex-wrap gap-3">
        {/* Dragon Default Avatar */}
        <button
          type="button"
          aria-label={t("The dragon")}
          aria-pressed={isDragonSelected}
          onClick={() => onChange({ ...value, avatar: DRAGON })}
          className={cx(tile, "bg-[#f1efeb]", isDragonSelected ? ring : "opacity-90 hover:opacity-100 hover:scale-105")}
          title={t("The dragon")}
        >
          <img src={dragonUrl(isDragonSelected ? "happy" : "idle")} alt="" draggable={false} className="h-full w-full object-cover" />
        </button>

        {/* All imported / studio 3D faces from workspace/avatar/ */}
        {libraryFaces.map((face) => {
          const selected = value.avatar === face.id;
          return (
            <button
              key={face.id}
              type="button"
              aria-label={face.name || t("The face drawn for you")}
              aria-pressed={selected}
              onClick={() => onChange({ ...value, avatar: face.id })}
              className={cx(tile, "bg-surface-2", selected ? ring : "opacity-90 hover:opacity-100 hover:scale-105")}
              title={face.name || face.id}
            >
              <img
                src={face.preview ? fileUrl(face.preview) : studioUrl(face.id, "idle")}
                alt=""
                draggable={false}
                className="h-full w-full object-cover"
              />
            </button>
          );
        })}

        {/* Fallback tile if profile references a studio face not yet in libraryFaces */}
        {otherStudio && !knownFaceIds.has(otherStudio) && (
          <button type="button" aria-label={t("The face drawn for you")} aria-pressed className={cx(tile, "bg-surface-2", ring)}>
            <img src={studioUrl(otherStudio, "happy")} alt="" draggable={false} className="h-full w-full object-cover" />
          </button>
        )}

        {/* Avatar Studio launcher */}
        {onStudio && (
          <button
            type="button"
            aria-label={t("Avatar studio")}
            onClick={onStudio}
            className={cx(tile, "flex items-center justify-center border border-dashed border-border text-accent hover:bg-surface-2 hover:scale-105")}
            title={t("Avatar studio")}
          >
            <Sparkles size={22} />
          </button>
        )}

        {/* Emoji Avatar */}
        <button
          type="button"
          aria-label={t("An emoji instead")}
          aria-pressed={emojiMode}
          onClick={() => onChange({ ...value, avatar: "" })}
          className={cx("flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-full text-[26px] transition cursor-pointer hover:scale-105", emojiMode ? ring : "opacity-90 hover:opacity-100")}
          style={{ background: `linear-gradient(135deg, ${value.color}, color-mix(in srgb, ${value.color} 60%, #ffffff))` }}
          title="Emoji"
        >
          {value.emoji}
        </button>
      </div>

      {emojiMode && (
        <div className="grid grid-cols-8 gap-1.5">
          {EMOJI.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => onChange({ ...value, emoji: e })}
              className={cx("flex aspect-square items-center justify-center rounded-2xl bg-surface-2 text-[22px]", value.emoji === e && "ring-2 ring-accent")}
            >
              {e}
            </button>
          ))}
        </div>
      )}

      {/* Avatar Background Mode: Original (unedited), Match theme, Custom color, or Custom image */}
      <div className="space-y-2 pt-1">
        <div className="text-[12px] text-muted">{t("Avatar background")}</div>
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              { id: "default", label: t("Original (unedited)") },
              { id: "theme", label: t("Match theme") },
              { id: "color", label: t("Custom color") },
              { id: "image", label: t("Custom image") },
            ] as const
          ).map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() =>
                onChange({
                  ...value,
                  avatar_bg_mode: opt.id,
                  avatar_bg_color: opt.id === "color" ? bgColor : value.avatar_bg_color,
                })
              }
              className={cx(
                "rounded-full border px-3 py-1 text-[12.5px] transition-colors",
                bgMode === opt.id ? "border-accent bg-accent/10 text-accent font-medium" : "border-border text-muted",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {(emojiMode || bgMode === "color") && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {AVATAR_COLORS.map((c) => {
              const activeColor = bgMode === "color" ? bgColor : value.color;
              return (
                <button
                  key={c}
                  type="button"
                  aria-label={c}
                  onClick={() =>
                    onChange({
                      ...value,
                      color: emojiMode ? c : value.color,
                      avatar_bg_color: c,
                    })
                  }
                  className="flex h-8 w-8 items-center justify-center rounded-full text-white"
                  style={{ background: c }}
                >
                  {activeColor === c && <Check size={16} />}
                </button>
              );
            })}
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[12px] text-muted hover:text-text">
              <input
                type="color"
                value={bgMode === "color" ? bgColor : (value.color || "#0064d4")}
                onChange={(e) =>
                  onChange({
                    ...value,
                    color: emojiMode ? e.target.value : value.color,
                    avatar_bg_color: e.target.value,
                    avatar_bg_mode: bgMode === "color" ? "color" : value.avatar_bg_mode,
                  })
                }
                aria-label={t("Custom")}
                className="h-4 w-4 cursor-pointer rounded-full border-0 bg-transparent p-0"
              />
              <span>{t("Custom")}</span>
            </label>
          </div>
        )}

        {bgMode === "image" && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleUploadBg(f);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              disabled={uploadingBg}
              onClick={() => fileRef.current?.click()}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface-2 px-3 py-1.5 text-[12.5px] font-medium hover:bg-surface"
            >
              <ImagePlus size={14} />
              {uploadingBg ? t("Uploading…") : t("Upload background image")}
            </button>
            <input
              type="text"
              value={value.avatar_bg_image || ""}
              onChange={(e) => onChange({ ...value, avatar_bg_mode: "image", avatar_bg_image: e.target.value })}
              placeholder={t("Image URL or workspace path")}
              className="min-w-[180px] flex-1 rounded-xl border border-border bg-surface px-3 py-1.5 text-[12.5px]"
            />
          </div>
        )}
      </div>

      {/* Separate Border Color & Glow Controls */}
      <div className="space-y-2 pt-1">
        <div className="flex items-center justify-between">
          <span className="text-[12px] text-muted">{t("Border color & glow")}</span>
          {borderMode !== "none" && (
            <span className="text-[11.5px] text-muted">
              {t("Glow")}: {borderGlow}%
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              { id: "theme", label: t("Match theme") },
              { id: "color", label: t("Custom border") },
              { id: "none", label: t("No border") },
            ] as const
          ).map((opt) => (
            <button
              key={opt.id}
              type="button"
              onClick={() =>
                onChange({
                  ...value,
                  avatar_border_mode: opt.id,
                  avatar_border_color: opt.id === "color" ? borderColor : value.avatar_border_color,
                })
              }
              className={cx(
                "rounded-full border px-3 py-1 text-[12.5px] transition-colors",
                borderMode === opt.id ? "border-accent bg-accent/10 text-accent font-medium" : "border-border text-muted",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {borderMode === "color" && (
          <div className="flex flex-wrap items-center gap-2 pt-1">
            {AVATAR_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                aria-label={c}
                onClick={() =>
                  onChange({
                    ...value,
                    avatar_border_mode: "color",
                    avatar_border_color: c,
                  })
                }
                className="flex h-7 w-7 items-center justify-center rounded-full text-white"
                style={{ background: c }}
              >
                {borderColor === c && <Check size={14} />}
              </button>
            ))}
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[12px] text-muted hover:text-text">
              <input
                type="color"
                value={borderColor}
                onChange={(e) =>
                  onChange({
                    ...value,
                    avatar_border_mode: "color",
                    avatar_border_color: e.target.value,
                  })
                }
                aria-label={t("Custom")}
                className="h-4 w-4 cursor-pointer rounded-full border-0 bg-transparent p-0"
              />
              <span>{t("Custom")}</span>
            </label>
          </div>
        )}

        {borderMode !== "none" && (
          <div className="flex items-center gap-3 pt-0.5 touch-none">
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={borderGlow}
              onChange={(e) => handleGlowInput(Number(e.target.value))}
              onPointerUp={(e) => commitGlow(Number((e.currentTarget as HTMLInputElement).value))}
              onTouchEnd={(e) => commitGlow(Number((e.currentTarget as HTMLInputElement).value))}
              onKeyUp={(e) => commitGlow(Number((e.currentTarget as HTMLInputElement).value))}
              aria-label={t("Border color & glow")}
              className="h-2 flex-1 cursor-pointer accent-accent touch-pan-x"
            />
          </div>
        )}
      </div>

      {/* Live Animations Switch */}
      {!emojiMode && (
        <div className="flex items-center justify-between gap-3 rounded-2xl border border-border/70 bg-surface-2/40 px-3.5 py-2.5">
          <div className="min-w-0">
            <div className="text-[13px] font-medium text-fg">{t("Live avatar animations")}</div>
            <div className="text-[11.5px] leading-snug text-muted">
              {animationsOn
                ? t("Plays looping 3D/video animations for each mood and situation.")
                : t("Animations paused — showing crisp still avatar images.")}
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={animationsOn}
            aria-label={t("Live avatar animations")}
            onClick={() => onChange({ ...value, avatar_animated: !animationsOn })}
            className={cx(
              "relative h-[24px] w-[42px] shrink-0 rounded-full transition-colors",
              animationsOn ? "bg-accent" : "bg-border",
            )}
          >
            <span
              className={cx(
                "absolute top-[3px] h-[18px] w-[18px] rounded-full bg-white shadow transition-all",
                animationsOn ? "left-[21px]" : "left-[3px]",
              )}
            />
          </button>
        </div>
      )}

      {/* Live 3D Animations Gallery, Situation Switcher & Make More Animations */}
      {!emojiMode && (
        <div className="rounded-2xl border border-border/70 bg-surface-2/40 p-3.5 text-[12.5px] space-y-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 font-semibold text-fg">
              <Film size={15} className="text-accent shrink-0" />
              <span>{t("3D animations & situations")}</span>
              <span className="rounded-full bg-surface px-2 py-0.5 text-[11px] font-medium text-muted border border-border/60">
                {videoAssets.length === 1 ? t("1 clip") : t("{n} clips", { n: videoAssets.length })}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                onClick={() => setShowMakeAnim((v) => !v)}
                className={cx(
                  "inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12px] font-medium transition",
                  showMakeAnim
                    ? "bg-accent text-accent-fg"
                    : "bg-accent/12 text-accent hover:bg-accent/20",
                )}
              >
                <Plus size={13} />
                {t("Make new animation")}
              </button>
              <input
                ref={assetFileRef}
                type="file"
                accept=".mp4,.webm,.webp,.png,.jpg,.jpeg,.gif,.zip,video/*,image/*"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUploadAvatarAsset(f);
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                disabled={uploadingAsset}
                onClick={() => assetFileRef.current?.click()}
                className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-3 py-1 text-[12px] font-medium hover:bg-surface-2 disabled:opacity-50"
              >
                <Upload size={12} />
                {uploadingAsset ? t("Uploading…") : t("Upload clip / .zip")}
              </button>
            </div>
          </div>

          {/* Make More Animations with AI Composer */}
          {showMakeAnim && (
            <div className="rounded-2xl border border-accent/35 bg-surface p-3 space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[12.5px] font-semibold text-fg flex items-center gap-1.5">
                  <Sparkles size={14} className="text-accent shrink-0" />
                  {t("Create a new live 3D animation for {name}", { name: activeFaceName })}
                </span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {ANIMATION_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    type="button"
                    onClick={() => {
                      setAnimPrompt(preset.prompt);
                      setAnimLabel(t(preset.label));
                    }}
                    className={cx(
                      "rounded-full border px-2.5 py-1 text-[11.5px] transition",
                      animPrompt === preset.prompt
                        ? "border-accent bg-accent/12 text-accent font-medium"
                        : "border-border bg-surface-2/60 text-muted hover:text-fg",
                    )}
                  >
                    {t(preset.label)}
                  </button>
                ))}
              </div>
              <textarea
                value={animPrompt}
                onChange={(e) => setAnimPrompt(e.target.value)}
                rows={2}
                placeholder={t("Describe the motion (e.g. waving hello, victory dance, powering up with red aura…)")}
                className="w-full resize-none rounded-xl border border-border bg-surface-2/60 px-3 py-2 text-[12.5px] outline-none focus:ring-2 focus:ring-accent/35"
              />
              <div className="flex flex-wrap items-center gap-2">
                <input
                  type="text"
                  value={animLabel}
                  onChange={(e) => setAnimLabel(e.target.value)}
                  placeholder={t("Short name (optional)")}
                  className="min-w-0 flex-1 rounded-xl border border-border bg-surface-2/60 px-3 py-1.5 text-[12px] outline-none focus:ring-2 focus:ring-accent/35"
                />
                <select
                  value={animSituation}
                  onChange={(e) => setAnimSituation(e.target.value)}
                  aria-label={t("Assign to situation")}
                  className="rounded-xl border border-border bg-surface-2 px-2.5 py-1.5 text-[12px] text-fg outline-none"
                >
                  <option value="idle">{t("Assign to: Idle")}</option>
                  <option value="working">{t("Assign to: Working")}</option>
                  <option value="waiting">{t("Assign to: Waiting")}</option>
                  <option value="happy">{t("Assign to: Happy")}</option>
                  <option value="call">{t("Assign to: Live Voice Call")}</option>
                  <option value="library">{t("Save to library only")}</option>
                </select>
                <button
                  type="button"
                  disabled={makingAnim || !animPrompt.trim()}
                  onClick={() => void handleGenerateAnimation()}
                  className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3.5 py-1.5 text-[12px] font-medium text-accent-fg disabled:opacity-50"
                >
                  {makingAnim ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                  {makingAnim ? t("Generating clip…") : t("Generate 3D clip")}
                </button>
              </div>
              {animError && <p className="text-[12px] break-words text-rose-600 dark:text-rose-300">{animError}</p>}
              {animSuccess && <p className="text-[12px] break-words text-emerald-600 dark:text-emerald-400">{animSuccess}</p>}
            </div>
          )}

          {/* Missing clips notice on a custom face */}
          {!isDragonSelected && videoAssets.length === 0 && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 space-y-2 text-[12px]">
              <p className="text-fg leading-snug">
                {t("No live 3D video clips are on this device for this avatar yet. Sync with your computer or generate live animations below.")}
              </p>
              <div className="flex flex-wrap gap-2">
                {onRedrawMoods && (
                  <button
                    type="button"
                    onClick={onRedrawMoods}
                    className="inline-flex items-center gap-1.5 rounded-full bg-accent px-3 py-1 text-[12px] font-medium text-accent-fg"
                  >
                    <RefreshCw size={12} />
                    {t("Generate standard poses & animations")}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setShowMakeAnim(true)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1 text-[12px] font-medium text-fg"
                >
                  <Plus size={12} />
                  {t("Make custom animation")}
                </button>
              </div>
            </div>
          )}

          {/* Visual Gallery of Live Animations — tap any clip to preview live or assign to a situation */}
          {videoAssets.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-[11.5px] font-medium text-muted">
                <span>{t("Tap any animation to preview it live")}</span>
                {savingSit && <span>{t("Saving…")}</span>}
              </div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {videoAssets.map((asset) => {
                  const usedFor = SITUATION_ROWS.filter((sit) => {
                    const mapped =
                      value.avatar_situation_clips?.[sit.id] ||
                      (isDragonSelected ? `dragon-${sit.id}.mp4` : activeStudioFace?.situations?.[sit.id]);
                    return mapped === asset.file || mapped === asset.path;
                  }).map((s) => s.id);
                  const isPreviewing = previewClipUrl === asset.url;
                  const isCustomDeletable = !isDragonSelected && asset.file.startsWith("custom-");
                  return (
                    <div
                      key={asset.file}
                      className={cx(
                        "group relative flex flex-col overflow-hidden rounded-2xl border bg-surface transition",
                        isPreviewing ? "border-accent ring-2 ring-accent/35" : "border-border/70 hover:border-accent/50",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => onPreviewClip?.(isPreviewing ? null : { url: asset.url, label: asset.label })}
                        className="relative aspect-square w-full overflow-hidden bg-surface-2 text-left"
                      >
                        <video
                          src={asset.url}
                          autoPlay
                          loop
                          muted
                          playsInline
                          preload="metadata"
                          className="h-full w-full object-cover"
                        />
                        <span className="absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-black/55 text-white">
                          <Play size={11} className="fill-current" />
                        </span>
                        {usedFor.length > 0 && (
                          <span className="absolute left-1.5 bottom-1.5 max-w-[calc(100%-12px)] truncate rounded-full bg-accent px-2 py-0.5 text-[10px] font-semibold text-accent-fg shadow-sm">
                            {usedFor.join(" · ")}
                          </span>
                        )}
                      </button>
                      <div className="flex items-center justify-between gap-1 px-2 py-1.5">
                        <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-fg" title={asset.label}>
                          {asset.label}
                        </span>
                        {isCustomDeletable && (
                          <button
                            type="button"
                            aria-label={t("Delete")}
                            onClick={() => void handleDeleteCustomAsset(asset.file)}
                            className="shrink-0 rounded-md p-1 text-muted hover:bg-surface-2 hover:text-rose-500"
                          >
                            <Trash2 size={12} />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Switch Animation Per Situation */}
          {activeFaceAssets.length > 0 && (
            <div className="space-y-2 border-t border-border/50 pt-2.5">
              <div className="flex items-center justify-between text-[11.5px] font-medium text-muted">
                <span>
                  {t("Which animation plays in each situation")} ({activeFaceName})
                </span>
                {onRedrawMoods && !isDragonSelected && (
                  <button
                    type="button"
                    onClick={onRedrawMoods}
                    className="inline-flex items-center gap-1 text-[11.5px] font-medium text-accent hover:underline"
                  >
                    <RefreshCw size={11} /> {t("Redraw poses")}
                  </button>
                )}
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {SITUATION_ROWS.map((sit) => {
                  const defaultFile = isDragonSelected
                    ? sit.id === "error"
                      ? "dragon-error.webp"
                      : sit.id === "call"
                        ? "dragon-idle.mp4"
                        : `dragon-${sit.id}.mp4`
                    : activeStudioFace?.situations?.[sit.id] || `${sit.id}.mp4`;
                  const currentFile = value.avatar_situation_clips?.[sit.id] || defaultFile;
                  return (
                    <label key={sit.id} className="flex min-w-0 flex-col gap-1 rounded-xl border border-border/60 bg-surface px-2.5 py-2">
                      <span className="text-[11.5px] font-medium text-fg">{t(sit.label)}</span>
                      <select
                        value={currentFile}
                        onChange={(e) => {
                          const chosen = e.target.value;
                          void handleSituationChange(sit.id, chosen);
                          const hit = activeFaceAssets.find((a) => a.file === chosen);
                          if (hit) onPreviewClip?.({ url: hit.url, label: hit.label });
                        }}
                        className="w-full min-w-0 truncate rounded-lg border border-border bg-surface-2 px-2 py-1 text-[12px] text-fg outline-none"
                      >
                        {!activeFaceAssets.some((a) => a.file === currentFile) && (
                          <option value={currentFile}>{currentFile}</option>
                        )}
                        {activeFaceAssets.map((asset) => (
                          <option key={asset.file} value={asset.file}>
                            {asset.label} ({asset.kind === "video" ? "3D" : t("still")})
                          </option>
                        ))}
                      </select>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
