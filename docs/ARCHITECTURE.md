# OilFlood architecture

## Scope now: Step A (visualize only)
A bare full-screen 3D globe. **No top bar, no logo, no chrome** (logo/favicon later). Only minimal floating controls: mode toggle, time scrubber, a small info card.

Two modes:
1. **Historical mode**: pick a saved past spill (pins on the globe). Watch it unfold over time: spill footprint/particles growing and drifting, with the **ocean currents at that time** animated underneath (and winds optional). Sources per spill: observed slick outlines where they exist (e.g. NOAA/NESDIS daily outlines for Deepwater Horizon), otherwise a physics hindcast (OpenDrift with HYCOM reanalysis currents for those dates).
2. **Prediction mode**: click the ocean, a spill appears, watch it spread 0–72 h over forecast currents.

Modeling (ML correction, skill scores, response guidance) comes **after** visualization works.

### Historical spill catalog
`data/spills/catalog.json`: `[{id, name, date_start, date_end, lat, lon, volume_bbl, oil_type, source_urls, has_observed_outlines}]`, seeded from NOAA IncidentNews for the Gulf of Mexico. Starter set: Deepwater Horizon (2010), Texas City Y (2014), Ixtoc I (1979, if forcing exists), plus 3–5 more Gulf incidents.

### API contract (Step A)
- `GET /api/spills` -> historical catalog
- `GET /api/spills/{id}/footprints` -> time series of observed outlines (GeoJSON per timestep) if available
- `GET /api/spills/{id}/tracks` -> hindcast particle tracks (same shape as prediction tracks)
- `GET /api/currents?start=&end=&bbox=` -> list of timesteps; `GET /api/currents/{t}` -> downsampled u/v grid for the flow layer

- `POST /api/spill` body `{lat, lon, start_time (ISO, optional, default now-ish/cached window), duration_h (default 72), volume_m3 (default 500), oil_type (default "IFO 380"), n_particles (default 2000)}` -> `{id, status}`
- `GET /api/spill/{id}` -> `{id, status: queued|running|done|error, progress, error?}`
- `GET /api/spill/{id}/tracks` -> JSON `{times: [unix_s...], particles: [{path: [[lon,lat],...], timestamps:[...], status_end: "active"|"stranded"|"evaporated"}...], budget: [{t, surface, evaporated, dispersed, stranded}...]}` (Arrow later if size is a problem)
- `GET /api/fields/currents?t=` -> u/v grid (JSON or PNG texture) for the flow layer (stretch goal)
- Frontend dev server proxies `/api` to the backend on port 5200. Production: FastAPI serves the built frontend from `web/dist` on port 5200; Cloudflare tunnel oilflood.xyz -> http://127.0.0.1:5200.

### Repo layout
- `backend/` FastAPI app + OpenDrift runner (`backend/app/`, `backend/requirements.txt`)
- `data/` forcing download/cache scripts (`data/fetch_*.py`); cached files in `data/cache/` (gitignored)
- `web/` Vite + TypeScript frontend

Goal: smooth, GPU-rendered 3D playback of an oil spill forecast (24/48/72 h) over the Gulf of Mexico off Houston/Galveston, plus a responder brief.

## Flow
```
Responder sets spill (location, time, volume, oil type)
  -> FastAPI /scenario
  -> OpenDrift (OpenOil) physics run, forced by current + wind forecasts
  -> ML drift correction (trained on NOAA Global Drifter Program tracks)
  -> cache particle tracks (Arrow/Parquet) + shoreline impact summary
  -> browser fetches binary tracks once, animates locally (deck.gl)
  -> local LLM (Ollama on tower GPUs) writes the response brief from the impact summary
```

## Frontend
- **MapLibre GL v5 globe projection**: floating 3D globe on a dark space background, smooth zoom from planet to coast; free tiles (OpenFreeMap / Esri ocean basemap), no API key.
- **deck.gl** layers:
  - `TripsLayer`: animated oil particles over time (60 fps, thousands of particles).
  - `HeatmapLayer` / contours: slick density / thickness.
  - Particle flow layer (deck.gl community `ParticleLayer` or windy-style WebGL shader): animated current + wind fields from PNG-encoded u/v textures.
  - `ScatterplotLayer`/`GeoJsonLayer`: threatened shoreline segments (NOAA ESI), staging points for booms/skimmers.
- Time slider + play/pause scrubbing 0–72 h. All animation client-side, no server round-trips during playback.
- Stack: Vite + React (or plain TS), served as static files by FastAPI.

## Backend
- **FastAPI** (Python), single port, exposed via Cloudflare tunnel at oilflood.xyz.
- Endpoints (draft): `POST /api/scenario`, `GET /api/scenario/{id}/tracks` (Arrow), `GET /api/fields/{t}.png` (u/v textures), `GET /api/scenario/{id}/impacts`, `POST /api/scenario/{id}/brief`.
- **OpenDrift / OpenOil** for transport + weathering.
- **ML correction**: small model (gradient boosting or MLP) predicting drift residuals from currents, winds, location; trained and evaluated on Global Drifter Program tracks in the Gulf.
- **Backtest**: replay historical drifters/spills, report separation distance and Liu–Weisberg skill score with vs without correction.
- Cache results on disk keyed by scenario hash.

## Not using
- CesiumJS: heavier and slower to build; MapLibre globe + deck.gl is enough.
- Three.js: would have to rebuild map plumbing.
