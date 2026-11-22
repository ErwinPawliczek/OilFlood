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

# --- planning assumptions (stated in the JSON + docs/RESPONSE.md) ---------------------------------------
DEPLOY_LEAD_H = 2.0     # boom in the water and anchored this long before the oil's predicted arrival
LOAD_H = 1.0            # call-out + loading at the staging base before travel
TRAVEL_KMH = 30.0       # average transit (truck on coastal roads or response vessel ~16 kt)
DETOUR = 1.3            # straight-line distance -> route distance
BOOM_FAIL_KT = 0.7      # boom losses start above ~0.7 kt current normal to the boom
BOOM_MAX_KM = 8.0       # boom drawn per site (display cap; the real length comes from the GRP / survey)
DISP_NM, DISP_DEPTH_M = 3.0, 10.0   # dispersant pre-authorization envelope (RRT 6 / RRT 4)
BURN_NM = 3.0           # in-situ burn: >= 3 nm from shore (RRT 6 ISB pre-authorization)
STEP_H = 3              # zones / skimmer targets every 3 h
ARRIVAL_BANDS = [(12, "<12 h"), (24, "12-24 h"), (48, "24-48 h"), (72, "48-72 h"), (1e9, ">72 h")]
ISO_HOURS = (6, 12, 24, 48, 72)


def _band(h: float) -> int:
    for i, (lim, _) in enumerate(ARRIVAL_BANDS):
        if h < lim:
            return i
    return len(ARRIVAL_BANDS) - 1


def _r(x, n=4):
    return round(float(x), n)


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


