import { Check, ImagePlus, Sparkles } from "lucide-react";
import { useRef, useState } from "react";
import { api } from "../api";
import { DRAGON, dragonUrl, isDragon, studioUrl } from "../avatars";
import { useT } from "../i18n";
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

/**
 * The dragon, a face drawn for you in the avatar studio (when the profile wears one), a tile
 * that opens the studio (when `onStudio` is given — Settings; setup has no model yet), plus
 * an emoji on a colour for anyone who would rather, and avatar background controls (Match theme,
 * Classic disc, Custom color, or Custom image).
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
  const fileRef = useRef<HTMLInputElement>(null);
  const emojiMode = value.avatar === "";
  const isDragonSelected = value.avatar === DRAGON;
  // a face from the studio: anything that is neither the dragon nor the emoji
  const otherStudio = !emojiMode && !isDragon(value.avatar) ? value.avatar : null;
  const bgMode = value.avatar_bg_mode || "theme";
  const bgColor = value.avatar_bg_color || value.color || "#0064d4";
  const ring = "ring-[2.5px] ring-accent ring-offset-2 ring-offset-bg";
  const tile = "relative h-[72px] w-[72px] shrink-0 overflow-hidden rounded-full transition cursor-pointer";

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

  return (
    <div className="space-y-3">
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

        {/* Other custom studio face */}
        {otherStudio && (
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
            title="Create Custom Avatar"
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
                      color: c,
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
                    color: e.target.value,
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
    </div>
  );
}
