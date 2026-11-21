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
        # Require the start; the run is clipped to the end of the forcing (jobs.submit).
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
    files = [f for f in scan() if f.covers(lon, lat, start, end)]
    # Prefer files with longer coverage of the run window.
    files.sort(key=lambda f: -(min(f.t1, end) - start).total_seconds())
    return files


def default_start_time() -> datetime:
    """Now (UTC, floored to the hour) if live forcing covers it, else the start
    of the most recent cached current window, else now."""
    now = datetime.now(timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
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


class SeedOnLand(ValueError):
    """The requested spill point is land (or too far from model water)."""


def _is_land_gshhg(lon: float, lat: float) -> bool:
    try:
        from roaring_landmask import RoaringLandmask
        global _landmask
        if _landmask is None:
            _landmask = RoaringLandmask.new()
        return bool(_landmask.contains(float(lon), float(lat)))
    except Exception:  # landmask unavailable: trust the forcing grid only
        return False


_landmask = None


def snap_to_water(f: ForcingFile, lon: float, lat: float, t: datetime, max_km: float):
    """Nearest point to (lon, lat) that has a finite current in forcing file `f` at time
    `t` and is water in OpenDrift's GSHHG landmask. Returns (lon, lat, moved_km).
    Raises SeedOnLand if nothing within max_km."""
    import xarray as xr

    with xr.open_dataset(f.path) as ds:
        uvar = next(v for v in ds.data_vars
                    if ds[v].attrs.get("standard_name") in CURRENT_NAMES or v in ("water_u", "uo", "u"))
        u = ds[uvar]
        tname = next(c for c in u.dims if np.issubdtype(ds[c].dtype, np.datetime64))
        u = u.sel({tname: np.datetime64(t)}, method="nearest")
        for dim in list(u.dims):
            if dim not in ("lat", "lon", "latitude", "longitude", "y", "x"):
                u = u.isel({dim: 0})
        lonn = next(c for c in ("lon", "longitude", "x") if c in u.coords)
        latn = next(c for c in ("lat", "latitude", "y") if c in u.coords)
        lons, lats = u[lonn].values.astype(float), u[latn].values.astype(float)
        lon_q = lon % 360 if lons.max() > 180 else lon
        # Only look at a window around the point.
        dlat = max_km / 111.0 + 0.1
        dlon = dlat / max(0.2, np.cos(np.radians(lat)))
        ii = np.nonzero(np.abs(lons - lon_q) <= dlon)[0]
        jj = np.nonzero(np.abs(lats - lat) <= dlat)[0]
        if len(ii) == 0 or len(jj) == 0:
            raise SeedOnLand("That point is outside the cached ocean forcing.")
        sub = u.isel({lonn: ii, latn: jj}).transpose(latn, lonn).values
    LO, LA = np.meshgrid(lons[ii], lats[jj])
    LO = np.where(LO > 180, LO - 360, LO)
    km = np.hypot((LO - lon) * 111.32 * np.cos(np.radians(lat)), (LA - lat) * 110.57)
    km = np.where(np.isfinite(sub), km, np.inf)
    # Half a grid diagonal: the requested point sits in this water cell already.
    cell_km = 0.75 * np.hypot(np.median(np.diff(lons)) * 111.32 * np.cos(np.radians(lat)),
                              np.median(np.diff(lats)) * 110.57)
    order = np.argsort(km, axis=None)
    for k in order[:400]:
        d = float(km.flat[k])
        if not np.isfinite(d) or d > max_km:
            break
        x, y = float(LO.flat[k]), float(LA.flat[k])
        if d <= cell_km and not _is_land_gshhg(lon, lat):
            return lon, lat, 0.0  # requested point is already water
        if not _is_land_gshhg(x, y):
            return round(x, 4), round(y, 4), round(d, 1)
    if _is_land_gshhg(lon, lat):
        raise SeedOnLand("That spot is on land. Click on open water.")
    raise SeedOnLand(
        f"That spot is in a bay or inshore water the ocean model treats as land (no model water within "
        f"{max_km:g} km). Click a bit further offshore.")


# --- live forecast (prediction mode) ------------------------------------------------
# data/fetch_live.py writes cache/forcing/live/{currents,winds}.nc + live.json (model, run, valid window).
# Prediction runs use these two files only, never reanalysis/hindcast windows.

def _live_dirs() -> list[Path]:
    return [d / "cache" / "forcing" / "live" for d in config.DATA_DIRS]


def _iso(t: datetime | None) -> str | None:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def live_forecast(now: datetime | None = None) -> dict | None:
    """The cached live forecast: forcing files + metadata. None if nothing is cached."""
    import json

    for d in _live_dirs():
        cp, wp = d / "currents.nc", d / "winds.nc"
        if not (cp.exists() and wp.exists()):
            continue
        by_path = {f.path: f for f in scan()}
        cur, wind = by_path.get(str(cp)), by_path.get(str(wp))
        if cur is None or wind is None or cur.t1 is None or wind.t1 is None:
            continue
        try:
            man = json.loads((d / "live.json").read_text())
        except Exception:  # older cache without a manifest
            man = {}
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        start = now.replace(minute=0, second=0, microsecond=0)
        valid_from, valid_until = max(cur.t0, wind.t0), min(cur.t1, wind.t1)
        hours = max(0.0, (valid_until - start).total_seconds() / 3600)
        mc, mw = man.get("currents") or {}, man.get("winds") or {}
        return {
            "currents": {"model": mc.get("model", "ocean forecast"), "issued_at": mc.get("run"),
                         "valid_from": _iso(cur.t0), "valid_until": _iso(cur.t1), "source": mc.get("source")},
            "winds": {"model": mw.get("model", "NCEP GFS 0.25°"), "issued_at": mw.get("run"),
                      "valid_from": _iso(wind.t0), "valid_until": _iso(wind.t1), "source": mw.get("source")},
            "valid_from": _iso(valid_from),
            "valid_until": _iso(valid_until),
            "updated_at": man.get("updated_at"),
            "now": _iso(now),
            "start": _iso(start),
            "hours_available": round(hours, 1),
            "stale": hours < config.MIN_RUN_H or start < valid_from,
            "bbox": [max(cur.lon_min, wind.lon_min), max(cur.lat_min, wind.lat_min),
                     min(cur.lon_max, wind.lon_max), min(cur.lat_max, wind.lat_max)],
            "_files": (cur, wind),
        }
    return None
