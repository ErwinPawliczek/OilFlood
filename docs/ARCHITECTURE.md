# OilFlood architecture

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
- **MapLibre GL** basemap: tilted 3D view, terrain/bathymetry, free tiles.
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
- CesiumJS: full globe, heavier and slower to build.
- Three.js: would have to rebuild map plumbing.
