# OilFlood

**Where will spilled oil go in the next 72 hours?** OilFlood is an oil-spill trajectory forecaster for first responders such as
Coast Guard, NOAA and local emergency managers in the first hours of a spill. Click the ocean and it releases a spill there *now*.
It runs two models on the **live** NOAA ocean-current and wind forecasts: the established OpenDrift/OpenOil model and our own
particle model (with an optional, experimental machine-learned drift correction). It then animates the slick for 72 h on a 3D globe: drift, weathering
and stranding on the coast. Historical mode replays real Gulf of Mexico spills (Deepwater Horizon, Texas City "Y", MC 209, ...)
on the ocean reanalysis for their dates, against observed slick outlines and the responders' own logged actions. Everything is
real data; there are no mocks. Built at EvoHacks 2 (Projects for Social Good).

**Live: https://oilflood.xyz** (best in desktop Chrome/Edge/Firefox with a GPU)

## Screenshots
Taken from a local test server on 2026-11-22 (UTC) with the live forecast of that day and the real historical data.

| | |
|---|---|
| ![Home](docs/img/home.png) | ![Prediction](docs/img/prediction.png) |
| **Home:** the globe flies to Houston/Galveston with live NOAA RTOFS currents and the historical spill pins. | **Prediction:** "Release it off Galveston" (1,000 bbl medium crude) on the live RTOFS + GFS forecast; slick at T+48 h, forecast block and run details. |
| ![Compare](docs/img/compare.png) | ![Deepwater Horizon](docs/img/dwh.png) |
| **Compare:** OpenOil (amber) and our model (blue) on the same spill, with the experimental ML toggle (off). | **Historical → Deepwater Horizon:** NOAA/NESDIS observed outline for 2010-06-02 over the HYCOM reanalysis currents. |
| ![Global currents](docs/img/global.png) | ![Response outline](docs/img/response.png) |
| **Zoomed out:** global RTOFS surface currents. | **Response outline** for a live spill off Padre Island: numbered priority shorelines (NOAA ESI) on the map, first landfall, boom-first list and dispersant/burn/skimming windows. |

## Features
- **Prediction mode (live).** Click anywhere in the Gulf of Mexico (or use the suggested point off Galveston). Pick the amount
  (bbl) and oil type (9 ADIOS oils). The run starts at the current hour on the newest **NOAA RTOFS** currents and **NCEP GFS**
  winds, which a systemd timer refreshes 4× a day. The panel shows which forecast run it used and how far ahead it is valid. A
  72 h run with 2000 particles takes ~15–25 s.
- **Two models, side by side.** *OpenOil* (OpenDrift, NOAA-style weathering) is the baseline. *Ours* is a numpy Lagrangian model;
  its ML drift correction is an **experimental** checkbox, off by default (see below). *Compare* draws both at once with separate
  oil budgets.
- **Historical mode.** 9 Gulf spills from NOAA IncidentNews, 1979–2023. Each spill shows its record, a hindcast on HYCOM reanalysis
  currents/winds for those dates, and, for DWH, 94 daily NOAA/NESDIS observed slick outlines. Its **response log** lists 280 dated
  responder actions with verbatim source quotes. Clicking a date moves the clock there.
- **Currents everywhere.** Animated surface currents in the style of windy.com: Gulf HYCOM/RTOFS at 0.1° on top of global RTOFS
  (live) or HYCOM GOFS 3.1 (historical). Colour shows speed. Both layers are synced to the simulation clock.
- **Backtest** (`docs/BACKTEST.md`): OpenOil vs ours (with and without ML) vs observed outlines. Scored with Liu–Weisberg skill,
  centroid separation, IoU and FSS at 24/48/72 h.
- **Response outline** (`docs/RESPONSE.md`): from the forecast, the outline gives time to first landfall, priority shorelines
  ranked by NOAA ESI sensitivity × arrival time, where to boom first, dispersant/in-situ-burn/skimming windows, closures, and
  precedents cited from past spills. An LLM summary is added if an API key is set. *Planning aid only, not operational guidance.*
- One draggable, collapsible panel. Time scrubber with "X hours/sec" playback. URL flags: `?mode=prediction`, `?spill=dwh`, `?model=ours|compare`, `?ml=1` (experimental ML on).

## The AI part
1. **Baseline (pre-existing model): OpenDrift OpenOil** (Norwegian Meteorological Institute), set up the way NOAA GNOME-style
   responders use it: currents + 2 % wind drift + Stokes drift, random-walk diffusion, evaporation, emulsification, dispersion,
   coastline stranding. It uses oils from NOAA's ADIOS database.
