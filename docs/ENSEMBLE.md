# Ensemble, probabilities and "build your own model": API contract (T24, A17)

Backend for T25 (A18, 3D visuals + model builder). Everything below is served by the FastAPI backend
(`backend/app/main.py`). Units: lon/lat in degrees (WGS84), times as unix seconds (UTC) plus `h` = hours since the
spill start, speeds in m/s, masses in kg, probabilities in 0..1. Method and equations: `docs/METHODOLOGY.md`.

Only **our model** (`"model": "ours"`) takes custom params and ensembles. OpenOil requests are unchanged.

## 1. `GET /api/model/params` - the model builder schema

```jsonc
{
  "model": "ours",
  "version": "ours-v4",
  "params": [
    {"name": "windage_pct", "label": "Wind drift (windage)", "type": "number", "unit": "% of U10",
     "min": 0, "max": 6, "step": 0.1, "default": 3.0,
     "description": "Share of the 10 m wind speed added to the drift ...",
     "citation": "ASCE (1996) / NOAA GNOME 1-4 % (docs/PROTOCOLS.md FCST-4)", "group": "transport"},
    {"name": "diffusivity_m2s", "type": "number", "unit": "m²/s", "min": 0, "max": 100, "default": 10, ...},
    {"name": "current_factor", "type": "number", "unit": "x", "min": 0, "max": 2, "default": 1, ...},
    {"name": "stokes", "type": "bool", "default": false, ...},
    {"name": "evaporation", "type": "bool", "default": true, ...},
    {"name": "emulsification", "type": "bool", "default": true, ...},
    {"name": "dispersion", "type": "bool", "default": true, ...},
    {"name": "beaching", "type": "bool", "default": true, ...},
    {"name": "ml", "type": "bool", "default": false, ...},
    {"name": "particles", "type": "int", "unit": "particles", "min": 100, "max": 10000, "default": 2000, ...},
    {"name": "horizon_h", "type": "number", "unit": "h", "min": 6, "max": 120, "step": 3, "default": 72, ...}
  ],
  "groups": [{"id": "transport", "label": "Transport"}, {"id": "weathering", "label": "Weathering"},
             {"id": "numerics", "label": "Run"}],
  "ensemble": {
    "max_members": 30, "default_members": 20, "seed_default": 1, "output_step_h": 3, "grid_deg": 0.05,
    "perturbations": [
      {"name": "windage_pct", "dist": "uniform", "low": 1.0, "high": 4.0, "citation": "..."},
      {"name": "wind_rot_deg", "dist": "normal", "sigma": 20, "citation": "..."},
      {"name": "current_factor", "dist": "normal", "mean": 1.0, "sigma": 0.2, "clip": [0.5, 1.5], "citation": "..."},
      {"name": "diffusivity_m2s", "dist": "lognormal", "median": "<user value>", "factor": 2, "citation": "..."}
    ]
  }
}
```
Every param has `name, label, type ("number"|"int"|"bool"), unit, min, max, step, default, description, citation, group`
(bools have `min`/`max` null).

## 2. `POST /api/spill` - new optional fields

```jsonc
{"lat": 28.8, "lon": -94.5, "volume_bbl": 4000, "oil_type": "ifo380", "model": "ours",
 "params": {"windage_pct": 2.5, "stokes": true, "horizon_h": 48},   // any subset; omitted = default
 "ensemble": 20,                                                     // 0 = single run (default), max 30
 "seed": 1}                                                          // ensemble RNG seed (default 1)
```
- `params` with `model != "ours"` -> 400. Unknown name or out-of-range value -> 400 with a message.
- `params.particles` / `params.horizon_h` override `n_particles` / `duration_h`. `params.ml` overrides `ml`.
- Params and the ensemble size/seed go into the run hash: same request = same id = cached result.
- Response (and `GET /api/spill/{id}`) adds:
  ```jsonc
  "params": {...all effective params...},       // only for model=ours
  "ensemble": {"members": 20, "seed": 1, "member_particles": 1000, "done": 7, "failed": 0} // null if ensemble=0
  ```
  Note: the existing top-level `"seed": [lon, lat]` is still the (snapped) spill point; the RNG seed is `ensemble.seed`.
  `progress` (0..1) covers control run + all members + aggregation.
- The id's `/tracks` and `/response` are the **control run** (unperturbed params, full particles), so the existing
  map keeps working. The ensemble adds the products below.

## 3. `GET /api/spill/{id}/probability?t=<h>`

`t` = hours since start (snapped to the nearest 3 h step). Omit `t` to get every step. 202 + status while running,
404 `{"detail": ...}` if the run has no ensemble.

