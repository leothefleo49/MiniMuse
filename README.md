<p align="center">
  <img src="assets/brand/nanomuse-icon.svg" width="96" height="96" alt="MiniMuse (MM)">
</p>

# MiniMuse

> **MiniMuse is a personal fork of [nanoMuse](https://github.com/nano-muse/nanoMuse) (GPL-3.0-or-later), built and maintained by [leothefleo49](https://github.com/leothefleo49).**
> It keeps everything upstream nanoMuse does — the agent, the Sentinel, the web console, the phone lines, the desktop app — and adds this fork's own features on top, listed below. Release builds ship a clean runtime and a clean phone capsule with zero personal data baked in.

---

## Navigation & Quick Skip Links

Every section below is one click away — each link on its own line:

[Quick Start](#quick-start)

[What It Is](#what-it-is)

[Multi-API Smart Credit Routing & Live Usage](#multi-api-smart-credit-routing--live-usage)

[Updated Models, Cost Cards & 1-Click Defaults](#updated-models-expandable-cost-cards--1-click-defaults)

[Startup Device Pairing & Device Overrides](#startup-device-pairing--optional-device-overrides)

[Full Muse Data Link & Profile/Avatar Transfer](#full-muse-data-link--profileavatarcode-transfer)

[Local-First Setup & Appearance](#local-first-setup--appearance)

[Multi-Account, 36+ APIs & AI Builder](#multi-account-36-apis--ai-builder)

[Calendar, Location & Enhanced Tasks](#calendar-location--enhanced-tasks)

[Easy Gmail & Google Setup](#easy-gmail--google-setup)

[Live Call & Wear OS Smartwatch](#live-call--wear-os-smartwatch)

[Private Chrome Extension & Background Control](#private-chrome-extension--background-control)

[Personal Cloud & Offline P2P Sync](#personal-cloud--offline-p2p-sync)

[Instant Background Auto-Update & Trash](#instant-background-auto-update--trash-protection)

[All Features](#everything-minimuse-can-do)

[Downloads & Platforms](#downloads--platforms)

[Building from Source](#building-from-source)

[Troubleshooting](#troubleshooting)

---

## Quick Start

1. **Install or launch MiniMuse:** download the package for your platform from [Releases](https://github.com/leothefleo49/MiniMuse/releases/latest) (see [Downloads & Platforms](#downloads--platforms)) or run `pip install -e ".[browser,hands]" && minimuse app`.
2. **Pick your language & sync or set up:** English is the default, with a prominent **Language Picker** right on the first screen. Already set up MiniMuse on another PC or phone? Click **Sync from another device** right on the startup screen to copy all your models, API keys, profile, AI avatar/animations, and chats in one step. Everything chosen during setup can be adjusted anytime later in *Settings*.
3. **Click "Use Default" or customize Multi-API routing:** in the startup wizard or *Connections -> Chat model*, click **Use Default** to automatically assign the best updated models for every task (`deepseek/deepseek-v4.1-flash-1017` for chat, `google/gemini-2.5-flash` for vision/hands, `google/gemini-3.1-flash-lite` for background tasks, `anthropic/claude-sonnet-4.5` & `deepseek/deepseek-v4.1-pro-1017` for heavy reasoning, and `qwen3.5:9b-32k` for local fallback). Or open **Multi-API Keys, Smart Credit Routing & Live Usage** to combine free Google AI Studio keys, DeepSeek direct credits, and OpenRouter fallback.
4. **Connect mail, calendar, & contacts your way:** click **Google Popup (App Password)**, **Google Calendar/Contacts Popup**, full **Google OAuth + Sync**, manual IMAP/ICS/VCF, or **Let MiniMuse AI set this up**.

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## What it is

One agent with a name and a look of its own, in the spirit of Meta's Muse: it **does things** instead of only answering — it keeps working while the app is closed, remembers you across sessions, and stops to ask before anything you could not undo. You bring any model you like (OpenRouter, DeepSeek, Google AI Studio / Gemini, OpenAI, Claude, Qwen, a local Ollama or any OpenAI-compatible endpoint) with your own keys. It runs on your PC, in your browser as a PWA, on your smartwatch (Wear OS), and — with this fork's APK — completely offline on your Android phone, even with your PC dead, broken, or disconnected.

Upstream nanoMuse is by the nano-muse contributors (paper: *nanoMuse: An Open-Source Personal Agent for Every Device You Own*). MiniMuse tracks it and layers this fork's upgrades on top; the upstream README of the pinned version is kept at [docs/readme/upstream-README-1.0.9.md](docs/readme/upstream-README-1.0.9.md).

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## Key Fork Highlights

### Multi-API Smart Credit Routing & Live Usage
- **Use multiple API keys simultaneously (`nanomuse/llm/smart_router.py`):** Store separate encrypted vault keys for **Google AI Studio (`GEMINI_API_KEY`)**, **DeepSeek Direct (`DEEPSEEK_API_KEY`)**, **OpenRouter (`OPENROUTER_API_KEY`)**, **Groq (`GROQ_API_KEY`)**, **OpenAI (`OPENAI_API_KEY`)**, **Anthropic (`ANTHROPIC_API_KEY`)**, **Moonshot (`MOONSHOT_API_KEY`)**, and **SiliconFlow (`SILICONFLOW_API_KEY`)**.
- **Maximize free tiers & prepaid credits automatically:**
  - When you pick a **Gemini** model (`gemini-2.5-flash`, `gemini-3.1-flash-lite`), MiniMuse automatically uses your free **Google AI Studio** key first.
  - When you pick a **DeepSeek** model (`deepseek-v4.1-flash-1017`, `deepseek-v4.1-pro-1017`), MiniMuse uses your **DeepSeek direct credits** first.
  - When any direct provider key runs out of credits (`HTTP 402`) or hits a rate/quota limit (`HTTP 429`), `RoutedLLM` seamlessly fails over mid-turn according to your custom per-provider rule:
    1. **Use OpenRouter for this model** (continues using the exact same Gemini/DeepSeek/Claude model via OpenRouter),
    2. **Switch to a fallback model** (e.g., switch to another model once your DeepSeek credits finish),
    3. **Pause this AI until quota resets** (stops using that free tier until its minute/daily/weekly/monthly reset window reopens),
    4. **Fallback to local AI (Ollama)**, or
    5. **Stop & notify**.
- **Live remaining usage & adaptive quota learning (`/api/connections/llm/usage-status`):**
  - Polls live prepaid credit balances directly from provider APIs (**OpenRouter `/api/v1/credits`**, **DeepSeek `/user/balance`**, **Moonshot `/v1/users/me/balance`**, **SiliconFlow `/v1/user/info`**).
  - Parses live `x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`, and `Retry-After` headers on every response.
  - Tracks rolling per-minute, daily, weekly, and monthly request & token counters in `llm-quota-state.json`, and **automatically learns unwritten free-tier ceilings** whenever an API stops so it knows whether a provider is token-based, message-based, free daily reset, or credit-balance based.

### Updated Models, Expandable Cost Cards & 1-Click Defaults
- **Updated suggested models:** Replaces outdated `deepseek-v4-flash` defaults with **`deepseek/deepseek-v4.1-flash-1017`** (`deepseek-v4.1-flash-1017`) and **`deepseek/deepseek-v4.1-pro-1017`**, alongside `google/gemini-2.5-flash`, `google/gemini-3.1-flash-lite`, `google/gemini-2.5-pro`, `anthropic/claude-sonnet-4.5`, `anthropic/claude-haiku-4.5`, `openai/gpt-5-mini`, `openai/gpt-5`, `qwen/qwen3.5-plus`, and local `qwen3.5:9b-32k`.
- **Expandable Model Details (`ⓘ` button on every suggested model):** Tap `ⓘ` next to any suggested model in setup or *Connections -> Chat model* to expand its **Company**, **Recommended Tier (`Light Use`, `Normal Use`, or `Heavy Use`)**, **Best for** summary, **Cost per 1M input/output tokens**, and **Free tier / Quota reset rules**.
- **1-Click "Use Default" Recommended Best Set (`/api/connections/llm/apply-defaults`):** Click **Use Default** in *Chat model* to automatically configure the optimal model for every job:
  - **Normal Chat & Tools:** `deepseek/deepseek-v4.1-flash-1017` (or `deepseek-v4.1-flash-1017` on direct DeepSeek)
  - **Vision & Screen Hands:** `google/gemini-2.5-flash`
  - **Light / Background Tasks:** `google/gemini-3.1-flash-lite`
  - **Heavy Use & Deep Reasoning (in quick-switch bar):** `anthropic/claude-sonnet-4.5` & `deepseek/deepseek-v4.1-pro-1017`
  - **Local Offline Fallback:** `qwen3.5:9b-32k`

### Startup Device Pairing & Optional Device Overrides
- **Pair right during initial setup:** Both the opening screen (`SignInGate`) and the setup wizard (`Onboarding`) include **Sync setup from another device**. Enter a 6-character pairing code (or pick a LAN-discovered device) or paste a **Setup & Profile Transfer Bundle** to immediately pull all models, encrypted vault API keys, profile details, custom AI avatar/animations, and chats — no need to configure a new device manually and pair later.
- **Optional Device-Specific Overrides (disabled by default):** Under *Settings -> Data controls -> Sync between your devices -> Advanced: Device-specific appearance & default model overrides*, you can optionally let a specific device keep its own theme/colors (`keep_local_appearance`) or use a device-specific default AI model when messages are typed from that device (`default_model`), while still seeing the model each message was originally sent with across all devices.

### Full Muse Data Link & Profile/Avatar/Code Transfer
- **Complete Muse Data Link (`data_export.py`, `data_import.py`, `ImportExport.tsx`):**
  - Exporting a backup or Muse transfer archive now bundles `profile/muse-profile.json`, `profile/USER.md`, `profile/IDENTITY.md`, custom `avatar/<face_id>/` icons and state animation frames (`idle`, `thinking`, `speaking`, `working`, `happy`, `error`), `memory/`, `threads/`, `goals/`, `reminders/`, and `workspace/` apps/code.
  - Importing a `.zip` or pasting a Muse output block / Setup Transfer Bundle restores your profile, user details, AI name, custom avatar icons & animations, and chats in one step.

### Local-First Setup & Appearance
- **Single prominent startup language picker:** English is the default everywhere (including English AI name suggestions `Muse, Nova, Aria, Atlas, Echo, Sage, Pixel, Lyra` and English model/memory subtitles). A clear language selector appears once at the very beginning of startup.
- **Floating mobile setup controls (no top white bar):** Progress dots and `Skip setup` float cleanly in the top corners without a full-width white header bar, letting the animated AI creature sit higher on mobile screens so more content fits at once without overlapping step headers.
- **Preset color swatches + Custom color picker across all appearance options:** Accent color, **Chat bubble color** (User messages), Canvas background color, and **AI Creature / Avatar color** all provide both curated preset swatches and a full custom color picker in Setup and Settings.
- **Reassurance notice:** Setup explicitly notes that *everything you choose during setup can be changed or adjusted anytime later in Settings*.

[↑ Back to Navigation](#navigation--quick-skip-links)

---

### Multi-Account, 36+ APIs & AI Builder
- **Multiple accounts per provider:** Connect multiple Google accounts (Gmail XOAUTH2 + Calendar + People addresses) and multiple IMAP/SMTP email accounts, including during initial setup.
- **36+ built-in API integrations + Universal AI API Builder:** One-click presets (GitHub, Notion, Linear, Slack, Discord, Stripe, Home Assistant, Spotify, Jira, Trello, Airtable, Twilio, Cloudflare, Supabase, Shopify, and more) alongside custom REST/OpenAPI discovery where the AI learns and saves endpoints (`api_connector`).

### Calendar, Location & Enhanced Tasks
- **Toggleable Calendar & Location tabs:** Enable or hide **Calendar** (Agenda, Week, Month, local event CRUD + `.ics` feeds, and map deep-links) and **Location & Maps** (saved/frequent places, travel & driving speed/traffic tracking, contact & friend addresses synced from Contacts/Google People, `#hashtag`/contact/context search, and one-tap opening in Google Maps, Apple Maps, Waze, or OpenStreetMap) in Onboarding or *Settings -> Navigation tabs & Maps*.
- **Enhanced Tasks & Subtasks (`Goals`):** Quick-add tasks or goals with inline `#tags`, 8-mode sorting and filtering, priority badges, recurrence, due times, and inline subtask checkboxes.

### Easy Gmail & Google Setup
Under **Onboarding** and **Connections** (*Email*, *Calendar*, *Contacts*), pick whichever style you prefer:
- **1-Click Google Popup & Sync (Zero Cloud Console needed):** click **Google Popup (App Password)**, **Google Calendar Popup**, or **Google Contacts Popup** to open the exact Google screen in a clean centered popup while MiniMuse pre-fills Gmail IMAP/SMTP (`imap.gmail.com:993`, `smtp.gmail.com:587`) or Calendar/Contacts naming.
- **Full Google OAuth 2.0 (PKCE + XOAUTH2 + API Sync):** optionally paste a Google Cloud Desktop OAuth Client ID once (stored encrypted in your vault as `GOOGLE_CLIENT_ID`). Click **Sign in with Google Popup** to connect Gmail over `SASL XOAUTH2` (with automatic token refresh and zero app password) and pull your Google Calendar events and Google Contacts in one click via **Sync Google Calendar & Contacts**.
- **AI-Guided Setup:** click **Let `<model>` set this up** beside Email, Calendar, or Contacts to launch an interactive chat walkthrough powered by whichever AI model you chose.
- **Full manual control stays intact:** custom IMAP/SMTP servers, `.ics` links/files, and `.vcf` uploads/paths work cleanly side by side.

[↑ Back to Navigation](#navigation--quick-skip-links)

---

### Live Call & Wear OS Smartwatch
- **MiniMuse Live Call mode (`/api/call/*`, `/ws/call`):** native low-latency voice support for **OpenAI Realtime** and **Gemini Live (`BidiGenerateContent`)** alongside a universal hands-free STT -> Agent -> TTS call loop that works with *any* text model, featuring turn-pause patience (`Patient 3.5s`, `Balanced 2s`, `Manual Done`, `+7s` extend) and Web Screen Wake Lock.
- **Wear OS Smartwatch app (`MiniMuse-<version>-wear.apk`):** standalone Compose wrist companion with auto LAN discovery + zero-typing intent auto-pairing (`/api/watch/*`), quick Live Call & Voice Ask buttons, `FLAG_KEEP_SCREEN_ON` + `PARTIAL_WAKE_LOCK` + **"Still Talking (+10s)"** speech protection so long wrist prompts don't cut off prematurely, recent chat replies, and one-tap wrist Sentinel approvals.
- **One-Click Automatic Watch App Download & Install (no manual GitHub download needed):**
  1. Open **Settings -> Smartwatch (Wear OS)** (or **Devices -> Smartwatch App**).
  2. Tap **Grant Phone & Install Permissions** if prompted on Android.
  3. On your watch (first time only), open **Settings -> Developer options**, enable **ADB debugging** and **Wireless debugging**, and enter the watch's `IP:Port` (and optional first-time 6-digit Wi-Fi pairing code) or connect via USB.
  4. Tap **Auto-Download & Install on Watch**: MiniMuse automatically downloads `MiniMuse-wear.apk` in the background, ensures `adb` is installed, installs the app onto your watch, grants watch microphone and notification permissions (`RECORD_AUDIO`, `POST_NOTIFICATIONS`), and opens the watch app already paired with your server URL and token!

### Private Chrome Extension & Background Control
- **Private unpacked extension (never published to Chrome Web Store):** the MV3 extension in `chrome-extension/` (and downloadable privately inside MiniMuse *Devices -> Chrome Side Panel -> Download Chrome Extension (.zip)* or from Releases as `MiniMuse-<version>-chrome-extension.zip`) stays 100% private and local. Extract the zip, open `chrome://extensions`, turn on **Developer mode**, and click **Load unpacked**.
- **Isolated background automation + live chat preview:** share any Chrome tab from the MiniMuse Side Panel via `chrome.debugger` (`/ws/chrome`), or pick a desktop window in *Devices -> Background Window Control*. On Windows, `WinWindowHands` captures frames (`PrintWindow`) and sends inputs (`PostMessageW`) in the background *without stealing focus*, streaming live thumbnails directly into chat so you can keep using your PC normally. Headless Playwright also launches with `--disable-extensions` isolation flags.

### Personal Cloud & Offline P2P Sync
- **Free `$0` Self-Hosted MiniMuse Cloud:** *Account / Settings -> Your Free Personal MiniMuse Cloud* surfaces your best direct LAN/Tailscale URL and offers a one-click free **Cloudflare Quick Tunnel (`trycloudflare.com`)** so your phone and watch reach your home PC securely over HTTPS without paying for a VPS or external cloud subscription.
- **Cloud-Free Direct P2P Sync & Offline Merge (`pairs.json`, `/v1/sync/*`):** pair your phone, tablet, and PC directly via UDP LAN auto-discovery (`48787`), 6-character codes, or QR links — zero external cloud account needed. Messages, deletions, settings, and profile edits made while offline queue locally in a durable journal and merge chronologically whenever devices see each other again.

### Instant Background Auto-Update & Trash Protection
- **Zero-downtime background auto-update (`service.py`, `selfupdate.py`, `AppUpdate.kt`):**
  - Checks `leothefleo49/MiniMuse` releases automatically in the background (within 25s of boot and every 10 minutes thereafter).
  - Waits until any active AI turn finishes so it never interrupts a running conversation or loses data, verifies SHA-256 checksums, swaps the binary/wheel in place, and restarts seamlessly while the web UI automatically reloads its Service Worker cache on the new version.
  - **Never keeps old versions:** automatically purges `.old` / `.prev` binaries on startup and deletes stale downloaded `.apk` files on Android before and after update verification.
- **365-day recoverable Trash & individual message delete:** deleting a chat moves it to recoverable Trash for 365 days (**Restore**, **Delete Forever**, **Empty Trash**); individual bubbles can also be deleted right from chat with tombstone propagation across P2P synced devices.
- **Clear confirmation dialogs & zero flashing Windows popups:** `nanomuse/win_proc.py` hides all Windows console subprocesses (`CREATE_NO_WINDOW` + `SW_HIDE`).

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## Everything MiniMuse can do

### The agent itself
- **Does real work, not just chat.** Plan-act loop with tool verification, prompt-cache preservation across steps, background continuation while the app is closed, and notifications when done.
- **Any model, four jobs + multi-API smart routing.** *Connections -> Chat model* (1-click **Use Default** set, multi-API credit/quota routing, OpenRouter, DeepSeek, Gemini, OpenAI, Anthropic, or local Ollama), *Hands model* (screen vision), *Image*, and *Video*, plus multi-select switchable options inside chat.
- **Memory that sticks.** Deep-memory storage, live FTS indexing, semantic archive search, and persistent house rules learned from your corrections.
- **The Sentinel.** Risk-tiered approval guard with per-action cards, approve-by-chat, Android notification actions, and Wear OS wrist approvals.
- **Goals, reminders, alarms.** Scheduled goal check-ins, quiet hours, and exact Android alarm wakeups when permitted.

### The web console (PWA) & native desktop
- **Messenger-grade chat.** Draft side chats, swipe-to-reply quote, swipe-left timestamp, hold-to-rename/clear/delete chats, bubble deletion, up to 3 agent emoji reactions per message, sandboxed HTML app artifacts (`?view=file&path=...`), Android Add-to-Home-Screen shortcuts, offline outbox, local + live API usage ledger, per-device App Lock (off by default, alphanumeric/biometric), and full multi-language UI (English default).

### Tools & Channels
`shell` · `files` · `browser` · `chrome` · `coding` · `computer` · `pc_control` · `phone` · `devices` · `calendar` · `contacts` · `email` (Multi-Account Password & Google XOAUTH2) · `location` · `api_connector` · `reminders` · `goals` · `memory` + `deep_memory` · `passwords` · `system_admin` · `mcp` · `connectors` · `skills` · `surface` · `sql`.
**Channels:** Telegram, Feishu, DingTalk, WeCom, CLI.

### Safety & privacy
- Vault secrets (`{{vault:...}}`) live encrypted on disk (`keep_private` `0600` ACLs) and never enter model prompts.
- Zero telemetry, zero analytics, zero third-party crash uploads.
- Release builds automatically verify no local databases, transcripts, or keys leaked into the package before sealing.

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## Downloads & Platforms

Choose the file for your device from [GitHub Releases](https://github.com/leothefleo49/MiniMuse/releases/latest) — every release ships signed/verified with `MiniMuse-<version>-SHA256SUMS`:

| Platform | Asset | Notes |
| --- | --- | --- |
| **Windows (x64)** — recommended | `MiniMuse-<version>-win64-setup.exe` | Proper installer: Start Menu & desktop shortcuts, appears in Windows *Settings → Apps* for one-click uninstall, installs per-user (no admin), and the app keeps updating itself in place from GitHub Releases. |
| **Windows (x64)** — portable | `MiniMuse-<version>-win64.exe` / `-win64.zip` | Single-file native `pywebview` window, zero console flash, no install needed, same SHA-256 background auto-update. |
| **Linux (x64)** | `MiniMuse-<version>-linux` / `-linux-x64.tar.gz` | Standalone native executable with desktop window and auto-update. |
| **macOS (Apple silicon & Intel)** | `nanomuse-<version>-py3-none-any.whl` | `pip install` the wheel (Python 3.10+) and run `minimuse app` for the native window; a signed `.app` bundle is on the roadmap. |
| **Android Phone (arm64)** | `MiniMuse-<version>-arm64.apk` | Unified Local + Connect signed APK (bundled Alpine aarch64 capsule + `proot` offline runtime OR remote PC/Docker connect). |
| **Wear OS Smartwatch** | `MiniMuse-<version>-wear.apk` | Standalone wrist companion with Live Call, stay-awake voice prompts, pairing code, and wrist approvals — installable straight from the phone app (*Devices → Install to watch*) or via ADB/Wear Installer. |
| **iOS / iPadOS** | TestFlight beta | The iOS app builds through TestFlight (see [docs/ios.md](docs/ios.md)); it is not yet a public App Store download. |
| **Chrome Extension (Private)** | `MiniMuse-<version>-chrome-extension.zip` | Private unpacked MV3 extension (`Load unpacked` in `chrome://extensions` — never public). |
| **Docker / NAS** | `docker build -t minimuse:latest .` | Containerized server with writable `/app/venv` for in-place updates and optional browser (`WITH_BROWSER=1`). |
| **Python (Any OS)** | `nanomuse-<version>-py3-none-any.whl` | `pip install` or `pip install -e ".[browser,hands]"` -> `minimuse app` or `minimuse serve`. |
| **Any device (browser)** | PWA | Open the server URL on any phone, tablet, or desktop browser and *Add to Home Screen* for the full app experience. |

Verify any download with `sha256sum -c MiniMuse-<version>-SHA256SUMS`.

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## Building from Source

```bash
# 1. Frontend bundle (built into nanomuse/server/static)
cd web && npm install && npm run typecheck && npm run lint && npm test && npm run build && cd ..

# 2. Python checks & tests
pytest
ruff check .
mypy nanomuse

# 3. Chrome Extension unpacked zip
zip -r ../dist/MiniMuse-<version>-chrome-extension.zip chrome-extension -x "*.DS_Store"

# 4. Windows native desktop executable + proper installer
py -3.12 -m venv .venv-build && .venv-build/Scripts/pip install -e ".[browser,hands]" pywebview pyinstaller
.venv-build/Scripts/python -m PyInstaller --noconfirm --clean --distpath ../dist scripts/desktop-app/minimuse_onefile.spec
#    then the Add/Remove-Programs installer (Inno Setup 6):
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=<version> scripts/desktop-app/minimuse-installer.iss

# 5. Unified Android Phone & Wear OS APKs (in WSL with JDK 21 + Android SDK + NDK 27.2)
FORCE_SEAL=1 bash build-classic-apk.sh
cd android-classic && ./gradlew :app:assembleRelease :wear:assembleRelease
```

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## Troubleshooting

- **How do I use free Gemini keys AND DeepSeek credits AND OpenRouter together?** Open *Connections -> Chat model -> Multi-API Keys, Smart Credit Routing & Live Usage*. Paste your free Google AI Studio key (`GEMINI_API_KEY`), your DeepSeek key (`DEEPSEEK_API_KEY`), and your OpenRouter key (`OPENROUTER_API_KEY`). Choose what happens when each key runs out (e.g. *Use OpenRouter for this model*, *Switch to fallback model*, or *Pause this AI until quota resets*).
- **How do I set up a second device without typing everything again?** On the very first startup screen (or step 1 of setup), click **Sync from another device** and enter the 6-character pairing code from your first device (*Settings -> Data controls -> Pair device*) or paste a **Setup & Profile Transfer Bundle**.
- **Gmail won't accept normal password:** Google blocks standard passwords for IMAP/SMTP. Use **Connections -> Email -> Google Popup (App Password)** and paste the 16-letter App Password, OR add a Desktop Google OAuth Client ID under **Google OAuth Sign-In** for passwordless XOAUTH2 + API sync.
- **How do I install the Watch APK without USB?** Enable **ADB debugging** + **Wireless debugging** in your watch's Developer options and send `MiniMuse-<version>-wear.apk` from your phone with *Wear Installer 2*, OR pair your watch over Wi-Fi via ADB and click **Install to watch** in MiniMuse *Devices*.
- **Chrome extension says "not on Chrome Web Store":** That is intentional — MiniMuse keeps the Chrome Companion private and self-hosted. Unzip `MiniMuse-<version>-chrome-extension.zip`, open `chrome://extensions`, turn on **Developer mode**, and click **Load unpacked**.
- **Want zero external cloud traffic?** Go to *Settings -> MiniMuse Cloud -> Nothing (`local`)*, and use **Personal MiniMuse Cloud** (LAN/Tailscale/Cloudflare Tunnel) + **P2P Sync** between your own devices.

[↑ Back to Navigation](#navigation--quick-skip-links)

---

## Upstream & License

Everything above stands on [nano-muse/nanoMuse](https://github.com/nano-muse/nanoMuse) — an open-source, non-profit personal agent by the nano-muse contributors ([GPL-3.0-or-later](LICENSE)), with its [docs](docs/index.md), [paper](https://arxiv.org/abs/2610.08699), and [attribution notices](NOTICE). Bugs in fork-added features -> [MiniMuse Issues](https://github.com/leothefleo49/MiniMuse/issues).
