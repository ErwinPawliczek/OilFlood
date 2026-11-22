# Methodology: from ocean forecast to probabilities to an action plan

This is how OilFlood turns a live ocean/wind forecast into "where the oil probably goes" and then into a ranked action plan.
Code: `backend/app/ourmodel/sim.py` (model), `ourmodel/params.py` (user params + ensemble design), `app/ensemble.py`
(ensemble + probability estimators), `app/response.py` (action plan). API: `docs/ENSEMBLE.md`. Model details and ML:
`docs/OURMODEL.md`. Response rules: `docs/PROTOCOLS.md`. Backtest: `docs/BACKTEST.md`.

## 1. Forcing (real data only)

| Mode | Currents | Winds |
|---|---|---|
| Prediction (`POST /api/spill`) | NOAA RTOFS global 1/12° forecast (latest cached run) | NCEP GFS 0.25° 10 m wind |
| Hindcast / backtest | HYCOM-TSIS GOMb0.04 reanalysis surface currents | the reanalysis' 10 m wind |

No synthetic fallback: a particle that leaves the forcing domain is frozen as `no_data`; a run with no forcing is rejected.

## 2. The particle model

Each of N Lagrangian particles carries oil mass m. Time step Δt = 15 min, output every 30 min (tracks) and every 1 h
(ensemble members).

**Advection** (RK2 midpoint):

  dx/dt = c · U_c(x, t) + ΔU_ML(x, t) + (α + β_S) · R_θ U_10(x, t)

- U_c: ocean-model surface current; c = **current multiplier** (default 1).
- U_10: 10 m wind; α = **windage** (default 3 %); R_θ rotates the wind field by θ (θ = 0 except in ensemble members).
- β_S = 0.013 when **Stokes** is on (wave Stokes drift ≈ 1.3 % of U10 downwind for developed seas; Ardhuin et al. 2009,
  JPO 39). Off by default because the classic 3 % windage already includes the wave-induced part. With Stokes on, use
  α ≈ 1.5–2 % (OpenOil uses 2 % + Stokes).
- ΔU_ML: experimental drifter-trained correction (`docs/OURMODEL.md`), **off** by default.

**Diffusion**: random walk, per axis Δx = √(2 K Δt) ξ, ξ ~ N(0, 1), with K = K_h + K_Fay(t). K_h = **diffusivity**
(default 10 m²/s, the GNOME/OpenDrift default). K_Fay = (1/8) d(R_Fay²)/dt makes the cloud grow like a Fay (1971) slick
while it is thicker than the terminal thickness.

**Weathering** (each can be switched off):
- Evaporation, Fingas (2004): %Ev = (0.165 %D + 0.045 (T − 15)) ln t (crudes, fuel oils), (0.0254 %D + 0.01 (T − 15)) √t
  (diesel, jet); %D from the ADIOS distillation curve, T = 25 °C.
- Emulsification, Mackay et al. (1980): dY/dt = 2·10⁻⁶ (U + 1)² (1 − Y / Y_max) after the ADIOS onset.
- Natural dispersion, Mackay et al. (1980): fraction/h = 0.11 (U + 1)² / (1 + 50 μ^½ h S).
- **Beaching**: a particle stepping onto GSHHG land or ocean-model land strands (mass → "stranded"). Off: it stays afloat at
  its last water position.

## 3. "Build your own model" params

`GET /api/model/params` serves the schema. Every value goes into the run hash, so the same params give the same cached run.

| Param | Default | Range | Meaning | Source |
|---|---|---|---|---|
| windage_pct | 3 | 0–6 % | α above | ASCE (1996); NOAA GNOME 1–4 % |
| diffusivity_m2s | 10 | 0–100 m²/s | K_h | GNOME / OpenDrift default |
| current_factor | 1 | 0–2 | c | GNOME current scaling |
| stokes | off | | β_S = 0.013 | Ardhuin et al. (2009) |
| evaporation / emulsification / dispersion | on | | weathering terms | Fingas (2004); Mackay et al. (1980) |
| beaching | on | | stranding on GSHHG | Wessel & Smith (1996) |
| ml | off | | ΔU_ML | docs/OURMODEL.md |
| particles | 2000 | 100–10,000 | control-run particles (members: min(·, 1000)) | |
| horizon_h | 72 | 6–120 h | run length (clipped to the forecast) | NOAA FCST-3 |

## 4. Ensemble (Monte-Carlo over the uncertain inputs)

One forecast run hides how uncertain it is. With `ensemble: N` (2–30) the backend runs the **control** (the user's params,
full particles: the map and outline) plus N members. Member k draws, from `numpy.random.default_rng(seed)` (default
seed 1, so the same request gives the same ensemble):

| Input | Draw | Why |
|---|---|---|
| windage α | U(1 %, 4 %) | NOAA's "minimum regret" windage range (PROTOCOLS.md FCST-2/FCST-4) |
| wind direction θ | N(0, 20°), whole field rotated | GFS 10 m direction error; **stated σ**, no GEFS spread is cached |
| current factor c | user c × clip(N(1, 0.2), 0.5, 1.5) | ocean-model surface current speed error (~20–30 %) |
| diffusivity K_h | user K × exp(N(0, ln 2)) | factor-2 uncertainty of sub-grid mixing (Okubo 1971 range) |

