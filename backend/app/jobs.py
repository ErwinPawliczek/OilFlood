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

MODEL_VERSION = "openoil-v2"


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


def submit(req: SpillRequest, extra_key: str = "") -> dict:
    params = _normalize(req)
    start = datetime.fromisoformat(params["start_time"])
    end = start + timedelta(hours=params["duration_h"])
    files = forcing.select(params["lon"], params["lat"], start, end)
    if config.FORCING_MODE == "cache" and not files:
        raise ValueError("No cached forcing covers this location/time (OILFLOOD_FORCING=cache)")
    params["forcing_files"] = [f.to_json() for f in files]

    key = json.dumps({**params, "v": MODEL_VERSION, "x": extra_key,
                      "fb": [config.CONST_CURRENT_U, config.CONST_CURRENT_V,
                             config.CONST_WIND_U, config.CONST_WIND_V]}, sort_keys=True)
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
