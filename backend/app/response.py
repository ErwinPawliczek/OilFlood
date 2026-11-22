"""First-responder action outline (PLAN P6, docs/RESPONSE.md).

Rule-based: predicted tracks (landfall, time-to-shore, oil budget) + NOAA ESI shoreline sensitivity
(data/esi/gulf_esi_cells.json) + the run's own forecast winds/currents + oil type/volume -> structured
outline (priority shorelines, where to boom first, dispersant / in-situ burn / skimming windows,
closures) with cited precedents from the spill response logs (data/spills/catalog.json).
An optional LLM layer (app/briefing.py) only rewrites this JSON as plain English.

NOT operational guidance: a planning aid built on one model run.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from . import config, oils

VERSION = "resp-v2"  # v2: + "layer" (map geometry, T21); windows use ETOPO1 depth + coast distance
KT = 1.943844  # m/s -> knots
NM_KM = 1.852

DISCLAIMER = ("Planning aid generated automatically from one model forecast. Not operational guidance: "
              "the Federal On-Scene Coordinator / Unified Command, the Area Contingency Plan and the "
              "Regional Response Team decide every response action.")

# --- rule tables (sources: docs/RESPONSE.md) -----------------------------------------------------------

# Oil groups, roughly ITOPF/NOAA persistence groups. Windows are typical upper bounds after release.
OIL_CLASSES = {
    "gasoline": {"label": "gasoline (non-persistent, flammable)", "group": 1, "persistent": False,
                 "dispersant": None, "burn": None, "mechanical": False},
    "condensate": {"label": "condensate (non-persistent)", "group": 1, "persistent": False,
                   "dispersant": None, "burn": None, "mechanical": False},
    "jet": {"label": "jet fuel (light refined, non-persistent)", "group": 1, "persistent": False,
            "dispersant": None, "burn": None, "mechanical": False},
    "diesel": {"label": "diesel / marine gas oil (light refined)", "group": 2, "persistent": False,
               "dispersant": None, "burn": 24, "mechanical": True},
    "crude_light": {"label": "light crude", "group": 2, "persistent": True,
                    "dispersant": 48, "burn": 48, "mechanical": True},
    "crude_medium": {"label": "medium crude", "group": 3, "persistent": True,
                     "dispersant": 36, "burn": 36, "mechanical": True},
    "crude_heavy": {"label": "heavy crude", "group": 4, "persistent": True,
                    "dispersant": 12, "burn": 24, "mechanical": True},
    "heavy_fuel": {"label": "heavy fuel oil (IFO / No. 6)", "group": 4, "persistent": True,
                   "dispersant": 0, "burn": 12, "mechanical": True},
}
OIL_ID_CLASS = {"crude_light": "crude_light", "crude_medium": "crude_medium", "crude_heavy": "crude_heavy",
                "ifo380": "heavy_fuel", "fuel_no6": "heavy_fuel", "diesel": "diesel", "jet": "jet",
                "gasoline": "gasoline", "condensate": "condensate"}

# Operating limits (kt / ft / nm), NOAA ORR + USCG/RRT guidance (docs/RESPONSE.md).
WIND_DISP_MIN, WIND_DISP_MAX = 4.0, 25.0
WIND_BURN_MAX = 20.0
WIND_MECH_MAX = 20.0
BOOM_CURRENT_KT = 0.75  # containment boom entrains oil above ~0.7-1 kt relative current
PREAUTH_NM = 3.0        # RRT 4/6 dispersant pre-authorization: >= 3 nm from shore and >= 10 m depth
PREAUTH_DEPTH_M = 10.0  # checked against ETOPO1 (data/cache/bathy) when cached
STAGE_H = 6.0           # assumed hours to get boom on site after notification (planning assumption)
LANDFALL_KM = 6.0       # stranded particle -> ESI cell match radius
APPROACH_KM = 10.0      # oil within this distance of a shoreline cell = "threatened"
STRETCH_KM = 30.0       # shoreline stretches reported as one priority item

# Shoreline treatment by ESI class (NOAA "Shoreline Assessment Manual" / ESI guidelines, summarised).
SHORE_TACTICS = [
    (10, "Wetlands (marsh/swamp/mangrove): exclusion boom at inlets and marsh edges first; no foot traffic "
         "or vegetation cutting; low-pressure flushing, sorbents and vacuum from boats; often natural recovery."),
    (9, "Sheltered flats / vegetated banks: boom off; avoid trampling and sediment mixing; sorbents, vacuum; "
        "natural recovery preferred."),
    (8, "Sheltered rocky shores, riprap and structures: boom off; flushing (low then high pressure), "
        "sorbents, manual wiping/removal of oiled debris."),
    (7, "Exposed tidal flats: oil usually refloats on the tide; recover from the water edge, avoid driving "
        "or walking on the flat."),
    (6, "Gravel / riprap: flushing and sorbents; heavy oil may need manual removal."),
    (3, "Sand beaches: hard to boom on exposed surf; plan manual/mechanical removal of oiled sand and tar "
        "balls (minimise clean sand removal); protect the inlets behind them instead."),
    (1, "Armored / man-made exposed shores: wave action cleans; flushing where amenity use requires."),
]

STATE_AGENCY = {"TX": "Texas GLO (lead state trustee) + TPWD", "LA": "LOSCO + LDWF / LDH (oyster beds)",
                "MS": "MDEQ + MDMR", "AL": "ADEM + ADCNR", "FL": "FDEP + FWC"}

# Navigation choke points (approximate channel-entrance coordinates, public charts).
PORT_ENTRANCES = [
    ("Brazos Santiago Pass (Brownsville)", 26.07, -97.15), ("Aransas Pass (Corpus Christi)", 27.83, -97.04),
    ("Matagorda Ship Channel", 28.42, -96.33), ("Freeport entrance", 28.93, -95.29),
    ("Bolivar Roads (Galveston / Houston Ship Channel)", 29.34, -94.72), ("Sabine Pass", 29.68, -93.83),
    ("Calcasieu Pass (Lake Charles)", 29.76, -93.34), ("Belle Pass (Port Fourchon)", 29.08, -90.22),
    ("Southwest Pass (Mississippi River)", 28.92, -89.43), ("South Pass (Mississippi River)", 28.99, -89.13),
    ("Ship Island Pass (Gulfport)", 30.20, -89.02), ("Horn Island Pass (Pascagoula)", 30.21, -88.49),
    ("Mobile Bay entrance", 30.23, -88.03), ("Pensacola Pass", 30.32, -87.31),
    ("St. Andrew Pass (Panama City)", 30.12, -85.73), ("Tampa Bay entrance", 27.60, -82.76),
    ("Key West main ship channel", 24.50, -81.80),
]


# --- static data ---------------------------------------------------------------------------------

def _data_file(rel: str) -> Path | None:
    for d in [*config.DATA_DIRS, config.REPO_ROOT / "data"]:
        p = d / rel
        if p.exists():
            return p
    return None


@lru_cache(maxsize=1)
def esi():
    p = _data_file("esi/gulf_esi_cells.json")
    if p is None:
        raise FileNotFoundError("data/esi/gulf_esi_cells.json missing (run data/build_esi.py)")
    d = json.loads(p.read_text())
    cells = d["cells"]
    lat = np.array([c[0] for c in cells])
    lon = np.array([c[1] for c in cells])
    tree = cKDTree(np.c_[lon * 111.32 * math.cos(math.radians(27.5)), lat * 110.57])
    return d, lat, lon, tree


@lru_cache(maxsize=1)
def places():
    p = _data_file("esi/gulf_places.json")
    if p is None:
        return None
    d = json.loads(p.read_text())["places"]
    lat = np.array([x[0] for x in d])
    lon = np.array([x[1] for x in d])
    return d, cKDTree(np.c_[lon * 111.32 * math.cos(math.radians(27.5)), lat * 110.57])


def _xy(lon, lat):
    return np.c_[np.asarray(lon) * 111.32 * math.cos(math.radians(27.5)), np.asarray(lat) * 110.57]


def esi_rank(code: str) -> float:
    import re
    m = re.match(r"\s*(\d+)([A-Z]?)", code or "")
    if not m:
        return 0.0
    return int(m[1]) + ((ord(m[2]) - 64) / 100 if m[2] else 0.0)


def place_name(lon: float, lat: float) -> str | None:
    pl = places()
    if pl is None:
        return None
    d, tree = pl
    dist, i = tree.query(_xy([lon], [lat])[0])
    p = d[int(i)]
    return f"{p[2]}, {p[3]}" + ("" if dist < 8 else f" ({dist:.0f} km)")


def _compass(deg: float) -> str:
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW",
            "NNW"][int(((deg % 360) + 11.25) // 22.5) % 16]


def sun_elevation(t: float, lat: float, lon: float) -> float:
    """Solar elevation (deg), NOAA simplified algorithm; good to ~1 deg."""
    dt = datetime.fromtimestamp(t, timezone.utc)
    doy = dt.timetuple().tm_yday
    hr = dt.hour + dt.minute / 60
    g = 2 * math.pi / 365 * (doy - 1 + (hr - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g)
                    - 0.040849 * math.sin(2 * g))
    tst = hr * 60 + eqt + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    s = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    return math.degrees(math.asin(max(-1.0, min(1.0, s))))


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def oil_class(oil_type: str | None) -> str:
    """ADIOS / catalog oil name or panel id -> OIL_CLASSES key."""
    s = (oil_type or "").lower()
    if s in OIL_ID_CLASS:
        return OIL_ID_CLASS[s]
    for o in oils.OILS:
        if o["adios"].lower() == s:
            return OIL_ID_CLASS[o["id"]]
    if any(k in s for k in ("ifo", "no.6", "no. 6", "#6", "bunker", "fuel oil", "slurry")):
        return "heavy_fuel"
    if "diesel" in s or "gas oil" in s:
        return "diesel"
    if "gasoline" in s:
        return "gasoline"
    if "jet" in s or "kerosene" in s:
        return "jet"
    if "condensate" in s:
        return "condensate"
    if "heavy" in s:
        return "crude_heavy"
    if any(k in s for k in ("light", "sweet", "mc252", "nigerian", "palanca")):
        return "crude_light"
    return "crude_medium"


# --- forcing along the slick ------------------------------------------------------------------------

def _env_series(req: dict, times: np.ndarray, cen: np.ndarray) -> dict:
    """Wind and current (from the run's own forcing files) at the slick centroid every hour."""
    from .ourmodel.grid import Grid
    out = {"wind_ms": [None] * len(times), "wind_dir_to": [None] * len(times), "current_ms": [None] * len(times),
           "source": []}
    t0 = datetime.fromtimestamp(float(times[0]), timezone.utc).replace(tzinfo=None)
    t1 = datetime.fromtimestamp(float(times[-1]), timezone.utc).replace(tzinfo=None)
    ok = np.isfinite(cen[:, 0])
    if not ok.any():
        return out
    bb = (float(np.nanmin(cen[:, 0])), float(np.nanmin(cen[:, 1])),
          float(np.nanmax(cen[:, 0])), float(np.nanmax(cen[:, 1])))
    for f in req.get("forcing_files", []):
        kind = "wind" if "wind" in f["kinds"] else "current" if "current" in f["kinds"] else None
        if kind is None or not Path(f["path"]).exists():
            continue
        try:
            g = Grid(f["path"], kind, bb, t0, t1, pad_deg=0.5)
            with np.errstate(invalid="ignore"):
                u, v = g.sample(times, np.nan_to_num(cen[:, 0], nan=-1e3), np.nan_to_num(cen[:, 1], nan=-1e3))
        except Exception:  # noqa: BLE001 - forcing is optional context here
            continue
        out["source"].append(Path(f["path"]).parent.name + "/" + Path(f["path"]).name)
        sp = np.hypot(u, v)
        if kind == "wind":
            out["wind_ms"] = [None if not np.isfinite(x) else round(float(x), 2) for x in sp]
            out["wind_dir_to"] = [None if not np.isfinite(a) else round(float(np.degrees(np.arctan2(a, b)) % 360))
                                  for a, b in zip(u, v)]
        else:
            out["current_ms"] = [None if not np.isfinite(x) else round(float(x), 3) for x in sp]
    return out


def _windows(hours: list[dict], ok_key: str) -> list[dict]:
    """Contiguous runs of hours where hours[i][ok_key] is True -> [{start, end, hours}]."""
    out, cur = [], None
    for h in hours:
        if h[ok_key]:
            if cur is None:
                cur = {"start": h["t"], "end": h["t"], "from_h": h["h"], "to_h": h["h"]}
            else:
                cur["end"], cur["to_h"] = h["t"], h["h"]
        elif cur is not None:
            out.append(cur)
            cur = None
    if cur is not None:
        out.append(cur)
    for w in out:
        w["hours"] = round(w["to_h"] - w["from_h"] + 1, 1)
        w["start"], w["end"] = _iso(w["start"]), _iso(w["end"] + 3600)
    return out


# --- precedents --------------------------------------------------------------------------------------

def _catalog() -> list[dict]:
    p = _data_file("spills/catalog.json")
    if p is None:
        return []
    d = json.loads(p.read_text())
    return d["spills"] if isinstance(d, dict) and "spills" in d else d


def precedents(ocls: str, vol_bbl: float, lon: float, lat: float, cats: list[str], exclude: str | None = None,
               per_cat: int = 2) -> dict:
    """Most similar catalog spills (oil group, size, distance) and, for each recommended tactic, the
    cited actions they actually took (verbatim quote + IncidentNews URL)."""
    g = OIL_CLASSES[ocls]["group"]
    ranked = []
    for s in _catalog():
        if s.get("id") == exclude:
            continue
        sc = oil_class(s.get("oil_type"))
        d_km = math.hypot((s["lon"] - lon) * 111.32 * math.cos(math.radians(lat)), (s["lat"] - lat) * 110.57)
        v = float(s.get("volume_bbl") or 0)
        score = (2.0 if sc == ocls else 1.0 if abs(OIL_CLASSES[sc]["group"] - g) <= 1 else 0.0)
        if v > 0 and vol_bbl > 0:
            score += max(0.0, 1.5 - abs(math.log10(v / vol_bbl)) / 1.5)
        score += max(0.0, 1.0 - d_km / 400)
        ranked.append((round(score, 2), round(d_km), s, sc))
    ranked.sort(key=lambda x: -x[0])
    spills = [{"id": s["id"], "name": s["name"], "year": str(s.get("date_start", ""))[:4],
               "oil": s.get("oil_type"), "oil_class": sc, "volume_bbl": s.get("volume_bbl"), "distance_km": d,
               "similarity": sc_, "outcome": (s.get("response_summary") or {}).get("outcome"),
               "src": (s.get("source_urls") or [None])[0]}
              for sc_, d, s, sc in ranked[:4]]
    by_cat: dict[str, list] = {}
    for c in cats:
        acts = []
        for sc_, d, s, _ in ranked:
            for a in s.get("response", []):
                if a.get("category") == c and a.get("src"):
                    acts.append({"spill": s["name"], "spill_id": s["id"], "date": a.get("date"),
                                 "action": a.get("action"), "quote": a.get("quote"), "src": a["src"]})
                    break  # first (earliest) matching action per spill
            if len(acts) >= per_cat:
                break
        if acts:
            by_cat[c] = acts
    return {"similar_spills": spills, "by_tactic": by_cat,
            "source": "data/spills/catalog.json (NOAA IncidentNews response logs, verbatim quotes)"}


# --- main ---------------------------------------------------------------------------------------------

def build(tracks: dict, req: dict, run_id: str | None = None, spill_id: str | None = None) -> dict:
    times = np.array(tracks["times"], dtype=float)
    t0 = float(times[0])
    parts = tracks["particles"]
    n = len(parts)
    meta = tracks.get("meta") or {}
    oil_name = meta.get("oil_type") or req.get("oil_type")
    ocls = oil_class(oil_name)
    oc = OIL_CLASSES[ocls]
    vol_bbl = float(req.get("volume_bbl") or meta.get("volume_bbl") or (req.get("volume_m3", 0) / 0.158987))
    lon0, lat0 = float(req["lon"]), float(req["lat"])
    dur_h = (times[-1] - t0) / 3600

    # Budget fractions at 0/24/48/72 h + end.
    bud = tracks.get("budget") or []
    m0 = sum(bud[0].get(k, 0) for k in ("surface", "evaporated", "dispersed", "stranded")) if bud else 0
    budget = []
    if m0 > 0:
        for hh in sorted({h for h in (6, 12, 24, 48, 72) if h <= dur_h + 0.01} | {round(dur_h)}):
            b = min(bud, key=lambda x: abs(x["t"] - (t0 + hh * 3600)))
            budget.append({"h": hh, **{k: round(100 * b.get(k, 0) / m0, 1)
                                       for k in ("surface", "evaporated", "dispersed", "stranded")}})

    # Hourly particle positions (surface oil) for centroid and approach checks.
    hrs = np.arange(t0, times[-1] + 1, 3600.0)
    pos = np.full((n, len(hrs), 2), np.nan)
    end_t = np.zeros(n)
    end_xy = np.zeros((n, 2))
    status = []
    for i, p in enumerate(parts):
        ts = np.asarray(p["timestamps"], float)
        xy = np.asarray(p["path"], float)
        status.append(p["status_end"])
        end_t[i], end_xy[i] = ts[-1], xy[-1]
        if len(ts) == 0:
            continue
        idx = np.searchsorted(ts, hrs, side="right") - 1
        alive = (idx >= 0) & (hrs <= ts[-1])
        pos[i, alive] = xy[idx[alive]]
    status = np.array(status)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # hours with no oil left -> NaN centroid
        cen = np.nanmean(pos, axis=0)  # (hours, 2) slick centroid

    # --- shoreline impacts -------------------------------------------------------------------------------
    ed, elat, elon, etree = esi()
    cells = ed["cells"]
    labels = ed["labels"]
    atlases = ed["atlases"]
    a_state = ed["atlas_state"]
    hit = {}  # cell index -> dict
    strand = status == "stranded"
    if strand.any():
        dist, ci = etree.query(_xy(end_xy[strand, 0], end_xy[strand, 1]), distance_upper_bound=LANDFALL_KM)
        for k, (dd, c, tt) in enumerate(zip(dist, ci, end_t[strand])):
            if not np.isfinite(dd):
                continue
            h = hit.setdefault(int(c), {"n": 0, "eta": tt, "approach": None})
            h["n"] += 1
            h["eta"] = min(h["eta"], tt)
    # Approach: surface oil within APPROACH_KM of a shoreline cell (hourly).
    flat = pos.reshape(-1, 2)
    fin = np.isfinite(flat[:, 0])
    if fin.any():
        tt_flat = np.broadcast_to(hrs, (n, len(hrs))).reshape(-1)[fin]
        dd, near = etree.query(_xy(flat[fin, 0], flat[fin, 1]), distance_upper_bound=APPROACH_KM)
        for j in np.nonzero(np.isfinite(dd))[0]:  # nearest shoreline cell only (outer coast, not the bay behind it)
            h = hit.setdefault(int(near[j]), {"n": 0, "eta": None, "approach": None})
            if h["approach"] is None or tt_flat[j] < h["approach"]:
                h["approach"] = tt_flat[j]

    # Stranded mass per particle (end-of-run stranded mass shared over stranded particles).
    strand_kg = bud[-1].get("stranded", 0) if bud else 0
    kg_per = strand_kg / max(1, int(strand.sum()))

    # Group hit cells into shoreline stretches of <= ~STRETCH_KM: seeds = most-oiled cells first.
    keys = sorted(hit, key=lambda k: (-hit[k]["n"], hit[k]["eta"] or hit[k]["approach"] or 0))
    groups: dict[int, list[int]] = {}
    if keys:
        kxy = _xy(elon[keys], elat[keys])
        seeds: list[int] = []
        for i, k in enumerate(keys):
            if seeds:
                d = np.hypot(*(kxy[seeds] - kxy[i]).T)
                j = int(np.argmin(d))
                if d[j] <= STRETCH_KM / 2:
                    groups[seeds[j]].append(k)
                    continue
            seeds.append(i)
            groups[i] = [k]

    shorelines = []
    for g, ks in groups.items():
        n_str = sum(hit[k]["n"] for k in ks)
        etas = [hit[k]["eta"] for k in ks if hit[k]["n"]]
        apps = [hit[k]["approach"] for k in ks if hit[k]["approach"] is not None]
        first = min(etas) if etas else min(apps)
        km = sum(cells[k][2] for k in ks)
        km10 = sum(cells[k][5] for k in ks)
        cmax = max((cells[k][3] for k in ks), key=esi_rank)
        dom = Counter()
        for k in ks:
            dom[cells[k][4]] += cells[k][2]
        cdom = dom.most_common(1)[0][0]
        hit_ks = [k for k in ks if hit[k]["n"]] or ks
        w = np.array([max(1, hit[k]["n"]) for k in hit_ks], float)
        clat = float(np.average(elat[hit_ks], weights=w))
        clon = float(np.average(elon[hit_ks], weights=w))
        st = Counter(a_state[atlases[cells[k][6]]] for k in ks).most_common(1)[0][0]
        rank_max = esi_rank(cmax)
        tier = 1 if rank_max >= 9 else 2 if rank_max >= 7 else 3
        landfall = n_str > 0
        eta_h = (first - t0) / 3600
        # Local current at the stretch when oil arrives (boom feasibility).
        shorelines.append({
            "name": place_name(clon, clat), "state": st, "lat": round(clat, 3), "lon": round(clon, 3),
            "bbox": [round(float(elon[ks].min()), 3), round(float(elat[ks].min()), 3),
                     round(float(elon[ks].max()), 3), round(float(elat[ks].max()), 3)],
            "impact": "landfall" if landfall else "threatened",
            "eta_h": round(eta_h, 1), "eta": _iso(first),
            "particles_pct": round(100 * n_str / n, 1) if n else 0,
            "stranded_kg": round(n_str * kg_per) if landfall else 0,
            "stranded_bbl": round(n_str * kg_per / 0.9 / 158.987, 1) if landfall else 0,
            "shoreline_km": round(km, 1), "wetland_km": round(km10, 1),
            "esi_most_sensitive": cmax, "esi_most_sensitive_label": labels.get(cmax, cmax),
            "esi_dominant": cdom, "esi_dominant_label": labels.get(cdom, cdom),
            "sensitivity_tier": tier,
            "shoreline_tactic": next(txt for r, txt in SHORE_TACTICS if esi_rank(cmax) >= r),
            "_cells": ks,
        })
    # Rank: oiled before merely threatened; then sensitivity tier (ESI 9-10 first); then arrival time;
    # tiny hits (< 0.5 % of particles) after the real ones.
    shorelines.sort(key=lambda s: (s["impact"] != "landfall" or s["particles_pct"] < 0.5, s["sensitivity_tier"],
                                   s["eta_h"]))
    for i, s in enumerate(shorelines):
        s["priority"] = i + 1
    landfalls = [s for s in shorelines if s["impact"] == "landfall"]
    first_land = min(landfalls, key=lambda s: s["eta_h"]) if landfalls else None
    first_threat = min(shorelines, key=lambda s: s["eta_h"]) if shorelines else None

    # --- environment + windows --------------------------------------------------------------------------
    env = _env_series(req, hrs, cen)
    evap = []
    if bud and m0 > 0:
        bt = np.array([b["t"] for b in bud], float)
        ev = np.array([b.get("evaporated", 0) / m0 for b in bud])
        evap = np.interp(hrs, bt, ev)
    from . import response_layer as rl
    okc = np.isfinite(cen[:, 0])
    sk = np.full(len(cen), np.nan)
    dep = np.full(len(cen), np.nan)
    if okc.any():
        sk[okc] = rl.shore_km(cen[okc, 0], cen[okc, 1])
        d_ = rl.depth_m(cen[okc, 0], cen[okc, 1])
        if d_ is not None:
            dep[okc] = d_
    nearest_shore_km = [None if not np.isfinite(x) else round(float(x), 1) for x in sk]
    depth_c = [None if not np.isfinite(x) else round(float(x)) for x in dep]
    hours = []
    for i, t in enumerate(hrs):
        h = (t - t0) / 3600
        w = env["wind_ms"][i]
        wk = None if w is None else w * KT
        cur = env["current_ms"][i]
        day = bool(np.isfinite(cen[i, 0]) and sun_elevation(t, cen[i, 1], cen[i, 0]) > 0)
        off_nm = None if nearest_shore_km[i] is None else nearest_shore_km[i] / NM_KM
        has_surface = bool(np.isfinite(cen[i, 0]))
        disp_ok = (oc["dispersant"] is not None and oc["dispersant"] > 0 and h <= oc["dispersant"] and has_surface
                   and wk is not None and WIND_DISP_MIN <= wk <= WIND_DISP_MAX and day
                   and off_nm is not None and off_nm >= PREAUTH_NM
                   and (depth_c[i] is None or depth_c[i] >= PREAUTH_DEPTH_M))
        burn_ok = (oc["burn"] is not None and h <= oc["burn"] and has_surface and wk is not None
                   and wk <= WIND_BURN_MAX and day and off_nm is not None and off_nm >= PREAUTH_NM)
        mech_ok = oc["mechanical"] and has_surface and wk is not None and wk <= WIND_MECH_MAX and day
        hours.append({"t": t, "h": round(h, 1), "wind_kt": None if wk is None else round(wk, 1),
                      "wind_to": None if env["wind_dir_to"][i] is None else _compass(env["wind_dir_to"][i]),
                      "current_kt": None if cur is None else round(cur * KT, 2), "daylight": day,
                      "offshore_nm": None if off_nm is None else round(off_nm, 1), "depth_m": depth_c[i],
                      "evaporated_pct": round(100 * float(evap[i]), 1) if len(evap) else None,
                      "disp": bool(disp_ok), "burn": bool(burn_ok), "mech": bool(mech_ok)})

    winds = [h["wind_kt"] for h in hours if h["wind_kt"] is not None]
    curs = [h["current_kt"] for h in hours if h["current_kt"] is not None]

    def _why_not(kind):
        if kind == "disp":
            if oc["dispersant"] is None:
                return f"Not recommended for {oc['label']}: it evaporates/disperses naturally within hours to days."
            if oc["dispersant"] == 0:
                return (f"{oc['label'].capitalize()} is generally too viscous for dispersants to work; only after a "
                        "successful test application, and only if approved by the RRT.")
        if kind == "burn" and oc["burn"] is None:
            return f"In-situ burning is not used for {oc['label']} (fire/vapour hazard, or slick too thin)."
        if kind == "mech" and not oc["mechanical"]:
            return (f"{oc['label'].capitalize()} spreads too thin and evaporates too fast for booms and skimmers; "
                    "keep responders out of the vapour cloud (monitor LEL), let it evaporate, protect intakes.")
        return None

    def _tactic(kind, title, conds, extra):
        wins = _windows(hours, kind)
        return {"tactic": title, "feasible": bool(wins), "windows": wins,
                "total_hours": round(sum(w["hours"] for w in wins), 1),
                "not_applicable": _why_not(kind), "conditions": conds, "notes": extra}

    disp = _tactic("disp", "Dispersants (aerial / vessel)",
                   [f"oil still fresh: within ~{oc['dispersant'] or 0} h for {oc['label']} (before emulsification "
                    "raises viscosity)",
                    f"wind {WIND_DISP_MIN:.0f}-{WIND_DISP_MAX:.0f} kt (some mixing energy, safe flight ops)",
                    "daylight (spotter aircraft)",
                    f">= {PREAUTH_NM:.0f} nm from shore and >= {PREAUTH_DEPTH_M:.0f} m deep (RRT 4/6 pre-authorization "
                    "zone; depth from ETOPO1 at the slick centroid" + ("" if rl.bathy() is not None else
                                                                        ", NOT checked: no bathymetry cached") + ")"],
                   ["Requires FOSC decision and RRT concurrence outside pre-authorized zones; SMART monitoring."])
    burn = _tactic("burn", "In-situ burning (fire boom)",
                   [f"within ~{oc['burn'] or 0} h for {oc['label']} (ignitable, < ~25 % water in emulsion)",
                    "slick collected to >= 2-3 mm in fire-resistant boom",
                    f"wind <= {WIND_BURN_MAX:.0f} kt, waves < ~3 ft (waves not modelled)",
                    "daylight; >= 3 nm offshore; smoke plume away from populated areas"],
                   ["Smoke drifts toward the wind direction listed per hour; check nearby communities downwind."])
    mech = _tactic("mech", "Containment boom + skimmers (on water)",
                   [f"wind <= {WIND_MECH_MAX:.0f} kt, waves < ~1 m (not modelled)",
                    f"relative current < ~{BOOM_CURRENT_KT} kt for containment boom (else deflection angles)",
                    "daylight for most skimming operations"],
                   [f"Mean current along the slick {np.mean(curs):.2f} kt (max {max(curs):.2f} kt)." if curs else
                    "No current data along the slick."])

    # --- boom-first list ----------------------------------------------------------------------------------
    boom_first = []
    for s in shorelines:
        if s["impact"] != "landfall" or s["particles_pct"] < 0.5:
            continue
        in_time = s["eta_h"] >= STAGE_H
        boom_first.append({
            "priority": s["priority"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "eta_h": s["eta_h"],
            "esi": s["esi_most_sensitive"], "esi_label": s["esi_most_sensitive_label"],
            "action": ("Exclusion boom across inlets/marsh openings; deflection boom to collection points"
                       if s["sensitivity_tier"] == 1 else
                       "Boom sheltered shoreline / structures; stage skimmers at the leading edge"
                       if s["sensitivity_tier"] == 2 else
                       "Protect inlets behind the beach; stage beach cleanup crews"),
            "lead_time_ok": in_time,
            "note": (f"Oil expected in {s['eta_h']:.0f} h: deploy before then." if in_time else
                     f"Oil expected in {s['eta_h']:.0f} h, likely before boom can be staged (~{STAGE_H:.0f} h): "
                     "go straight to shoreline protection/cleanup crews and protect the next stretch."),
        })
        if len(boom_first) >= 5:
            break

    # --- closures ---------------------------------------------------------------------------------------
    closures = []
    ext = {}
    for hh in (24, 48, 72):
        k = int(min(len(hrs) - 1, hh))
        if hh > dur_h + 0.01:
            continue
        p = pos[:, k]
        p = p[np.isfinite(p[:, 0])]
        if len(p):
            ext[hh] = [round(float(p[:, 0].min()), 2), round(float(p[:, 1].min()), 2),
                       round(float(p[:, 0].max()), 2), round(float(p[:, 1].max()), 2)]
    if ext:
        closures.append({"type": "safety_zone",
                         "what": "USCG safety zone / vessel exclusion over the slick (and around burn/dispersant ops)",
                         "extent_bbox": ext, "who": "USCG Captain of the Port"})
    allp = flat[fin]
    tt_all = np.broadcast_to(hrs, (n, len(hrs))).reshape(-1)[fin]
    for name, plat, plon in PORT_ENTRANCES:
        if len(allp) == 0:
            break
        d = np.hypot((allp[:, 0] - plon) * 111.32 * math.cos(math.radians(plat)), (allp[:, 1] - plat) * 110.57)
        m = d < 15
        if m.any():
            closures.append({"type": "waterway", "what": f"{name}: consider traffic restriction / boom across "
                                                         "the entrance", "eta_h": round((tt_all[m].min() - t0) / 3600, 1),
                             "eta": _iso(tt_all[m].min()), "who": "USCG Captain of the Port (Sector)"})
    states = sorted({s["state"] for s in shorelines if s["impact"] == "landfall"}) or \
        sorted({s["state"] for s in shorelines})
    if oc["persistent"] or landfalls:
        closures.append({"type": "fishery",
                         "what": "Precautionary fishing/shellfish harvest closure over the slick"
                                 + (" and the oiled coast" if landfalls else " and the threatened coast" if shorelines
                                    else "")
                                 + (f" ({', '.join(states)})" if states else ""),
                         "who": "; ".join([STATE_AGENCY[s] for s in states if s in STATE_AGENCY]
                                          + ["NOAA Fisheries (federal waters)"])})
    if ocls in ("gasoline", "condensate", "jet"):
        closures.insert(0, {"type": "hazard", "what": "Vapour/fire hazard: keep vessels and aircraft clear "
                                                      "until LEL readings are safe; consider shelter-in-place "
                                                      "downwind if near shore", "who": "USCG / local emergency management"})

    # --- precedents -------------------------------------------------------------------------------------
    rec = ["closure"]
    if boom_first or mech["feasible"]:
        rec += ["boom", "skimming"]
    if disp["feasible"]:
        rec.append("dispersant")
    if burn["feasible"]:
        rec.append("in_situ_burn")
    if landfalls:
        rec += ["shoreline_cleanup", "wildlife"]
    prec = precedents(ocls, vol_bbl, lon0, lat0, rec, exclude=spill_id)

    headline = []
    if first_land:
        headline.append(f"First landfall in ~{first_land['eta_h']:.0f} h near {first_land['name'] or 'the coast'} "
                        f"(ESI {first_land['esi_most_sensitive']}: {first_land['esi_most_sensitive_label']}).")
    elif first_threat:
        headline.append(f"No landfall within {dur_h:.0f} h; oil comes within {APPROACH_KM:.0f} km of "
                        f"{first_threat['name'] or 'the coast'} in ~{first_threat['eta_h']:.0f} h.")
    else:
        headline.append(f"No shoreline reached or threatened within {dur_h:.0f} h of this forecast.")
    if budget:
        b = budget[-1]
        headline.append(f"After {b['h']} h: {b['surface']:.0f} % on the surface, {b['evaporated']:.0f} % evaporated, "
                        f"{b['dispersed']:.0f} % dispersed, {b['stranded']:.0f} % on shore.")

    # --- map layer (T21): geometry for arrival times, boom plan, tactic zones, staging, timeline ---------------
    try:
        layer = rl.build_layer(t0=t0, hrs=hrs, pos=pos, status=status, end_xy=end_xy, end_t=end_t, hit=hit,
                               stretch_cells=[s["_cells"] for s in shorelines], shorelines=shorelines,
                               boom_first=boom_first, hours=hours, oc=oc, req=req, port_entrances=PORT_ENTRANCES)
    except Exception as e:  # noqa: BLE001 - the text outline must still work
        import traceback
        traceback.print_exc()
        layer = {"error": f"{type(e).__name__}: {e}"}
    for s in shorelines:
        s.pop("_cells", None)

    out = {
        "version": VERSION, "run_id": run_id, "spill_id": spill_id, "disclaimer": DISCLAIMER,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "spill": {"lat": lat0, "lon": lon0, "start": _iso(t0), "horizon_h": round(dur_h, 1),
                  "volume_bbl": round(vol_bbl, 1), "oil_type": oil_name, "oil_class": ocls,
                  "oil_class_label": oc["label"], "persistent": oc["persistent"],
                  "model": meta.get("model") or req.get("model") or "openoil", "particles": n,
                  "forecast": req.get("forecast")},
        "headline": headline,
        "eta_first_landfall_h": first_land["eta_h"] if first_land else None,
        "eta_first_landfall": first_land["eta"] if first_land else None,
        "first_landfall": first_land and {k: first_land[k] for k in ("name", "state", "lat", "lon",
                                                                     "esi_most_sensitive", "esi_most_sensitive_label")},
        "eta_first_threat_h": first_threat["eta_h"] if first_threat else None,
        "stranded_pct_end": budget[-1]["stranded"] if budget else None,
        "budget": budget,
        "priority_shorelines": shorelines[:12],
        "shorelines_total": len(shorelines),
        "boom_first": boom_first,
        "tactics": {"dispersant": disp, "in_situ_burn": burn, "mechanical": mech},
        "conditions": {
            "wind_kt": {"min": min(winds), "mean": round(float(np.mean(winds)), 1), "max": max(winds)} if winds else None,
            "current_kt": {"mean": round(float(np.mean(curs)), 2), "max": max(curs)} if curs else None,
            "hourly": [{k: h[k] for k in ("h", "wind_kt", "wind_to", "current_kt", "daylight", "offshore_nm",
                                          "depth_m", "evaporated_pct")} for h in hours[::3]],
            "source": env["source"] or None,
        },
        "closures": closures,
        "layer": layer,
        "precedents": prec,
        "sources": [
            {"what": "Shoreline sensitivity", "ref": ed["source"], "url": ed["source_url"]},
            {"what": "Response options", "ref": "NOAA ORR, Characteristics of Response Strategies: A Guide for "
             "Spill Response Planning in Marine Environments (2010)",
             "url": "https://response.restoration.noaa.gov/oil-and-chemical-spills/oil-spills/resources/"
                    "characteristics-response-strategies.html"},
            {"what": "Dispersant pre-authorization", "ref": "RRT-6 FOSC Dispersant Pre-Approval Guidelines v4.0 (2001): "
             "offshore waters from the 10 m isobath or 3 nmi, whichever is farther from shore, to 200 nmi "
             "(RRT 4 1996 'green zone': >= 3 mi and >= 10 m)",
             "url": "https://www.glo.texas.gov/sites/default/files/2025-07/rrt6.pdf"},
            {"what": "In-situ burn pre-authorization", "ref": "RRT-6 In-Situ Burn Policy for the Offshore Environment "
             "(Appendix 13, 2020): seaward of 3 miles; RRT VI ISB Plan (1994) excludes some reefs + an area off Grand Isle",
             "url": "https://www.glo.texas.gov/sites/default/files/resources/glo/ost/spill-response-resources/rrtvi/"
                    "RRT6_ISB_Policy_Offshore_Appendix13.pdf"},
            {"what": "Precedents", "ref": prec["source"], "url": "https://incidentnews.noaa.gov/"},
            {"what": "Place names", "ref": "US Census Bureau 2023 Gazetteer (places)",
             "url": "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html"},
        ],
        "limits": [
            "One deterministic forecast run; real oil can go elsewhere. Re-run as forecasts update.",
            "Stranding happens on the model coastline (GSHHG / ocean-model land, ~1-4 km); ESI matching uses "
            f"{LANDFALL_KM:.0f} km, so bay-interior shorelines are not resolved.",
            "Waves, slick thickness and emulsion water content are not modelled: windows use wind, daylight, distance "
            "from shore, water depth (dispersants; ETOPO1" + ("" if rl.bathy() is not None else ", NOT available here")
            + ") and oil-type time limits only.",
            f"Boom staging time is assumed ~{STAGE_H:.0f} h in the text outline; the map layer uses the nearest real "
            "staging base instead (layer.rules).",
            "Map boom sites follow a generic ESI rule, not the official Geographic Response Plans (TX GLO, LA ACP).",
        ],
    }
    return json.loads(json.dumps(out, default=_plain))


def _plain(o):
    """numpy scalars -> JSON."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not np.isfinite(o) else float(o)
    raise TypeError(type(o))
