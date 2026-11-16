# MiniMuse upgrade plan

Working checklist for the v1.2.0 upgrade. Items are checked only after they were
built and verified (tests, typecheck, build or a manual run). Each major area is
committed separately. Anything that could not be verified stays unchecked and is
called out in the notes at the bottom.

## 1. Crashes and navigation
- [ ] Devices screen no longer crashes with `Cannot read properties of undefined (reading 'toLowerCase')`
- [ ] Mobile: tapping **Devices** in the side menu opens it on the first tap (no second tap on the menu button)
- [ ] Mobile compass/location button asks for location permission first instead of failing with "User denied geolocation"
- [ ] Android WebView grants geolocation to the page after the OS permission is granted

## 2. Setup (onboarding)
- [ ] "Transfer Muse data" in setup opens a real transfer area (upload export, paste, or copy the Muse prompt)
- [ ] Setup asks what the AI should call you (your name) so the first chat does not have to ask
- [ ] Setup has a permissions step that asks for the base permissions up front: notifications, auto self-update, location, microphone, nearby devices/connections, install updates (Android)
- [ ] The auto phone-app-update permission is no longer hidden in watch settings; it lives in Permissions with a clear label and a real button
- [ ] AI "subtext" (personality notes) from setup is actually sent to the model and visible/editable afterwards

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
- [ ] **Safety** section: when the AI may act with or without asking (per category), plus more safety options (quiet hours, spending limit, message-sending rules, app-install rules, location sharing, emergency stop)
- [ ] **Permissions** section: real OS/hardware permissions with status and grant buttons (notifications, location, camera, mic, contacts, calendar, SMS, phone, files, accessibility, overlay, install apps, battery optimisation, usage access, notification access, Bluetooth/nearby, exact alarms)
- [ ] Wireless debugging / full phone control guide with simple steps (enable developer options, pair, connect)

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

## 14. Release
- [ ] All checks pass (Python tests, web typecheck/tests/build, Android unit tests where buildable)
- [ ] Version bumped, CHANGELOG and release notes written
- [ ] Artifacts built (Windows, APK, Wear APK, wheel, Linux where possible)
- [ ] Pushed to GitHub and release published

## Notes (filled in as work progresses)
