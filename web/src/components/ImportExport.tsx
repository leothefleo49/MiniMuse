import { Check, Copy, Download, FileUp, Loader2, Sparkles, TextCursorInput } from "lucide-react";
import { useRef, useState } from "react";
import { api, type ImportOptions, type ImportResult } from "../api";
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
export const MUSE_PROMPT = `Please prepare a complete, up-to-date export archive of everything we have worked on together inside your persistent workspace/deep-memory storage (the files that get included when I export my Muse data), so I can transfer or sync everything cleanly into MiniMuse.

First, check what is already saved in your workspace/deep-memory export files:
- If most of our chats, transcripts, memories, or apps are NOT saved there in the format below yet, create all of them now.
- If some transcripts or files are missing, outdated, incomplete, or saved in an inconsistent format while others already exist, go ahead and update/rebuild everything cleanly across the whole archive so nothing is stale, partial, or left behind. Replace any outdated or partial copies rather than leaving duplicates.

Work through each section systematically:

1. Save or update my Profile, User Details & AI Identity:
   - Write "profile/muse-profile.json" into your workspace containing:
     {"name": "<your AI name>", "user_name": "<what you call me>", "tagline": "<your one-line tagline>", "color": "<your hex accent color>", "emoji": "<your emoji>", "avatar": "<your avatar face id>", "tone": "<formal|casual|playful|concise>", "communication": "<short|detailed|bullets>", "style": "<detailed description of your personality, voice, habits, and how you talk to me>"}
   - Write "profile/USER.md" with everything you know about me (name, background, preferences, contacts, recurring routines/tasks, and life context).
   - Write "profile/IDENTITY.md" with your complete persona, instructions, and style rules.
   - Copy any custom AI avatar icons, still poses (idle, working, waiting, happy, error), or animations (.webp, .png, .mp4, .webm) into "avatar/<face_id>/" in your workspace.

2. Save or update ALL Chat Transcripts (oldest to newest):
   - Go through EVERY past chat and side conversation we have had. Make sure each chat is saved or updated under "chat-transcripts/YYYY-MM-DD-<title>-chat-transcript.md" inside the workspace/deep-memory export directory.
   - Format every message in order with a markdown heading above each turn using exact date, time, and speaker name, e.g.:
     ## User — 2026-10-17 1:18:45 PM MDT
     (or 24h timestamp + timezone) and ## Muse — ...
   - Include my messages and your replies word for word, noting any images, files, links, or code inline where they appeared. If an existing transcript file is missing recent turns or used an incomplete summary, rewrite/update the whole file cleanly.

3. Save or update the Main Chat transcript:
   - Write or update the full main-chat transcript using the exact same turn-by-turn format with speaker and timestamp above every message, ensuring all recent messages are included.

4. Save or update every App, Tool, Webpage, Game & Code Project:
   - Under "apps/" in your workspace, save the COMPLETE current source code for every app/tool/script you built for me (every HTML, CSS, JS, TS, Python, or config file with its proper folder structure, not summaries). Update outdated versions in place.

5. Save or update Memories, Notes, Plans, Goals & Documents:
   - Ensure all long-term memories, daily notes, people/group notes, goals, and key documents are stored as Markdown/JSON files in your exported workspace/deep-memory storage.

6. Confirm & Give Export Steps:
   - Paste a \`\`\`muse-profile ... \`\`\` JSON block in your reply with my profile & AI identity fields.
   - List what you created or updated and confirm everything is saved inside your workspace/deep-memory export storage.
   - Tell me clearly how to export and send it to MiniMuse: open Muse Settings -> Export data (or Data Controls -> Export), download the exported .zip file, and upload that .zip inside MiniMuse under Settings -> Import & export.`;

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

function summaryOf(result: ImportResult, t: (key: string, vars?: Record<string, string | number>) => string) {
  const bits: string[] = [];
  if (result.profile_updated) bits.push(t("AI profile & user details linked"));
  if (result.avatar_files) bits.push(t("{n} avatar icons & animations", { n: String(result.avatar_files) }));
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
  const [busy, setBusy] = useState<"file" | "text" | "export" | "bundle" | "">("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [copiedBundle, setCopiedBundle] = useState(false);
  const prompt = MUSE_PROMPT;

  const options = (): ImportOptions => ({
    restore_chats: restoreChats,
    background_memory: backgroundMemory,
    apps,
    main_chat: mainChat,
  });

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
              void run(() => api.importData(file, file.name, options()), "file");
            }}
          />
          <p className="pt-2 text-center text-[12px] text-muted">{t("A Muse export zip, a MiniMuse export zip, or any transcript or text file, up to 4 GB.")}</p>
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
