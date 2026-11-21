"""Train the ML drift correction on NOAA Global Drifter Program (GDP) hourly tracks.

    cd backend && ~/miniforge3/envs/oilflood/bin/python -m app.ourmodel.train [--refetch]

1. For every cached hindcast forcing window (data/cache/forcing/<window>/{currents,winds}.nc,
   HYCOM-TSIS GOMb0.04 reanalysis currents + winds) download the GDP hourly QC drifter fixes inside
   the Gulf box and window from NOAA AOML ERDDAP (dataset drifter_hourly_qc) -> data/cache/gdp/<window>.csv
2. Smooth drifter velocities (6 h centred mean), sample currents/winds/vorticity/divergence at each fix.
3. Grouped 5-fold cross-validation by drifter ID (a drifter is never in train and test at once):
   velocity RMSE and 24 h / 48 h trajectory separation for current-only vs current+ML.
4. Fit on all drifters, write drift_model.joblib + drift_model.json (model card with the held-out scores).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config
from . import ml
from .grid import Grid

ERDDAP = "https://erddap.aoml.noaa.gov/gdp/erddap/tabledap/drifter_hourly_qc.csv"
BOX = (-98.0, 18.0, -80.0, 31.0)
M_LAT = 110574.0
M_LON = 111320.0


def windows() -> list[dict]:
    out = []
    for d in config.DATA_DIRS:
        root = d / "cache" / "forcing"
        if not root.is_dir():
            continue
        for w in sorted(root.iterdir()):
            if w.name == "live" or not (w / "currents.nc").exists() or not (w / "winds.nc").exists():
                continue
            out.append({"name": w.name, "cur": str(w / "currents.nc"), "wind": str(w / "winds.nc")})
        if out:
            break
    return out


def gdp_dir() -> Path:
    return config.DATA_DIRS[0] / "cache" / "gdp"


def fetch_gdp(name: str, t0: str, t1: str, refetch: bool = False) -> pd.DataFrame | None:
    out = gdp_dir() / f"{name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    if refetch or not out.exists():
        q = (f"ID,time,latitude,longitude,ve,vn,err_ve,err_vn,drogue_lost_date"
             f"&time%3E={t0}&time%3C={t1}&latitude%3E={BOX[1]}&latitude%3C={BOX[3]}"
             f"&longitude%3E={BOX[0]}&longitude%3C={BOX[2]}")
        url = f"{ERDDAP}?{q}"
        print(f"  GET {url}", file=sys.stderr)
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                out.write_bytes(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:  # ERDDAP: no rows
                out.write_text("")
            else:
                raise
    if out.stat().st_size == 0:
        return None
    df = pd.read_csv(out, skiprows=[1])
    return df if len(df) else None


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    dl = pd.to_datetime(df["drogue_lost_date"], utc=True, errors="coerce")
    df["drogued"] = (~(dl.notna() & (df["time"] >= dl))).astype(float)
    df = df.sort_values(["ID", "time"]).reset_index(drop=True)
    parts = []
    for _, g in df.groupby("ID"):
        g = g.set_index("time").asfreq("1h")  # gaps -> NaN rows
        g["ID"] = g["ID"].ffill().bfill()
        sm = g[["ve", "vn"]].rolling(6, center=True, min_periods=6).mean()
        g["ve_s"], g["vn_s"] = sm["ve"], sm["vn"]
        parts.append(g.reset_index())
    out = pd.concat(parts, ignore_index=True)
    out["ts"] = out["time"].astype("int64") // 10**9
    return out


def sample(df: pd.DataFrame, cur: Grid, wind: Grid) -> pd.DataFrame:
    t, lo, la = df["ts"].values.astype(float), df["longitude"].values, df["latitude"].values
    df = df.copy()
    df["uc"], df["vc"] = cur.sample(t, lo, la)
    df["uw"], df["vw"] = wind.sample(t, lo, la)
    df["vort"], df["div"] = cur.gradients(t, lo, la)
    return df


def X_of(df: pd.DataFrame, drogued=None) -> np.ndarray:
    return ml.features(df.uc.values, df.vc.values, df.uw.values, df.vw.values, df.vort.values, df["div"].values,
                       df.drogued.values if drogued is None else drogued)


def make_models():
    from sklearn.ensemble import HistGradientBoostingRegressor
    kw = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=200,
              l2_regularization=1.0, random_state=0)
    return HistGradientBoostingRegressor(**kw), HistGradientBoostingRegressor(**kw)


def integrate(starts: pd.DataFrame, grids: dict, hours: int, method: str, models=None, alpha=0.0,
              dt_s: float = 1800.0, cov: dict | None = None) -> dict[int, np.ndarray]:
    """Advect virtual drifters from `starts` (rows with ts/lon/lat/window/drogued) for `hours`.
    method: 'current' | 'current+wind' (alpha) | 'ml'. Returns {hour: (lon, lat)} at 24 h multiples."""
    res = {}
    lon = starts["longitude"].values.astype(float).copy()
    lat = starts["latitude"].values.astype(float).copy()
    t = starts["ts"].values.astype(float).copy()
    dro = starts["drogued"].values.astype(float)
    win = starts["window"].values
    dead = np.zeros(len(lon), bool)

    def vel(t, lon, lat):
        u = np.full(len(lon), np.nan)
        v = np.full(len(lon), np.nan)
        for w in np.unique(win):
            m = win == w
            cur, wnd = grids[w]
            uc, vc = cur.sample(t[m], lon[m], lat[m])
            uu, vv = uc.copy(), vc.copy()
            if method == "current+wind":
                uw, vw = wnd.sample(t[m], lon[m], lat[m])
                uu, vv = uc + alpha * uw, vc + alpha * vw
            elif method == "ml":
                uw, vw = wnd.sample(t[m], lon[m], lat[m])
                vo, dv = cur.gradients(t[m], lon[m], lat[m])
                X = ml.features(np.nan_to_num(uc), np.nan_to_num(vc), np.nan_to_num(uw), np.nan_to_num(vw),
                                vo, dv, dro[m])
                wc = ml.coverage_weight(cov, lon[m], lat[m])  # same coverage rule as at run time
                uu, vv = uc + wc * models[0].predict(X), vc + wc * models[1].predict(X)
            u[m], v[m] = uu, vv
        return u, v

    n_steps = int(hours * 3600 / dt_s)
    for k in range(1, n_steps + 1):
        u1, v1 = vel(t, lon, lat)
        lo2 = lon + 0.5 * dt_s * u1 / (M_LON * np.cos(np.radians(lat)))
        la2 = lat + 0.5 * dt_s * v1 / M_LAT
        u2, v2 = vel(t + 0.5 * dt_s, lo2, la2)
        bad = ~np.isfinite(u2) | ~np.isfinite(v2)
        dead |= bad  # left the forcing / hit model land: freeze (counts as a miss below)
        u2, v2 = np.where(bad, 0, u2), np.where(bad, 0, v2)
        lon = lon + dt_s * u2 / (M_LON * np.cos(np.radians(lat)))
        lat = lat + dt_s * v2 / M_LAT
        t = t + dt_s
        if (k * dt_s) % (24 * 3600) == 0:
            res[int(k * dt_s // 3600)] = (lon.copy(), lat.copy(), dead.copy())
    return res


def km(lo1, la1, lo2, la2):
    return np.hypot((lo1 - lo2) * 111.32 * np.cos(np.radians(la1)), (la1 - la2) * 110.574)


def rmse(du, dv):
    return float(np.sqrt(np.nanmean(du ** 2 + dv ** 2)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--folds", type=int, default=5)
    a = ap.parse_args()
    t_wall = time.time()

    frames, grids = [], {}
    for w in windows():
        cur = Grid(w["cur"], "current")
        wind = Grid(w["wind"], "wind")
        t0 = datetime.fromtimestamp(cur.t[0], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        t1 = datetime.fromtimestamp(cur.t_end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        raw = fetch_gdp(w["name"], t0, t1, a.refetch)
        n_raw = 0 if raw is None else len(raw)
        print(f"{w['name']}: {t0}..{t1}  GDP fixes {n_raw}", file=sys.stderr)
        if raw is None:
            continue
        grids[w["name"]] = (cur, wind)
        df = sample(prepare(raw), cur, wind)
        df["window"] = w["name"]
        frames.append(df)
    allpts = pd.concat(frames, ignore_index=True)
    allpts["ID"] = allpts["ID"].astype(np.int64)
    train = allpts.dropna(subset=["ve_s", "vn_s", "uc", "vc", "uw", "vw"]).reset_index(drop=True)
    train["ru"], train["rv"] = train.ve_s - train.uc, train.vn_s - train.vc
    ids = np.sort(train.ID.unique())
    print(f"training rows {len(train)}  drifters {len(ids)}  "
          f"(drogued {train.drogued.mean():.0%} of rows)", file=sys.stderr)

    # --- grouped K-fold by drifter --------------------------------------------------------
    rng = np.random.default_rng(0)
    perm = rng.permutation(ids)
    fold_of = {d: i % a.folds for i, d in enumerate(perm)}
    train["fold"] = train.ID.map(fold_of)
    allpts["fold"] = allpts.ID.map(fold_of)
    oof_u = np.full(len(train), np.nan)
    oof_v = np.full(len(train), np.nan)
    alpha_fold = {}
    traj_rows = []
    for k in range(a.folds):
        tr, te = train.fold != k, train.fold == k
        if te.sum() == 0:
            continue
        mu, mv = make_models()
        Xtr = X_of(train[tr])
        mu.fit(Xtr, train.ru[tr])
        mv.fit(Xtr, train.rv[tr])
        Xte = X_of(train[te])
        oof_u[te.values] = mu.predict(Xte)
        oof_v[te.values] = mv.predict(Xte)
        # Best constant windage on the training folds' undrogued drifters (a fair physics baseline).
        und = train[tr & (train.drogued == 0)]
        W2 = (und.uw ** 2 + und.vw ** 2).sum()
        alpha_fold[k] = float((und.ru * und.uw + und.rv * und.vw).sum() / W2) if W2 > 0 else 0.0

        # Trajectory test: virtual drifters released every 12 h along each held-out track.
        te_pts = allpts[(allpts.fold == k) & allpts.longitude.notna()].copy()
        te_pts = te_pts[(te_pts.ts % (12 * 3600)) == 0]
        if len(te_pts) == 0:
            continue
        obs = allpts.dropna(subset=["longitude"]).set_index(["ID", "ts"])[["longitude", "latitude"]]
        runs = {
            "current": integrate(te_pts, grids, 48, "current"),
            "current+wind": integrate(te_pts, grids, 48, "current+wind", alpha=alpha_fold[k]),
            "ml": integrate(te_pts, grids, 48, "ml", models=(mu, mv),
                            cov=ml.coverage_grid(train[tr].longitude.values, train[tr].latitude.values, BOX)),
        }
        for h in (24, 48):
            key = list(zip(te_pts.ID.values, te_pts.ts.values + h * 3600))
            o = obs.reindex(key)
            ok = o.longitude.notna().values
            row = {"fold": k, "h": h, "n": int(ok.sum())}
            for name, r in runs.items():
                lo, la, dead = r[h]
                d = km(lo, la, o.longitude.values, o.latitude.values)
                row[name] = d[ok]
                row[name + "_dead"] = dead[ok]
            row["drogued"] = te_pts.drogued.values[ok]
            traj_rows.append(row)
        print(f"  fold {k}: test drifters {train[te].ID.nunique()} rows {te.sum()} alpha {alpha_fold[k]:.4f}",
              file=sys.stderr)

    # --- held-out scores ---------------------------------------------------------------
    def vel_scores(mask):
        s = train[mask]
        al = s.fold.map(alpha_fold).values
        return {
            "rows": int(mask.sum()),
            "drifters": int(s.ID.nunique()),
            "rmse_current_only": round(rmse(s.ru.values, s.rv.values), 4),
            "rmse_current_plus_windage": round(rmse(s.ru.values - al * s.uw.values, s.rv.values - al * s.vw.values), 4),
            "rmse_ml": round(rmse(s.ru.values - oof_u[mask.values], s.rv.values - oof_v[mask.values]), 4),
        }

    def traj_scores(h, drog=None):
        rows = [r for r in traj_rows if r["h"] == h]
        if not rows:
            return None
        out = {}
        sel = np.concatenate([np.ones(len(r["current"]), bool) if drog is None else (r["drogued"] == drog)
                              for r in rows])
        alive = ~np.concatenate([r["current_dead"] | r["ml_dead"] | r["current+wind_dead"] for r in rows])
        sel &= alive  # only virtual drifters that stayed inside model water for every method
        out["n"] = int(sel.sum())
        for name in ("current", "current+wind", "ml"):
            d = np.concatenate([r[name] for r in rows])[sel]
            out[f"median_km_{name}"] = round(float(np.median(d)), 1) if len(d) else None
            out[f"mean_km_{name}"] = round(float(np.mean(d)), 1) if len(d) else None
        if out["n"]:
            dc = np.concatenate([r["current"] for r in rows])[sel]
            dm = np.concatenate([r["ml"] for r in rows])[sel]
            out["ml_closer_fraction"] = round(float(np.mean(dm < dc)), 3)
        return out

    scores = {
        "velocity_all": vel_scores(train.fold.notna()),
        "velocity_drogued": vel_scores(train.drogued == 1),
        "velocity_undrogued": vel_scores(train.drogued == 0),
        "traj_24h_all": traj_scores(24), "traj_48h_all": traj_scores(48),
        "traj_24h_undrogued": traj_scores(24, 0.0), "traj_48h_undrogued": traj_scores(48, 0.0),
        "traj_24h_drogued": traj_scores(24, 1.0), "traj_48h_drogued": traj_scores(48, 1.0),
        "windage_alpha_per_fold": {str(k): round(v, 4) for k, v in alpha_fold.items()},
    }
    print(json.dumps(scores, indent=1), file=sys.stderr)

    # --- final model on all drifters ----------------------------------------------------------
    import joblib
    mu, mv = make_models()
    X = X_of(train)
    mu.fit(X, train.ru)
    mv.fit(X, train.rv)
    # Sanity: windage the model implies for undrogued vs drogued drifters (projection on the wind).
    und = train[train.drogued == 0]
    if len(und):
        Xu0, Xu1 = X_of(und, 0.0), X_of(und, 1.0)
        du = mu.predict(Xu0) - mu.predict(Xu1)
        dv = mv.predict(Xu0) - mv.predict(Xu1)
        W2 = (und.uw ** 2 + und.vw ** 2).sum()
        scores["implied_extra_windage_undrogued"] = round(float((du * und.uw + dv * und.vw).sum() / W2), 4)
    # Windage implied by each drogue setting and by the mean the oil model uses (should be ~0).
    W2a = (train.uw ** 2 + train.vw ** 2).sum()
    imp = {}
    pr = {}
    for name, dro in (("drogued", 1.0), ("undrogued", 0.0)):
        Xd = X_of(train, dro)
        pr[name] = (mu.predict(Xd), mv.predict(Xd))
        imp[name] = round(float((pr[name][0] * train.uw + pr[name][1] * train.vw).sum() / W2a), 4)
    mu_, mv_ = (pr["drogued"][0] + pr["undrogued"][0]) / 2, (pr["drogued"][1] + pr["undrogued"][1]) / 2
    imp["oil_mean"] = round(float((mu_ * train.uw + mv_ * train.vw).sum() / W2a), 4)
    scores["implied_windage"] = imp
    joblib.dump({"u": mu, "v": mv}, ml.MODEL_PATH, compress=3)
    cov = ml.coverage_grid(train.longitude.values, train.latitude.values, BOX)
    np.savez_compressed(ml.COVERAGE_PATH, x0=cov["x0"], y0=cov["y0"], counts=cov["counts"].astype(np.int32))
    scores["coverage_cells_applied"] = int((cov["counts"] >= ml.COVER_MIN).sum())
    scores["coverage_cells_total"] = int(cov["counts"].size)
    per_window = train.groupby("window").agg(rows=("ID", "size"), drifters=("ID", "nunique")).to_dict("index")
    card = {
        "name": "OilFlood drift correction (HistGradientBoosting, u and v)",
        "trained_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": "6 h mean drifter velocity minus forcing current at the drifter (m/s)",
        "features": ml.FEATURES,
        "training_data": {
            "source": "NOAA AOML Global Drifter Program hourly QC (ERDDAP drifter_hourly_qc)",
            "forcing": "HYCOM-TSIS GOMb0.04 reanalysis currents + its 10 m winds (data/cache/forcing/<window>)",
            "box": BOX,
            "rows": int(len(train)), "drifters": int(len(ids)),
            "per_window": {k: {kk: int(vv) for kk, vv in v.items()} for k, v in per_window.items()},
        },
        "validation": f"{a.folds}-fold cross-validation grouped by drifter ID (held-out drifters)",
        "scores": scores,
        "train_seconds": round(time.time() - t_wall, 1),
    }
    ml.CARD_PATH.write_text(json.dumps(card, indent=1))
    print(f"wrote {ml.MODEL_PATH} ({ml.MODEL_PATH.stat().st_size // 1024} KB) and {ml.CARD_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
