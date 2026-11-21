"""Discover forcing NetCDF files in data/cache and pick the ones covering a run.

Any CF NetCDF that OpenDrift's reader_netCDF_CF_generic can open works; we only
look at standard_name attributes, the time axis and the lon/lat extent.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import config

log = logging.getLogger(__name__)

CURRENT_NAMES = {"x_sea_water_velocity", "eastward_sea_water_velocity", "surface_eastward_sea_water_velocity"}
WIND_NAMES = {"x_wind", "eastward_wind"}
STOKES_NAMES = {"sea_surface_wave_stokes_drift_x_velocity"}

# Common variable names that often lack a CF standard_name (ERA5 NetCDF, HYCOM, CMEMS waves).
NAME_TO_STANDARD = {
    "u10": "x_wind", "v10": "y_wind",
    "water_u": "x_sea_water_velocity", "water_v": "y_sea_water_velocity",
    "uo": "x_sea_water_velocity", "vo": "y_sea_water_velocity",
    "VSDX": "sea_surface_wave_stokes_drift_x_velocity", "VSDY": "sea_surface_wave_stokes_drift_y_velocity",
}


@dataclass
class ForcingFile:
    path: str
    kinds: set = field(default_factory=set)  # {"current","wind","stokes"}
    mapping: dict = field(default_factory=dict)  # var name -> standard_name, for vars missing one
    t0: datetime | None = None
    t1: datetime | None = None
    lon_min: float = -180
    lon_max: float = 180
    lat_min: float = -90
    lat_max: float = 90

    def covers(self, lon: float, lat: float, start: datetime, end: datetime) -> bool:
        if self.t0 is None or self.t1 is None:
            return False
        lon_ok = self.lon_min <= lon <= self.lon_max or self.lon_min <= lon % 360 <= self.lon_max
        # Accept partial time overlap (fallback fills the rest), but require the start.
        return lon_ok and self.lat_min <= lat <= self.lat_max and self.t0 <= start <= self.t1

    def to_json(self):
        return {
            "path": self.path,
            "kinds": sorted(self.kinds),
            "mapping": self.mapping,
            "t0": self.t0.isoformat() if self.t0 else None,
            "t1": self.t1.isoformat() if self.t1 else None,
            "bbox": [self.lon_min, self.lat_min, self.lon_max, self.lat_max],
        }


_cache: dict[str, tuple[float, ForcingFile | None]] = {}
_lock = threading.Lock()


def _to_naive_utc(t) -> datetime:
    ts = np.datetime64(t, "s").astype("datetime64[s]").astype(datetime)
    return ts.replace(tzinfo=None) if isinstance(ts, datetime) else ts


def _inspect(path: Path) -> ForcingFile | None:
    import xarray as xr

    try:
        with xr.open_dataset(path, decode_times=True) as ds:
            names = {ds[v].attrs.get("standard_name") for v in ds.data_vars}
            mapping = {v: NAME_TO_STANDARD[v] for v in ds.data_vars
                       if v in NAME_TO_STANDARD and not ds[v].attrs.get("standard_name")}
            names |= set(mapping.values())
            kinds = set()
            if names & CURRENT_NAMES:
                kinds.add("current")
            if names & WIND_NAMES:
                kinds.add("wind")
            if names & STOKES_NAMES:
                kinds.add("stokes")
            if not kinds:
                return None
            ff = ForcingFile(path=str(path), kinds=kinds, mapping=mapping)
            tname = next((c for c in ds.coords if c.lower() in ("time", "valid_time", "t")), None)
            if tname is None:
                tname = next((c for c in ds.coords if np.issubdtype(ds[c].dtype, np.datetime64)), None)
            if tname is not None and np.issubdtype(ds[tname].dtype, np.datetime64):
                tv = ds[tname].values
                ff.t0, ff.t1 = _to_naive_utc(tv.min()), _to_naive_utc(tv.max())
            for cand in ("lon", "longitude", "x", "nav_lon"):
                if cand in ds.variables:
                    v = ds[cand].values
                    ff.lon_min, ff.lon_max = float(np.nanmin(v)), float(np.nanmax(v))
                    break
            for cand in ("lat", "latitude", "y", "nav_lat"):
                if cand in ds.variables:
                    v = ds[cand].values
                    ff.lat_min, ff.lat_max = float(np.nanmin(v)), float(np.nanmax(v))
                    break
            return ff
    except Exception as e:  # unreadable / not CF: ignore
        log.debug("skip %s: %s", path, e)
        return None


def scan() -> list[ForcingFile]:
    """All usable forcing files under <data dir>/cache, cached by mtime."""
    out = []
    for d in config.DATA_DIRS:
        root = d / "cache"
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.nc")):
            key = str(p)
            mtime = p.stat().st_mtime
            with _lock:
                hit = _cache.get(key)
            if hit is None or hit[0] != mtime:
                ff = _inspect(p)
                with _lock:
                    _cache[key] = (mtime, ff)
            else:
                ff = hit[1]
            if ff is not None:
                out.append(ff)
    return out


def select(lon: float, lat: float, start: datetime, end: datetime) -> list[ForcingFile]:
    if config.FORCING_MODE == "constant":
        return []
    files = [f for f in scan() if f.covers(lon, lat, start, end)]
    # Prefer files with longer coverage of the run window.
    files.sort(key=lambda f: -(min(f.t1, end) - start).total_seconds())
    return files


def default_start_time() -> datetime:
    """Now (UTC, floored to the hour) if live forcing covers it, else the start
    of the most recent cached current window, else now."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    if config.FORCING_MODE == "constant":
        return now
    currents = [f for f in scan() if "current" in f.kinds and f.t0]
    if not currents or any(f.t0 <= now <= f.t1 for f in currents):
        return now
    latest = max(currents, key=lambda f: f.t1)
    t0 = latest.t0.replace(minute=0, second=0, microsecond=0)
    return t0 if t0 >= latest.t0 else t0 + timedelta(hours=1)


def open_normalized(f: dict):
    """Open a forcing file as an xarray Dataset that OpenDrift's generic reader
    understands: datetime axis named 'time' with standard_name, and CF
    standard_names added for known variable names (ERA5 u10/v10, HYCOM, CMEMS)."""
    import xarray as xr

    ds = xr.open_dataset(f["path"])
    tname = next((c for c in ds.coords if np.issubdtype(ds[c].dtype, np.datetime64)
                  and ds[c].ndim == 1 and ds[c].size > 1), None)
    if tname and tname != "time" and "time" not in ds.variables:
        ds = ds.rename({tname: "time"})
        tname = "time"
    if tname:
        ds[tname].attrs.setdefault("standard_name", "time")
    for v, sn in (f.get("mapping") or {}).items():
        if v in ds.data_vars:
            ds[v].attrs["standard_name"] = sn
    return ds
