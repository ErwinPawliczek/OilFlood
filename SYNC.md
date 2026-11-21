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
| T10 | Global ocean currents layer (RTOFS live + HYCOM global historical), Gulf HYCOM on top | Erwin | done (agent A4, branch a4-global; ready to merge) |
| T11 | Real data only (remove mocks/fallbacks) + single draggable UI panel with date/time + days/sec row | Erwin | done (agent A5, branch a5-ui; includes a4-global merge) |
| T9 | Bug fixes: fix logged bugs (small fixes directly; ask in SYNC before big refactors) | Brother | todo |

## Agents (Erwin's tower)
Briefs in `agents/`. Each Claude agent works in its own worktree and branch and pushes there; Erwin's bot merges to main.

## Done log
- 2026-11-21 A5 (branch a5-ui, T11): **no mock/synthetic data anywhere**. Deleted web/src/mock.ts + geo.ts (fake currents, fake tracks, fake catalog, coarse land mask) and the backend constant-current/wind fallback (OILFLOOD_FORCING/CONST_* removed). Backend runs use only cached real NetCDF (needs currents+winds covering the start, duration clipped to forcing end, particles leaving the domain -> `no_data`); seeds on HYCOM land snap to nearest model water (8 km prediction, 30 km hindcast). Historical mode loads each spill's own HYCOM Gulf currents + A4's global layer for its dates; DWH shows 94 NESDIS outlines over time + hindcast. UI is ONE floating window (src/panel.ts): drag by header, position/collapse in localStorage, mode toggle + spill list/prediction status + clock (UTC + local) + play/scrub + rate "X days/sec"/"X hours/sec" with −/+ + "no data for this date" notes + budget + legend. `?spill=<id>` URL flag. Merged origin/a4-global into a5-ui (no conflicts) so main.ts carries both. Verified headless on :5201 (Texas City Y, DWH, prediction, land click).
- 2026-11-21 A4 (branch a4-global): **T10 global currents done.** `data/fetch_global.py`: `live` = NOAA RTOFS global latest 00z run (NOMADS, 0–72 h every 3 h, 0.25°, 25 PNGs, 27 MB), `--catalog` = HYCOM GOFS 3.1 global for each spill window (0.5°: dwh 168 fields/53 MB, texas_city_y 40/13 MB, mc209_2017 61/20 MB, dbl_152 66/21 MB, eugene_island_2009 51/17 MB, main_pass_2023 61/20 MB). No global data exists before 1994 (Ixtoc, Burmah Agate, Mega Borg). Output `data/cache/fields/global/<window>/` (index.json + `<t>.png` + `<t>.json` meta, same PNG encoding as the Gulf fields). Backend: `/api/currents` + `/api/currents/{t}` take `layer=gulf|global|auto` (default unchanged), plus `GET /api/currents/global/{t}.png`; if a Gulf window has no NetCDF, the old calls fall back to the global field. Web: `src/globalCurrents.ts` + a second `FlowField` under the Gulf one (masked inside 18–31N 98–80W when the Gulf has real data), time-synced with the timeline. Verified end to end on a test instance (:5204, this branch + a2 cache): live RTOFS over the whole globe (Americas, Indian Ocean/Asia, Pacific) and Texas City Y 2014 with HYCOM global + Gulf on top. 52 fps with the global layer vs 60 without (headless Chrome, RTX 3080 Ti). Commands/sizes in data/README.md.
- 2026-11-21 Erwin: merged a1/a2/a3 into main; claimed T11 (real data + one draggable panel), agent A5 on branch a5-ui.
- 2026-11-21 Erwin: claimed T10 (global currents, requested by brother), agent A4 on branch a4-global.
- 2026-11-21 Erwin: skeleton pushed.
- 2026-11-21 Erwin: docs/ARCHITECTURE.md (deck.gl + MapLibre frontend, FastAPI + OpenDrift + ML correction backend).
- 2026-11-21 Erwin: roles set (Erwin builds, brother tests + fixes bugs), claimed T2.
- 2026-11-21 Erwin: docs/RESEARCH.md oil spill modeling research
- 2026-11-21 A1 (branch a1-model): `backend/` FastAPI on :5200 with OpenDrift 1.14.11 OpenOil (conda env `oilflood`, see backend/README.md). Works: POST/GET `/api/spill`, `/api/spill/{id}/tracks` per contract (budget in kg; extra `meta` key), request-hash cache in backend/runs/, 4-process worker pool with progress, `/api/spills`, `/api/spills/{id}/footprints`, `/api/spills/{id}/tracks` (hindcast from catalog), `/api/currents` + `/api/currents/{t}` (u/v grid from cached NetCDF), `/api/forcing` debug, serves web/dist. `smoke_test.py` (Texas City Y, 2000 particles, 72 h) passes in ~5–10 s on constant fallback forcing. NetCDF reader path verified with synthetic HYCOM-shaped (0–360 lon, depth dim) and ERA5-shaped (`valid_time`, u10/v10 without standard_name, descending lat) files; datasets are normalized before OpenDrift sees them. Not yet tested with A2's real files. `meta.env_mean` in tracks shows the mean current/wind the particles actually saw (equals fallback values if no reader covered them).
- 2026-11-21 A2-data: started (claim T2 data work on branch a2-data)
- 2026-11-21 A3 (branch a3-globe): `web/` Vite+TS globe app (MapLibre v5 globe + deck.gl 9 interleaved), see web/README.md. Dev: `cd web && npm i && npm run dev` -> http://localhost:5273 (proxies /api to 5200); `npm run build` -> web/dist. Historical mode (pins, fly-to, observed outlines + hindcast particles, polls 202 hindcasts), prediction mode (click ocean -> POST /api/spill -> progress -> 72 h playback), windy-style current streaks colored by speed, time bar (play/pause/speed/scrub/UTC clock), oil budget panel. Verified end to end against the live backend (real NESDIS DWH outlines, real Texas City Y hindcast, real prediction runs, real live currents). Per-endpoint mock fallback (`?mock=1` forces it) with a red MOCK DATA badge naming which parts are synthetic. Gotcha: LineLayer/TextLayer/TripsLayer misrender on the MapLibre globe; used binary PathLayers + HTML marker labels instead.

