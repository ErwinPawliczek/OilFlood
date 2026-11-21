# OilFlood's own oil-spill model ("ours")

Code: `backend/app/ourmodel/` (`sim.py` model, `grid.py` forcing sampler, `ml.py` + `train.py` ML drift correction).
API: `POST /api/spill` with `"model": "ours"` (default `"openoil"`), hindcasts `GET /api/spills/{id}/tracks?model=ours`,
`"ml": false` turns the ML term off. Status and `tracks.meta` carry `model` / `model_label`. `GET /api/models`
lists both models with the ML hold-out scores. The panel's **OpenOil / Ours / Compare** toggle shows either model or both
(OpenOil amber, ours cyan, budgets side by side) in prediction and historical modes.

Both models run on the same files: the live RTOFS + GFS forecast for predictions, and HYCOM-TSIS reanalysis currents + winds
for hindcasts (`jobs.plan` picks them, snaps the seed off model land and clips the duration). They also start from the same
oil record (ADIOS, via OpenOil's database), the same particle count, the same output format and the same seed point.

## Physics (per particle, dt = 15 min, output every 30 min)

| Process | Equation | Notes |
|---|---|---|
| Advection | **dx/dt = U_c + ΔU_ML + α·U_10**, RK2 midpoint | U_c ocean-model surface current, U_10 10 m wind, α = 0.03 (the classic 3 % rule; it includes wave/Stokes drift because there is no wave model). OpenOil baseline: 2 % wind + parameterised Stokes. |
| Diffusion | random walk, σ = √(2 K Δt) per axis, K = K_h + K_Fay(t) | K_h = 10 m²/s (same as the baseline). |
| Spreading | Fay (1971): gravity–inertia R = 1.14 (Δ g V₀ t²)^¼, gravity–viscous R = 1.45 (Δ g V₀² t^1.5 / ν_w^½)^⅙ (smaller of the two); **K_Fay = (1/8) d(R²)/dt** | A disc of radius R has per-axis variance R²/4, so this diffusivity grows the cloud like a Fay slick. Δ = (ρ_w − ρ_oil)/ρ_w, ν_w = 1e-6 m²/s. Stops at terminal thickness (10 µm, 100 µm for oils > 100 cSt). |
| Evaporation | Fingas (2004): %Ev = (0.165 %D + 0.045 (T−15)) ln t for crudes, fuel oils, gasoline, condensate; %Ev = (0.0254 %D + 0.01 (T−15)) √t for diesel/jet fuel (t in minutes) | %D = mass % distilled at 180 °C from the ADIOS distillation cuts; capped at the mass fraction boiling below 300 °C. T = 25 °C (no SST in the forcing). |
| Emulsification | Mackay et al. (1980): dY/dt = 2·10⁻⁶ (U+1)² (1 − Y/Y_max) | Starts at the ADIOS onset (evaporated fraction ≥ "bullwinkle"); Y_max from ADIOS (0 for IFO/diesel/gasoline). |
| Viscosity | μ = μ₀ exp(5 F_e) · exp(2.5 Y / (1 − 0.654 Y)) | Mackay evaporation term + Mooney emulsion term. |
| Natural dispersion | Mackay et al. (1980): fraction/h = 0.11 (U+1)² / (1 + 50 μ^½ h S) | μ in cP, h slick thickness in cm (V/πR², ≥ terminal), S oil–water surface tension (dyne/cm, ADIOS). |
| Beaching | A particle whose step ends on GSHHG land (`roaring_landmask`, the same coastline OpenDrift uses) or on ocean-model land is **stranded** at its last water position; its remaining mass goes to "stranded". | Currents are sampled bilinearly over the water corners only, so particles next to the model coast still move. No refloating. |
| Out of domain | leaves the forcing box → `no_data` (frozen), like the baseline. | |

Particles carry mass. Evaporation and dispersion remove mass from each surface particle. Each particle also gets a random
threshold u ~ U(0,1) and stops being drawn once its lost-mass fraction passes u (end status `evaporated` or `dispersed`,
whichever took more). So the number of drawn particles follows the surface budget.

## ML drift correction

**Goal:** learn the part of the drift that the ocean model misses, from real drifter tracks, using only fields the model has at
run time.

- **Data:** NOAA AOML **Global Drifter Program hourly QC** (`drifter_hourly_qc` on erddap.aoml.noaa.gov), all fixes in the
  Gulf box (18–31 N, 98–80 W) inside every cached hindcast forcing window. Only 3 windows have drifters:
  DWH 2010-04-20..07-16 (33 drifters, 12,415 fixes), MC 209 2017 (6 drifters, 1,298 fixes) and Eugene Island 2009 (3 drifters, 376 fixes).
  The DBL 152, Main Pass and Texas City Y windows have none. Total: **42 drifters, 14,089 hourly fixes**, 64 % drogued.
  The raw CSVs are cached in `data/cache/gdp/` (gitignored).
- **Forcing at each fix:** HYCOM-TSIS GOMb0.04 reanalysis surface currents + its 10 m winds (the hindcast forcing files),
  sampled with the same `grid.Grid` the model uses.
- **Target:** r = v_drifter − U_c (m/s, east and north). v_drifter = 6 h centred mean of the GDP velocities, which smooths out
  tides and fix noise.
- **Features:** U_c (u, v), U_10 (u, v), |U_c|, |U_10|, current vorticity and divergence (centred differences on the model grid),
  drogue flag (1 = 15 m drogue attached, 0 = lost, from `drogue_lost_date`).
- **Model:** two scikit-learn `HistGradientBoostingRegressor`s (u, v): 300 iterations, learning rate 0.05, 15 leaves,
  min 200 samples per leaf, L2 = 1. The model is 434 KB (`drift_model.joblib`). The card with all scores is `drift_model.json`.
- **Use in the oil model:** ΔU_ML = ½ [f(x, drogued=1) + f(x, drogued=0)] · w(lon, lat).
  - The drogue average cancels the wind-correlated part. Projected on the wind, the drogued prediction is −0.7 %, the undrogued +0.7 % and the mean 0.0 %, so the oil's own 3 % windage is not counted twice.
  - w is the **training-coverage mask**: 1 in 0.5° cells whose 3×3 neighbourhood holds ≥ 24 training fixes, fading linearly to 0 below that (360 of 936 Gulf cells). Outside the cells drifters visited (most of the shallow shelf), our model is physics only and does not extrapolate deep-water corrections.
  - `tracks.meta.ml_coverage_fraction` reports the share of particle-steps that got the correction.

### Hold-out skill (5-fold cross-validation grouped by drifter: a drifter is never in train and test)

Velocity RMSE of the residual (m/s), out-of-fold:

| | rows | currents only | currents + best constant windage* | currents + ML |
|---|---|---|---|---|
| all | 14,089 | 0.410 | 0.414 | **0.358** |
| drogued | 9,034 | 0.424 | 0.432 | **0.360** |
| undrogued | 5,055 | 0.384 | 0.381 | **0.353** |

\* windage fitted on the training folds' undrogued drifters (0.7–1.3 % of the wind).

Trajectory test: virtual drifters were released every 12 h along each held-out track and advected 24 h / 48 h (RK2, 30 min) through the
reanalysis. The table gives the separation from where the real drifter went. The ML runs use the coverage mask built from the training folds only. Only releases that stayed in model water for every method are counted.

| separation (km) | n | currents only (median / mean) | + windage | **+ ML** | ML closer |
|---|---|---|---|---|---|
| 24 h, all | 1,120 | 21.2 / 25.4 | 21.8 / 26.0 | **18.1 / 22.0** | 55 % |
| 48 h, all | 1,038 | 38.0 / 48.1 | 40.5 / 49.3 | **34.5 / 43.4** | 55 % |
| 24 h, undrogued | 405 | 21.1 / 24.9 | 20.6 / 24.4 | **18.8 / 22.6** | 49 % |
| 48 h, undrogued | 381 | 40.1 / 49.0 | 41.0 / 47.6 | **35.9 / 44.0** | 52 % |

"ML closer" = share of releases where the ML track ends nearer the drifter than the currents-only track. Releases outside the training-fold coverage get no correction and count as ties. So the gain is in fewer large misses (mean and median), not in winning most pairs.

### Limitations (read before trusting it)

- **Small, one-event training set.** 88 % of the fixes are from the DWH summer of 2010, mostly in deep water. A subset is all the
  cached forcing allows (GDP fixes have to be paired with reanalysis forcing on the same dates). More windows of HYCOM-TSIS
  forcing would give more drifters. See "Retrain".
- **Forcing mismatch in prediction mode.** The correction was learned against HYCOM-TSIS reanalysis errors but is applied to
  the live RTOFS forecast, whose errors are larger and different. No skill is claimed for RTOFS.
- **Spill check (qualitative):** at Main Pass 2023 (observed: slick moved SW, no shoreline impact) OpenOil and ours *without* ML
  both drift SW with 0 % stranded. Ours *with* ML (30 % of particle-steps in coverage) drifts NE and strands 35 %, so there the ML
  makes it worse. At MC 209 2017 both models drift W/SW, as the response-time trajectories in the notes did. A real backtest
  against the DWH NESDIS outlines is still to do (P5).
- Mackay dispersion is known to overstate dispersion of thin, light slicks. Diesel disperses ~90 % in 72 h here vs ~44 % in
  OpenOil, while the heavy oils are close to OpenOil. Water temperature is a fixed 25 °C. No subsurface oil, no refloating and no
  separate Stokes drift.

## Retrain

```bash
cd backend
~/miniforge3/envs/oilflood/bin/python -m app.ourmodel.train            # ~1 min on 32 cores; --refetch re-downloads GDP
```
It scans `data/cache/forcing/<window>/` (all hindcast windows, not `live`), downloads GDP fixes for each window (cached),
runs the grouped CV, prints the scores, and writes `drift_model.joblib`, `drift_coverage.npz` and `drift_model.json` next to
the code. Commit these three files (≈ 450 KB) and bump `VERSION` in `sim.py` so cached runs are recomputed. To add training
data, fetch more reanalysis windows with `data/fetch_hycom.py` (any Gulf dates with drifters, e.g. 2012 or 2015–2016) and re-run.
