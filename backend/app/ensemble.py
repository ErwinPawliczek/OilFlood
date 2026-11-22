"""Monte-Carlo ensemble of our model + probability products (docs/ENSEMBLE.md, docs/METHODOLOGY.md).

A run with `ensemble: N` = one control run (the user's params, full particles: /tracks and /response) plus N perturbed
members (params.draw_members) run in parallel processes, then aggregated into runs/<id>/ensemble.json:
probability grid per 3 h step, per-shoreline-segment P(oiled by T) + ETA quantiles, centroid ellipses, mean forces.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import shutil
import threading
import time
import traceback
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

import numpy as np

from . import config, runner
from .ourmodel import params as MP

ENS_WORKERS = int(os.environ.get("OILFLOOD_ENS_WORKERS", str(max(2, min(16, (os.cpu_count() or 4) - 4)))))
ENS_MAX_S = float(os.environ.get("OILFLOOD_ENS_MAX_S", "600"))  # wall-clock cap for all members
CHI2_2 = {50: 1.3863, 90: 4.6052}  # chi-square quantiles, 2 dof
M_LAT, M_LON = 110574.0, 111320.0

_pool: ProcessPoolExecutor | None = None
_plock = threading.Lock()


def pool() -> ProcessPoolExecutor:
    global _pool
    with _plock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=ENS_WORKERS, mp_context=mp.get_context("spawn"))
        return _pool


def shutdown() -> None:
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)


# --- worker side ---------------------------------------------------------------------------------------

def run_member(run_dir: str, params: dict, member: dict) -> dict:
    """One perturbed member (worker process): compact hourly arrays -> members/<k>.npz + <k>.json."""
    import warnings
    warnings.filterwarnings("ignore")
    from opendrift.models.openoil.adios import dirjs

    from .ourmodel import sim
    md = Path(run_dir) / "members"
    k = member["member"]
    prog = md / f"{k}.prog"

    def status(progress=0.0, **_):
        prog.write_text(str(progress))

    mp_ = {**(params.get("model_params") or {}),
           **{x: member[x] for x in ("windage_pct", "wind_rot_deg", "current_factor", "diffusivity_m2s")}}
    p = {**params, "compact": True, "rng_seed": member["rng_seed"], "model_params": mp_,
         "n_particles": min(int(params["n_particles"]), MP.MEMBER_PARTICLES),
         "_oil_resolved": runner.resolve_oil_type(params.get("oil_type"), dirjs.get_oil_names())}
    if p.get("seed_lon") is not None:  # backtest: seeded from an observed outline -> first n points
        p["seed_lon"], p["seed_lat"] = p["seed_lon"][:p["n_particles"]], p["seed_lat"][:p["n_particles"]]
    out = sim.run(Path(run_dir), p, status)
    np.savez_compressed(md / f"{k}.npz", lon=out["lon"], lat=out["lat"], mass=out["mass"], state=out["state"],
                        strand_t=out["strand_t"], m0=out["m0"])
    meta = {"member": k, "t0": out["t0"], "times": out["times"], "budget": out["budget"], "forces": out["forces"],
            "model_params": out["model_params"], "runtime_s": out["runtime_s"]}
    (md / f"{k}.json").write_text(json.dumps(meta))
    prog.write_text("1")
    return {"member": k, "runtime_s": out["runtime_s"]}


def run_control(run_dir: str, params: dict) -> None:
    runner.run_spill(str(Path(run_dir) / "control"), params)


# --- coordinator (thread in the API process) ------------------------------------------------------------

def start(run_id: str, d: Path, params: dict) -> Future:
    """Kick off control + members; returns a Future that resolves when ensemble.json is written."""
    fut: Future = Future()
    fut.set_running_or_notify_cancel()
    threading.Thread(target=_coordinate, args=(run_id, d, params, fut), daemon=True, name=f"ens-{run_id}").start()
    return fut


def _coordinate(run_id: str, d: Path, params: dict, fut: Future) -> None:
    try:
        ens = params["ensemble"]
        members = MP.draw_members(int(ens["members"]), int(ens["seed"]), _eff_params(params))
        md = d / "members"
        if md.exists():
            shutil.rmtree(md)
        md.mkdir(parents=True)
        (d / "control").mkdir(exist_ok=True)
        (d / "control" / "status.json").write_text(json.dumps({"status": "queued", "progress": 0.0}))
        (d / "members.json").write_text(json.dumps(members))
        runner._status(d, status="running", progress=0.0, started_at=time.time(), members_done=0, members_failed=0)
        pl = pool()
        cf = pl.submit(run_control, str(d), params)
        mf = {m["member"]: pl.submit(run_member, str(d), params, m) for m in members}
        t_start = time.time()
        n = len(members)
        while True:
            done = [k for k, f in mf.items() if f.done()]
            failed = [k for k in done if mf[k].exception() is not None]
            progs = []
            for k, f in mf.items():
                if f.done():
                    progs.append(1.0)
                else:
                    try:
                        progs.append(float((md / f"{k}.prog").read_text() or 0))
                    except (OSError, ValueError):
                        progs.append(0.0)
            try:
                cs = json.loads((d / "control" / "status.json").read_text())
            except (OSError, json.JSONDecodeError):
                cs = {}
            cprog = 1.0 if cf.done() else float(cs.get("progress", 0.0))
            runner._status(d, progress=round(0.9 * (cprog + sum(progs)) / (n + 1), 3),
                           members_done=len(done) - len(failed), members_failed=len(failed))
            timed_out = time.time() - t_start > ENS_MAX_S
            if cf.done() and (len(done) == n or timed_out):
                break
            time.sleep(1.0)
        if cf.exception() is not None:
            raise cf.exception()
        cs = json.loads((d / "control" / "status.json").read_text())
        if cs.get("status") != "done":
            raise RuntimeError(cs.get("error") or "control run failed")
        for k, f in mf.items():
            if not f.done():
                f.cancel()
            elif f.exception() is not None:
                members[k]["error"] = f"{type(f.exception()).__name__}: {f.exception()}"
        ok = [m["member"] for m in members if (md / f"{m['member']}.npz").exists() and "error" not in m]
        if len(ok) < 2:
            raise RuntimeError(f"only {len(ok)} of {n} ensemble members finished")
        runner._status(d, progress=0.92, members_done=len(ok), members_failed=n - len(ok))
        pl.submit(aggregate, str(d), params, members, ok).result()
        os.replace(d / "control" / "tracks.json", d / "tracks.json")
        runner._status(d, status="done", progress=1.0, finished_at=time.time())
        fut.set_result(True)
    except Exception as e:  # noqa: BLE001
        runner._status(d, status="error", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()[-4000:])
        fut.set_exception(e)


def _eff_params(params: dict) -> dict:
    eff = MP.defaults()
    eff.update({k: v for k, v in (params.get("model_params") or {}).items() if k in eff})
    eff["ml"] = bool(params.get("ml", False))
    eff["particles"] = int(params["n_particles"])
    eff["horizon_h"] = float(params.get("requested_duration_h", params["duration_h"]))
    return eff


# --- aggregation (worker process) ------------------------------------------------------------------------

def _vec(u: float, v: float) -> dict:
    return {"u": round(float(u), 4), "v": round(float(v), 4), "speed": round(float(math.hypot(u, v)), 4),
            "dir_to": round(float(math.degrees(math.atan2(u, v)) % 360), 1)}


def _forces_steps(forces_list: list[list[dict]], budgets: list[list[dict]], t0: float) -> list[dict]:
    """Mean over members (or one run) of the per-3 h drift terms + budget fractions."""
    out = []
    nw = min(len(f) for f in forces_list)
    prev_spread, prev_h = 0.0, 0.0
    for w in range(nw):
        rows = [f[w] for f in forces_list]
        h = rows[0]["h"]
        mean = {c: np.mean([r[c] for r in rows], axis=0) for c in ("current", "wind", "stokes", "ml", "wind10")}
        tot = mean["current"] + mean["wind"] + mean["stokes"] + mean["ml"]
        spread = float(np.mean([r["spread_km"] for r in rows]))
        st = {"h": h, "t": int(t0 + h * 3600), **{c: _vec(*mean[c]) for c in ("current", "wind", "stokes", "ml")},
              "total": _vec(*tot), "wind10": _vec(*mean["wind10"]),
              "diffusion": {"k_m2s": round(float(np.mean([r["k_m2s"] for r in rows])), 3), "spread_km": round(spread, 3),
                            "speed": round(max(spread - prev_spread, 0) * 1000 / max((h - prev_h) * 3600, 1), 5)}}
        prev_spread, prev_h = spread, h
        fr = []
        for b in budgets:
            e = min(b, key=lambda x: abs(x["t"] - (t0 + h * 3600)))
            tot_m = sum(e.get(k, 0) for k in ("surface", "evaporated", "dispersed", "stranded"))
            fr.append([e.get(k, 0) / tot_m if tot_m > 0 else 0 for k in ("surface", "evaporated", "dispersed",
                                                                          "stranded")])
        fm = np.mean(fr, axis=0)
        st["budget"] = {k: round(float(x), 4) for k, x in zip(("surface", "evaporated", "dispersed", "stranded"), fm)}
        out.append(st)
    return out


def _ellipse(pts: np.ndarray, q: int) -> dict | None:
    """Gaussian ellipse containing q % of the member centroids (pts: lon/lat, >= 3 finite rows)."""
    if len(pts) < 3:
        return None
    c = pts.mean(axis=0)
    kx = M_LON * math.cos(math.radians(c[1])) / 1000
    ky = M_LAT / 1000
    xy = np.c_[(pts[:, 0] - c[0]) * kx, (pts[:, 1] - c[1]) * ky]
    cov = np.cov(xy.T) + np.eye(2) * 1e-6
    w, v = np.linalg.eigh(cov)
    s = CHI2_2[q]
    a, b = math.sqrt(s * max(w[1], 0)), math.sqrt(s * max(w[0], 0))
    ex, ey = v[0, 1], v[1, 1]  # major axis
    ang = math.degrees(math.atan2(ex, ey)) % 180
    th = np.linspace(0, 2 * np.pi, 49)
    px = a * np.cos(th) * ex - b * np.sin(th) * ey
    py = a * np.cos(th) * ey + b * np.sin(th) * ex
    poly = [[round(float(c[0] + x / kx), 4), round(float(c[1] + y / ky), 4)] for x, y in zip(px, py)]
    return {"center": [round(float(c[0]), 4), round(float(c[1]), 4)], "a_km": round(a, 2), "b_km": round(b, 2),
            "angle_deg": round(ang, 1), "polygon": poly}


def _q(vals: np.ndarray, q: float):
    """Empirical quantile over ALL members (inf = never reached); None if that member share never arrives."""
    x = float(np.quantile(vals, q, method="inverted_cdf"))
    return None if not math.isfinite(x) else round(x, 1)


def aggregate(run_dir: str, params: dict, members: list[dict], ok: list[int]) -> None:
    from . import response
    d = Path(run_dir)
    md = d / "members"
    arrs = [np.load(md / f"{k}.npz") for k in ok]
    metas = [json.loads((md / f"{k}.json").read_text()) for k in ok]
    M = len(ok)
    t0 = float(metas[0]["t0"])
    n_hr = min(a["lon"].shape[0] for a in arrs)
    dur_h = float(params["duration_h"])
    hours = [h for h in range(0, n_hr, MP.OUTPUT_STEP_H)]
    if hours[-1] != n_hr - 1:
        hours.append(n_hr - 1)
    res = MP.GRID_DEG

    lon = [a["lon"] for a in arrs]
    lat = [a["lat"] for a in arrs]
    mass = [a["mass"] for a in arrs]
    state = [a["state"] for a in arrs]
    surf = [(s == 0) & (m > 0) & np.isfinite(x) for s, m, x in zip(state, mass, lon)]  # active, afloat, drawn

    # grid extent: every surface particle of every member at every 3 h step
    lo_all = np.concatenate([lon[i][hours][surf[i][hours]] for i in range(M)] + [np.array([params["lon"]])])
    la_all = np.concatenate([lat[i][hours][surf[i][hours]] for i in range(M)] + [np.array([params["lat"]])])
    lon0 = math.floor(float(lo_all.min()) / res) * res - res
    lat0 = math.floor(float(la_all.min()) / res) * res - res
    nx = int(math.ceil((float(lo_all.max()) - lon0) / res)) + 2
    ny = int(math.ceil((float(la_all.max()) - lat0) / res)) + 2

    # hourly mass-weighted centroids per member (response tactic gates use them)
    cen = np.full((M, n_hr, 2), np.nan)
    for i in range(M):
        w = np.where(surf[i][:n_hr], mass[i][:n_hr], 0.0)
        ws = w.sum(axis=1)
        okh = ws > 0
        cen[i, okh, 0] = (np.nan_to_num(lon[i][:n_hr]) * w).sum(axis=1)[okh] / ws[okh]
        cen[i, okh, 1] = (np.nan_to_num(lat[i][:n_hr]) * w).sum(axis=1)[okh] / ws[okh]

    steps = []
    for h in hours:
        cnt = np.zeros(nx * ny)
        msum = np.zeros(nx * ny)
        skg = []
        for i in range(M):
            s = surf[i][h]
            ix = ((lon[i][h][s] - lon0) / res).astype(int)
            iy = ((lat[i][h][s] - lat0) / res).astype(int)
            flat = iy * nx + ix
            mm = np.bincount(flat, weights=mass[i][h][s].astype(float), minlength=nx * ny)
            cnt += mm > 0
            msum += mm
            skg.append(float(mass[i][h].sum()))
        p = cnt / M
        nz = np.nonzero(cnt)[0]
        cells = [[int(c % nx), int(c // nx), round(float(p[c]), 3), round(float(msum[c] / M), 1)] for c in nz]
        cell_lat = lat0 + (nz // nx + 0.5) * res
        a_cell = (res * M_LON / 1000) * (res * M_LAT / 1000) * np.cos(np.radians(cell_lat))
        cm = cen[:, h]
        fin = np.isfinite(cm[:, 0])
        steps.append({
            "h": h, "t": int(t0 + h * 3600), "cells": cells, "p_max": round(float(p.max()) if len(nz) else 0.0, 3),
            "area_km2": {f"p{int(q * 100)}": round(float(a_cell[p[nz] >= q].sum()), 1) for q in (0.1, 0.5, 0.9)},
            "surface_kg": {"mean": round(float(np.mean(skg)), 1), "p10": round(float(np.quantile(skg, 0.1)), 1),
                           "p90": round(float(np.quantile(skg, 0.9)), 1)},
            "centroid": {"mean": [round(float(x), 4) for x in cm[fin].mean(axis=0)] if fin.any() else None,
                         "members": [[round(float(x), 4), round(float(y), 4)] if f else None
                                     for (x, y), f in zip(cm, fin)],
                         "ellipse50": _ellipse(cm[fin], 50), "ellipse90": _ellipse(cm[fin], 90)}})

    # --- shoreline segments: stranded particles -> ESI cells (same 6 km match as the outline) ------------------
    ed, elat, elon, etree = response.esi()
    ecells = ed["cells"]
    cell_eta: dict[int, np.ndarray] = {}  # ESI cell -> per-member first stranding time (h; inf = never)
    for i in range(M):
        st_t = arrs[i]["strand_t"]
        sm = np.isfinite(st_t)
        if not sm.any():
            continue
        last = lon[i].shape[0] - 1
        dist, ci = etree.query(response._xy(lon[i][last][sm], lat[i][last][sm]),
                               distance_upper_bound=response.LANDFALL_KM)
        tt = (st_t[sm] - t0) / 3600
        for dd, c, t_ in zip(dist, ci, tt):
            if not np.isfinite(dd):
                continue
            arr = cell_eta.setdefault(int(c), np.full(M, np.inf))
            arr[i] = min(arr[i], t_)
    keys = sorted(cell_eta, key=lambda c: (-int(np.isfinite(cell_eta[c]).sum()), float(np.min(cell_eta[c]))))
    groups: list[list[int]] = []
    seeds_xy: list[np.ndarray] = []
    for c in keys:
        xy = response._xy([elon[c]], [elat[c]])[0]
        if seeds_xy:
            dd = np.hypot(*(np.array(seeds_xy) - xy).T)
            j = int(np.argmin(dd))
            if dd[j] <= response.STRETCH_KM / 2:
                groups[j].append(c)
                continue
        seeds_xy.append(xy)
        groups.append([c])
    shorelines = []
    for gi, ks in enumerate(groups):
        eta = np.min(np.array([cell_eta[c] for c in ks]), axis=0)
        cmax = max((ecells[c][3] for c in ks), key=response.esi_rank)
        hitn = np.array([np.isfinite(cell_eta[c]).sum() for c in ks], float)
        clon = float(np.average(elon[ks], weights=hitn))
        clat = float(np.average(elat[ks], weights=hitn))
        from collections import Counter
        stt = Counter(ed["atlas_state"][ed["atlases"][ecells[c][6]]] for c in ks).most_common(1)[0][0]
        shorelines.append({
            "id": gi, "name": response.place_name(clon, clat), "state": stt,
            "lon": round(clon, 3), "lat": round(clat, 3),
            "bbox": [round(float(elon[ks].min()), 3), round(float(elat[ks].min()), 3),
                     round(float(elon[ks].max()), 3), round(float(elat[ks].max()), 3)],
            "esi": cmax, "esi_label": ed["labels"].get(cmax, cmax), "esi_rank": response.esi_rank(cmax),
            "shoreline_km": round(sum(ecells[c][2] for c in ks), 1),
            "path": _chain([[float(elon[c]), float(elat[c])] for c in ks]),
            "p_oiled": {**{str(T): round(float(np.mean(eta <= T)), 3) for T in (24, 48, 72) if T <= dur_h + 0.01},
                        "end": round(float(np.mean(np.isfinite(eta))), 3)},
            "eta_h": {"p10": _q(eta, 0.1), "p50": _q(eta, 0.5), "p90": _q(eta, 0.9)},
            "_cells": ks})
    shorelines.sort(key=lambda s: (-s["p_oiled"]["end"], s["eta_h"]["p10"] if s["eta_h"]["p10"] is not None else 1e9))
    for i, s in enumerate(shorelines):
        s["id"] = i

    forces = _forces_steps([m["forces"] for m in metas], [m["budget"] for m in metas], t0)
    ctr = json.loads((d / "control" / "tracks.json").read_text())
    cmeta = ctr.get("meta") or {}
    control_forces = _forces_steps([cmeta["forces"]], [ctr["budget"]], t0) if cmeta.get("forces") else []

    perts = [{k: m[k] for k in ("member", "windage_pct", "wind_rot_deg", "current_factor", "diffusivity_m2s")}
             | {"ok": m["member"] in ok, **({"error": m["error"]} if "error" in m else {})} for m in members]
    ens = params["ensemble"]
    out = {
        "version": 1, "members": len(members), "members_ok": M, "seed": ens["seed"],
        "member_particles": int(arrs[0]["lon"].shape[1]), "step_h": MP.OUTPUT_STEP_H,
        "t0": int(t0), "hours": hours, "times": [int(t0 + h * 3600) for h in hours],
        "grid": {"res_deg": res, "lon0": round(lon0, 4), "lat0": round(lat0, 4), "nx": nx, "ny": ny},
        "steps": steps, "shorelines": shorelines, "perturbations": perts,
        "forces": forces, "control_forces": control_forces,
        # response.py inputs (not served by /probability)
        "_cen_hourly": [[[None if not np.isfinite(x) else round(float(x), 4) for x in pt] for pt in row] for row in cen],
        "_cell_eta": {str(c): [None if not np.isfinite(x) else round(float(x), 2) for x in v]
                      for c, v in cell_eta.items()},
        "runtime_s": {"members_mean": round(float(np.mean([m["runtime_s"] for m in metas])), 1)},
    }
    runner._write_json(d / "ensemble.json", out)


def _chain(pts: list[list[float]]) -> list[list[float]]:
    """Order points into a nearest-neighbour chain starting from the westernmost one (3D ribbon)."""
    if len(pts) <= 2:
        return [[round(x, 4), round(y, 4)] for x, y in pts]
    rest = sorted(pts)
    out = [rest.pop(0)]
    while rest:
        x, y = out[-1]
        j = min(range(len(rest)), key=lambda k: (rest[k][0] - x) ** 2 + (rest[k][1] - y) ** 2)
        out.append(rest.pop(j))
    return [[round(x, 4), round(y, 4)] for x, y in out]


def load(run_dir: Path) -> dict | None:
    p = run_dir / "ensemble.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def probability_view(ens: dict, run_id: str, t: float | None) -> dict:
    out = {k: v for k, v in ens.items() if not k.startswith("_") and k not in ("forces", "control_forces")}
    out["run_id"] = run_id
    out["shorelines"] = [{k: v for k, v in s.items() if not k.startswith("_")} for s in ens["shorelines"]]
    if t is not None:
        st = min(ens["steps"], key=lambda s: abs(s["h"] - t))
        out["steps"] = [st]
    return out
