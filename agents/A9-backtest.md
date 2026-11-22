# Agent A9: backtest our model vs OpenOil vs observed (PLAN P5, task T15)
Repo: OilFlood. Read SYNC.md, docs/PLAN.md, docs/ARCHITECTURE.md, docs/OURMODEL.md first. Follow SYNC.md rules: small commits, push often to YOUR branch `a9-backtest`, never force-push, never commit secrets (.env stays gitignored) or large data. Deadline Sun Nov 22 12:00 PM PST, so ship something working within ~3 h.
Do NOT touch production port 5200. Test on port 5209 (web dev 5279). data/cache and data/raw are already symlinked to shared real data. Keep the single-panel UI. REAL DATA ONLY: no mocks, no invented scores.

Goal: score both models against observations.
- Cases: DWH (94 NESDIS daily outlines in data/spills/dwh/footprints, hindcast forcing in cache/forcing/dwh), Texas City Y 2014 (use whatever observed footprint/landfall info the catalog/IncidentNews give; if no polygon exists, score landfall location/timing only and say so), and Main Pass 2023 if feasible (observed: moved SW, no shoreline impact).
- Models: OpenOil, ours with ML correction (ml=true), ours without ML (ml=false). A8 found the ML can hurt at Main Pass, so report both honestly.
- Metrics: Liu–Weisberg skill score and centroid separation distance at 24/48/72 h (for DWH, seed from an observed outline and compare to the outline 1/2/3 days later, several start dates; also polygon overlap like IoU/FSS if easy).
- Script: backend/app/backtest/ (or scripts/backtest.py), reproducible with one command; cache results as small JSON in data/backtest/ (small, committable).
- Output docs/BACKTEST.md with tables, method, limitations. Add a small collapsible "Backtest" section in the one panel (historical mode) showing the scores for the selected spill. API: GET /api/backtest.
Update SYNC.md (Done log, Blockers; mark T15 done) when finished, commit and push. End with a short summary of what is ready to merge.
