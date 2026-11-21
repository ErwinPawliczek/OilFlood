# SYNC.md — OilFlood coordination

Both bots: `git pull` and read this before every task. Claim a task here and push the claim **before** starting it. Small commits, never force-push, never commit secrets (.env is gitignored).

**Deadline:** Devpost submission by **Sun Nov 22, 12:00 PM PST** (aim for 10:30 AM). All code must be written after Nov 21, 9:00 AM PST.

## Project
OilFlood: oil spill trajectory forecasting for first responders. Uses ocean current + wind data and tanker routes, plus an AI model (fine-tuned or pre-existing), backtested on historical spills, to output a response brief: where the oil goes over the next 24–72 h, which shorelines are threatened and when, and where to stage booms/skimmers.

Users: Coast Guard / NOAA-style responders and local emergency managers in the first hours of a spill.

## Tasks
| ID | Task | Owner | Status |
|----|------|-------|--------|
| T1 | Repo skeleton, README, SYNC.md | Erwin | done |
| T2 | Data: currents/wind source + historical spill set (e.g. NOAA) | Erwin | in progress (agent A2, branch a2-data) |
| T3 | Trajectory model (particle drift + AI correction) | Erwin | physics backend done (agent A1, branch a1-model); ML correction todo |
| T4 | Backtest on historical spills + metrics | Erwin | todo |
| T5 | Bare globe UI: historical mode (past spills + currents over time) + prediction mode (click-to-spill); response layer later | Erwin | in progress (agent A3, branch a3-globe) |
| T6 | Demo video (2–3 min) | Erwin | todo |
| T7 | Devpost write-up + README setup steps | Erwin | todo |
| T8 | Testing: follow README setup on a clean machine, test every feature, log bugs below | Brother | todo |
| T9 | Bug fixes: fix logged bugs (small fixes directly; ask in SYNC before big refactors) | Brother | todo |

## Agents (Erwin's tower)
Briefs in `agents/`. Each Claude agent works in its own worktree and branch and pushes there; Erwin's bot merges to main.

## Done log
- 2026-11-21 Erwin: skeleton pushed.
- 2026-11-21 Erwin: docs/ARCHITECTURE.md (deck.gl + MapLibre frontend, FastAPI + OpenDrift + ML correction backend).
- 2026-11-21 Erwin: roles set (Erwin builds, brother tests + fixes bugs), claimed T2.
- 2026-11-21 Erwin: docs/RESEARCH.md oil spill modeling research
- 2026-11-21 A1 (branch a1-model): `backend/` FastAPI on :5200 with OpenDrift 1.14.11 OpenOil (conda env `oilflood`, see backend/README.md). Works: POST/GET `/api/spill`, `/api/spill/{id}/tracks` per contract (budget in kg; extra `meta` key), request-hash cache in backend/runs/, 4-process worker pool with progress, `/api/spills`, `/api/spills/{id}/footprints`, `/api/spills/{id}/tracks` (hindcast from catalog), `/api/currents` + `/api/currents/{t}` (u/v grid from cached NetCDF), `/api/forcing` debug, serves web/dist. `smoke_test.py` (Texas City Y, 2000 particles, 72 h) passes in ~5–10 s on constant fallback forcing. NetCDF reader path verified with synthetic HYCOM-shaped files (0–360 lon, depth dim), not yet with A2's real files.

## Roles
- Erwin builds everything (T2–T7).
- Brother does testing and bug fixes (T8–T9).

## Data outline (brother)
_Waiting on brother's data outline; paste it here or in docs/DATA.md._

## Bugs
Format: `- [open|fixed] short title — steps to reproduce — who found / who fixed`
_None yet._

## Blockers
- A1: no real forcing yet (A2's data/cache empty when tested). Backend runs on constant fallback (SW current 0.09 m/s, SE wind 5 m/s) until then. To use A2's cache: `OILFLOOD_DATA_DIR=/home/shmiggy/OilFlood-a2-data/data` (or after merge, just `data/`). Check `GET /api/forcing` to confirm files are detected; any CF NetCDF with x/eastward_sea_water_velocity or x/eastward_wind standard names works.
- A1 note: Texas City Y seed is inside Galveston Bay; with ~4 km HYCOM nearly all oil strands in the bay within hours (expected, see RESEARCH.md risks). Offshore clicks drift normally.
