#!/usr/bin/env python
"""Deepwater Horizon daily surface-oil outlines from NOAA/NESDIS Satellite Analysis Branch.

Source: https://satepsanone.nesdis.noaa.gov/pub/OMS/disasters/DeepwaterHorizon/
  composites/2010/YYYYMMDD_Composite.zip   daily composite analyses (2010-05-17 .. 2010-08-25)
  mpsr/2010/YYYYMMDD/shapefiles_*.zip      per-image Marine Pollution Surveillance Reports
                                           (used for days without a composite: unioned)

Output: data/spills/dwh/footprints/dwh_YYYYMMDD.geojson (one FeatureCollection per day,
one MultiPolygon feature, lon/lat WGS84, simplified to ~500 m). Raw zips cached in
data/raw/dwh/. Idempotent; re-run skips existing days (--force to rebuild).

  python data/fetch_dwh_footprints.py
"""
from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from pathlib import Path

import requests
import shapefile  # pyshp
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from common import RAW, SPILLS, UA, download, log

BASE = "https://satepsanone.nesdis.noaa.gov/pub/OMS/disasters/DeepwaterHorizon"
OUT = SPILLS / "dwh" / "footprints"
SIMPLIFY_DEG = 0.004


def listing(url: str) -> list[str]:
    html = requests.get(url, headers=UA, timeout=120).text
    return [h for h in re.findall(r'href="([^"?/][^"]*)"', html)]


def read_zip(path: Path):
    """Yield shapely geometries (WGS84) from every shapefile in a zip."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    for shp in [n for n in names if n.lower().endswith(".shp")]:
        stem = shp[:-4]
        get = lambda ext: next((n for n in names if n.lower() == (stem + ext).lower()), None)  # noqa: E731
        dbf, shx, prj = get(".dbf"), get(".shx"), get(".prj")
        r = shapefile.Reader(shp=io.BytesIO(z.read(shp)), dbf=io.BytesIO(z.read(dbf)) if dbf else None,
                             shx=io.BytesIO(z.read(shx)) if shx else None)
        tf = None
        if prj:
            crs = CRS.from_wkt(z.read(prj).decode("latin-1"))
            if not crs.is_geographic:
                tf = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
        for i in range(len(r)):
            try:  # some NESDIS files have corrupt records; skip just those
                s = r.shape(i)
                if s.shapeType == shapefile.NULL:
                    continue
                g = shape(s.__geo_interface__)
            except Exception:  # noqa: BLE001
                continue
            if g.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            if tf:
                g = transform(tf, g)
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty:
                yield g


def round_coords(o, nd=4):
    if isinstance(o, (list, tuple)):
        if o and isinstance(o[0], (int, float)):
            return [round(o[0], nd), round(o[1], nd)]
        return [round_coords(x, nd) for x in o]
    return o


def build_day(day: str, zips: list[Path], kind: str, force: bool) -> Path | None:
    dest = OUT / f"dwh_{day}.geojson"
    if dest.exists() and not force:
        return dest
    geoms = []
    for z in zips:
        try:
            geoms += list(read_zip(z))
        except Exception as e:  # noqa: BLE001
            log(f"  {z.name}: unreadable ({e})")
    if not geoms:
        log(f"  {day}: no polygons")
        return None
    g = unary_union(geoms).simplify(SIMPLIFY_DEG, preserve_topology=True)
    if g.geom_type == "Polygon":
        from shapely.geometry import MultiPolygon
        g = MultiPolygon([g])
    lat = g.centroid.y
    import math
    area_km2 = g.area * (111.32 ** 2) * math.cos(math.radians(lat))
    gj = mapping(g)
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "spill": "dwh", "kind": kind,
                       "area_km2": round(area_km2, 1), "n_files": len(zips),
                       "source": "NOAA/NESDIS Satellite Analysis Branch, " + ", ".join(z.name for z in zips)},
        "geometry": {"type": gj["type"], "coordinates": round_coords(gj["coordinates"])},
    }]}
    OUT.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(fc, separators=(",", ":")))
    log(f"  {day}: {kind} {len(zips)} file(s) -> {area_km2:,.0f} km2, {dest.stat().st_size / 1e3:.0f} kB")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    raw = RAW / "dwh"
    days: dict[str, tuple[str, list[str]]] = {}
    for f in listing(f"{BASE}/composites/2010/"):
        m = re.match(r"(\d{8})_Composite\.zip$", f)
        if m:
            days[m[1]] = ("composite", [f"{BASE}/composites/2010/{f}"])
    for d in listing(f"{BASE}/mpsr/2010/"):
        day = d.strip("/")
        if not re.fullmatch(r"\d{8}", day) or day in days:
            continue
        files = [f for f in listing(f"{BASE}/mpsr/2010/{day}/") if f.lower().endswith(".zip") and "shape" in f.lower()]
        # prefer "_revised" versions of the same pass
        revised = {f.replace("_revised", "") for f in files if "_revised" in f}
        files = [f for f in files if f not in revised]
        if files:
            days[day] = ("mpsr_union", [f"{BASE}/mpsr/2010/{day}/{f}" for f in files])
    log(f"{len(days)} days with outlines: {min(days)} .. {max(days)}")
    n = 0
    for day in sorted(days):
        kind, urls = days[day]
        if (OUT / f"dwh_{day}.geojson").exists() and not a.force:
            n += 1
            continue
        zips = [download(u, raw / day / u.rsplit("/", 1)[1], timeout=300) for u in urls]
        if build_day(day, zips, kind, a.force):
            n += 1
    total = sum(p.stat().st_size for p in OUT.glob("*.geojson"))
    log(f"{n} daily footprints in {OUT} ({total / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
