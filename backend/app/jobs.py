"""Spill job queue: request hashing, on-disk cache under backend/runs/, process pool."""
from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typing import Literal

from pydantic import BaseModel, Field

from . import config, forcing, oils, runner

MODEL_VERSION = "openoil-v4-live"  # v3: real forcing only; v4: predictions on the live forecast only
from .ourmodel.sim import VERSION as OURS_VERSION  # bump sim.VERSION when app/ourmodel changes
BBL_M3 = 0.158987
MODEL_LABELS = {"openoil": "OpenDrift OpenOil (baseline)", "ours": "OilFlood model (ours, + ML drift correction)"}


class SpillRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=360)
    start_time: datetime | None = None
    duration_h: float = Field(72, gt=0, le=240)
    volume_m3: float = Field(500, gt=0)
    volume_bbl: float | None = Field(None, gt=0, le=10_000_000, description="overrides volume_m3")
    oil_type: str = "IFO 380"  # an id from GET /api/oils, an ADIOS name, or free text
    n_particles: int = Field(2000, ge=10, le=20000)
    wind_drift_factor: float = Field(0.02, ge=0, le=0.1)
    model: Literal["openoil", "ours"] = Field("openoil", description="openoil = OpenDrift OpenOil baseline, "
                                              "ours = OilFlood model (docs/OURMODEL.md)")
    ml: bool = Field(True, description="ours only: apply the ML drift correction")


_pool: ProcessPoolExecutor | None = None
_futures: dict[str, object] = {}
_lock = threading.Lock()


def pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=config.MAX_WORKERS,
                                    mp_context=mp.get_context("spawn"))
    return _pool


def shutdown() -> None:
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)


def _normalize(req: SpillRequest, live: bool = False) -> dict:
    if req.start_time is None and live:
        start = datetime.now(timezone.utc).replace(tzinfo=None, minute=0)
    else:
        start = req.start_time or forcing.default_start_time()
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    start = start.replace(second=0, microsecond=0)
    return {
        "lat": round(req.lat, 4),
        "lon": round(((req.lon + 180) % 360) - 180, 4),
        "start_time": start.isoformat(),
        "duration_h": float(req.duration_h),
        "volume_m3": round(float(req.volume_bbl) * BBL_M3, 3) if req.volume_bbl else float(req.volume_m3),
        "volume_bbl": float(req.volume_bbl) if req.volume_bbl else round(float(req.volume_m3) / BBL_M3, 1),
        "oil_type": oils.resolve(req.oil_type),
        "n_particles": int(req.n_particles),
        "wind_drift_factor": float(req.wind_drift_factor),
        # Only our model adds keys, so OpenOil run hashes (and cached runs) are unchanged.
        **({"model": "ours", "ml": bool(req.ml)} if req.model == "ours" else {}),
    }


def run_dir(run_id: str) -> Path:
    if not run_id.isalnum():
        raise ValueError("bad id")
    return config.RUNS_DIR / run_id


def read_status(run_id: str) -> dict | None:
    p = run_dir(run_id) / "status.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:  # mid-write on some filesystems; retry once
        time.sleep(0.05)
        return json.loads(p.read_text())


class NoForcing(ValueError):
    """No cached real forcing for the requested place/time."""


def _fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M UTC")


def _live_files(params: dict) -> tuple:
    """Prediction mode: the cached live forecast only (data/fetch_live.py), never reanalysis."""
    lf = forcing.live_forecast()
    if lf is None:
        raise NoForcing("No live forecast is cached on the server (run data/refresh_live.sh).")
    cur, wind = lf["_files"]
    start = datetime.fromisoformat(params["start_time"])
    vf, vu = max(cur.t0, wind.t0), min(cur.t1, wind.t1)
    if start < vf or start > vu - timedelta(hours=config.MIN_RUN_H):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if vf <= now <= vu - timedelta(hours=config.MIN_RUN_H):
            raise NoForcing(f"Prediction mode runs only on the live forecast, which covers {_fmt(vf)} to {_fmt(vu)}; "
                            f"{_fmt(start)} is outside it. Use historical mode for past dates.")
        raise NoForcing(f"The live forecast on the server ({_fmt(vf)} to {_fmt(vu)}) is out of date; "
                        "refresh it with data/refresh_live.sh.")
    lon, lat = params["lon"], params["lat"]
    if not (cur.covers(lon, lat, start, start) and wind.covers(lon, lat, start, start)):
        if forcing._is_land_gshhg(lon, lat):
            raise forcing.SeedOnLand("That spot is on land. Click on open water in the Gulf of Mexico.")
        b = lf["bbox"]
        raise NoForcing(f"That point is outside the live forecast area ({b[1]:.0f}-{b[3]:.0f}N, "
                        f"{-b[2]:.0f}-{-b[0]:.0f}W, Gulf of Mexico).")
    params["forecast"] = {k: v for k, v in lf.items() if not k.startswith("_") and k not in ("now", "start", "stale",
                                                                                               "hours_available")}
    return cur, wind


