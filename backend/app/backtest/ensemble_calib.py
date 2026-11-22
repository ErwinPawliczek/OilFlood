"""Ensemble calibration on Deepwater Horizon (T24, docs/METHODOLOGY.md "Validation").

For each DWH start day (same starts, seeds and HYCOM forcing as the P5 backtest, `app/backtest/run.py`), run a control
(the user-default params) plus N perturbed members of our model (no ML, transport only: weathered particles stay
drawn), then score against the NOAA/NESDIS observed outline 24/48/72 h later on the 0.05 deg backtest raster:

  coverage_p10  share of observed oiled cells where P(oil) >= 0.1   (the "90 % area": outside it, < 10 % of members)
  coverage_p50  share of observed oiled cells where P(oil) >= 0.5
  coverage_ctrl share of observed oiled cells the single control run touches
  centroid_in90 share of starts whose observed centroid falls inside the 90 % ellipse of member centroids
  area_ratio    area(P >= 0.1) / observed area
  brier         mean (P - observed)^2 over the raster box (and the same for the control's 0/1 field)

Run (from backend/):  python -m app.backtest.ensemble_calib [--members 20] [--starts 12] [--workers 16]
Writes data/backtest/ensemble_calibration.json (served in GET /api/models -> ours.calibration and /api/backtest).
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np

from .. import ensemble
from ..ourmodel import params as MP
from . import run as bt

OUT = bt.OUT_DIR / "ensemble_calibration.json"
RUNS = bt.RUNS / "ensemble"


def _inside_ellipse(pt, cen_pts, q=90) -> bool | None:
    e = ensemble._ellipse(cen_pts, q)
    if e is None:
        return None
    c = cen_pts.mean(axis=0)
    kx = bt.R_EARTH * 1000 * math.pi / 180 * math.cos(math.radians(c[1])) / 1000
    ky = bt.R_EARTH * 1000 * math.pi / 180 / 1000
    xy = np.c_[(cen_pts[:, 0] - c[0]) * kx, (cen_pts[:, 1] - c[1]) * ky]
    cov = np.cov(xy.T) + np.eye(2) * 1e-6
    d = np.array([(pt[0] - c[0]) * kx, (pt[1] - c[1]) * ky])
    return bool(d @ np.linalg.solve(cov, d) <= ensemble.CHI2_2[q])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", type=int, default=20)
    ap.add_argument("--starts", type=int, default=12)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--seed", type=int, default=MP.ENS_SEED)
    a = ap.parse_args()

    cat = bt.catalog()["dwh"]
    starts = bt.dwh_starts(a.starts)
    eff = MP.defaults()
    members = MP.draw_members(a.members, a.seed, eff)
    control = {"member": "c", "windage_pct": eff["windage_pct"], "wind_rot_deg": 0.0, "current_factor": 1.0,
               "diffusivity_m2s": eff["diffusivity_m2s"], "rng_seed": a.seed * 1000 + 999}
    jobs_ = []
    for d in starts:
        g0 = bt.load_outline(d)
        clo, cla = bt.outline_centroid(g0)
        start = datetime.fromisoformat(d) + timedelta(hours=bt.DWH_OBS_HOUR)
        seeds = {}

        def seed_fn(p, g0=g0, rs=int(d.replace("-", "")), seeds=seeds):
            if "s" not in seeds:
                seeds["s"] = bt.sample_in(g0, bt.N_PART, np.random.default_rng(rs), bt.water_fn(p))
            return seeds["s"]
        p = bt.plan_run(cla, clo, start, 72, cat["oil_type"], bt.DWH_SEED_BBL, "ours_noml", seed_fn)
        rd = RUNS / f"{d}_s{a.seed}"
        (rd / "members").mkdir(parents=True, exist_ok=True)
        for m in [control, *members]:
            if not (rd / "members" / f"{m['member']}.npz").exists():
                jobs_.append((str(rd), p, m))
    print(f"{len(starts)} starts, {len(jobs_)} member runs to compute", file=sys.stderr)
    t0 = time.time()
    todo = jobs_
    if todo:
        with ProcessPoolExecutor(max_workers=a.workers, mp_context=mp.get_context("spawn")) as ex:
            futs = [ex.submit(ensemble.run_member, *j) for j in todo]
            for i, f in enumerate(as_completed(futs), 1):
                f.result()
                if i % 20 == 0 or i == len(futs):
                    print(f"  {i}/{len(futs)} ({time.time() - t0:.0f} s)", file=sys.stderr)

    rows = []
    for d in starts:
        rd = RUNS / f"{d}_s{a.seed}" / "members"
        D = datetime.fromisoformat(d)
        obs = [bt.load_outline((D + timedelta(days=k)).strftime("%Y-%m-%d")) for k in (1, 2, 3)]
        mem = [np.load(rd / f"{m['member']}.npz") for m in members]
        ctl = np.load(rd / "c.npz")
        row = {"start": d, "h": []}
        for k, h in enumerate(bt.HORIZONS):
            g = obs[k]

            def pts(z):
                s = (z["state"][h] != 2) & np.isfinite(z["lon"][h])  # transport skill: all particles still in domain
                return z["lon"][h][s], z["lat"][h][s]
            allb = [g.bounds] + [(*(lambda lo, la: (lo.min(), la.min(), lo.max(), la.max()))(*pts(z)),) for z in mem]
            box = bt.raster_box(*allb)
            ob = bt.raster_poly(g, box)
            P = np.mean([bt.raster_pts(*pts(z), box) for z in mem], axis=0)
            C = bt.raster_pts(*pts(ctl), box)
            n_obs = max(int(ob.sum()), 1)
            cen = np.array([[float(np.mean(pts(z)[0])), float(np.mean(pts(z)[1]))] for z in mem])
            oc = bt.outline_centroid(g)
            cell_km2 = (bt.CELL * 111.32 * math.cos(math.radians(oc[1]))) * (bt.CELL * 110.57)
            row["h"].append({
                "h": h, "obs_cells": int(ob.sum()),
                "coverage_p10": round(float((ob & (P >= 0.1)).sum() / n_obs), 3),
                "coverage_p50": round(float((ob & (P >= 0.5)).sum() / n_obs), 3),
                "coverage_ctrl": round(float((ob & C).sum() / n_obs), 3),
                "area_ratio_p10": round(float((P >= 0.1).sum() / n_obs), 2),
                "area_p10_km2": round(float((P >= 0.1).sum() * cell_km2)),
                "brier": round(float(np.mean((P - ob) ** 2)), 4),
                "brier_ctrl": round(float(np.mean((C.astype(float) - ob) ** 2)), 4),
                "centroid_in90": _inside_ellipse(oc, cen, 90),
                "centroid_in50": _inside_ellipse(oc, cen, 50),
                "centroid_err_km": round(float(bt.km(cen[:, 0].mean(), cen[:, 1].mean(), *oc)), 1),
            })
        rows.append(row)

    summ = []
    for k, h in enumerate(bt.HORIZONS):
        rr = [r["h"][k] for r in rows]

        def med(key):
            return round(float(np.median([x[key] for x in rr])), 3)

        def frac(key):
            v = [x[key] for x in rr if x[key] is not None]
            return round(float(np.mean(v)), 3) if v else None
        summ.append({"h": h, "n": len(rr), "coverage_p10_median": med("coverage_p10"),
                     "coverage_p50_median": med("coverage_p50"), "coverage_ctrl_median": med("coverage_ctrl"),
                     "area_ratio_p10_median": med("area_ratio_p10"), "brier_mean": frac("brier"),
                     "brier_ctrl_mean": frac("brier_ctrl"), "centroid_in90_frac": frac("centroid_in90"),
                     "centroid_in50_frac": frac("centroid_in50"), "centroid_err_km_median": med("centroid_err_km")})
    out = {"version": 1, "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ"), "case": "dwh",
           "model": "ours (no ML), transport only", "members": a.members, "seed": a.seed,
           "member_particles": MP.MEMBER_PARTICLES, "cell_deg": bt.CELL, "starts": starts,
           "observed": "NOAA/NESDIS daily DWH surface-oil analyses (data/spills/dwh/footprints), taken at 18 UTC",
           "forcing": "HYCOM-TSIS GOMb0.04 reanalysis currents + its 10 m winds (same as docs/BACKTEST.md)",
           "perturbations": MP.PERTURBATIONS, "summary": summ, "per_start": rows,
           "note": ("A calibrated ensemble would put the observed centroid inside the 90 % ellipse ~90 % of the time "
                    "and inside the 50 % ellipse ~50 %; coverage_p10 is the share of the observed slick inside the "
                    "area reached by >= 10 % of members."),
           "finding": ("The P >= 0.1 area holds ~2x more of the observed slick than one run and the Brier score is ~1/3 "
                       "lower, but the centroid ellipses are badly under-dispersive (the 4 perturbed parameters do not "
                       "sample current-pattern errors): read them as parameter spread, not a 90 % confidence region.")}
    OUT.write_text(json.dumps(out, indent=1))
    for s in summ:
        print(s)
    print("wrote", OUT, file=sys.stderr)


if __name__ == "__main__":
    main()