Each member has its own random-walk seed (seed·1000 + k), min(particles, 1000) particles, the same oil, start and forcing.
Members run in parallel worker processes (`OILFLOOD_ENS_WORKERS`, default 16): 20 members × 72 h ≈ 20 s on the 32-core
tower (~5 s per member). Total time is capped (`OILFLOOD_ENS_MAX_S`, 600 s); members that fail are reported and left out.

What the ensemble does **not** sample: the forecast's own current *pattern* errors (eddy position), wind speed errors,
the release amount/time, and the oil type. It is a spread over the parameters responders usually argue about, not a full
forecast ensemble (GEFS/RTOFS ensembles are not cached).

## 5. Probability estimators (`GET /api/spill/{id}/probability`)

Let M = members that finished, and for member m at time t the set S_m(t) of its surface particles (afloat, not weathered
away, in domain) with masses w_i.

- **P(oil present) per cell** (0.05° ≈ 5 km grid, every 3 h): p_c(t) = (1/M) Σ_m 1[∃ i ∈ S_m(t) in cell c]. A plain
  member-vote estimator; with M = 20 its resolution is 5 %, and its standard error is √(p(1−p)/M) (≤ 11 % at M = 20).
- **Expected surface mass per cell**: E[m_c(t)] = (1/M) Σ_m Σ_{i ∈ S_m(t) ∩ c} w_i (kg).
- **Areas** `area_km2.p10/p50/p90`: total area of cells with p ≥ 0.1 / 0.5 / 0.9. The p ≥ 0.1 area is the "90 % area":
  outside it fewer than 10 % of members put oil.
- **Centroid path + ellipses**: per member the mass-weighted centroid of S_m(t); across members the sample mean μ and
  covariance Σ (local km frame). The q % ellipse is {x : (x − μ)ᵀ Σ⁻¹ (x − μ) ≤ χ²₂(q)} with χ²₂(50 %) = 1.386,
  χ²₂(90 %) = 4.605 (Gaussian assumption). Reported as semi-axes a, b (km), the major-axis bearing and a polygon.
- **Shoreline segments**: every particle that strands is matched to the nearest NOAA ESI shoreline cell within 6 km (same
  rule as the outline). For each cell and member, the first stranding time; cells are grouped greedily into segments of
  ≤ 30 km (most-hit cells first). For a segment, member m's arrival T_m = min over its cells (∞ if never).
  - **P(oiled by T)** = (1/M) Σ_m 1[T_m ≤ T] for T = 24, 48, 72 h and the horizon.
  - **ETA quantiles** P10/P50/P90: empirical quantiles of {T_m} over **all** members (inverted CDF, members that never arrive
    count as ∞). So P50 is null when fewer than half the members reach the segment: "median arrival" only exists if a
    median member arrives.
- **Forces** (`/forces`): during the run, every step adds the mean over active particles of c·U_c, α·U10, β_S·U10, ΔU_ML
  and U10; each 3 h window reports their averages (ensemble: then averaged over members) as vectors (u, v, speed, bearing).
  Diffusion has no direction: K̄ over the window, the spreading length √(4 ∫K dt) and its rate. Budget = mean fractions of
  the released mass that are surface / evaporated / dispersed / stranded.

## 6. How probabilities drive the action plan (`/response`, resp-v4)

The outline is built from the control run as before (`docs/RESPONSE.md`, protocols in `docs/PROTOCOLS.md`). With an
ensemble:

1. **Priority shorelines** get P(oiled within 72 h), P10/P50/P90 ETA and an ESI weight w = ESI rank / 10 (ESI 10 marsh =
   1.0, ESI 1 exposed rocky = 0.1). They are **ranked by priority score = P72 × w**: a 60 % chance of oiling a salt marsh
   outranks a 90 % chance of oiling a sand beach (0.60 vs 0.27–0.36). Segments that only the ensemble reaches (≥ 5 % of
   members) are added with `impact: "ensemble"`.
2. **Boom first**: every segment with P72 ≥ 10 % (instead of "control run strands ≥ 0.5 % here"). **Deploy-by = the P10
   ETA**, the earliest plausible arrival: if boom is in place by then, it is there in time for 90 % of the members. P50 is
   shown for planning. `lead_time_ok` compares the P10 ETA with the Tier 1 lead time (33 CFR 154.1045).
3. **Tactic windows**: for each hour, the share of members whose gates pass. Wind, daylight, emulsion and oil gates come
   from the forecast at the control slick (they barely change between members); the location gates (dispersant ≥ 3 nm
   AND ≥ 10 m, burn ≥ 3 nm) and "oil on the surface" are evaluated on each member's own centroid. Each window reports
   `p_members`, and `windows_p50` lists the hours where at least half the members pass.
4. The disclaimer stays: planning aid, not operational guidance.

## 7. Validation

