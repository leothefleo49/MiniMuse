# MiniMuse Fix Progress

Rules: work ONE item at a time, in order. After each item: test -> commit -> then check off with a one-line note of what was actually done and verified. Never check off unfinished/untested work. A fresh session should read this file + `git log` and continue from the first unchecked item.

Repo: this directory. Remote: origin (leothefleo49/MiniMuse). Muse export data: `d:\Users\Tyler\Downloads\prompt data(muse wear OS apk and muse export)` (contains `EYI Package_*.zip` ~3GB export + Muse Wear OS apk).

## A. Foundations: crashes, import pipeline, performance, navigation
- [ ] 1. Investigate repo layout (android app, watch app, import code) and note key file paths in "Notes" below
- [ ] 2. Crash: split-screen / multi-window setup goes black and stays frozen (handle configChanges/resizeable, no heavy work on resize)
- [ ] 3. Full screen / edge-to-edge: remove black bars top and bottom
- [ ] 4. Muse import: remove size limit (stream the zip, no in-memory load)
- [ ] 5. Muse import: study real export format from the zip; fix parsing so messages, titles, roles (back-and-forth), tasks, voice-call transcripts import correctly (no giant overlapping text, no "task listed as title")
- [ ] 6. Muse import: runs in background safely (service/worker), faster, with a visible progress indicator reachable from anywhere; setup can continue meanwhile
- [ ] 7. Performance: UI renders locally, no lag/stutter after each click; network/AI work off the main thread
- [ ] 8. Navigation: back arrow goes back one step, not to home chat
- [ ] 9. Setup got cut short by earlier bug: allow re-running / resuming setup; all setup items editable later

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
(none yet)
