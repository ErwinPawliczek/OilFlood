# Backtest: OpenOil vs our model vs observed spills (P5)

Code: `backend/app/backtest/` · results: `data/backtest/results.json` (committed, ~100 KB) · API: `GET /api/backtest`
(`?spill=dwh`, `&detail=true` adds the per-start rows) · panel: historical mode → a spill → **Backtest** (collapsible).

```bash
cd backend
~/miniforge3/envs/oilflood/bin/python -m app.backtest            # all cases, 12 workers (~25 min cold, ~5 min cached)
~/miniforge3/envs/oilflood/bin/python -m app.backtest --only dwh --max-starts 5   # quick subset
~/miniforge3/envs/oilflood/bin/python -m app.backtest.report     # the markdown tables below
```
Runs are cached in `backend/runs/backtest/<hash>/` (gitignored). The first run also trains the held-out ML variant
(below) into `backend/runs/backtest/ml_nodwh/` (~1 min). Needs the cached hindcast forcing (`data/cache/forcing/{dwh,texas_city_y,main_pass_2023}`).

## Models compared

| key | model |
|---|---|
| `openoil` | OpenDrift OpenOil, exactly as the app runs it (2 % windage + parameterised Stokes, K = 10 m²/s, NOAA weathering, stranding) |
| `ours_ml` | our model (docs/OURMODEL.md) with the shipped ML drift correction |
| `ours_noml` | our model, ML off (`ml=false`) |
| `ours_ml_nodwh` | DWH only: our model with the ML **retrained without the DWH window** (MC 209 2017 + Eugene Island 2009 drifters, 9 drifters / 1,674 fixes). The shipped ML is 88 % DWH-2010 drifters, so on DWH it is not out of sample; this variant is. |
| `persistence` | DWH only: "the slick stays where it was seen" (outline D used as the forecast for D+1..D+3). A reference any model should beat. |

All models run on the same HYCOM-TSIS GOMb0.04 reanalysis currents + its 10 m winds as the app's hindcasts, 2000 particles,
dt = 15 min, output every 30 min. Seeds go through the same `jobs.plan` as the app (seed snapped to model water ≤ 30 km).

## Metrics

- **Centroid separation** (km) at 24 / 48 / 72 h: great-circle distance between the model centroid (mean of all particle
  positions) and the observed centroid (area-weighted centroid of the NESDIS outline).
- **Liu–Weisberg skill** (Liu & Weisberg 2011, tolerance n = 1): s = max(0, 1 − Σ dᵢ / Σ lᵢ), where dᵢ is the separation at
  day i and lᵢ the length of the observed centroid path from day 0 to day i. s = 1 is perfect; 0 means the error is as large
  as the observed movement. A slick that barely moves (small lᵢ) gives s ≈ 0 even for small errors, which is common at 24 h.
- **IoU** (intersection over union) of the model footprint and the observed outline on a 0.05° (~5 km) raster. The model
  footprint is every cell with ≥ 1 particle.
- **FSS** (fractions skill score, Roberts & Lean 2008) of the same two rasters at a 5 × 5 cell (~25 km) neighbourhood.
  1 = perfect, 0 = no overlap at that scale.
- Particle positions: every particle counts. A particle whose track ended (stranded, evaporated, out of the domain) keeps its
  last position; our model is run with `record_all` so weathered particles keep moving instead of disappearing. So these are
  **transport** scores (where the oil mass went), not slick-visibility scores.
- Stranded share: particles that beached by that time.

RESULTS_PLACEHOLDER
