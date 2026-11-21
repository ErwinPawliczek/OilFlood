# OilFlood backend

FastAPI + OpenDrift **OpenOil** on port **5200**. Implements the Step A API contract in `docs/ARCHITECTURE.md`.

## Setup (tested, Linux, miniforge3)
```bash
cd backend
~/miniforge3/bin/mamba env create -f environment.yml      # creates env "oilflood" (~3 min)
# update later: ~/miniforge3/bin/mamba env update -n oilflood -f environment.yml
```

## Run
```bash
cd backend
~/miniforge3/envs/oilflood/bin/uvicorn app.main:app --host 127.0.0.1 --port 5200
# second terminal:
~/miniforge3/envs/oilflood/bin/python smoke_test.py         # Texas City Y, 72 h, 2000 particles (~15 s)
```
If `web/dist` exists it is served at `/` (production). In dev, Vite (port 5273) proxies `/api` to 5200.

## Config (env vars)
| Var | Default | Meaning |
|---|---|---|
| `OILFLOOD_DATA_DIR` | `<repo>/data` | Where `cache/**/*.nc` forcing and `spills/catalog.json` live. Colon-separated list allowed, e.g. `/home/shmiggy/OilFlood-a2-data/data` to use A2's worktree. |
| `OILFLOOD_SNAP_KM` | `8` | Prediction seeds on a forcing-grid land cell move to the nearest model water within this distance, else 400 "on land". |
| `OILFLOOD_SNAP_KM_HINDCAST` | `30` | Same for catalog hindcasts (Texas City Y sits in Galveston Bay, which is land in HYCOM). |
| `OILFLOOD_WORKERS` | `4` | Parallel OpenOil runs (separate processes). |
| `OILFLOOD_RUNS_DIR` | `backend/runs` | Run cache (gitignored). |

Forcing discovery: every `*.nc` under `<data dir>/cache/` is inspected for CF standard names
(`x_sea_water_velocity`/`eastward_sea_water_velocity`, `x_wind`/`eastward_wind`, Stokes) plus time range and bbox
(0–360 longitudes OK). Files covering the spill start time are added as `reader_netCDF_CF_generic` readers.
**Real forcing only**: there is no constant/synthetic fallback. A run needs cached currents *and* winds covering its start
(else 400 with the cached windows listed); its duration is clipped to the forcing's end; particles leaving the forcing domain
are deactivated (`status_end: "no_data"`). Catalog oil names map to ADIOS oils (DWH -> `LIGHT LOUISIANNA SWEET, BP`).
Clicks on land / in bays the model treats as land get a 400 with a friendly message; pre-2001 catalog spills get a 400 (no reanalysis).
`GET /api/forcing` shows what the backend sees.

## Model settings
OpenOil, NOAA weathering, oil from ADIOS (`"IFO 380"` -> `IFO-380LS 2014`; any OpenOil oil name works), instantaneous
surface release in a 50 m radius, `wind_drift_factor` 0.02, Stokes drift on (from wave files if present, else
tabularised from wind), horizontal diffusivity 10 m²/s, vertical mixing, evaporation, emulsification, dispersion,
coastline stranding on the built-in GSHHG landmask. Time step 15 min, output every 30 min.

## API
- `POST /api/spill` `{lat, lon, volume_bbl?, volume_m3=500, oil_type, duration_h=72, start_time?, n_particles=2000, wind_drift_factor=0.02}`
  -> `{id, status, duration_h, requested_duration_h, start_time, volume_bbl, volume_m3, oil_type, seed, seed_moved_km, forecast, truncated, note?}`.
  **Prediction mode = live forecast only (T12):** the run uses `data/cache/forcing/live/{currents,winds}.nc` (written by
  `data/fetch_live.py`) and nothing else, starting now (UTC, floored to the hour). A `start_time` outside the live window
  is rejected (400) — use historical mode for past dates. If the forecast ends before now + `duration_h`, the run stops at
  the last forecast time: `truncated: true` and `note` say so. `volume_bbl` overrides `volume_m3` (1 bbl = 0.158987 m³).
  `oil_type` is an id from `GET /api/oils` (or an ADIOS name / free text). `forecast` = models, issued-at (run), valid window.
  `id` is a hash of the normalized request + forcing files; identical requests on the same forecast return the cached run.
- `GET /api/live` -> `{available, currents:{model, issued_at, valid_from, valid_until, source}, winds:{...}, valid_from, valid_until,
  hours_available, short_by_h, stale, note?}` (503 if no live forecast is cached).
- `GET /api/oils` -> `{default, oils:[{id, label, adios}]}` (oil types offered in the prediction panel).
- `GET /api/spill/{id}` -> `{id, status: queued|running|done|error, progress, error?}` + the same run summary as the POST.
- `GET /api/spill/{id}/tracks` -> 200 with `{times, particles:[{path:[[lon,lat]..], timestamps, status_end}], budget:[{t, surface, evaporated, dispersed, stranded}], meta}`; 202 + status while running; 409 on error.
  Budget is **kg of oil**. `dispersed` = naturally dispersed + submerged + biodegraded. Particle paths end when the particle strands/evaporates. ~9 MB raw / ~1.8 MB gzipped for 2000 particles x 72 h.
- `GET /api/spills` -> `data/spills/catalog.json` (A2).
- `GET /api/spills/{id}/footprints` -> `{id, footprints:[{t, name, geojson}]}` from `data/spills/<id>/footprints/*.geojson` (date parsed from file name).
- `GET /api/spills/{id}/tracks` -> hindcast run from catalog lat/lon/date_start/volume_bbl/oil_type; 202 + status until done, then tracks JSON.
- `GET /api/currents?start=&end=&bbox=` -> `{bbox, times:[unix_s]}` available in cached current NetCDF.
- `GET /api/currents/{t}?bbox=&step=0.1` (alias `GET /api/fields/currents?t=`) -> `{t, lon0, lat0, dlon, dlat, nx, ny, u:[], v:[]}`, row-major south->north, west->east, `null` on land.
- Global layer (T10, `data/fetch_global.py`; fields in `data/cache/fields/global/<window>/`). All additions are optional params, old calls behave as before:
  - `GET /api/currents?layer=global&start=&end=` -> `{layer:"global", bbox:[-180,-90,180,90], times, windows:[{window, source, t_start, t_end, n, dlon}], png:"/api/currents/global/{t}.png", vmax, gulf_box}`.
  - `GET /api/currents?layer=auto&...` -> the Gulf answer plus a `global` key. No `layer`: Gulf; if the Gulf NetCDF has no times for the window, the global times are returned instead (with the requested bbox).
  - `GET /api/currents/global/{t}.png` -> nearest global u/v texture within 12 h (header `X-Field-Time`). Encoding as in data/README.md.
  - `GET /api/currents/{t}?layer=global&bbox=&step=` -> JSON grid (same shape as the Gulf one) decoded from the PNG, capped at ~260k nodes. `layer=auto` -> global grid with the Gulf field pasted inside 18–31N 98–80W. No `layer` and no Gulf NetCDF for that time -> the global field over the requested bbox (default Gulf box).
- `GET /api/forcing`, `GET /api/health`.
