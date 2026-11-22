"""Gulf of Mexico bathymetry for the response layer (dispersant >= 10 m depth rule, distance to shore).

Source: NOAA NCEI ETOPO1 global relief (1 arc-minute, ice surface), served by NOAA CoastWatch ERDDAP
(dataset `etopo180`, https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.html). Box 17-31.5N, 98.5-79.5W.
Writes data/cache/bathy/etopo1_gulf.npz (~1.2 MB, gitignored; int16 altitude in m, negative = below sea level).

Run (from repo root, ~10 s):  python -I data/fetch_bathy.py
"""
from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

import numpy as np

URL = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.nc?"
       "altitude%5B(17.0):1:(31.5)%5D%5B(-98.5):1:(-79.5)%5D")
OUT = Path(__file__).resolve().parent / "cache" / "bathy" / "etopo1_gulf.npz"


def main() -> None:
    import xarray as xr
    OUT.parent.mkdir(parents=True, exist_ok=True)
    raw = urllib.request.urlopen(URL, timeout=300).read()
    d = xr.open_dataset(io.BytesIO(raw))
    a = d.altitude.values.astype(np.int16)
    np.savez_compressed(OUT, z=a, lat=d.latitude.values.astype(np.float64), lon=d.longitude.values.astype(np.float64),
                        source="NOAA NCEI ETOPO1 (ice surface, 1 arc-min) via NOAA CoastWatch ERDDAP etopo180",
                        url="https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.html")
    print(f"{OUT} {a.shape} {OUT.stat().st_size / 1e6:.1f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
