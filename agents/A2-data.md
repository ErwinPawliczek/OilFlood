# Agent A2: ocean forcing data
Repo: OilFlood. Read SYNC.md, docs/ARCHITECTURE.md and docs/RESEARCH.md first. Follow SYNC.md rules (small commits, push often to YOUR branch `a2-data`, never force-push, never commit secrets or large data; data/cache/ is gitignored). Deadline Sun Nov 22 12:00 PM PST.

Goal: scripts in `data/` that download and cache Gulf of Mexico forcing as NetCDF that OpenDrift readers can open directly (reader_netCDF_CF_generic).
- Box: 18N–31N, 98W–80W.
- Currents: HYCOM GOMb0.04 reanalysis (or GLBy/ESPC) via THREDDS/OPeNDAP, surface layer, hourly or 3-hourly. Two windows: (1) hindcast 2014-03-21 to 2014-03-26 (Texas City Y), (2) a live/recent window: latest available RTOFS or HYCOM forecast for the next 72 h.
- Winds: 10 m u/v for the same windows (GFS via NOMADS/THREDDS for live, ERA5 needs signup so prefer NCEP reanalysis/GFS analysis or Open-Meteo archive for 2014).
- Optional stretch: export a downsampled u/v current field per timestep as JSON or PNG texture for the frontend flow layer (`data/cache/fields/`).
- Write `data/README.md` with exact commands, sizes, and which files land where. Make fetches idempotent and resumable. Verify each file opens with xarray and has CF standard names OpenDrift recognizes (x_sea_water_velocity, y_sea_water_velocity, x_wind, y_wind); rename variables if needed.
Report in SYNC.md what's cached and any blockers.
