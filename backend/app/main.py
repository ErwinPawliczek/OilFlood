"""OilFlood backend: FastAPI on :5200. Run: uvicorn app.main:app --port 5200 (from backend/)."""
from __future__ import annotations

import gzip
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import anyio
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import briefing, config, ensemble, fields, forcing, global_fields, jobs, oils, response
from .jobs import SpillRequest

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    jobs.shutdown()
    ensemble.shutdown()


app = FastAPI(title="OilFlood", lifespan=lifespan)
# level 6: ~3x faster than the default 9 on the 9 MB track JSONs for ~2 % more bytes
app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=6)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class _JsonFile(FileResponse):
    """JSON file; gzip-accepting clients get a cached `<file>.gz` sidecar (written once, served with
    Content-Encoding: gzip, so GZipMiddleware leaves it alone). Track files are ~9 MB raw / ~1.5 MB gzipped."""

    async def __call__(self, scope, receive, send):
        accept = dict(scope.get("headers") or []).get(b"accept-encoding", b"")
        gz = await anyio.to_thread.run_sync(_gz_sidecar, Path(self.path)) if b"gzip" in accept else None
        if gz is None:
            return await super().__call__(scope, receive, send)
        r = FileResponse(gz, media_type="application/json", headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return await r(scope, receive, send)


def _gz_sidecar(p: Path) -> Path | None:
    try:
        st = p.stat()
        if st.st_size < 4096:
            return None
        gz = p.with_name(p.name + ".gz")
        if not gz.exists() or gz.stat().st_mtime < st.st_mtime:
            tmp = gz.with_name(f"{gz.name}.{os.getpid()}.tmp")
            tmp.write_bytes(gzip.compress(p.read_bytes(), 6))
            os.replace(tmp, gz)
        return gz
    except OSError:
        return None


def _json_file(p: Path):
    return _JsonFile(p, media_type="application/json")


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


def _response_for(run_id: str, brief: bool, spill_id: str | None = None):
    """Response outline for a finished run (cached as runs/<id>/response.json; LLM text cached separately)."""
    try:
        d = jobs.run_dir(run_id)
    except ValueError:
        raise HTTPException(400, "bad id")
    p = jobs.tracks_path(run_id)
    if p is None:
        st = jobs.status(run_id)
        if st is None:
            raise HTTPException(404, "unknown spill id")
        return JSONResponse(st, status_code=409 if st["status"] == "error" else 202)
    cache = d / "response.json"
    out = None
    if cache.exists():
        try:
            out = json.loads(cache.read_text())
        except json.JSONDecodeError:
            out = None
        if out and out.get("version") != response.VERSION:
            out = None
    if out is None:
        req = json.loads((d / "request.json").read_text())
        out = response.build(json.loads(p.read_text()), req, run_id=run_id, spill_id=spill_id,
                             ens=ensemble.load(d) if req.get("ensemble") else None)
        cache.write_text(json.dumps(out))
    if brief:
        prov = briefing.provider()
        bc = d / "briefing.json"
        b = None
        if prov and bc.exists():
            b = json.loads(bc.read_text())
            if b.get("version") != response.VERSION or b.get("model") != prov[2] or not b.get("text"):
                b = None
        if b is None:
            b = briefing.generate(out)
            if b.get("text"):
                bc.write_text(json.dumps({**b, "version": response.VERSION}))
        out["briefing"] = b
    else:
        out["briefing"] = {"text": None, "note": "not requested"}
    return out


@app.get("/api/spill/{run_id}/response")
def get_spill_response(run_id: str, brief: bool = True):
    """First-responder action outline for a finished prediction run (docs/RESPONSE.md): ETA to landfall,
    priority shorelines (NOAA ESI + arrival time), where to boom first, dispersant / in-situ burn / skimming
    windows, closures, cited precedents. `briefing.text` = optional LLM plain-English summary (null if no key).
    202 + status while the run is still going."""
    return _response_for(run_id, brief)


def _finished_dir(run_id: str):
    """(run dir, None) for a finished run, else (None, error response)."""
    try:
        d = jobs.run_dir(run_id)
    except ValueError:
        raise HTTPException(400, "bad id")
    st = jobs.status(run_id)
    if st is None:
        raise HTTPException(404, "unknown spill id")
    if st["status"] != "done":
        return None, JSONResponse(st, status_code=409 if st["status"] == "error" else 202)
    return d, None


@app.get("/api/spill/{run_id}/probability")
def get_spill_probability(run_id: str, t: float | None = Query(None, description="hours since start (nearest 3 h step)")):
    """Ensemble probability products (docs/ENSEMBLE.md section 3): P(oil present) + expected surface mass on a 0.05 deg
    grid every 3 h, per-shoreline-segment P(oiled by T) + ETA P10/P50/P90, member centroids with 50/90 % ellipses."""
    d, err = _finished_dir(run_id)
    if err is not None:
        return err
    ens = ensemble.load(d)
    if ens is None:
        raise HTTPException(404, "This run has no ensemble: POST /api/spill with model=ours and ensemble=N (2-30).")
    return ensemble.probability_view(ens, run_id, t)


@app.get("/api/spill/{run_id}/forces")
def get_spill_forces(run_id: str):
    """Per 3 h step: mean drift decomposition (current, wind drift, Stokes, ML, diffusion) + oil budget
    (docs/ENSEMBLE.md section 4). Ensemble runs: mean over members, plus the control run."""
    d, err = _finished_dir(run_id)
    if err is not None:
        return err
    ens = ensemble.load(d)
    if ens is not None:
        return {"run_id": run_id, "source": "ensemble", "members": ens["members_ok"], "step_h": ens["step_h"],
                "components": ["current", "wind", "stokes", "ml", "diffusion"], "steps": ens["forces"],
                "control": {"steps": ens["control_forces"]}}
    tr = json.loads((d / "tracks.json").read_text())
    meta = tr.get("meta") or {}
    if not meta.get("forces"):
        raise HTTPException(404, "Force decomposition is only computed by our model (model=ours, ours-v4 T24+).")
    return {"run_id": run_id, "source": "control", "members": 0, "step_h": 3,
            "components": ["current", "wind", "stokes", "ml", "diffusion"],
            "steps": ensemble._forces_steps([meta["forces"]], [tr["budget"]], float(tr["times"][0]))}


@app.get("/api/model/params")
def get_model_params():
    """Schema of our model's user params ("build your own model") + the ensemble perturbation design."""
    from .ourmodel import params as MP
    return MP.schema()


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


@app.get("/api/backtest")
def get_backtest(spill: str | None = Query(None, description="catalog id, e.g. dwh; omit for all cases"),
                 detail: bool = Query(False, description="include per-start rows (DWH)")):
    """P5 backtest scores (docs/BACKTEST.md): OpenOil vs ours +ML vs ours no-ML vs observed.
    Precomputed by `python -m app.backtest` into data/backtest/results.json."""
    p = config.REPO_ROOT / "data" / "backtest" / "results.json"
    if not p.exists():
        raise HTTPException(404, "No backtest results; run `python -m app.backtest` in backend/.")
    res = json.loads(p.read_text())
    cases = res.get("cases", [])
    if not detail:
        cases = [{k: v for k, v in c.items() if k != "per_start"} for c in cases]
    if spill is not None:
        cases = [c for c in cases if c["id"] == spill]
        if not cases:
            return {**{k: v for k, v in res.items() if k != "cases"}, "cases": [],
                    "note": f"No backtest for {spill!r}: only spills with observed positions and cached forcing "
                            "are scored (dwh, texas_city_y, main_pass_2023)."}
    cal = _calibration()
    if cal is not None and not detail:
        cal = {k: v for k, v in cal.items() if k != "per_start"}
    return {**{k: v for k, v in res.items() if k != "cases"}, "cases": cases, "ensemble_calibration": cal}


@app.get("/api/models")
def get_models():
    """The two trajectory models POST /api/spill (and hindcasts) can run, with our ML model card's
    held-out skill (docs/OURMODEL.md)."""
    from .ourmodel import ml
    card = {}
    if ml.CARD_PATH.exists():
        card = json.loads(ml.CARD_PATH.read_text())
    sc = card.get("scores", {})
    td = card.get("training_data", {})
    skill = None
    if sc:
        skill = {"drifters": td.get("drifters"), "rows": td.get("rows"),
                 "validation": card.get("validation"), "trained_at": card.get("trained_at"),
                 "traj_24h": sc.get("traj_24h_all"), "traj_48h": sc.get("traj_48h_all"),
                 "velocity": sc.get("velocity_all")}
    return {"default": "openoil", "models": [
        {"id": "openoil", "label": jobs.MODEL_LABELS["openoil"], "short": "OpenOil",
         "summary": "OpenDrift OpenOil 1.14: currents + 2 % wind + Stokes drift, random walk, "
                    "NOAA ADIOS weathering, stranding on the GSHHG coastline."},
        {"id": "ours", "label": jobs.MODEL_LABELS["ours"], "short": "Ours",
         "summary": "Our Lagrangian model: currents + 3 % wind, Fay spreading, Fingas evaporation, "
                    "Mackay emulsification/dispersion, beaching. Optional experimental ML drift correction "
                    "(trained on NOAA drifters; ml=true), off by default.",
         "ml_default": False,
         "ml_note": "Experimental: in the oil backtest (docs/BACKTEST.md) the ML correction gives no gain on "
                    "Deepwater Horizon and strands 62 % of the oil on the Mississippi Delta at Main Pass 2023 "
                    "(observed: none), so it is off by default.",
         "skill": skill, "params_url": "/api/model/params",
         "ensemble": {"max_members": 30, "default_members": 20, "probability_url": "/api/spill/{id}/probability",
                      "forces_url": "/api/spill/{id}/forces"},
         "calibration": _calibration(summary_only=True)},
    ]}


def _calibration(summary_only: bool = False):
    """DWH ensemble calibration (python -m app.backtest.ensemble_calib, docs/METHODOLOGY.md)."""
    p = config.REPO_ROOT / "data" / "backtest" / "ensemble_calibration.json"
    if not p.exists():
        return None
    c = json.loads(p.read_text())
    return {k: v for k, v in c.items() if k not in ("per_start", "perturbations")} if summary_only else c


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
    """Full catalog records, including the evolution notes and `response` log
    (schema: docs/ARCHITECTURE.md, "Historical spill catalog")."""
    return _catalog()


@app.get("/api/spills/{spill_id}")
def get_spill_record(spill_id: str):
    return _spill(spill_id)


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
def get_spill_hindcast(spill_id: str, n_particles: int = 2000, duration_h: float = 72,
                       model: str = Query("openoil", pattern="^(openoil|ours)$"), ml: bool = False):
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
                       n_particles=n_particles, model=model, ml=ml)
    try:
        sub = jobs.submit(req, extra_key=f"hist:{spill_id}", snap_km=config.SNAP_KM_HINDCAST)
    except ValueError as e:
        raise HTTPException(400, str(e))
    p = jobs.tracks_path(sub["id"])
    if p is not None and sub["status"] == "done":
        return _json_file(p)
    st = jobs.status(sub["id"]) or sub
    return JSONResponse(st, status_code=409 if st.get("status") == "error" else 202)