**Deterministic skill** (docs/BACKTEST.md, 52 DWH start days, NESDIS outlines): ours without ML has a median 72 h centroid
error of 42 km (OpenOil 44 km), and nothing beats persistence (39 km). So a single run is off by tens of km at 72 h,
which is the reason to show probabilities instead of one line.

**Ensemble calibration** (`python -m app.backtest.ensemble_calib`, `data/backtest/ensemble_calibration.json`, also in
`GET /api/models` → ours.calibration and `GET /api/backtest` → ensemble_calibration): 20 members (seed 1), 12 DWH start days
(2010-05-08 … 2010-07-12, evenly spaced from the 52 backtest starts), ours without ML, transport only, NESDIS outlines on
the 0.05° backtest raster:

| horizon | observed slick inside P ≥ 0.1 area | inside P ≥ 0.5 area | inside single control run | P ≥ 0.1 area / observed area | Brier ensemble | Brier control | obs. centroid in 90 % ellipse | in 50 % ellipse | ens. mean centroid error |
|---|---|---|---|---|---|---|---|---|---|
| 24 h | 75 % | 51 % | 43 % | 2.4 | 0.036 | 0.054 | 8 % | 0 % | 22 km |
| 48 h | 71 % | 39 % | 36 % | 2.2 | 0.040 | 0.059 | 17 % | 0 % | 24 km |
| 72 h | 65 % | 31 % | 32 % | 2.4 | 0.040 | 0.059 | 17 % | 8 % | 35 km |

(medians over start days for coverage, area ratio and centroid error; means for Brier and ellipse hit rates)

What this says:
- **The probability map is more useful than one run.** The P ≥ 0.1 area holds about twice as much of the observed slick
  as the single control run (65–75 % vs 32–43 %) for an area ~2.3× the observed slick, and the Brier score of P(oil)
  is ~32 % lower than the control's 0/1 field (0.040 vs 0.059 at 72 h).
- **The centroid ellipses are badly under-dispersive.** A calibrated 90 % ellipse would contain the observed centroid
  ~90 % of the time; ours does 8–17 %. The four perturbed parameters spread the member centroids by a few km while the
  real error is 22–35 km. That error comes mostly from the current field itself (eddy/front position) and from comparing
  particles to a satellite outline, neither of which the ensemble samples. So read the ellipses as "spread due to
  windage/wind/current-speed/mixing uncertainty", **not** as a 90 % confidence region for the slick centre.
- The response plan therefore ranks by P(oiled) but keeps the deterministic uncertainty envelope and the NOAA advice to
  re-run daily and re-tune to overflights (FCST-3).

## 8. Limits

- The ensemble samples four parameters, not the forecast's structural errors; on DWH its centroid spread is far too small (8–17 % of observed centroids inside the 90 % ellipse, section 7):
  treat P = 0 as "not in this ensemble", not "impossible".
- M = 20 members: probabilities are quantised to 5 % with up to ±11 % sampling error.
- The wind-direction σ is a stated value (no GEFS spread is cached), and the 20 % current error is a stated value.
- Probability of oiling counts stranding on the model coastline (GSHHG / HYCOM land, ~1–4 km) matched to ESI cells
  within 6 km; bays and inlets are not resolved.
- Tactic-window fractions keep the control's wind/emulsion at each hour; only location gates vary per member.
- Stokes drift is a wind-based parameterisation (no wave model); turning it on without lowering windage double-counts
  wave drift.
- No subsurface oil, no refloating, fixed 25 °C water.

## References

- Ardhuin, F., Marié, L., Rascle, N., Forget, P., Roland, A. (2009). Observation and estimation of Lagrangian, Stokes, and
  Eulerian currents induced by wind and waves at the sea surface. J. Phys. Oceanogr. 39, 2820–2838.
- ASCE Task Committee on Modeling of Oil Spills (1996). State-of-the-art review of modeling transport and fate of oil
  spills. J. Hydraulic Eng. 122(11), 594–609.
- Fay, J. A. (1971). Physical processes in the spread of oil on a water surface. Proc. Joint Conf. Prevention and Control
  of Oil Spills, 463–467.
- Fingas, M. (2004). Modeling evaporation using models that are not boundary-layer regulated. J. Hazard. Mater. 107, 27–36.
- Liu, Y., Weisberg, R. H. (2011). Evaluation of trajectory modeling in different dynamic regions using normalized
  cumulative Lagrangian separation. J. Geophys. Res. 116, C09013.
- Mackay, D., Buist, I., Mascarenhas, R., Paterson, S. (1980). Oil spill processes and models. Environment Canada
  EPS 3-EC-80-8.
- NOAA ORR. GNOME technical documentation (Zelenke, Barker, Beegle-Krause, et al. 2012, NOAA Tech. Memo NOS OR&R 40).
- Okubo, A. (1971). Oceanic diffusion diagrams. Deep-Sea Research 18, 789–802.
- Wessel, P., Smith, W. H. F. (1996). A global, self-consistent, hierarchical, high-resolution shoreline database.
  J. Geophys. Res. 101(B4), 8741–8743.
