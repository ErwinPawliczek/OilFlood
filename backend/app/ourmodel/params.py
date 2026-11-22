"""User-facing parameters of our model ("build your own model") and the ensemble perturbation design.

Schema served by GET /api/model/params; validated params go into the run hash (jobs._normalize).
Equations and sources for every entry: docs/METHODOLOGY.md.
"""
from __future__ import annotations

from . import sim

PARAMS = [
    {"name": "windage_pct", "label": "Wind drift (windage)", "type": "number", "unit": "% of U10",
     "min": 0.0, "max": 6.0, "step": 0.1, "default": round(100 * sim.WINDAGE, 2), "group": "transport",
     "description": "Share of the 10 m wind velocity added to the oil's drift (direct wind drag on the slick; with "
                    "Stokes off it also stands in for wave-induced drift). NOAA uses 1-4 %, the classic rule is 3 %.",
     "citation": "ASCE Task Committee (1996) J. Hydraulic Eng. 122(11); NOAA GNOME windage 1-4 % "
                 "(docs/PROTOCOLS.md FCST-4)"},
    {"name": "diffusivity_m2s", "label": "Horizontal diffusivity K", "type": "number", "unit": "m²/s",
     "min": 0.0, "max": 100.0, "step": 1.0, "default": sim.K_H, "group": "transport",
     "description": "Random-walk diffusivity for unresolved turbulence and sub-grid currents: each step a particle moves "
                    "sqrt(2 K dt) per axis at random. Fay spreading adds to it while the slick is thick.",
     "citation": "GNOME default 1e5 cm²/s = 10 m²/s (NOAA ORR GNOME technical documentation); OpenDrift default"},
    {"name": "current_factor", "label": "Current multiplier", "type": "number", "unit": "x",
     "min": 0.0, "max": 2.0, "step": 0.05, "default": 1.0, "group": "transport",
     "description": "Scales the ocean-model surface current (RTOFS forecast / HYCOM reanalysis). <1 if you think the "
                    "model overestimates the current, 0 = wind + diffusion only.",
     "citation": "Sensitivity knob; GNOME 'current scaling' (NOAA ORR GNOME user manual)"},
    {"name": "stokes", "label": "Stokes drift (wave)", "type": "bool", "min": None, "max": None, "step": None,
     "default": False, "group": "transport",
     "description": "Adds a wave Stokes drift of 1.3 % of the 10 m wind, downwind (fully developed sea, no wave model). "
                    "If you turn it on, lower windage to ~1.5-2 % so the wave part is not counted twice "
                    "(OpenOil uses 2 % + Stokes).",
     "citation": "Ardhuin et al. (2009) J. Phys. Oceanogr. 39, 2820-2838 (surface Stokes drift ~1.3 % of U10 "
                 "for developed seas)"},
    {"name": "evaporation", "label": "Evaporation", "type": "bool", "min": None, "max": None, "step": None,
     "default": True, "group": "weathering",
     "description": "Fingas evaporation from the oil's ADIOS distillation curve.",
     "citation": "Fingas (2004) J. Hazard. Mater. 107, 27-36"},
    {"name": "emulsification", "label": "Emulsification", "type": "bool", "min": None, "max": None, "step": None,
     "default": True, "group": "weathering",
     "description": "Water uptake (mousse) and the resulting viscosity rise, which slows natural dispersion.",
     "citation": "Mackay, Buist, Mascarenhas & Paterson (1980) EPS 3-EC-80-8"},
    {"name": "dispersion", "label": "Natural dispersion", "type": "bool", "min": None, "max": None, "step": None,
     "default": True, "group": "weathering",
     "description": "Breaking waves mix oil droplets into the water column (removed from the surface).",
     "citation": "Mackay et al. (1980) EPS 3-EC-80-8"},
    {"name": "beaching", "label": "Beaching (stranding)", "type": "bool", "min": None, "max": None, "step": None,
     "default": True, "group": "weathering",
     "description": "Oil that reaches the coastline (GSHHG) strands there. Off: particles stop at the coast and stay "
                    "afloat (worst case for coastal water, no shoreline loading).",
     "citation": "GSHHG coastline (Wessel & Smith 1996) via roaring_landmask, as in OpenDrift"},
    {"name": "ml", "label": "ML drift correction (experimental)", "type": "bool", "min": None, "max": None,
     "step": None, "default": False, "group": "transport",
     "description": "Gradient-boosted correction learned from NOAA drifters. Off by default: no gain in the oil "
                    "backtest (docs/BACKTEST.md).",
     "citation": "NOAA AOML Global Drifter Program hourly QC; docs/OURMODEL.md"},
    {"name": "particles", "label": "Particles", "type": "int", "unit": "particles", "min": 100, "max": 10000,
     "step": 100, "default": 2000, "group": "numerics",
     "description": "Lagrangian elements in the control run. More = smoother map, slower. Ensemble members use "
                    "min(particles, 1000).",
     "citation": "Monte-Carlo particle method (e.g. Zelenke et al. 2012, GNOME technical documentation)"},
    {"name": "horizon_h", "label": "Forecast horizon", "type": "number", "unit": "h", "min": 6.0, "max": 120.0,
     "step": 3.0, "default": 72.0, "group": "numerics",
     "description": "Hours to simulate (clipped to the end of the live forecast).",
     "citation": "NOAA trajectory forecasts typically cover 24-72 h and are re-run daily (PROTOCOLS.md FCST-3)"},
]
BY_NAME = {p["name"]: p for p in PARAMS}
GROUPS = [{"id": "transport", "label": "Transport"}, {"id": "weathering", "label": "Weathering"},
          {"id": "numerics", "label": "Run"}]
