"""ML drift correction: gradient-boosted trees trained on NOAA Global Drifter Program
tracks vs our forcing (see train.py and docs/OURMODEL.md).

Target: the drifter velocity the ocean model misses, r = v_drifter - v_current (m/s, east/north).
Features (all sampled from the same forcing the particle model uses):
  uc, vc   ocean-model surface current (m/s)
  uw, vw   10 m wind (m/s)
  cs, ws   current / wind speed
  vort, div  current vorticity / divergence (1e-5 1/s)
  drogued  1 = drifter still has its 15 m drogue (follows the water), 0 = drogue lost
At run time the particle model asks for drogued=1, i.e. the corrected *water* velocity,
and adds the oil's own wind drift (3 %) on top (sim.py)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "drift_model.joblib"
CARD_PATH = HERE / "drift_model.json"

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
