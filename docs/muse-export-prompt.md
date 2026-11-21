# The Muse export prompt

Settings → Import & export shows one prompt to send Muse in a fresh chat. Muse builds (or
updates) a `minimuse-archive/` folder inside its own exportable workspace; the person then
downloads Muse's export zip and uploads it to MiniMuse, where the importer reads that
folder.

This file is the prompt itself, kept here so it can be reviewed and improved without
opening the app. The copy shown in the app lives in
`web/src/components/ImportExport.tsx` (`MUSE_PROMPT`); the two must stay identical.

## Why the wording is this exact

The importer reads the archive mechanically, so every instruction below exists because a
real export got it wrong once:

| Instruction | What went wrong without it |
| --- | --- |
| `counts` block with fixed key names | Imports could not be verified against the archive's own totals, so gaps went unnoticed |
| One header shape, `## YYYY-MM-DD h:mm AM ZZZ - Speaker` | Exports arrived in five shapes; four of them were silently unreadable |
| No other header shape, no merged turns | A "Messages:" count in the file disagreed with the number of readable turns |
| Move data into the archive instead of copying | The same 637 uploads appeared under `workspace/user/files` and `notes/uploads` |
| No `node_modules`, `.venv`, caches | One export carried 40,911 dependency files against 72 real ones |
| One folder per app, data beside the code | Build output next to source registered junk apps such as "index" |
| Remove files that no longer exist | Stale source accumulated across runs until the app no longer matched |
| `null` instead of a plausible value | Profile fields were invented when nothing was stored |
| Append to the MANIFEST changelog | Later runs could not tell what had changed since the last transfer |

## Transfer codes (updates only)

A first transfer is always a full archive. Later ones can be much smaller: after each
import MiniMuse knows the moment the archive was built, and Muse can send only what changed
since then.

Paste the transfer code on its own line above the prompt:

```
SINCE: 2026-11-18T17:42:03Z
<prompt>
```

Muse then sets `"scope": "delta"`, records `since_code`, and makes the counts block describe
only the delta. The importer treats a delta archive as an update: unchanged files are
skipped by content hash, changed transcripts merge into their existing chat by message
time, and the verification report compares against the delta counts rather than the whole
archive.

Deletions are not part of delta transfers yet: a file removed in Muse stays in MiniMuse
until it is deleted here.

## The prompt

The full text is `MUSE_PROMPT` in `web/src/components/ImportExport.tsx`. It is deliberately
in English and written as instructions to another assistant, not as documentation:

1. **Step 0, inventory** - list every kind of stored data before writing anything, so the
   archive follows what exists rather than a fixed list.
2. **Catch-all rule** - anything about the person or the work goes in, even if unnamed here.
3. **Never invent, never copy secrets** - `null` for unknown, `[REDACTED]` for credentials.
4. **One archive folder**, seven sections: `profile/`, `avatar/<face_id>/`,
   `chat-transcripts/`, `apps/`, `notes/`, `system/`, `MANIFEST.md`.
5. **Verification before reporting** - count what was written, compare with `counts`, check
   turn headings against reported message counts, validate every JSON file.
6. **Every run** - build or update in place, no duplicate copies, append to the changelog.
7. **Updates only** - the delta rules above.
8. **Nothing silent** - the archive's profile carries an `excluded` list naming anything the
   export could not include (media that only lives in the chat store, files too large to
   bundle), and the report repeats it, so nothing is claimed transferred when it is not.
9. **Report back** - the profile JSON block, verified totals, gaps, and how to export.

## Changing it

Change the prompt in `ImportExport.tsx` first, then mirror the wording here. Keep the
header example, the `counts` key names, and the folder names byte-identical to what
`nanomuse/server/data_import.py` parses: `_VERIFY_BUCKETS` for the counts, `HEADER_PATTERNS`
for the transcript headers, and `_classify` for the section layout.
