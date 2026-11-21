"""Global surface-current fields (RTOFS live + HYCOM global hindcasts) exported by data/fetch_global.py.

Files: <data_dir>/cache/fields/global/<window>/{index.json, <t>.png, <t>.json}. PNG encoding is the same as the
Gulf fields: R=(u/vmax+1)*127.5, G=(v/vmax+1)*127.5, B=speed/vmax*255, A=255 water / 0 land, rows north->south,
regular cell-centred global grid (lon0 = -180 + d/2, lat0 = -90 + d/2).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import config

GULF_BOX = (-98.0, 18.0, -80.0, 31.0)  # lon_min, lat_min, lon_max, lat_max: Gulf HYCOM is drawn/served here
GLOBAL_BBOX = [-180.0, -90.0, 180.0, 90.0]


def _dirs() -> list[Path]:
    return [d / "cache" / "fields" / "global" for d in config.DATA_DIRS]


@lru_cache(maxsize=64)
def _read_index(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text())


def windows() -> list[dict]:
    """All exported global windows: [{window, dir, index}] (first data dir wins on duplicate names)."""
    out, seen = [], set()
    for root in _dirs():
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            p = d / "index.json"
            if d.name in seen or not p.is_file():
                continue
            seen.add(d.name)
            out.append({"window": d.name, "dir": d, "index": _read_index(str(p), p.stat().st_mtime)})
    return out


def list_times(t0: int | None = None, t1: int | None = None) -> dict:
    ts, wins, meta = set(), [], None
    for w in windows():
        idx = w["index"]
        sel = [t for t in idx["times"] if (t0 is None or t >= t0) and (t1 is None or t <= t1)]
        if not sel:
            continue
        ts.update(sel)
        wins.append({"window": w["window"], "source": idx.get("source", ""), "t_start": sel[0], "t_end": sel[-1],
                     "n": len(sel), "dlon": idx["dlon"]})
        meta = meta or idx
    return {"layer": "global", "bbox": GLOBAL_BBOX, "times": sorted(ts), "windows": wins,
            "png": "/api/currents/global/{t}.png", "vmax": meta["vmax"] if meta else None,
            "gulf_box": list(GULF_BOX),
            "png_encoding": meta.get("png_encoding") if meta else None}


def find(t: int, max_gap_s: int = 12 * 3600) -> tuple[Path, dict, int] | None:
    """Nearest exported field to unix time t: (png path, index, field time)."""
    best = None
    for w in windows():
        times = w["index"]["times"]
        if not times:
            continue
        arr = np.asarray(times)
        i = int(np.argmin(np.abs(arr - t)))
        gap = abs(int(arr[i]) - t)
        # prefer the window whose span contains t, then the smallest gap
        inside = times[0] <= t <= times[-1]
        key = (not inside, gap)
        if gap <= max_gap_s and (best is None or key < best[0]):
            best = (key, w["dir"] / f"{int(arr[i])}.png", w["index"], int(arr[i]))
    return None if best is None else best[1:]


@lru_cache(maxsize=48)
def _decode(path: str, mtime: float, vmax: float):
    from PIL import Image
    a = np.asarray(Image.open(path).convert("RGBA"))[::-1]  # rows south->north
    land = a[..., 3] == 0
    u = (a[..., 0].astype(np.float32) / 127.5 - 1) * vmax
    v = (a[..., 1].astype(np.float32) / 127.5 - 1) * vmax
    u[land] = np.nan
    v[land] = np.nan
    return u, v


def decoded(t: int):
    hit = find(t)
    if hit is None:
        return None
    path, idx, tt = hit
    u, v = _decode(str(path), path.stat().st_mtime, float(idx["vmax"]))
    return u, v, idx, tt, path


def sample(u, v, idx, lons, lats):
    """Bilinear sample of the cell-centred global grid at node lons (1-D) x lats (1-D), wrapping in longitude.
    Returns (ny, nx) arrays; a NaN neighbour (land) gives NaN, like the Gulf JSON grids."""
    nx, ny = idx["nx"], idx["ny"]
    x = ((np.asarray(lons) - idx["lon0"]) / idx["dlon"]) % nx
    y = np.clip((np.asarray(lats) - idx["lat0"]) / idx["dlat"], 0, ny - 1)
    i0 = np.floor(x).astype(int)
    i1 = (i0 + 1) % nx
    fx = (x - i0)[None, :]
    j0 = np.clip(np.floor(y).astype(int), 0, ny - 1)
    j1 = np.clip(j0 + 1, 0, ny - 1)
    fy = (y - j0)[:, None]

    def bl(a):
        return ((a[j0][:, i0] * (1 - fx) + a[j0][:, i1] * fx) * (1 - fy)
                + (a[j1][:, i0] * (1 - fx) + a[j1][:, i1] * fx) * fy)
    return bl(u), bl(v)


def field_at(t: int, bbox=None, step: float | None = None, max_points: int = 260_000) -> dict | None:
    """JSON grid (same shape as the Gulf /api/currents/{t}) decoded from the nearest global PNG."""
    got = decoded(t)
    if got is None:
        return None
    u, v, idx, tt, path = got
    bb = list(bbox) if bbox else list(GLOBAL_BBOX)
    bb[1], bb[3] = max(bb[1], -89.9), min(bb[3], 89.9)
    native = float(idx["dlon"])
    if not step:
        step = native
    step = max(float(step), native / 2)
    # cap payload size: coarsen until the grid has <= max_points nodes
    while ((bb[2] - bb[0]) / step + 1) * ((bb[3] - bb[1]) / step + 1) > max_points:
        step *= 2
    lons = np.arange(bb[0], bb[2] + 1e-9, step)
    lats = np.arange(bb[1], bb[3] + 1e-9, step)
    ui, vi = sample(u, v, idx, lons, lats)
    return {"t": tt, "lon0": float(lons[0]), "lat0": float(lats[0]), "dlon": step, "dlat": step,
            "nx": len(lons), "ny": len(lats), "u": _pack(ui), "v": _pack(vi), "units": "m/s",
            "layer": "global", "source": f"global/{path.parent.name}/{path.name}"}


def _pack(a):
    a = np.round(a.astype(float), 3)
    return [None if not np.isfinite(x) else float(x) for x in a.ravel()]


def in_gulf(lon: float, lat: float) -> bool:
    return GULF_BOX[0] <= lon <= GULF_BOX[2] and GULF_BOX[1] <= lat <= GULF_BOX[3]


def bbox_inside_gulf(bb) -> bool:
    return bb[0] >= GULF_BOX[0] and bb[1] >= GULF_BOX[1] and bb[2] <= GULF_BOX[2] and bb[3] <= GULF_BOX[3]


def merge_gulf(glob: dict, gulf: dict | None) -> dict:
    """Paste Gulf values onto a global grid for nodes inside GULF_BOX (where the Gulf grid has water)."""
    if not gulf:
        return glob
    nx, ny = glob["nx"], glob["ny"]
    lons = glob["lon0"] + np.arange(nx) * glob["dlon"]
    lats = glob["lat0"] + np.arange(ny) * glob["dlat"]
    gu = np.array([np.nan if x is None else x for x in gulf["u"]], float).reshape(gulf["ny"], gulf["nx"])
    gv = np.array([np.nan if x is None else x for x in gulf["v"]], float).reshape(gulf["ny"], gulf["nx"])
    u = np.array([np.nan if x is None else x for x in glob["u"]], float).reshape(ny, nx)
    v = np.array([np.nan if x is None else x for x in glob["v"]], float).reshape(ny, nx)
    ii = np.round((lons - gulf["lon0"]) / gulf["dlon"]).astype(int)
    jj = np.round((lats - gulf["lat0"]) / gulf["dlat"]).astype(int)
    for j in range(ny):
        if not (GULF_BOX[1] <= lats[j] <= GULF_BOX[3]) or not 0 <= jj[j] < gulf["ny"]:
            continue
        for i in range(nx):
            if GULF_BOX[0] <= lons[i] <= GULF_BOX[2] and 0 <= ii[i] < gulf["nx"]:
                u[j, i], v[j, i] = gu[jj[j], ii[i]], gv[jj[j], ii[i]]
    out = dict(glob)
    out.update(u=_pack(u), v=_pack(v), layer="auto", source=f"{glob['source']} + gulf {gulf.get('source', '')}")
    return out