2. **Ours: physics + learned drift correction** (`backend/app/ourmodel/`, details in [docs/OURMODEL.md](docs/OURMODEL.md)). The
   physics: RK2 particle advection, Fay spreading, Fingas evaporation, Mackay emulsification/dispersion, beaching on GSHHG.
   On top of that, an optional, **experimental** (off by default) **scikit-learn HistGradientBoosting** model predicts the error
   of the ocean model's current. It was trained
   on **NOAA Global Drifter Program** hourly drifters (42 drifters, 14,089 fixes in the Gulf) vs the HYCOM forcing the drifters
   actually saw. Features are the current, wind, speeds, vorticity, divergence and a drogue flag.
   - Hold-out skill (5-fold CV grouped by drifter): velocity RMSE **0.410 → 0.358 m/s**; 24 h separation median **21.2 → 18.1 km**;
     48 h **38.0 → 34.5 km**.
   - The correction is applied only inside the drifter training coverage, and the training set is mostly DWH-2010 deep water.
   - **On real oil spills it did not help, so it is off by default** ([docs/BACKTEST.md](docs/BACKTEST.md)). On Deepwater Horizon
     (52 start days, scored against NOAA/NESDIS outlines) the median 72 h centroid error is 46 km with ML and 42 km without
     (OpenOil 44 km); ML ends closer than no-ML on only 48 % of start days at 72 h, even though DWH is in its training data. At
     Main Pass 2023, the one true out-of-sample oil case, ours + ML strands 62 % of the oil on the Mississippi Delta within 9 h;
     OpenOil and ours without ML strand 0 %, and the observed slick did not reach shore. No model beat simple persistence on DWH
     (39 km at 72 h), largely because neither model has a continuous wellhead source term.
   - In the app it is a checkbox labelled *experimental* under the model toggle; API: `ml=true` on `POST /api/spill` or the
     hindcast endpoints (default `false`).
   - Retrain: `cd backend && python -m app.ourmodel.train` (~1 min; downloads the drifter CSVs from AOML ERDDAP).
3. **LLM briefing (optional)** in the response outline: a short plain-language summary written *from* the rule-based outline.
   It only runs if `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `XAI_API_KEY` is set; otherwise that section is skipped.

## Architecture
```
 browser (web/: Vite + TypeScript, MapLibre GL v5 globe + deck.gl 9, no framework)
   │  GET /api/currents, /api/currents/global/{t}.png   flow-layer fields (JSON 0.1° Gulf grid, PNG global u/v textures)
   │  POST /api/spill → poll → GET /api/spill/{id}/tracks   particle tracks (gzip, ~1.5 MB for 2000 particles × 72 h)
   ▼
 backend/: FastAPI on :5200 (also serves web/dist)
   ├─ jobs.py      run cache keyed by request + forcing hash, 4-process worker pool, progress
   ├─ runner.py    OpenDrift OpenOil          ├─ ourmodel/  our model (+ experimental ML correction)
   ├─ forcing.py   finds cached CF NetCDF     ├─ fields.py / global_fields.py  flow-layer fields
   └─ catalog, footprints, backtest, response outline
   ▲
 data/: fetch scripts → data/cache/ (NetCDF forcing + PNG/JSON fields, gitignored), data/spills/ (catalog + outlines, committed)
   └─ refresh_live.sh run by a systemd user timer (RTOFS + GFS + global fields, 4×/day)
