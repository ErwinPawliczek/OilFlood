"""OilFlood backend: FastAPI on :5200. Run: uvicorn app.main:app --port 5200 (from backend/)."""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, fields, forcing, jobs
from .jobs import SpillRequest

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    jobs.shutdown()


app = FastAPI(title="OilFlood", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _json_file(p: Path):
    return FileResponse(p, media_type="application/json")


# --- prediction mode -----------------------------------------------------------

@app.post("/api/spill")
def post_spill(req: SpillRequest):
    try:
        return jobs.submit(req)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/spill/{run_id}")
def get_spill(run_id: str):
    try:
        st = jobs.status(run_id)
    except ValueError:
        raise HTTPException(400, "bad id")
    if st is None:
        raise HTTPException(404, "unknown spill id")
    return st


@app.get("/api/spill/{run_id}/tracks")
def get_spill_tracks(run_id: str):
    try:
        p = jobs.tracks_path(run_id)
    except ValueError:
        raise HTTPException(400, "bad id")
    if p is None:
        st = jobs.status(run_id)
        if st is None:
            raise HTTPException(404, "unknown spill id")
        return JSONResponse(st, status_code=409 if st["status"] == "error" else 202)
    return _json_file(p)


# --- historical mode -----------------------------------------------------------

def _catalog_path() -> Path | None:
    for d in config.DATA_DIRS:
        p = d / "spills" / "catalog.json"
        if p.exists():
            return p
    return None


def _catalog() -> list[dict]:
    p = _catalog_path()
    if p is None:
        return []
    data = json.loads(p.read_text())
    return data["spills"] if isinstance(data, dict) and "spills" in data else data


def _spill(spill_id: str) -> dict:
    for s in _catalog():
        if str(s.get("id")) == spill_id:
            return s
    raise HTTPException(404, "unknown historical spill")


@app.get("/api/spills")
def get_spills():
    return _catalog()


@app.get("/api/spills/{spill_id}/footprints")
def get_footprints(spill_id: str):
    s = _spill(spill_id)
    out = []
    for d in config.DATA_DIRS:
        fp = d / "spills" / spill_id / "footprints"
        if not fp.is_dir():
            continue
        for f in sorted(list(fp.glob("*.geojson")) + list(fp.glob("*.json"))):
            m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", f.name)
            t = int(datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc).timestamp()) if m else None
            out.append({"t": t, "name": f.stem, "geojson": json.loads(f.read_text())})
        break
    return {"id": s.get("id"), "footprints": out}


@app.get("/api/spills/{spill_id}/tracks")
def get_spill_hindcast(spill_id: str, n_particles: int = 2000, duration_h: float = 72):
    """Hindcast for a catalog spill. Starts the run on first call; returns 202 +
    status until done, then the same JSON shape as /api/spill/{id}/tracks."""
    s = _spill(spill_id)
    vol_m3 = float(s.get("volume_bbl") or 3000) * 0.158987
    start = s.get("date_start") or s.get("start_time")
    req = SpillRequest(lat=s["lat"], lon=s["lon"], start_time=start, duration_h=duration_h,
                       volume_m3=vol_m3, oil_type=s.get("oil_type") or "IFO 380",
                       n_particles=n_particles)
    try:
        sub = jobs.submit(req, extra_key=f"hist:{spill_id}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    p = jobs.tracks_path(sub["id"])
    if p is not None and sub["status"] == "done":
        return _json_file(p)
    st = jobs.status(sub["id"]) or sub
    return JSONResponse(st, status_code=409 if st.get("status") == "error" else 202)


# --- currents (flow layer) ----------------------------------------------------

@app.get("/api/forcing")
def get_forcing():
    """Debug: which forcing files the backend sees."""
    return {"mode": config.FORCING_MODE, "data_dirs": [str(d) for d in config.DATA_DIRS],
            "files": [f.to_json() for f in forcing.scan()]}


@app.get("/api/currents")
def get_currents(start: str | None = None, end: str | None = None,
                 bbox: str | None = Query(None, description="lon_min,lat_min,lon_max,lat_max")):
    return fields.list_times(start, end, bbox)


@app.get("/api/currents/{t}")
def get_current_field(t: int, bbox: str | None = None, step: float = 0.1):
    out = fields.field_at(t, bbox, step)
    if out is None:
        raise HTTPException(404, "no cached current field covers that time")
    return out


@app.get("/api/fields/currents")
def get_fields_currents(t: int, bbox: str | None = None, step: float = 0.1):
    return get_current_field(t, bbox, step)


@app.get("/api/health")
def health():
    return {"ok": True}


# --- static frontend -------------------------------------------------------------

if config.WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=config.WEB_DIST, html=True), name="web")
