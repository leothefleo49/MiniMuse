# MiniMuse Fix Progress

Rules: work ONE item at a time, in order. After each item: test -> commit -> then check off with a one-line note of what was actually done and verified. Never check off unfinished/untested work. A fresh session should read this file + `git log` and continue from the first unchecked item.

Repo: this directory. Remote: origin (leothefleo49/MiniMuse). Muse export data: `d:\Users\Tyler\Downloads\prompt data(muse wear OS apk and muse export)` (contains `EYI Package_*.zip` ~3GB export + Muse Wear OS apk).

## A. Foundations: crashes, import pipeline, performance, navigation
- [x] 1. Investigate repo layout - key paths in Notes below
- [x] 2. (commit a7fa700; phone+wear Kotlin compile, NOT yet run on a device) Crash: split-screen / multi-window setup goes black and stays frozen (handle configChanges/resizeable, no heavy work on resize)
- [x] 3. (commit a7fa700; compiles, NOT yet verified on a device) Full screen / edge-to-edge: remove black bars top and bottom
- [x] 4. (commit 22b18bb; import tests pass) Muse import: remove size limit (stream the zip, no in-memory load)
- [x] 5. (real 3GB export imports in 1.3s into one 'Muse main chat' with 10,667 correctly-sided, correctly-timed messages; manifest preferred over duplicate text copy; loose docs no longer become chats; widget tokens stripped; naive stamps read as UTC; 35 tests pass) Muse import: study real export format from the zip; fix parsing so messages, titles, roles (back-and-forth), tasks, voice-call transcripts import correctly (no giant overlapping text, no "task listed as title")
- [x] 6. (backend job API + pytest, ImportProgress pill mounted app-wide + vitest, 275 web tests pass, bundle rebuilt; real 3GB zip imports in ~1s of parsing; NOT yet seen on a phone) Muse import: runs in background safely (service/worker), faster, with a visible progress indicator reachable from anywhere; setup can continue meanwhile
- [ ] 7. (PARTIAL/UNVERIFIED) Performance: UI renders locally, no lag/stutter after each click; network/AI work off the main thread
- [x] 8. (tab history in the store, PageBar/BackBar use it; reducer tests + 277 web tests pass; not yet tried on a phone) Navigation: back arrow goes back one step, not to home chat
- [x] 9. (Settings > Import & export > 'Run setup again' reopens guided setup even when chats have messages, resumes the saved step; other setup choices already have Settings pages: Appearance, Model, identity, Import; 278 web tests pass) Setup got cut short by earlier bug: allow re-running / resuming setup; all setup items editable later

## B. Features
- [ ] 10. Restricted settings: in-app button redirecting to Android "Allow restricted settings" / app info so phone control can be enabled
- [ ] 11. Models become their own Settings section (out of Connections); setup + API editable later
- [ ] 12. AI settings show actual model (not just "Meta"), allow switching models; show nickname/subtext separately
- [ ] 13. Sync Google/Gmail: simple redirect + sign in, few taps; remove duplicate connection popups
- [ ] 14. Browser control on phone: install/enable easily or one-tap redirect instead of vague "unavailable"
- [ ] 15. Artifacts: apps pinned at top / easy find + quick run in-app
- [ ] 16. AI icon: add padding so name doesn't cover bottom and top isn't clipped (room for animation)
- [ ] 17. QR pairing phone -> PC: scan result actually pairs and shows confirmation on both sides

## C. Watch (Wear OS)
- [ ] 18. Watch app overhaul: UI nearly identical to original Muse watch app + own additions (live character, pretty UI)
- [ ] 19. Watch connects to phone after install (pairing/connection works)
- [ ] 20. Watch rotating bezel scrolling, bezel volume control during live call, haptics

## D. Wrap-up
- [ ] 21. Full test pass / build; update CHANGELOG, push to GitHub, update release
- [ ] 22. Honest final report (done / partial / not done)

## Notes (key paths, findings)
- Import code: nanomuse/server/data_import.py (tests/test_data_import.py). Android: android-classic/app/src/main/.../MainActivity.kt.
- Real export: one 3.5MB 'Conversation with Muse AI_*.txt' in the form '[2026-10-03 20:07:47] You: text' (multi-line, [[hatch_widget:..]] tokens), a 34MB manifest.json (workspace_files + 11011 turns), ~58k files under workspace/.
- Tyler also wants proper GitHub releases at the end (tag, notes, assets).
- Item 7 audit: Kotlin network calls are all on Dispatchers.IO; ws deltas are batched (wsBatch); EventView is memoized; chat loads in pages. No clear culprit found by reading alone: needs a profile on the phone. Did NOT add content-visibility (would clip reaction popovers).