- 2026-11-21 A2-data (branch a2-data): T2 forcing + catalog done. See data/README.md for commands, sources and formats. All forcing NetCDF opens in OpenDrift 1.14.11 `reader_netCDF_CF_generic` (`oilflood` env), and short OceanDrift test runs work on MC209, Texas City Y and DWH. Cached on the tower under `/home/shmiggy/OilFlood-a2-data/data/cache/` (gitignored):
  - `forcing/<window>/{currents,winds}.nc` (x/y_sea_water_velocity, x/y_wind, dims time/lat/lon, lon −180..180). Hindcasts use HYCOM-TSIS GOMb0.04 reanalysis currents + its 10 m winds: texas_city_y (2014-03-21..26 hourly, 83 MB), dwh (2010-04-20..07-16 6-hourly, 234 MB), mc209_2017 (3 h, 53 MB), dbl_152 (3 h, 46 MB), eugene_island_2009 (1 h, 95 MB), main_pass_2023 (3 h, 40 MB). live = ESPC-D-V02 currents 2026-11-21 12Z..11-24 12Z (3 h) + GFS 0.25° winds to 11-25 00Z (4 MB).
  - `fields/<window>/`: index.json + per-step `<unix>.json` (same shape as /api/currents/{t}) + `<unix>.png` u/v texture, 0.1°, 181×130.
  - Committed: `data/spills/catalog.json` (9 Gulf spills from NOAA IncidentNews; Ixtoc I, Burmah Agate and Mega Borg predate HYCOM, so `forcing: null`) and `data/spills/dwh/footprints/dwh_YYYYMMDD.geojson` (94 days of NOAA/NESDIS outlines, 2010-05-02..08-25, 3.7 MB).
  - To use: `OILFLOOD_DATA_DIR=/home/shmiggy/OilFlood-a2-data/data`, or copy `data/cache/` into the main checkout. Refresh live before the demo: `cd data && .venv/bin/python fetch_live.py --force && .venv/bin/python export_fields.py live --force`.

## Roles
- Erwin builds everything (T2–T7).
- Brother does testing and bug fixes (T8–T9).

## Data outline (brother)
See docs/DATA.md (received 2026-11-21 11:23 PT). Agent A2: prefer these sources; until ERA5/CMEMS accounts exist, use no-signup fallbacks.

## Bugs
Format: `- [open|fixed] short title — steps to reproduce — who found / who fixed`
- [fixed] DWH hindcast fails: `ValueError: Unknown oil type 'Louisiana light sweet crude (MC252)'` — select Deepwater Horizon in historical mode (or GET /api/spills/dwh/tracks, then poll the returned id) — catalog oil_type isn't an ADIOS/OpenOil name; map it (e.g. a generic light crude) — found by A3 / fixed by A5 (-> ADIOS `LIGHT LOUISIANNA SWEET, BP`; all catalog oils mapped)
- [fixed] Currents only exist for the live window: /api/currents returns no timesteps for historical spill dates (2010, 2014...), so the globe shows mock currents for past spills (labelled MOCK) — found by A3 / fixed by A5 (cache populated; frontend loads the spill's own window; mocks removed)
- [fixed] Prediction click on land (e.g. inland Mexico, outside the frontend's coarse mask) returns `Simulation stopped within first timestep`; the UI shows the error, but the backend could return a friendlier "seed point on land" — found by A3 / fixed by A5 (400 "That spot is on land" / "in a bay ... the model treats as land"; also fixed roaring_landmask call that always said water)

## Blockers
- A5 note: pre-2001 catalog spills (Ixtoc I, Burmah Agate, Mega Borg) have no reanalysis: the panel shows the record + "No current data" + a 400 hindcast message; nothing is drawn for them. Galveston Bay seeds stay a known limitation (Texas City Y strands ~97% within hours).
- A4: to show global currents on oilflood.xyz after merging, run `cd data && python fetch_global.py live --catalog` in the main checkout (or point `OILFLOOD_DATA_DIR` at a cache that has `cache/fields/global/`), rebuild web, restart the backend. Refresh live daily: `fetch_global.py live --force`.
- A4 note: deck.gl layers can look missing right after `map.jumpTo` in headless SwiftShader Chrome. That is a repaint artifact; with a real GPU they render fine at all longitudes.
- [resolved by A5] A1 constant fallback: backend now uses the real cached forcing (symlink data/cache to A2's cache, or OILFLOOD_DATA_DIR). Check GET /api/forcing.
- A2: Galveston Bay is land in HYCOM GOMb0.04 (~4 km). The Texas City Y point 29.363N 94.798W is NaN, and the nearest water is Bolivar Roads, about 29.33N 94.72W. Hindcast seeds may need a nudge there.
- A2: the live ESPC-D-V02 forecast ends 2026-11-24 12Z (~8 h short of now+72 h at fetch time). RTOFS is not used because NOMADS retired OPeNDAP and the full files are global and very large. Runs past the end fall back per A1's forcing logic.
- A2: the HYCOM THREDDS server sometimes hangs on a request. The scripts retry with a 5 min timeout and resume from raw chunks.
- A1 note: Texas City Y seed is inside Galveston Bay; with ~4 km HYCOM nearly all oil strands in the bay within hours (expected, see RESEARCH.md risks). Offshore clicks drift normally.
