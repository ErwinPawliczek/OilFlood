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

VERSION = "resp-v3"  # v3 (T23): real protocol gates (PROTOCOLS.md R1-R10), uncertainty envelope, Tier 1 lead time
# v2: + "layer" (map geometry, T21); windows use ETOPO1 depth + coast distance
KT = 1.943844  # m/s -> knots
NM_KM = 1.852

DISCLAIMER = ("Planning aid generated automatically from one model forecast. Not operational guidance: "
              "the Federal On-Scene Coordinator / Unified Command, the Area Contingency Plan and the "
              "Regional Response Team decide every response action.")

# --- rule tables (sources: docs/RESPONSE.md) -----------------------------------------------------------

# Oil groups, roughly ITOPF/NOAA persistence groups. No age limits any more (T23, GAP-2): dispersant / burn
# feasibility comes from the oil's ADIOS API gravity, pour point and the slick's modelled emulsion water content
# (app/protocols.py). `light_product`: gasoline / condensate / jet evaporate within hours and are a fire/explosion
# hazard, so containment, skimming and burning are not planned for them (CRS S6, gasoline note).
OIL_CLASSES = {
    "gasoline": {"label": "gasoline (non-persistent, flammable)", "group": 1, "persistent": False,
                 "light_product": True, "mechanical": False},
    "condensate": {"label": "condensate (non-persistent)", "group": 1, "persistent": False,
                   "light_product": True, "mechanical": False},
    "jet": {"label": "jet fuel (light refined, non-persistent)", "group": 1, "persistent": False,
            "light_product": True, "mechanical": False},
    "diesel": {"label": "diesel / marine gas oil (light refined)", "group": 2, "persistent": False,
               "light_product": False, "mechanical": True},
    "crude_light": {"label": "light crude", "group": 2, "persistent": True, "light_product": False, "mechanical": True},
    "crude_medium": {"label": "medium crude", "group": 3, "persistent": True, "light_product": False, "mechanical": True},
    "crude_heavy": {"label": "heavy crude", "group": 4, "persistent": True, "light_product": False, "mechanical": True},
    "heavy_fuel": {"label": "heavy fuel oil (IFO / No. 6)", "group": 4, "persistent": True, "light_product": False,
                   "mechanical": True},
}
OIL_ID_CLASS = {"crude_light": "crude_light", "crude_medium": "crude_medium", "crude_heavy": "crude_heavy",
                "ifo380": "heavy_fuel", "fuel_no6": "heavy_fuel", "diesel": "diesel", "jet": "jet",
                "gasoline": "gasoline", "condensate": "condensate"}

# Operating limits: all cited, see app/protocols.py (rule IDs -> docs/PROTOCOLS.md / data/response/protocols.json).
from . import protocols as P  # noqa: E402

