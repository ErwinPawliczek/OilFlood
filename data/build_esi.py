"""Gulf coast shoreline sensitivity grid from NOAA's National ESI Shoreline (ESIL).

Source: NOAA OR&R National Environmental Sensitivity Index Shoreline, Gulf/Atlantic ESI
(https://response.restoration.noaa.gov/esi_download -> NationalESI_Shoreline.zip, ~237 MB, compiled 2017
from the most recent state ESI atlases, 1996-2017).

Output (committed, small): data/esi/gulf_esi_cells.json. The Gulf shoreline (TX, LA, MS, AL, FL Gulf side
incl. the Keys) is binned into 0.05 deg cells (~5 km). Each cell keeps the shoreline length, the most
sensitive ESI class in it, the length-dominant class, the length of ESI 10 wetlands, and the state atlas.

Run (from repo root, ~2 min):
  curl -L -o data/raw/esi_dl/NationalESI_Shoreline.zip \
    https://response.restoration.noaa.gov/sites/default/files/esimaps/gisdata/NationalESI_Shoreline.zip
  (cd data/raw/esi_x && unzip ../esi_dl/NationalESI_Shoreline.zip)
  PROJ_DATA=$CONDA_PREFIX/share/proj python -I data/build_esi.py data/raw/esi_x/GULF_ATLANTIC_ESI.gdb
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pyogrio
import shapely

CELL = 0.05
GULF_ATLASES = {"TEXAS", "LOUISIANA", "MISSISSIPPI", "ALABAMA", "FLORIDA PANHANDLE", "NORTHWEST FLORIDA",
                "SOUTHWEST FLORIDA", "SOUTH FLORIDA"}
ATLAS_STATE = {"TEXAS": "TX", "LOUISIANA": "LA", "MISSISSIPPI": "MS", "ALABAMA": "AL", "FLORIDA PANHANDLE": "FL",
               "NORTHWEST FLORIDA": "FL", "SOUTHWEST FLORIDA": "FL", "SOUTH FLORIDA": "FL"}
OUT = Path(__file__).resolve().parent / "esi" / "gulf_esi_cells.json"


def rank(code: str) -> float:
    """'10A' -> 10.01, '8C' -> 8.03, '7' -> 7.0 (higher = more sensitive to oil)."""
    m = re.match(r"\s*(\d+)([A-Z]?)", code or "")
    if not m:
        return 0.0
    return int(m[1]) + (ord(m[2]) - 64) / 100 if m[2] else float(m[1])


def main(gdb: str) -> None:
    df = pyogrio.read_dataframe(gdb, bbox=(-98, 24.4, -80.0, 31.0),
                                columns=["ESI", "LINE", "ENVIR", "MOST_SENSITIVE", "LANDWARD_SHORETYPE",
                                         "ATLAS_NAME", "YEAR_PUB"])
    df = df[df.ATLAS_NAME.isin(GULF_ATLASES) & df.LINE.isin(["S", "B"]) & df.ENVIR.isin(["E", "M"])]
    # South Florida atlas covers both coasts: keep the Gulf side + Florida Bay/Keys, drop Biscayne/Atlantic.
    c = shapely.centroid(df.geometry.values)
    cx, cy = shapely.get_x(c), shapely.get_y(c)
    df = df[(df.ATLAS_NAME != "SOUTH FLORIDA") | (cx < -80.9) | (cy < 25.35)]
    print(f"{len(df)} Gulf ESI shoreline features", file=sys.stderr)

    # Labels for each ESI code, taken from the data's own LANDWARD_SHORETYPE strings ("8C: Sheltered Riprap").
    lab = defaultdict(Counter)
    for s in df.LANDWARD_SHORETYPE.dropna():
        m = re.match(r"\s*(\d+[A-Z]?)\s*:\s*(.+)", s)
        if m:
            lab[m[1]][m[2].strip()] += 1
    labels = {k: v.most_common(1)[0][0] for k, v in lab.items()}

    parts = shapely.get_parts(df.geometry.values, return_index=True)
    geoms, gidx = parts
    xy, pidx = shapely.get_coordinates(geoms, return_index=True)
    same = pidx[1:] == pidx[:-1]
    a, b = xy[:-1][same], xy[1:][same]
    feat = gidx[pidx[:-1][same]]
    mid = (a + b) / 2
    kx = 111.32 * np.cos(np.radians(mid[:, 1]))
    seg_km = np.hypot((b[:, 0] - a[:, 0]) * kx, (b[:, 1] - a[:, 1]) * 110.57)

    ms = df.MOST_SENSITIVE.fillna("").values
    atlas = df.ATLAS_NAME.values
    years = df.YEAR_PUB.values
    ci = np.floor(mid[:, 0] / CELL).astype(int)
    cj = np.floor(mid[:, 1] / CELL).astype(int)

    cells: dict[tuple[int, int], dict] = {}
    for k in range(len(seg_km)):
        key = (int(ci[k]), int(cj[k]))
        f = feat[k]
        c = cells.get(key)
        if c is None:
            c = cells[key] = {"km": 0.0, "sx": 0.0, "sy": 0.0, "codes": Counter(), "atlas": Counter(), "year": Counter()}
        L = float(seg_km[k])
        c["km"] += L
        c["sx"] += mid[k, 0] * L
        c["sy"] += mid[k, 1] * L
        c["codes"][ms[f]] += L
        c["atlas"][atlas[f]] += L
        c["year"][int(years[f])] += L

    atlases = sorted(GULF_ATLASES)
    rows = []
    for c in cells.values():
        if c["km"] < 0.05:
            continue
        codes = {k: v for k, v in c["codes"].items() if rank(k) > 0}
        if not codes:
            continue
        cmax = max(codes, key=rank)
        cmain = max(codes, key=codes.get)
        km10 = sum(v for k, v in codes.items() if rank(k) >= 10)
        rows.append([round(c["sy"] / c["km"], 4), round(c["sx"] / c["km"], 4), round(c["km"], 1), cmax, cmain,
                     round(km10, 1), atlases.index(c["atlas"].most_common(1)[0][0]),
                     c["year"].most_common(1)[0][0]])
    rows.sort(key=lambda r: (r[1], r[0]))
    out = {
        "source": "NOAA OR&R National ESI Shoreline (Gulf/Atlantic ESIL, compiled 2017 from state ESI atlases)",
        "source_url": "https://response.restoration.noaa.gov/esi_download",
        "data_url": "https://response.restoration.noaa.gov/sites/default/files/esimaps/gisdata/NationalESI_Shoreline.zip",
        "cell_deg": CELL,
        "fields": ["lat", "lon", "shore_km", "esi_most_sensitive", "esi_dominant", "wetland_km_esi10", "atlas",
                   "atlas_year"],
        "atlases": atlases,
        "atlas_state": ATLAS_STATE,
        "labels": dict(sorted(labels.items(), key=lambda kv: rank(kv[0]))),
        "cells": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    print(f"{len(rows)} cells, {sum(r[2] for r in rows):.0f} km shoreline -> {OUT} "
          f"({OUT.stat().st_size / 1e3:.0f} KB)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/raw/esi_x/GULF_ATLANTIC_ESI.gdb")
