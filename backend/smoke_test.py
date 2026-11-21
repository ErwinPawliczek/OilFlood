"""Smoke test: Texas City Y (2014-03-22, seeded at Bolivar Roads 29.33N 94.72W, the nearest HYCOM water) through the HTTP API.

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
    a = ap.parse_args()

    code, h = call(a.base, "/api/health")
    assert code == 200 and h.get("ok"), f"health failed: {code} {h}"

    body = {"lat": 29.33, "lon": -94.72, "start_time": "2014-03-22T12:00:00",
            "duration_h": a.hours, "volume_m3": 636, "oil_type": "IFO 380", "n_particles": a.n}
    code, r = call(a.base, "/api/spill", body)
    assert code == 200 and "id" in r, f"POST failed: {code} {r}"
    sid = r["id"]
    print("spill id", sid, r["status"])

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
    assert tr["times"] and len(tr["times"]) == int(a.hours * 2) + 1, len(tr["times"])
    ps = tr["particles"]
    assert len(ps) > 0.9 * a.n, f"only {len(ps)} particles"
    for p in ps[:50]:
        assert len(p["path"]) == len(p["timestamps"]) >= 1
        lon, lat = p["path"][0]
        assert -96 < lon < -93 and 28 < lat < 31, p["path"][0]
        assert p["status_end"] in ("active", "stranded", "evaporated")
    b = tr["budget"]
    assert b and set(b[0]) == {"t", "surface", "evaporated", "dispersed", "stranded"}
    ends = {}
    for p in ps:
        ends[p["status_end"]] = ends.get(p["status_end"], 0) + 1
    print("particles", len(ps), "end status", ends)
    print("budget t=end (kg)", b[-1])
    print("meta", {k: tr.get("meta", {}).get(k) for k in ("oil_type", "forcing", "runtime_s")})

    code, r2 = call(a.base, "/api/spill", body)
    assert r2["id"] == sid and r2["status"] == "done", f"cache miss: {r2}"
    print("cache hit OK\nSMOKE TEST PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SMOKE TEST FAILED:", e)
        sys.exit(1)
