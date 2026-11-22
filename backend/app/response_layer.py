"""Map layer for the response outline (T21, docs/RESPONSE.md "Response layer").

Turns the rule-based outline of one model run into geometry the globe can draw, all in GeoJSON-ish dicts:
  - shoreline arrival segments: real NOAA ESI shoreline lines in the oiled/threatened cells, coloured by ETA + ESI
  - offshore arrival isochrones: where surface oil has reached by T+6/12/24/48/72 h
  - boom plan: ESI shoreline lines next to where particles strand, typed exclusion / deflection / beach crews,
    with deploy-by and mobilize-by times
  - tactic zones per 3 h step: dispersant and in-situ burn polygons = surface oil x time window (oil age, wind,
    daylight) x regulatory envelope (>= 3 nm from shore and >= 10 m deep, ETOPO1 bathymetry)
  - mechanical recovery targets: densest surface-oil clusters per 3 h step
  - staging: nearest real staging bases (data/response/staging_bases.json, cited) with travel-time lines
  - timeline: dated actions (mobilize, deploy boom, windows open/close, landfall) for the "Next actions" list

Planning aid only, not operational guidance (same disclaimer as the outline).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

NM_KM = 1.852
KT = 1.943844

# --- thresholds: all from app/protocols.py (cited rule IDs, docs/PROTOCOLS.md) ----------------------------
from . import protocols as P  # noqa: E402

BOOM_MAX_KM = 8.0       # boom drawn per site (display cap only; the real length comes from the GRP / survey)
DISP_NM, DISP_DEPTH_M = P.DISP_MIN_NM, P.DISP_MIN_DEPTH_M   # DISP-1
BURN_NM = P.BURN_MIN_NM                                     # ISB-1
STEP_H = 3              # zones / skimmer targets every 3 h (display step)
ZS = 0.02               # ~2 km zone grid; a "patch" = 3x3 cells (~6 km) = what one fire-boom team can work
ARRIVAL_BANDS = [(12, "<12 h"), (24, "12-24 h"), (48, "24-48 h"), (72, "48-72 h"), (1e9, ">72 h")]
ISO_HOURS = (6, 12, 24, 48, 72)


def _band(h: float) -> int:
    for i, (lim, _) in enumerate(ARRIVAL_BANDS):
        if h < lim:
            return i
    return len(ARRIVAL_BANDS) - 1


def _r(x, n=4):
    return round(float(x), n)


def patch_bbl(p: np.ndarray, per_bbl: float) -> tuple[float, float]:
    """Surface oil in the densest ~6 km patch (3x3 cells of ZS deg) in bbl, and the densest cell's mean thickness
    in mm (a lower bound: real oil is patchy). p = (n, 2) positions at one hour (NaN = not on the surface)."""
    p = p[np.isfinite(p[:, 0])]
    if not len(p) or per_bbl <= 0:
        return 0.0, 0.0
    occ: dict = {}
    for k in zip(np.floor(p[:, 1] / ZS).astype(int).tolist(), np.floor(p[:, 0] / ZS).astype(int).tolist()):
        occ[k] = occ.get(k, 0) + 1
    best = max(sum(occ.get((k[0] + a, k[1] + b), 0) for a in (-1, 0, 1) for b in (-1, 0, 1)) for k in occ)
    kmax = max(occ.values())
    lat = float(np.mean(p[:, 1]))
    area_m2 = (ZS * 110574) * (ZS * 111320 * math.cos(math.radians(lat)))
    return best * per_bbl, kmax * per_bbl * 0.158987 / area_m2 * 1000


def nearest_equipment_base(lon: float, lat: float, k: int = 1, types=("osro_depot", "uscg_station")):
    """Nearest boom/skimmer sources (OSRO / state depots, USCG small-boat stations) from staging_bases.json."""
    bases = (staging_bases() or {}).get("bases", [])
    ds = sorted(((_km(lon, lat, x["lon"], x["lat"]), x) for x in bases if x["type"] in types), key=lambda t: t[0])
    out = [{"name": x["name"], "type": x["type"], "lon": x["lon"], "lat": x["lat"], "km": round(d, 1),
            "travel_h": round(d / (P.ROAD_MPH * 1.609344), 1)} for d, x in ds[:k]]
    return out if k > 1 else (out[0] if out else None)


def _km(lon1, lat1, lon2, lat2):
    la = math.radians((lat1 + lat2) / 2)
    return math.hypot((lon2 - lon1) * 111.32 * math.cos(la), (lat2 - lat1) * 110.57)


# --- static data ------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def bathy():
    """ETOPO1 Gulf grid (data/cache/bathy/etopo1_gulf.npz, fetched by data/fetch_bathy.py) or None."""
    from .response import _data_file
    p = _data_file("cache/bathy/etopo1_gulf.npz")
    if p is None:
        return None
    d = np.load(p)
    z, lat, lon = d["z"].astype(np.float32), d["lat"], d["lon"]
    land = z >= 0
    # coastline pixels: land with a water 4-neighbour (and water pixels next to land are shore-adjacent)
    pad = np.pad(land, 1, constant_values=False)
    water_nb = (~pad[:-2, 1:-1] | ~pad[2:, 1:-1] | ~pad[1:-1, :-2] | ~pad[1:-1, 2:])
    coast = land & water_nb
    iy, ix = np.nonzero(coast)
    from .response import _xy
    tree = cKDTree(_xy(lon[ix], lat[iy]))
    return {"z": z, "lat": lat, "lon": lon, "tree": tree, "dlat": float(lat[1] - lat[0]),
            "dlon": float(lon[1] - lon[0]), "source": str(d["source"]), "url": str(d["url"])}


def depth_m(lon, lat):
    """Water depth (m, positive down; <= 0 on land) at points, nearest ETOPO1 pixel. None if no bathymetry."""
    b = bathy()
    if b is None:
        return None
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    iy = np.clip(np.rint((lat - b["lat"][0]) / b["dlat"]).astype(int), 0, len(b["lat"]) - 1)
    ix = np.clip(np.rint((lon - b["lon"][0]) / b["dlon"]).astype(int), 0, len(b["lon"]) - 1)
    return -b["z"][iy, ix]


def shore_km(lon, lat):
    """Distance (km) to the nearest shoreline: ETOPO1 coastline pixels (incl. Mexico/Cuba) and NOAA ESI
    shoreline cells, whichever is nearer."""
    from .response import _xy, esi
    xy = _xy(np.atleast_1d(lon), np.atleast_1d(lat))
    d = esi()[3].query(xy)[0]
    b = bathy()
    if b is not None:
        d = np.minimum(d, b["tree"].query(xy)[0])
    return d


@lru_cache(maxsize=1)
def staging_bases() -> dict | None:
    from .response import _data_file
    p = _data_file("response/staging_bases.json")
    if p is None:
        return None
    return json.loads(p.read_text())


def _esi_gdb() -> Path | None:
    from .response import _data_file
    return _data_file("raw/esi_x/GULF_ATLANTIC_ESI.gdb")


def _read_esi_lines(bboxes: list[tuple]) -> list[tuple]:
    """Real ESI shoreline features inside the given bboxes -> [(esi_code, rank, LineString coords...)]."""
    gdb = _esi_gdb()
    if gdb is None:
        return []
    import os
    import sys
    if not os.environ.get("PROJ_DATA"):
        os.environ["PROJ_DATA"] = str(Path(sys.prefix) / "share" / "proj")
    import pyogrio
    import shapely
    from .response import esi_rank
    out, seen = [], set()
    for bb in bboxes:
        try:
            df = pyogrio.read_dataframe(str(gdb), bbox=bb, columns=["ESI", "LINE", "ENVIR", "ATLAS_NAME"])
        except Exception:  # noqa: BLE001 - geometry is optional context
            continue
        df = df[df.LINE.isin(["S", "B"]) & df.ENVIR.isin(["E", "M"])]
        geoms = shapely.simplify(df.geometry.values, 0.0003)
        for code, g in zip(df.ESI.values, geoms):
            if g is None or g.is_empty:
                continue
            for part in getattr(g, "geoms", [g]):
                if part.length < 0.0005:
                    continue
                c = np.asarray(part.coords)[:, :2]
                key = (round(c[0, 0], 5), round(c[0, 1], 5), round(c[-1, 0], 5), round(c[-1, 1], 5))
                if key in seen:
                    continue
                seen.add(key)
                ranks = [esi_rank(x) for x in str(code or "").split("/")]
                out.append((str(code or ""), max(ranks) if ranks else 0.0, c))
    return out


# --- geometry helpers ----------------------------------------------------------------------------------------

def _cells_union(cells: set[tuple[int, int]], step: float, smooth: bool = True):
    """Grid cells (iy, ix) of size `step` deg -> shapely (Multi)Polygon."""
    import shapely
    if not cells:
        return None
    boxes = [shapely.box(ix * step, iy * step, (ix + 1) * step, (iy + 1) * step) for iy, ix in cells]
    g = shapely.union_all(boxes)
    if smooth:
        g = g.buffer(step * 0.35, join_style="round").buffer(-step * 0.35, join_style="round")
    return shapely.simplify(g, step * 0.2)


def _poly_coords(g) -> list:
    """shapely (Multi)Polygon -> GeoJSON MultiPolygon coordinates (rounded)."""
    if g is None or g.is_empty:
        return []
    out = []
    for p in getattr(g, "geoms", [g]):
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        rings = [p.exterior, *p.interiors]
        out.append([[[_r(x), _r(y)] for x, y in r.coords] for r in rings])
    return out


def _sample_current_uv(req: dict, t: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Current vector (u east, v north; kt) from the run's own current forcing at points/times; NaN where unknown."""
    from .ourmodel.grid import Grid
    nan = np.full(len(lon), np.nan)
    if not len(lon):
        return nan, nan
    bb = (float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max()))
    t0 = datetime.fromtimestamp(float(np.min(t)), timezone.utc).replace(tzinfo=None)
    t1 = datetime.fromtimestamp(float(np.max(t)) + 3600, timezone.utc).replace(tzinfo=None)
    for f in req.get("forcing_files", []):
        if "current" not in f.get("kinds", []) or not Path(f["path"]).exists():
            continue
        try:
            g = Grid(f["path"], "current", bb, t0, t1, pad_deg=0.3)
            with np.errstate(invalid="ignore"):
                u, v = g.sample(t, lon, lat)
            return np.asarray(u) * KT, np.asarray(v) * KT
        except Exception:  # noqa: BLE001
            continue
    return nan, nan


