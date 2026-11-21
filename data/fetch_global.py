#!/usr/bin/env python
"""Global surface-current fields for the globe's flow layer (the Gulf HYCOM fields are drawn on top).

  python data/fetch_global.py live                    # NOAA RTOFS global, latest 00z run, 0-72 h every 3 h
  python data/fetch_global.py live --every-h 6
  python data/fetch_global.py --catalog               # HYCOM GOFS 3.1 global for every catalog spill window
  python data/fetch_global.py --only texas_city_y,mc209_2017
  python data/fetch_global.py --window my_case --start 2012-06-01 --end 2012-06-04 --every-h 3

Sources (no accounts):
  live        RTOFS global 1/12 deg (HYCOM, tripolar), NOMADS
              https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/rtofs.YYYYMMDD/rtofs_glo_2ds_fNNN_prog.nc
              (~148 MB per hourly file; only u/v are kept, raw files are deleted after binning unless --keep-raw)
  historical  HYCOM GOFS 3.1 global 1/12 deg via NCSS (surface water_u/water_v, horizStride=3, ~2.5 MB per step):
              GLBv0.08/expt_53.X reanalysis 1994-2015, GLBv0.08/expt_92.8|57.7|92.9|93.0 analysis 2017-2020,
              GLBy0.08/expt_93.0 2018-12..2024-09, ESPC-D-V02 (FMRC best) 2024-08..now.
              Before 1994 there is no HYCOM global; those spills get no global layer.

Output data/cache/fields/global/<window>/ (same encoding as data/cache/fields/<window>/, see export_fields.py):
  index.json   {window, layer:"global", bbox, lon0, lat0, dlon, dlat, nx, ny, times, vmax, units, png_encoding, source, grid:"cell-centre"}
  <t>.png      RGBA nx x ny, rows NORTH->south. R=(u/vmax+1)*127.5, G=(v/vmax+1)*127.5, B=speed/vmax*255, A=255 water/0 land.
  <t>.json     per-step meta {t, lon0, lat0, dlon, dlat, nx, ny, vmax, png, source}. No inline u/v (a 0.25 deg
               global grid is ~2M numbers); the backend decodes the PNG for GET /api/currents/{t}?layer=global.
Grid: regular cell-centred lon -180..180, lat -90..90 (lon0 = -180 + step/2). Values are bin means of the native
1/12 deg points falling in each cell (u/v rotated to east/north on the RTOFS tripolar Arctic).
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np
import requests
import xarray as xr
from PIL import Image

from common import FIELDS, RAW, SPILLS, UA, download, iso, log, parse_time

OUT = FIELDS / "global"
NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/"
NCSS = "https://ncss.hycom.org/thredds/ncss/"
# (dataset path, first time, last time) in priority order
HYCOM = [
    ("GLBv0.08/expt_53.X/data/{year}", "1994-01-01", "2015-12-31T21:00"),
    ("GLBv0.08/expt_92.8", "2017-02-01T12:00", "2017-06-01T09:00"),
    ("GLBv0.08/expt_57.7", "2017-06-01T12:00", "2017-10-01T09:00"),
    ("GLBv0.08/expt_92.9", "2017-10-01T12:00", "2018-03-20T09:00"),
    ("GLBv0.08/expt_93.0", "2018-01-01T12:00", "2020-02-18T09:00"),
    ("GLBy0.08/expt_93.0", "2018-12-04T12:00", "2024-09-05T09:00"),
    ("FMRC_ESPC-D-V02_uv3z/FMRC_ESPC-D-V02_uv3z_best.ncd", "2024-09-05T12:00", "2100-01-01"),  # ~last 10 days only
]


class Binner:
    """Bin-average scattered native points (2-D lon/lat) onto a regular global cell-centred grid."""

    def __init__(self, lon, lat, step):
        lon = ((np.asarray(lon, np.float64) + 180) % 360) - 180
        lat = np.asarray(lat, np.float64)
        self.nx, self.ny = int(round(360 / step)), int(round(180 / step))
        ix = np.floor((lon + 180) / step).astype(np.int64) % self.nx
        iy = np.floor((lat + 90) / step).astype(np.int64)
        ok = (iy >= 0) & (iy < self.ny)
        self.flat = np.where(ok, iy * self.nx + ix, -1).ravel()
        self.step = step

    def __call__(self, u, v):
        u, v = np.asarray(u).ravel(), np.asarray(v).ravel()
        good = (self.flat >= 0) & np.isfinite(u) & np.isfinite(v) & (np.abs(u) < 50) & (np.abs(v) < 50)
        idx = self.flat[good]
        n = self.nx * self.ny
        cnt = np.bincount(idx, minlength=n).astype(np.float64)
        su = np.bincount(idx, weights=u[good], minlength=n)
        sv = np.bincount(idx, weights=v[good], minlength=n)
        with np.errstate(invalid="ignore", divide="ignore"):
            U, V = su / cnt, sv / cnt
        U[cnt == 0] = np.nan
        V[cnt == 0] = np.nan
        return U.reshape(self.ny, self.nx), V.reshape(self.ny, self.nx)


def write_step(out, t, U, V, step, vmax, source):
    mask = np.isfinite(U) & np.isfinite(V)
    uu, vv = np.nan_to_num(U), np.nan_to_num(V)
    rgba = np.zeros(U.shape + (4,), np.uint8)
    rgba[..., 0] = np.clip(np.round((uu / vmax + 1) * 127.5), 0, 255)
    rgba[..., 1] = np.clip(np.round((vv / vmax + 1) * 127.5), 0, 255)
    rgba[..., 2] = np.clip(np.hypot(uu, vv) / vmax * 255, 0, 255)
    rgba[..., 3] = np.where(mask, 255, 0)
    Image.fromarray(rgba[::-1]).save(out / f"{t}.png", optimize=True)
    ny, nx = U.shape
    meta = {"t": t, "lon0": -180 + step / 2, "lat0": -90 + step / 2, "dlon": step, "dlat": step, "nx": nx, "ny": ny,
            "vmax": vmax, "units": "m/s", "png": f"{t}.png", "source": source}
    (out / f"{t}.json").write_text(json.dumps(meta, separators=(",", ":")))


def write_index(out, window, times, step, vmax, source, extra=None):
    nx, ny = int(round(360 / step)), int(round(180 / step))
    idx = {"window": window, "layer": "global", "bbox": [-180.0, -90.0, 180.0, 90.0],
           "lon0": -180 + step / 2, "lat0": -90 + step / 2, "dlon": step, "dlat": step, "nx": nx, "ny": ny,
           "grid": "cell-centre", "times": sorted(times), "vmax": vmax, "units": "m/s",
           "json": "<t>.json", "png": "<t>.png", "source": source,
           "png_encoding": "R=(u/vmax+1)*127.5, G=(v/vmax+1)*127.5, B=speed/vmax*255, A=255 water/0 land; rows north->south"}
    idx.update(extra or {})
    (out / "index.json").write_text(json.dumps(idx, indent=1))
    keep = {f"{t}.png" for t in times} | {f"{t}.json" for t in times} | {"index.json"}
    for p in out.iterdir():
        if p.name not in keep:
            p.unlink()
    size = sum(p.stat().st_size for p in out.iterdir())
    log(f"[global/{window}] {len(times)} fields {nx}x{ny} -> {out} ({size / 1e6:.1f} MB)")


def unix(t) -> int:
    return int(np.datetime64(t, "s").astype(np.int64))


# ---------------------------------------------------------------- RTOFS live

def latest_rtofs_run(max_h: int) -> tuple[str, str]:
    html = requests.get(NOMADS, headers=UA, timeout=60).text
    days = sorted(set(re.findall(r"rtofs\.(\d{8})/", html)), reverse=True)
    for d in days:
        url = f"{NOMADS}rtofs.{d}/"
        listing = requests.get(url, headers=UA, timeout=120).text
        if f"rtofs_glo_2ds_f{max_h:03d}_prog.nc" in listing:
            return d, url
        log(f"run {d} not complete to f{max_h:03d} yet, trying older")
    raise RuntimeError("no complete RTOFS run on NOMADS")


def rtofs_angle(lon, lat):
    """Angle of the grid x-axis from east (radians), for rotating tripolar u/v to east/north."""
    dlon = ((np.roll(lon, -1, axis=1) - np.roll(lon, 1, axis=1) + 180) % 360) - 180
    dlat = np.roll(lat, -1, axis=1) - np.roll(lat, 1, axis=1)
    return np.arctan2(dlat, dlon * np.cos(np.deg2rad(lat)))


def fetch_live(step, every_h, max_h, vmax, keep_raw, force, workers=3):
    day, url = latest_rtofs_run(max_h)
    out = OUT / "live"
    idx_path = out / "index.json"
    if idx_path.exists() and not force and json.loads(idx_path.read_text()).get("run") == day:
        log(f"[global/live] RTOFS run {day} already exported (use --force)")
        return
    out.mkdir(parents=True, exist_ok=True)
    raw = RAW / "global_live" / day
    hours = list(range(0, max_h + 1, every_h))
    state = {}

    def get(h):
        return download(f"{url}rtofs_glo_2ds_f{h:03d}_prog.nc", raw / f"f{h:03d}_prog.nc", timeout=900, min_bytes=10_000_000)

    times = []
    with ThreadPoolExecutor(workers) as ex:
        for h, path in zip(hours, ex.map(get, hours)):
            with xr.open_dataset(path) as ds:
                if "binner" not in state:
                    lon, lat = ds.Longitude.values, ds.Latitude.values
                    state["binner"] = Binner(lon, lat, step)
                    ang = rtofs_angle(lon, lat)
                    state["cos"], state["sin"] = np.cos(ang), np.sin(ang)
                u = ds.u_velocity.values[0, 0].astype(np.float64)
                v = ds.v_velocity.values[0, 0].astype(np.float64)
                t = unix(ds.MT.values[0])
            c, s = state["cos"], state["sin"]
            ue, vn = u * c - v * s, u * s + v * c
            U, V = state["binner"](ue, vn)
            write_step(out, t, U, V, step, vmax, f"NOAA RTOFS global {day} t00z f{h:03d}")
            times.append(t)
            log(f"  f{h:03d} {datetime.utcfromtimestamp(t):%Y-%m-%d %H:%M} max {np.nanmax(np.hypot(U, V)):.2f} m/s")
            if not keep_raw:
                path.unlink()
    write_index(out, "live", times, step, vmax, f"NOAA RTOFS global (HYCOM 1/12 deg), run {day} 00z, {NOMADS}",
                {"run": day, "every_h": every_h})


# ---------------------------------------------------------------- HYCOM historical

def hycom_dataset(t: datetime) -> str | None:
    for path, a, b in HYCOM:
        if parse_time(a) <= t <= parse_time(b):
            return path.format(year=t.year)
    return None


def fetch_hycom(window, start, end, step, every_h, vmax, force, workers=4):
    out = OUT / window
    idx_path = out / "index.json"
    if idx_path.exists() and not force:
        log(f"[global/{window}] already exported (use --force)")
        return
    t = start.replace(minute=0, second=0, microsecond=0)
    t -= timedelta(hours=t.hour % every_h)
    want = []
    while t <= end:
        want.append(t)
        t += timedelta(hours=every_h)
    # group consecutive times per dataset into day-sized chunks (one NCSS request each)
    chunks: dict[tuple, list] = {}
    for t in want:
        ds = hycom_dataset(t)
        if ds is None:
            continue
        chunks.setdefault((ds, t.date()), []).append(t)  # one NCSS request per day
    if not chunks:
        log(f"[global/{window}] no HYCOM global data for {iso(start)}..{iso(end)}; skipped")
        return
    out.mkdir(parents=True, exist_ok=True)
    raw = RAW / "global" / window
    stride = 3 if step >= 0.2 else 1

    def req(ds, ts, dest):
        params = {"var": ["water_u", "water_v"], "horizStride": stride, "vertCoord": 0, "accept": "netcdf4"}
        if len(ts) == 1:
            params["time"] = iso(ts[0])
        else:
            params.update(time_start=iso(ts[0]), time_end=iso(ts[-1]), timeStride=1)
        return download(NCSS + ds, dest, params=params, timeout=600, min_bytes=100_000, tries=2 if len(ts) > 1 else 4)

    def get(key):
        """Day chunk -> list of NetCDF paths. Some aggregations (e.g. expt_92.9) reject time ranges with
        "response too large" (non-monotonic time axis) but answer single times, so fall back to per-time requests."""
        ds, day = key
        ts = chunks[key]
        try:
            return [req(ds, ts, raw / f"{day:%Y%m%d}_{every_h}h_s{stride}.nc")]
        except RuntimeError as e:
            if len(ts) == 1:
                raise
            log(f"  [{window}] {day}: range request failed ({str(e)[-80:]}); fetching {len(ts)} single times")
            return [req(ds, [t], raw / f"{t:%Y%m%d%H}_{every_h}h_s{stride}.nc") for t in ts]

    times, binner, srcs = [], None, set()
    keys = sorted(chunks)
    with ThreadPoolExecutor(workers) as ex:
        for key, paths in zip(keys, ex.map(get, keys)):
            wanted = {unix(np.datetime64(t)) for t in chunks[key]}
            for path in paths:
                with xr.open_dataset(path) as ds:
                    ds = ds.isel(depth=0) if "depth" in ds.dims else ds
                    if "time" not in ds.dims:
                        ds = ds.expand_dims("time")
                    if binner is None or binner.shape != (ds.lat.size, ds.lon.size):
                        LON, LAT = np.meshgrid(ds.lon.values, ds.lat.values)
                        binner = Binner(LON, LAT, step)
                        binner.shape = (ds.lat.size, ds.lon.size)
                    for k, tv in enumerate(np.atleast_1d(ds.time.values)):
                        tu = unix(tv)
                        if tu not in wanted or tu in times:
                            continue
                        u = ds.water_u.isel(time=k).values.astype(np.float64)
                        v = ds.water_v.isel(time=k).values.astype(np.float64)
                        U, V = binner(u, v)
                        write_step(out, tu, U, V, step, vmax, f"HYCOM {key[0]}")
                        times.append(tu)
            srcs.add(key[0])
            log(f"  [{window}] {key[1]} ok ({len(times)} steps)")
    if not times:
        log(f"[global/{window}] nothing decoded")
        return
    write_index(out, window, times, step, vmax, "HYCOM GOFS 3.1 global 1/12 deg via NCSS: " + ", ".join(sorted(srcs)),
                {"every_h": every_h})


def every_for(hours: float) -> int:
    return 3 if hours <= 10 * 24 else 6 if hours <= 30 * 24 else 12


def catalog_windows(only=None):
    cat = json.loads((SPILLS / "catalog.json").read_text())
    for s in cat:
        if only and s["id"] not in only:
            continue
        fw = s.get("forcing_window") or {}
        start = parse_time(fw["start"]) if fw.get("start") else parse_time(s["date_start"]) - timedelta(days=1)
        end = parse_time(fw["end"]) if fw.get("end") else parse_time(s.get("date_end") or s["date_start"]) + timedelta(days=1)
        # match the Gulf hindcast window length used by fetch_hycom.py (catalog windows can be months long)
        end = min(end, start + timedelta(days=100))
        yield s["id"], start, end


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("windows", nargs="*", help="'live' and/or catalog spill ids")
    ap.add_argument("--catalog", action="store_true", help="every catalog spill")
    ap.add_argument("--only", default="", help="comma-separated catalog ids")
    ap.add_argument("--window", help="custom historical window name (with --start/--end)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--step", type=float, default=0, help="output grid spacing, degrees (default: live 0.25, hindcast 0.5)")
    ap.add_argument("--every-h", type=int, default=0, help="hours between fields (default: live 3, hindcast 3/6/12 by length)")
    ap.add_argument("--max-h", type=int, default=72, help="live forecast horizon")
    ap.add_argument("--vmax", type=float, default=2.5)
    ap.add_argument("--keep-raw", action="store_true", help="keep the 148 MB RTOFS files in data/raw/")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ids = [w for w in a.windows if w != "live"] + [x for x in a.only.split(",") if x]
    did = False
    if "live" in a.windows:
        fetch_live(a.step or 0.25, a.every_h or 3, a.max_h, a.vmax, a.keep_raw, a.force)
        did = True
    if a.window:
        s, e = parse_time(a.start), parse_time(a.end)
        fetch_hycom(a.window, s, e, a.step or 0.5, a.every_h or every_for((e - s).total_seconds() / 3600), a.vmax, a.force)
        did = True
    if a.catalog or ids:
        for wid, s, e in catalog_windows(set(ids) if ids else None):
            try:
                fetch_hycom(wid, s, e, a.step or 0.5, a.every_h or every_for((e - s).total_seconds() / 3600), a.vmax, a.force)
            except Exception as ex:  # noqa: BLE001 - one bad window shouldn't stop the rest
                log(f"[global/{wid}] FAILED: {ex}")
        did = True
    if not did:
        ap.error("give 'live', spill ids, --catalog or --window")


if __name__ == "__main__":
    main()
