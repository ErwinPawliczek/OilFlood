"""ML drift correction: gradient-boosted trees trained on NOAA Global Drifter Program
tracks vs our forcing (see train.py and docs/OURMODEL.md).

Target: the drifter velocity the ocean model misses, r = v_drifter - v_current (m/s, east/north).
Features (all sampled from the same forcing the particle model uses):
  uc, vc   ocean-model surface current (m/s)
  uw, vw   10 m wind (m/s)
  cs, ws   current / wind speed
  vort, div  current vorticity / divergence (1e-5 1/s)
  drogued  1 = drifter still has its 15 m drogue (follows the water), 0 = drogue lost
At run time the particle model uses the mean of the drogued=1 and drogued=0 predictions as the
ocean-model current error (their wind-correlated parts cancel: -0.7 % and +0.7 % of the wind), masked by
the training coverage, and adds the oil's own 3 % wind drift on top (sim.py)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "drift_model.joblib"
CARD_PATH = HERE / "drift_model.json"
COVERAGE_PATH = HERE / "drift_coverage.npz"

# Coverage mask: the correction is only applied in 0.5 deg cells (or their 8 neighbours) that held at least
# COVER_MIN hourly training fixes; it fades linearly to 0 below that. Elsewhere (e.g. the shallow shelf, where
# no training drifter went) the model is physics only, instead of extrapolating deep-water corrections.
CELL_DEG = 0.5
COVER_MIN = 24

FEATURES = ["uc", "vc", "uw", "vw", "cs", "ws", "vort", "div", "drogued"]


def features(uc, vc, uw, vw, vort, div, drogued) -> np.ndarray:
    uc, vc, uw, vw = (np.asarray(a, float) for a in (uc, vc, uw, vw))
    n = uc.shape[0]
    return np.column_stack([
        uc, vc, uw, vw, np.hypot(uc, vc), np.hypot(uw, vw),
        np.nan_to_num(np.asarray(vort, float) * 1e5), np.nan_to_num(np.asarray(div, float) * 1e5),
        np.broadcast_to(np.asarray(drogued, float), (n,)),
    ])


@lru_cache(maxsize=1)
def load():
    """(model_u, model_v, card) or None if no trained model is shipped."""
    if not MODEL_PATH.exists():
        return None
    import joblib
    m = joblib.load(MODEL_PATH)
    card = json.loads(CARD_PATH.read_text()) if CARD_PATH.exists() else {}
    return m["u"], m["v"], card


def correction(X: np.ndarray, max_ms: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Predicted residual (du, dv) in m/s, clipped to +-max_ms (outside the training range
    trees extrapolate flat, the clip is only a guard)."""
    mdl = load()
    if mdl is None or len(X) == 0:
        z = np.zeros(len(X))
        return z, z
    mu, mv, _ = mdl
    return (np.clip(mu.predict(X), -max_ms, max_ms), np.clip(mv.predict(X), -max_ms, max_ms))


def coverage_grid(lon: np.ndarray, lat: np.ndarray, box) -> dict:
    """Counts of training fixes per CELL_DEG cell, max-filtered over the 8 neighbours."""
    x0, y0, x1, y1 = box
    nx, ny = int(round((x1 - x0) / CELL_DEG)), int(round((y1 - y0) / CELL_DEG))
    ix = np.clip(((lon - x0) / CELL_DEG).astype(int), 0, nx - 1)
    iy = np.clip(((lat - y0) / CELL_DEG).astype(int), 0, ny - 1)
    c = np.zeros((ny, nx))
    np.add.at(c, (iy, ix), 1)
    p = np.pad(c, 1)
    m = np.max([p[1 + dy:1 + dy + ny, 1 + dx:1 + dx + nx] for dy in (-1, 0, 1) for dx in (-1, 0, 1)], axis=0)
    return {"x0": x0, "y0": y0, "counts": m}


def coverage_weight(cov: dict | None, lon, lat) -> np.ndarray:
    lon, lat = np.asarray(lon, float), np.asarray(lat, float)
    if cov is None:
        return np.zeros(lon.shape)
    c = cov["counts"]
    ny, nx = c.shape
    ix = np.floor((lon - cov["x0"]) / CELL_DEG).astype(int)
    iy = np.floor((lat - cov["y0"]) / CELL_DEG).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    w = np.zeros(lon.shape)
    w[ok] = np.clip(c[iy[ok], ix[ok]] / COVER_MIN, 0, 1)
    return w


@lru_cache(maxsize=1)
def load_coverage() -> dict | None:
    if not COVERAGE_PATH.exists():
        return None
    z = np.load(COVERAGE_PATH)
    return {"x0": float(z["x0"]), "y0": float(z["y0"]), "counts": z["counts"]}


def oil_correction(uc, vc, uw, vw, vort, div, lon, lat, max_ms: float = 0.5):
    """Current-error correction for surface oil: mean of the drogued and undrogued predictions,
    times the training-coverage weight. Returns (du, dv, weight)."""
    X1 = features(uc, vc, uw, vw, vort, div, 1.0)
    X0 = features(uc, vc, uw, vw, vort, div, 0.0)
    du1, dv1 = correction(X1, max_ms)
    du0, dv0 = correction(X0, max_ms)
    w = coverage_weight(load_coverage(), lon, lat)
    return 0.5 * (du1 + du0) * w, 0.5 * (dv1 + dv0) * w, w
