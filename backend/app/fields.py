"""Downsampled surface current u/v grids from cached NetCDF, for the frontend flow layer."""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache

import numpy as np

from . import forcing

U_NAMES = ("x_sea_water_velocity", "eastward_sea_water_velocity", "surface_eastward_sea_water_velocity")
V_NAMES = ("y_sea_water_velocity", "northward_sea_water_velocity", "surface_northward_sea_water_velocity")
DEFAULT_BBOX = (-98.0, 18.0, -80.0, 31.0)  # Gulf of Mexico


def _bbox(s: str | None):
    if not s:
        return DEFAULT_BBOX
    v = [float(x) for x in s.split(",")]
    if len(v) != 4:
        raise ValueError("bbox must be lon_min,lat_min,lon_max,lat_max")
    return tuple(v)


def _unix(t) -> int:
    return int(np.datetime64(t, "s").astype(np.int64))


def _parse(s: str | None):
    if not s:
        return None
    return int(s) if s.isdigit() else _unix(datetime.fromisoformat(s.replace("Z", "")))


def _find_var(ds, names):
    for v in ds.data_vars:
        if ds[v].attrs.get("standard_name") in names or v in names:
            return v
    return None


@lru_cache(maxsize=16)
def _times(path: str, mtime: float) -> tuple[int, ...]:
    import xarray as xr
    with xr.open_dataset(path) as ds:
        tname = next(c for c in ds.coords if np.issubdtype(ds[c].dtype, np.datetime64))
        return tuple(_unix(t) for t in ds[tname].values)


def _current_files(bbox):
    import os
    out = []
    for f in forcing.scan():
        if "current" not in f.kinds or f.t0 is None:
            continue
        lon_c = (bbox[0] + bbox[2]) / 2
        lat_c = (bbox[1] + bbox[3]) / 2
        lon_ok = f.lon_min <= lon_c <= f.lon_max or f.lon_min <= lon_c % 360 <= f.lon_max
        if lon_ok and f.lat_min <= lat_c <= f.lat_max:
            out.append((f.path, os.stat(f.path).st_mtime))
    return out


def list_times(start=None, end=None, bbox=None) -> dict:
    bb = _bbox(bbox)
    t0, t1 = _parse(start), _parse(end)
    ts = set()
    kinds = set()
    for path, mt in _current_files(bb):
        n = len(ts)
        for t in _times(path, mt):
            if (t0 is None or t >= t0) and (t1 is None or t <= t1):
                ts.add(t)
        if len(ts) > n:
            kinds.add("/forcing/live/" in path.replace("\\", "/"))
    out = {"bbox": list(bb), "times": sorted(ts)}
    if kinds == {True}:  # flow layer label: the live forecast model (RTOFS or ESPC), not the Gulf reanalysis
        lf = forcing.live_forecast()
        if lf:
            out["source"] = f"{lf['currents']['model']} forecast"
    return out


def field_at(t: int, bbox=None, step: float = 0.1) -> dict | None:
    import xarray as xr
    bb = _bbox(bbox)
    step = max(float(step), 0.02)
    best = None
    for path, mt in _current_files(bb):
        ts = np.array(_times(path, mt))
        if len(ts) == 0 or not (ts.min() - 3 * 3600 <= t <= ts.max() + 3 * 3600):
            continue
        i = int(np.argmin(np.abs(ts - t)))
        d = abs(int(ts[i]) - t)
        if best is None or d < best[2]:
            best = (path, i, d, mt)
    if best is None:
        return None
    path, i, _, mt = best
    return _field(path, mt, i, tuple(bb), step)


@lru_cache(maxsize=128)  # ~0.2 s to interpolate one step; the flow layer asks for the same steps over and over
def _field(path: str, mtime: float, i: int, bb: tuple, step: float) -> dict:
    import xarray as xr
    with xr.open_dataset(path) as ds:
        uvar, vvar = _find_var(ds, U_NAMES), _find_var(ds, V_NAMES)
        tname = next(c for c in ds.coords if np.issubdtype(ds[c].dtype, np.datetime64))
        u, v = ds[uvar].isel({tname: i}), ds[vvar].isel({tname: i})
        for dim in list(u.dims):  # surface level
            if dim.lower() in ("depth", "z", "lev", "level", "deptht"):
                u, v = u.isel({dim: 0}), v.isel({dim: 0})
        lonn = next(c for c in ("lon", "longitude", "x") if c in u.coords or c in ds.coords)
        latn = next(c for c in ("lat", "latitude", "y") if c in u.coords or c in ds.coords)
        if float(ds[lonn].max()) > 180:
            u = u.assign_coords({lonn: ((u[lonn] + 180) % 360) - 180}).sortby(lonn)
            v = v.assign_coords({lonn: ((v[lonn] + 180) % 360) - 180}).sortby(lonn)
        lons = np.arange(bb[0], bb[2] + 1e-9, step)
        lats = np.arange(bb[1], bb[3] + 1e-9, step)
        ui = u.interp({lonn: lons, latn: lats}).values
        vi = v.interp({lonn: lons, latn: lats}).values
        tt = _unix(ds[tname].values[i])

    def pack(a):  # rows south->north, NaN (land) -> null
        a = np.round(a.astype(float), 3)
        return [None if not np.isfinite(x) else float(x) for x in a.ravel()]

    return {"t": tt, "lon0": bb[0], "lat0": bb[1], "dlon": step, "dlat": step,
            "nx": len(lons), "ny": len(lats), "u": pack(ui), "v": pack(vi),
            "units": "m/s", "source": path.split("/")[-1]}