# Physics params that sim.run reads (particles / horizon map onto n_particles / duration_h; ml onto `ml`).
PHYSICS = ("windage_pct", "diffusivity_m2s", "current_factor", "stokes", "evaporation", "emulsification",
           "dispersion", "beaching")
STOKES_FACTOR = sim.STOKES_FACTOR

# --- ensemble design (docs/METHODOLOGY.md "Ensemble") ---------------------------------------------------
ENS_MAX = 30
ENS_DEFAULT = 20
ENS_SEED = 1
MEMBER_PARTICLES = 1000
OUTPUT_STEP_H = 3
GRID_DEG = 0.05
PERTURBATIONS = [
    {"name": "windage_pct", "dist": "uniform", "low": 1.0, "high": 4.0,
     "description": "Wind drift drawn uniformly in NOAA's 1-4 % range (replaces the user windage).",
     "citation": "NOAA GNOME minimum-regret windage 1-4 % (PROTOCOLS.md FCST-2, FCST-4)"},
    {"name": "wind_rot_deg", "dist": "normal", "sigma": 20.0,
     "description": "The whole wind field is rotated by a constant angle per member: GFS 10 m wind direction error. "
                    "No GEFS spread is cached on the server, so sigma is a stated value, not a forecast spread.",
     "citation": "Stated sigma; order of GFS 10 m wind direction RMSE over the ocean at 1-3 day lead (~20-30 deg)"},
    {"name": "current_factor", "dist": "normal", "mean": 1.0, "sigma": 0.2, "clip": [0.5, 1.5],
     "description": "Current speed multiplied by a factor (x the user's current multiplier).",
     "citation": "Stated: model surface current speed errors of order 20-30 % vs drifters (docs/OURMODEL.md hold-out)"},
    {"name": "diffusivity_m2s", "dist": "lognormal", "median": "user value", "factor": 2.0,
     "description": "K multiplied by exp(N(0, ln 2)): a factor-of-2 spread around the user's K.",
     "citation": "Stated; K of 1-100 m²/s spans the GNOME/OpenDrift range for 1-10 km scales (Okubo 1971)"},
]


def defaults() -> dict:
    return {p["name"]: p["default"] for p in PARAMS}


def validate(raw: dict | None) -> dict:
    """User params -> full effective dict (defaults filled). Raises ValueError with a user-facing message."""
    out = defaults()
    for k, v in (raw or {}).items():
        spec = BY_NAME.get(k)
        if spec is None:
            raise ValueError(f"Unknown model param {k!r}. Valid: {', '.join(BY_NAME)}")
        if spec["type"] == "bool":
            if not isinstance(v, bool):
                raise ValueError(f"{k} must be true or false")
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"{k} must be a number")
            if not spec["min"] <= v <= spec["max"]:
                raise ValueError(f"{k}={v} is outside {spec['min']}..{spec['max']} {spec.get('unit') or ''}".strip())
            v = int(round(v)) if spec["type"] == "int" else round(float(v), 4)
        out[k] = v
    return out


def physics(eff: dict) -> dict:
    """The subset sim.run reads, only where it differs from the default (so default runs hash as before)."""
    d = defaults()
    return {k: eff[k] for k in PHYSICS if eff[k] != d[k]}


def schema() -> dict:
    return {"model": sim.MODEL_ID, "version": sim.VERSION, "params": PARAMS, "groups": GROUPS,
            "ensemble": {"max_members": ENS_MAX, "default_members": ENS_DEFAULT, "seed_default": ENS_SEED,
                         "member_particles": MEMBER_PARTICLES, "output_step_h": OUTPUT_STEP_H,
                         "grid_deg": GRID_DEG, "perturbations": PERTURBATIONS},
            "doc": "docs/METHODOLOGY.md"}


def draw_members(n: int, seed: int, eff: dict) -> list[dict]:
    """Reproducible perturbations for n members around the user's effective params."""
    import numpy as np
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n):
        wp = float(rng.uniform(1.0, 4.0))
        rot = float(rng.normal(0.0, 20.0))
        cf = float(np.clip(rng.normal(1.0, 0.2), 0.5, 1.5)) * float(eff["current_factor"])
        kh = float(eff["diffusivity_m2s"]) * float(np.exp(rng.normal(0.0, np.log(2.0))))
        out.append({"member": k, "windage_pct": round(wp, 3), "wind_rot_deg": round(rot, 2),
                    "current_factor": round(cf, 4), "diffusivity_m2s": round(kh, 3),
                    "rng_seed": int(seed) * 1000 + k})
    return out
