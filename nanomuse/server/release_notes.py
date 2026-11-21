"""Structured What's New notes and upgrade detection across MiniMuse releases.

Tracks `<data_dir>/update_seen.json` so whenever MiniMuse upgrades (via auto-update,
pip, installer, or APK), the UI greets the user with a concise popup explaining what
was Added, Improved, and Fixed instead of silently restarting without notice.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from nanomuse import __version__
from nanomuse.logger import logger

BUILTIN_NOTES: dict[str, dict[str, Any]] = {
    "1.2.23": {
        "title": "Instant desktop boot, bigger avatar, memory, and chats that match",
        "added": [
            "The desktop window opens immediately on a glowing icon that says MiniMuse is booting",
            "Bulk delete for chats, and Match chats, which makes this device's chats a copy of a paired device",
            "A Memory section in Settings, including the Muse memory files from an import, synced between paired devices",
        ],
        "improved": [
            "The avatar is larger everywhere, and larger again in the sheet that opens when you tap it",
            "Sync keeps long assistant replies, including ones that came from a Muse import",
        ],
        "fixed": [
            "A memory refresh that fails no longer throws away the list already on screen",
            "Uploads, research notes, and virtualenv files in a Muse export stay files instead of becoming extra chats",
            "Opening a chat on Android no longer flashes the system play button before the avatar moves",
            "Clearing a chat resets its count, so an empty chat does not keep the old total",
        ],
    },
    "1.2.13": {
        "title": "PC-phone sync fixed, unified Devices, Muse Watch OS overhaul & Desktop Fullscreen",
        "added": [
            "Desktop Fullscreen Mode (F11 shortcut, sidebar Fullscreen button, and Appearance toggle, remembered across launches)",
            "Bidirectional sync of Muse memories, goals, reminders, profile, avatar animations/frames, and MiniMuse HTML apps between paired devices",
            "Muse-faithful Wear OS watch app: auto-hiding curved bezel buttons, scroll-to-shrink animated hero, horizontal swipe pages (Chats, Home, Watch Apps, Settings), curved top status, and left-bezel call volume arc",
        ],
        "improved": [
            "Unified Devices screen and device count across Settings and Devices (phone + PC now shows 2 devices with no duplicate 'Paired directly' split)",
        ],
        "fixed": [
            "Fixed 'Not syncing: name client is not defined' crash in peer-to-peer sync",
            "Fixed PC-to-phone pairing overwriting the phone's device ID and showing 'localhost', and fixed profile/avatar transfer silently failing",
        ],
    },
    "1.2.12": {
        "title": "The imported avatar shows up instead of a broken image",
        "added": [
            "The files route falls back to normal authentication when a signed link no longer matches, so a stale avatar link still loads",
        ],
        "improved": [
            "A profile avatar whose signed link had expired now loads through the session instead of showing a broken image",
        ],
        "fixed": [],
    },
    "1.2.11": {
        "title": "The real message count on long imported chats",
        "added": [
            "A chat imported before the counter existed takes its true message total from the search index once, instead of showing the 2,000 it kept",
        ],
        "improved": [
            "The count takes the larger of the kept events and the messages the deep archive knows, and survives restarts",
        ],
        "fixed": [
            "No more '2,000 events' ceiling on a 5,000-message imported chat",
        ],
    },
    "1.2.10": {
        "title": "Real message counts, a phone that listens, and the Muse avatar after a re-pair",
        "added": [
            "A chat's message count is its true total (up to five digits, then 99,999+)",
            "The phone's runtime listens on its network on its own, so a computer can pair with it and push chats",
        ],
        "improved": [
            "A re-pair, or a different data folder, is noticed: the other device reads the history from the start instead of trusting a cursor from the old store",
        ],
        "fixed": [
            "The imported Muse avatar is chosen by the folder that holds the animation, so a face with only stills is not worn when the moving one was imported",
        ],
    },
    "1.2.9": {
        "title": "Imported chats in order, and side chats that reach your phone",
        "added": [
            "A one-time backfill: side chats a paired device skipped arrive on its next sync, without pairing again",
        ],
        "improved": [
            "The chat list is ordered by the newest message from either person, imported chats included",
            "Chats imported before this build are put in order on the next start, without importing again",
        ],
        "fixed": [
            "Side chats now sync to your own paired devices: a paired phone no longer receives the main chat alone",
            "Syncing side chats is the default; the switch in Data controls still turns it off and the choice is remembered",
        ],
    },
    "1.2.8": {
        "title": "The app uses the whole screen, and the Devices page agrees with itself",
        "added": [],
        "improved": [
            "The window fills a 16:9 monitor and an ultrawide: no more 1200px column with empty margins",
            "Settings uses two columns on a wide screen and three on an ultrawide",
            "The conversation column grows with the screen instead of staying narrow",
        ],
        "fixed": [
            "A device paired directly (no account) now appears on the Devices page under 'Paired directly', is counted in the device total, and lights the sidebar dot - the same state Data controls already showed",
        ],
    },
    "1.2.7": {
        "title": "Watch: a keyboard that opens, an animated face, and no more 'Connecting...'",
        "added": [
            "The watch home screen plays the agent's own animation, looped, instead of a still",
        ],
        "improved": [
            "The three round buttons follow the bezel's curve, with the chat button lower like the other two",
            "The keyboard icon is the real one, and the glyphs are bigger",
        ],
        "fixed": [
            "The keyboard button now opens the watch's keyboard (and says so if the watch has none)",
            "'Connecting...' now names the address that is not answering, with Retry and Re-pair, and finds the server again if its address changed",
        ],
    },
    "1.2.6": {
        "title": "Pairing works now, and it says what it is doing",
        "added": [
            "Pairing shows each step: reaching the other device, copying setup and keys, then syncing chats",
            "Setup has a Pair a device button on every step, so it is never hidden when you resume a setup",
            "Import & export shows upload progress while a big archive is on its way",
        ],
        "improved": [],
        "fixed": [
            "Pairing with a phone never worked: the computer app listened on this machine only while its QR handed out the network address",
            "The pairing code now carries the port the app really uses",
            "The other device is recorded at the address it actually reached you from, never a loopback address",
        ],
    },
    "1.2.5": {
        "title": "Your whole Muse export, read and checked",
        "added": [
            "Import & export: a Keep the original archive switch keeps the .zip you sent, byte for byte, next to a report of what the import found; delete either from the same screen",
            "Muse import now reads the newer export archive completely: profile and user details, the avatar bundle and its moods, transcripts in every header shape, apps with their own data, notes, goals, documents, uploads, and the system folder",
            "After an import the app shows the archive's own counts against what actually landed, section by section, and saves the same report in the workspace",
        ],
        "improved": [
            "The Muse prompt now asks for one exact transcript format, a counts block, no dependency folders, and an update-only mode for later transfers",
            "A conversation Muse carries twice in one archive arrives as one chat",
            "Sending the same archive again stores nothing twice and does not double the search index",
        ],
        "fixed": [
            "Transcripts that switch header format halfway through no longer lose their messages",
            "Goal notes named like transcripts stay notes, and HTML documents no longer show up as junk apps",
            "Avatar clips are no longer cut off at the text size limit",
        ],
    },
    "1.2.4": {
        "title": "The watch looks like Muse again",
        "added": [
            "Watch home: the agent's face full-bleed with round call / chat / voice buttons",
            "Watch chat: indigo bubbles, keyboard + mic row, approvals in the conversation",
            "The agent's avatar travels to the watch via /api/watch/state + the files API",
        ],
        "improved": [],
        "fixed": [
            "The watch no longer shows a plain button list: it now matches the original Muse watch app",
        ],
    },
    "1.2.3": {
        "title": "Google sign-in simplified",
        "added": [],
        "improved": [
            "Google sign-in works out of the box; the client id and secret are behind an Advanced toggle",
        ],
        "fixed": [],
    },
    "1.2.2": {
        "title": "Google sign-in out of the box, faster chats, and watch polish",
        "added": [
            "Google sign-in works without your own Google Cloud project: a public OAuth client id ships with the app",
            "Faster chats: off-screen messages are skipped while rendering",
        ],
        "improved": [
            "Watch pairing screen: clearer steps, optional server address",
        ],
        "fixed": [],
    },
    "1.2.1": {
        "title": "Muse imports that read properly, in the background, plus fixes from your first run",
        "added": [
            "Importing your Muse data now runs in the background with a progress bar you can see anywhere, setup included",
            "Settings, Import & export, Run setup again: pick up a setup that was cut short",
            "Settings, Model: see which model is answering and switch it right there",
            "One tap to install browser control on the computer",
            "Your apps are pinned at the top of Artifacts with a Run button",
            "Watch: the rotating bezel scrolls and sets call volume, with haptic feedback",
        ],
        "improved": [
            "The back arrow goes back one screen instead of always to the chat",
            "Watch pairing tries every server found on Wi-Fi with your one code",
        ],
        "fixed": [
            "Muse imports show real back-and-forth messages with the right sides and times, clean titles, and one chat per voice call",
            "Imports have no size limit and no longer turn every loose file into a chat",
            "Split-screen no longer freezes the phone app black, and the black bars are gone",
            "Scanning a pairing QR pairs from any screen and confirms on both devices",
            "The AI icon has room above it and the name tag no longer covers it",
        ],
    },
    "1.2.0": {
        "title": "Watch auto-install that finds the watch, a real map, safer rules and Auto proactivity",
        "added": [
            "An interactive map in Location (OpenStreetMap, no key): saved places, tap to add one, most-visited places, common routes and trip stats",
            "Auto proactivity: MiniMuse reaches out more when you answer its check-ins and backs off when you do not",
            "MiniMuse can be chosen as the default assistant on a phone or watch (Settings, Default assistant); not yet checked on real devices",
            "Transfer your Muse data in setup, and a permissions step that asks for what each device needs up front",
            "An Emergency stop and a real Permissions list under Safety",
        ],
        "improved": [
            "Watch auto-install finds the watch on any network interface, stays correct when the watch's address changes, and says plainly what to do when none is found",
            "Switch mode on the phone now asks first and says what will happen",
            "Setup uses the width of a wide screen; Calendar and Map are reachable from the phone drawer",
            "Approval rules moved under Safety",
        ],
        "fixed": [
            "The Devices screen no longer crashes",
            "A saved 'Never' approval rule is enforced again after a restart, and editing one rule no longer drops shell from 'always ask'",
            "GPS pings are no longer counted as trips",
            "The first chat no longer asks for your name twice",
        ],
    },
    "1.0.25": {
        "title": "Settings Redesign, Usage Restoration, Wear OS Polish & Privacy Hardening",
        "added": [
            "Restored the full Usage dashboard as its own dedicated tab inside Settings (and routed #usage directly to it)",
            "Added Wi-Fi mDNS watch discovery from Android and desktop plus a strict watch-only ADB install guard so watch updates never install onto phones",
            "Added live Settings search result counts (No results found / 1 result / N results), clickable jump shortcuts, and per-setting info toggles",
        ],
        "improved": [
            "Redesigned the Wear OS companion UI with pure-OLED contrast, rippled action cards, MiniMuse separated blue MM icon badge, pulsing voice orb halo, clean chat bubbles, and a collapsed Options drawer",
            "Replaced the Wear OS launcher icon with the blue MiniMuse separated MM logo and declared android.hardware.type.watch as required",
            "Separated desktop Settings sections into focused single-page views instead of one stacked scroll page",
            "Consolidated Muse migration into one versatile prompt that inspects existing exports, rebuilds partial data consistently, and explains how to export the .zip archive",
        ],
        "fixed": [
            "Fixed oversized onboarding top white bar on tall Android phones by stopping double safe-area insets and vertically centering progress dots and Skip setup",
            "Fixed Windows Defender Firewall re-prompting on desktop updates and CLI launches by routing serve/app through the single canonical MiniMuse.exe, stripping Mark of the Web ADS, and skipping non-canonical LAN discovery binds",
            "Audited and scrubbed personal data, private LAN/Tailscale auth bypass rules, and hardcoded workstation paths across the codebase",
        ],
    },
    "1.0.24": {
        "title": "Evergreen Self-Updates & Single-Flight Installer",
        "added": [
            "Evergreen app-shell versioning (SHELL_VERSION): routine UI and server updates apply in-place via the Python wheel without prompting to re-download or reinstall the APK",
            "Autonomous Wear OS watch OTA updater with FileProvider self-install, /api/watch/version feed, server-driven quick actions/cards, and an embedded Open App Shell WebView",
            "Automatic background ADB watch push when a previously paired watch reconnects",
        ],
        "improved": [
            "In-app Update buttons lock immediately on tap and display live download percentage progress",
            "About & Update banner prioritize instant in-place updates and keep the phone, watch, and PC runtime aligned",
        ],
        "fixed": [
            "Fixed repeated taps on Install Update queuing duplicate APK downloads in Android DownloadManager",
            "Fixed Wear OS in-app updater opening the browser instead of downloading and installing the update package natively",
        ],
    },
    "1.0.23": {
        "title": "MiniMuse v1.0.23",
        "added": [
            "Slim Centered Setup Top Bar (Onboarding.tsx): restored a compact 40px top bar with vertically centered progress dots and Skip setup button",
            "1-Tap Phone Google Calendar & Contacts Sync (MainActivity.kt, Bridge.kt, /api/connections/calendar/import): pull your Android phone's synced Google calendars and contacts straight into MiniMuse with 1 tap without hunting for .ics links or .vcf exports",
            "1-Click Free Weather & Maps APIs + Auto-Linked Google Workspace APIs (/api/connections/apis/enable-free, /api/connections/google/enable-apis): enable Open-Meteo Weather & OpenStreetMap Maps with zero API keys, and auto-authorize Gmail, Google Calendar, Tasks & Drive REST APIs from your Google OAuth sign-in",
        ],
        "improved": [
            "Quick Inline API Setup & Direct Key Links (ApiIntegrationsCard.tsx, api_registry.py): added direct Get API Key links across 25+ popular APIs, inline key-paste + auto-test on save, and collapsible advanced URL/header fields",
            "Auto-Detected Email Setup (ConnectionsScreen.tsx): typing your Gmail, Outlook, iCloud, Fastmail, QQ, or 163 address automatically configures IMAP/SMTP servers, strips spaces from pasted Google App Passwords, and hides manual ports cleanly",
            "Focused Onboarding Connect Tabs (Onboarding.tsx): quick-switch between All, Google & Mail, Calendar & Contacts, and 36+ APIs during setup",
        ],
        "fixed": [
            "Fixed Google OAuth sign-in not automatically triggering initial Calendar/Contacts sync or enabling Google REST APIs",
            "Fixed ApiIntegrationsCard auth mode dropdown mismatch for api_key_header and api_key_query presets",
        ],
    },
    "1.0.22": {
        "title": "MiniMuse v1.0.22",
        "added": [
            "Automatic Smartwatch App Download & One-Click Watch Install (Settings → Smartwatch & Devices → Smartwatch): automatically downloads MiniMuse-wear.apk in the background without visiting GitHub, installs it onto your watch via USB or Wireless Debugging, grants watch microphone and notification permissions, and opens the watch app already paired with your server URL and token",
            "Dedicated Smartwatch (Wear OS) section directly in Settings and one-click Grant Phone & Install Permissions button in Settings → Permissions and Settings → Smartwatch",
            "Automatic ADB / Platform-Tools provisioning: pre-bundled android-tools inside the Android local Linux runtime and automatic Google Platform-Tools download on Windows, macOS, and Linux when ADB is not yet installed",
            "Zero-typing Watch Intent Auto-Pairing (wear/MainActivity.kt): accepts server_url, token, agent_name, and pair_code intent extras so newly installed watches connect immediately on first launch",
        ],
        "improved": [
            "Self-update now supports editable/source checkouts as well as wheel, Android, and frozen binary installs, and automatically enables update checks whenever auto_update is turned on",
            "Android self-updater (AppUpdate.kt) now prompts for Install Unknown Apps permission proactively when needed, launches the package installer immediately from the foreground Activity when the download completes, and posts a high-priority direct Activity notification",
            "Wheel self-updater falls back to direct wheel extraction when pip is stripped in lightweight mobile/embedded runtimes",
        ],
        "fixed": [
            "Fixed update_check = false blocking background auto-updates even when auto_update was enabled in app settings",
            "Fixed Android background APK download completion silently skipping the install sheet on Android 10–15 when triggered from a BroadcastReceiver",
            "Fixed /api/watch/install and /api/watch/apk failing when MiniMuse-wear.apk was not already manually downloaded to disk",
        ],
    },
    "1.0.21": {
        "title": "MiniMuse v1.0.21",
        "added": [
            "Multi-API Keys & Smart Credit Routing (nanomuse/llm/smart_router.py, ConnectionsScreen): combine free Google AI Studio keys for Gemini, DeepSeek direct credits for DeepSeek, and OpenRouter fallback — with per-provider exhaustion policies (fallback to OpenRouter, switch model, pause until quota resets, fallback to local Ollama, or stop)",
            "Live Remaining Usage & Adaptive Quota Learning (/api/connections/llm/usage-status): queries live API credit balances from OpenRouter, DeepSeek, Moonshot, and SiliconFlow, tracks rolling minute/day/week/month request and token counters, reads x-ratelimit headers, and automatically learns unwritten free-tier ceilings when an API stops",
            "Updated Suggested Models, Expandable Cost Cards & 1-Click Use Default Set: updated defaults to deepseek/deepseek-v4.1-flash-1017 (deepseek-v4.1-flash-1017) and deepseek/deepseek-v4.1-pro-1017 alongside google/gemini-2.5-flash, google/gemini-3.1-flash-lite, anthropic/claude-sonnet-4.5, openai/gpt-5-mini, and qwen3.5:9b-32k, with expandable (ⓘ) cost/company/tier cards and a 1-click Use Default button",
            "Startup Device Pairing & Optional Device-Specific Overrides: pair right during initial setup (or paste a Setup & Profile Transfer Bundle) to copy all models, vault API keys, profile, custom AI avatar/animations, and chats at once, plus optional per-device appearance and default model overrides (disabled by default)",
            "Full Muse Data Link (data_export.py, data_import.py, ImportExport.tsx): exports and restores profile/muse-profile.json, USER.md, IDENTITY.md, custom avatar/<face_id>/ icons and animations, chats, memory, goals, and workspace apps/code",
        ],
        "improved": [
            "Single prominent startup language picker, floating mobile setup header controls (no white bar covering the AI creature), reassurance notice that all setup choices can be changed later in Settings, and preset color swatches + custom color pickers across all appearance options",
            "Instant zero-downtime background auto-update loop that waits for active AI turns to finish, hot-swaps cleanly, reloads connected clients, and purges old .old/.prev binaries, wheels, and APKs so old versions are never kept",
        ],
        "fixed": [
            "Fixed mid-setup 'v.some is not a function' crash when advancing from Model setup to Connect by unwrapping r.apis in api.apis() and guarding ApiIntegrationsCard arrays",
            "Fixed non-English text in English mode (separated English/Chinese AI name suggestion pools, replaced Chinese example text in RecallCard) and replaced remaining user-facing nanomuse serve / nanoMuse Cloud strings with minimuse serve / MiniMuse Cloud",
        ],
    },
    "1.0.20": {
        "title": "MiniMuse v1.0.20",
        "added": [
            "English is now the default language across the entire app, paired with an upfront language picker in initial setup (SignInGate and Onboarding) that sets both the UI locale and the AI response language across English, Chinese, Spanish, French, German, Japanese, Korean, and Portuguese",
            "Custom hex color pickers for canvas background, accent color, and chat bubbles directly inside the onboarding Appearance step",
            "Local AI Hardware & Runtime Advisor (/api/system/capabilities and LocalAiAdvisor) inspecting RAM, CPU cores, OS, and running Ollama, LM Studio, or vLLM runtimes to recommend models your device can handle and advise honestly when a low-RAM phone is better served by a cheap or free OpenRouter cloud model",
            "Touch-friendly searchable model browsers (ModelSearchField and Switchable Options search) replacing mobile-hostile HTML datalists so models can be searched and added reliably on phones",
        ],
        "improved": [
            "Popular-first, rare-alphabetical ranking across model lists (rank_models, splitPopularAndAlphabetical, orderRows) and 36+ API integration presets (preset_list)",
            "Cleaned up mixed Chinese subtitles in provider metadata so setup cards render in clean English by default",
        ],
        "fixed": [
            "Fixed mid-setup stall and blank background-only crash screen after saving model configuration by adding recoverable ErrorBoundary wrappers, ModelCard/ModelMenu null-safety guards, persistent onboarding step state across reloads, and explicit Save & continue progression from Model setup to Connect",
            "Preserved unknown vision capability as indeterminate in model_takes_images() for generic open-weight model families",
        ],
    },
    "1.0.19": {
        "title": "MiniMuse v1.0.19",
        "added": [
            "36+ built-in API presets (GitHub, Notion, Slack, Linear, Stripe, Home Assistant, Spotify, Jira, Trello, Airtable, Twilio, Cloudflare, Supabase, Shopify, and more) plus the Universal AI API Builder with OpenAPI discovery and learned endpoints",
            "Multi-account support for Google (Gmail XOAUTH2, Calendar sync, and People contact addresses) and IMAP/SMTP Email right from initial setup and Connections",
            "Startup appearance customization during onboarding for theme, accent color, scale, AI name and creature look, plus toggleable navigation tabs",
            "New toggleable Calendar screen with Agenda, Week, Month views, local event CRUD alongside ICS/Google feeds, and Map deep-links",
            "New toggleable Location and Maps screen tracking saved/frequent places, travel history, driving speeds and traffic estimates, friend/contact addresses from Contacts and Google People, #tag search, and 1-tap Google/Apple/Waze/OSM map links",
            "Enhanced Tasks and Goals with quick-add inline #tags, 8-mode sorting/filtering, priority badges, recurrence, due times, and expandable inline subtask toggles",
        ],
        "improved": [
            "Local / bring-your-own-key setup is now the primary hero screen on desktop, web, and Android onboarding, while nanoMuse Cloud moves to a collapsible side option",
            "Cleaner top-aligned scrollable setup layout and transparent header scrim across mobile screens",
        ],
        "fixed": [
            "Removed the solid white bar overlay covering the animated AI circle creature on mobile onboarding viewports",
            "Fixed Location screen map links and ContactBook address validation so address-only contacts save cleanly",
        ],
    },
    "1.0.18": {
        "title": "MiniMuse v1.0.18",
        "added": [
            "Canonical Windows and Linux single-path install handoff (%LOCALAPPDATA%\\Programs\\MiniMuse\\MiniMuse.exe on Windows, ~/.local/bin/MiniMuse on Linux) so pinned shortcuts and Windows Firewall approvals persist across updates.",
            "Loopback-only authenticated /api/system/shutdown handoff endpoint and automatic Windows Start Menu / Desktop shortcut repair.",
            "Poll-verified post-update browser reload (waitForUpdatedServerAndReload) that waits for /api/health to report the new server version before reloading.",
        ],
        "improved": [
            "Service Worker cache versioning (nm-shell-v5, nm-data-v5) with network-only bypass for /api/health, /api/update, /api/state, and /api/settings plus live Settings version refresh.",
            "Automatic cleanup of obsolete downloaded installers, extracted temp folders, and redundant cached Android update APKs while keeping rollback wheels intact.",
            "Consistent Windows AppUserModelID across standalone and Electron desktop apps plus stale second-instance handoff on Windows and support for both MiniMuse-Desktop-* and nanoMuse-Desktop-* NSIS installers.",
        ],
        "fixed": [
            "Eliminated silent updater swap skips by writing SWAPPED exit status and recording swap errors in updates/swap_error.txt surfaced automatically on next startup.",
            "Resolved stale post-update version banners caused by aggressive browser/SW caching or older tray and runtime instances staying resident on the port.",
        ],
    },
    "1.0.17": {
        "title": "MiniMuse v1.0.17",
        "added": [
            "OpenRouter as default quick-setup provider while preserving all other providers, local endpoints, and accounts.",
            "Multi-model lineup selection in Connections with instant model switching directly in the chat header pill.",
            "One-click Gmail & Google popup setup, Google OAuth 2.0 PKCE + XOAUTH2 + Calendar/Contacts sync, plus 'Let AI set this up' guided setup.",
            "Step-by-step Wear OS smartwatch install guide and direct ADB watch APK push under Devices.",
        ],
        "improved": [
            "Eliminated flashing Windows PowerShell and CMD windows across background subprocesses, ACL saves, GPU checks, coding runners, and Tailscale probes.",
            "Smoothed chat rendering performance: animation-frame coalesced streaming deltas, memoized markdown & bubbles, cached Win32 probes, and rAF resize observers.",
            "Clearer Devices badge confirming the Chrome Extension stays private and unpacked rather than listed on Chrome Web Store.",
        ],
        "fixed": [
            "Windows native foreground process detection without spawning repeated tasklist shells.",
            "Resettable setup state syncing in ModelCard so default OpenRouter preset stays active cleanly before saving.",
        ],
    },
    "1.0.16": {
        "title": "MiniMuse v1.0.16",
        "added": [
            "365-day recoverable Trash for deleted chats with Restore, Delete Forever, and Empty Trash.",
            "Individual message deletion right from any chat bubble.",
            "Cloud-free Direct P2P Sync with LAN auto-discovery, 6-char pairing codes, and offline edit merge.",
            "Universal auto-update across desktop, Android, web PWA, and Python runtime with What's New changelog popups.",
            "MiniMuse Live Call mode for real-time voice models plus universal hands-free voice loop.",
            "Simplified Wear OS Smartwatch app with Live Call and keep-awake speech protection.",
            "Chrome Extension + background tab/window automation previewed live inside chat without stealing PC focus.",
            "Free Personal MiniMuse Cloud relay helper using Tailscale or Cloudflare Tunnel.",
        ],
        "improved": [
            "Clear confirmation warnings before Hands-on PC control, external browser popups, permissions, or cloud-off resets.",
            "MiniMuse branding consistency across greetings, tray, Android accessibility, and server banners while preserving nanoMuse Cloud.",
            "Browser automation isolates headless sessions from interfering extensions.",
        ],
        "fixed": [
            "New chat draft flow, side-chat rename, and side-chat delete reliability.",
            "P2P /v1/sync/* route priority and Windows concurrent save atomic locks.",
            "Removed Goku preset avatar from default avatar pickers.",
        ],
    }
}

_SECTION_RE = re.compile(r"^#{1,4}\s*(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.+?)\s*$")
_CHAN_VERSION_RE = re.compile(
    r"^##\s+\[?v?([0-9]+(?:\.[0-9]+)+)\]?(?:\s*[-\u2013\u2014]\s*(.*))?$"
)


def _clean_md_line(text: str) -> str:
    s = re.sub(r"`([^`]+)`", r"\1", text.strip())
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return " ".join(s.split())


def _bucket_for_heading(heading: str) -> str | None:
    low = heading.lower()
    if any(k in low for k in ("add", "new", "feature", "highlight")):
        return "added"
    if any(k in low for k in ("fix", "bug", "repair", "patch")):
        return "fixed"
    if any(k in low for k in ("improv", "chang", "enhanc", "updat", "polish", "perf")):
        return "improved"
    return None


def _bucket_for_line(line: str) -> str:
    low = line.lower()
    if any(k in low for k in ("fix", "bug", "crash", "error", "race", "remov")):
        return "fixed"
    if any(k in low for k in ("add", "new ", "support", "introduc", "watch", "call", "chrome", "trash")):
        return "added"
    return "improved"


def parse_release_markdown(
    version: str, markdown: str, title: str = ""
) -> dict[str, Any]:
    ver = version.lstrip("v").strip()
    added: list[str] = []
    improved: list[str] = []
    fixed: list[str] = []
    bucket: str | None = None

    for raw in markdown.splitlines():
        hm = _SECTION_RE.match(raw)
        if hm:
            detected = _bucket_for_heading(hm.group(1))
            if detected is not None:
                bucket = detected
            continue
        bm = _BULLET_RE.match(raw)
        if not bm:
            continue
        item = _clean_md_line(bm.group(1))
        if not item:
            continue
        target = bucket or _bucket_for_line(item)
        if target == "added" and len(added) < 8:
            added.append(item)
        elif target == "fixed" and len(fixed) < 8:
            fixed.append(item)
        elif target == "improved" and len(improved) < 8:
            improved.append(item)

    if not (added or improved or fixed) and ver in BUILTIN_NOTES:
        b = BUILTIN_NOTES[ver]
        return {
            "version": ver,
            "title": title or str(b.get("title") or f"MiniMuse v{ver}"),
            "added": list(b.get("added") or []),
            "improved": list(b.get("improved") or []),
            "fixed": list(b.get("fixed") or []),
        }
    return {
        "version": ver,
        "title": title or f"MiniMuse v{ver}",
        "added": added,
        "improved": improved,
        "fixed": fixed,
    }


def _find_changelog_section(version: str) -> str:
    ver = version.lstrip("v").strip()
    candidates = [
        Path(__file__).resolve().parents[2] / "CHANGELOG.md",
        Path.cwd() / "CHANGELOG.md",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text("utf-8", errors="replace")
        except OSError:
            continue
        capturing = False
        lines: list[str] = []
        for line in text.splitlines():
            m = _CHAN_VERSION_RE.match(line)
            if m:
                if capturing:
                    break
                if m.group(1) == ver:
                    capturing = True
                continue
            if capturing:
                lines.append(line)
        if lines:
            return "\n".join(lines)
    return ""


def _parse_ver(v: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


class ReleaseNotesTracker:
    """Persists last seen app version and pending What's New popup data."""

    def __init__(self, data_dir: Path, current: str = __version__) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "update_seen.json"
        self.current = current.lstrip("v")
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text("utf-8"))
                if isinstance(raw, dict):
                    return raw
            except (OSError, ValueError):
                pass
        return {}

    def _save(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.data_dir / f"{self.path.name}.{os.getpid()}.tmp"
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.debug("update_seen save failed: {}", exc)

    def _has_prior_data(self) -> bool:
        for name in ("state.db", "config.toml", "pairs.json", "threads"):
            if (self.data_dir / name).exists():
                return True
        return False

    def detect_on_startup(self) -> dict[str, Any] | None:
        """Detect an upgrade since the last run and prepare a What's New card."""
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return self.pending()
        last = str(self._data.get("last_version") or "").lstrip("v")
        cur_t = _parse_ver(self.current)
        if not last:
            if self._has_prior_data():
                # Upgrading onto release-notes tracking from an existing install.
                notes = self.notes_for(self.current)
                pending = {**notes, "previous": ""}
                self._data["pending"] = pending
                self._data["last_version"] = self.current
                self._save()
                return pending
            self._data["last_version"] = self.current
            self._save()
            return None
        last_t = _parse_ver(last)
        if cur_t and last_t and cur_t > last_t:
            notes = self.notes_for(self.current)
            pending = {**notes, "previous": last}
            self._data["pending"] = pending
            self._data["last_version"] = self.current
            self._save()
            return pending
        return self.pending()

    def cache_release(self, version: str, markdown: str, title: str = "") -> dict[str, Any]:
        ver = version.lstrip("v").strip()
        notes = parse_release_markdown(ver, markdown, title)
        cached = self._data.setdefault("cached", {})
        if isinstance(cached, dict):
            cached[ver] = notes
            self._save()
        return notes

    def notes_for(self, version: str, markdown: str = "", title: str = "") -> dict[str, Any]:
        ver = version.lstrip("v").strip()
        cached = self._data.get("cached")
        if isinstance(cached, dict) and isinstance(cached.get(ver), dict):
            c = dict(cached[ver])
            if c.get("added") or c.get("improved") or c.get("fixed"):
                return c
        if markdown:
            parsed = parse_release_markdown(ver, markdown, title)
            if parsed.get("added") or parsed.get("improved") or parsed.get("fixed"):
                return parsed
        changelog_md = _find_changelog_section(ver)
        if changelog_md:
            parsed = parse_release_markdown(ver, changelog_md, title)
            if parsed.get("added") or parsed.get("improved") or parsed.get("fixed"):
                return parsed
        return parse_release_markdown(ver, "", title)

    def pending(self) -> dict[str, Any] | None:
        p = self._data.get("pending")
        return dict(p) if isinstance(p, dict) and p.get("version") else None

    def dismiss(self) -> None:
        self._data["pending"] = None
        self._data["last_version"] = self.current
        self._save()
