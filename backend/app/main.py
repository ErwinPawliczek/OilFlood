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

from . import config, fields, forcing, global_fields, jobs, oils
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
    """Prediction: always on the latest cached LIVE forecast (currents + GFS winds), starting now.
    `volume_bbl` and `oil_type` (id from /api/oils) set the release. If the forecast ends before
    now + duration_h, the run stops there: `truncated: true` + `note`."""
    try:
        return jobs.submit(req, live=True)
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


@app.get("/api/live")
def get_live():
    """The live forecast prediction mode runs on: models, issued-at (model run), valid window, hours from now."""
    lf = forcing.live_forecast()
    if lf is None:
        return JSONResponse({"available": False, "note": "No live forecast is cached on the server."}, status_code=503)
    out = {k: v for k, v in lf.items() if not k.startswith("_")}
    out["available"] = not out["stale"]
    want = 72.0
    out["requested_hours"] = want
    out["short_by_h"] = round(max(0.0, want - out["hours_available"]), 1)
    if out["stale"]:
        out["note"] = (f"The live forecast is out of date (valid {out['valid_from']} to {out['valid_until']}). "
                       "Predictions are disabled until it is refreshed.")
    elif out["short_by_h"] > 0.5:
        out["note"] = (f"The live forecast reaches only {out['hours_available']:.0f} h from now "
                       f"(until {out['valid_until']}); predictions stop there.")
    return out


@app.get("/api/oils")
def get_oils():
    return {"default": oils.DEFAULT_OIL_ID, "oils": oils.OILS}


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
    if not s.get("forcing"):
        raise HTTPException(400, f"No ocean reanalysis exists for {str(s.get('date_start', ''))[:4]} "
                                 "(HYCOM starts in 2001), so there is no real hindcast for this spill.")
    vol_m3 = float(s.get("volume_bbl") or 3000) * 0.158987
    start = s.get("date_start") or s.get("start_time")
    req = SpillRequest(lat=s["lat"], lon=s["lon"], start_time=start, duration_h=duration_h,
                       volume_m3=vol_m3, oil_type=s.get("oil_type") or "IFO 380",
                       n_particles=n_particles)
    try:
        sub = jobs.submit(req, extra_key=f"hist:{spill_id}", snap_km=config.SNAP_KM_HINDCAST)
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
    return {"mode": "cache-only", "data_dirs": [str(d) for d in config.DATA_DIRS],
            "files": [f.to_json() for f in forcing.scan()]}


@app.get("/api/currents")
def get_currents(start: str | None = None, end: str | None = None,
                 bbox: str | None = Query(None, description="lon_min,lat_min,lon_max,lat_max"),
                 layer: str | None = Query(None, description="gulf | global | auto (default: gulf, global if no gulf data)")):
    """Timesteps. layer=gulf: Gulf HYCOM NetCDF (original behaviour). layer=global: RTOFS/HYCOM global PNG fields
    (+ `png` URL template, `vmax`, `windows`). No layer: Gulf, falling back to global when the Gulf has nothing."""
    if layer not in (None, "gulf", "global", "auto"):
        raise HTTPException(400, "layer must be gulf, global or auto")
    if layer == "global":
        return global_fields.list_times(fields._parse(start), fields._parse(end))
    out = fields.list_times(start, end, bbox)
    out["layer"] = "gulf"
    if out["times"]:
        if layer == "auto":
            out["global"] = global_fields.list_times(fields._parse(start), fields._parse(end))
        return out
    g = global_fields.list_times(fields._parse(start), fields._parse(end))
    if not g["times"]:
        return out
    g["bbox"] = out["bbox"]  # same region the caller asked for; /api/currents/{t} without layer serves it
    return g


@app.get("/api/currents/global/{t}.png")
def get_global_png(t: int):
    """Nearest global u/v texture (within 12 h). Header X-Field-Time = the field's unix time."""
    hit = global_fields.find(t)
    if hit is None:
        raise HTTPException(404, "no global current field near that time")
    path, _, tt = hit
    return FileResponse(path, media_type="image/png",
                        headers={"X-Field-Time": str(tt), "Cache-Control": "public, max-age=3600"})


@app.get("/api/currents/{t}")
def get_current_field(t: int, bbox: str | None = None, step: float | None = None,
                      layer: str | None = Query(None, description="gulf | global | auto")):
    """u/v grid. gulf: Gulf NetCDF (0.1 deg default). global: decoded global field (native step default, capped
    at ~260k nodes). auto: global grid with the Gulf field pasted inside 18-31N 98-80W. No layer: gulf, else global."""
    bb = fields._bbox(bbox) if bbox else None
    if layer == "global":
        out = global_fields.field_at(t, bb, step)
    elif layer == "auto":
        gb = list(bb or global_fields.GLOBAL_BBOX)
        if global_fields.bbox_inside_gulf(gb):
            out = fields.field_at(t, bbox, step or 0.1) or global_fields.field_at(t, gb, step)
        else:
            out = global_fields.field_at(t, gb, step)
            if out:
                ib = (max(gb[0], global_fields.GULF_BOX[0]), max(gb[1], global_fields.GULF_BOX[1]),
                      min(gb[2], global_fields.GULF_BOX[2]), min(gb[3], global_fields.GULF_BOX[3]))
                if ib[0] < ib[2] and ib[1] < ib[3]:
                    gulf = fields.field_at(t, ",".join(map(str, ib)), out["dlon"])
                    out = global_fields.merge_gulf(out, gulf)
    else:
        out = fields.field_at(t, bbox, step or 0.1)
        if out is None:  # no Gulf NetCDF for that time: same bbox (default Gulf) from the global field
            out = global_fields.field_at(t, bb or fields.DEFAULT_BBOX, step or 0.1)
    if out is None:
        raise HTTPException(404, "no cached current field covers that time")
    return out


@app.get("/api/fields/currents")
def get_fields_currents(t: int, bbox: str | None = None, step: float | None = None, layer: str | None = None):
    return get_current_field(t, bbox, step, layer)


@app.get("/api/health")
def health():
    return {"ok": True}


# --- static frontend -------------------------------------------------------------

if config.WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=config.WEB_DIST, html=True), name="web")
