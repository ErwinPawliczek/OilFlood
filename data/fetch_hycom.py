#!/usr/bin/env python
"""Fetch HYCOM-TSIS GOMb0.04 reanalysis (Jan 2001 - Aug 2024, hourly, ~4 km)
surface currents + 10 m winds for a time window over the Gulf of Mexico box.

Source: https://tds.hycom.org/thredds/catalogs/GOMb0.04/reanalysis.xml
NCSS subset per year: .../ncss/GOMb0.04/reanalysis/<year>/3z (u, v at depth 0)
                      .../ncss/GOMb0.04/reanalysis/<year>/2d (wnd_ewd, wnd_nwd = 10 m wind)

Examples:
  python data/fetch_hycom.py --window texas_city_y            # 2014-03-21..26 hourly
  python data/fetch_hycom.py --window my_case --start 2012-06-01 --end 2012-06-04 --stride-h 3
  python data/fetch_hycom.py --catalog                         # every spill in data/spills/catalog.json HYCOM covers

Idempotent: raw daily chunks go to data/raw/hycom/<window>/, finished files to
data/cache/forcing/<window>/{currents,winds}.nc; re-running skips what exists.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import xarray as xr

from common import BOX, FORCING, RAW, SPILLS, day_chunks, download, iso, log, parse_time, to_opendrift, verify, write_nc

BASE = "https://tds.hycom.org/thredds/ncss/GOMb0.04/reanalysis/{year}/{kind}"
COVER = (datetime(2001, 1, 1), datetime(2024, 8, 31, 23))

WINDOWS = {
    # Texas City "Y" collision, Galveston Bay, 2014-03-22 ~12:30 CDT
    "texas_city_y": ("2014-03-21", "2014-03-26", 1),
}

KINDS = {
    "3z": {"vars": ["u", "v"], "extra": {"vertCoord": "0"}, "kind": "current", "out": "currents.nc",
           "rename": {"MT": "time", "Latitude": "lat", "Longitude": "lon", "u": "x_sea_water_velocity",
                      "v": "y_sea_water_velocity"}},
    "2d": {"vars": ["wnd_ewd", "wnd_nwd"], "extra": {}, "kind": "wind", "out": "winds.nc",
           "rename": {"MT": "time", "Latitude": "lat", "Longitude": "lon", "wnd_ewd": "x_wind",
                      "wnd_nwd": "y_wind"}},
}


def chunk_params(kind: str, t0: datetime, t1: datetime, stride_h: int, box=BOX):
    p = [("var", v) for v in KINDS[kind]["vars"]]
    p += [("north", box[3]), ("south", box[1]), ("west", box[0]), ("east", box[2]),
          ("time_start", iso(t0)), ("time_end", iso(t1)), ("timeStride", stride_h),
          ("accept", "netcdf4")]
    p += list(KINDS[kind]["extra"].items())
    return p


def fetch_window(name: str, start: datetime, end: datetime, stride_h: int = 1, workers: int = 3,
                 force: bool = False, box=BOX):
    start, end = max(start, COVER[0]), min(end, COVER[1])
    if start >= end:
        log(f"[{name}] outside HYCOM GOMb0.04 coverage {COVER[0]:%Y-%m-%d}..{COVER[1]:%Y-%m-%d}; skipped")
        return None
    outdir = FORCING / name
    done = all((outdir / k["out"]).exists() for k in KINDS.values())
    if done and not force:
        log(f"[{name}] already cached in {outdir}")
        return outdir
    log(f"[{name}] HYCOM GOMb0.04 {start} .. {end} every {stride_h} h")
    jobs = []
    for kind in KINDS:
        for t0, t1 in day_chunks(start, end, 24):
            last = t1 >= end
            t1_incl = t1 if last else t1 - timedelta(hours=stride_h)
            if t1_incl < t0:
                t1_incl = t0
            dest = RAW / "hycom" / name / f"{kind}_{t0:%Y%m%dT%H}.nc"
            url = BASE.format(year=t0.year, kind=kind)
            jobs.append((url, dest, chunk_params(kind, t0, t1_incl, stride_h, box)))

    def run(j):
        url, dest, params = j
        existed = dest.exists()
        download(url, dest, params=params, timeout=900)
        if not existed:
            log(f"  got {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(run, jobs))

    for kind, spec in KINDS.items():
        files = sorted((RAW / "hycom" / name).glob(f"{kind}_*.nc"))
        ds = xr.open_mfdataset(files, combine="nested", concat_dim="MT", decode_times=True)
        pad = timedelta(minutes=10)  # raw times jitter by a few minutes; snap first, then slice
        ds = ds.sel(MT=slice(start - pad, end + pad))
        ds = to_opendrift(ds, spec["rename"], spec["kind"], f"HYCOM-TSIS GOMb0.04 reanalysis ({BASE.format(year='YYYY', kind=kind)})")
        ds = ds.sel(time=slice(start, end))
        dest = write_nc(ds, outdir / spec["out"])
        ds.close()
        log("  " + verify(dest, spec["kind"]))
    return outdir


def stride_for(hours: float) -> int:
    """Keep long windows manageable: hourly <= 7 d, 3-hourly <= 30 d, else 6-hourly."""
    return 1 if hours <= 7 * 24 else 3 if hours <= 30 * 24 else 6


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", help="window name (preset or custom with --start/--end)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--stride-h", type=int)
    ap.add_argument("--catalog", action="store_true", help="fetch every catalog spill covered by HYCOM")
    ap.add_argument("--only", help="with --catalog: comma-separated spill ids")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true", help="rebuild merged files from raw chunks")
    a = ap.parse_args()

    if a.catalog:
        spills = json.loads((SPILLS / "catalog.json").read_text())
        spills = spills["spills"] if isinstance(spills, dict) else spills
        only = set(a.only.split(",")) if a.only else None
        for s in spills:
            if only and s["id"] not in only:
                continue
            fw = s.get("forcing_window") or {}
            pad = timedelta(0) if fw else timedelta(days=1)
            start = parse_time(fw.get("start") or s["date_start"]) - pad
            end = parse_time(fw.get("end") or s.get("date_end") or s["date_start"]) + pad
            if not s.get("forcing"):
                log(f"[{s['id']}] {s['date_start'][:10]} not covered by HYCOM GOMb0.04; skipped")
                continue
            stride = a.stride_h or fw.get("stride_h") or stride_for((end - start).total_seconds() / 3600)
            try:
                fetch_window(s["id"], start, end, stride, a.workers, a.force)
            except Exception as e:  # noqa: BLE001
                log(f"[{s['id']}] FAILED: {e}")
        return
    if not a.window:
        ap.error("--window or --catalog required")
    if a.window in WINDOWS and not a.start:
        s, e, st = WINDOWS[a.window]
    else:
        if not (a.start and a.end):
            ap.error("custom window needs --start and --end")
        s, e, st = a.start, a.end, 1
    fetch_window(a.window, parse_time(s), parse_time(e), a.stride_h or st, a.workers, a.force)


if __name__ == "__main__":
    main()
