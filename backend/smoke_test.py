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
    print("cache hit OK\nSMOKE TEST PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SMOKE TEST FAILED:", e)
        sys.exit(1)
