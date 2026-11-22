# OilFlood web (globe frontend)

Vite + TypeScript, MapLibre GL v5 (`projection: globe`) + deck.gl 9 (`MapboxOverlay`, interleaved). No framework.

```bash
cd web
npm install
npm run dev        # http://localhost:5273, proxies /api -> http://127.0.0.1:5200
npm run build      # -> web/dist (served by the FastAPI backend on :5200)
npm run typecheck
```

URL flags: `?mode=prediction` opens in prediction mode (default: historical); `?spill=<id>` opens a catalog spill (e.g. `?spill=dwh`).
Default view (T17): the page opens on the globe and flies to Houston/Galveston (`HOME` in main.ts, offset so the panel doesn't cover it).
Prediction mode shows a pulsing suggested spill point ~30 km SE of Galveston (`DEMO_SPILL`) plus a "Release it off Galveston" button.
To develop against another backend port: `VITE_API=http://127.0.0.1:5201 npm run dev`.

## What it does
- **Historical mode**: catalog pins from `GET /api/spills`. Pick one (pin or list) and the camera flies there and plays it back:
  observed outlines (`/api/spills/{id}/footprints`) as polygons, hindcast particles (`/api/spills/{id}/tracks`, polled while the
  backend answers 202 `{id,status,progress}`), and animated currents for those dates.
- **Prediction mode**: click the ocean, `POST /api/spill`, poll `GET /api/spill/{id}` (progress bar), then animate `GET /api/spill/{id}/tracks` 0–72 h.
- **Currents**: windy-style streaks colored by speed, in both modes, from `GET /api/currents?start&end&bbox` + `GET /api/currents/{t}`.
- **Global currents** (`src/globalCurrents.ts`): a second streak layer *under* the Gulf one, from `GET /api/currents?layer=global` + PNG textures
  `GET /api/currents/global/{t}.png`. Live = NOAA RTOFS, historical = HYCOM GOFS 3.1 for the spill's dates. Inside 18–31N 98–80W the global
  layer is masked whenever the Gulf layer has real data.
  Same clock as the timeline (linear blend between steps). For performance: at most 8 decoded textures are kept (each capped at 1440×720),
  6500 particles are spawned only on the visible hemisphere, the global layer updates at half frame rate, and the Gulf streaks thin out when zoomed out.
  Streaks slower than 0.25 m/s are drawn at 0.25 m/s length so shelf currents stay visible (colour still shows the real speed).
- **One floating panel** (`src/panel.ts` + `main.ts`): drag it by its header (position + collapsed state saved in localStorage
  key `oilflood.panel.v1`; double-click or the chevron collapses it). It holds the mode toggle, spill list / prediction status,
  the time row (sim date+time in UTC and local, play/pause (space), scrubber, animation rate shown as "X days/sec" or
  "X hours/sec" with −/+), notes when real data is missing for the current date, the oil budget and the legend. There are no other panels.

## Real data only
There is no mock or synthetic fallback anywhere. Everything drawn comes from the backend's cached real data (HYCOM / RTOFS / GFS
forcing, NOAA/NESDIS outlines, OpenDrift runs on that forcing). When something is missing for the current date or place the panel
says so ("No current data for 2010-07-20", "No observed outline for ...", backend error text such as "That spot is on land").

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
