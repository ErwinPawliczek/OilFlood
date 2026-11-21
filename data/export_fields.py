#!/usr/bin/env python
"""Export downsampled surface-current fields for the frontend flow layer.

Reads data/cache/forcing/<window>/currents.nc and writes data/cache/fields/<window>/:
  index.json           {window, bbox, lon0, lat0, dlon, dlat, nx, ny, times:[unix_s], vmax, units, source,
                        json:"<t>.json", png:"<t>.png"}
  <unix_t>.json        {t, lon0, lat0, dlon, dlat, nx, ny, u:[...], v:[...], units, source}
                       u/v row-major, rows south->north (lat0 is the south edge), null = land.
                       Same shape as the backend's GET /api/currents/{t}.
  <unix_t>.png         RGBA texture, rows NORTH->south (image order): R = (u/vmax + 1) * 127.5,
                       G = (v/vmax + 1) * 127.5, B = speed/vmax * 255, A = 255 water / 0 land.

  python data/export_fields.py texas_city_y live      # one or more windows
  python data/export_fields.py --all                  # every window in data/cache/forcing/
  python data/export_fields.py live --step 0.1 --every-h 3
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import xarray as xr
from PIL import Image

from common import BOX, FIELDS, FORCING, log


def export(window: str, step: float = 0.1, every_h: int = 1, force: bool = False, vmax: float = 2.0):
    src = FORCING / window / "currents.nc"
    if not src.exists():
        log(f"[{window}] no {src}; run a fetch script first")
        return
    out = FIELDS / window
    idx_path = out / "index.json"
    if idx_path.exists() and not force and idx_path.stat().st_mtime > src.stat().st_mtime:
        log(f"[{window}] fields up to date")
        return
    out.mkdir(parents=True, exist_ok=True)
    with xr.open_dataset(src) as ds:
        lon0, lat0 = max(BOX[0], float(ds.lon.min())), max(BOX[1], float(ds.lat.min()))
        lon1, lat1 = min(BOX[2], float(ds.lon.max())), min(BOX[3], float(ds.lat.max()))
        lons = np.round(np.arange(lon0, lon1 + 1e-9, step), 4)
        lats = np.round(np.arange(lat0, lat1 + 1e-9, step), 4)
        times = ds.time.values
        t_unix = (times.astype("datetime64[s]").astype(np.int64))
        keep = [i for i, t in enumerate(t_unix) if (t - t_unix[0]) % (every_h * 3600) == 0]
        # coarsen by block-mean onto the target grid (nan-aware), then interp to exact nodes
        sub = ds[["x_sea_water_velocity", "y_sea_water_velocity"]].isel(time=keep)
        native = float(abs(ds.lon[1] - ds.lon[0]))
        f = max(1, int(round(step / native)))
        if f > 1:
            sub = sub.coarsen(lon=f, lat=f, boundary="trim").mean()
        sub = sub.interp(lon=lons, lat=lats, method="linear").load()
        written = []
        for k, i in enumerate(keep):
            t = int(t_unix[i])
            u = sub.x_sea_water_velocity.isel(time=k).values
            v = sub.y_sea_water_velocity.isel(time=k).values
            mask = np.isfinite(u) & np.isfinite(v)

            def pack(a):
                a = np.round(a.astype(float), 3)
                return [float(x) if np.isfinite(x) else None for x in a.ravel()]

            payload = {"t": t, "lon0": float(lons[0]), "lat0": float(lats[0]), "dlon": step, "dlat": step,
                       "nx": len(lons), "ny": len(lats), "u": pack(u), "v": pack(v), "units": "m/s",
                       "source": f"{window}/currents.nc"}
            (out / f"{t}.json").write_text(json.dumps(payload, separators=(",", ":")))
            uu, vv = np.nan_to_num(u), np.nan_to_num(v)
            rgba = np.zeros((len(lats), len(lons), 4), np.uint8)
            rgba[..., 0] = np.clip((uu / vmax + 1) * 127.5, 0, 255)
            rgba[..., 1] = np.clip((vv / vmax + 1) * 127.5, 0, 255)
            rgba[..., 2] = np.clip(np.hypot(uu, vv) / vmax * 255, 0, 255)
            rgba[..., 3] = np.where(mask, 255, 0)
            Image.fromarray(rgba[::-1]).save(out / f"{t}.png", optimize=True)
            written.append(t)
        src_attr = ds.attrs.get("source", "")
    index = {"window": window, "bbox": [float(lons[0]), float(lats[0]), float(lons[-1]), float(lats[-1])],
             "lon0": float(lons[0]), "lat0": float(lats[0]), "dlon": step, "dlat": step,
             "nx": len(lons), "ny": len(lats), "times": written, "vmax": vmax, "units": "m/s",
             "json": "<t>.json", "png": "<t>.png", "source": src_attr,
             "png_encoding": "R=(u/vmax+1)*127.5, G=(v/vmax+1)*127.5, B=speed/vmax*255, A=255 water/0 land; rows north->south"}
    idx_path.write_text(json.dumps(index, indent=1))
    size = sum(p.stat().st_size for p in out.iterdir())
    log(f"[{window}] {len(written)} fields {len(lons)}x{len(lats)} -> {out} ({size / 1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("windows", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--step", type=float, default=0.1, help="grid spacing in degrees")
    ap.add_argument("--every-h", type=int, default=0, help="keep one field every N hours (default: 1 h, 3 h live, 6 h if > 30 days)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    wins = sorted(p.name for p in FORCING.iterdir() if (p / "currents.nc").exists()) if a.all else a.windows
    if not wins:
        ap.error("give window names or --all")
    for w in wins:
        every = a.every_h
        if not every:
            with xr.open_dataset(FORCING / w / "currents.nc") as ds:
                span_h = (ds.time.values[-1] - ds.time.values[0]) / np.timedelta64(1, "h")
            every = 6 if span_h > 30 * 24 else 1
        export(w, a.step, every, a.force)


if __name__ == "__main__":
    main()