@app.get("/api/spills/{spill_id}/response")
def get_spill_hindcast_response(spill_id: str, n_particles: int = 2000, duration_h: float = 72,
                                model: str = Query("openoil", pattern="^(openoil|ours)$"), ml: bool = False,
                                brief: bool = True):
    """Response outline for a catalog spill's hindcast (same shape as /api/spill/{id}/response; the spill's
    own response log is left out of the precedents). Starts the hindcast if needed (202 until done)."""
    s = _spill(spill_id)
    if not s.get("forcing"):
        raise HTTPException(400, "No hindcast exists for this spill (no ocean reanalysis before 2001).")
    vol_m3 = float(s.get("volume_bbl") or 3000) * 0.158987
    req = SpillRequest(lat=s["lat"], lon=s["lon"], start_time=s.get("date_start") or s.get("start_time"),
                       duration_h=duration_h, volume_m3=vol_m3, oil_type=s.get("oil_type") or "IFO 380",
                       n_particles=n_particles, model=model, ml=ml)
    try:
        sub = jobs.submit(req, extra_key=f"hist:{spill_id}", snap_km=config.SNAP_KM_HINDCAST)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _response_for(sub["id"], brief, spill_id=spill_id)


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

class _Web(StaticFiles):
    """web/dist: Vite's hashed /assets/* never change -> cache for a year; index.html is always revalidated."""

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if path.startswith("assets/")
                                      else "no-cache")
        return r


if config.WEB_DIST.is_dir():
    app.mount("/", _Web(directory=config.WEB_DIST, html=True), name="web")
