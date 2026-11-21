# Agent A4: global ocean currents
Repo: OilFlood. Read SYNC.md, docs/ARCHITECTURE.md, docs/DATA.md and data/README.md first. Follow SYNC.md rules (small commits, push often to YOUR branch `a4-global`, never force-push, never commit secrets or large data; data/cache/ is gitignored). Deadline Sun Nov 22 12:00 PM PST.

Goal: real worldwide surface-current time series for the globe, with the existing high-res HYCOM Gulf fields kept on top.
- Live window: NOAA RTOFS global (no signup), from NOMADS https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/ or its THREDDS/OPeNDAP, latest run, surface u/v, every 3–6 h for 0–72 h.
- Historical: HYCOM global (GOFS 3.1 reanalysis/analysis, or ESPC-D-V02 at https://tds.hycom.org/thredds/catalog.html) surface u/v for each catalog spill's date window outside the Gulf box where available. Fallback: NOAA OSCAR. Copernicus GLORYS needs an account; skip it.
- Export a downsampled global field per timestep (~0.25°–0.5°, keep each file small, e.g. PNG u/v texture + JSON meta) to data/cache/fields/global/<window>/, same format as the existing Gulf fields so the frontend can reuse its streak layer.
- Backend: extend GET /api/currents (backward compatible) so the frontend can ask for global vs gulf and a time; serve the Gulf field inside 18N–31N 98W–80W and global elsewhere.
- Frontend (web/): render the global layer under the Gulf layer, time-synced with the timeline; keep the bare-globe look and performance smooth (cap texture size).
- Verify with real downloads: show at least one live RTOFS window covering the whole globe end to end on the running site. Document exact commands and sizes in data/README.md.
Report in SYNC.md (Done log, Blockers) when done.
