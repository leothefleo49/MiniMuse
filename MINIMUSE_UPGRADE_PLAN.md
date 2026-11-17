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
- [x] Setup uses the width on wide screens. Measured in a real browser at 1920x1080: the entry screen was already two columns; the setup shell was 1080 px wide and the Appearance step a single column 1,390 px tall (scrolling in a 946 px window). Now the shell is 1280 px and Appearance is two columns of 592 px, 821 px tall, **no scrolling**. Phone width unchanged (the new rules only apply from 1024 px).
- [~] Main app uses wide layouts on desktop: the map tab is two columns (map + stats) at wide widths, Settings already had a wide layout. Calendar, Devices and the other screens were not re-audited.

## 4. Modes and MiniMuse Cloud
- [~] "Switch mode" no longer restarts the app silently: all three places that trigger it (Devices, and two in Settings) now ask first and say what happens (it drops the connection, stops the phone's background runtime and notifications, returns to the start screen, deletes nothing). The wiring is tested (disconnect fires only after confirming); not run on a phone.
- [ ] **Not done:** "always run locally, with cloud/sync as an add-on at the same time". Today the phone app is either local or connected to a computer; running both at once and resyncing later is an architecture change I did not attempt.
- [~] Cloud branding: the web UI already says "MiniMuse Cloud" everywhere a person reads it (no "nanoMuse Cloud" in any screen). **Not done:** a free, self-hosted MiniMuse Cloud of your own; the account service behind it is unchanged.

## 5. Pairing devices that both have data
- [ ] When pairing two devices that both have data, a dialog offers: keep this device, keep the other device, or merge data and keep settings from one (default)
- [ ] Backend merge honours the choice (no duplicated chats, settings taken from the chosen side)

## 6. Map
- [x] In-app interactive map (OpenStreetMap tiles, no key) as the first tab of Location. Verified in a real browser: container renders, 12 of 12 tiles loaded, pins, attribution.
- [x] Shows saved/favorite places (amber star pins) and lets you add a place by tapping the map (tap opens "New place here" with the coordinates and a best-effort street name; saving put a 5th pin on the map). [~] "My location" (current position) is built but needs a granted location permission, which was not exercised.
- [~] Most-visited places, common routes (A to B, with trips, miles, minutes, mph) and trip statistics (trips, miles, average and top speed) are shown and checked against seeded data. **Most-driven roads are not possible**: a trip stores where it started and ended, not the road; road stats need tracked trips, which are not recorded.
- [x] Works on phone and wide screens (400 px: map 368x320, 4 tiles, no sideways scroll; 1920 px: two columns). Also found and fixed on the way: GPS pings were stored as ordinary trips, inflating "trips" (9 instead of 5) and creating a fake "GPS Snapshot" route (server fix + a rule for already-saved samples).

## 7. Calendar
- [x] Calendar is reachable directly inside the app. On desktop it was already in the sidebar; on a phone there was **no way in** (the drawer only listed Devices, Coding agents, Trash). The drawer now has Calendar and Map & places rows (on by default, following the same on/off settings). Verified in a real browser at 400 px.

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
- [x] Proactivity has an **Auto (adaptive)** mode. A check-in that actually reached you (a goal pass whose reply was not quiet) is noted; replying within 6 hours marks it answered; the recent answer rate sets the interval (x0.5 / x1 / x2 / x4) and how readily a pass speaks up. No evidence yet means it behaves like Default. 20 tests, including through a real goal pass with a scripted model. The Settings dial has the fifth option (typecheck and suite pass; the dial was not rendered in a browser).

## 12. Muse transfer: continuous sync and merge
- [ ] Muse transfer offers a recurring update prompt with a chosen interval (e.g. daily, weekly, custom)
- [ ] Re-importing the same account does not duplicate chats, memories or settings
- [ ] Overlapping chats can be merged by time (interleaved) or kept as separate sections in one chat
- [ ] The merge style can be set per chat in the chat's 3-dot menu

## 13. Watch app (Wear OS)
- [x] Watch setup text explains how to enable Developer options on the watch (tap Build number 7 times) before wireless debugging: the "no watch found" and "only a phone" popups, the card's guide, and the two messages that used to skip it all say so now (tests pass).
- [ ] Muse-style home: large animated 3D-feeling avatar that turns and reacts (idle, listening, thinking, speaking)
- [ ] Chat, call and mic buttons along the bottom
- [ ] After a period of no interaction the screen shows only the avatar
- [ ] Scrolling up shrinks the avatar smoothly
- [ ] Swipe left-to-right opens chats / side menu; right-to-left opens settings
- [ ] Rotating bezel / crown scrolls
- [ ] An Apps page (before settings) lists apps the AI made, with watch versions

## 13b. Make MiniMuse the default assistant on every device
- [~] Phone (Android): an `ASSIST` / `VOICE_COMMAND` activity plus a voice-interaction service (the pair Android's own role definition requires), so MiniMuse can be listed under *Default digital assistant app*; holding home or power then opens the main chat. Settings has a **Choose MiniMuse** button that opens Android's page and a status that rereads when you come back. Compiles, and the merged manifest was checked for every declaration. **Not run on a phone**: I cannot confirm Android actually lists it.
- [~] Watch (Wear OS): the same two components in the watch app (compiles, manifest checked). Not run on a watch; the card tells the person where to look in the watch's settings, without claiming an exact menu path.
- [ ] **Not done:** PC (Windows) summon shortcut and "start with Windows". Windows has no slot for a third-party default assistant, and the shortcut would live in the desktop shell, which I could not build and test here.
- [~] Settings has one **Default assistant** section: the phone's status is read for real; the watch and the computer are instructions and an honest note, not live status.
- [x] Limits written down: the card says Windows does not let an app replace its built-in assistant, and that each device lets you choose only in its own settings.

## 14. Release
- [~] Checks: web typecheck, eslint, 273 tests and the production build pass; Python full suite 1628 pass, 54 fail, and all 54 also fail on the untouched v1.1.0 (no new failures). Android: compile only.
- [x] Version bumped to 1.2.0 (Python, web, extension, citation, rebrand script), CHANGELOG and in-app release notes written. Android versionCode/versionName were NOT bumped: the Android apps were not rebuilt.
- [~] Built: wheel and sdist only. **Not built:** Windows installer, Linux build, phone APK, watch APK (need PyInstaller/WSL and the release signing key, which are not available here).
- [x] Pushed to GitHub; v1.2.0 published as a PRE-release (not "Latest") so installed apps do not auto-update to an unverified build.

## Notes

**Legend:** `[x]` verified here, `[~]` implemented and tested but not run on the real target (a phone, a watch), `[ ]` not done.

### The watch
- The watch address, port and pairing code are not in any chat. The one pairing code anywhere (Nov 8) was for the phone and has expired. The `192.168.1.50:41234` style values in tests are fixtures.
- No watch is reachable from this PC. The only device on the network is the phone. So an install onto a real watch was **not** done. What was proven live: the real endpoint refused the real phone (`phone_only`), nothing was installed on it.
- Why auto-detection failed, four causes, each reproduced: the mDNS query left through the wrong network interface (Tailscale); it did not ask for unicast replies; it never listened on the multicast group, where Android answers; and the remembered `ip:port` goes stale, with the background update running once.

### Bugs found on the way (not on the original list)
- A saved "Never" approval rule stopped being enforced after a restart (reproduced on the untouched v1.1.0).
- Changing any approval rule removed `shell` from "always ask".
- GPS pings were stored as manual trips, inflating trip counts and inventing a "GPS Snapshot" route.
- On a phone there was no way to reach Calendar or Location.

### Tests
- Web: 40 files / 273 tests, tsc and eslint clean.
- Python: 55 tests fail in the full suite; all 55 also fail on the untouched v1.1.0 (checked in a separate worktree), so they are not regressions from this work. They were not fixed.
- Android: phone and watch modules compile; merged manifests checked. No Android unit/instrumented tests were run on a device.

### Not done
Local+cloud at the same time and a free self-hosted MiniMuse Cloud (4); the pairing "keep / keep other / merge" dialog and merge backend (5); simple API-linking redesign (8); a global popup system beyond the watch popups (9); recurring Muse transfer, dedupe and merge styles (12); the whole watch-app redesign (13); the PC summon shortcut (13b); most-driven roads (6, no data); a real-watch install (1b); rebuilt APKs, Windows and Linux installers (14).
