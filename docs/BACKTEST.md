# Backtest: OpenOil vs our model vs observed spills (P5)

Code: `backend/app/backtest/` (+ `backend/app/source.py`) · results: `data/backtest/results.json` (committed, ~100 KB) · API: `GET /api/backtest`
(`?spill=dwh`, `&detail=true` adds the per-start rows) · panel: historical mode → a spill → **Backtest** (collapsible).

```bash
cd backend
~/miniforge3/envs/oilflood/bin/python -m app.backtest            # all cases, 12 workers (~25 min cold, ~5 min cached)
~/miniforge3/envs/oilflood/bin/python -m app.backtest --only dwh --max-starts 5   # quick subset (DWH also runs the 3 wellhead-source variants)
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
| `openoil_src`, `ours_ml_src`, `ours_noml_src` | DWH only: the same three runs **plus a continuous wellhead release** (see "Wellhead source" below). |
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

## Results

### Summary

- **Nothing beats persistence on DWH, with or without the wellhead source.** Over 52 start days, "the slick stays where it
  was seen" has the smallest median centroid error (23 / 31 / 39 km at 24 / 48 / 72 h) and the best LW skill and FSS. Each
  model ends closer than persistence on only 38–48 % of start days at 72 h.
- **Adding the continuous wellhead release (federal flow estimate minus containment) changes almost nothing.** Before → after,
  median 72 h centroid error: OpenOil 44 → 43 km, ours + ML 46 → 44 km, ours without ML 42 → 42 km. FSS at 72 h goes up a
  little for all three (0.51 → 0.53, 0.49 → 0.53, 0.53 → 0.54), LW skill at 48 h goes down (0.18 → 0.11, 0.15 → 0.12,
  0.20 → 0.16), and each source run ends closer than its no-source twin on only 48–58 % of start days, i.e. a coin flip. The
  share of start days beating persistence at 72 h drops (44 → 38 %, 40 → 38 %, 46 → 40 %). So the missing source term was
  **not** the reason the models lose to persistence. The hypothesis from the first backtest is not supported.
- **Among the models, ours without ML scores best on DWH** (72 h: 42 km median, LW 0.31, FSS 0.53), then OpenOil (44 km, LW 0.26,
  FSS 0.51) and ours + ML (46 km, LW 0.27, FSS 0.49). The differences are a few km, about the same size as the day-to-day spread,
  so read them as "about equal", not as a ranking.
- **The ML correction does not help on oil.** On DWH it is closer than no-ML on about half the start days (52 / 54 / 48 %) and a
  little worse in the medians, even though most DWH particle-steps are inside its coverage and it was trained on drifters from the
  same summer (in-sample). Retrained with the DWH window held out, its coverage mask never reaches the DWH slick (0 % of
  particle-steps), so it is effectively the no-ML model. Its 25 / 35 / 38 % "closer" shares are not a skill signal: identical
  centroids count as "not closer", and the runs differ only where a few particles touch the edge of the coverage mask.
- **Main Pass 2023:** all three runs move the floating oil SW like the observed slick (bearing 208–224°). OpenOil and ours without
  ML strand nothing, as observed. **Ours + ML strands 62 % on the Mississippi Delta (around 29.16N 89.01W) within 9 h**, which
  did not happen. This is the clearest failure of the ML in the backtest. All slicks are shorter at 24 h than the observed > 64 km
  (OpenOil 37 km, ours + ML 21 km, ours without ML 11 km), so both models spread too little along the current here.
- **Texas City Y 2014:** from the catalog collision point (inside Galveston Bay, which HYCOM GOMb0.04 treats as land), all models
  strand 100 % in the bay within the run. That matches the reported Galveston-area oiling, but none of the oil reaches the Gulf,
  where it was seen on Mar 24–25. Seeded at the bay entrance on Mar 23 instead, OpenOil gets closest to the two overflight
  positions (nearest floating oil 15 km on Mar 24, 8 km on Mar 25). Ours without ML is 21 / 17 km away, ours + ML 21 / 24 km.

_Generated 2026-11-22T02:04:53Z · 2000 particles · raster 0.05° · FSS window 5 cells_

### Deepwater Horizon (`dwh`)

52 start days (2010-05-08 … 2010-07-12). Medians over start days (means in brackets).

| model | sep 24 h (km) | sep 48 h | sep 72 h | LW 24 h | LW 48 h | LW 72 h | IoU 72 h | FSS 24 h | FSS 72 h | closer than persistence (72 h) |
|---|---|---|---|---|---|---|---|---|---|---|
| Persistence (observed outline D) | 23 (28) | 31 (34) | 39 (40) | 0.00 | 0.20 | 0.34 | 0.23 | 0.78 | 0.59 | – |
| OpenOil | 27 (31) | 36 (40) | 44 (46) | 0.00 | 0.18 | 0.26 | 0.20 | 0.72 | 0.51 | 44 % |
| Ours + ML | 25 (31) | 36 (39) | 46 (47) | 0.00 | 0.15 | 0.27 | 0.19 | 0.73 | 0.49 | 40 % |
| Ours (no ML) | 25 (30) | 33 (38) | 42 (44) | 0.00 | 0.20 | 0.31 | 0.21 | 0.73 | 0.53 | 46 % |
| Ours + ML (DWH drifters held out) | 25 (30) | 33 (38) | 42 (44) | 0.00 | 0.19 | 0.31 | 0.20 | 0.73 | 0.53 | 48 % |
| OpenOil + wellhead source | 26 (30) | 36 (38) | 43 (47) | 0.00 | 0.11 | 0.28 | 0.20 | 0.71 | 0.53 | 38 % |
| Ours + ML + wellhead source | 26 (30) | 38 (38) | 44 (46) | 0.00 | 0.12 | 0.28 | 0.20 | 0.73 | 0.53 | 38 % |
| Ours (no ML) + wellhead source | 25 (30) | 34 (37) | 42 (45) | 0.00 | 0.16 | 0.28 | 0.21 | 0.73 | 0.54 | 40 % |

- Ours + ML ends closer to the observed centroid than ours without ML on 52 % / 54 % / 48 % of start days (24 / 48 / 72 h).
- Ours + ML (DWH held out) ends closer to the observed centroid than ours without ML on 25 % / 35 % / 38 % of start days (24 / 48 / 72 h).
- OpenOil + wellhead source ends closer to the observed centroid than OpenOil (no source) on 48 % / 56 % / 50 % of start days (24 / 48 / 72 h).
- Ours + ML + wellhead source ends closer to the observed centroid than Ours + ML (no source) on 52 % / 56 % / 50 % of start days (24 / 48 / 72 h).
- Ours (no ML) + wellhead source ends closer to the observed centroid than Ours (no ML) (no source) on 48 % / 58 % / 50 % of start days (24 / 48 / 72 h).
- Ours + ML: mean share of particle-steps inside the ML coverage mask 75 %.
- Ours + ML (DWH drifters held out): mean share of particle-steps inside the ML coverage mask 0 %.
- Mean stranded share at 72 h: OpenOil 14 %, Ours + ML 12 %, Ours (no ML) 12 %, Ours + ML (DWH drifters held out) 12 %, OpenOil + wellhead source 7 %, Ours + ML + wellhead source 6 %, Ours (no ML) + wellhead source 6 %.

### Texas City "Y" collision (`texas_city_y`)

Observed: IncidentNews overflight positions (catalog evolution) + reported shoreline oiling; no polygons.

- 2014-03-24 overflight: leading edge 28°48'N 95°12'W (assumed 2014-03-24T17:00:00 UTC) — https://incidentnews.noaa.gov/incident/8741/19501
- 2014-03-25 overflight: sheens/tarballs 28°34.3'N 95°26.9'W (assumed 2014-03-25T17:00:00 UTC) — https://incidentnews.noaa.gov/incident/8741/19566

**Seed at the collision site (catalog point/time, as the app hindcast)** — seed 29.337N 94.840W (moved 5.1 km to model water), 72 h.

| model | Mar 24: nearest floating oil (km) | centroid (km) | Mar 25: nearest (km) | centroid (km) | stranded at end | stranded centroid |
|---|---|---|---|---|---|---|
| OpenOil | all oil ashore | – | all oil ashore | – | 100 % | 29.38N 94.87W |
| Ours + ML | all oil ashore | – | all oil ashore | – | 100 % | 29.35N 94.88W |
| Ours (no ML) | all oil ashore | – | all oil ashore | – | 100 % | 29.37N 94.86W |

**Seed at the Galveston entrance (Bolivar Roads 29.33N 94.72W), 2014-03-23 12 UTC, when oil was reported leaving the bay** — seed 29.330N 94.720W (moved 0.0 km to model water), 60 h.

| model | Mar 24: nearest floating oil (km) | centroid (km) | Mar 25: nearest (km) | centroid (km) | stranded at end | stranded centroid |
|---|---|---|---|---|---|---|
| OpenOil | 14.7 | 20.5 | 8.1 | 15.4 | 0 % | – |
| Ours + ML | 21.2 | 26.5 | 24.2 | 34.1 | 0 % | 29.33N 94.72W |
| Ours (no ML) | 20.7 | 25.8 | 17.0 | 24.3 | 0 % | – |

Not scored: Matagorda Island landfall (2014-03-27) is after the cached forcing ends (2014-03-26).

### Main Pass pipeline (`main_pass_2023`)

Observed: Nov 17: slick over 40 miles (64 km) long heading SW from the leak; Nov 18: moving SW away from shore; Nov 28: no shoreline impacts found (IncidentNews, catalog evolution).

| model | centroid bearing 24 / 48 / 72 h (°) | toward SW (180–270°) | centroid distance 72 h (km) | stranded 72 h | first stranding (h) | slick length 24 h (km) |
|---|---|---|---|---|---|---|
| **observed** | SW | yes | – | 0 % (no shoreline impact) | none | > 64 |
| OpenOil | 223 / 222 / 218 | yes | 133 | 0 % | – | 37 |
| Ours + ML | 224 / 215 / 208 | yes | 112 | 62 % | 9 | 21 |
| Ours (no ML) | 223 / 221 / 217 | yes | 130 | 0 % | – | 11 |

## Wellhead source (DWH `_src` runs)

Code: `backend/app/source.py` (shared by both models), `backend/app/backtest/dwh_source.py` (the DWH schedule).
A run's params may carry an optional `"source"` (lon, lat, piecewise-linear rate in bbl/day, particle count). Without it nothing
changes: no existing run hash or result moved. (The `ours_ml_nodwh` runs were recomputed only because the hash contains the
worktree path. They differ from the earlier ones by ≤ 0.2 km per run. Our model's random seed uses Python's per-process `hash()`.
The "closer" shares of that ML-vs-no-ML tie-break moved from 17 / 31 / 42 % to 25 / 35 / 38 %, which shows that number is noise.)

- **Location:** the MC252 wellhead, 28.7367 N 88.3872 W (catalog `dwh`), released on the surface in a 50 m disc.
- **Gross flow (official):** US DOI / Flow Rate Technical Group + DOE estimate (press release 2010-08-02): 62,000 bbl/day at the
  start, 53,000 bbl/day immediately before the well was shut in (2010-07-15 19:25 UTC), ±10 %, 4.9 M bbl in total, ~800,000 bbl
  captured. The release only says the rate decreased (reservoir depletion), so we **interpolate linearly** between the two rates.
  https://www.doi.gov/news/pressreleases/US-Scientific-Teams-Refine-Estimates-of-Oil-Flow-from-BP-Well-Prior-to-Capping
- **Collection subtracted (bbl/day, linear between knots):** riser insertion tube May 17–25 (22,000 bbl total, i.e. ~2,400/day,
  [DOE/DOI containment slide, June 30](https://www.energy.gov/sites/prod/files/edg/open/documents/6.0_Item_25_Deepwater_Horizon_Containment_-_30_JUN.pdf));
  LMRP cap from June 4: 6,000 first day → ~10,000
  ([PBS](https://www.pbs.org/newshour/science/bp-dome-capturing-more-than-10000-barrels-a-day)) → 15,420 on June 14
  ([Maritime Executive daily update](https://maritime-executive.com/article/deepwater-horizon-update-june-15-2010));
  + Q4000 from June 16: 16,026 + 9,269 = 25,295 on June 17
  ([BP update, archived](https://www.webharvest.gov/congress111th/20101202090543/http://judiciary.house.gov/issues/BPUpdates/BPUpdate100618.pdf)),
  14,730 + 8,020 = 22,750 on June 26
  ([BP update, archived](https://www.webharvest.gov/congress111th/20101202090543/http://judiciary.house.gov/issues/BPUpdates/BPupdate100628.pdf)),
  ~24,000 on June 30 (DOE/DOI slide). **Assumed:** held at 24,000 to July 9, then ~8,000 (Q4000 only, its late-June rate) after the
  LMRP cap was removed on July 10. This schedule integrates to **787,000 bbl** collected, consistent with the DOI's ~800,000.
  Net to the sea: ~62,000 bbl/day in early May, ~30,000 in late June–early July, ~45,000 from July 10.
- **Particle weighting:** a source particle stands for the same volume as a seeded-outline particle. The day-D outline is taken to
  hold the net release of the previous 3 days (an assumption; the surface residence time of DWH oil is not known well). So the
  source count is 2000 × (net release during the 72 h run) / (net release in the 3 days before D), 1,558–2,935 particles per run.
  Each particle's mass is the real net release over the run divided by the count (it only affects weathering; scores use positions).
- **Physics:** both models release the particles at the computed times (OpenOil in 1 h batches through `seed_elements`, ours
  particle by particle). In our model each source particle evaporates by its own oil age and gets background diffusion only
  (K = 10 m²/s, no Fay term). OpenOil handles them like any other element.

## Limitations (read before quoting a number)

- **The wellhead source is a surface release at the wellhead.** The real oil came up from 1,500 m. Part of it stayed at depth
  (deep plumes, subsea dispersant injection) and the rest surfaced near the well after a few hours. The `_src` runs put all net
  release on the surface at the well, and the source-to-seed weighting rests on one assumption (outline D = 3 days of release).
  Without a source (`openoil`, `ours_*`), the runs never release the fresh oil that is in the observed D+k outlines.
- **NESDIS outlines are daily composites of SAR / optical analyses**, with no time of day and changing coverage (cloud, swath).
  All days are taken at 18 UTC. Outline area changes a lot from one day to the next for reasons that are not oil transport, which
  limits IoU / FSS for every method.
- **Position, not thickness.** The model footprint is "any particle in a 5 km cell" and the observed one is "inside the outline".
  Neither carries oil amount. Weathered particles are kept (transport only). Centroids of large, patchy slicks hide shape errors.
- **Liu–Weisberg skill on a slick centroid** is 0 at 24 h for almost every start, because the DWH centroid moves little in a day
  (small Σlᵢ). It is more useful at 48–72 h.
- **The ML is in-sample on DWH.** The shipped ML used 33 DWH-window drifters (88 % of its training fixes). The held-out variant is
  out of sample but then the coverage mask switches it off over the slick, so DWH cannot measure the ML's out-of-sample value on
  oil. Only Main Pass (no drifters in that window) is a true out-of-sample oil check, and there the ML hurts.
- **Texas City Y has no polygons**; two overflight fixes with coordinates (times not reported, noon CDT assumed) and reported
  shoreline oiling are all there is. Galveston Bay is land in the 4 km HYCOM grid, which decides the catalog-seed result. The
  Matagorda Island landfall (Mar 27) is after the cached forcing ends (Mar 26 00 UTC), so landfall timing is not scored.
- **Main Pass has no polygons or fixes** with coordinates, only the direction (SW), length (> 40 mi) and "no shoreline impact".
  The catalog volume (26,000 bbl) is the maximum potential release, not a measurement; it only affects spreading and weathering.
- **One realisation per run** (2000 particles, fixed random seed). No ensembles, so small differences between models are noise.
- DWH seed volume is a fixed 10,000 bbl per outline (affects Fay spreading / weathering only; the score uses positions).
- Forcing is the same reanalysis for all methods, so this compares the models' drift physics, not forecast skill. In
  prediction mode the forcing (RTOFS + GFS) is worse, and errors would be larger.

