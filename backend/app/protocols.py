"""Real response protocols as code (T23). Every threshold here carries the rule ID(s) of
data/response/protocols.json / docs/PROTOCOLS.md and through them a source (S1..S27).

Used by app/response.py (hourly tactic gates, lead time, precedents, explainer) and app/response_layer.py
(spatial gates: dispersant/burn zones, boom-site current, uncertainty envelope).

Planning aid only. The FOSC / Unified Command, the ACP and the RRT decide every response action.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

DOC_URL = "https://github.com/ErwinPawliczek/OilFlood/blob/main/docs/PROTOCOLS.md"

# --- thresholds (value, rule IDs) ------------------------------------------------------------------------
# Dispersant (RRT-6 pre-approval, S1/S5)
DISP_MIN_NM = 3.0            # DISP-1: >= 3 nm from shore ...
DISP_MIN_DEPTH_M = 10.0      # DISP-1: ... AND >= 10 m isobath, "whichever is farthest"
DISP_CIRCLE_NM = 10.0        # DISP-2: 10 nm planning circle around the source
DISP_WIND_MAX_KT = 25.0      # DISP-4: aerial go/no-go wind <= 25 kt (DWH: < 35 kt with drift correction, DISP-9)
DISP_WIND_SOFT_MIN_KT = 4.0  # DISP-8: "a few knots of wind and a light chop" (CRS Fig. 3 bands start at 4-6 kt): soft flag
DISP_VIS_NM, DISP_CEIL_FT = 3.0, 1000.0  # DISP-4: not in the run's forcing -> reported as "check"
API_BANDS = [(0, 17, "difficult/impossible to disperse"), (17, 34.5, "easily dispersed if treated promptly"),
             (34.5, 45, "easily dispersed, relatively non-persistent"), (45, 99, "no need: dissipates naturally")]  # DISP-5
EMULSION_MAX = 0.25          # ISB-2 / WIN-4 (burn hard gate); R3 applies the same ~25 % to dispersants
EMULSION_TEST_BURN = 0.50    # ISB-2 / ISB-3: up to 50 % water with a successful test burn
# In-situ burn (RRT-6 ISB policy, S2/S3/S4)
BURN_MIN_NM = 3.0            # ISB-1: seaward of 3 nm
BURN_WIND_MAX_KT = 21.7      # ISB-4: <= 25 mph
BURN_WIND_PREF_KT = 17.4     # ISB-4: preferred < 20 mph
BURN_CURRENT_MAX_KT = 0.75   # ISB-2: ocean current <= 0.75 kt
BURN_SUN_MIN_DEG = 15.0      # ISB-5: sun > 15 deg above the horizon
BURN_MIN_BBL = 500.0         # ISB-3: > 500 bbl per burn (1994 RRT VI ISB plan)
BURN_THICK_MM = (2.0, 3.0)   # ISB-2: oil collected in fire boom to >= 2-3 mm (field check, not modelled)
BURN_WAVE_FT = 3.0           # ISB-2: waves <= 3 ft (no wave forecast in the run)
GRAND_ISLE = (-90.0291, 29.2129)  # Census 2023 gazetteer centroid of Grand Isle, LA (data/esi/gulf_places.json)
GRAND_ISLE_BUFFER_MI = 7.0   # ISB-1: 7 mi from the centre of Grand Isle when the wind is onshore
POP_BUFFER_MI = 3.0          # ISB-3: plume not toward a population centre within 3 mi
# Boom (CRS S6, ISB policy S3)
BOOM_ENTRAIN_KT = 0.70       # MECH-1: entrainment starts at 0.7 kt perpendicular to the boom
BOOM_ENTRAIN_KT_RRT6 = 0.75  # ISB-6: 0.75 kt in the RRT-6 ISB policy
BOOM_NOHOLD_KT = 1.0         # ISB-6: oil lost "at a very high rate" above 1 kt
# Lead time (33 CFR 154.1045 / App. C, S16/S15)
NOTIFY_H = 0.5               # MECH-6: notification within 30 min of discovery
MOBILIZE_H = 2.0             # MECH-6: Tier 1 mobilized and en route within 2 h of notification
TIER1_HVP_H, TIER1_OTHER_H = 6.0, 12.0  # MECH-6: Tier 1 arrival 6 h higher-volume port area, 12 h elsewhere
ROAD_MPH, WATER_KT = 35.0, 5.0          # R6: 33 CFR 154 App. C travel assumptions (35 mph land, 5 kt water)
MARSH_BOOM_DAYS = (3, 5)     # MECH-7: Galveston Bay ACP marsh-tributary protective booming 3-5 days
# Uncertainty (GNOME, S7)
WINDAGE_RANGE = (0.01, 0.04)  # FCST-4: windage 1-4 %
# Windows
WIN_HARD_AFTER_H = (24, 48)  # WIN-2: most oils hard to recover/burn/disperse after 24-48 h

# 33 CFR 154.1020 "Higher volume port area" (Gulf of Mexico entries). The regulation names the ports but gives no
# boundaries: the boxes / river line below are OilFlood's approximate extents around each named port area.
HVP_AREAS = [
    {"name": "Galveston Bay and Houston Ship Channel, TX", "bbox": (-95.35, 29.0, -94.45, 29.85)},
    {"name": "Sabine-Neches River, TX", "bbox": (-94.2, 29.6, -93.7, 30.15)},
    {"name": "Lake Charles, LA", "bbox": (-93.45, 29.7, -93.15, 30.25)},
    {"name": "Corpus Christi, TX", "bbox": (-97.5, 27.65, -96.95, 27.95)},
    {"name": "Pascagoula, MS", "bbox": (-88.65, 30.2, -88.45, 30.45)},
    {"name": "Louisiana Offshore Oil Port (LOOP), LA", "point": (-90.025, 28.885), "km": 20.0},
    {"name": "Mississippi River from Southwest Pass, LA to Baton Rouge, LA",
     "line": [(-89.43, 28.92), (-89.36, 29.29), (-89.78, 29.57), (-90.07, 29.95), (-90.55, 30.07),
              (-91.0, 30.10), (-91.19, 30.44)], "km": 15.0},
]
HVP_SOURCE = ("33 CFR 154.1020 (definition, via law.cornell.edu mirror) + 154.1045(f) Tier 1 6 h / 12 h; "
              "area extents are OilFlood approximations")


def _km(lon1, lat1, lon2, lat2):
    la = math.radians((lat1 + lat2) / 2)
    return math.hypot((lon2 - lon1) * 111.32 * math.cos(la), (lat2 - lat1) * 110.57)


def _seg_km(p, a, b):
    """distance (km) from point p to segment a-b (local flat approximation)."""
    la = math.radians(p[1])
    k = 111.32 * math.cos(la)
    ax, ay, bx, by, px, py = a[0] * k, a[1] * 110.57, b[0] * k, b[1] * 110.57, p[0] * k, p[1] * 110.57
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def hvp_area(lon: float, lat: float) -> str | None:
    for a in HVP_AREAS:
        if "bbox" in a:
            x0, y0, x1, y1 = a["bbox"]
            if x0 <= lon <= x1 and y0 <= lat <= y1:
                return a["name"]
        elif "point" in a:
            if _km(lon, lat, *a["point"]) <= a["km"]:
                return a["name"]
        else:
            ln = a["line"]
            if min(_seg_km((lon, lat), ln[i], ln[i + 1]) for i in range(len(ln) - 1)) <= a["km"]:
                return a["name"]
    return None


def lead_time(lon: float, lat: float, base: dict | None) -> dict:
    """R6: boom on scene = 0.5 h notify + 2 h mobilize + road travel from the nearest equipment base (35 mph,
    straight line), floored at the USCG Tier 1 planning time (6 h higher-volume port area / 12 h elsewhere)."""
    hvp = hvp_area(lon, lat)
    tier1 = TIER1_HVP_H if hvp else TIER1_OTHER_H
    travel = (base["km"] / (ROAD_MPH * 1.609344)) if base else None
    calc = NOTIFY_H + MOBILIZE_H + (travel or 0.0)
    return {"lead_h": round(max(tier1, calc), 1), "tier1_h": tier1, "hvp": hvp,
            "calc_h": round(calc, 1), "travel_h": None if travel is None else round(travel, 1),
            "en_route_h": NOTIFY_H + MOBILIZE_H, "base": base["name"] if base else None,
            "rule": "R6 / MECH-6 (33 CFR 154.1045: notify 30 min, en route 2 h, Tier 1 " +
                    ("6 h higher-volume port area: " + hvp if hvp else "12 h outside higher-volume port areas") + ")"}


def api_band(api: float | None) -> dict:
    if api is None:
        return {"ok": None, "text": "API gravity unknown"}
    for lo, hi, txt in API_BANDS:
        if lo <= api < hi:
            return {"ok": 17 <= api <= 45, "text": f"API {api:.1f}: {txt}", "no_need": api > 45}
    return {"ok": None, "text": f"API {api:.1f}"}


# --- data files -------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def protocols() -> dict:
    from .response import _data_file
    p = _data_file("response/protocols.json")
    return json.loads(p.read_text()) if p else {"sources": {}, "rules": [], "real_responses": []}


def src(ids: list[str]) -> list[dict]:
    S = protocols()["sources"]
    return [{"id": i, "title": S[i]["title"], "url": S[i]["url"]} for i in ids if i in S]


def rule(rid: str) -> dict | None:
    return next((r for r in protocols()["rules"] if r["id"] == rid), None)


def gate(key: str, label: str, ok: bool | None, value=None, limit: str = "", rules: tuple = (), soft=False) -> dict:
    """One go/no-go check. ok=None -> data not in the run ('check'), soft -> advisory, does not block."""
    ids = sorted({s for r in rules for s in ((rule(r) or {}).get("sources") or [])})
    return {"key": key, "label": label, "ok": None if ok is None else bool(ok), "value": value, "limit": limit, "rules": list(rules),
            "sources": ids, "soft": soft}


# --- oil properties from the ADIOS record the model ran with -----------------------------------------------

CLASS_ADIOS = {"crude_light": "LIGHT LOUISIANNA SWEET, BP", "crude_medium": "Generic Medium Crude",
               "crude_heavy": "Generic Heavy Crude", "heavy_fuel": "IFO-380LS 2014", "diesel": "Generic Diesel",
               "jet": "Generic Jet Fuel", "gasoline": "Generic Gasoline", "condensate": "Generic Condensate"}


@lru_cache(maxsize=64)
def oil_props(name: str | None, ocls: str) -> dict:
    """API gravity, pour point, max emulsion water fraction and emulsification onset from the NOAA ADIOS oil
    database (the same record OpenOil / our model ran with); class-generic ADIOS oil if the name is unknown."""
    try:
        from opendrift.models.openoil.adios import dirjs

        from .runner import resolve_oil_type
        names = dirjs.get_oil_names()
        adios = name if name in names else resolve_oil_type(name, names) if name else CLASS_ADIOS[ocls]
        o = dirjs.find_full_oil_from_name(adios)
        g = o.gnome_oil
        pp = g.get("pour_point")
        return {"adios": adios, "api": float(g["api"]) if g.get("api") is not None else None,
                "pour_point_c": None if pp is None else round(float(pp) - 273.15, 1),
                "ymax": float(o.emulsion_water_fraction_max or 0.0),
                "onset": float(o.bullwinkle) if o.bullwinkle is not None and o.bullwinkle >= 0 else 0.0,
                "density": float(o.density_at_temp(273.15 + 25.0)),
                "source": "NOAA ADIOS oil database (via OpenDrift)"}
    except Exception as e:  # noqa: BLE001 - outline must still build
        return {"adios": name, "api": None, "pour_point_c": None, "ymax": 0.0, "onset": 0.0, "density": 900.0,
                "source": f"ADIOS lookup failed ({type(e).__name__})"}


K_EM = 2.0e-6  # Mackay et al. (1980) emulsification rate (1/s), same constant as app/ourmodel/sim.py


def emulsion_series(props: dict, wind_ms: list, evap_frac: list, dt_s: float = 3600.0) -> list[float]:
    """Water fraction of the slick per hour: Mackay dY/dt = K (U+1)^2 (1 - Y/Ymax) once the evaporated fraction
    passes the ADIOS onset ('bullwinkle'); the model's own forecast wind at the slick and its oil budget."""
    y, out = 0.0, []
    ymax, onset = props["ymax"], props["onset"]
    for w, fe in zip(wind_ms, evap_frac):
        out.append(round(y, 3))
        if ymax > 0 and w is not None and (fe or 0) >= onset:
            y = min(ymax, y + dt_s * K_EM * (w + 1) ** 2 * (1 - y / ymax))
    return out


