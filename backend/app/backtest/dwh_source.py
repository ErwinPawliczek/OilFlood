"""Deepwater Horizon wellhead release schedule for the backtest (docs/BACKTEST.md, "Wellhead source").

Net release to the sea = federal gross flow estimate - oil collected at the wellhead by the containment systems.

Gross flow (US DOI press release, 2010-08-02, "U.S. Scientific Teams Refine Estimates of Oil Flow from BP's Well
Prior to Capping", Flow Rate Technical Group + DOE team): 62,000 bbl/day at the beginning of the spill, 53,000 bbl/day
immediately before the well was capped (shut in 2010-07-15 14:25 CDT = 19:25 UTC), +/- ~10 %, 4.9 M bbl total of which
~800,000 bbl were captured. The release says the rate decreased over the 87 days; we interpolate linearly (assumption).
  https://www.doi.gov/news/pressreleases/US-Scientific-Teams-Refine-Estimates-of-Oil-Flow-from-BP-Well-Prior-to-Capping

Collection (bbl/day, linear between the knots; see CONTAINMENT_SRC):
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

WELL_LON, WELL_LAT = -88.3872, 28.7367   # MC252 wellhead (data/spills/catalog.json, dwh)
GROSS = [("2010-04-22T00:00:00", 62_000.0), ("2010-07-15T19:25:00", 53_000.0)]
SHUT_IN = "2010-07-15T19:25:00"
CONTAINMENT = [  # (time UTC, collected bbl/day)
    ("2010-05-16T12:00:00", 0.0), ("2010-05-17T00:00:00", 2_400.0),   # riser insertion tube: 22,000 bbl in May
    ("2010-05-25T00:00:00", 2_400.0), ("2010-05-25T12:00:00", 0.0),
    ("2010-06-03T12:00:00", 0.0), ("2010-06-04T00:00:00", 6_000.0),   # LMRP cap, first 24 h
    ("2010-06-06T00:00:00", 10_000.0),                                 # LMRP cap ~10,000/day
    ("2010-06-14T00:00:00", 15_420.0), ("2010-06-16T00:00:00", 15_420.0),
    ("2010-06-17T00:00:00", 25_295.0),                                 # LMRP 16,026 + Q4000 9,269 (June 17)
    ("2010-06-26T00:00:00", 22_750.0),                                 # LMRP 14,730 + Q4000 8,020 (June 26)
    ("2010-06-30T00:00:00", 24_000.0), ("2010-07-09T12:00:00", 24_000.0),  # ~24,000/day (DOE/DOI, June 30); held
    ("2010-07-10T12:00:00", 8_000.0), ("2010-07-15T19:25:00", 8_000.0),    # LMRP cap removed Jul 10: Q4000 only
]
CONTAINMENT_SRC = [
    "https://www.energy.gov/sites/prod/files/edg/open/documents/6.0_Item_25_Deepwater_Horizon_Containment_-_30_JUN.pdf",
    "https://www.pbs.org/newshour/science/bp-dome-capturing-more-than-10000-barrels-a-day",
    "https://maritime-executive.com/article/deepwater-horizon-update-june-15-2010",
    "https://www.webharvest.gov/congress111th/20101202090543/http://judiciary.house.gov/issues/BPUpdates/BPUpdate100618.pdf",
    "https://www.webharvest.gov/congress111th/20101202090543/http://judiciary.house.gov/issues/BPUpdates/BPupdate100628.pdf",
]
GROSS_SRC = "https://www.doi.gov/news/pressreleases/US-Scientific-Teams-Refine-Estimates-of-Oil-Flow-from-BP-Well-Prior-to-Capping"
RES_DAYS = 3.0  # outline D stands for the net release of the previous RES_DAYS days (sets the source particle count)


def _ts(s: str) -> float:
    return float(np.datetime64(datetime.fromisoformat(s), "s").astype(np.int64))


def net_knots() -> tuple[list[str], list[float]]:
    """Piecewise-linear net release (bbl/day) knots: gross - collection, 0 after shut-in."""
    gt = [_ts(t) for t, _ in GROSS]
    gr = [r for _, r in GROSS]
    ct = [_ts(t) for t, _ in CONTAINMENT]
    cr = [r for _, r in CONTAINMENT]
    t_all = sorted(set(gt + ct))
    out_t, out_r = [], []
    for t in t_all:
        out_t.append(t)
        out_r.append(max(0.0, float(np.interp(t, gt, gr) - np.interp(t, ct, cr, left=0.0, right=cr[-1]))))
    # stop the release at shut-in
    out_t.append(_ts(SHUT_IN) + 1)
    out_r.append(0.0)
    iso = [datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S") for t in out_t]
    return iso, out_r


def source_for(start: datetime, hours: float, n_seed: int) -> dict:
    """The params["source"] for a DWH run starting at `start` whose outline seed has n_seed particles. The source
    particle count is n_seed x (net release during the run) / (net release in the RES_DAYS before the start), so a
    source particle and a seed particle stand for about the same volume of oil."""
    from .. import source as srcmod
    t_iso, r = net_knots()
    src = {"lon": WELL_LON, "lat": WELL_LAT, "rate_t": t_iso, "rate_bbl_day": [round(x, 1) for x in r]}
    t0 = _ts(start.isoformat())
    run_bbl = srcmod.released_bbl(src, t0, t0 + hours * 3600)
    prev_bbl = srcmod.released_bbl(src, t0 - RES_DAYS * 86400, t0)
    src["n_particles"] = int(round(n_seed * run_bbl / prev_bbl)) if prev_bbl > 0 else 0
    return src


def total_collected_bbl() -> float:
    ct = np.array([_ts(t) for t, _ in CONTAINMENT])
    cr = np.array([r for _, r in CONTAINMENT])
    return float(np.trapezoid(cr, ct) / 86400)


if __name__ == "__main__":
    t, r = net_knots()
    for a, b in zip(t, r):
        print(a, round(b))
    print("collected total (bbl):", round(total_collected_bbl()))
    s = {"rate_t": t, "rate_bbl_day": r}
    from .. import source as srcmod
    print("net released Apr 22 - shut-in (bbl):", round(srcmod.released_bbl(s, _ts("2010-04-22T00:00:00"), _ts(SHUT_IN))))
    print(source_for(datetime(2010, 6, 1, 18), 72, 2000)["n_particles"])
