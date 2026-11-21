"""OpenOil simulation worker. Runs in a separate process (see jobs.py)."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

DEFAULT_OIL = "IFO-380LS 2014"
OIL_ALIASES = {
    "IFO380": "IFO-380LS 2014",
    "IFO 380": "IFO-380LS 2014",
    "RMG380": "IFO-380LS 2014",
    "BUNKERC": "IFO-380LS 2014",
}


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def resolve_oil_type(name: str | None, oiltypes: list[str]) -> str:
    if not name:
        return DEFAULT_OIL
    if name in oiltypes:
        return name
    n = _norm(name)
    for k, v in OIL_ALIASES.items():
        if _norm(k) == n:
            return v
    exact = [o for o in oiltypes if _norm(o) == n]
    if exact:
        return exact[0]
    partial = sorted((o for o in oiltypes if n in _norm(o)), key=len)
    if partial:
        return partial[0]
    raise ValueError(f"Unknown oil type {name!r}")


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def _status(run_dir: Path, **kw) -> None:
    p = run_dir / "status.json"
    cur = {}
    if p.exists():
        try:
            cur = json.loads(p.read_text())
        except Exception:
            pass
    cur.update(kw)
    _write_json(p, cur)


def run_spill(run_dir: str, params: dict) -> None:
    """Entry point for the worker process. params: see jobs.SpillRequest."""
    run_dir = Path(run_dir)
    try:
        _run(run_dir, params)
    except Exception as e:
        _status(run_dir, status="error", error=f"{type(e).__name__}: {e}",
                traceback=traceback.format_exc()[-4000:])


def _run(run_dir: Path, p: dict) -> None:
    import warnings
    warnings.filterwarnings("ignore")
    from opendrift.models.openoil import OpenOil
    from opendrift.readers import reader_netCDF_CF_generic

    from . import config, forcing

    t_wall = time.time()
    start = datetime.fromisoformat(p["start_time"])
    duration = timedelta(hours=float(p["duration_h"]))
    total_s = duration.total_seconds()
    last_write = [0.0]

    class ProgressOpenOil(OpenOil):
        def update(self, *a, **kw):
            r = super().update(*a, **kw)
            now = time.time()
            if now - last_write[0] > 0.5:
                last_write[0] = now
                frac = (self.time - self.start_time).total_seconds() / total_s
                _status(run_dir, progress=round(min(max(frac, 0.0), 0.99), 3))
            return r

    _status(run_dir, status="running", progress=0.0, started_at=time.time())

    o = ProgressOpenOil(loglevel=30, weathering_model="noaa")

    # --- forcing -------------------------------------------------------------
    forcing_used = []
    has_stokes = False
    for f in p.get("forcing_files", []):
        try:
            ds = forcing.open_normalized(f)
            o.add_reader(reader_netCDF_CF_generic.Reader(ds, name=Path(f["path"]).name))
            forcing_used.append(f["path"])
            has_stokes |= "stokes" in f["kinds"]
        except Exception as e:  # bad file: keep going on fallback
            logging.warning("reader failed for %s: %s", f["path"], e)

    # Constant fallback wherever readers don't cover (or no readers at all).
    o.set_config("environment:fallback:x_sea_water_velocity", config.CONST_CURRENT_U)
    o.set_config("environment:fallback:y_sea_water_velocity", config.CONST_CURRENT_V)
    o.set_config("environment:fallback:x_wind", config.CONST_WIND_U)
    o.set_config("environment:fallback:y_wind", config.CONST_WIND_V)

    # --- physics (docs/RESEARCH.md §2, §6) -----------------------------------
    o.set_config("drift:stokes_drift", True)
    if not has_stokes:
        # No wave model: Stokes drift parameterised from wind + fetch.
        o.set_config("drift:use_tabularised_stokes_drift", True)
    o.set_config("environment:fallback:horizontal_diffusivity", 10.0)  # m2/s (= GNOME 1e5 cm2/s)
    o.set_config("general:coastline_action", "stranding")
    o.set_config("processes:evaporation", True)
    o.set_config("processes:emulsification", True)
    o.set_config("processes:dispersion", True)
    o.set_config("drift:vertical_mixing", True)

    oil = resolve_oil_type(p.get("oil_type"), o.oiltypes)
    n = int(p["n_particles"])
    o.seed_elements(
        lon=float(p["lon"]), lat=float(p["lat"]), time=start, number=n,
        radius=float(p.get("radius_m", 50)), z=0,
        oil_type=oil, m3_per_hour=float(p["volume_m3"]),  # instantaneous: m3_per_hour * 1 h
        wind_drift_factor=float(p.get("wind_drift_factor", 0.02)),
    )

    o.run(duration=duration,
          time_step=timedelta(minutes=config.TIME_STEP_MIN),
          time_step_output=timedelta(minutes=config.OUTPUT_STEP_MIN))

    _status(run_dir, progress=0.99)
    tracks = build_tracks(o, start, duration, config.OUTPUT_STEP_MIN)
    tracks["meta"] = {
        "lat": p["lat"], "lon": p["lon"], "start_time": p["start_time"],
        "duration_h": p["duration_h"], "volume_m3": p["volume_m3"],
        "oil_type": oil, "n_particles": n,
        "forcing": forcing_used or ["constant"],
        "fallback": {"current_uv": [config.CONST_CURRENT_U, config.CONST_CURRENT_V],
                     "wind_uv": [config.CONST_WIND_U, config.CONST_WIND_V]},
        "wind_drift_factor": p.get("wind_drift_factor", 0.02),
        "runtime_s": round(time.time() - t_wall, 1),
        # Mean forcing seen by particles; equals the fallback values if readers gave nothing.
        "env_mean": {v: round(float(np.nanmean(o.result[v].values)), 3)
                     for v in ("x_sea_water_velocity", "y_sea_water_velocity", "x_wind", "y_wind")
                     if v in o.result},
        "mass_units": "kg",
    }
    _write_json(run_dir / "tracks.json", tracks)
    _status(run_dir, status="done", progress=1.0, finished_at=time.time())


def build_tracks(o, start: datetime, duration: timedelta, step_min: int) -> dict:
    r = o.result
    sim_times = r["time"].values.astype("datetime64[s]").astype(np.int64)
    lon = r["lon"].values  # (trajectory, time), NaN after deactivation
    lat = r["lat"].values
    status = r["status"].values
    cats = list(o.status_categories)

    # Full output time axis (OpenDrift stops early if every element is stranded).
    t0 = int(np.datetime64(start, "s").astype(np.int64))
    n_out = int(duration.total_seconds() // (step_min * 60)) + 1
    times = [t0 + i * step_min * 60 for i in range(n_out)]

    particles = []
    for i in range(lon.shape[0]):
        ok = np.isfinite(lon[i]) & np.isfinite(lat[i])
        idx = np.nonzero(ok)[0]
        if len(idx) == 0:
            continue
        last = idx[-1]
        st = status[i, last]
        name = cats[int(st)] if np.isfinite(st) and int(st) < len(cats) else "active"
        if name not in ("active", "stranded", "evaporated"):
            name = "evaporated" if "evapor" in name else "active"
        particles.append({
            "path": [[round(float(lon[i, j]), 4), round(float(lat[i, j]), 4)] for j in idx],
            "timestamps": [int(sim_times[j]) for j in idx],
            "status_end": name,
        })

    budget = []
    b = o.get_oil_budget()
    if b is not None:
        def arr(k):
            return np.nan_to_num(np.asarray(b[k], dtype=float))
        surf, sub, strd = arr("mass_surface"), arr("mass_submerged"), arr("mass_stranded")
        evap, disp, bio = arr("mass_evaporated"), arr("mass_dispersed"), arr("mass_biodegraded")
        for j, t in enumerate(sim_times):
            budget.append({
                "t": int(t),
                "surface": round(float(surf[j]), 2),
                "evaporated": round(float(evap[j]), 2),
                # Natural dispersion + oil mixed below the surface + biodegraded.
                "dispersed": round(float(disp[j] + sub[j] + bio[j]), 2),
                "stranded": round(float(strd[j]), 2),
            })
        # Pad to the full horizon when the run ended early (state is frozen).
        while budget and budget[-1]["t"] < times[-1]:
            nxt = dict(budget[-1])
            nxt["t"] = budget[-1]["t"] + step_min * 60
            budget.append(nxt)

    return {"times": times, "particles": particles, "budget": budget}