def plan(params: dict, snap_km: float, live: bool = False) -> dict:
    """Attach real forcing files to a normalized request, clip the duration to the
    forcing's time coverage and move the seed off model land. Raises NoForcing /
    forcing.SeedOnLand (both ValueError) with a user-facing message.
    live=True (POST /api/spill, prediction mode): only the live forecast files are used."""
    start = datetime.fromisoformat(params["start_time"])
    end = start + timedelta(hours=params["duration_h"])
    if live:
        cur, wind = _live_files(params)
    else:
        files = forcing.select(params["lon"], params["lat"], start, end)
        cur = next((f for f in files if "current" in f.kinds), None)
        wind = next((f for f in files if "wind" in f.kinds), None)
    if cur is None and forcing._is_land_gshhg(params["lon"], params["lat"]):
        raise forcing.SeedOnLand("That spot is on land. Click on open water in the Gulf of Mexico.")
    if cur is None or wind is None:
        have = sorted({(f.t0, f.t1) for f in forcing.scan() if "current" in f.kinds and f.t0
                       and f.lon_min <= params["lon"] <= f.lon_max and f.lat_min <= params["lat"] <= f.lat_max})
        what = "ocean currents" if cur is None else "winds"
        hint = (" Cached windows here: " + "; ".join(f"{_fmt(a)} to {_fmt(b)}" for a, b in have[-4:])) if have \
            else " This location is outside the cached forcing (Gulf of Mexico, 18-31N, 98-80W)."
        raise NoForcing(f"No real {what} data cached for {_fmt(start)} at this location.{hint}")
    cover_end = min(cur.t1, wind.t1)
    hours = min(params["duration_h"], (cover_end - start).total_seconds() / 3600)
    if hours < config.MIN_RUN_H:
        raise NoForcing(f"Real forcing ends at {_fmt(cover_end)}, too soon after {_fmt(start)} to run.")
    lon, lat, moved = forcing.snap_to_water(cur, params["lon"], params["lat"], start, snap_km)
    if hours < params["duration_h"] - 0.01:
        params["note"] = (f"The forecast data ends {_fmt(cover_end)}, so this run covers {hours:.0f} h "
                          f"instead of the requested {params['duration_h']:.0f} h.")
    params.update(lon=lon, lat=lat, duration_h=round(hours, 2), seed_moved_km=moved,
                  requested_lonlat=[params["lon"], params["lat"]],
                  requested_duration_h=params["duration_h"],
                  forcing_files=[cur.to_json(), wind.to_json()])
    return params


def _summary(params: dict) -> dict:
    out = {k: params[k] for k in ("duration_h", "requested_duration_h", "start_time", "volume_bbl", "volume_m3",
                                  "oil_type", "seed_moved_km", "forecast", "note") if k in params}
    out["model"] = params.get("model", "openoil")
    out["model_label"] = MODEL_LABELS.get(out["model"], out["model"])
    if out["model"] == "ours":
        out["ml"] = params.get("ml", True)
    out["truncated"] = "note" in params
    out["seed"] = [params["lon"], params["lat"]]
    return out


def submit(req: SpillRequest, extra_key: str = "", snap_km: float | None = None, live: bool = False) -> dict:
    params = plan(_normalize(req, live), config.SNAP_KM_PREDICTION if snap_km is None else snap_km, live)
    key = json.dumps({**params, "v": MODEL_VERSION if params.get("model", "openoil") == "openoil" else OURS_VERSION,
                      "x": extra_key}, sort_keys=True)
    run_id = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = run_dir(run_id)

    with _lock:
        st = read_status(run_id)
        fut = _futures.get(run_id)
        alive = fut is not None and not fut.done()
        if st and st.get("status") == "done" and (d / "tracks.json").exists():
            return {"id": run_id, "status": "done", **_summary(params)}
        if alive:
            return {"id": run_id, "status": st.get("status", "queued") if st else "queued", **_summary(params)}
        # New, errored, or orphaned (server restarted mid-run): (re)run.
        d.mkdir(parents=True, exist_ok=True)
        (d / "request.json").write_text(json.dumps(params, indent=1))
        runner._write_json(d / "status.json", {"id": run_id, "status": "queued", "progress": 0.0,
                                               "created_at": time.time()})
        _futures[run_id] = pool().submit(runner.run_spill, str(d), params)
    return {"id": run_id, "status": "queued", **_summary(params)}


def status(run_id: str) -> dict | None:
    st = read_status(run_id)
    if st is None:
        return None
    out = {"id": run_id, "status": st.get("status", "queued"), "progress": st.get("progress", 0.0)}
    try:
        out.update(_summary(json.loads((run_dir(run_id) / "request.json").read_text())))
    except Exception:  # noqa: BLE001 - request.json is written before status.json; best effort
        pass
    if st.get("status") == "error":
        out["error"] = st.get("error")
    elif st.get("status") in ("queued", "running"):
        fut = _futures.get(run_id)
        if fut is None:  # orphaned by a restart
            out.update(status="error", error="run interrupted (server restarted); POST again")
        elif fut.done() and fut.exception() is not None:
            out.update(status="error", error=str(fut.exception()))
    return out


def tracks_path(run_id: str) -> Path | None:
    p = run_dir(run_id) / "tracks.json"
    return p if p.exists() else None
