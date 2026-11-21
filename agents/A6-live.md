# Agent A6: live prediction pipeline (PLAN P2)
Repo: OilFlood. Read SYNC.md, docs/PLAN.md, docs/ARCHITECTURE.md, data/README.md first. Follow SYNC.md rules (small commits, push often to YOUR branch `a6-live`, never force-push, never commit secrets or large data; data/cache/ is gitignored). Deadline Sun Nov 22 12:00 PM PST.
Do not touch production port 5200. Test on 5202. data/cache and data/raw are already symlinked.

Goal: prediction mode always uses LIVE forecast forcing, not historical/reanalysis.
- Auto-fetch the latest forecast currents (HYCOM ESPC-D-V02 or equivalent live window already started by A2; add RTOFS only if you can do it without huge full-globe files) and GFS 10 m winds for 0–72 h. Idempotent script `data/fetch_live.py` (or extend existing fetch scripts).
- Install a systemd user timer or cron on this machine that refreshes the live window a few times a day (`fetch_live` + `fetch_global.py live --force` if present). Document the unit/cron in data/README.md; do not break existing timers.
- Backend: prediction (POST /api/spill) must run on the latest live forecast files only. Let the user set oil amount (bbl) and oil type. If the live forecast is shorter than 72 h, run to the last available time and say so clearly in the API and UI (A2: ESPC-D-V02 ended ~8 h short of now+72 h).
- Frontend: in the single draggable panel, prediction mode has amount + type controls and shows the forecast issued-at / valid-until. Keep the one-panel UI. No mocks.
Update SYNC.md (Done log, Blockers) when done.
