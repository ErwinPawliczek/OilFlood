"""Backtest OpenOil, ours (+ML) and ours (no ML) against observed spills (docs/BACKTEST.md).

  cd backend && ~/miniforge3/envs/oilflood/bin/python -m app.backtest [--workers 12] [--only dwh] [--max-starts N]

Runs are cached in backend/runs/backtest/<hash>/ (gitignored); the scores go to data/backtest/results.json
(small, committed). Re-running only scores again unless the inputs change.

Cases
- dwh: seed 2000 particles uniformly inside the NOAA/NESDIS outline of day D, run 72 h on the HYCOM-TSIS
  hindcast, compare with the outlines of D+1, D+2, D+3. Every D in the forcing window with all three
  following outlines is used. Persistence (outline D unchanged) is scored as a reference.
- texas_city_y: no observed polygons. Score against the two overflight positions with coordinates in
  the catalog (IncidentNews) and the reported Galveston-area shoreline oiling; Matagorda landfall (Mar 27) is
  after the cached forcing ends (Mar 26), so it cannot be scored.
- main_pass_2023: no polygons. Observed: slick moved SW (>40 mi long on Nov 17), no shoreline impact. Score the
  centroid bearing, slick length and stranded fraction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .. import config, jobs, runner

OUT_DIR = config.REPO_ROOT / "data" / "backtest"
RUNS = config.RUNS_DIR / "backtest"
FOOT = config.REPO_ROOT / "data" / "spills" / "dwh" / "footprints"
CATALOG = config.REPO_ROOT / "data" / "spills" / "catalog.json"
VERSION = "bt-v1"

MODELS = {
    "openoil": {"label": "OpenOil", "model": "openoil"},
    "ours_ml": {"label": "Ours + ML", "model": "ours", "ml": True},
    "ours_noml": {"label": "Ours (no ML)", "model": "ours", "ml": False},
}
# DWH only: the shipped ML was trained on drifters that are 88 % from the DWH window, so it is not out of sample
# there. This variant retrains it with the DWH window left out (MC 209 2017 + Eugene Island 2009 drifters only).
ML_NODWH_DIR = RUNS / "ml_nodwh"
DWH_EXTRA = {"ours_ml_nodwh": {"label": "Ours + ML (DWH drifters held out)", "model": "ours", "ml": True,
                               "ml_dir": str(ML_NODWH_DIR)},
             # DWH only: the same runs plus the continuous wellhead release (dwh_source.py, docs/BACKTEST.md)
             "openoil_src": {"label": "OpenOil + wellhead source", "model": "openoil", "source": True},
             "ours_ml_src": {"label": "Ours + ML + wellhead source", "model": "ours", "ml": True, "source": True},
             "ours_noml_src": {"label": "Ours (no ML) + wellhead source", "model": "ours", "ml": False,
                               "source": True}}
SRC_PAIRS = {"openoil_src": "openoil", "ours_ml_src": "ours_ml", "ours_noml_src": "ours_noml"}
ALL_MODELS = {**MODELS, **DWH_EXTRA}
HORIZONS = (24, 48, 72)
N_PART = 2000
CELL = 0.05           # raster cell (deg) for IoU / FSS (~5 km)
FSS_WIN = 5           # FSS neighbourhood in cells (~25 km)
DWH_OBS_HOUR = 18     # NESDIS daily composites have no time; all days are taken at 18 UTC (differences matter)
DWH_SEED_BBL = 10_000  # surface oil represented by a seeded outline (only affects Fay spreading / weathering)
R_EARTH = 6371.0


# --- geometry helpers ------------------------------------------------------------------------------------------

def km(lo1, la1, lo2, la2):
    lo1, la1, lo2, la2 = (np.radians(np.asarray(a, float)) for a in (lo1, la1, lo2, la2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def bearing(lo1, la1, lo2, la2) -> float:
    """Compass bearing (deg, 0 = N, 90 = E) from point 1 to point 2."""
    p1, p2 = math.radians(la1), math.radians(la2)
    dl = math.radians(lo2 - lo1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def load_outline(day: str):
    import shapely
    from shapely.geometry import shape
    p = FOOT / f"dwh_{day.replace('-', '')}.geojson"
    if not p.exists():
        return None
    g = json.loads(p.read_text())
    geoms = [shapely.make_valid(shape(f["geometry"])) for f in g["features"] if f.get("geometry")]
    if not geoms:
        return None
    g = shapely.make_valid(shapely.union_all(geoms))
    shapely.prepare(g)  # fast contains_xy for sampling / rasterising
    return g


def outline_centroid(geom) -> tuple[float, float]:
    """Area-weighted centroid in a local equal-area-ish frame (lon scaled by cos lat)."""
    import shapely
    from shapely import affinity
    lat0 = geom.centroid.y
    c = math.cos(math.radians(lat0))
    g2 = affinity.scale(geom, xfact=c, yfact=1.0, origin=(0, 0))
    cc = g2.centroid
    return cc.x / c, cc.y


def sample_in(geom, n: int, rng, ok_fn) -> tuple[np.ndarray, np.ndarray]:
    """n points uniform inside geom that pass ok_fn(lon, lat) -> bool mask (water + model current)."""
    import shapely
    x0, y0, x1, y1 = geom.bounds
    lo_out, la_out = [], []
    have = 0
    for _ in range(200):
        m = max(4 * (n - have), 2000)
        lo = rng.uniform(x0, x1, m)
        la = rng.uniform(y0, y1, m)
        keep = shapely.contains_xy(geom, lo, la)
        lo, la = lo[keep], la[keep]
        if len(lo):
            k2 = ok_fn(lo, la)
            lo, la = lo[k2], la[k2]
        lo_out.append(lo)
        la_out.append(la)
        have += len(lo)
        if have >= n:
            break
    lo, la = np.concatenate(lo_out)[:n], np.concatenate(la_out)[:n]
    if len(lo) < n:
        raise RuntimeError("could not sample enough water points in outline")
    return lo, la


# --- rasters ---------------------------------------------------------------------------------------------------

def raster_box(*boxes, pad=0.5):
    x0 = min(b[0] for b in boxes) - pad
    y0 = min(b[1] for b in boxes) - pad
    x1 = max(b[2] for b in boxes) + pad
    y1 = max(b[3] for b in boxes) + pad
    nx, ny = int(math.ceil((x1 - x0) / CELL)), int(math.ceil((y1 - y0) / CELL))
    return x0, y0, nx, ny


def raster_poly(geom, box) -> np.ndarray:
    import shapely
    x0, y0, nx, ny = box
    xs = x0 + (np.arange(nx) + 0.5) * CELL
    ys = y0 + (np.arange(ny) + 0.5) * CELL
    X, Y = np.meshgrid(xs, ys)
    return shapely.contains_xy(geom, X.ravel(), Y.ravel()).reshape(ny, nx)


def raster_pts(lo, la, box) -> np.ndarray:
    x0, y0, nx, ny = box
    ix = np.floor((np.asarray(lo) - x0) / CELL).astype(int)
    iy = np.floor((np.asarray(la) - y0) / CELL).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    r = np.zeros((ny, nx), bool)
    r[iy[ok], ix[ok]] = True
    return r


def iou(a: np.ndarray, b: np.ndarray) -> float:
    u = (a | b).sum()
    return float((a & b).sum() / u) if u else 0.0


def fss(a: np.ndarray, b: np.ndarray, win: int = FSS_WIN) -> float:
    """Fractions skill score (Roberts & Lean 2008) of two binary fields at a win x win neighbourhood."""
    from scipy.ndimage import uniform_filter
    fa = uniform_filter(a.astype(float), win, mode="constant")
    fb = uniform_filter(b.astype(float), win, mode="constant")
    den = (fa ** 2).sum() + (fb ** 2).sum()
    return float(1 - ((fa - fb) ** 2).sum() / den) if den else 0.0


def lw_skill(sep: list[float], cum_len: list[float]) -> float:
    """Liu & Weisberg (2011) skill, tolerance n = 1: s = max(0, 1 - sum(d_i) / sum(l_i))."""
    L = sum(cum_len)
    if L <= 0:
        return 0.0
    return max(0.0, 1.0 - sum(sep) / L)


# --- model output -> particle positions at a time ---------------------------------------------------------------

def positions(tracks: dict, t: int):
    """(lon, lat, stranded) of every particle at unix time t. A path that ended earlier (stranded,
    evaporated, left the domain) keeps its last position. Stranded = path frozen from some step on and
    status_end == stranded (our model keeps recording stranded particles at their beach position)."""
    lo, la, st = [], [], []
    for p in tracks["particles"]:
        ts = p["timestamps"]
        if not ts or ts[0] > t:
            continue
        j = int(np.searchsorted(ts, t, side="right")) - 1
        x, y = p["path"][j]
        lo.append(x)
        la.append(y)
        s = False
        if p["status_end"] == "stranded":
            path = p["path"]
            k = len(path) - 1
            while k > 0 and path[k - 1] == path[-1]:
                k -= 1
            s = ts[k] <= t
        st.append(s)
    return np.array(lo), np.array(la), np.array(st, bool)


# --- runs ------------------------------------------------------------------------------------------------------

_PLAN_CACHE: dict = {}


def plan_run(lat, lon, start: datetime, hours: float, oil: str, volume_bbl: float, model_key: str,
             seed_fn=None) -> dict:
    m = ALL_MODELS[model_key]
    key = (lat, lon, start, hours, oil, volume_bbl)
    if key not in _PLAN_CACHE:  # jobs.plan opens the forcing (~5 s); same for every model
        req = jobs.SpillRequest(lat=lat, lon=lon, start_time=start, duration_h=hours, volume_bbl=volume_bbl,
                                oil_type=oil, n_particles=N_PART)
        _PLAN_CACHE[key] = jobs.plan(jobs._normalize(req), config.SNAP_KM_HINDCAST)
    p = json.loads(json.dumps(_PLAN_CACHE[key]))
    if m["model"] == "ours":  # what jobs._normalize adds for our model
        p.update(model="ours", ml=bool(m.get("ml", True)))
    if m["model"] == "ours":
        p["record_all"] = True
    if m.get("ml_dir"):
        p["ml_dir"] = m["ml_dir"]
    if seed_fn is not None:
        slo, sla = seed_fn(p)
        p["seed_lon"], p["seed_lat"] = [round(float(x), 5) for x in slo], [round(float(x), 5) for x in sla]
        p["n_particles"] = len(slo)
    if m.get("source"):
        from .dwh_source import source_for
        p["source"] = source_for(start, hours, int(p["n_particles"]))
    return p


def run_key(p: dict) -> str:
    v = jobs.MODEL_VERSION if p.get("model", "openoil") == "openoil" else jobs.OURS_VERSION
    return hashlib.sha1(json.dumps({**p, "v": v, "bt": VERSION}, sort_keys=True).encode()).hexdigest()[:16]


def execute(params: list[dict], workers: int) -> list[dict]:
    """Run (or reuse) every planned run; returns tracks dicts in the same order."""
    RUNS.mkdir(parents=True, exist_ok=True)
    todo = []
    for p in params:
        d = RUNS / run_key(p)
        if not (d / "tracks.json").exists():
            d.mkdir(parents=True, exist_ok=True)
            (d / "request.json").write_text(json.dumps(p))
            todo.append((str(d), p))
    print(f"{len(params)} runs, {len(todo)} to compute", file=sys.stderr)
    if todo:
        t0 = time.time()
        # One OpenMP thread per worker: the ML (HistGradientBoosting) predict otherwise starts a thread per core
        # in every worker and the oversubscribed pool crawls.
        os.environ["OMP_NUM_THREADS"] = "1"
        with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn")) as ex:
            futs = [ex.submit(runner.run_spill, d, p) for d, p in todo]
            for i, f in enumerate(as_completed(futs), 1):
                f.result()
                if i % 10 == 0 or i == len(futs):
                    print(f"  {i}/{len(futs)} done ({time.time() - t0:.0f} s)", file=sys.stderr)
    out = []
    for p in params:
        d = RUNS / run_key(p)
        tp = d / "tracks.json"
        if not tp.exists():
            st = json.loads((d / "status.json").read_text()) if (d / "status.json").exists() else {}
            out.append({"error": st.get("error", "no output")})
        else:
            out.append(json.loads(tp.read_text()))
    return out


def water_fn(p: dict):
    """Seed filter: GSHHG water and finite model current at the start time (both models then start in water)."""
    from roaring_landmask import RoaringLandmask

    from ..ourmodel.grid import Grid
    cur_f = next(f for f in p["forcing_files"] if "current" in f["kinds"])
    start = datetime.fromisoformat(p["start_time"])
    g = Grid(cur_f["path"], "current", (p["lon"] - 6, p["lat"] - 5, p["lon"] + 6, p["lat"] + 5),
             start, start + timedelta(hours=6))
    land = RoaringLandmask.new()
    t = float(np.datetime64(start, "s").astype(np.int64))

    def ok(lo, la):
        u, _ = g.sample(t, lo, la)
        return np.isfinite(u) & ~land.contains_many(lo.astype(np.float64), la.astype(np.float64))
    return ok


# --- cases -----------------------------------------------------------------------------------------------------

def catalog() -> dict:
    return {s["id"]: s for s in json.loads(CATALOG.read_text())}


def dwh_starts(max_starts: int | None) -> list[str]:
    days = sorted(f.stem[4:] for f in FOOT.glob("dwh_*.geojson"))
    have = {f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days}
    from .. import forcing
    cur = [f for f in forcing.scan() if "current" in f.kinds and f.t0 and f.t0.year == 2010]
    end = max(f.t1 for f in cur)
    out = []
    for d in sorted(have):
        D = datetime.fromisoformat(d)
        nxt = [(D + timedelta(days=k)).strftime("%Y-%m-%d") for k in (1, 2, 3)]
        if all(n in have for n in nxt) and D + timedelta(days=3, hours=DWH_OBS_HOUR) <= end:
            out.append(d)
    if max_starts and len(out) > max_starts:
        idx = np.linspace(0, len(out) - 1, max_starts).round().astype(int)
        out = [out[i] for i in idx]
    return out


def case_dwh(workers: int, max_starts: int | None) -> dict:
    cat = catalog()["dwh"]
    starts = dwh_starts(max_starts)
    plans, meta = [], []
    for d in starts:
        g0 = load_outline(d)
        clo, cla = outline_centroid(g0)
        start = datetime.fromisoformat(d) + timedelta(hours=DWH_OBS_HOUR)
        rng_seed = int(d.replace("-", ""))
        seeds = {}

        def seed_fn(p, g0=g0, rng_seed=rng_seed, seeds=seeds):
            if "s" not in seeds:  # same seed points for all three models
                seeds["s"] = sample_in(g0, N_PART, np.random.default_rng(rng_seed), water_fn(p))
            return seeds["s"]
        for mk in ALL_MODELS:
            plans.append(plan_run(cla, clo, start, 72, cat["oil_type"], DWH_SEED_BBL, mk, seed_fn))
            meta.append((d, mk))
    tracks = execute(plans, workers)

    per_start = []
    by = {(d, mk): (p, tr) for (d, mk), p, tr in zip(meta, plans, tracks)}
    for d in starts:
        D = datetime.fromisoformat(d)
        obs = [load_outline((D + timedelta(days=k)).strftime("%Y-%m-%d")) for k in (0, 1, 2, 3)]
        oc = [outline_centroid(g) for g in obs]
        steps = [float(km(oc[k - 1][0], oc[k - 1][1], oc[k][0], oc[k][1])) for k in (1, 2, 3)]
        cum = list(np.cumsum(steps))
        row = {"start": d, "obs_area_km2": [round(_area_km2(g)) for g in obs],
               "obs_centroid": [[round(x, 4), round(y, 4)] for x, y in oc],
               "obs_centroid_path_km": [round(c, 1) for c in cum], "models": {}}
        # persistence reference
        pers = {"sep_km": [], "iou": [], "fss": []}
        for k in (1, 2, 3):
            pers["sep_km"].append(round(float(km(oc[0][0], oc[0][1], oc[k][0], oc[k][1])), 1))
            box = raster_box(obs[0].bounds, obs[k].bounds)
            a, b = raster_poly(obs[0], box), raster_poly(obs[k], box)
            pers["iou"].append(round(iou(a, b), 3))
            pers["fss"].append(round(fss(a, b), 3))
        pers["lw"] = [round(lw_skill(pers["sep_km"][:k], cum[:k]), 3) for k in (1, 2, 3)]
        row["models"]["persistence"] = pers
        for mk in ALL_MODELS:
            p, tr = by[(d, mk)]
            if "error" in tr:
                row["models"][mk] = {"error": tr["error"]}
                continue
            t0 = tr["times"][0]
            r = {"sep_km": [], "iou": [], "fss": [], "stranded": [], "centroid": []}
            for k, h in enumerate(HORIZONS, 1):
                lo, la, st = positions(tr, t0 + h * 3600)
                mc = (float(lo.mean()), float(la.mean()))
                r["centroid"].append([round(mc[0], 4), round(mc[1], 4)])
                r["sep_km"].append(round(float(km(mc[0], mc[1], oc[k][0], oc[k][1])), 1))
                box = raster_box(obs[k].bounds, (lo.min(), la.min(), lo.max(), la.max()))
                a, b = raster_pts(lo, la, box), raster_poly(obs[k], box)
                r["iou"].append(round(iou(a, b), 3))
                r["fss"].append(round(fss(a, b), 3))
                r["stranded"].append(round(float(st.mean()), 3))
            r["lw"] = [round(lw_skill(r["sep_km"][:k], cum[:k]), 3) for k in (1, 2, 3)]
            if p.get("source"):
                r["source_particles"] = p["source"]["n_particles"]
            if "ml_coverage_fraction" in tr.get("meta", {}):
                r["ml_coverage"] = tr["meta"]["ml_coverage_fraction"]
            row["models"][mk] = r
        per_start.append(row)

    names = ["persistence", *ALL_MODELS]
    summary = {}
    for mk in names:
        rows = [r["models"][mk] for r in per_start if "error" not in r["models"].get(mk, {"error": 1})]
        s = {"n": len(rows)}
        for met in ("sep_km", "lw", "iou", "fss"):
            arr = np.array([r[met] for r in rows], float)
            s[met + "_median"] = [round(float(x), 3) for x in np.median(arr, 0)]
            s[met + "_mean"] = [round(float(x), 3) for x in arr.mean(0)]
        if mk != "persistence":
            pr = np.array([r["models"]["persistence"]["sep_km"] for r in per_start
                           if "error" not in r["models"].get(mk, {"error": 1})])
            s["beats_persistence_sep"] = [round(float(x), 3) for x in (np.array([r["sep_km"] for r in rows]) < pr).mean(0)]
            s["stranded_mean"] = [round(float(x), 3) for x in np.array([r["stranded"] for r in rows]).mean(0)]
            if mk.startswith("ours_ml"):
                s["ml_coverage_mean"] = round(float(np.mean([r.get("ml_coverage", 0) for r in rows])), 3)
        summary[mk] = s
    # paired: ML vs no ML separation (share of start days where the ML run's centroid ends closer)
    for mk, key in (("ours_ml", "ml_closer_share"), ("ours_ml_nodwh", "ml_nodwh_closer_share")):
        pairs = [(r["models"][mk]["sep_km"], r["models"]["ours_noml"]["sep_km"]) for r in per_start
                 if "error" not in r["models"][mk] and "error" not in r["models"]["ours_noml"]]
        if pairs:
            a = np.array([x for x, _ in pairs])
            b = np.array([y for _, y in pairs])
            summary[key] = [round(float(x), 3) for x in (a < b).mean(0)]
    # paired: with vs without the wellhead source (share of start days where the source run ends closer)
    src_closer = {}
    for mk, base in SRC_PAIRS.items():
        pairs = [(r["models"][mk]["sep_km"], r["models"][base]["sep_km"]) for r in per_start
                 if "error" not in r["models"][mk] and "error" not in r["models"][base]]
        if pairs:
            a = np.array([x for x, _ in pairs])
            b = np.array([y for _, y in pairs])
            src_closer[mk] = [round(float(x), 3) for x in (a < b).mean(0)]
    if src_closer:
        summary["source_closer_share"] = src_closer
    return {
        "id": "dwh", "name": cat["name"], "kind": "outlines",
        "observed": "NOAA/NESDIS daily satellite oil outlines (data/spills/dwh/footprints)",
        "method": (f"Seed {N_PART} particles uniformly inside the outline of day D (water points only), run 72 h "
                   f"from D {DWH_OBS_HOUR}:00 UTC on HYCOM-TSIS reanalysis currents + winds, compare with the "
                   f"outlines of D+1/D+2/D+3. Persistence = outline D unchanged."),
        "starts": starts, "horizons_h": list(HORIZONS), "summary": summary, "per_start": per_start,
        "source": _source_meta(),
    }


def _source_meta() -> dict:
    from . import dwh_source as ds
    t, r = ds.net_knots()
    return {"lon": ds.WELL_LON, "lat": ds.WELL_LAT, "gross": ds.GROSS, "containment": ds.CONTAINMENT,
            "net_rate_t": t, "net_rate_bbl_day": [round(x) for x in r], "res_days": ds.RES_DAYS,
            "gross_src": ds.GROSS_SRC, "containment_src": ds.CONTAINMENT_SRC,
            "collected_total_bbl": round(ds.total_collected_bbl())}


def _area_km2(geom) -> float:
    from shapely import affinity
    c = math.cos(math.radians(geom.centroid.y))
    return affinity.scale(geom, xfact=c * 111.32, yfact=110.574, origin=(0, 0)).area


def _cat_src(cat: dict, date: str, key: str) -> str | None:
    for e in cat.get("evolution", []):
        if e["date"] == date and key in e["note"]:
            return e.get("src")
    return None


def case_texas(workers: int) -> dict:
    cat = catalog()["texas_city_y"]
    # Overflight positions with coordinates (catalog evolution, IncidentNews). Times are not given: noon CDT (17 UTC) assumed.
    fixes = [
        {"label": "2014-03-24 overflight: leading edge 28°48'N 95°12'W", "t": "2014-03-24T17:00:00",
         "lon": -95.2, "lat": 28.8, "src": _cat_src(cat, "2014-03-24", "28°48")},
        {"label": "2014-03-25 overflight: sheens/tarballs 28°34.3'N 95°26.9'W", "t": "2014-03-25T17:00:00",
         "lon": -(95 + 26.9 / 60), "lat": 28 + 34.3 / 60, "src": _cat_src(cat, "2014-03-25", "28°34")},
    ]
    variants = [
        {"id": "catalog_seed", "label": "Seed at the collision site (catalog point/time, as the app hindcast)",
         "lat": cat["lat"], "lon": cat["lon"], "start": datetime.fromisoformat(cat["date_start"][:19]), "hours": 72},
        {"id": "entrance_seed", "label": "Seed at the Galveston entrance (Bolivar Roads 29.33N 94.72W), "
         "2014-03-23 12 UTC, when oil was reported leaving the bay", "lat": 29.33, "lon": -94.72,
         "start": datetime(2014, 3, 23, 12), "hours": 60},
    ]
    plans, meta = [], []
    for v in variants:
        for mk in MODELS:
            plans.append(plan_run(v["lat"], v["lon"], v["start"], v["hours"], cat["oil_type"],
                                  cat["volume_bbl"], mk))
            meta.append((v["id"], mk))
    tracks = execute(plans, workers)
    out_v = []
    for v in variants:
        rv = {"id": v["id"], "label": v["label"], "start": v["start"].isoformat(), "models": {}}
        for (vid, mk), p, tr in zip(meta, plans, tracks):
            if vid != v["id"]:
                continue
            rv["seed"] = [p["lon"], p["lat"]]
            rv["seed_moved_km"] = p.get("seed_moved_km")
            rv["duration_h"] = p["duration_h"]
            if "error" in tr:
                rv["models"][mk] = {"error": tr["error"]}
                continue
            r = {"fixes": []}
            for fx in fixes:
                t = int(np.datetime64(fx["t"], "s").astype(np.int64))
                if t > tr["times"][-1]:
                    r["fixes"].append(None)
                    continue
                lo, la, st = positions(tr, t)
                fl = ~st
                e = {"floating_frac": round(float(fl.mean()), 3)}
                if fl.any():
                    d = km(lo[fl], la[fl], fx["lon"], fx["lat"])
                    e["nearest_km"] = round(float(d.min()), 1)
                    e["centroid_km"] = round(float(km(lo[fl].mean(), la[fl].mean(), fx["lon"], fx["lat"])), 1)
                r["fixes"].append(e)
            lo, la, st = positions(tr, tr["times"][-1])
            r["stranded_end"] = round(float(st.mean()), 3)
            if st.any():
                r["stranded_centroid"] = [round(float(lo[st].mean()), 3), round(float(la[st].mean()), 3)]
                # Galveston-area shoreline (observed oiled by Mar 25: Big Reef, Pelican Island, East Beach, Texas City Dike)
                gal = (la[st] > 29.1) & (lo[st] > -95.1) & (lo[st] < -94.5)
                r["stranded_galveston_frac"] = round(float(gal.mean()), 3)
            r["ml_coverage"] = tr.get("meta", {}).get("ml_coverage_fraction")
            rv["models"][mk] = r
        out_v.append(rv)
    return {
        "id": "texas_city_y", "name": cat["name"], "kind": "points",
        "observed": "IncidentNews overflight positions (catalog evolution) + reported shoreline oiling; no polygons",
        "fixes": fixes, "variants": out_v,
        "not_scored": "Matagorda Island landfall (2014-03-27) is after the cached forcing ends (2014-03-26).",
    }


def case_main_pass(workers: int) -> dict:
    cat = catalog()["main_pass_2023"]
    start = datetime.fromisoformat(cat["date_start"][:19])
    plans = [plan_run(cat["lat"], cat["lon"], start, 72, cat["oil_type"], cat["volume_bbl"], mk) for mk in MODELS]
    tracks = execute(plans, workers)
    res = {}
    for mk, p, tr in zip(MODELS, plans, tracks):
        if "error" in tr:
            res[mk] = {"error": tr["error"]}
            continue
        t0 = tr["times"][0]
        r = {"bearing_deg": [], "dist_km": [], "toward_sw": [], "stranded": []}
        for h in HORIZONS:
            if t0 + h * 3600 > tr["times"][-1]:
                continue
            lo, la, st = positions(tr, t0 + h * 3600)
            fl = ~st
            mlo, mla = float(lo[fl].mean()) if fl.any() else float(lo.mean()), float(la[fl].mean()) if fl.any() else float(la.mean())
            b = bearing(p["lon"], p["lat"], mlo, mla)
            r["bearing_deg"].append(round(b))
            r["dist_km"].append(round(float(km(p["lon"], p["lat"], mlo, mla)), 1))
            r["toward_sw"].append(bool(180 <= b <= 270))
            r["stranded"].append(round(float(st.mean()), 3))
        lo, la, st = positions(tr, t0 + 24 * 3600)
        fl = ~st
        if fl.sum() > 10:
            X = np.column_stack([(lo[fl] - lo[fl].mean()) * 111.32 * math.cos(math.radians(p["lat"])),
                                 (la[fl] - la[fl].mean()) * 110.574])
            ax = np.linalg.svd(X - X.mean(0), full_matrices=False)[2][0]
            proj = X @ ax
            r["length_24h_km"] = round(float(np.percentile(proj, 98) - np.percentile(proj, 2)), 1)
        lo, la, st = positions(tr, tr["times"][-1])
        if st.any():
            r["stranded_centroid"] = [round(float(lo[st].mean()), 3), round(float(la[st].mean()), 3)]
        first = [positions(tr, t)[2].mean() for t in tr["times"]]
        hit = next((i for i, f in enumerate(first) if f > 0.01), None)
        r["first_stranding_h"] = None if hit is None else round((tr["times"][hit] - t0) / 3600, 1)
        r["ml_coverage"] = tr.get("meta", {}).get("ml_coverage_fraction")
        res[mk] = r
    return {
        "id": "main_pass_2023", "name": cat["name"], "kind": "qualitative",
        "observed": ("Nov 17: slick over 40 miles (64 km) long heading SW from the leak; Nov 18: moving SW away from "
                     "shore; Nov 28: no shoreline impacts found (IncidentNews, catalog evolution)."),
        "seed": [plans[0]["lon"], plans[0]["lat"]], "start": start.isoformat(),
        "models": res,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--only", choices=["dwh", "texas_city_y", "main_pass_2023"])
    ap.add_argument("--max-starts", type=int, default=None, help="DWH: limit the number of start days (evenly spaced)")
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res_path = OUT_DIR / "results.json"
    old = json.loads(res_path.read_text()) if res_path.exists() else {}
    cases = {c["id"]: c for c in old.get("cases", [])}
    t0 = time.time()
    if a.only in (None, "dwh"):
        if not (ML_NODWH_DIR / "drift_model.joblib").exists():
            import subprocess
            print("training the held-out ML (DWH window excluded) ...", file=sys.stderr)
            subprocess.run([sys.executable, "-m", "app.ourmodel.train", "--exclude", "dwh",
                            "--out-dir", str(ML_NODWH_DIR)], check=True, cwd=config.BACKEND_DIR)
        cases["dwh"] = case_dwh(a.workers, a.max_starts)
    if a.only in (None, "texas_city_y"):
        cases["texas_city_y"] = case_texas(a.workers)
    if a.only in (None, "main_pass_2023"):
        cases["main_pass_2023"] = case_main_pass(a.workers)
    out = {
        "version": VERSION,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": {k: v["label"] for k, v in ALL_MODELS.items()} | {"persistence": "Persistence (observed outline D)"},
        "model_versions": {"openoil": jobs.MODEL_VERSION, "ours": jobs.OURS_VERSION},
        "n_particles": N_PART, "cell_deg": CELL, "fss_window_cells": FSS_WIN,
        "cases": [cases[k] for k in ("dwh", "texas_city_y", "main_pass_2023") if k in cases],
    }
    res_path.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {res_path} ({res_path.stat().st_size // 1024} KB) in {time.time() - t0:.0f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
