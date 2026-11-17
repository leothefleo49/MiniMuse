# MiniMuse upgrade plan

Working checklist for the v1.2.0 upgrade. Items are checked only after they were
built and verified (tests, typecheck, build or a manual run). Each major area is
committed separately. Anything that could not be verified stays unchecked and is
called out in the notes at the bottom.

Legend: `[x]` done and verified here · `[~]` implemented and passes tests/compile, but could not be
run on the real target (phone, watch, real browser), so it still needs a hands-on check · `[ ]` not done.

## 1. Crashes and navigation
- [x] Devices screen no longer crashes with `Cannot read properties of undefined (reading 'toLowerCase')` (regression tests for the exact crash shapes: `DevicesScreen.test.ts`)
- [~] Mobile: tapping **Devices** in the side menu opens it on the first tap (the drawer closes first and the screen switches without a view transition; Kotlin and TS compile; not run on a phone)
- [~] Mobile compass/location button asks for location permission first instead of failing with "User denied geolocation" (new `onGeolocationPermissionsShowPrompt`; app compiles; not run on a phone)
- [~] Android WebView grants geolocation to the page after the OS permission is granted (same change; microphone permission added to the manifest and the WebView prompt)

## 1b. Watch auto-detect and auto-install (priority fix)
Root causes found on the owner's PC (a multi-homed Windows machine: Tailscale, WSL and Wi-Fi), each
reproduced against the real phone on the LAN before fixing:
1. the raw mDNS fallback never chose a network interface (Windows sent the query out of Tailscale);
2. it never asked for unicast replies;
3. it never listened on the mDNS multicast group, and an Android phone answers by multicast
   (measured: 0 unicast packets, the ADB service found 3 of 3 times on the group);
4. the "remember the watch" file stored `ip:port`, which changes whenever Wireless debugging restarts,
   and the background update ran once, 12 s after start-up, so it silently stopped matching.