# --- main ---------------------------------------------------------------------------------------------------

def build_layer(*, t0: float, hrs: np.ndarray, pos: np.ndarray, status: np.ndarray, end_xy: np.ndarray,
                end_t: np.ndarray, hit: dict, stretch_cells: list[list[int]], shorelines: list[dict],
                boom_first: list[dict], hours: list[dict], oc: dict, req: dict, port_entrances: list,
                members: list[dict] | None = None, lon0: float = 0.0, lat0: float = 0.0) -> dict:
    from .response import esi, _xy, _iso, PORT_ENTRANCES  # noqa: F401
    ed, elat, elon, _ = esi()
    cells = ed["cells"]
    nH = len(hrs)
    H = (hrs - t0) / 3600
    notes: list[str] = []
    b = bathy()
    if b is None:
        notes.append("No cached bathymetry (data/fetch_bathy.py): dispersant zones use distance from shore only, "
                     "the >= 10 m depth rule is NOT checked.")

    # ---------------- 1. shoreline arrival segments (real ESI lines in oiled / threatened cells) -------------
    cell_eta = {}
    for k, h in hit.items():
        e = [x for x in (h["eta"] if h["n"] else None, h["approach"]) if x is not None]
        if e:
            cell_eta[k] = ((min(e) - t0) / 3600, "landfall" if h["n"] else "threatened")
    cell_key = {(int(math.floor(cells[k][0] / 0.05)), int(math.floor(cells[k][1] / 0.05))): k for k in cell_eta}
    # read ESI per stretch bbox (stretches are <= ~30 km), most important stretches first
    bboxes = []
    for s in shorelines[:40]:
        x0, y0, x1, y1 = s["bbox"]
        bboxes.append((x0 - 0.03, y0 - 0.03, x1 + 0.03, y1 + 0.03))
    feats = _read_esi_lines(bboxes)
    segs = []
    nverts = 0
    for code, rank, c in feats:
        mid = c[len(c) // 2]
        key = (int(math.floor(mid[1] / 0.05)), int(math.floor(mid[0] / 0.05)))
        k = cell_key.get(key)
        if k is None:
            continue
        eta_h, imp = cell_eta[k]
        if nverts > 40000:
            break
        nverts += len(c)
        tier = 1 if rank >= 9 else 2 if rank >= 7 else 3
        segs.append({"path": [[_r(x), _r(y)] for x, y in c], "eta_h": round(eta_h, 1), "band": _band(eta_h),
                     "impact": imp, "esi": code, "tier": tier})
    shore_src = "lines"
    if not segs and cell_eta and _esi_gdb() is None:  # no raw ESI geodatabase on this machine: fall back to the committed ESI cell centroids
        shore_src = "cells"
        for k, (eta_h, imp) in cell_eta.items():
            from .response import esi_rank
            rank = esi_rank(cells[k][3])
            segs.append({"point": [_r(elon[k]), _r(elat[k])], "eta_h": round(eta_h, 1), "band": _band(eta_h),
                         "impact": imp, "esi": cells[k][3], "tier": 1 if rank >= 9 else 2 if rank >= 7 else 3})
        notes.append("Raw NOAA ESI geodatabase not found (data/raw/esi_x): shoreline arrival is shown per 5 km ESI "
                     "cell, boom lines are not drawn.")

    # ---------------- 2. offshore arrival isochrones (first time surface oil enters a 0.05 deg cell) ---------
    STEP = 0.05
    first = {}
    for j in range(nH):
        p = pos[:, j]
        p = p[np.isfinite(p[:, 0])]
        if not len(p):
            continue
        for iy, ix in set(zip(np.floor(p[:, 1] / STEP).astype(int).tolist(), np.floor(p[:, 0] / STEP).astype(int).tolist())):
            if (iy, ix) not in first:
                first[(iy, ix)] = H[j]
    isochrones = []
    for th in ISO_HOURS:
        if th > H[-1] + 0.01:
            continue
        g = _cells_union({c for c, h in first.items() if h <= th}, STEP)
        coords = _poly_coords(g)
        if coords:
            isochrones.append({"t_h": th, "coordinates": coords})

    # ---------------- 2b. uncertainty envelope (R1): best guess + windage 1-4 % members, cumulative ------------
    uncertainty = []
    if members:
        firstu = dict(first)
        for m in members:
            for j in range(nH):
                p = pos[:, j]
                p = p[np.isfinite(p[:, 0])]
                if not len(p):
                    continue
                q = p + np.array([m["dx_deg"][j], m["dy_deg"][j]])
                for iy, ix in set(zip(np.floor(q[:, 1] / STEP).astype(int).tolist(),
                                      np.floor(q[:, 0] / STEP).astype(int).tolist())):
                    if (iy, ix) not in firstu or H[j] < firstu[(iy, ix)]:
                        firstu[(iy, ix)] = H[j]
        for th in ISO_HOURS:
            if th > H[-1] + 0.01 or th < 12:
                continue
            g = _cells_union({c for c, h in firstu.items() if h <= th}, STEP)
            coords = _poly_coords(g)
            if coords:
                uncertainty.append({"t_h": th, "coordinates": coords})

    # ---------------- 3. regulatory envelope + tactic zones + skimmer targets ---------------------------------
    fin = np.isfinite(pos[..., 0])
    if fin.any():
        bx0, by0 = np.nanmin(pos[..., 0]) - 0.3, np.nanmin(pos[..., 1]) - 0.3
        bx1, by1 = np.nanmax(pos[..., 0]) + 0.3, np.nanmax(pos[..., 1]) + 0.3
    else:
        bx0 = by0 = bx1 = by1 = 0.0
    gx = np.arange(math.floor(bx0 / ZS), math.ceil(bx1 / ZS))
    gy = np.arange(math.floor(by0 / ZS), math.ceil(by1 / ZS))
    envelope = {}
    if len(gx) and len(gy) and len(gx) * len(gy) < 600_000:
        GX, GY = np.meshgrid(gx, gy)
        clon, clat = (GX.ravel() + 0.5) * ZS, (GY.ravel() + 0.5) * ZS
        dkm = shore_km(clon, clat)
        dep = depth_m(clon, clat)
        water = dep > 0 if dep is not None else np.ones(len(clon), bool)
        ok_disp = water & (dkm >= DISP_NM * NM_KM) & ((dep >= DISP_DEPTH_M) if dep is not None else True)
        ok_burn = water & (dkm >= BURN_NM * NM_KM)
        envelope = {"disp": {(int(a), int(c)) for a, c, o in zip(GY.ravel(), GX.ravel(), ok_disp) if o},
                    "burn": {(int(a), int(c)) for a, c, o in zip(GY.ravel(), GX.ravel(), ok_burn) if o}}
        g = _cells_union(envelope["disp"], ZS)
        import shapely
        boxg = shapely.box(gx[0] * ZS, gy[0] * ZS, (gx[-1] + 1) * ZS, (gy[-1] + 1) * ZS)
        # the envelope's seaward edge as lines (the "3 nm / 10 m line"), without the box edges
        disp_line = []
        if g is not None:
            inner = boxg.buffer(-ZS * 1.5)
            ln = shapely.intersection(g.boundary, inner)
            for part in getattr(ln, "geoms", [ln]):
                if part.geom_type == "LineString" and part.length > ZS * 2:
                    disp_line.append([[_r(x), _r(y)] for x, y in shapely.simplify(part, ZS * 0.25).coords])
    else:
        disp_line = []

    hour_by_h = {round(h["h"]): h for h in hours}

    def time_ok(kind: str, h: float) -> bool:
        """hour-level gates from response.py (everything but the per-cell zone / volume / Grand Isle checks)."""
        x = hour_by_h.get(round(h))
        if x is None:
            return False
        return bool(x["disp_t"] if kind == "disp" else x["burn_t"] if kind == "burn" else x["mech"])

    # Census places for the ISB 3 mi population check (ISB-3); Grand Isle buffer (ISB-1)
    from .response import places as _places
    pl = _places()
    gi_km = P.GRAND_ISLE_BUFFER_MI * 1.609344
    pop_km = P.POP_BUFFER_MI * 1.609344

    def burn_cell_ok(k, occ, per_bbl, wind_to_deg) -> bool:
        lo, la = (k[1] + 0.5) * ZS, (k[0] + 0.5) * ZS
        if sum(occ.get((k[0] + a, k[1] + b_), 0) for a in (-1, 0, 1) for b_ in (-1, 0, 1)) * per_bbl < P.BURN_MIN_BBL:
            return False  # ISB-3: > 500 bbl per burn within the ~6 km patch around the cell
        dgi = _km(lo, la, *P.GRAND_ISLE)
        if dgi <= gi_km and wind_to_deg is not None:
            # "onshore" = the wind blows from the burn toward Grand Isle
            bx, by = (P.GRAND_ISLE[0] - lo) * math.cos(math.radians(la)), P.GRAND_ISLE[1] - la
            if bx * math.sin(math.radians(wind_to_deg)) + by * math.cos(math.radians(wind_to_deg)) > 0:
                return False
        if pl is not None:
            from .response import _xy as _xy_
            if pl[1].query_ball_point(_xy_([lo], [la])[0], pop_km):
                return False  # ISB-3: population centre within 3 mi (any direction: conservative)
        return True

    zones = []
    skim = []
    for j in range(0, nH, STEP_H):
        h = float(H[j])
        p = pos[:, j]
        p = p[np.isfinite(p[:, 0])]
        if not len(p):
            continue
        occ = {}
        for iy, ix in zip(np.floor(p[:, 1] / ZS).astype(int).tolist(), np.floor(p[:, 0] / ZS).astype(int).tolist()):
            occ[(iy, ix)] = occ.get((iy, ix), 0) + 1
        for kind in ("disp", "burn"):
            if not envelope or not time_ok(kind, h):
                continue
            c = {k for k in occ if k in envelope[kind]}
            if kind == "burn":
                x = hour_by_h.get(round(h)) or {}
                c = {k for k in c if burn_cell_ok(k, occ, x.get("per_bbl", 0.0), x.get("wind_to_deg"))}
            if not c:
                continue
            g = _cells_union(c, ZS)
            cs = _poly_coords(g)
            if cs:
                zones.append({"kind": kind, "h": round(h, 1), "pct": round(100 * sum(occ[k] for k in c) / len(pos), 1),
                              "coordinates": cs})
        # densest clusters of surface oil (3x3 cell neighbourhood, ~6 km), non-max suppression 12 km
        if oc["mechanical"]:
            dens = {k: sum(occ.get((k[0] + a, k[1] + bb_), 0) for a in (-1, 0, 1) for bb_ in (-1, 0, 1)) for k in occ}
            picked = []
            for k, v in sorted(dens.items(), key=lambda kv: -kv[1]):
                lo, la = (k[1] + 0.5) * ZS, (k[0] + 0.5) * ZS
                if any(_km(lo, la, q[0], q[1]) < 12 for q in picked):
                    continue
                picked.append((lo, la, v))
                if len(picked) >= 3:
                    break
            for rank_, (lo, la, v) in enumerate(picked):
                if v < max(3, 0.01 * len(pos)):
                    continue
                skim.append({"h": round(h, 1), "lon": _r(lo), "lat": _r(la), "rank": rank_ + 1,
                             "pct": round(100 * v / len(pos), 1), "ok": time_ok("mech", h)})

    # ---------------- 4. boom plan on the real ESI shoreline ----------------------------------------------------
    sb = staging_bases()
    bases = (sb or {}).get("bases", [])

    def nearest_bases(lon, lat, k=2, types=("osro_depot", "uscg_station")):
        r = nearest_equipment_base(lon, lat, k=max(k, 2), types=types)
        return r[:k]

    strand = status == "stranded"
    s_xy = end_xy[strand]
    s_t = end_t[strand]
    booms, inlets = [], []
    import shapely
    for bf in boom_first:
        s = next((x for x in shorelines if x["priority"] == bf["priority"]), None)
        if s is None:
            continue
        ks = set(s.get("_cells", []))
        x0, y0, x1, y1 = s["bbox"]
        m = (s_xy[:, 0] >= x0 - 0.06) & (s_xy[:, 0] <= x1 + 0.06) & (s_xy[:, 1] >= y0 - 0.06) & (s_xy[:, 1] <= y1 + 0.06)
        pts = s_xy[m]
        site_lon, site_lat = bf["lon"], bf["lat"]
        eta_h = bf["eta_h"]
        # current vector at the site over the arrival window (ETA -3 h .. +3 h, hourly); the strongest hour counts
        tt = t0 + (eta_h + np.arange(-3, 4)) * 3600
        uu, vv = _sample_current_uv(req, tt, np.full(len(tt), site_lon), np.full(len(tt), site_lat))
        if not np.isfinite(uu).any() and len(pts):  # site on model land: use the stranding points offshore
            q = pts[:30]
            u2, v2 = _sample_current_uv(req, np.repeat(tt, len(q)), np.tile(q[:, 0], len(tt)), np.tile(q[:, 1], len(tt)))
            with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
                __import__("warnings").simplefilter("ignore", RuntimeWarning)
                uu, vv = np.nanmean(u2.reshape(len(tt), -1), axis=1), np.nanmean(v2.reshape(len(tt), -1), axis=1)
        spd = np.hypot(uu, vv)
        if np.isfinite(spd).any():
            jm = int(np.nanargmax(spd))
            cu, cv, cur = float(uu[jm]), float(vv[jm]), float(spd[jm])
        else:
            cu = cv = cur = float("nan")

        def across(c):
            """current component perpendicular to a boom laid along line c (kt); full speed if unknown."""
            if not np.isfinite(cur):
                return float("nan")
            dx = (c[-1, 0] - c[0, 0]) * math.cos(math.radians(site_lat))
            dy = c[-1, 1] - c[0, 1]
            L_ = math.hypot(dx, dy)
            if L_ == 0:
                return cur
            return abs(cu * (-dy / L_) + cv * (dx / L_))

        def boom_kind(p_kt):
            if not np.isfinite(p_kt) or p_kt <= P.BOOM_ENTRAIN_KT:
                return "exclusion"          # MECH-1: below 0.7 kt across the boom
            if p_kt <= P.BOOM_NOHOLD_KT:
                return "deflection"         # R5: 0.7-1 kt -> deflection boom angled to a collection point
            return "nohold"                 # ISB-6: > 1 kt across -> very high loss: boom will not hold
        angle = round(math.degrees(math.asin(min(1.0, P.BOOM_ENTRAIN_KT / cur)))) if (
            np.isfinite(cur) and cur > P.BOOM_ENTRAIN_KT) else None
        lines = []
        if len(pts) and feats:
            ptree = cKDTree(_xy(pts[:, 0], pts[:, 1]))
            cand = []
            for code, rank, c in feats:
                mid = c[len(c) // 2]
                if not (x0 - 0.05 <= mid[0] <= x1 + 0.05 and y0 - 0.05 <= mid[1] <= y1 + 0.05):
                    continue
                d, _ = ptree.query(_xy(c[:, 0], c[:, 1]))
                dmin = float(d.min())
                if dmin <= 2.0:
                    cand.append((dmin, code, rank, c))
            cand.sort(key=lambda x: x[0])
            # boom only the shoreline where the oil comes ashore: clip to ~2 km around the stranding points
            near = shapely.MultiPoint(pts[:400]).buffer(0.02, quad_segs=4)
            tot = 0.0
            clipped = []
            for dmin, code, rank, c in cand:
                g = shapely.LineString(c).intersection(near)
                for part in getattr(g, "geoms", [g]):
                    if part.geom_type == "LineString" and part.length > 0.002:
                        clipped.append((dmin, code, rank, np.asarray(part.coords)[:, :2]))
            for dmin, code, rank, c in clipped:
                L = float(shapely.LineString(c).length) * 105
                if tot + L > BOOM_MAX_KM and tot > 0:
                    continue
                tot += L
                pk = across(c)
                kind = boom_kind(pk) if rank >= 7 else "beach"  # exposed beach / armored: no boom on the surf line
                lines.append({"path": [[_r(x), _r(y)] for x, y in c], "kind": kind, "esi": code,
                              "km": round(L, 2), "across_kt": None if not np.isfinite(pk) else round(pk, 2)})
                if tot >= BOOM_MAX_KM:
                    break
        kinds = {ln["kind"] for ln in lines}
        acr = [ln["across_kt"] for ln in lines if ln["kind"] != "beach" and ln["across_kt"] is not None]
        p_site = max(acr) if acr else (cur if np.isfinite(cur) else None)  # no line orientation: full speed
        main_kind = ("nohold" if "nohold" in kinds else "deflection" if "deflection" in kinds
                     else "exclusion" if "exclusion" in kinds else "beach" if "beach" in kinds
                     else boom_kind(p_site if p_site is not None else float("nan"))
                     if s["sensitivity_tier"] <= 2 else "beach")
        lt = bf.get("lead") or P.lead_time(site_lon, site_lat, None)
        ready = lt["lead_h"]
        nb = nearest_bases(site_lon, site_lat) if bases else []
        late = bool(eta_h < ready)
        for name, plat, plon in port_entrances:
            if _km(plon, plat, site_lon, site_lat) <= 25 and not any(i["name"] == name for i in inlets):
                inlets.append({"name": name, "lon": plon, "lat": plat, "eta_h": eta_h, "deploy_by_h": round(ready, 1),
                               "late": late, "priority": bf["priority"]})
        booms.append({
            "priority": bf["priority"], "name": bf["name"], "lon": site_lon, "lat": site_lat,
            "eta_h": eta_h, "eta_earliest_h": bf.get("eta_earliest_h"),
            "deploy_by_h": round(ready, 1), "mobilize_by_h": lt["en_route_h"], "lead": lt,
            "late": late, "kind": main_kind,
            "current_kt": None if not np.isfinite(cur) else round(cur, 2),
            "across_kt": None if p_site is None else round(float(p_site), 2), "boom_angle_deg": angle,
            "km": round(sum(ln["km"] for ln in lines), 1), "lines": lines, "staging": nb,
            "esi": bf["esi"], "esi_label": bf["esi_label"],
            "label": {"exclusion": "Exclusion boom",
                      "deflection": f"Deflection boom ~{angle}° to current" if angle else "Deflection boom",
                      "nohold": "Boom won't hold (>1 kt across): shoreline crews + skimmers downstream",
                      "beach": "Beach crews + inlet boom"}[main_kind],
            "rule": "R5: across-boom current <= 0.7 kt exclusion; 0.7-1 kt deflection at asin(0.7/current); "
                    "> 1 kt boom will not hold (MECH-1, ISB-6)",
        })

    # ---------------- 5. staging for on-water ops (slick centroid when the first window opens) -----------------
    staging_ops = []
    if bases:
        for kind, key in (("dispersant / ISB vessels", "disp"), ("skimmers", "mech")):
            j = next((j for j in range(nH) if time_ok(key, float(H[j])) and np.isfinite(pos[:, j, 0]).any()), None)
            if j is None:
                continue
            c = np.nanmean(pos[:, j], axis=0)
            nb = nearest_bases(float(c[0]), float(c[1]), 1, ("osro_depot",))
            if nb:
                staging_ops.append({"for": kind, "h": round(float(H[j]), 1), "lon": _r(c[0]), "lat": _r(c[1]), **{"base": nb[0]}})

    # ---------------- 6. timeline ----------------------------------------------------------------------------
    ev = []
    if booms:
        ev.append({"h": P.NOTIFY_H, "kind": "mobilize", "lon": booms[0]["lon"], "lat": booms[0]["lat"],
                   "text": "Notify NRC / USCG COTP and authorize Tier 1 mobilization (within 30 min, MECH-6)"})
    for bm in booms:
        where = bm["name"] or "shore"
        lt = bm["lead"]
        src_ = bm["staging"][0]["name"] if bm["staging"] else "the nearest base"
        if bm["late"]:
            ev.append({"h": 0.0, "kind": "late", "lon": bm["lon"], "lat": bm["lat"],
                       "text": f"{where}: oil ashore in ~{max(1, round(bm['eta_h']))} h, before Tier 1 boom from "
                               f"{src_} can be on scene (~{lt['lead_h']:.0f} h). Shoreline crews; protect the next site."})
            continue
        ev.append({"h": bm["mobilize_by_h"], "kind": "mobilize", "lon": bm["lon"], "lat": bm["lat"],
                   "text": f"Tier 1 en route: {bm['label'].lower()} for {where} from {src_}"
                           + (f" (~{bm['staging'][0]['travel_h']:.1f} h by road)" if bm["staging"] else "")})
        ev.append({"h": bm["deploy_by_h"], "kind": "deploy", "lon": bm["lon"], "lat": bm["lat"],
                   "text": f"{bm['label']} on scene at {where} ({bm['km']:.1f} km mapped, ESI {bm['esi']}; "
                           f"oil at T+{bm['eta_h']:.0f} h, earliest T+{(bm['eta_earliest_h'] or bm['eta_h']):.0f} h)"})
    for s in shorelines[:8]:
        if s["impact"] == "landfall" and s["particles_pct"] >= 0.5:
            ev.append({"h": s["eta_h"], "kind": "landfall", "lon": s["lon"], "lat": s["lat"],
                       "text": f"Oil ashore at {s['name'] or 'coast'} ({s['particles_pct']}% of particles, ESI {s['esi_most_sensitive']})"})
    for kind, label in (("disp", "Dispersant"), ("burn", "In-situ burn"), ("mech", "Skimming")):
        prev = False
        for j in range(nH):
            h = float(H[j])
            okk = time_ok(kind, h) and np.isfinite(pos[:, j, 0]).any()
            if kind != "mech" and okk:
                okk = any(z["kind"] == kind and abs(z["h"] - h) < STEP_H for z in zones)
            if okk != prev:
                c = np.nanmean(pos[:, j], axis=0) if np.isfinite(pos[:, j, 0]).any() else [np.nan, np.nan]
                if np.isfinite(c[0]):
                    ev.append({"h": round(h, 1), "kind": f"{kind}_{'open' if okk else 'close'}", "lon": _r(c[0]),
                               "lat": _r(c[1]), "text": f"{label} window {'opens' if okk else 'closes'}"
                               + ("" if okk or kind == "mech" else
                                  " (a protocol gate closed: see the tactic's gates in the outline)")})
                prev = okk
    for i_ in inlets:
        ev.append({"h": max(0.0, i_["deploy_by_h"]), "kind": "inlet", "lon": i_["lon"], "lat": i_["lat"],
                   "text": f"Boom across {i_['name']}" + (" (oil arrives before it can be in place)"
                                                          if i_["late"] else "")})
    ev.sort(key=lambda e: (e["h"], e["kind"]))

    sources = [
        {"what": "Boom: 0.7 kt across the boom, > 1 kt very high loss", "ref": "NOAA/API Characteristics of Response "
         "Strategies (2010) (MECH-1); RRT-6 ISB policy (ISB-6)", "url": P.src(["S6"])[0]["url"] if P.src(["S6"]) else ""},
        {"what": "Lead time", "ref": "33 CFR 154.1045: notify within 30 min, Tier 1 en route within 2 h, on scene 6 h "
         "(higher-volume port area, 154.1020) / 12 h elsewhere; App. C 35 mph road travel (MECH-6, R6)",
         "url": "https://www.law.cornell.edu/cfr/text/33/154.1045"},
        {"what": "Dispersant zone, burn zone, burn gates", "ref": "RRT-6 dispersant pre-approval (DISP-1/2/4/5), RRT-6 ISB "
         "policy (ISB-1..5), 1994 RRT VI ISB plan (ISB-3)", "url": P.DOC_URL},
        {"what": "Shoreline geometry", "ref": "NOAA OR&R National ESI Shoreline (GULF_ATLANTIC_ESI.gdb, 2017)",
         "url": "https://response.restoration.noaa.gov/esi_download"},
    ]
    if b is not None:
        sources.append({"what": "Bathymetry (10 m rule, distance from shore)", "ref": b["source"], "url": b["url"]})
    if sb:
        sources.append({"what": "Staging bases", "ref": sb.get("source", "data/response/staging_bases.json"),
                        "url": sb.get("url", "")})
    def circle(lon, lat, km, n=72):
        return [[_r(lon + km / (111.32 * math.cos(math.radians(lat))) * math.cos(a)), _r(lat + km / 110.57 * math.sin(a))]
                for a in np.linspace(0, 2 * math.pi, n)]
    circles = [{"kind": "disp_circle", "path": circle(lon0, lat0, P.DISP_CIRCLE_NM * NM_KM),
                "label": "10 nm dispersant planning circle (DISP-2)"}]
    if fin.any() and _km(float(np.nanmean(pos[..., 0])), float(np.nanmean(pos[..., 1])), *P.GRAND_ISLE) < 150:
        circles.append({"kind": "grand_isle", "path": circle(*P.GRAND_ISLE, P.GRAND_ISLE_BUFFER_MI * 1.609344),
                        "label": "Grand Isle 7 mi ISB buffer in onshore wind (ISB-1)"})
    return {
        "version": "layer-v2",
        "bands": [lab for _, lab in ARRIVAL_BANDS],
        "shore_source": shore_src,
        "shore": segs,
        "isochrones": isochrones,
        "uncertainty": uncertainty,
        "circles": circles,
        "booms": booms,
        "inlets": inlets,
        "zones": zones,
        "zone_step_h": STEP_H,
        "dispersant_line": disp_line,
        "skimmer_targets": skim,
        "staging_bases": [{k: x[k] for k in ("name", "type", "lon", "lat")} for x in bases],
        "staging_ops": staging_ops,
        "timeline": ev,
        "assumptions": {
            "notify_h": P.NOTIFY_H, "en_route_h": P.NOTIFY_H + P.MOBILIZE_H, "road_mph": P.ROAD_MPH,
            "tier1_hvp_h": P.TIER1_HVP_H, "tier1_other_h": P.TIER1_OTHER_H,
            "boom_entrain_kt": P.BOOM_ENTRAIN_KT, "boom_nohold_kt": P.BOOM_NOHOLD_KT, "boom_max_km": BOOM_MAX_KM,
            "disp_nm": DISP_NM, "disp_depth_m": DISP_DEPTH_M if b is not None else None, "burn_nm": BURN_NM,
            "burn_min_bbl": P.BURN_MIN_BBL,
        },
        "rules": [
            "Boom on scene = notify 0.5 h + Tier 1 en route by 2.5 h + road travel from the nearest equipment base at "
            f"{P.ROAD_MPH:.0f} mph, never sooner than the USCG Tier 1 time: {P.TIER1_HVP_H:.0f} h in a higher-volume port "
            f"area (33 CFR 154.1020: Galveston Bay/Houston Ship Channel, Sabine-Neches, Lake Charles, Corpus Christi, "
            f"Mississippi River SW Pass-Baton Rouge, LOOP, Pascagoula), {P.TIER1_OTHER_H:.0f} h elsewhere (R6, MECH-6). "
            "If the oil arrives first: shoreline crews and protect the next site (WIN-3).",
            "Boom type from the forecast current ACROSS each boom line (component perpendicular to the ESI shoreline "
            f"segment, strongest hour within +/-3 h of arrival): <= {P.BOOM_ENTRAIN_KT} kt exclusion boom (MECH-1; "
            f"{P.BOOM_ENTRAIN_KT_RRT6} kt in RRT-6, ISB-6); {P.BOOM_ENTRAIN_KT}-{P.BOOM_NOHOLD_KT:.0f} kt deflection boom "
            f"at asin({P.BOOM_ENTRAIN_KT}/current) to a collection point; > {P.BOOM_NOHOLD_KT:.0f} kt boom will not "
            "hold (ISB-6: very high loss rate): shoreline crews + skimmers downstream (R5). ESI 1-6 exposed beaches: no "
            "boom on the surf line, beach crews + boom across nearby inlets.",
            f"Dispersant zone = surface oil while every hour-level gate passes (API 17-45 and pour point below sea "
            f"temperature DISP-5; emulsion <= 25 % WIN-4; wind <= 25 kt and daylight DISP-4) AND >= {DISP_NM:.0f} nm "
            f"from shore" + (f" AND >= {DISP_DEPTH_M:.0f} m deep (ETOPO1)" if b is not None else
                               " (depth NOT checked: no bathymetry)")
            + " (DISP-1, 'whichever is farthest'). Closer in: RRT-6 concurrence case by case (DISP-3). The 10 nm circle "
            "around the source is RRT-6's planning circle (DISP-2). Visibility/ceiling are not forecast: check.",
            f"In-situ burn zone = ~2 km cells >= {BURN_NM:.0f} nm offshore (ISB-1) with > {P.BURN_MIN_BBL:.0f} bbl of "
            "surface oil in the ~6 km patch around them (ISB-3), outside the Grand Isle 7 mi buffer when the wind blows "
            "toward it (ISB-1) and > 3 mi from any Census place (ISB-3), while emulsion <= 25 %, wind <= 21.7 kt, "
            "current <= 0.75 kt and sun > 15° (ISB-2/4/5). Reef/bank exclusion areas are not in our data.",
            "Uncertainty outline (dashed) = where oil could be if the wind drift were 1 % or 4 % instead of the "
            "model's value (GNOME windage range, FCST-4): best guess + uncertainty, like NOAA's minimum-regret area.",
            "Skimmer targets = the 3 densest clusters of surface particles (~6 km neighbourhood) every 3 h.",
        ],
        "notes": notes,
        "sources": sources,
    }
