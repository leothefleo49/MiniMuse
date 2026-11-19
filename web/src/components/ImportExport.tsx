import { Check, Copy, Download, FileUp, Loader2, Sparkles, TextCursorInput, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type ImportOptions, type ImportResult, type KeptCopy } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import { MuseCaption, MuseCard, MuseDivider, MuseRow, MuseSectionLabel, MuseSwitchRow } from "./MuseList";

/**
 * Settings → Import & export: the quick way to bring another assistant's data in and to
 * take this runtime's data out.
 *
 * The Muse half is the big one. It shows the prompt a person sends Muse in a fresh chat:
 * Muse then writes a full transcript of every past chat (every message with its date and
 * time) and the complete source of every app it ever built into its workspace, where
 * Muse's own data export picks it up. That zip is imported right here, with switches for
 * what should happen to the chats, the background memory and the apps.
 */

/** The prompt to send Muse, unchanged, in a chat. It stays in English: Muse reads it. */
export const MUSE_PROMPT = `Please prepare a complete, up-to-date export archive of EVERYTHING in your persistent workspace / deep-memory storage (the files that get included when I export my Muse data), so I can transfer or sync it cleanly into MiniMuse.

STEP 0 - Inventory first, then build
Before writing anything, scan your ENTIRE persistent storage and list every kind of data that exists about me or our work together: chats, transcripts, memories, notes, goals, documents, files I uploaded, media you generated, apps and their data, avatar assets, settings, schedules, connectors, and anything else. Build the archive from what you actually find, not only from the list below. If a kind of data exists that this prompt does not name, give it its own clearly named folder and describe it in the MANIFEST.

CATCH-ALL RULE, the most important rule here: plus any files or data not listed here that you can find anywhere in your storage. If it exists and it is about me, my life, or our work together, include it.

NEVER INVENT. Every value comes from a real file or record. If something cannot be sourced, write null (or "unknown") and note it in the MANIFEST. Do not fill gaps with plausible-sounding details.

NEVER COPY SECRETS. No passwords, API keys, tokens or one-time codes. Replace each one in place with [REDACTED] and note it in the MANIFEST.

ONE ARCHIVE FOLDER. Build everything inside a single folder named minimuse-archive/ in your exportable workspace, with exactly the sections below. Do not scatter the same data across other folders: if a source lives elsewhere, move the content into the archive instead of keeping a second copy. If you cannot move it, copy it once and say so in the MANIFEST.

1. profile/
- profile/muse-profile.json, every field you can actually source:
  {"assistant": {"name": "<your AI name>", "tagline": "<one line>", "color": "<hex>", "emoji": "<emoji>", "tone": "<formal|casual|playful|concise>", "communication": "<short|detailed|bullets>", "style": "<your personality and voice>"},
   "user": {"call_name": "<what you call me>", "full_name": "<my name>", "timezone": "<IANA zone>"},
   "avatar": {"face_id": "<your avatar folder>"},
   "archive_root": "workspace/your_files/minimuse-archive/",
   "built_at": "YYYY-MM-DD",
   "scope": "full",
   "counts": {"chat_transcripts_in_archive": 0, "total_transcript_messages": 0, "chats_total": 0, "app_folders_in_archive": 0, "avatar_files": 0, "memory_files": 0, "goal_files": 0, "document_files": 0}}
  The counts block is required and must equal what you actually wrote (see VERIFICATION).
- profile/USER.md: everything you know about me, my background, people, preferences, routines and goals.
- profile/IDENTITY.md: your complete persona, instructions and style rules. If the stored file is blank or a template, say so in the MANIFEST and write the fullest honest version from your real configuration and behaviour.

2. avatar/<face_id>/
- Every file of your avatar folder: frames, stills, candidates, animations. Formats .webp .png .jpg .mp4 .webm. Keep the folder structure and the original file names.
- avatar/<face_id>/avatar-model.json: {"face_id": "...", "active_image": "<file>", "animated_variants_mp4": {"idle": "<file>", "working": "<file>", "waiting": "<file>"}, "moods": {"idle": "<file>", "working": "<file>", "waiting": "<file>", "happy": "<file>", "error": "<file>"}}
- If no 3D model files (.glb .gltf .fbx .vrm .obj) exist, say so in the MANIFEST instead of implying they were included.

3. chat-transcripts/
- One file per conversation: the main chat, every side chat, every voice call and every archived thread, at chat-transcripts/YYYY-MM-DD-<title>-chat-transcript.md (the date is the chat's first message).
- Write every message word for word, oldest first, with one heading above each turn in exactly this shape:
  ## 2026-11-05 5:49 PM MST - Tyler
  ## 2026-11-05 5:50 PM MST - Muse
  The time is the message's own local time, with AM/PM and the zone abbreviation. Use the speaker names we actually use. No other header shape, no merged turns, no summaries.
- Note images, files, links, voice notes and code inline, where they appeared.
- Put a short metadata block at the top of each file (title, chat id, date range, message count), then the turns.
- Never write a summary transcript. If an existing file is partial or a summary, replace it with the full verbatim version.

4. apps/
- One folder per app, tool, game or code project, with its complete current source: every HTML, CSS, JS, TS, Python and config file, in the real folder structure. Never summaries or excerpts.
- Include each app's own data (its database file or a data export) inside that app's folder, so the app can actually run again.
- Leave out installed dependencies and build caches: node_modules, .venv, venv, __pycache__, .cache, .bun-cache, dist, .next. They are not your work. List them as excluded in the MANIFEST with a count, so the numbers add up.
- Update files in place on later runs, and delete files that no longer exist in the source, so no stale code is left behind.

5. notes/
- notes/memory/ long-term memory, daily logs, people and group notes, standing rules
- notes/goals/ every goal with its own notes and workspace files
- notes/documents/ plans, reports, spreadsheets, decks, schedules, reminders
- notes/uploads/ the files I uploaded to you and the media you generated for me, as the real files
- Keep the folder structure you actually use inside each of these.

6. system/
- Preferences and standing instructions, including proactive and notification settings
- Custom skills and connector docs, with credentials removed
- Scheduled and recurring jobs: name, schedule, what each does, where it delivers
- Anything else that shapes how you work for me

7. MANIFEST.md at the archive root
- Every section, with the number of files in it
- For transcripts: the file count and the total message count
- The date of this build, the method used, and one dated changelog entry per run (append, never rewrite)
- Every gap, exclusion, redaction and limitation, named honestly
- The same counts block as profile/muse-profile.json, so the two can be compared

VERIFICATION, before you report back
- Count what you actually wrote and compare it with the counts block. The two must match.
- Check every transcript file: the number of turn headings must equal the message count you report for that chat.
- Validate every JSON file you wrote.
- If a check fails, or something could not be retrieved, say exactly what and why. Never call the archive complete when it is not.

ON EVERY RUN, first or repeat
- If no archive exists, build it complete, including any kind of data this prompt does not name.
- If it exists, update it in place: append new messages, refresh changed files, add new chats, apps and data, remove files that no longer exist. Keep the same paths and folder names.
- Never write a second copy of anything (no -copy, -v2, -final files, no duplicates under another folder).
- Append one dated entry to the MANIFEST changelog.

UPDATES ONLY, when I paste a transfer code
- If my message contains a line like SINCE: 2026-11-18T17:42:03Z, this is an update, not a rebuild.
- Include only what is new or changed since that time: chats with newer messages, and notes, goals, documents, files and app folders whose files changed after it. Use message timestamps and file modification times, and add a 10 minute safety margin.
- Set "scope": "delta" and "since_code": "<the code>" in profile/muse-profile.json, and make the counts block describe only this delta.
- Paths, header formats, the MANIFEST fields and everything else stay exactly the same. Files that did not change are not re-sent.

REPORT BACK
- Paste a muse-profile JSON block with the profile fields and the counts.
- List what you created or updated, with the verified totals per section, plus every gap and redaction.
- Confirm everything is saved inside your exportable workspace.
- Tell me how to send it: Muse Settings -> Data controls -> Export data, download the .zip file, then upload it in MiniMuse under Settings -> Import & export.`;