- [x] mDNS discovery queries every LAN interface, asks for unicast replies and listens on the multicast group (live: finds the real phone's ADB service at 192.168.1.30:35347 where the old code found nothing; 15 unit tests)
- [x] A found service whose A record is missing still resolves (falls back to the packet's sender address); PTR-only replies are resolved with a second question
- [~] The pairing screen needs only the 6-digit code: host:port of the watch's pairing service comes from discovery (server flow tested with a stateful fake `adb`; the card's logic is unit tested; the card itself was not rendered in a browser)
- [x] After pairing, the watch's connect service is picked up automatically (retry window), classified as a watch, and the APK is installed without more input (fake-adb flow test)
- [x] Legacy "Debug over Wi-Fi" (port 5555) watches on the LAN are found and connected too (fake-adb flow test)
- [x] A paired watch is detected and updated in the background on its own, by serial, even when its port or address changes (`_autodetect_once` tested with a fake adb; the real periodic loop was run live on this LAN: it ran on schedule, refused the phone, reported "not reachable" through `/api/watch/devices`, installed nothing)
- [x] The install endpoint returns a machine-readable reason (no watch, phone only, needs pairing, pairing code needed/wrong, unauthorized, ADB missing, APK missing, install failed)
- [~] The card shows an obvious popup for each reason and auto-installs when a watch appears (`NoticeDialog`, `watchInstall.ts`; tested as pure logic, not rendered in a browser)
- [x] The Wear APK is never installed onto a phone (live: the real endpoint refused the real Z Flip7 with `phone_only`, and `pm list packages` on it shows nothing installed)
- [ ] End-to-end install onto a real watch (**no watch is reachable from this PC: it is not on the Wi-Fi and Wireless debugging is not advertising; no emulator available**)

## 2. Setup (onboarding)
- [~] "Transfer Muse data" in setup opens a real transfer area: a *From Muse* tab in the setup card with the Muse prompt, .zip/file import and paste (the importer's setup mode is tested; the tab itself was not rendered in a browser)
- [x] Setup asks what the AI should call you, and the first chat no longer asks again: setup already had the field, but the first conversation always began by asking; now it greets you by name and goes straight to naming the AI (5 new tests through the real API)
- [~] Setup has a permissions step right after the welcome that asks for the basics up front (notifications, install updates, find devices on the Wi-Fi) with one "Allow the basics" button; the rest are in Settings (the step, the permission model and the setup flow are tested; Android's real dialogs and the page itself were not run on a phone or in a browser)
- [~] The auto phone-app-update permission is no longer hidden in watch settings: the button is gone from the watch card and it is now the row **Install updates by itself** in Permissions, with a one-sentence reason and an Allow button (model unit tested; not rendered on a phone)
- [~] AI "subtext" from setup shows up: the style notes already went to the model; the *tagline* was only visible on the small SOUL card because the line under the name is deliberately blank at rest. It now shows under the name in the chat header and in the chats drawer (compiles and the web suite passes; not looked at in a browser)

## 3. Wide screens (Windows / PC)
- [ ] Setup uses the width on wide screens (two-column layout, no tiny centered phone column)
- [ ] Main app uses wide layouts on desktop (content max-widths, multi-column grids for settings, devices, calendar, map)

## 4. Modes and MiniMuse Cloud
- [ ] "Switch mode" no longer restarts the app silently; it shows a clear confirmation explaining what happens
- [ ] The app always runs locally, with cloud/sync as an add-on (local + cloud at the same time); disconnects resync later
- [ ] Cloud branding is "MiniMuse Cloud" everywhere a user sees it, with notes on self-hosting it for free

## 5. Pairing devices that both have data
- [ ] When pairing two devices that both have data, a dialog offers: keep this device, keep the other device, or merge data and keep settings from one (default)
- [ ] Backend merge honours the choice (no duplicated chats, settings taken from the chosen side)

## 6. Map
- [ ] In-app interactive map (OpenStreetMap tiles, no key) inside the Location tab
- [ ] Shows current location, saved/favorite places, and lets you add a place by tapping the map
- [ ] Shows common travels / most-visited places, most-driven routes and roads, and trip statistics from the travel log
- [ ] Works on phone and wide screens

## 7. Calendar
- [ ] Calendar is reachable directly inside the app (visible in navigation by default, not only behind a toggle)

## 8. Simple API connections
- [ ] Setup and Settings show only simple connections (sign in, or one/two clicks), with a clear "Link" action per service
- [ ] Advanced/custom API fields are tucked away, not in the main flow
- [ ] A clear note says the AI can build and sync any API for you once the app is running

## 9. Clear popups for important state changes
- [ ] A global notice/popup system (center modal for blocking issues, toast for info)
- [ ] Watch install with no watch detected shows an obvious popup
- [ ] Other key actions (pairing, update, connect/disconnect, sync errors) use the popup system

## 10. Safety vs. Permissions
- [~] **Safety** section: the Always / Ask first / Never rules moved here from Permissions, with 8 new categories mapped to real tools (run commands, send email = message-sending rule, browser, control the screen, saved passwords, install software = app-install rule, share location, act on other devices), plus links to quiet hours and usage limits and an **Emergency stop**. Server enforcement is tested (`test_safety_rules.py`, 8 tests); the page itself was not rendered in a browser. Two existing bugs found and fixed on the way: a saved "Never" stopped being enforced after a restart (proved on the original v1.1.0), and touching any rule silently removed `shell` from "always ask". Phone-side rows are still enforced by the phone app, not the server.
- [~] **Permissions** section: real system permissions with status and Allow buttons: notifications, install updates, nearby devices, location, microphone, camera, calendar, contacts, background running, show over apps, exact alarms, control this phone (Android: the bridge now reports and requests them; Kotlin compiles; the list is unit tested). Browser/desktop: notifications, location, microphone, camera. Not covered: SMS, phone calls, files, usage access, notification access, Bluetooth.
- [~] Full phone control guide: Android's accessibility service is the mechanism the app really uses; the guide lists the steps including "Allow restricted settings". Developer options / Wireless debugging are explained for the watch (Devices). Controlling a phone from a PC over ADB is **not built**, so it is not described.

## 11. Proactivity
- [ ] Proactivity has an **Auto (adaptive)** mode that adjusts how often the AI reaches out based on your responses

## 12. Muse transfer: continuous sync and merge
- [ ] Muse transfer offers a recurring update prompt with a chosen interval (e.g. daily, weekly, custom)
- [ ] Re-importing the same account does not duplicate chats, memories or settings
- [ ] Overlapping chats can be merged by time (interleaved) or kept as separate sections in one chat
- [ ] The merge style can be set per chat in the chat's 3-dot menu

## 13. Watch app (Wear OS)
- [ ] Watch setup text explains how to enable Developer options on the watch (tap Build number 7 times) before wireless debugging
- [ ] Muse-style home: large animated 3D-feeling avatar that turns and reacts (idle, listening, thinking, speaking)
- [ ] Chat, call and mic buttons along the bottom
- [ ] After a period of no interaction the screen shows only the avatar
- [ ] Scrolling up shrinks the avatar smoothly
- [ ] Swipe left-to-right opens chats / side menu; right-to-left opens settings
- [ ] Rotating bezel / crown scrolls
- [ ] An Apps page (before settings) lists apps the AI made, with watch versions

## 13b. Make MiniMuse the default assistant on every device
- [ ] Phone (Android): MiniMuse is offered under *Default digital assistant app* (voice-interaction service + ASSIST/VOICE_COMMAND entry points), with a one-tap button in Settings that opens the right system screen and shows whether it is already the default
- [ ] Watch (Wear OS): the watch app declares the same assistant entry points so it can be chosen as the watch's assistant, with a button/guide in the watch settings
- [ ] PC (Windows): a global summon hotkey (configurable) and a "start with Windows" switch in Settings; the Copilot key can be mapped to it
- [ ] Settings has one "Default assistant" section that shows per-device status (phone / watch / PC) and what to tap on each
- [ ] Honest limits written down: what the OS lets an app do on each platform (e.g. Windows has no third-party default-assistant slot)

## 14. Release
- [ ] All checks pass (Python tests, web typecheck/tests/build, Android unit tests where buildable)
- [ ] Version bumped, CHANGELOG and release notes written
- [ ] Artifacts built (Windows, APK, Wear APK, wheel, Linux where possible)
- [ ] Pushed to GitHub and release published

## Notes (filled in as work progresses)
