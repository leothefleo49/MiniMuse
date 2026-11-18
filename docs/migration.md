# Migrating from another assistant

You can bring selected memories and text documents into the desktop app, then reconnect
the services you use. The Python runtime's web console goes further: Settings → Import &
export takes a whole Muse export ZIP, restores its conversations and apps, and teaches
the agent the imported history. Keep the original export while checking what has moved.

## What can be moved

| Exported material | Where it goes in nanoMuse | What is preserved |
| --- | --- | --- |
| Short facts and preferences | **Soul & memory → Import memory** | Selected text, one memory per line |
| Notes and conversation transcripts | **Library → Import Markdown files…** | Text documents, not resumable chat sessions |
| Connector definitions and scripts | Configure each service under **Settings → Connectors** | Only the capabilities of the connector or MCP server you configure |
| Tags or categories | Keep them in document headings or text when present in the export | Text only, not imported tag metadata |
| Conversation manifests and resources | Keep them with the original archive | No automatic reconstruction of turns, attachments or session IDs |

An export can only transfer what it contains. If tags or connector files are missing,
nanoMuse cannot recover them from a transcript or a manifest. Check your own export;
different export paths may include different material.

## Import selected memories

1. Open your agent's profile, choose **Soul & memory**, then **Import memory**.
   The same action is available under **Settings → Data controls**.
2. Paste facts you want the agent to remember, one per line. Review them first;
   keep long notes and transcripts in the Library.
3. Import a small batch, check the saved lines, then continue with the rest.

An import accepts at most **200 nonempty lines**, each at most **400 characters** after
whitespace and leading list markers are normalized. Desktop memory holds **400 entries
in total**, including existing memories. Lines differing only in letter case or extra
whitespace count as duplicates and are not added again. Punctuation is preserved when
comparing lines, so negative numbers and decimals remain distinct.

If any limit is exceeded, the current implementation rejects the **whole batch** and
keeps the existing memory unchanged. It does not shorten imported lines or remove old
memories to make room. Split a large batch, shorten individual facts yourself, or review
unneeded memories before trying again. Older builds can truncate lines or discard old
entries, so use a build containing the import-limit fix before moving your full set.

If the app cannot write the imported memories to disk, it shows an error instead of
success. The new lines may still be visible in memory. Check disk space and write
access, then retry the same import before closing the app; a retry saves the lines
even when they are already listed as duplicates.

Desktop memories contain text and a source marker, such as **Imported**. They do not
have the Python runtime's `category` field. The runtime's
[`nanomuse memory add --category …`](cli.md#memory) writes to its own memory store;
it does not add categories to desktop memories or import Muse tags. A prefix such as
`[Project]` in a desktop memory is ordinary text and counts toward the length limit.

## Keep conversations as reference documents

In the **Library**, open the menu beside **Create…** and choose **Import Markdown
files…**. It accepts `.md`, `.markdown` and `.txt` files up to **2 MB each**. Split larger
transcripts into readable files first. Files are copied into the Library; a number is
added to the filename when a name is already taken.

This makes a transcript available as a document. It does not recreate the chat list,
roles, timestamps, tool calls, attachments or a conversation that can be resumed. Keep
images and other resources with the original export and check the references in any
documents you convert. Importing a text file does not import its linked resources.

Do not write foreign transcripts into the harness's session files or manufacture relay
sync records. Those formats belong to nanoMuse's session and sync lifecycle and are not
an external conversation import API.

## Reconfigure connectors

For each service you need, list the actions it provided, its authentication method and
any local files it used. Then check **Settings → Connectors** for a supported connection.
For a custom connection, configure a server that actually implements MCP, using its
documented transport and authentication. See the
[desktop connector settings](desktop-muse.md#settings) and, for the Python runtime,
[MCP server configuration](configuration.md#mcp-servers).

Files named `connector.yaml` or `CONNECTOR.md` can help you understand the old setup,
but nanoMuse has no importer for that connector format. A Python script that calls a
service's API is not automatically an MCP server. It needs a reviewed adapter that
implements MCP before an MCP client can discover and call its tools. Copying the file
or pointing a server command at an ordinary client script does not supply that adapter.

Enter credentials through the supported connector setup, and keep runtime secrets in
the vault. Select the tools and permissions you need, then verify a read-only operation
before using the connection for changes. Bring over your own preferences as selected
facts; do not copy another product's system prompt or assume its instructions configure
nanoMuse's tools and permissions.

## Import a whole Muse export in the runtime

The runtime's web console (the app the server serves) has **Settings → Import & export**.
It moves a Muse export in one pass, including the conversations, which the desktop paths
above do not.

First, get the most out of Muse. Open a new chat in Muse and send it the prompt the page
shows (there is a *Copy the prompt* button): it asks Muse to write a complete transcript
of every past chat, every message with its date and time, and the complete source of
every app it ever built, into Muse's workspace. This can use a lot of your Muse usage,
so run it once. When Muse is done, export your data in Muse's settings, and import that
ZIP on the page.

The import accepts a Muse export ZIP, a nanoMuse export ZIP, a transcript file, or text
pasted straight in, up to 4 GB. Five switches decide what happens:

- **Restore chats as conversations** makes each imported chat a chat here. A chat whose
  title matches an existing one merges into it, message by message, in time order, and a
  conversation carried twice in the same archive (the archive's own copy and Muse's
  working copy) is recognized by its chat id and kept as one chat. Nothing already in
  nanoMuse is deleted, and a chat is never overwritten: a name collision on a file gets a
  numbered copy.
- **Keep chat history in background memory** writes every imported message into the
  archive the agent searches with `deep_memory`, so later chats can look it up even when
  the conversation is not restored.
- **Muse's apps and code** either keeps the complete source in the workspace, registers
  the apps so they run at `/apps/<name>`, does both, or skips them.
- **The Muse main chat** becomes a side chat (the safe pick), takes over as the main
  chat while the current main chat is set aside as a side chat, untouched, or merges
  into the current main chat with both histories interleaved by message time.
- **Keep the original archive** stores the uploaded ZIP, byte for byte, under
  `workspace/imports/` next to a report of what the import found, so the agent can check
  the source later. Delete either file from the same screen; the imported chats do not
  depend on it.

Every section of a Muse archive is read: the profile and user details, the avatar bundle
plus the stills and clips the app animates, the transcripts, the apps with their own data,
notes, goals, documents, uploads, and the system folder with preferences and schedules.

Imports are idempotent: the runtime remembers what it has already imported by content, so
sending the same archive again stores no memory file twice, does not double the search
index, and adds no message that is already in the chat. Credentials are never part of an
import.

When the archive carries its own counts (`counts` in `profile/muse-profile.json`), the
import compares them with what it actually staged and shows the result, section by section.
A section with fewer files than the archive promised is named as a gap instead of being
called complete; more files than promised is reported as such, since nothing is missing.

## Update a Muse export instead of rebuilding it

A second transfer does not have to repeat the first. After an import, paste the transfer
code (a timestamp) above the prompt as `SINCE: <code>`, and Muse sends only what changed
after it. The importer treats the result as an update: files whose bytes it already has are
skipped, changed transcripts merge into their existing chat, and the verification compares
against the delta counts. Deletions are not synced yet: a file removed in Muse stays here
until it is deleted here.

## Check the result

Compare the imported memory count with the number of new, distinct facts you expected.
Open a sample of the documents and check their text and resource references. Confirm
each reconfigured connection exposes the intended tools. Keep the original archive
until these checks pass; it remains the source for history and metadata that have no
import path yet.
