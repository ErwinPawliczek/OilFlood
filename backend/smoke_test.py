"""Smoke test: a live-forecast prediction (OpenOil baseline and our model) off Galveston (28.8N 94.5W, 4000 bbl IFO 380) through the HTTP API.

Usage (server running on :5200):  python smoke_test.py [--base http://127.0.0.1:5200] [--hours 72]
"""
import argparse
import json
import sys
import time
import urllib.request


def call(base, path, body=None):
    req = urllib.request.Request(base + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5200")
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--models", default="openoil,ours", help="comma list: openoil,ours")
    ap.add_argument("--ensemble", type=int, default=8, help="ensemble members for the ensemble test (0 = skip)")
    a = ap.parse_args()

    code, h = call(a.base, "/api/health")
    assert code == 200 and h.get("ok"), f"health failed: {code} {h}"

    for model in a.models.split(","):
        # Prediction runs start now on the live forecast (data/fetch_live.py); off Galveston.
        body = {"lat": 28.8, "lon": -94.5, "duration_h": a.hours, "volume_bbl": 4000, "oil_type": "ifo380",
                "n_particles": a.n, "model": model}
        code, r = call(a.base, "/api/spill", body)
        assert code == 200 and "id" in r, f"POST failed: {code} {r}"
        sid = r["id"]
        print(f"[{model}] spill id", sid, r["status"], f"{r['duration_h']} h", r.get("note") or "")
        assert r.get("forecast"), "no live forecast metadata"

        t0 = time.time()
        while True:
            code, st = call(a.base, f"/api/spill/{sid}")
            assert code == 200, st
            print(f"  {st['status']:8s} {st['progress']:.0%}")
            if st["status"] == "done":
                break
            assert st["status"] != "error", f"run failed: {st.get('error')}"
            assert time.time() - t0 < 900, "timeout"
            time.sleep(2)

        code, tr = call(a.base, f"/api/spill/{sid}/tracks")
        assert code == 200, f"tracks: {code}"
        assert tr["times"] and len(tr["times"]) == int(r["duration_h"] * 2) + 1, len(tr["times"])
        ps = tr["particles"]
        assert len(ps) > 0.9 * a.n, f"only {len(ps)} particles"
        for p in ps[:50]:
            assert len(p["path"]) == len(p["timestamps"]) >= 1
            lon, lat = p["path"][0]
            assert -96 < lon < -93 and 28 < lat < 31, p["path"][0]
            assert p["status_end"] in ("active", "stranded", "evaporated", "dispersed", "no_data")
        b = tr["budget"]
        assert b and set(b[0]) == {"t", "surface", "evaporated", "dispersed", "stranded"}
        ends = {}
        for p in ps:
            ends[p["status_end"]] = ends.get(p["status_end"], 0) + 1
        print("particles", len(ps), "end status", ends)
        print("budget t=end (kg)", b[-1])
        print("meta", {k: tr.get("meta", {}).get(k) for k in ("model", "oil_type", "forcing", "runtime_s")})
        assert tr["meta"]["model"] == model and r.get("model") == model, "tracks/status must say which model ran"

        code, r2 = call(a.base, "/api/spill", body)
        assert r2["id"] == sid and r2["status"] == "done", f"cache miss: {r2}"
        actions_test(a.base, sid)
    print("cache hit OK")
    if a.ensemble:
        ensemble_test(a)
    print("SMOKE TEST PASSED")


def wait(base, sid, limit=900):
    t0 = time.time()
    while True:
        code, st = call(base, f"/api/spill/{sid}")
        assert code == 200, st
        if st["status"] == "done":
            return st
        assert st["status"] != "error", f"run failed: {st.get('error')}"
        assert time.time() - t0 < limit, "timeout"
        time.sleep(2)


def raw(base, path):
    with urllib.request.urlopen(base + path, timeout=120) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode()


def actions_test(base, sid):
    """T27: time-series action document (JSON + md / csv / html exports, disclaimer on every one)."""
    code, ac = call(base, f"/api/spill/{sid}/actions")
    assert code == 200 and ac["rows"] and "not operational guidance" in ac["disclaimer"].lower(), (code, ac.get("detail"))
    hs = [r["h"] for r in ac["rows"]]
    assert hs == sorted(hs) and hs[0] == 0, hs[:5]
    for r in ac["rows"]:
        assert {"tplus", "t_utc", "t_local", "action", "where", "why", "rules", "confidence"} <= set(r), r
    kinds = {r["kind"] for r in ac["rows"]}
    assert {"start", "notify", "reforecast", "end"} <= kinds, kinds
    for fmt in ("md", "csv", "html"):
        code, ct, body = raw(base, f"/api/spill/{sid}/actions?format={fmt}")
        assert code == 200 and "NOT operational guidance" in body, (fmt, code, body[:200])
    print(f"  actions: {len(ac['rows'])} rows, kinds {sorted(kinds)}")
    return ac


def ensemble_test(a):
    """T24: custom params + ensemble -> probability, forces, probabilistic response (docs/ENSEMBLE.md)."""
    code, sch = call(a.base, "/api/model/params")
    assert code == 200 and {p["name"] for p in sch["params"]} >= {"windage_pct", "diffusivity_m2s", "current_factor",
                                                                 "stokes", "beaching", "particles", "horizon_h"}, sch
    code, r = call(a.base, "/api/spill", {"lat": 28.8, "lon": -94.5, "model": "openoil", "params": {"stokes": True}})
    assert code == 400, f"params with openoil must 400: {code}"
    code, r = call(a.base, "/api/spill", {"lat": 28.8, "lon": -94.5, "model": "ours", "params": {"windage_pct": 9}})
    assert code == 400, f"out-of-range param must 400: {code}"
    body = {"lat": 27.0, "lon": -97.1, "volume_bbl": 4000, "oil_type": "ifo380", "model": "ours",
            "ensemble": a.ensemble, "seed": 7, "params": {"windage_pct": 2.5, "stokes": True, "horizon_h": min(a.hours, 72),
                                                          "particles": 1000}}
    code, r = call(a.base, "/api/spill", body)
    assert code == 200 and r["ensemble"]["members"] == a.ensemble, r
    assert r["params"]["windage_pct"] == 2.5 and r["params"]["stokes"] is True, r["params"]
    sid = r["id"]
    t0 = time.time()
    st = wait(a.base, sid)
    print(f"[ensemble] {sid} {a.ensemble} members done in {time.time() - t0:.0f} s, {st['ensemble']}")
    code, pr = call(a.base, f"/api/spill/{sid}/probability")
    assert code == 200 and pr["members_ok"] >= 2, pr
    assert pr["hours"][0] == 0 and pr["step_h"] == 3 and len(pr["steps"]) == len(pr["hours"])
    s0 = pr["steps"][0]
    assert s0["cells"] and all(0 < c[2] <= 1 for c in s0["cells"]) and s0["p_max"] == 1.0, s0["p_max"]
    last = pr["steps"][-1]
    print("  P>=0.5 area at end", last["area_km2"], "ellipse90", (last["centroid"]["ellipse90"] or {}).get("a_km"))
    for sh in pr["shorelines"][:3]:
        print("  shoreline", sh["name"], sh["esi"], sh["p_oiled"], sh["eta_h"])
        assert 0 <= sh["p_oiled"]["end"] <= 1
    code, one = call(a.base, f"/api/spill/{sid}/probability?t=24")
    assert code == 200 and len(one["steps"]) == 1 and one["steps"][0]["h"] == 24
    code, fo = call(a.base, f"/api/spill/{sid}/forces")
    assert code == 200 and fo["source"] == "ensemble" and fo["steps"], fo
    f = fo["steps"][0]
    assert {"current", "wind", "stokes", "ml", "total", "diffusion", "budget"} <= set(f), f.keys()
    assert f["stokes"]["speed"] > 0, "stokes on but zero"
    code, rs = call(a.base, f"/api/spill/{sid}/response?brief=false")
    assert code == 200 and rs["ensemble"]["members"] >= 2, rs.get("ensemble")
    for s in rs["priority_shorelines"]:
        assert "p_oiled_72h" in s and "priority_score" in s
    print("  headline", rs["headline"][0])
    ac = actions_test(a.base, sid)
    assert any(r["p"] is not None for r in ac["rows"]), "ensemble actions must carry probabilities"
    code, r2 = call(a.base, "/api/spill", body)
    assert r2["id"] == sid and r2["status"] == "done", f"ensemble cache miss: {r2}"
    print("ensemble OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SMOKE TEST FAILED:", e)
        sys.exit(1)