# --- precedents from real responses (protocols.json real_responses, docs/PROTOCOLS.md section 4) ---------------

# (incident index in real_responses, what_responders_did index) per tactic; texts are taken verbatim from the JSON.
_PREC = {
    "dispersant": [(0, 1), (1, 2)],
    "in_situ_burn": [(0, 2)],
    "boom": [(1, 0), (0, 3)],
    "skimming": [(1, 2), (0, 4)],
    "closure": [(1, 1)],
    "shoreline": [(1, 3), (1, 4)],
    "forecast": [(0, 0)],
}
_SHORT = {0: "Deepwater Horizon 2010", 1: "Texas City Y 2014"}


def precedents_for(kind: str) -> list[dict]:
    rr = protocols().get("real_responses") or []
    out = []
    for i, j in _PREC.get(kind, []):
        if i < len(rr) and j < len(rr[i].get("what_responders_did", [])):
            r = rr[i]
            out.append({"incident": _SHORT.get(i, r["incident"]), "text": r["what_responders_did"][j],
                        "sources": src(r.get("sources", [])[:2]), "url": (r.get("links") or [None])[0]})
    return out


def model_vs_reality() -> list[dict]:
    rr = protocols().get("real_responses") or []
    return [{"incident": _SHORT[i], "text": rr[i]["model_vs_reality"], "url": (rr[i].get("links") or [None])[0]}
            for i in (0, 1) if i < len(rr)]