def _sample_current_kt(req: dict, t: np.ndarray, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Current speed (kt) from the run's own current forcing at points/times; NaN where unknown."""
    from .ourmodel.grid import Grid
    out = np.full(len(lon), np.nan)
    if not len(lon):
        return out
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
            out = np.hypot(u, v) * KT
            break
        except Exception:  # noqa: BLE001
            continue
    return out


# --- main ---------------------------------------------------------------------------------------------------

def build_layer(*, t0: float, hrs: np.ndarray, pos: np.ndarray, status: np.ndarray, end_xy: np.ndarray,
                end_t: np.ndarray, hit: dict, stretch_cells: list[list[int]], shorelines: list[dict],
                boom_first: list[dict], hours: list[dict], oc: dict, req: dict, port_entrances: list) -> dict:
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
        if nverts > 60000:
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

    # ---------------- 3. regulatory envelope + tactic zones + skimmer targets ---------------------------------
    ZS = 0.02  # ~2 km zone grid
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
        x = hour_by_h.get(round(h))
        if x is None or x["wind_kt"] is None:
            return False
        if kind == "disp":
            lim = oc["dispersant"]
            return bool(lim and h <= lim and 4.0 <= x["wind_kt"] <= 25.0 and x["daylight"])
        if kind == "burn":
            lim = oc["burn"]
            return bool(lim is not None and h <= lim and x["wind_kt"] <= 20.0 and x["daylight"])
        return bool(oc["mechanical"] and x["wind_kt"] <= 20.0 and x["daylight"])

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
            # burn needs thick, collectable oil: only cells with >= 0.2 % of the particles
            if kind == "burn":
                c = {k for k in c if occ[k] >= max(2, 0.002 * len(pos))}
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

    # boom / skimmers come from equipment depots (OSRO, state) and USCG small-boat stations, not from offices
    # or air stations; those stay on the map as context.
    EQUIP = ("osro_depot", "uscg_station")

    def nearest_bases(lon, lat, k=2, types=EQUIP):
        ds = sorted(((_km(lon, lat, x["lon"], x["lat"]), x) for x in bases if x["type"] in types), key=lambda t: t[0])
        return [{"name": x["name"], "type": x["type"], "lon": x["lon"], "lat": x["lat"], "km": round(d, 1),
                 "travel_h": round(d * DETOUR / TRAVEL_KMH, 1)} for d, x in ds[:k]]

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
        cur = _sample_current_kt(req, np.array([t0 + eta_h * 3600]), np.array([site_lon]), np.array([site_lat]))[0]
        if not np.isfinite(cur) and len(pts):
            cc = _sample_current_kt(req, np.full(len(pts[:50]), t0 + eta_h * 3600), pts[:50, 0], pts[:50, 1])
            cur = float(np.nanmax(cc)) if np.isfinite(cc).any() else float("nan")
        strong = bool(np.isfinite(cur) and cur > BOOM_FAIL_KT)
        angle = round(math.degrees(math.asin(min(1.0, BOOM_FAIL_KT / cur)))) if strong else None
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
                if rank >= 7:
                    kind = "deflection" if strong else "exclusion"
                else:
                    kind = "beach"  # exposed beach / armored: booming the surf line does not work
                lines.append({"path": [[_r(x), _r(y)] for x, y in c], "kind": kind, "esi": code,
                              "km": round(L, 2)})
                if tot >= BOOM_MAX_KM:
                    break
        kinds = {ln["kind"] for ln in lines}
        main_kind = ("deflection" if "deflection" in kinds else "exclusion" if "exclusion" in kinds
                     else "beach" if "beach" in kinds else ("deflection" if strong else "exclusion")
                     if s["sensitivity_tier"] <= 2 else "beach")
        deploy_by = eta_h - DEPLOY_LEAD_H
        nb = nearest_bases(site_lon, site_lat) if bases else []
        mob = deploy_by - (nb[0]["travel_h"] if nb else 0) - LOAD_H
        for name, plat, plon in port_entrances:
            if _km(plon, plat, site_lon, site_lat) <= 25 and not any(i["name"] == name for i in inlets):
                inlets.append({"name": name, "lon": plon, "lat": plat, "eta_h": eta_h,
                               "deploy_by_h": round(deploy_by, 1), "priority": bf["priority"]})
        booms.append({
            "priority": bf["priority"], "name": bf["name"], "lon": site_lon, "lat": site_lat,
            "eta_h": eta_h, "deploy_by_h": round(deploy_by, 1), "mobilize_by_h": round(mob, 1),
            "late": bool(mob < 0), "kind": main_kind,
            "current_kt": None if not np.isfinite(cur) else round(float(cur), 2), "boom_angle_deg": angle,
            "km": round(sum(ln["km"] for ln in lines), 1), "lines": lines, "staging": nb,
            "esi": bf["esi"], "esi_label": bf["esi_label"],
            "label": {"exclusion": "Exclusion boom", "deflection": f"Deflection boom ~{angle}° to current" if angle else
                      "Deflection boom", "beach": "Beach crews + inlet boom"}[main_kind],
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
    for bm in booms:
        where = bm["name"] or "shore"
        if bm["late"]:
            ev.append({"h": 0.0, "kind": "late", "lon": bm["lon"], "lat": bm["lat"],
                       "text": f"{where}: oil ashore in ~{max(1, round(bm['eta_h']))} h, before boom from "
                               f"{bm['staging'][0]['name'] if bm['staging'] else 'the nearest base'} can be in place. "
                               "Shoreline crews; protect the next site."})
        else:
            ev.append({"h": max(0.0, bm["mobilize_by_h"]), "kind": "mobilize", "lon": bm["lon"], "lat": bm["lat"],
                       "text": f"Mobilize {bm['label'].lower()} for {where}"
                               + (f" from {bm['staging'][0]['name']} (~{bm['staging'][0]['travel_h']:.1f} h)" if bm["staging"] else "")})
        if bm["late"] and bm["deploy_by_h"] < 0:
            continue
        ev.append({"h": max(0.0, bm["deploy_by_h"]), "kind": "deploy", "lon": bm["lon"], "lat": bm["lat"],
                   "text": f"{bm['label']} in place at {where} ({bm['km']:.1f} km mapped, ESI {bm['esi']})"})
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
                               + ("" if okk or kind == "mech" else " (oil too weathered, wind, dark or outside the envelope)")})
                prev = okk
    for i_ in inlets:
        ev.append({"h": max(0.0, i_["deploy_by_h"]), "kind": "inlet", "lon": i_["lon"], "lat": i_["lat"],
                   "text": f"Exclusion boom across {i_['name']}" + (" (oil arrives before it can be in place)"
                                                                     if i_["deploy_by_h"] < 0 else "")})
    ev.sort(key=lambda e: (e["h"], e["kind"]))

    sources = [
        {"what": "Boom types, 0.7 kt failure speed", "ref": "NOAA ORR Characteristics of Response Strategies (2010) "
         "p. 39; ITOPF TIP 03 Use of Booms (2011): boom angle 90/45/28/20 deg at 0.7/1/1.5/2 kt",
         "url": "https://www.itopf.org/fileadmin/uploads/itopf/data/Documents/TIPS_TAPS_new/"
                "TIP_3_Use_of_Booms_in_Oil_Pollution_Response.pdf"},
        {"what": "Response time context", "ref": "33 CFR 155.1050 (OSRO tiers 12/36/60 h in higher volume port areas); "
         "33 CFR 154.1045 (facility: 1,000 ft of boom within 1 h)", "url": "https://www.ecfr.gov/current/title-33/section-155.1050"},
        {"what": "Shoreline geometry", "ref": "NOAA OR&R National ESI Shoreline (GULF_ATLANTIC_ESI.gdb, 2017)",
         "url": "https://response.restoration.noaa.gov/esi_download"},
    ]
    if b is not None:
        sources.append({"what": "Bathymetry (10 m rule, distance from shore)", "ref": b["source"], "url": b["url"]})
    if sb:
        sources.append({"what": "Staging bases", "ref": sb.get("source", "data/response/staging_bases.json"),
                        "url": sb.get("url", "")})
    return {
        "version": "layer-v1",
        "bands": [lab for _, lab in ARRIVAL_BANDS],
        "shore_source": shore_src,
        "shore": segs,
        "isochrones": isochrones,
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
            "deploy_lead_h": DEPLOY_LEAD_H, "load_h": LOAD_H, "travel_kmh": TRAVEL_KMH, "detour": DETOUR,
            "boom_fail_kt": BOOM_FAIL_KT, "boom_max_km": BOOM_MAX_KM, "disp_nm": DISP_NM,
            "disp_depth_m": DISP_DEPTH_M if b is not None else None, "burn_nm": BURN_NM,
        },
        "rules": [
            f"Deploy-by = predicted arrival minus {DEPLOY_LEAD_H:.0f} h (boom anchored and tended before oil arrives); "
            f"mobilize-by = deploy-by minus travel from the nearest staging base ({DETOUR}x straight line at "
            f"{TRAVEL_KMH:.0f} km/h) minus {LOAD_H:.0f} h loading.",
            "Boom type by shoreline: ESI 7-10 (flats, sheltered shores, marsh) -> exclusion boom; if the forecast "
            f"current at the site exceeds ~{BOOM_FAIL_KT} kt -> deflection boom angled to the current "
            f"(angle = asin({BOOM_FAIL_KT} / current) so the normal component stays below ~{BOOM_FAIL_KT} kt); "
            "ESI 1-6 exposed beaches -> no boom on the surf line, beach crews + exclusion boom across nearby inlets.",
            f"Dispersant zone = surface oil while the dispersant window is open (oil age, wind 4-25 kt, daylight) "
            f"AND >= {DISP_NM:.0f} nm from shore"
            + (f" AND >= {DISP_DEPTH_M:.0f} m deep (ETOPO1)" if b is not None else " (depth NOT checked: no bathymetry)")
            + ": RRT-6 pre-approval zone = from the 10 m isobath or 3 nmi, whichever is farther from shore "
            "(RRT-6 Dispersant Pre-Approval Guidelines v4.0); RRT 4 green zone >= 3 mi and >= 10 m. Closer in needs "
            "RRT consultation (RRT-6 2005 near-shore expedited process). The Flower Garden Banks NMS has its own RRT-6 "
            "guidance (not mapped here).",
            f"In-situ burn zone = collectable oil (>= 0.2 % of particles per ~2 km cell) while the burn window is "
            f"open and >= {BURN_NM:.0f} nm from shore (RRT-6 offshore ISB policy: seaward of 3 miles; the 1994 plan "
            "excludes some reefs and an area off Grand Isle, not mapped here).",
            "Skimmer targets = the 3 densest clusters of surface particles (~6 km neighbourhood) every 3 h.",
        ],
        "notes": notes,
        "sources": sources,
    }
