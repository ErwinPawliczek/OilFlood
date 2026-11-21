"""OilFlood's own oil-spill model ("ours"). Equations and sources: docs/OURMODEL.md.

Lagrangian surface particles on the same real forcing the OpenOil baseline uses:
  advection   dx/dt = U_current + dU_ML + a_w * U_wind10           (RK2 midpoint, dt = 15 min)
  diffusion   random walk, K = K_h + K_Fay(t),  K_Fay = (1/8) d(R_Fay^2)/dt
  spreading   Fay gravity-viscous radius R = 1.45 (D g V0^2 t^1.5 / nu_w^0.5)^(1/6) until terminal thickness
  evaporation Fingas (2004): %Ev = (0.165 %D + 0.045 (T-15)) ln t   (crudes, fuel oils, gasoline, condensate)
                             %Ev = (0.0254 %D + 0.01 (T-15)) sqrt t  (diesel, jet fuel), t in minutes
  emulsion    Mackay et al. (1980): dY/dt = 2e-6 (U+1)^2 (1 - Y/Ymax), after the ADIOS onset (bullwinkle)
  dispersion  Mackay et al. (1980): fraction/h = 0.11 (U+1)^2 / (1 + 50 mu^0.5 h S)
  beaching    particle that steps onto GSHHG land (or ocean-model land) is stranded at its last water position
Oil properties (density, viscosity, distillation cuts, Ymax, onset, surface tension) come from the
same ADIOS records OpenOil uses, so the two models start from identical oil.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import ml
from .grid import Grid

MODEL_ID = "ours"
MODEL_LABEL = "OilFlood model (Lagrangian + Fay + Fingas/Mackay + ML drift correction)"
VERSION = "ours-v4"

G = 9.81
RHO_W = 1025.0
NU_W = 1.0e-6         # sea water kinematic viscosity, m2/s
T_WATER_C = 25.0      # no SST in the forcing: Gulf of Mexico surface default (see OURMODEL.md)
K_H = 10.0            # horizontal diffusivity m2/s (same as the OpenOil baseline)
WINDAGE = 0.03        # classic 3 % wind drift of surface oil (includes wave/Stokes drift; no wave model)
C4_VISC = 5.0         # Mackay viscosity increase with evaporation: mu = mu0 exp(C4 Fe)
K_EM = 2.0e-6         # Mackay emulsification rate constant (1/s)
M_LAT = 110574.0
M_LON = 111320.0


# --- oil properties ------------------------------------------------------------------------

def oil_props(name: str) -> dict:
    from opendrift.models.openoil.adios import dirjs
    o = dirjs.find_full_oil_from_name(name)
    g = o.gnome_oil
    tk = 273.15 + T_WATER_C
    rho = float(o.density_at_temp(tk))
    kvis = float(o.kvis_at_temp(tk))
    bp = np.asarray(g["boiling_point"], float)
    mf = np.asarray(g["mass_fraction"], float)
    pct_d180 = 100.0 * float(mf[bp <= 453.15].sum())        # % distilled at 180 C (Fingas input)
    f_max = float(mf[bp <= 573.15].sum())                    # evaporable: cuts boiling below 300 C
    ymax = float(o.emulsion_water_fraction_max or 0.0)
    onset = float(o.bullwinkle) if o.bullwinkle is not None and o.bullwinkle >= 0 else 0.0
    try:
        st = float(o.oil_water_surface_tension()) * 1000.0  # N/m -> dyne/cm
    except Exception:  # noqa: BLE001
        st = 24.0
    # Fingas: diesel-like middle distillates (diesel, jet fuel) follow sqrt(t); crudes, fuel oils and the
    # very volatile products (gasoline, condensate: mostly distilled by 180 C) follow ln(t).
    light = ymax == 0.0 and kvis < 20e-6 and pct_d180 < 60
    return {"name": name, "density": rho, "kvis": kvis, "mu_cp": kvis * rho * 1000.0, "pct_d180": pct_d180,
            "f_evap_max": f_max, "ymax": ymax, "emul_onset": onset, "surface_tension": st,
            "evap_law": "sqrt" if light else "ln",
            "h_terminal": 1e-4 if kvis > 1e-4 else 1e-5}


def fingas_fraction(p: dict, t_s: float) -> float:
    t_min = max(t_s / 60.0, 1.0)
    if p["evap_law"] == "sqrt":
        pct = (0.0254 * p["pct_d180"] + 0.01 * (T_WATER_C - 15)) * math.sqrt(t_min)
    else:
        pct = (0.165 * p["pct_d180"] + 0.045 * (T_WATER_C - 15)) * math.log(t_min)
    return float(min(max(pct / 100.0, 0.0), p["f_evap_max"]))


def fay_radius(v0: float, delta: float, t_s: float) -> float:
    """Fay (1971) gravity-inertia then gravity-viscous radius (m)."""
    t_s = max(t_s, 1.0)
    r_gi = 1.14 * (delta * G * v0 * t_s ** 2) ** 0.25
    r_gv = 1.45 * (delta * G * v0 ** 2 * t_s ** 1.5 / NU_W ** 0.5) ** (1 / 6)
    return min(r_gi, r_gv)


# --- the run -----------------------------------------------------------------------------------

def run(run_dir: Path, p: dict, status) -> dict:
    """Run our model for normalized/planned params `p` (jobs.plan). Returns the tracks payload."""
    from roaring_landmask import RoaringLandmask

    from .. import config

    t_wall = time.time()
    rng = np.random.default_rng(int(abs(hash((p["lon"], p["lat"], p["start_time"]))) % 2**32))
    start = datetime.fromisoformat(p["start_time"])
    dur_s = float(p["duration_h"]) * 3600
    dt = config.TIME_STEP_MIN * 60.0
    out_dt = config.OUTPUT_STEP_MIN * 60.0
    t0 = float(np.datetime64(start, "s").astype(np.int64))

    files = p["forcing_files"]
    cur_f = next(f for f in files if "current" in f["kinds"])
    wind_f = next(f for f in files if "wind" in f["kinds"])
    bb = (p["lon"] - 6, p["lat"] - 5, p["lon"] + 6, p["lat"] + 5)
    t_end = start + timedelta(seconds=dur_s)
    cur = Grid(cur_f["path"], "current", bb, start, t_end)
    wind = Grid(wind_f["path"], "wind", bb, start, t_end)
    land = RoaringLandmask.new()

    oil_name = p["_oil_resolved"]
    op = oil_props(oil_name)
    use_ml = bool(p.get("ml", True)) and ml.load() is not None
    windage = WINDAGE

    n = int(p["n_particles"])
    v0 = float(p["volume_m3"])
    m_total = v0 * op["density"]
    m0 = np.full(n, m_total / n)
    delta = max((RHO_W - op["density"]) / RHO_W, 0.01)

    # Seed: uniform disc of the initial radius around the (snapped) spill point.
    r0 = max(float(p.get("radius_m", 50)), fay_radius(v0, delta, dt))
    rr = r0 * np.sqrt(rng.random(n))
    th = 2 * np.pi * rng.random(n)
    lat = p["lat"] + rr * np.sin(th) / M_LAT
    lon = p["lon"] + rr * np.cos(th) / (M_LON * np.cos(np.radians(p["lat"])))

    mass = m0.copy()           # surface mass per particle (kg)
    evap = np.zeros(n)
    disp = np.zeros(n)
    strd = np.zeros(n)         # stranded mass
    water = np.zeros(n)        # emulsion water fraction Y
    state = np.zeros(n, np.int8)   # 0 active, 1 stranded, 2 no_data
    visible = np.ones(n, bool)
    vis_end = np.empty(n, dtype=object)
    vis_u = rng.random(n)      # particle disappears when its lost mass fraction passes this

    n_out = int(round(dur_s / out_dt)) + 1
    times = [int(t0 + i * out_dt) for i in range(n_out)]
    paths = [[] for _ in range(n)]
    stamps = [[] for _ in range(n)]
    budget = []
    ml_mag = []
    ml_cov = []

    def record(k_out: int):
        tt = times[k_out]
        for i in np.nonzero(visible)[0]:
            paths[i].append([round(float(lon[i]), 4), round(float(lat[i]), 4)])
            stamps[i].append(tt)
        budget.append({"t": tt, "surface": round(float(mass[state != 1].sum()), 2),
                       "evaporated": round(float(evap.sum()), 2), "dispersed": round(float(disp.sum()), 2),
                       "stranded": round(float(strd.sum()), 2)})

    def velocity(t, lo, la, corr):
        uc, vc = cur.sample(t, lo, la)
        uw, vw = wind.sample(t, lo, la)
        uw, vw = np.nan_to_num(uw), np.nan_to_num(vw)
        return uc + corr[0] + windage * uw, vc + corr[1] + windage * vw, uc, vc, uw, vw

    record(0)
    n_steps = int(round(dur_s / dt))
    steps_per_out = max(1, int(round(out_dt / dt)))
    fe_prev = 0.0
    last_status = 0.0
    for k in range(1, n_steps + 1):
        t = t0 + (k - 1) * dt
        age = (k - 1) * dt
        act = state == 0
        idx = np.nonzero(act)[0]
        if len(idx):
            lo, la = lon[idx], lat[idx]
            uc, vc = cur.sample(t, lo, la)
            uw, vw = wind.sample(t, lo, la)
            uw, vw = np.nan_to_num(uw), np.nan_to_num(vw)
            if use_ml:
                vo, dv = cur.gradients(t, lo, la)
                cu_, cv_, wcov = ml.oil_correction(np.nan_to_num(uc), np.nan_to_num(vc), uw, vw, vo, dv, lo, la)
                corr = (cu_, cv_)
                ml_mag.append(float(np.nanmean(np.hypot(cu_, cv_))))
                ml_cov.append(float(np.mean(wcov > 0)))
            else:
                corr = (np.zeros(len(idx)), np.zeros(len(idx)))
            # RK2 midpoint
            u1 = np.nan_to_num(uc) + corr[0] + windage * uw
            v1 = np.nan_to_num(vc) + corr[1] + windage * vw
            lo_m = lo + 0.5 * dt * u1 / (M_LON * np.cos(np.radians(la)))
            la_m = la + 0.5 * dt * v1 / M_LAT
            u2, v2, *_ = velocity(t + 0.5 * dt, lo_m, la_m, corr)
            u2 = np.where(np.isfinite(u2), u2, u1)
            v2 = np.where(np.isfinite(v2), v2, v1)
            # Diffusion: background + Fay spreading as an equivalent diffusivity while the slick is thick.
            r_a, r_b = fay_radius(v0, delta, age), fay_radius(v0, delta, age + dt)
            v_surf = mass[state != 1].sum() / op["density"]
            h = v_surf / (math.pi * r_a ** 2)
            k_fay = (r_b ** 2 - r_a ** 2) / (8 * dt) if h > op["h_terminal"] else 0.0
            sig = math.sqrt(2 * (K_H + k_fay) * dt)
            dx = u2 * dt + sig * rng.standard_normal(len(idx))
            dy = v2 * dt + sig * rng.standard_normal(len(idx))
            nlo = lo + dx / (M_LON * np.cos(np.radians(la)))
            nla = la + dy / M_LAT
            # Coast / domain checks.
            outside = ~cur.inside(nlo, nla)
            on_land = np.zeros(len(idx), bool)
            ins = ~outside
            if ins.any():
                on_land[ins] = land.contains_many(nlo[ins].astype(np.float64), nla[ins].astype(np.float64))
                cu, _ = cur.sample(t + dt, nlo[ins], nla[ins])
                on_land[ins] |= ~np.isfinite(cu)  # ocean-model coastline (e.g. bays HYCOM treats as land)
            moved = ~outside & ~on_land
            lon[idx[moved]], lat[idx[moved]] = nlo[moved], nla[moved]
            sidx = idx[on_land]
            state[sidx] = 1
            strd[sidx] += mass[sidx]
            mass[sidx] = 0.0  # stranded mass leaves the surface budget
            state[idx[outside]] = 2

            # --- weathering of surface oil (active + out-of-domain particles keep weathering) -----
            wsp = np.hypot(uw, vw)
            fe = fingas_fraction(op, age + dt)
            surf = np.nonzero(state != 1)[0]
            if fe > fe_prev and fe_prev < 1:
                de = mass[surf] * (fe - fe_prev) / (1 - fe_prev)
                mass[surf] -= de
                evap[surf] += de
            fe_prev = fe
            # wind at each surface particle (out-of-domain ones use the slick mean)
            w_all = np.full(n, float(np.mean(wsp)) if len(wsp) else 5.0)
            w_all[idx] = wsp
            ws = w_all[surf]
            if op["ymax"] > 0 and fe >= op["emul_onset"]:
                water[surf] += dt * K_EM * (ws + 1) ** 2 * (1 - water[surf] / op["ymax"])
                water[surf] = np.minimum(water[surf], op["ymax"])
            y = water[surf]
            mu = op["mu_cp"] * math.exp(C4_VISC * fe) * np.exp(2.5 * y / (1 - 0.654 * y))
            h_cm = max(h, op["h_terminal"]) * 100.0
            frac_h = 0.11 * (ws + 1) ** 2 / (1 + 50 * np.sqrt(mu) * h_cm * op["surface_tension"])
            dd = mass[surf] * np.minimum(frac_h * dt / 3600.0, 1.0)
            mass[surf] -= dd
            disp[surf] += dd

        # Visibility: a particle stands for its share of surface oil; it fades out once that is gone.
        lost = 1 - (mass + strd) / m0
        gone = visible & (state == 0) & (lost > vis_u)
        for i in np.nonzero(gone)[0]:
            vis_end[i] = "evaporated" if evap[i] >= disp[i] else "dispersed"
        visible &= ~gone

        if k % steps_per_out == 0:
            record(k // steps_per_out)
        now = time.time()
        if now - last_status > 0.5:
            last_status = now
            status(progress=round(min(k / n_steps, 0.99), 3))

    particles = []
    for i in range(n):
        if not paths[i]:
            continue
        if state[i] == 1:
            st = "stranded"
        elif state[i] == 2:
            st = "no_data"
        elif not visible[i]:
            st = vis_end[i] or "evaporated"
        else:
            st = "active"
        particles.append({"path": paths[i], "timestamps": stamps[i], "status_end": st})

    return {
        "times": times,
        "particles": particles,
        "budget": budget,
        "model_extra": {
            "oil_properties": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in op.items()},
            "ml_correction": use_ml,
            "ml_mean_correction_ms": round(float(np.mean(ml_mag)), 4) if ml_mag else 0.0,
            # share of particle-steps inside the drifter training coverage (where the correction applies)
            "ml_coverage_fraction": round(float(np.mean(ml_cov)), 3) if ml_cov else 0.0,
            "windage": windage,
            "water_temp_c": T_WATER_C,
            "emulsion_water_fraction_end": round(float(np.mean(water)), 3),
            "runtime_s": round(time.time() - t_wall, 1),
        },
    }
