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
| T5 | Bare globe UI: historical mode (past spills + currents over time) + prediction mode (click-to-spill); response layer later | Erwin | done Step A (agent A3, branch a3-globe; response layer later) |
| T6 | Demo video (2–3 min) | Erwin | todo |
| T7 | Devpost write-up + README setup steps | Erwin | todo |
| T8 | Testing: follow README setup on a clean machine, test every feature, log bugs below | Brother | todo |
| T10 | Global ocean currents layer (RTOFS live + HYCOM global historical), Gulf HYCOM on top | Erwin | in progress (agent A4, branch a4-global) |
| T9 | Bug fixes: fix logged bugs (small fixes directly; ask in SYNC before big refactors) | Brother | todo |

## Agents (Erwin's tower)
Briefs in `agents/`. Each Claude agent works in its own worktree and branch and pushes there; Erwin's bot merges to main.

## Done log
- 2026-11-21 Erwin: claimed T10 (global currents, requested by brother), agent A4 on branch a4-global.
- 2026-11-21 Erwin: skeleton pushed.
- 2026-11-21 Erwin: docs/ARCHITECTURE.md (deck.gl + MapLibre frontend, FastAPI + OpenDrift + ML correction backend).
- 2026-11-21 Erwin: roles set (Erwin builds, brother tests + fixes bugs), claimed T2.
- 2026-11-21 Erwin: docs/RESEARCH.md oil spill modeling research
- 2026-11-21 A1 (branch a1-model): `backend/` FastAPI on :5200 with OpenDrift 1.14.11 OpenOil (conda env `oilflood`, see backend/README.md). Works: POST/GET `/api/spill`, `/api/spill/{id}/tracks` per contract (budget in kg; extra `meta` key), request-hash cache in backend/runs/, 4-process worker pool with progress, `/api/spills`, `/api/spills/{id}/footprints`, `/api/spills/{id}/tracks` (hindcast from catalog), `/api/currents` + `/api/currents/{t}` (u/v grid from cached NetCDF), `/api/forcing` debug, serves web/dist. `smoke_test.py` (Texas City Y, 2000 particles, 72 h) passes in ~5–10 s on constant fallback forcing. NetCDF reader path verified with synthetic HYCOM-shaped (0–360 lon, depth dim) and ERA5-shaped (`valid_time`, u10/v10 without standard_name, descending lat) files; datasets are normalized before OpenDrift sees them. Not yet tested with A2's real files. `meta.env_mean` in tracks shows the mean current/wind the particles actually saw (equals fallback values if no reader covered them).
- 2026-11-21 A2-data: started (claim T2 data work on branch a2-data)
- 2026-11-21 A3 (branch a3-globe): `web/` Vite+TS globe app (MapLibre v5 globe + deck.gl 9 interleaved), see web/README.md. Dev: `cd web && npm i && npm run dev` -> http://localhost:5273 (proxies /api to 5200); `npm run build` -> web/dist. Historical mode (pins, fly-to, observed outlines + hindcast particles, polls 202 hindcasts), prediction mode (click ocean -> POST /api/spill -> progress -> 72 h playback), windy-style current streaks colored by speed, time bar (play/pause/speed/scrub/UTC clock), oil budget panel. Verified end to end against the live backend (real NESDIS DWH outlines, real Texas City Y hindcast, real prediction runs, real live currents). Per-endpoint mock fallback (`?mock=1` forces it) with a red MOCK DATA badge naming which parts are synthetic. Gotcha: LineLayer/TextLayer/TripsLayer misrender on the MapLibre globe; used binary PathLayers + HTML marker labels instead.

## Roles
- Erwin builds everything (T2–T7).
- Brother does testing and bug fixes (T8–T9).

## Data outline (brother)
See docs/DATA.md (received 2026-11-21 11:23 PT). Agent A2: prefer these sources; until ERA5/CMEMS accounts exist, use no-signup fallbacks.

## Bugs
Format: `- [open|fixed] short title — steps to reproduce — who found / who fixed`
- [open] DWH hindcast fails: `ValueError: Unknown oil type 'Louisiana light sweet crude (MC252)'` — select Deepwater Horizon in historical mode (or GET /api/spills/dwh/tracks, then poll the returned id) — catalog oil_type isn't an ADIOS/OpenOil name; map it (e.g. a generic light crude) — found by A3
- [open] Currents only exist for the live window: /api/currents returns no timesteps for historical spill dates (2010, 2014...), so the globe shows mock currents for past spills (labelled MOCK) — found by A3
- [open] Prediction click on land (e.g. inland Mexico, outside the frontend's coarse mask) returns `Simulation stopped within first timestep`; the UI shows the error, but the backend could return a friendlier "seed point on land" — found by A3

## Blockers
- A1: no real forcing yet (A2's data/cache empty when tested). Backend runs on constant fallback (SW current 0.09 m/s, SE wind 5 m/s) until then. To use A2's cache: `OILFLOOD_DATA_DIR=/home/shmiggy/OilFlood-a2-data/data` (or after merge, just `data/`). Check `GET /api/forcing` to confirm files are detected; any CF NetCDF with x/eastward_sea_water_velocity or x/eastward_wind standard names works.
- A1 note: Texas City Y seed is inside Galveston Bay; with ~4 km HYCOM nearly all oil strands in the bay within hours (expected, see RESEARCH.md risks). Offshore clicks drift normally.