```jsonc
{
  "run_id": "...", "members": 20, "members_ok": 20, "seed": 1, "step_h": 3,
  "t0": 1763800000, "hours": [0, 3, 6, ...], "times": [unix, ...],
  "grid": {"res_deg": 0.05, "lon0": -96.0, "lat0": 27.5, "nx": 80, "ny": 60},   // cell (ix,iy) SW corner = lon0+ix*res, lat0+iy*res
  "steps": [
    {"h": 24, "t": 1763886400,
     "cells": [[ix, iy, p, mass_kg], ...],      // p = P(oil present in cell) = fraction of members with surface oil there;
                                                 // mass_kg = expected surface oil mass in the cell (mean over members)
     "p_max": 0.85,
     "area_km2": {"p10": 820.0, "p50": 310.0, "p90": 95.0},   // area of cells with p >= 0.1 / 0.5 / 0.9
     "surface_kg": {"mean": 1.2e5, "p10": 1.0e5, "p90": 1.4e5},
     "centroid": {
        "mean": [lon, lat],
        "members": [[lon, lat] | null, ...],     // per member, surface-mass-weighted centroid (null: no surface oil)
        "ellipse50": {"center": [lon, lat], "a_km": 12.1, "b_km": 5.3, "angle_deg": 35.0, "polygon": [[lon, lat], ...]},
        "ellipse90": {...}                       // angle_deg: major axis, degrees clockwise from north
     }}
  ],
  "shorelines": [                                 // ESI shoreline segments reached by any member (grouped <= 30 km)
    {"id": 0, "name": "Galveston, TX", "state": "TX", "lon": -94.8, "lat": 29.3, "bbox": [w, s, e, n],
     "esi": "10A", "esi_label": "...", "esi_rank": 10.01, "shoreline_km": 12.4,
     "path": [[lon, lat], ...],                   // ESI cell centres along the segment (for a 3D ribbon)
     "p_oiled": {"24": 0.1, "48": 0.45, "72": 0.6, "end": 0.6},   // P(oil strands on the segment by T h)
     "eta_h": {"p10": 30.5, "p50": 52.0, "p90": null}}            // ETA quantiles over ALL members (null = fewer than
  ],                                                               // that share of members reach it within the horizon)
  "perturbations": [{"member": 0, "windage_pct": 2.1, "wind_rot_deg": -12.0, "current_factor": 1.08,
                     "diffusivity_m2s": 7.4, "ok": true}, ...]
}
```

## 4. `GET /api/spill/{id}/forces`

Works for any finished `ours` run (single or ensemble). Per 3 h step, the **mean over surface particles** (and, for an
ensemble, the mean over members) of each drift term over the preceding 3 h:

```jsonc
{
  "run_id": "...", "source": "ensemble" | "control", "members": 20, "step_h": 3,
  "components": ["current", "wind", "stokes", "ml", "diffusion"],
  "steps": [
    {"h": 3, "t": unix,
     "current": {"u": 0.12, "v": -0.05, "speed": 0.13, "dir_to": 112.6},   // m/s, dir_to = degrees clockwise from N
     "wind":    {...},     // windage x U10 (wind drift of the oil, not the wind itself)
     "stokes":  {...},     // 0 when Stokes is off
     "ml":      {...},     // 0 when ML is off
     "total":   {...},     // vector sum of the above (the advective drift)
     "wind10":  {...},     // the 10 m wind itself (context)
     "diffusion": {"k_m2s": 14.2, "spread_km": 2.1, "speed": 0.03},     // isotropic: no direction.
                                                                          // spread_km = sqrt(4 * integral K dt);
                                                                          // speed = d(spread)/dt over the step
     "budget": {"surface": 0.82, "evaporated": 0.15, "dispersed": 0.02, "stranded": 0.01}  // fractions of released mass
    }
  ],
  "control": {"steps": [...]}    // ensemble runs only: the same for the control run
}
```

## 5. `GET /api/spill/{id}/response` (resp-v4)

Unchanged shape; when the run has an ensemble it adds:
- `ensemble`: `{"members", "seed", "method"}`.
- each `priority_shorelines[i]`: `p_oiled_72h`, `p_oiled` (by 24/48/72/end), `eta_p10_h`, `eta_p50_h`, `eta_p90_h`,
  `esi_weight` (rank/10), `priority_score` = P(oiled within 72 h) x ESI weight. With an ensemble, shorelines are
  **ranked by `priority_score`**; segments only the ensemble reaches are added with `"impact": "ensemble"`.
- each `boom_first[i]`: `deploy_by_h` / `deploy_by` (= P10 ETA: earliest plausible arrival), `eta_p50_h`,
  `lead_time_ok` uses the P10 ETA.
- each `tactics.*.windows[i]`: `p_members` = mean fraction of members whose gates pass over the window; each tactic
  also gets `p_members_by_h` (`[[h, p], ...]` every 3 h).

## 6. `GET /api/models` and `GET /api/backtest`

`/api/models` -> the `ours` entry gets `params_url: "/api/model/params"`, `ensemble: {max_members, ...}` and
`calibration` (ensemble skill on DWH days, see METHODOLOGY.md; null if not computed).

## Status (A17, 2026-11-22)
Implemented and tested on :5230 (`backend/smoke_test.py` runs an 8-member ensemble). Timing on the tower: 20 members x 72 h
~20 s, 8 members ~16 s (process spawn dominates). DWH calibration numbers: docs/METHODOLOGY.md section 7.
