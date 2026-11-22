"""Optional continuous point source (e.g. a leaking wellhead) shared by both models.

A run's params may carry
    "source": {"lon": .., "lat": .., "rate_t": [iso times], "rate_bbl_day": [..], "n_particles": N}
The release rate is piecewise linear between the (rate_t, rate_bbl_day) knots (constant outside them). The N source
particles are released at the wellhead at times spread over the run so that equal particle counts carry equal
released volume (deterministic: quantiles (i + 0.5) / N of the cumulative release). Without "source" nothing changes
(run hashes and results of existing runs are untouched). Used by the DWH backtest (docs/BACKTEST.md).
"""
from __future__ import annotations

from datetime import datetime

import numpy as np

BBL_M3 = 0.158987


def _knots(src: dict) -> tuple[np.ndarray, np.ndarray]:
    t = np.array([np.datetime64(datetime.fromisoformat(s[:19]), "s").astype(np.int64) for s in src["rate_t"]], float)
    r = np.asarray(src["rate_bbl_day"], float)
    return t, r


def _cum_bbl(src: dict, ts: np.ndarray) -> np.ndarray:
    """Cumulative released volume (bbl) at unix times ts, from the first knot (trapezoid on a fine grid)."""
    kt, kr = _knots(src)
    lo, hi = min(kt[0], ts.min()), max(kt[-1], ts.max())
    grid = np.unique(np.concatenate([np.arange(lo, hi + 600, 600.0), kt, ts]))
    rate = np.interp(grid, kt, kr) / 86400.0  # bbl/s
    cum = np.concatenate([[0.0], np.cumsum(0.5 * (rate[1:] + rate[:-1]) * np.diff(grid))])
    return np.interp(ts, grid, cum)


def released_bbl(src: dict, t_a: float, t_b: float) -> float:
    c = _cum_bbl(src, np.array([t_a, t_b], float))
    return float(c[1] - c[0])


def release_times(src: dict, t0: float, dur_s: float) -> np.ndarray:
    """Unix release time of each source particle (sorted), within [t0, t0 + dur_s)."""
    n = int(src["n_particles"])
    grid = np.arange(t0, t0 + dur_s + 1, 300.0)
    cum = _cum_bbl(src, grid)
    cum = cum - cum[0]
    if n <= 0 or cum[-1] <= 0:
        return np.zeros(0)
    q = (np.arange(n) + 0.5) / n * cum[-1]
    return np.interp(q, cum, grid)


def volume_m3_per_particle(src: dict, t0: float, dur_s: float) -> float:
    n = int(src["n_particles"])
    return released_bbl(src, t0, t0 + dur_s) * BBL_M3 / max(n, 1)
