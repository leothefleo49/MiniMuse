import { Check, Film, ImagePlus, Sparkles, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fileUrl } from "../api";
import { DRAGON, dragonUrl, isDragon, studioUrl } from "../avatars";
import { useT } from "../i18n";
import type { AvatarLibraryView } from "../types";
import { cx } from "../util";

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
  onStudio,
}: {
  value: AvatarChoice;
  onChange: (v: AvatarChoice) => void;
  /** the "draw a new one" tile: opens the avatar studio screen */
  onStudio?: () => void;
}) {
  const t = useT();
  const [uploadingBg, setUploadingBg] = useState(false);
  const [uploadingAsset, setUploadingAsset] = useState(false);
  const [savingSit, setSavingSit] = useState(false);
  const [library, setLibrary] = useState<AvatarLibraryView | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const assetFileRef = useRef<HTMLInputElement>(null);

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
  const borderGlow = typeof value.avatar_border_glow === "number" ? value.avatar_border_glow : 55;
  const ring = "ring-[2.5px] ring-accent ring-offset-2 ring-offset-bg";
  const tile = "relative h-[72px] w-[72px] shrink-0 overflow-hidden rounded-full transition cursor-pointer";

  const libraryFaces = library?.faces ?? [];
  const knownFaceIds = new Set(libraryFaces.map((f) => f.id));
  const activeStudioFace = libraryFaces.find((f) => f.id === otherStudio) || (libraryFaces.length > 0 ? libraryFaces[0] : null);
  // Defensively handle both new `assets` array and legacy `clips`/`stills` shapes so `.length` never crashes
  const activeFaceAssets = (() => {
    if (!activeStudioFace) return [];
    if (Array.isArray(activeStudioFace.assets) && activeStudioFace.assets.length > 0) {
      return activeStudioFace.assets;
    }
    const fallback: Array<{ file: string; path: string; kind: "video" | "image"; label: string; size: number; canonical: boolean }> = [];
    for (const c of activeStudioFace.clips ?? []) {
      if (c?.file) {
        fallback.push({
          file: c.file,
          path: `avatar/${activeStudioFace.id}/${c.file}`,
          kind: "video",
          label: c.label || c.file,
          size: 0,
          canonical: false,
        });
      }
    }
    for (const s of activeStudioFace.stills ?? []) {
      if (s) {
        fallback.push({
          file: s,
          path: `avatar/${activeStudioFace.id}/${s}`,
          kind: "image",
          label: s,
          size: 0,
          canonical: false,
        });
      }
    }
    return fallback;
  })();

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
    try {
      const targetFace = otherStudio || activeStudioFace?.id || "custom";
      const res = await api.avatarUploadAsset(targetFace, file);
      await loadLibrary();
      if (res.face_id) {
        onChange({
          ...value,
          avatar: res.face_id,
          avatar_situation_clips: res.profile?.avatar_situation_clips ?? value.avatar_situation_clips,
        });
      }
    } catch {
      /* ignore upload error */
    } finally {
      setUploadingAsset(false);
    }
  };

  const handleSituationChange = async (sitId: string, chosenFile: string) => {
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

      {/* Avatar Background Mode: Match theme, Classic disc, Custom color, or Custom image */}
      <div className="space-y-2 pt-1">
        <div className="text-[12px] text-muted">{t("Avatar background")}</div>
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              { id: "theme", label: t("Match theme") },
              { id: "default", label: t("Classic disc") },
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
          <div className="flex items-center gap-3 pt-0.5">
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={borderGlow}
              onChange={(e) =>
                onChange({
                  ...value,
                  avatar_border_glow: Number(e.target.value),
                })
              }
              aria-label={t("Border color & glow")}
              className="h-1.5 flex-1 cursor-pointer accent-accent"
            />
          </div>
        )}
      </div>

      {/* Animations by Situation & Custom 3D Models / Clips */}
      <details className="rounded-2xl border border-border/70 bg-surface-2/40 px-3.5 py-2.5 text-[12.5px]">
        <summary className="cursor-pointer font-medium text-fg flex items-center gap-2">
          <Film size={14} className="text-accent shrink-0" />
          <span>{t("Customize animations by situation & import 3D models")}</span>
        </summary>
        <div className="mt-3 space-y-3 border-t border-border/50 pt-3">
          <p className="text-[11.5px] leading-snug text-muted">
            {t("Choose which animation or image plays in each situation across your PC, phone, and watch, upload custom .mp4/.webp animations or Muse 3D avatar .zip bundles, or have AI create new ones.")}
          </p>

          <div className="flex flex-wrap gap-2">
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
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-surface px-3 py-1.5 text-[12px] font-medium hover:bg-surface-2 disabled:opacity-50"
            >
              <Upload size={13} />
              {uploadingAsset ? t("Uploading…") : t("Upload animation or 3D model (.mp4 / .webp / .zip)")}
            </button>
            {onStudio && (
              <button
                type="button"
                onClick={onStudio}
                className="inline-flex items-center gap-1.5 rounded-full bg-accent/12 px-3 py-1.5 text-[12px] font-medium text-accent hover:bg-accent/20"
              >
                <Sparkles size={13} />
                {t("Generate / adjust with AI")}
              </button>
            )}
          </div>

          {activeStudioFace && activeFaceAssets.length > 0 && (
            <div className="space-y-2 pt-1">
              <div className="flex items-center justify-between text-[11.5px] font-medium text-muted">
                <span>
                  {t("Situation animations for")} {activeStudioFace.name || activeStudioFace.id}
                </span>
                {savingSit && <span>{t("Saving…")}</span>}
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {SITUATION_ROWS.map((sit) => {
                  const currentFile =
                    value.avatar_situation_clips?.[sit.id] ||
                    activeStudioFace.situations?.[sit.id] ||
                    `${sit.id}.mp4`;
                  return (
                    <label key={sit.id} className="flex flex-col gap-1 rounded-xl border border-border/60 bg-surface px-2.5 py-2">
                      <span className="text-[11.5px] font-medium text-fg">{t(sit.label)}</span>
                      <select
                        value={currentFile}
                        onChange={(e) => void handleSituationChange(sit.id, e.target.value)}
                        className="rounded-lg border border-border bg-surface-2 px-2 py-1 text-[12px] text-fg outline-none"
                      >
                        {activeFaceAssets.map((asset) => (
                          <option key={asset.file} value={asset.file}>
                            {asset.label} ({asset.kind === "video" ? "3D video" : "still"} — {asset.file.slice(0, 34)})
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
      </details>
    </div>
  );
}
