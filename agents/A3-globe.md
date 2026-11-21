# Agent A3: globe frontend
Repo: OilFlood. Read SYNC.md and docs/ARCHITECTURE.md first. Follow SYNC.md rules (small commits, push often to YOUR branch `a3-globe`, never force-push, never commit secrets). Deadline Sun Nov 22 12:00 PM PST.

Goal: `web/` Vite + TypeScript app: a full-screen floating 3D globe, nothing else. NO top bar, header, logo, or sidebar. Only small floating controls: a Historical / Prediction mode toggle, a time scrubber with play/pause/speed and clock, and a small info card.
- Historical mode: pins for saved spills from GET /api/spills; selecting one flies the camera there and plays the spill over time (observed footprints as GeoJSON polygons if present, else hindcast particle tracks) with animated ocean currents underneath for those dates (flow/particle layer from /api/currents).
- Prediction mode: click the ocean, a spill appears, watch it spread (contract below).
- Animated current field is a first-class layer in both modes (windy-style moving streaks, colored by speed).
- MapLibre GL JS v5 with `projection: globe`, dark space background with subtle atmosphere/glow, clean ocean-focused basemap (free, no API key: e.g. OpenFreeMap or Esri World Ocean raster). Smooth zoom from whole Earth to the Galveston coast. Start view: globe centered on the Gulf of Mexico.
- deck.gl via `@deck.gl/mapbox` MapboxOverlay (interleaved) so layers render correctly on the globe. Oil particles: TripsLayer (or ScatterplotLayer with per-frame interpolation) with trails, colored by age/thickness; optional HeatmapLayer for slick density.
- Click on ocean -> drop a spill marker, POST /api/spill per contract in docs/ARCHITECTURE.md, poll status with a progress indicator, then animate. Time slider 0–72 h with play/pause/speed and a clock readout. Small oil-budget panel (surface/evaporated/dispersed/stranded) from the tracks payload.
- Until the backend exists, ship a mock mode (`?mock=1` or automatic fallback) that generates plausible fake tracks client-side so the UI is fully demoable now. Clearly label mock data in the UI.
- Vite dev server on 5173 proxying /api to http://127.0.0.1:5200. `npm run build` -> web/dist.
- Keep it fast: 60 fps with 2000 particles x 145 timesteps. Minimal, polished UI (one control bar, one info panel).
- README section with setup/run commands. Report progress in SYNC.md.
