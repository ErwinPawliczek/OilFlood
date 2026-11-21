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
| `OILFLOOD_FORCING` | `auto` | `auto`: cached NetCDF covering the spill + constant fallback elsewhere. `cache`: NetCDF only (400 if none). `constant`: constant forcing only. |
| `OILFLOOD_CONST_CURRENT_U/V` | `-0.08 / -0.05` m/s | Fallback current (weak SW down-coast). |
| `OILFLOOD_CONST_WIND_U/V` | `-3 / 4` m/s | Fallback 10 m wind (SE onshore breeze). |
| `OILFLOOD_WORKERS` | `4` | Parallel OpenOil runs (separate processes). |
| `OILFLOOD_RUNS_DIR` | `backend/runs` | Run cache (gitignored). |

Forcing discovery: every `*.nc` under `<data dir>/cache/` is inspected for CF standard names
(`x_sea_water_velocity`/`eastward_sea_water_velocity`, `x_wind`/`eastward_wind`, Stokes) plus time range and bbox
(0–360 longitudes OK). Files covering the spill start time are added as `reader_netCDF_CF_generic` readers.
`GET /api/forcing` shows what the backend sees.

## Model settings
OpenOil, NOAA weathering, oil from ADIOS (`"IFO 380"` -> `IFO-380LS 2014`; any OpenOil oil name works), instantaneous
surface release in a 50 m radius, `wind_drift_factor` 0.02, Stokes drift on (from wave files if present, else
tabularised from wind), horizontal diffusivity 10 m²/s, vertical mixing, evaporation, emulsification, dispersion,
coastline stranding on the built-in GSHHG landmask. Time step 15 min, output every 30 min.

## API
- `POST /api/spill` `{lat, lon, start_time?, duration_h=72, volume_m3=500, oil_type="IFO 380", n_particles=2000, wind_drift_factor=0.02}` -> `{id, status}`.
  `id` is a hash of the normalized request; identical requests return the cached run instantly.
  Default `start_time`: now (UTC, hour) if cached currents cover it, else start of the latest cached current window.
- `GET /api/spill/{id}` -> `{id, status: queued|running|done|error, progress, error?}`
- `GET /api/spill/{id}/tracks` -> 200 with `{times, particles:[{path:[[lon,lat]..], timestamps, status_end}], budget:[{t, surface, evaporated, dispersed, stranded}], meta}`; 202 + status while running; 409 on error.
  Budget is **kg of oil**. `dispersed` = naturally dispersed + submerged + biodegraded. Particle paths end when the particle strands/evaporates. ~9 MB raw / ~1.8 MB gzipped for 2000 particles x 72 h.
- `GET /api/spills` -> `data/spills/catalog.json` (A2).
- `GET /api/spills/{id}/footprints` -> `{id, footprints:[{t, name, geojson}]}` from `data/spills/<id>/footprints/*.geojson` (date parsed from file name).
- `GET /api/spills/{id}/tracks` -> hindcast run from catalog lat/lon/date_start/volume_bbl/oil_type; 202 + status until done, then tracks JSON.
- `GET /api/currents?start=&end=&bbox=` -> `{bbox, times:[unix_s]}` available in cached current NetCDF.
- `GET /api/currents/{t}?bbox=&step=0.1` (alias `GET /api/fields/currents?t=`) -> `{t, lon0, lat0, dlon, dlat, nx, ny, u:[], v:[]}`, row-major south->north, west->east, `null` on land.
- `GET /api/forcing`, `GET /api/health`.
