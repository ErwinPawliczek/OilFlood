# Agent A11: polish for demo (PLAN P7, task T17)
Repo: OilFlood. Read SYNC.md, docs/PLAN.md, docs/ARCHITECTURE.md, backend/README.md, web/README.md, data/README.md first. Follow SYNC.md rules: small commits, push often to YOUR branch `a11-polish`, never force-push, never commit secrets (.env stays gitignored) or large data. Deadline Sun Nov 22 12:00 PM PST, so ship within ~3 h.
Do NOT touch production port 5200. Test on port 5211 (web dev 5281). data/cache and data/raw are already symlinked to shared real data. Keep the single-panel UI. REAL DATA ONLY: no mocks.
Two other agents are working in parallel: A9 (branch a9-backtest) adds a "Backtest" panel section + /api/backtest; A10 (branch a10-response) adds a "Response outline" panel section + /api/spill/{id}/response. Do NOT build those features and keep your edits to web/src/panel.ts and main.ts small and local to avoid merge conflicts.

Goals:
1. Top-level README.md for Devpost judges: what OilFlood is (one paragraph), the live site https://oilflood.xyz, screenshots placeholder list, features, architecture summary, data sources with links and licences, the AI part (our ML drift correction + OpenOil baseline), and exact setup steps from a clean machine (conda env, data fetch commands, backend run, web build), verified by actually following them in a fresh temp clone where feasible (do not re-download huge data; document sizes).
2. Demo-ready default view: on first load open centered on Houston/Galveston with currents visible and a sensible default zoom; prediction mode default spill point near Galveston offshore.
3. Perf + bugs: look at the open notes in SYNC.md (Bugs, Blockers, e.g. the console error from the global currents layer after a map jump, the window.__extraLayers debug hook) and fix small ones. Check initial load size/time and big JSON responses (gzip if missing).
Update SYNC.md (Done log, Blockers; mark T17 done) when finished, commit and push. End with a short summary of what is ready to merge.