```
Production: the tower runs uvicorn on 127.0.0.1:5200 and a Cloudflare tunnel serves it as oilflood.xyz. More detail is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [backend/README.md](backend/README.md), [web/README.md](web/README.md) and
[data/README.md](data/README.md).

## Data sources
| Data | Source | Used for | Licence / terms |
|---|---|---|---|
| Live ocean currents | NOAA NCEP **RTOFS** global 1/12° ([NOMADS](https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/)) | prediction runs, live flow layer | US Government work, public domain |
| Live winds | NCEP **GFS 0.25°** 10 m winds ([UCAR THREDDS](https://thredds.ucar.edu/thredds/catalog/grib/NCEP/GFS/Global_0p25deg/catalog.html)) | prediction runs | public domain |
| Fallback live currents | **HYCOM ESPC-D-V02** ([tds.hycom.org](https://tds.hycom.org/thredds/catalog.html)) | used if RTOFS is unavailable | HYCOM.org free data, no restrictions; please acknowledge HYCOM |
| Hindcast currents + winds | **HYCOM-TSIS GOMb0.04** Gulf reanalysis 2001–2024 ([HYCOM](https://www.hycom.org/data/gomb0pt04/gom-reanalysis)) | historical replays, backtest, ML training | HYCOM.org, free |
| Global hindcast currents | **HYCOM GOFS 3.1** global ([HYCOM](https://www.hycom.org/dataserver/gofs-3pt1/analysis)) | historical global flow layer | HYCOM.org, free |
| Spill catalog + response logs | NOAA **IncidentNews** ([incidentnews.noaa.gov](https://incidentnews.noaa.gov/)) | historical mode, precedents | public domain |
| DWH slick outlines | NOAA/NESDIS **SAB** ([satepsanone.nesdis.noaa.gov](https://satepsanone.nesdis.noaa.gov/pub/OMS/disasters/DeepwaterHorizon/)) | DWH observed footprints, backtest | public domain |
| Drifters | NOAA AOML **Global Drifter Program** hourly ([ERDDAP](https://erddap.aoml.noaa.gov/gdp/erddap/tabledap/drifter_hourly_qc.html)) | ML training/validation | public domain; cite Elipot et al. (2016) |
| Shoreline sensitivity | NOAA OR&R **National ESI Shoreline** ([ESI downloads](https://response.restoration.noaa.gov/esi_download)) | response outline | public domain |
| Place names | US Census Bureau **Gazetteer** 2023 ([census.gov](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html)) | response outline | public domain |
| Oil properties | NOAA **ADIOS** oil database (`adios_db`) | both models | public domain (NOAA) |
| Coastline | **GSHHG** via OpenDrift / `roaring_landmask` | stranding | LGPL |
| Basemap tiles | **Esri World Ocean Base** | globe background | Esri terms of use, attribution shown on the map |

Software: OpenDrift (GPL-2.0), MapLibre GL JS (BSD-3), deck.gl (MIT), FastAPI (MIT), xarray (Apache-2.0), scikit-learn (BSD-3).

## Setup from a clean machine
Tested on Linux (Ubuntu, x86-64) with miniforge3 and Node ≥ 20.19 (Vite 8). No accounts or API keys are needed.
Verified on 2026-11-21 by following these steps in a fresh clone with a new conda env: the env builds, the backend starts,
`smoke_test.py` passes and the web build is served (the data cache was shared rather than downloaded again).

### 1. Clone + Python env (~3–5 min)
```bash
git clone https://github.com/ErwinPawliczek/OilFlood.git && cd OilFlood
# miniforge3 if you don't have conda/mamba: https://github.com/conda-forge/miniforge
mamba env create -f backend/environment.yml        # env "oilflood": OpenDrift, ADIOS, xarray, scikit-learn, FastAPI, ...
conda activate oilflood
```

### 2. Data (real forcing; nothing is bundled except the spill catalog, DWH outlines, ESI grid and the trained ML model)
Run these from `data/` with the `oilflood` env active. Every script is idempotent and resumable; finished files are skipped.
```bash
cd data
# Prediction mode: live RTOFS currents + GFS winds + both flow layers
./refresh_live.sh               # ~5 min; streams ~4 GB of RTOFS (deleted after use), keeps ~30 MB
                                # (uses $OILFLOOD_PY, default ~/miniforge3/envs/oilflood/bin/python)
# Historical mode: reanalysis for the catalog spills (optional, big)
python fetch_hycom.py --catalog # HYCOM-TSIS Gulf, ~550 MB kept; DWH alone takes 30-60 min, others ~5 min each
python export_fields.py --all   # Gulf flow-layer fields from the NetCDF (~10 s per window)
python fetch_global.py --catalog # HYCOM global flow layer per spill, ~145 MB kept (~2 GB raw in data/raw/, deletable)
cd ..
```
Quick start: `python fetch_hycom.py --window texas_city_y` (~5 min, 83 MB) is enough to try historical mode on one spill.

Sizes on our server: `data/cache/` is ~1 GB (forcing ~550 MB, fields ~430 MB, drifters 1.3 MB) and `data/raw/` is ~1.9 GB
(only resume chunks; delete after export). Keep live data fresh with the timer (`data/install_live_timer.sh`, a systemd user
timer at 02/08/14/20:30 UTC) or by re-running `./refresh_live.sh`.

### 3. Backend
```bash
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 5200
python smoke_test.py            # second terminal: live prediction off Galveston with both models (~1 min)
```
`GET /api/live` shows the cached forecast and `GET /api/forcing` shows every NetCDF the backend found.
To use a cache somewhere else: `OILFLOOD_DATA_DIR=/path/to/data`.

### 4. Frontend
```bash
cd web
npm ci
npm run build                   # -> web/dist (~2 MB, 0.5 MB gzipped), served by the backend: open http://127.0.0.1:5200
# or, for development with hot reload:
npm run dev                     # http://localhost:5273, proxies /api to :5200 (VITE_API=http://127.0.0.1:<port> to change)
```

## Repo layout
`backend/` API + models · `web/` globe frontend · `data/` fetch scripts, spill catalog, outlines, ESI · `docs/` architecture,
model, backtest, response, research notes · `SYNC.md` team/agent coordination log.

## Limits (honest list)
- Forecasts cover only the Gulf of Mexico (18–31°N, 98–80°W), because that is where the real forcing is cached.
- ~4–8 km ocean models treat bays such as Galveston Bay as land, so in-bay spills strand within hours.
- The ML correction was learned from reanalysis errors, mostly DWH-2010 deep water, and the oil backtest shows no gain from it
  (harmful at Main Pass 2023). It is off by default; when switched on in live mode it is applied to RTOFS without a skill claim.
- Spills before 2001 (Ixtoc I, Burmah Agate, Mega Borg) have no ocean reanalysis. They show the record and response log only.
