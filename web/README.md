# OilFlood web (globe frontend)

Vite + TypeScript, MapLibre GL v5 (`projection: globe`) + deck.gl 9 (`MapboxOverlay`, interleaved). No framework.

```bash
cd web
npm install
npm run dev        # http://localhost:5273, proxies /api -> http://127.0.0.1:5200
npm run build      # -> web/dist (served by the FastAPI backend on :5200)
npm run typecheck
```

URL flags: `?mock=1` forces client-side mock data everywhere. `?mode=prediction` opens in prediction mode (default: historical).

## What it does
- **Historical mode**: catalog pins from `GET /api/spills`. Pick one (pin or list) and the camera flies there and plays it back:
  observed outlines (`/api/spills/{id}/footprints`) as polygons, hindcast particles (`/api/spills/{id}/tracks`, polled while the
  backend answers 202 `{id,status,progress}`), and animated currents for those dates.
- **Prediction mode**: click the ocean, `POST /api/spill`, poll `GET /api/spill/{id}` (progress bar), then animate `GET /api/spill/{id}/tracks` 0–72 h.
- **Currents**: windy-style streaks colored by speed, in both modes, from `GET /api/currents?start&end&bbox` + `GET /api/currents/{t}`.
- Time bar: play/pause (space), speed (0.25–4×), scrubber, UTC clock. Info card: spill details, oil budget (fractions; kg is normalised), current legend.

## Mock fallback
Each endpoint falls back to synthetic client-side data independently if the backend is unreachable, returns 404/5xx, or has no
data for the window (e.g. currents for 2010). The info card shows a red **MOCK DATA** badge listing exactly which parts are mock.
Mock = analytic Loop Current + eddies + shelf current field, a coarse Gulf land mask, and a simple Lagrangian run (`src/mock.ts`).

## Data formats accepted
- Current grid: `{t, lon0, lat0, dlon, dlat, nx, ny, u:[ny*nx], v:[ny*nx]}` row-major, lat ascending, `null` = land
  (also `{lon:[..], lat:[..], u:[[..]], v}`; descending lat is flipped). Timestep list: `{times:[unix|ISO], bbox:[w,s,e,n]}` or a bare array.
- Footprints: `{footprints:[{t, geojson}]}`, a FeatureCollection with `properties.time|date`, or `[{t, geojson}]`.
- Tracks: contract in `docs/ARCHITECTURE.md`; times may be unix seconds or ISO.

## Notes for whoever touches the rendering
On the MapLibre globe in interleaved mode, deck.gl `LineLayer`, `TextLayer` and `TripsLayer` do not render correctly (nothing
drawn / wrong trail times). `PathLayer`, `ScatterplotLayer` and polygon layers work. So: streaks and oil trails are binary
`PathLayer`s re-interpolated each frame (`src/flow.ts`, `src/oil.ts`), labels are HTML markers, and layers use
`depthCompare: 'always'` to avoid z-fighting with the globe surface. Binary attribute buffers are ping-ponged because deck.gl
only re-uploads when the array reference changes.
