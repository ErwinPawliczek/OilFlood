"""Shared helpers for OilFlood forcing downloads.

Layout (all gitignored):
  data/raw/<window>/...            per-chunk downloads (resumable, safe to delete)
  data/cache/forcing/<window>/currents.nc   OpenDrift-ready surface currents
  data/cache/forcing/<window>/winds.nc      OpenDrift-ready 10 m winds
  data/cache/fields/<window>/               downsampled JSON current grids for the frontend
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests
import xarray as xr

DATA = Path(__file__).resolve().parent
RAW = DATA / "raw"
CACHE = DATA / "cache"
FORCING = CACHE / "forcing"
FIELDS = CACHE / "fields"
SPILLS = DATA / "spills"

# Gulf of Mexico box (lon_min, lat_min, lon_max, lat_max)
BOX = (-98.0, 18.0, -80.0, 31.0)

UA = {"User-Agent": "OilFlood-data-fetch/0.1 (hackathon research)"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def parse_time(s: str) -> datetime:
    s = s.strip().replace("Z", "")
    if len(s) == 10:
        s += "T00:00:00"
    return datetime.fromisoformat(s).replace(tzinfo=None)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def download(url: str, dest: Path, params=None, tries: int = 4, timeout: int = 600, min_bytes: int = 200) -> Path:
    """Idempotent download: skips if dest exists, writes via .part, retries with backoff."""
    if dest.exists() and dest.stat().st_size >= min_bytes:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    last = None
    for k in range(tries):
        try:
            with requests.get(url, params=params, headers=UA, stream=True, timeout=timeout) as r:
                r.raise_for_status()
                with open(part, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            if part.stat().st_size < min_bytes:
                raise IOError(f"short response ({part.stat().st_size} B): {part.read_bytes()[:200]!r}")
            part.rename(dest)
            return dest
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  retry {k + 1}/{tries} {dest.name}: {e}")
            time.sleep(5 * (k + 1))
    raise RuntimeError(f"download failed {url}: {last}")


def to_opendrift(ds: xr.Dataset, rename: dict, kind: str, source: str) -> xr.Dataset:
    """Normalise to time/lat/lon (lon in -180..180, ascending), drop depth,
    and set CF standard names OpenDrift's reader_netCDF_CF_generic recognises."""
    ds = ds.rename({k: v for k, v in rename.items() if k in ds.variables or k in ds.dims})
    for d in list(ds.dims):
        if d not in ("time", "lat", "lon"):
            ds = ds.isel({d: 0}, drop=True) if ds.sizes[d] >= 1 else ds
    drop = [v for v in ds.variables if v not in ("time", "lat", "lon") and v not in rename.values()]
    ds = ds.drop_vars(drop, errors="ignore")
    if float(ds.lon.max()) > 180:
        ds = ds.assign_coords(lon=((ds.lon + 180) % 360) - 180)
    ds = ds.sortby("lon").sortby("lat").sortby("time")
    _, idx = np.unique(ds.time.values, return_index=True)
    ds = ds.isel(time=np.sort(idx))
    ds["lon"].attrs = {"standard_name": "longitude", "units": "degrees_east", "axis": "X"}
    ds["lat"].attrs = {"standard_name": "latitude", "units": "degrees_north", "axis": "Y"}
    ds["time"].attrs = {"standard_name": "time", "axis": "T"}
    names = {
        "current": {"x_sea_water_velocity": "eastward surface sea water velocity",
                    "y_sea_water_velocity": "northward surface sea water velocity"},
        "wind": {"x_wind": "eastward 10 m wind", "y_wind": "northward 10 m wind"},
    }[kind]
    for v, ln in names.items():
        # HYCOM fill (1.27e30) is not always decoded as NaN; mask anything unphysical
        ds[v] = ds[v].where(np.abs(ds[v]) < 100).astype("float32")
        ds[v].attrs = {"standard_name": v, "long_name": ln, "units": "m s-1"}
    ds.attrs = {"Conventions": "CF-1.6", "title": f"OilFlood {kind} forcing", "source": source,
                "history": f"{iso(now_utc())} built by OilFlood data/ scripts"}
    return ds


def write_nc(ds: xr.Dataset, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    enc = {v: {"zlib": True, "complevel": 4, "_FillValue": np.float32(np.nan)} for v in ds.data_vars}
    enc["time"] = {"units": "seconds since 1970-01-01 00:00:00", "calendar": "standard", "dtype": "float64"}
    tmp = dest.with_name(dest.name + ".tmp")
    ds.load().to_netcdf(tmp, encoding=enc)
    tmp.replace(dest)
    return dest


def verify(path: Path, kind: str) -> str:
    """Open with xarray and check standard names; returns a one-line summary."""
    need = {"current": ("x_sea_water_velocity", "y_sea_water_velocity"), "wind": ("x_wind", "y_wind")}[kind]
    with xr.open_dataset(path) as ds:
        sn = {ds[v].attrs.get("standard_name") for v in ds.data_vars}
        missing = [n for n in need if n not in sn]
        if missing:
            raise ValueError(f"{path}: missing standard_name {missing}")
        t = ds.time.values
        sp = np.hypot(ds[need[0]].values, ds[need[1]].values)
        lim = {"current": 5.0, "wind": 80.0}[kind]
        if not np.isfinite(sp).any() or np.nanmax(sp) > lim:
            raise ValueError(f"{path}: implausible speeds (max {np.nanmax(sp):.3g} m/s > {lim})")
        return (f"{path.relative_to(DATA)}: {len(t)} steps {str(t[0])[:16]}..{str(t[-1])[:16]} "
                f"grid {ds.sizes['lat']}x{ds.sizes['lon']} max|v|={np.nanmax(sp):.2f} m/s "
                f"{path.stat().st_size / 1e6:.1f} MB")


def day_chunks(start: datetime, end: datetime, hours: int = 24):
    t = start
    while t < end:
        t2 = min(t + timedelta(hours=hours), end)
        yield t, t2
        t = t2
