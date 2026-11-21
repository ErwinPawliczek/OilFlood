#!/usr/bin/env python
"""Fetch the latest LIVE forecast window for prediction mode (0-72 h from now).

Currents (default --currents auto: RTOFS, ESPC if RTOFS fails):
  rtofs  NOAA RTOFS global 1/12 deg (HYCOM), newest complete daily 00z run on NOMADS, 3-hourly,
         https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/rtofs.YYYYMMDD/rtofs_glo_2ds_fNNN_prog.nc
         (148 MB global surface files, fetched in ~4 s each; only the Gulf box is kept. Raw files go to
         data/raw/global_live/<run>/fNNN_prog.nc, the same place fetch_global.py looks, so the globe layer
         can reuse them with --keep-raw. They are deleted afterwards otherwise.)
  espc   HYCOM ESPC-D-V02 1/12 deg FMRC "best" via NCSS (Gulf subset only). Note: in Nov 2026 the newest
         ESPC run on tds.hycom.org was 5 days old and ended ~8 h short of now+72 h, hence RTOFS first.
Winds: NCEP GFS 0.25 deg 10 m u/v (UCAR THREDDS "Best", 3-hourly),
       https://thredds.ucar.edu/thredds/ncss/grid/grib/NCEP/GFS/Global_0p25deg/Best

Window: (now - back_h) floored to 3 h .. now + hours (default 72), clipped to what the servers have.

  python data/fetch_live.py                     # -> data/cache/forcing/live/{currents,winds}.nc + live.json
  python data/fetch_live.py --force             # re-download even if nothing new
  python data/fetch_live.py --currents espc

Idempotent: currents are skipped when the cached file is from the newest model run and already reaches
now + hours; winds are skipped when GFS has no newer run and the cached file reaches now + hours.
live.json records what is cached: model, run (issued-at), first/last valid time, source URL.
The backend's prediction mode reads only these files (see backend/app/forcing.py live_forecast()).
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timedelta

import numpy as np
import requests
import xarray as xr

from common import BOX, FORCING, RAW, UA, download, iso, log, now_utc, to_opendrift, verify, write_nc

ESPC = "https://tds.hycom.org/thredds/ncss/FMRC_ESPC-D-V02_uv3z/FMRC_ESPC-D-V02_uv3z_best.ncd"
GFS = "https://thredds.ucar.edu/thredds/ncss/grid/grib/NCEP/GFS/Global_0p25deg/Best"
NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/"
RTOFS_MAX_H = 192
OUT = FORCING / "live"
MANIFEST = OUT / "live.json"


def time_span(base: str) -> tuple[datetime, datetime]:
    xml = requests.get(base + "/dataset.xml", headers=UA, timeout=120).text
    b = re.search(r"<begin>([^<]+)</begin>", xml)[1]
    e = re.search(r"<end>([^<]+)</end>", xml)[1]
    return datetime.fromisoformat(b.replace("Z", "")), datetime.fromisoformat(e.replace("Z", ""))


def box_params(box=BOX):
    return [("north", box[3]), ("south", box[1]), ("west", box[0]), ("east", box[2]), ("accept", "netcdf4")]


def cached_span(path) -> tuple[datetime, datetime] | None:
    if not path.exists():
        return None
    with xr.open_dataset(path) as ds:
        t = ds.time.values
        return (datetime.fromisoformat(str(t[0])[:19]), datetime.fromisoformat(str(t[-1])[:19]))


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text())
    except Exception:  # noqa: BLE001
        return {}


def max_run_time(ds, name) -> str | None:
    if name in ds.variables:
        v = ds[name].values
        if np.issubdtype(v.dtype, np.datetime64) and v.size:
            return iso(datetime.fromisoformat(str(v.max())[:19]))
    return None


# ------------------------------------------------------------------ RTOFS

def rtofs_runs() -> list[str]:
    html = requests.get(NOMADS, headers=UA, timeout=120).text
    return sorted(set(re.findall(r"rtofs\.(\d{8})/", html)), reverse=True)


def rtofs_pick(want_end: datetime) -> tuple[str, int]:
    """Newest 00z run whose listing has the forecast hour reaching want_end. Returns (YYYYMMDD, last hour)."""
    for day in rtofs_runs():
        run0 = datetime.strptime(day, "%Y%m%d")
        need = min(RTOFS_MAX_H, 3 * math.ceil((want_end - run0).total_seconds() / 3600 / 3))
        listing = requests.get(f"{NOMADS}rtofs.{day}/", headers=UA, timeout=120).text
        if f"rtofs_glo_2ds_f{need:03d}_prog.nc" in listing:
            return day, need
        log(f"  RTOFS run {day} has no f{need:03d} yet, trying the previous run")
    raise RuntimeError("no usable RTOFS run on NOMADS")


def fetch_rtofs(t0: datetime, want_end: datetime, keep_raw: bool) -> tuple[xr.Dataset, dict]:
    day, last_h = rtofs_pick(want_end)
    run0 = datetime.strptime(day, "%Y%m%d")
    first_h = max(0, 3 * math.floor((t0 - run0).total_seconds() / 3600 / 3))
    hours = list(range(first_h, last_h + 1, 3))
    raw = RAW / "global_live" / day
    log(f"RTOFS run {day} 00z f{first_h:03d}..f{last_h:03d} ({len(hours)} files)")
    sl = None
    steps = []
    for h in hours:
        p = download(f"{NOMADS}rtofs.{day}/rtofs_glo_2ds_f{h:03d}_prog.nc", raw / f"f{h:03d}_prog.nc",
                     timeout=900, min_bytes=10_000_000)
        with xr.open_dataset(p) as ds:
            if sl is None:
                lon = ((ds.Longitude.values + 180) % 360) - 180
                lat = ds.Latitude.values
                m = (lon >= BOX[0] - 0.1) & (lon <= BOX[2] + 0.1) & (lat >= BOX[1] - 0.1) & (lat <= BOX[3] + 0.1)
                jj, ii = np.nonzero(m)
                sl = dict(Y=slice(jj.min(), jj.max() + 1), X=slice(ii.min(), ii.max() + 1))
                sub_lon, sub_lat = lon[sl["Y"], sl["X"]], lat[sl["Y"], sl["X"]]
                # Below 47N the RTOFS grid is Mercator: lon depends only on X, lat only on Y.
                assert np.ptp(sub_lon, axis=0).max() < 1e-3 and np.ptp(sub_lat, axis=1).max() < 1e-3
                lon1, lat1 = sub_lon[0].astype("float64"), sub_lat[:, 0].astype("float64")
            sub = ds[["u_velocity", "v_velocity"]].isel(sl).isel(Layer=0).load()
            steps.append(xr.Dataset(
                {"water_u": (("time", "lat", "lon"), sub.u_velocity.values),
                 "water_v": (("time", "lat", "lon"), sub.v_velocity.values)},
                coords={"time": sub.MT.values, "lat": lat1, "lon": lon1}))
        if not keep_raw:
            p.unlink()
    ds = xr.concat(steps, "time")
    src = f"NOAA RTOFS global 1/12 deg (HYCOM), run {day} 00z, {NOMADS}rtofs.{day}/"
    info = {"model": "NOAA RTOFS global 1/12°", "run": iso(run0), "run_id": day, "source": src,
            "server_end": iso(run0 + timedelta(hours=RTOFS_MAX_H))}
    return to_opendrift(ds, {"water_u": "x_sea_water_velocity", "water_v": "y_sea_water_velocity"}, "current", src), info


# ------------------------------------------------------------------ ESPC (fallback)

def fetch_espc(t0: datetime, want_end: datetime, stamp: str) -> tuple[xr.Dataset, dict]:
    b, e = time_span(ESPC)
    s, t1 = max(t0, b), min(want_end, e)
    log(f"ESPC-D-V02 currents {s} .. {t1} (server has {b} .. {e})")
    p = [("var", "water_u"), ("var", "water_v"), ("vertCoord", "0"), ("time_start", iso(s)), ("time_end", iso(t1))]
    f = download(ESPC, RAW / "live" / f"espc_{stamp}_{t1:%Y%m%dT%H}.nc", params=p + box_params(), timeout=900)
    ds = xr.open_dataset(f)
    run = max_run_time(ds, "time_run")
    info = {"model": "HYCOM ESPC-D-V02 1/12°", "run": run, "run_id": run, "source": ESPC, "server_end": iso(e)}
    return to_opendrift(ds, {"water_u": "x_sea_water_velocity", "water_v": "y_sea_water_velocity"},
                        "current", f"HYCOM ESPC-D-V02 1/12 deg forecast, run {run} ({ESPC})"), info


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=72, help="forecast hours after now")
    ap.add_argument("--back-h", type=int, default=6, help="hours before now to include")
    ap.add_argument("--currents", choices=("auto", "rtofs", "espc"), default="auto")
    ap.add_argument("--keep-raw", action="store_true", help="keep RTOFS files in data/raw/global_live/ for fetch_global.py")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    now = now_utc()
    t0 = (now - timedelta(hours=a.back_h)).replace(minute=0, second=0, microsecond=0)
    t0 -= timedelta(hours=t0.hour % 3)
    want_end = now + timedelta(hours=a.hours)
    stamp = t0.strftime("%Y%m%dT%H")
    man = load_manifest()
    OUT.mkdir(parents=True, exist_ok=True)

    # --- currents -------------------------------------------------------------
    cur = OUT / "currents.nc"
    span = cached_span(cur)
    old = man.get("currents") or {}
    need = a.force or span is None or span[0] > now
    if not need:
        if old.get("model", "").startswith("NOAA RTOFS"):
            try:
                day, _ = rtofs_pick(want_end)
                need = day != old.get("run_id") or span[1] < want_end - timedelta(hours=3)
            except Exception as e:  # noqa: BLE001
                log(f"  RTOFS check failed ({e}); refetching")
                need = True
        else:  # ESPC / unknown cache: try to move to RTOFS (or a longer ESPC window)
            need = True
    if need:
        info = None
        if a.currents in ("auto", "rtofs"):
            try:
                ds, info = fetch_rtofs(t0, want_end, a.keep_raw)
            except Exception as e:  # noqa: BLE001
                if a.currents == "rtofs":
                    raise
                log(f"  RTOFS failed: {e}; falling back to HYCOM ESPC-D-V02")
        if info is None:
            ds, info = fetch_espc(t0, want_end, stamp)
        write_nc(ds, cur)
        ds.close()
        span = cached_span(cur)
        man["currents"] = {**info, "t0": iso(span[0]), "t1": iso(span[1]), "fetched_at": iso(now_utc()),
                           "file": "cache/forcing/live/currents.nc"}
    else:
        log(f"currents: cached run {old.get('run_id')} reaches {span[1]}, nothing newer")
    log("  " + verify(cur, "current"))

    # --- winds ----------------------------------------------------------------
    wnd = OUT / "winds.nc"
    span = cached_span(wnd)
    old = man.get("winds") or {}
    b, e = time_span(GFS)
    need = (a.force or span is None or span[0] > now or span[1] < want_end - timedelta(hours=3)
            or old.get("server_end") != iso(e))
    if need:
        s, t1 = max(t0, b), min((want_end + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0), e)
        log(f"GFS 10 m winds {s} .. {t1}")
        p = [("var", "u-component_of_wind_height_above_ground"), ("var", "v-component_of_wind_height_above_ground"),
             ("vertCoord", "10"), ("time_start", iso(s)), ("time_end", iso(t1))]
        f = download(GFS, RAW / "live" / f"gfs_{stamp}_{t1:%Y%m%dT%H}_{e:%Y%m%dT%H}.nc",
                     params=p + box_params(), timeout=900)
        ds = xr.open_dataset(f)
        # THREDDS names the axes time/time1/... and reftime/reftime1/... depending on the request
        ds = ds.rename({k: re.sub(r"\d+$", "", k) for k in list(ds.variables)
                        if re.fullmatch(r"(time|reftime)\d+", k) and re.sub(r"\d+$", "", k) not in ds.variables})
        run = max_run_time(ds, "reftime")
        ds = ds.rename({"latitude": "lat", "longitude": "lon"})
        ds = to_opendrift(ds, {"u-component_of_wind_height_above_ground": "x_wind",
                               "v-component_of_wind_height_above_ground": "y_wind"},
                          "wind", f"NCEP GFS 0.25 deg, latest run {run} ({GFS})")
        write_nc(ds, wnd)
        ds.close()
        span = cached_span(wnd)
        man["winds"] = {"model": "NCEP GFS 0.25°", "run": run, "run_id": run, "source": GFS, "server_end": iso(e),
                        "t0": iso(span[0]), "t1": iso(span[1]), "fetched_at": iso(now_utc()),
                        "file": "cache/forcing/live/winds.nc"}
    else:
        log(f"winds: cached GFS run {old.get('run')} reaches {span[1]}, nothing newer")
    log("  " + verify(wnd, "wind"))

    # --- manifest ---------------------------------------------------------------
    c, w = man["currents"], man["winds"]
    valid_until = min(c["t1"], w["t1"])
    man.update(updated_at=iso(now_utc()), requested_hours=a.hours,
               valid_from=max(c["t0"], w["t0"]), valid_until=valid_until,
               hours_from_now=round((datetime.fromisoformat(valid_until[:19]) - now).total_seconds() / 3600, 1))
    tmp = MANIFEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(man, indent=1))
    tmp.replace(MANIFEST)
    short = a.hours - man["hours_from_now"]
    log(f"live window valid {man['valid_from']} .. {valid_until} "
        f"({man['hours_from_now']} h from now{f', {short:.0f} h SHORT of {a.hours} h' if short > 0.5 else ''})")
    # old per-run raw chunks are not needed once the cache is written
    for f in (RAW / "live").glob("*.nc"):
        if stamp not in f.name:
            f.unlink()


if __name__ == "__main__":
    main()