type AppsChoice = ImportOptions["apps"];
type MainChoice = ImportOptions["main_chat"];

/** A small segmented row of radio choices, drawn like the settings' other pickers. */
function ChoiceRow<T extends string>({
  label,
  choices,
  value,
  onChange,
}: {
  label: string;
  choices: Array<{ id: T; label: string }>;
  value: T;
  onChange: (v: T) => void;
}) {
  const t = useT();
  return (
    <div className="mx-4 mt-4">
      <p className="px-4 pb-2 text-[13px] leading-[18px] text-muted">{label}</p>
      <div className="flex flex-wrap gap-1.5 px-4" role="radiogroup" aria-label={label}>
        {choices.map((c) => (
          <button
            key={c.id}
            type="button"
            role="radio"
            aria-checked={value === c.id}
            onClick={() => onChange(c.id)}
            className={
              "rounded-full border px-3 py-1.5 text-[13px] transition " +
              (value === c.id
                ? "border-accent bg-accent/10 text-fg"
                : "border-border bg-surface-2 text-muted hover:text-fg")
            }
          >
            {t(c.label)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function summaryOf(result: ImportResult, t: (key: string, vars?: Record<string, string | number>) => string) {
  const bits: string[] = [];
  if (result.profile_updated) bits.push(t("AI profile & user details linked"));
  if (result.avatar_files) bits.push(t("{n} avatar icons & animations", { n: String(result.avatar_files) }));
  if (result.avatar_moods) bits.push(t("{n} avatar moods ready", { n: String(result.avatar_moods) }));
  if (result.threads_created) bits.push(t("{n} new chats", { n: String(result.threads_created) }));
  if (result.threads_merged) bits.push(t("{n} chats merged", { n: String(result.threads_merged) }));
  if (result.events_added) bits.push(t("{n} messages added", { n: String(result.events_added) }));
  if (result.messages_indexed) bits.push(t("{n} messages in background memory", { n: String(result.messages_indexed) }));
  if (result.memory_files) bits.push(t("{n} memory files", { n: String(result.memory_files) }));
  if (result.apps_saved) bits.push(t("{n} apps saved", { n: String(result.apps_saved) }));
  if (result.apps_registered) bits.push(t("{n} apps restored", { n: String(result.apps_registered) }));
  if (result.duplicates) bits.push(t("{n} duplicates skipped", { n: String(result.duplicates) }));
  if (result.skipped_files) bits.push(t("{n} files skipped", { n: String(result.skipped_files) }));
  if (!bits.length) bits.push(t("Nothing new in there: it may all be imported already."));
  return bits.join(", ") + ".";
}

/**
 * `full` is the Settings page: the Muse prompt, the import, and the export. `import` is what the
 * setup screen embeds: the prompt and the import only, with no export block and no page margin.
 */
export function ImportExport({ mode = "full" }: { mode?: "full" | "import" }) {
  const t = useT();
  const { toast, refreshSettings } = useStore();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [fileName, setFileName] = useState("");
  const [pasted, setPasted] = useState("");
  const [restoreChats, setRestoreChats] = useState(true);
  const [backgroundMemory, setBackgroundMemory] = useState(true);
  const [apps, setApps] = useState<AppsChoice>("both");
  const [mainChat, setMainChat] = useState<MainChoice>("side");
  const [keepCopy, setKeepCopy] = useState(true);
  const [busy, setBusy] = useState<"file" | "text" | "export" | "bundle" | "">("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [copies, setCopies] = useState<KeptCopy[]>([]);
  const [removing, setRemoving] = useState("");
  const [uploadPct, setUploadPct] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copiedBundle, setCopiedBundle] = useState(false);
  const prompt = MUSE_PROMPT;

  const options = (): ImportOptions => ({
    restore_chats: restoreChats,
    background_memory: backgroundMemory,
    apps,
    main_chat: mainChat,
    keep_copy: keepCopy,
  });

  // the originals kept from earlier transfers: listed so they can be read again or deleted
  const reloadCopies = () => {
    api
      .importCopies()
      .then(setCopies)
      .catch(() => setCopies([]));
  };

  useEffect(() => {
    reloadCopies();
    const kick = () => reloadCopies();
    window.addEventListener("nanomuse:import-finished", kick);
    return () => window.removeEventListener("nanomuse:import-finished", kick);
  }, []);

  const removeCopy = async (name: string) => {
    setRemoving(name);
    try {
      await api.deleteImportCopy(name);
      setCopies((list) => list.filter((c) => c.name !== name));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setRemoving("");
    }
  };

  const run = async (what: () => Promise<ImportResult>, kind: "file" | "text") => {
    setBusy(kind);
    setResult(null);
    try {
      const done = await what();
      setResult(done);
      if (done.profile_updated || done.avatar_files) {
        await refreshSettings().catch(() => undefined);
      }
      toast(t("Imported."));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  // A file is uploaded here, then read and applied by the runtime in the background: the
  // progress pill (ImportProgress, mounted app-wide) follows it, and the screen stays free.
  const startBackground = async (file: File) => {
    setBusy("file");
    setResult(null);
    setUploadPct(0);
    try {
      await api.importDataBackground(file, file.name, options(), setUploadPct);
      window.dispatchEvent(new Event("nanomuse:import-started"));
      toast(t("Import started. It runs in the background: keep using the app."));
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy("");
      setUploadPct(0);
    }
  };

  const copySetupBundle = async () => {
    setBusy("bundle");
    try {
      const bundle = await api.exportSetupBundle();
      await navigator.clipboard.writeText(JSON.stringify(bundle, null, 2));
      setCopiedBundle(true);
      window.setTimeout(() => setCopiedBundle(false), 2500);
      toast(t("Setup & Profile Transfer Bundle copied to clipboard!"));
    } catch (e) {
      toast((e as Error).message || t("Could not copy setup bundle."));
    } finally {
      setBusy("");
    }
  };

  const exportData = async () => {
    setBusy("export");
    try {
      const { blob, name } = await api.exportData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setBusy("");
    }
  };

  const copyPrompt = async () => {
    try {
      await navigator.clipboard.writeText(prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast(t("Could not copy; select the text and copy it by hand."));
    }
  };

  return (
    <div className={mode === "full" ? "-mx-4 pb-2" : "pb-1"} data-testid={`import-export-${mode}`}>
      {/* ---- From Muse: the prompt, the warning, what to do after ---- */}
      <MuseSectionLabel>{t("From Muse")}</MuseSectionLabel>
      <MuseCard>
        <div className="px-4 pb-3 pt-4">
          <div className="flex items-center gap-2 text-[16px] leading-[21px]">
            <Sparkles size={18} className="shrink-0 text-fg" />
            {t("Bring over everything Muse has")}
          </div>
        </div>
        <MuseDivider inset={16} />
        <MuseCaption>
          {t("Open a chat in Muse and send the single prompt below. It tells Muse to save or update all transcripts, memories, profile details, and apps in the format this importer reads.")}
        </MuseCaption>
        <div className="px-4 pb-3">
          <div className="max-h-64 overflow-y-auto whitespace-pre-wrap rounded-xl border border-border bg-surface-2 px-3.5 py-3 font-mono text-[12px] leading-[17px] text-fg/90">
            {prompt}
          </div>
          <button
            type="button"
            onClick={() => void copyPrompt()}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-surface px-3 py-2 text-[13.5px] transition active:bg-surface-2"
          >
            {copied ? <Check size={15} className="text-accent" /> : <Copy size={15} />}
            {copied ? t("Copied") : t("Copy the prompt")}
          </button>
        </div>
        <MuseDivider inset={16} />
        <MuseCaption className="pb-4">
          {t("How to export from Muse: open Muse Settings -> Data controls -> Export data, download the .zip archive when ready, then upload that .zip file under Import below.")}
        </MuseCaption>
      </MuseCard>

      {/* ---- Import ---- */}
      <MuseSectionLabel>{t("Import")}</MuseSectionLabel>
      <MuseCard>
        <MuseSwitchRow label={t("Restore chats as conversations")} checked={restoreChats} onChange={setRestoreChats} />
        <MuseDivider inset={16} />
        <MuseCaption>
          {t("Each imported chat appears in the chat list. A chat with the same name merges, message by message, in time order. Nothing already here is deleted.")}
        </MuseCaption>
        <MuseDivider inset={52} />
        <MuseSwitchRow label={t("Keep chat history in background memory")} checked={backgroundMemory} onChange={setBackgroundMemory} />
        <MuseDivider inset={16} />
        <MuseCaption>
          {t("Every imported message also goes into the archive the agent searches, so later chats can look it up even when the conversation is not restored.")}
        </MuseCaption>
        <MuseDivider inset={52} />
        <ChoiceRow<AppsChoice>
          label={t("Muse's apps and code")}
          value={apps}
          onChange={setApps}
          choices={[
            { id: "save", label: "Save files" },
            { id: "restore", label: "Restore apps" },
            { id: "both", label: "Both" },
            { id: "neither", label: "Neither" },
          ]}
        />
        <MuseCaption>
          {t("Save files keeps the complete source in the workspace. Restore apps also makes them interactive at /apps/name.")}
        </MuseCaption>
        <ChoiceRow<MainChoice>
          label={t("The Muse main chat")}
          value={mainChat}
          onChange={setMainChat}
          choices={[
            { id: "side", label: "A side chat" },
            { id: "replace", label: "Becomes the main chat" },
            { id: "merge", label: "Merges into the main chat" },
          ]}
        />
        <MuseCaption className="pb-1">
          {t("A side chat is the safe pick. Becomes the main chat sets the current main chat aside as a side chat, untouched. Merges into the main chat interleaves both histories by message time.")}
        </MuseCaption>
        <MuseDivider inset={52} />
        <MuseSwitchRow label={t("Keep the original archive")} checked={keepCopy} onChange={setKeepCopy} />
        <MuseDivider inset={16} />
        <MuseCaption>
          {t("The .zip you send stays in the workspace, exactly as it arrived, next to a report of what the import found. That lets the agent re-check the original later. Delete it whenever you like; the imported chats do not depend on it.")}
        </MuseCaption>
        {copies.length > 0 && (
          <div className="px-4 pb-2 pt-1">
            {copies.map((copy) => (
              <div key={copy.name} className="flex items-center gap-2 border-t border-border/60 py-2 first:border-t-0">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] text-fg/90">{copy.name}</p>
                  <p className="text-[11.5px] text-muted">
                    {(copy.size / (1024 * 1024)).toFixed(1)} MB
                    {copy.report ? ` · ${t("verification report")}` : ""}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label={t("Delete {name}", { name: copy.name })}
                  disabled={removing === copy.name}
                  onClick={() => void removeCopy(copy.name)}
                  className="shrink-0 rounded-full p-1.5 text-muted transition active:bg-surface-2 disabled:opacity-50"
                >
                  {removing === copy.name ? (
                    <Loader2 size={15} className="animate-spin" />
                  ) : (
                    <Trash2 size={15} />
                  )}
                </button>
              </div>
            ))}
          </div>
        )}
        <div className="px-4 pb-4 pt-1">
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            disabled={busy !== ""}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-surface px-3 py-2.5 text-[13.5px] transition active:bg-surface-2 disabled:opacity-60"
          >
            {busy === "file" ? <Loader2 size={16} className="animate-spin" /> : <FileUp size={16} />}
            {busy === "file" ? t("Importing...") : fileName ? t("Import {name}", { name: fileName }) : t("Import a file")}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept=".zip,.md,.txt,.json,.csv,.log,.html,.htm,.markdown,application/zip,text/plain,application/json"
            className="hidden"
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = "";
              if (!file) return;
              setFileName(file.name);
              void startBackground(file);
            }}
          />
          <p className="pt-2 text-center text-[12px] text-muted">{t("A Muse export zip, a MiniMuse export zip, or any transcript or text file, of any size.")}</p>
          {busy === "file" && uploadPct > 0 && (
            <div className="pt-2" role="status" aria-live="polite">
              <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div
                  className="h-full rounded-full bg-accent transition-[width] duration-300"
                  style={{ width: `${uploadPct}%` }}
                />
              </div>
              <p className="pt-1 text-center text-[11.5px] text-muted">
                {uploadPct < 100
                  ? t("Uploading {name}: {n}%", { name: fileName, n: String(uploadPct) })
                  : t("Upload done. Reading the archive...")}
              </p>
            </div>
          )}
        </div>
        <MuseDivider inset={16} />
        <div className="px-4 py-4">
          <textarea
            value={pasted}
            onChange={(e) => setPasted(e.currentTarget.value)}
            placeholder={t("Or paste a transcript or any text here")}
            rows={3}
            className="w-full resize-y rounded-xl border border-border bg-surface-2 px-3.5 py-2.5 text-[13.5px] outline-none placeholder:text-muted focus:ring-2 focus:ring-accent/40"
          />
          <button
            type="button"
            disabled={!pasted.trim() || busy !== ""}
            onClick={() => void run(() => api.importText(pasted, "Imported text", options()), "text")}
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-xl border border-border bg-surface px-3 py-2 text-[13.5px] transition active:bg-surface-2 disabled:opacity-60"
          >
            {busy === "text" ? <Loader2 size={15} className="animate-spin" /> : <TextCursorInput size={15} />}
            {busy === "text" ? t("Importing...") : t("Import this text")}
          </button>
        </div>
      </MuseCard>
      {result && (
        <MuseCaption className="pt-1">
          <span className="text-accent">{summaryOf(result, t)}</span>
          {result.note ? ` ${result.note}` : ""}
        </MuseCaption>
      )}
      {result?.verify && (
        <MuseCard className="mt-2">
          <div className="px-4 py-3">
            <p className="text-[13.5px] text-fg/90">
              {result.verify.ok
                ? t("Everything the archive declares is here: every section matched.")
                : t("Some sections did not match what the archive declares. The gaps are named below.")}
            </p>
            <ul className="mt-1.5 space-y-0.5">
              {result.verify.lines.map((line) => (
                <li key={line} className="text-[12px] leading-[17px] text-muted">
                  {line}
                </li>
              ))}
            </ul>
            <p className="pt-2 text-[12px] text-muted">
              {t("The same report is saved in the workspace, next to the kept original.")}
            </p>
          </div>
        </MuseCard>
      )}

      {/* ---- Export (the Settings page only) ---- */}
      {mode === "full" && (
        <>
      <MuseSectionLabel>{t("Export")}</MuseSectionLabel>
      <MuseCard>
        <MuseRow
          icon={busy === "export" ? <Loader2 size={22} className="animate-spin" /> : <Download size={22} />}
          label={t("Download your agent data")}
          onClick={() => void exportData()}
          chevron={false}
        />
        <MuseDivider inset={16} />
        <MuseRow
          icon={busy === "bundle" ? <Loader2 size={22} className="animate-spin" /> : copiedBundle ? <Check size={22} className="text-accent" /> : <Copy size={22} />}
          label={copiedBundle ? t("Copied Setup & Profile Transfer Bundle") : t("Copy Setup & Profile Transfer Bundle")}
          onClick={() => void copySetupBundle()}
          chevron={false}
        />
        <MuseDivider inset={16} />
        <MuseCaption className="pb-2.5 pt-2">
          {t("Download a complete zip of everything this MiniMuse keeps (profile, user details, AI icons & animations, chats, memory, goals, reminders, and apps), or copy a Setup & Profile Transfer Bundle to paste into another device during setup.")}
        </MuseCaption>
      </MuseCard>
        </>
      )}
    </div>
  );
}