T_WATER_C = 25.0        # sea-surface temperature for the pour-point check = our model's Gulf default (OURMODEL.md)
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

    # --- environment + gates (T23: docs/PROTOCOLS.md R3/R4/R5, app/protocols.py) --------------------------
    env = _env_series(req, hrs, cen)
    from . import response_layer as rl
    props = P.oil_props(meta.get("oil_type") or oil_name, ocls)
    # continuous release: the run's own point source (backtest), or a catalog blowout (DWH, Ixtoc)
    cat_rel = next((str(c.get("release") or "") for c in _catalog() if spill_id and c.get("id") == spill_id), "")
    continuous = bool(req.get("source")) or "blowout" in cat_rel.lower()
    # fraction evaporated of the oil released so far (continuous releases: m0 grows), surface volume per hour
    evap, surf_bbl = np.zeros(len(hrs)), np.zeros(len(hrs))
    if bud:
        bt = np.array([b["t"] for b in bud], float)
        tot = np.array([sum(b.get(k, 0) for k in ("surface", "evaporated", "dispersed", "stranded")) for b in bud])
        ev = np.array([b.get("evaporated", 0) for b in bud]) / np.maximum(tot, 1e-9)
        evap = np.interp(hrs, bt, ev)
        surf_bbl = np.interp(hrs, bt, np.array([b.get("surface", 0) for b in bud], float)) / props["density"] / 0.158987
    emul = P.emulsion_series(props, env["wind_ms"], list(evap))
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
    have_bathy = rl.bathy() is not None
    ab = P.api_band(props["api"])
    pour_ok = None if props["pour_point_c"] is None else props["pour_point_c"] < T_WATER_C
    hours = []
    n_alive = np.isfinite(pos[..., 0]).sum(axis=0)
    for i, t in enumerate(hrs):
        h = (t - t0) / 3600
        w = env["wind_ms"][i]
        wk = None if w is None else w * KT
        cur = env["current_ms"][i]
        ck = None if cur is None else cur * KT
        has_surface = bool(np.isfinite(cen[i, 0]))
        sun = sun_elevation(t, cen[i, 1], cen[i, 0]) if has_surface else -90.0
        off_nm = None if nearest_shore_km[i] is None else nearest_shore_km[i] / NM_KM
        y = 0.0 if continuous else emul[i]  # continuous release: the freshest oil at the source (GAP-2)
        per_bbl = surf_bbl[i] / n_alive[i] if n_alive[i] else 0.0
        patch = rl.patch_bbl(pos[:, i], per_bbl) if has_surface else (0.0, 0.0)
        zone_d = (None if off_nm is None else False if off_nm < P.DISP_MIN_NM
                  else None if depth_c[i] is None else depth_c[i] >= P.DISP_MIN_DEPTH_M)
        g_disp = [
            P.gate("api", "Oil dispersibility (API gravity)", ab["ok"], ab["text"], "17-45", ("DISP-5",)),
            P.gate("pour", "Pour point below sea temperature", pour_ok,
                   None if props["pour_point_c"] is None else f"{props['pour_point_c']:.0f} °C vs {T_WATER_C:.0f} °C",
                   "pour point < water temp", ("DISP-5",)),
            P.gate("emulsion", "Emulsion water content", y <= P.EMULSION_MAX, f"{100 * y:.0f} %", "<= 25 %",
                   ("WIN-4",)),
            P.gate("zone", "Pre-approval zone (>= 3 nm AND >= 10 m)", zone_d,
                   None if off_nm is None else f"{off_nm:.1f} nm, " + (f"{depth_c[i]} m" if depth_c[i] is not None
                                                                        else "depth n/a"),
                   ">= 3 nm and >= 10 m", ("DISP-1", "DISP-3")),
            P.gate("wind", "Wind (aerial)", None if wk is None else wk <= P.DISP_WIND_MAX_KT,
                   None if wk is None else f"{wk:.0f} kt", "<= 25 kt", ("DISP-4",)),
            P.gate("mixing", "Mixing energy", None if wk is None else wk >= P.DISP_WIND_SOFT_MIN_KT,
                   None if wk is None else f"{wk:.0f} kt", "a few knots (~4 kt)", ("DISP-8",), soft=True),
            P.gate("daylight", "Daylight", sun > 0, f"sun {sun:.0f}°", "daylight only", ("DISP-4",)),
            P.gate("vis", "Visibility >= 3 nm, ceiling >= 1,000 ft", None, "not in forecast", "check", ("DISP-4",)),
        ]
        gi_km = P._km(cen[i, 0], cen[i, 1], *P.GRAND_ISLE) if has_surface else 1e9
        g_burn = [
            P.gate("emulsion", "Emulsion water content", y <= P.EMULSION_MAX, f"{100 * y:.0f} %",
                   "<= 25 % (50 % with test burn)", ("ISB-2", "WIN-4")),
            P.gate("volume", "Oil to burn in one patch (~6 km)", patch[0] >= P.BURN_MIN_BBL, f"{patch[0]:.0f} bbl",
                   "> 500 bbl per burn", ("ISB-3",)),
            P.gate("zone", "Seaward of 3 nm", None if off_nm is None else off_nm >= P.BURN_MIN_NM,
                   None if off_nm is None else f"{off_nm:.1f} nm", ">= 3 nm", ("ISB-1",)),
            P.gate("grand_isle", "Outside Grand Isle 7 mi buffer", gi_km > P.GRAND_ISLE_BUFFER_MI * 1.609344,
                   f"{gi_km / 1.609344:.0f} mi", "> 7 mi in onshore wind", ("ISB-1",)),
            P.gate("wind", "Wind", None if wk is None else wk <= P.BURN_WIND_MAX_KT,
                   None if wk is None else f"{wk:.0f} kt", "<= 21.7 kt (25 mph)", ("ISB-4",)),
            P.gate("wind_pref", "Wind preferred", None if wk is None else wk <= P.BURN_WIND_PREF_KT,
                   None if wk is None else f"{wk:.0f} kt", "< 17.4 kt (20 mph)", ("ISB-4",), soft=True),
            P.gate("current", "Current", None if ck is None else ck <= P.BURN_CURRENT_MAX_KT,
                   None if ck is None else f"{ck:.2f} kt", "<= 0.75 kt", ("ISB-2",)),
            P.gate("sun", "Sun > 15° (no night/dawn/dusk)", sun > P.BURN_SUN_MIN_DEG, f"sun {sun:.0f}°", "> 15°",
                   ("ISB-5",)),
            P.gate("thickness", "Collected to >= 2-3 mm in fire boom", None,
                   f"model cell mean {patch[1]:.3f} mm", "field check (overflight)", ("ISB-2",)),
            P.gate("waves", "Waves <= 3 ft", None, "not in forecast", "check", ("ISB-2",)),
        ]
        g_mech = [
            P.gate("daylight", "Daylight (spotting)", sun > 0, f"sun {sun:.0f}°", "daylight", ("ISB-5",), soft=False),
            P.gate("waves", "Short-period waves <= 2-3 ft", None, "not in forecast", "check", ("MECH-2",)),
        ]

        def ok_all(gs):  # every hard gate passes or is unknown (unknown = "check" in the panel)
            return all(g["ok"] is not False for g in gs if not g["soft"])
        disp_ok = (has_surface and not oc["light_product"] and ab["ok"] is not False and ok_all(g_disp))
        burn_ok = (has_surface and not oc["light_product"] and ok_all(g_burn))
        mech_ok = bool(oc["mechanical"] and has_surface and ok_all(g_mech))
        # time-only gates (everything except the zone): the map layer applies the zone per ~2 km cell
        disp_t = has_surface and not oc["light_product"] and ab["ok"] is not False and all(
            g["ok"] is not False for g in g_disp if not g["soft"] and g["key"] != "zone")
        burn_t = has_surface and not oc["light_product"] and all(
            g["ok"] is not False for g in g_burn if not g["soft"] and g["key"] not in ("zone", "volume", "grand_isle"))
        hours.append({"t": t, "h": round(h, 1), "wind_kt": None if wk is None else round(wk, 1),
                      "wind_to": None if env["wind_dir_to"][i] is None else _compass(env["wind_dir_to"][i]),
                      "wind_to_deg": env["wind_dir_to"][i],
                      "current_kt": None if ck is None else round(ck, 2), "daylight": sun > 0,
                      "sun_deg": round(sun, 1),
                      "offshore_nm": None if off_nm is None else round(off_nm, 1), "depth_m": depth_c[i],
                      "evaporated_pct": round(100 * float(evap[i]), 1),
                      "emulsion_pct": round(100 * y, 1), "surface_bbl": round(float(surf_bbl[i])),
                      "patch_bbl": round(patch[0]), "per_bbl": per_bbl,
                      "disp": bool(disp_ok), "burn": bool(burn_ok), "mech": bool(mech_ok),
                      "disp_t": bool(disp_t), "burn_t": bool(burn_t), "surface": has_surface,
                      "gates": {"disp": g_disp, "burn": g_burn, "mech": g_mech}})

    winds = [h["wind_kt"] for h in hours if h["wind_kt"] is not None]
    curs = [h["current_kt"] for h in hours if h["current_kt"] is not None]

    def _why_not(kind):
        if kind == "disp":
            if oc["light_product"] or ab.get("no_need"):
                return (f"Not needed for {oc['label']}: {ab['text']} (RRT-6 oil table, DISP-5); it evaporates and "
                        "disperses naturally.")
            if ab["ok"] is False:
                return (f"{ab['text']} (RRT-6 oil table, DISP-5): only after a successful test application, with "
                        "RRT concurrence.")
        if kind == "burn" and oc["light_product"]:
            return (f"In-situ burning is not planned for {oc['label']}: it evaporates within hours and is a "
                    "fire/explosion hazard (CRS, S6).")
        if kind == "mech" and not oc["mechanical"]:
            return (f"{oc['label'].capitalize()} spreads too thin and evaporates too fast for booms and skimmers; "
                    "keep responders out of the vapour cloud (monitor LEL), let it evaporate, protect intakes (CRS, S6).")
        return None

    def _gate_summary(kind):
        """per gate: hours passed / failed / unknown over hours with surface oil (what blocks the window)."""
        out = []
        hs = [h for h in hours if h["surface"]]
        for gi, g0 in enumerate(hs[0]["gates"][kind] if hs else []):
            vals = [h["gates"][kind][gi]["ok"] for h in hs]
            first_fail = next((h for h in hs if h["gates"][kind][gi]["ok"] is False), None)
            out.append({**{k: g0[k] for k in ("key", "label", "limit", "rules", "sources", "soft")},
                        "pass_h": sum(v is True for v in vals), "fail_h": sum(v is False for v in vals),
                        "unknown_h": sum(v is None for v in vals),
                        "first_value": g0["value"], "first_fail": None if first_fail is None else {
                            "h": first_fail["h"], "value": first_fail["gates"][kind][gi]["value"]}})
        return out

    def _tactic(kind, title, extra, prec_key):
        wins = _windows(hours, kind)
        return {"tactic": title, "feasible": bool(wins), "windows": wins,
                "total_hours": round(sum(w["hours"] for w in wins), 1),
                "not_applicable": _why_not(kind), "gates": _gate_summary(kind),
                "conditions": [f"{g['label']}: {g['limit']} [{', '.join(g['rules'])}]" for g in
                               (hours[0]["gates"][kind] if hours else [])],
                "notes": extra, "precedents": P.precedents_for(prec_key)}

    disp = _tactic("disp", "Dispersants (aerial / vessel)",
                   ["FOSC decides under RRT-6 pre-approval; RRT notified, results to RRT within 24 h; spray contractor "
                    "under contract before the spill (AUTH-1, DISP-6).",
                    "SMART monitoring for every application (Tier I visual minimum; positive feedback to continue; birds "
                    "1,000 ft clear) (DISP-7).",
                    "Outside the pre-approval zone (< 3 nm or < 10 m): RRT-6 concurrence case by case (DISP-3)."
                    + ("" if have_bathy else " Depth NOT checked: no bathymetry cached.")]
                   + (["Diesel: not excluded by the RRT-6 table but evaporates/disperses naturally fast; "
                       "dispersing it is a judgment call (GAP-14)."] if ocls == "diesel" else []),
                   "dispersant")
    burn = _tactic("burn", "In-situ burning (fire boom)",
                   ["FOSC decides after consulting RRT-6 (EPA minimum) with the ISB checklist, smoke-plume model and "
                    "weather (AUTH-2).",
                    "SMART PM10 monitoring: level of concern 150 µg/m³ (1 h) (ISB-7). No plume toward a population centre "
                    "within 3 mi (ISB-3).",
                    "Reef / bank / hard-bottom exclusion areas of the RRT-6 ISB plan are not in OilFlood's data: check "
                    "the ACP maps (GAP-5)."], "in_situ_burn")
    mech = _tactic("mech", "Containment boom + skimmers (on water)",
                   ["On open water the boom drifts with the slick: the limit is tow speed / current across the boom, "
                    f"{P.BOOM_ENTRAIN_KT} kt (MECH-1), not the ocean current itself.",
                    "Expect a fraction: DWH skimmed ~3 %, Texas City Y recovered ~18 % as liquid (MON-2, R7).",
                    f"Mean current along the slick {np.mean(curs):.2f} kt (max {max(curs):.2f} kt)." if curs else
                    "No current data along the slick."], "skimming")

    # --- uncertainty envelope (R1, FCST-2/FCST-4): windage 1-4 % instead of the model's own value ---------------
    wd_model = float(req.get("wind_drift_factor") or (0.03 if (meta.get("model") or req.get("model")) == "ours"
                                                    else 0.02))
    uw = np.array([0.0 if h["wind_kt"] is None or h["wind_to_deg"] is None else
                   h["wind_kt"] / KT * math.sin(math.radians(h["wind_to_deg"])) for h in hours])
    vw = np.array([0.0 if h["wind_kt"] is None or h["wind_to_deg"] is None else
                   h["wind_kt"] / KT * math.cos(math.radians(h["wind_to_deg"])) for h in hours])
    cum_u, cum_v = np.concatenate([[0], np.cumsum(uw)[:-1]]) * 3600, np.concatenate([[0], np.cumsum(vw)[:-1]]) * 3600
    members = []
    for wd in P.WINDAGE_RANGE:
        dw = wd - wd_model
        members.append({"windage": wd, "dx_deg": dw * cum_u / (111320 * math.cos(math.radians(lat0))),
                        "dy_deg": dw * cum_v / 110574})
    # earliest plausible arrival per ESI cell over the members (oil within APPROACH_KM of the cell)
    early: dict[int, float] = {}
    for m in members:
        sh = pos.copy()
        sh[..., 0] += m["dx_deg"][None, :]
        sh[..., 1] += m["dy_deg"][None, :]
        f2 = sh.reshape(-1, 2)
        fin2 = np.isfinite(f2[:, 0])
        if not fin2.any():
            continue
        tt2 = np.broadcast_to(hrs, (n, len(hrs))).reshape(-1)[fin2]
        dd2, nr2 = etree.query(_xy(f2[fin2, 0], f2[fin2, 1]), distance_upper_bound=APPROACH_KM)
        for j in np.nonzero(np.isfinite(dd2))[0]:
            c = int(nr2[j])
            if c not in early or tt2[j] < early[c]:
                early[c] = tt2[j]
    for s in shorelines:
        e = [early[k] for k in s["_cells"] if k in early]
        best = s["eta_h"]
        s["eta_earliest_h"] = round(min([best] + [(x - t0) / 3600 for x in e]), 1)
    known = {k for s in shorelines for k in s["_cells"]}
    extra_cells = [k for k in early if k not in known]
    envelope_only = {"cells": len(extra_cells), "shoreline_km": round(sum(cells[k][2] for k in extra_cells), 1),
                     "earliest_h": round(min((early[k] - t0) / 3600 for k in extra_cells), 1) if extra_cells else None,
                     "places": sorted({place_name(float(elon[k]), float(elat[k])) or "" for k in
                                       sorted(extra_cells, key=lambda k: early[k])[:6]} - {""})}

    # --- boom-first list with real lead times (R6) ---------------------------------------------------------------
    boom_first = []
    for s in shorelines:
        if s["impact"] != "landfall" or s["particles_pct"] < 0.5:
            continue
        base = rl.nearest_equipment_base(s["lon"], s["lat"])
        lt = P.lead_time(s["lon"], s["lat"], base)
        in_time = s["eta_h"] >= lt["lead_h"]
        early_ok = s["eta_earliest_h"] >= lt["lead_h"]
        marsh = s["sensitivity_tier"] == 1
        boom_first.append({
            "priority": s["priority"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "eta_h": s["eta_h"],
            "eta_earliest_h": s["eta_earliest_h"],
            "esi": s["esi_most_sensitive"], "esi_label": s["esi_most_sensitive_label"],
            "action": ("Exclusion boom across inlets/marsh openings; deflection boom to collection points"
                       if s["sensitivity_tier"] == 1 else
                       "Boom sheltered shoreline / structures; stage skimmers at the leading edge"
                       if s["sensitivity_tier"] == 2 else
                       "Protect inlets behind the beach; stage beach cleanup crews"),
            "lead": lt, "lead_time_ok": in_time, "lead_time_ok_earliest": early_ok,
            "note": ((f"Oil expected in {s['eta_h']:.0f} h (earliest {s['eta_earliest_h']:.0f} h); boom on scene in "
                      f"~{lt['lead_h']:.0f} h (Tier 1 {lt['tier1_h']:.0f} h): deploy before then.") if in_time else
                     (f"Oil expected in {s['eta_h']:.0f} h, before Tier 1 boom can be on scene (~{lt['lead_h']:.0f} h"
                      f"{', higher-volume port area' if lt['hvp'] else ''}): go straight to shoreline crews and "
                      "protect the next site (R6).")) + (
                f" Marsh-wide protective booming takes {P.MARSH_BOOM_DAYS[0]}-{P.MARSH_BOOM_DAYS[1]} days (Galveston "
                "Bay ACP, MECH-7): pre-position." if marsh else ""),
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
                         "extent_bbox": ext, "who": "USCG Captain of the Port",
                         "basis": "Responder safety (R9, IncidentNews safety-zone practice)"})
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
                             "eta": _iso(tt_all[m].min()), "who": "USCG Captain of the Port (Sector)",
                             "basis": "Planning assumption (15 km trigger is OilFlood's, GAP-15); precedent: Texas City Y "
                                      "2014, COTP closed the Houston Ship Channel to stop wakes pushing oil ashore"})
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
                               boom_first=boom_first, hours=hours, oc=oc, req=req, port_entrances=PORT_ENTRANCES,
                               members=members, lon0=lon0, lat0=lat0)
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
        "boom_precedents": P.precedents_for("boom"),
        "shoreline_precedents": P.precedents_for("shoreline"),
        "closure_precedents": P.precedents_for("closure"),
        "uncertainty": {
            "label": "Best guess + uncertainty",
            "method": (f"Best guess = this model run (windage {100 * wd_model:.0f} %). Uncertainty = the same particles "
                       f"re-drifted with windage {100 * P.WINDAGE_RANGE[0]:.0f} % and {100 * P.WINDAGE_RANGE[1]:.0f} % "
                       "(GNOME's 1-4 % range, FCST-4) using the forecast wind at the slick. Like NOAA's 'minimum "
                       "regret' area (FCST-2): areas that could be hit if the wind drift differs, not the extent of oil. "
                       "Wind-direction and current uncertainty are not included."),
            "windage_model": wd_model, "windage_range": list(P.WINDAGE_RANGE),
            "envelope_only_shoreline": envelope_only,
            "skill": "NOAA: a 48 h trajectory is within 1-2 miles only if winds, currents and windage are right; "
                     "re-run at least daily and re-tune to overflights (FCST-3). OilFlood DWH backtest: 72 h median "
                     "centroid error 39-46 km (docs/BACKTEST.md).",
            "sources": P.src(["S7", "S8"]),
        },
        "oil": {**{k: props[k] for k in ("adios", "api", "pour_point_c", "ymax", "source")},
                "api_band": ab["text"], "continuous_release": continuous,
                "emulsion_pct": {str(hh): round(100 * (0.0 if continuous else emul[min(hh, len(emul) - 1)]))
                                 for hh in (6, 12, 24, 48, 72) if hh <= dur_h + 0.01},
                "window_note": "Most oils become hard to recover, burn or disperse within 24-48 h on open water (WIN-2)"
                               + ("; with a continuous release fresh oil keeps surfacing near the source, so the gates "
                                  "are evaluated for the freshest oil (DWH burned and dispersed for ~11-13 weeks)."
                                  if continuous else ".")},
        "protocol": _protocol_steps(shorelines, boom_first, disp, burn, mech, P),
        "model_vs_reality": P.model_vs_reality(),
        "tactics": {"dispersant": disp, "in_situ_burn": burn, "mechanical": mech},
        "conditions": {
            "wind_kt": {"min": min(winds), "mean": round(float(np.mean(winds)), 1), "max": max(winds)} if winds else None,
            "current_kt": {"mean": round(float(np.mean(curs)), 2), "max": max(curs)} if curs else None,
            "hourly": [{k: h[k] for k in ("h", "wind_kt", "wind_to", "current_kt", "daylight", "sun_deg", "offshore_nm",
                                          "depth_m", "evaporated_pct", "emulsion_pct", "surface_bbl", "patch_bbl",
                                          "disp", "burn", "mech")} for h in hours[::3]],
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
            {"what": "Response protocols (all thresholds, rule IDs)", "ref": "OilFlood docs/PROTOCOLS.md + "
             "data/response/protocols.json (RRT-6, NOAA, USCG/CFR, NTSB sources S1-S27)", "url": P.DOC_URL},
            {"what": "Tier 1 response times", "ref": "33 CFR 154.1045 (Tier 1 6 h higher-volume port area, 12 h "
             "elsewhere; en route within 2 h) + 154.1020 (higher-volume port areas)",
             "url": "https://www.law.cornell.edu/cfr/text/33/154.1045"},
            {"what": "Oil properties (API, pour point, emulsion)", "ref": props["source"] + f": {props['adios']}",
             "url": "https://adios.orr.noaa.gov/"},
            {"what": "Precedents", "ref": prec["source"], "url": "https://incidentnews.noaa.gov/"},
            {"what": "Place names", "ref": "US Census Bureau 2023 Gazetteer (places)",
             "url": "https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html"},
        ],
        "limits": [
            "One forecast run plus a windage-only uncertainty envelope; real oil can go elsewhere. Re-run as forecasts "
            "update (NOAA re-runs at least daily, FCST-3).",
            "Stranding happens on the model coastline (GSHHG / ocean-model land, ~1-4 km); ESI matching uses "
            f"{LANDFALL_KM:.0f} km, so bay-interior shorelines are not resolved.",
            "Waves, visibility and cloud ceiling are not in the run's forcing: those gates show 'check' (RRT-6 needs "
            "visibility >= 3 nm / ceiling >= 1,000 ft for aerial dispersant; ISB and skimming need waves <= 2-3 ft).",
            "Emulsion water content is Mackay's formula on the forecast wind and the run's evaporation (same as our "
            "model); slick thickness is a cell mean (a lower bound), so the 2-3 mm burn thickness stays a field check.",
            "Water depth for the 10 m dispersant rule: ETOPO1" + ("" if rl.bathy() is not None else
                                                                 " NOT available here (depth not checked)") + ".",
            "Lead time = 0.5 h notify + 2 h mobilize + road travel at 35 mph from the nearest equipment base, floored at "
            "the USCG Tier 1 time (6 h higher-volume port areas, 12 h elsewhere). Real fleets can be faster (Texas City "
            "Y: first responders within 2 h) or slower.",
            "Boom-site current comes from the ocean model grid (HYCOM ~4-9 km); tidal passes and inlets are not "
            "resolved, so the across-boom current there is usually stronger than shown (DWH: pilings defeated by pass "
            "currents).",
            "Reef / bank ISB exclusion zones and ACP/GRP priority protection areas are not loaded: shoreline priority "
            "is ESI-only, defer to the ACP/GRP site list (R8).",
            "Map boom sites follow a generic ESI rule, not the official Geographic Response Plans (TX GLO, LA ACP).",
        ],
    }
    return json.loads(json.dumps(out, default=_plain))


