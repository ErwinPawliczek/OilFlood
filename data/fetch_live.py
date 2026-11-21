#!/usr/bin/env python
"""Fetch the live/recent forecast window for prediction mode.

Currents: HYCOM ESPC-D-V02 global 1/12 deg (FMRC "best" time series, 3-hourly),
          https://tds.hycom.org/thredds/ncss/FMRC_ESPC-D-V02_uv3z/FMRC_ESPC-D-V02_uv3z_best.ncd
Winds:    NCEP GFS 0.25 deg 10 m u/v (UCAR THREDDS "Best", 3-hourly),
          https://thredds.ucar.edu/thredds/ncss/grid/grib/NCEP/GFS/Global_0p25deg/Best

Window: from (now - back_h), floored to 3 h, to now + hours (default 72), clipped
to what each server has. NOAA RTOFS is not used: NOMADS retired OPeNDAP (SCN 25-81)
and the full RTOFS files are global and very large.

  python data/fetch_live.py                 # -> data/cache/forcing/live/{currents,winds}.nc
  python data/fetch_live.py --hours 96 --force

Re-run any time to refresh (it skips if the cached window already reaches now + hours,
unless --force). Older copies are not kept.
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta

import requests
import xarray as xr

from common import BOX, FORCING, RAW, UA, download, iso, log, now_utc, to_opendrift, verify, write_nc

ESPC = "https://tds.hycom.org/thredds/ncss/FMRC_ESPC-D-V02_uv3z/FMRC_ESPC-D-V02_uv3z_best.ncd"
GFS = "https://thredds.ucar.edu/thredds/ncss/grid/grib/NCEP/GFS/Global_0p25deg/Best"


def time_span(base: str) -> tuple[datetime, datetime]:
    xml = requests.get(base + "/dataset.xml", headers=UA, timeout=120).text
    b = re.search(r"<begin>([^<]+)</begin>", xml)[1]
    e = re.search(r"<end>([^<]+)</end>", xml)[1]
    return datetime.fromisoformat(b.replace("Z", "")), datetime.fromisoformat(e.replace("Z", ""))


def box_params(box=BOX):
    return [("north", box[3]), ("south", box[1]), ("west", box[0]), ("east", box[2]), ("accept", "netcdf4")]


def cached_end(path) -> datetime | None:
    if not path.exists():
        return None
    with xr.open_dataset(path) as ds:
        return datetime.fromisoformat(str(ds.time.values[-1])[:19])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hours", type=int, default=72, help="forecast hours after now")
    ap.add_argument("--back-h", type=int, default=6, help="hours before now to include")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    now = now_utc()
    t0 = (now - timedelta(hours=a.back_h)).replace(minute=0, second=0, microsecond=0)
    t0 -= timedelta(hours=t0.hour % 3)
    want_end = now + timedelta(hours=a.hours)
    out = FORCING / "live"
    raw = RAW / "live"
    stamp = t0.strftime("%Y%m%dT%H")

    # --- currents -------------------------------------------------------------
    cur = out / "currents.nc"
    ce = cached_end(cur)
    if a.force or ce is None or ce < min(want_end, time_span(ESPC)[1]) - timedelta(hours=3):
        b, e = time_span(ESPC)
        s, t1 = max(t0, b), min(want_end, e)
        log(f"ESPC-D-V02 currents {s} .. {t1} (server has {b} .. {e})")
        if t1 < want_end:
            log(f"  NOTE: ESPC forecast ends {e}, {(want_end - e).total_seconds() / 3600:.0f} h short of now+{a.hours} h")
        p = [("var", "water_u"), ("var", "water_v"), ("vertCoord", "0"), ("time_start", iso(s)), ("time_end", iso(t1))]
        f = download(ESPC, raw / f"espc_{stamp}_{t1:%Y%m%dT%H}.nc", params=p + box_params(), timeout=900)
        ds = xr.open_dataset(f)
        ds = to_opendrift(ds, {"water_u": "x_sea_water_velocity", "water_v": "y_sea_water_velocity"},
                          "current", f"HYCOM ESPC-D-V02 1/12 deg forecast ({ESPC})")
        write_nc(ds, cur)
        ds.close()
    log("  " + verify(cur, "current"))

    # --- winds ----------------------------------------------------------------
    wnd = out / "winds.nc"
    we = cached_end(wnd)
    if a.force or we is None or we < want_end - timedelta(hours=3):
        b, e = time_span(GFS)
        s, t1 = max(t0, b), min(want_end + timedelta(hours=3), e)
        log(f"GFS 10 m winds {s} .. {t1}")
        p = [("var", "u-component_of_wind_height_above_ground"), ("var", "v-component_of_wind_height_above_ground"),
             ("vertCoord", "10"), ("time_start", iso(s)), ("time_end", iso(t1))]
        f = download(GFS, raw / f"gfs_{stamp}_{t1:%Y%m%dT%H}.nc", params=p + box_params(), timeout=900)
        ds = xr.open_dataset(f)
        ds = ds.rename({"latitude": "lat", "longitude": "lon"})
        ds = to_opendrift(ds, {"u-component_of_wind_height_above_ground": "x_wind",
                               "v-component_of_wind_height_above_ground": "y_wind"},
                          "wind", f"NCEP GFS 0.25 deg ({GFS})")
        write_nc(ds, wnd)
        ds.close()
    log("  " + verify(wnd, "wind"))


if __name__ == "__main__":
    main()
