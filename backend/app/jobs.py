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

from pydantic import BaseModel, Field

from . import config, forcing, runner

MODEL_VERSION = "openoil-v3-real"  # v3: real forcing only, no constant fallback


class SpillRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=360)
    start_time: datetime | None = None
    duration_h: float = Field(72, gt=0, le=240)
    volume_m3: float = Field(500, gt=0)
    oil_type: str = "IFO 380"
    n_particles: int = Field(2000, ge=10, le=20000)
    wind_drift_factor: float = Field(0.02, ge=0, le=0.1)


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


def _normalize(req: SpillRequest) -> dict:
    start = req.start_time or forcing.default_start_time()
    if start.tzinfo is not None:
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
    start = start.replace(second=0, microsecond=0)
    return {
        "lat": round(req.lat, 4),
        "lon": round(((req.lon + 180) % 360) - 180, 4),
        "start_time": start.isoformat(),
        "duration_h": float(req.duration_h),
        "volume_m3": float(req.volume_m3),
        "oil_type": req.oil_type,
        "n_particles": int(req.n_particles),
        "wind_drift_factor": float(req.wind_drift_factor),
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


def plan(params: dict, snap_km: float) -> dict:
    """Attach real forcing files to a normalized request, clip the duration to the
    forcing's time coverage and move the seed off model land. Raises NoForcing /
    forcing.SeedOnLand (both ValueError) with a user-facing message."""
    start = datetime.fromisoformat(params["start_time"])
    end = start + timedelta(hours=params["duration_h"])
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
    params.update(lon=lon, lat=lat, duration_h=round(hours, 2), seed_moved_km=moved,
                  requested_lonlat=[params["lon"], params["lat"]],
                  requested_duration_h=params["duration_h"],
                  forcing_files=[cur.to_json(), wind.to_json()])
    return params


def submit(req: SpillRequest, extra_key: str = "", snap_km: float | None = None) -> dict:
    params = plan(_normalize(req), config.SNAP_KM_PREDICTION if snap_km is None else snap_km)
    key = json.dumps({**params, "v": MODEL_VERSION, "x": extra_key}, sort_keys=True)
    run_id = hashlib.sha1(key.encode()).hexdigest()[:16]
    d = run_dir(run_id)

    with _lock:
        st = read_status(run_id)
        fut = _futures.get(run_id)
        alive = fut is not None and not fut.done()
        if st and st.get("status") == "done" and (d / "tracks.json").exists():
            return {"id": run_id, "status": "done"}
        if alive:
            return {"id": run_id, "status": st.get("status", "queued") if st else "queued"}
        # New, errored, or orphaned (server restarted mid-run): (re)run.
        d.mkdir(parents=True, exist_ok=True)
        (d / "request.json").write_text(json.dumps(params, indent=1))
        runner._write_json(d / "status.json", {"id": run_id, "status": "queued", "progress": 0.0,
                                               "created_at": time.time()})
        _futures[run_id] = pool().submit(runner.run_spill, str(d), params)
    return {"id": run_id, "status": "queued"}


def status(run_id: str) -> dict | None:
    st = read_status(run_id)
    if st is None:
        return None
    out = {"id": run_id, "status": st.get("status", "queued"), "progress": st.get("progress", 0.0)}
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