def _protocol_steps(shorelines, boom_first, disp, burn, mech, P) -> dict:
    """'How we go from model to action' (docs/PROTOCOLS.md section 1), filled in with this run's numbers."""
    land = [s for s in shorelines if s["impact"] == "landfall"]
    late = [b for b in boom_first if not b["lead_time_ok"]]
    nwin = {k: t["total_hours"] for k, t in (("dispersant", disp), ("burn", burn), ("skimming", mech))}
    return {"doc_url": P.DOC_URL, "steps": [
        {"step": "Forecast", "text": "Trajectory best guess + uncertainty envelope, like NOAA's GNOME best estimate "
         "and minimum regret (R1).", "this_run": f"{len(land)} shoreline stretches oiled, {len(shorelines)} reached "
         "or threatened", "rules": ["FCST-2", "FCST-4"]},
        {"step": "Priority sites (ICS-232 style)", "text": "Rank the threatened shoreline: ESI 10 > 9 > 8 > 7 > rest, "
         "then arrival time; the ACP/GRP site list overrides (R8).",
         "this_run": f"top site: {shorelines[0]['name']} (ESI {shorelines[0]['esi_most_sensitive']})" if shorelines
         else "no shoreline reached", "rules": ["ESI-1", "ESI-3"]},
        {"step": "Tactic gates", "text": "Go/no-go per hour: dispersant (RRT-6 zone, API, wind, daylight), in-situ burn "
         "(emulsion, 500 bbl, wind, current, sun > 15°), boom (current across the boom) (R3-R5).",
         "this_run": ", ".join(f"{k} {v:.0f} h" for k, v in nwin.items()) + " open in this forecast",
         "rules": ["DISP-1", "DISP-4", "DISP-5", "ISB-2", "ISB-3", "ISB-5", "MECH-1"]},
        {"step": "Lead time vs arrival", "text": "Boom on scene = notify 0.5 h + en route 2 h + travel, at least the "
         "USCG Tier 1 time (6 h higher-volume port, 12 h elsewhere); if oil arrives first, switch to shoreline crews "
         "and the next site (R6).", "this_run": f"{len(boom_first) - len(late)} of {len(boom_first)} boom sites "
         "reachable before the oil", "rules": ["MECH-6", "MECH-7", "WIN-3"]},
        {"step": "Assignments (ICS-204 style)", "text": "Dated actions: notify, mobilize, boom in place, windows "
         "open/close, landfall. The FOSC / Unified Command approves (R10).",
         "this_run": "see Next actions", "rules": ["ICS-1", "AUTH-1", "AUTH-2"]},
    ]}


def _plain(o):
    """numpy scalars -> JSON."""
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if not np.isfinite(o) else float(o)
    raise TypeError(type(o))
