"""Fast numpy sampling of the cached forcing NetCDF (regular lon/lat/time grids).

Used both by the particle model (sim.py) and by the ML training script (train.py),
so the drift correction is trained on exactly the fields the model later sees.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import xarray as xr

U_NAMES = ("x_sea_water_velocity", "eastward_sea_water_velocity", "water_u", "uo", "x_wind", "eastward_wind", "u10")


def _pick(ds: xr.Dataset, kind: str) -> tuple[str, str]:
    """(u, v) variable names for kind 'current' | 'wind'."""
    std = {ds[v].attrs.get("standard_name", ""): v for v in ds.data_vars}
    if kind == "current":
        pairs = [("x_sea_water_velocity", "y_sea_water_velocity"),
                 ("eastward_sea_water_velocity", "northward_sea_water_velocity"),
                 ("surface_eastward_sea_water_velocity", "surface_northward_sea_water_velocity")]
        names = [("water_u", "water_v"), ("uo", "vo")]
    else:
        pairs = [("x_wind", "y_wind"), ("eastward_wind", "northward_wind")]
        names = [("u10", "v10")]
    for a, b in pairs:
        if a in std and b in std:
            return std[a], std[b]
    for a, b in names + pairs:
        if a in ds.data_vars and b in ds.data_vars:
            return a, b
    raise KeyError(f"no {kind} variables in dataset")


class Grid:
    """u, v on (time, lat, lon); trilinear interpolation; NaN = land / outside."""

    def __init__(self, path: str, kind: str, bbox: tuple | None = None,
                 t0: datetime | None = None, t1: datetime | None = None, pad_deg: float = 0.0):
        with xr.open_dataset(path) as ds:
            un, vn = _pick(ds, kind)
            u, v = ds[un], ds[vn]
            tname = next(d for d in u.dims if np.issubdtype(ds[d].dtype, np.datetime64))
            latn = next(d for d in u.dims if d in ("lat", "latitude", "y"))
            lonn = next(d for d in u.dims if d in ("lon", "longitude", "x"))
            for d in list(u.dims):
                if d not in (tname, latn, lonn):  # depth etc.: surface level
                    u, v = u.isel({d: 0}), v.isel({d: 0})
            lon = ds[lonn].values.astype(float)
            if lon.max() > 180:  # 0..360 -> -180..180, keep ascending
                lon = np.where(lon > 180, lon - 360, lon)
                order = np.argsort(lon)
                u, v, lon = u.isel({lonn: order}), v.isel({lonn: order}), lon[order]
            lat = ds[latn].values.astype(float)
            if lat[0] > lat[-1]:
                u, v, lat = u.isel({latn: slice(None, None, -1)}), v.isel({latn: slice(None, None, -1)}), lat[::-1]
            tt = ds[tname].values.astype("datetime64[s]").astype(np.int64)
            ti = np.arange(len(tt))
            if t0 is not None:
                a = np.datetime64(t0, "s").astype(np.int64)
                ti = ti[tt >= (tt[tt <= a].max() if (tt <= a).any() else tt[0])]
            if t1 is not None:
                b = np.datetime64(t1, "s").astype(np.int64)
                ti = ti[tt[ti] <= (tt[tt >= b].min() if (tt >= b).any() else tt[-1])]
            xi, yi = np.arange(len(lon)), np.arange(len(lat))
            if bbox is not None:
                x0, y0, x1, y1 = bbox
                xi = xi[(lon >= x0 - pad_deg - 0.1) & (lon <= x1 + pad_deg + 0.1)]
                yi = yi[(lat >= y0 - pad_deg - 0.1) & (lat <= y1 + pad_deg + 0.1)]
            sel = {tname: ti, latn: yi, lonn: xi}
            self.u = u.isel(sel).transpose(tname, latn, lonn).values.astype(np.float32)
            self.v = v.isel(sel).transpose(tname, latn, lonn).values.astype(np.float32)
        self.t = tt[ti].astype(np.float64)
        self.lon, self.lat = lon[xi], lat[yi]
        self.dlon = float(self.lon[1] - self.lon[0])
        self.dlat = float(self.lat[1] - self.lat[0])
        self.bbox = (float(self.lon[0]), float(self.lat[0]), float(self.lon[-1]), float(self.lat[-1]))
        self.t_end = float(self.t[-1])

    def inside(self, lon, lat) -> np.ndarray:
        x0, y0, x1, y1 = self.bbox
        return (lon >= x0) & (lon <= x1) & (lat >= y0) & (lat <= y1)

    def sample(self, t: float | np.ndarray, lon: np.ndarray, lat: np.ndarray):
        """Trilinear (time, lat, lon) interpolation. Any NaN corner -> NaN (coast/land)."""
        lon = np.asarray(lon, float)
        lat = np.asarray(lat, float)
        t = np.broadcast_to(np.asarray(t, float), lon.shape)
        fx = (lon - self.lon[0]) / self.dlon
        fy = (lat - self.lat[0]) / self.dlat
        nt = len(self.t)
        if nt == 1:
            it = np.zeros(lon.shape, int)
            ft = np.zeros(lon.shape)
        else:
            it = np.clip(np.searchsorted(self.t, t, side="right") - 1, 0, nt - 2)
            ft = np.clip((t - self.t[it]) / (self.t[it + 1] - self.t[it]), 0.0, 1.0)
        out = ~((fx >= 0) & (fx <= len(self.lon) - 1) & (fy >= 0) & (fy <= len(self.lat) - 1))
        ix = np.clip(np.floor(fx).astype(int), 0, len(self.lon) - 2)
        iy = np.clip(np.floor(fy).astype(int), 0, len(self.lat) - 2)
        ax, ay = fx - ix, fy - iy
        res = []
        for F in (self.u, self.v):
            acc = 0.0
            for dt_, wt in ((0, 1 - ft), (1, ft)):
                k = np.minimum(it + dt_, nt - 1)
                c = (F[k, iy, ix] * (1 - ax) * (1 - ay) + F[k, iy, ix + 1] * ax * (1 - ay)
                     + F[k, iy + 1, ix] * (1 - ax) * ay + F[k, iy + 1, ix + 1] * ax * ay)
                acc = acc + c * wt
            acc = np.where(out, np.nan, acc)
            res.append(acc)
        return res[0], res[1]

    def gradients(self, t, lon, lat):
        """Current vorticity and divergence (1/s) by centred differences over one grid cell."""
        dx = self.dlon
        dy = self.dlat
        ue, ve = self.sample(t, lon + dx, lat)
        uw, vw = self.sample(t, lon - dx, lat)
        un, vn = self.sample(t, lon, lat + dy)
        us, vs = self.sample(t, lon, lat - dy)
        mx = 2 * dx * 111320.0 * np.cos(np.radians(lat))
        my = 2 * dy * 110574.0
        vort = (ve - vw) / mx - (un - us) / my
        div = (ue - uw) / mx + (vn - vs) / my
        return vort, div
