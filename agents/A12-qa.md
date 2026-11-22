# Agent A12: post-merge QA + honest ML default + README screenshots (task T18)
Repo: OilFlood. Read SYNC.md, docs/PLAN.md, README.md, docs/BACKTEST.md, docs/RESPONSE.md, docs/OURMODEL.md first. Follow SYNC.md rules: small commits, push often to YOUR branch `a12-qa`, never force-push, never commit secrets (.env stays gitignored) or large data. Deadline Sun Nov 22 12:00 PM PST; ship within ~3 h.
Do NOT touch production port 5200 or ~/OilFlood-runtime. Test on port 5212 (web dev 5282). data/cache and data/raw are symlinked to shared real data. Keep the single-panel UI. REAL DATA ONLY: no mocks, no fake numbers.
Main now has A9 (backtest), A10 (response outline) and A11 (polish) merged together for the first time (a9/a10 conflicts in web/src/main.ts + style.css were hand-resolved by keeping both sides).
Another agent, A13 (branch a13-devpost), only writes docs/DEVPOST.md and docs/DEMO.md; do not edit those files.

Goals:
1. QA the merged main end to end in headless Chromium on your port: historical mode (DWH, Texas City Y, Main Pass, Mega Borg), live prediction (demo spill off Galveston), Compare (ours vs OpenOil), Backtest section, Response outline section with shoreline markers, playback controls. Fix any bugs from the merge (duplicate/missing panel sections, toggles, CSS, console errors). Log anything you can't fix under Bugs in SYNC.md.
2. ML honesty (A9 finding): the backtest shows the ML drift correction gives no gain on DWH and strands 62 % at Main Pass. Make "ours" default to ml=false and show the ML option as a clearly labelled "experimental" toggle in the UI, with a one-line note pointing to the backtest. Keep backend API backward compatible. Update README.md's AI section to state the backtest result honestly (numbers from docs/BACKTEST.md only).
3. Take the README screenshots (docs/img/*.png placeholders listed in README.md) from your test server at 1280x800-ish, small PNGs (<400 KB each), and wire them into README.md.
Update SYNC.md (Done log, Bugs, Blockers; mark T18 done) when finished, commit and push. End with a short summary of what is ready to merge.
