"""Time-series action document (T27): "what to do when", built from the response outline (resp-v4) of one run.

Every row is a time (hours after release + UTC + local), an action, where, why (the forecast quantity that drives it
+ the protocol rule ID of data/response/protocols.json / docs/PROTOCOLS.md) and a confidence (ensemble probability,
share of ensemble members whose protocol gates pass, or "single run" when there is no ensemble).

Nothing here is new modelling: the rows re-arrange numbers the outline already computed (ETAs, lead times, tactic gate
windows, closures) on one clock, plus fixed protocol checkpoints (notification, Tier 1/2/3, re-forecast cycle).

Planning aid only. The FOSC / Unified Command, the ACP and the RRT decide every response action.
"""
from __future__ import annotations

import csv
import html
import io
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import protocols as P
from .response import sun_elevation

VERSION = "actions-v1"
DISCLAIMER = ("Planning aid generated automatically from a model forecast. NOT operational guidance: the Federal "
              "On-Scene Coordinator / Unified Command, the Area Contingency Plan and the Regional Response Team decide "
              "every response action. Times are model estimates and change with every new forecast.")

# Tier 2 / Tier 3 planning arrival times (33 CFR 154.1045, MECH-6): (higher-volume port area, elsewhere)
TIER2_H, TIER3_H = (30.0, 36.0), (54.0, 60.0)
# Re-forecast checkpoints: every new GFS cycle (6-hourly) on day 1, then every 12 h (operational period, ICS-2;
# NOAA recalibrates from overflights at least daily, FCST-1 / MON-1)
REFORECAST_DAY1_H, REFORECAST_LATER_H = 6.0, 12.0

TACTIC = {
    "dispersant": ("Dispersant", "DISP-1, DISP-4, DISP-5, AUTH-1, DISP-6",
                   "FOSC approval + SMART Tier I monitoring; RRT-6 pre-approval zone (>= 3 nm and >= 10 m)"),
    "in_situ_burn": ("In-situ burn", "ISB-1, ISB-2, ISB-4, ISB-5, AUTH-2, ISB-7",
                     "FOSC + RRT-6 concurrence; PM10 monitoring (150 ug/m3 trigger)"),
    "mechanical": ("Skimming / on-water boom", "MECH-2, ISB-5", "daylight; short-period waves <= 2-3 ft (check)"),
}
ORDER = {"start": 0, "notify": 1, "overflight": 2, "mobilize": 3, "safety": 4, "boom_on_scene": 5, "tactic_open": 6,
         "tactic_close": 7, "tier": 8, "closure": 9, "eta_p10": 10, "deadline": 11, "eta_p50": 12, "landfall": 13,
         "reforecast": 14, "late": 15, "end": 16}


def local_zone(lon: float, lat: float) -> ZoneInfo:
    """US Gulf coast: Central time west of the Apalachicola River (~85 W), Eastern east of it; Mexico's Gulf coast
    (Tamaulipas/Veracruz) observes Central standard time year-round."""
    if lat < 25.95 and lon < -96.5:
        return ZoneInfo("America/Monterrey")
    return ZoneInfo("America/New_York" if lon > -85.0 else "America/Chicago")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _pct(p) -> str:
    return f"{round(100 * p)} %"


def build(out: dict) -> dict:
    """Action timeline from a response outline (app/response.build output)."""
    sp = out["spill"]
    t0 = _parse(sp["start"])
    horizon = float(sp.get("horizon_h") or 72)
    lon0, lat0 = float(sp["lon"]), float(sp["lat"])
    tz = local_zone(lon0, lat0)
    ens = out.get("ensemble") or None
    nmem = (ens or {}).get("members") or 0
    lay = out.get("layer") or {}
    rows: list[dict] = []

    def add(h, kind, action, where, why, rules, conf=None, p=None, lon=None, lat=None):
        if h is None or h < 0 or h > horizon + 0.01:
            return
        rows.append({"h": round(float(h), 2), "kind": kind, "action": action, "where": where, "why": why,
                     "rules": rules, "confidence": conf or ("control run (best guess)" if nmem else "single run (best guess)"),
                     "p": None if p is None else round(float(p), 3),
                     "lon": lon if lon is not None else lon0, "lat": lat if lat is not None else lat0})

    src = f"{sp['lat']:.3f} N, {abs(sp['lon']):.3f} {'W' if sp['lon'] < 0 else 'E'}"
    fc = sp.get("forecast") or {}
    fc_txt = ""
    if fc.get("currents") and fc.get("winds"):
        fc_txt = (f"; forcing {fc['currents'].get('model')} issued {fc['currents'].get('issued_at') or '?'}, "
                  f"{fc['winds'].get('model')} issued {fc['winds'].get('issued_at') or '?'}")
    model = "our model" if sp.get("model") == "ours" else "OpenDrift OpenOil"
    add(0, "start", f"Spill reported: {float(sp.get('volume_bbl') or 0):,.0f} bbl {sp.get('oil_class_label', '')}",
        src, f"Forecast start ({model}, {sp.get('particles', '?')} particles{f', {nmem}-member ensemble' if nmem else ''}"
        f"{fc_txt})", "FCST-1", "reported")

    booms = lay.get("booms") or []
    hvp = next((b["lead"]["hvp"] for b in booms if b.get("lead") and b["lead"].get("hvp")), None)
    lead0 = booms[0]["lead"] if booms and booms[0].get("lead") else P.lead_time(lon0, lat0, None)
    add(P.NOTIFY_H, "notify", "Notify the National Response Center (1-800-424-8802) and the USCG COTP; request NOAA SSC "
        "trajectory support", src, "Regulatory notification within 30 min of discovery; NOAA first-hour package",
        "MECH-6, AUTH-3, FCST-1", "protocol")
    add(P.NOTIFY_H, "safety", "USCG safety zone over the slick; vessel exclusion around response operations",
        src, "Responder and public safety over the forecast slick extent", "R9", "protocol")

    # first daylight at the source: overflight to confirm the slick (observations drive the next forecast)
    for k in range(0, int(min(horizon, 24)) * 4 + 1):
        h = k / 4
        if sun_elevation((t0 + timedelta(hours=h)).timestamp(), lat0, lon0) > 5:
            add(max(h, P.NOTIFY_H + 0.5), "overflight", "Overflight / observer: confirm slick position, extent and "
                "thickness (feeds the next forecast)", src, "First daylight at the source (sun > 5 deg)", "MON-1, FCST-1",
                "protocol")
            break

    add(P.NOTIFY_H + P.MOBILIZE_H, "mobilize", "Tier 1 equipment mobilized and en route", lead0.get("base") or src,
        "33 CFR 154.1045: en route within 2 h of notification", "MECH-6", "protocol")

    # --- boom sites: on scene (lead time) and deadline (P10 / best-guess arrival) ------------------------------
    bf = {b.get("priority"): b for b in out.get("boom_first") or []}
    for b in booms:
        where = b.get("name") or f"{b['lat']:.3f}, {b['lon']:.3f}"
        lt = b.get("lead") or {}
        st = (b.get("staging") or [{}])[0]
        f = bf.get(b.get("priority")) or {}
        p_oil = f.get("p_oiled_72h")
        conf_p = _pct(p_oil) + f" of {nmem} members oil it by 72 h" if p_oil is not None and nmem else None
        if b.get("late"):
            add(0.0, "late", f"Too late for boom at {where}: send shoreline crews, protect the next site",
                where, f"Oil ashore ~T+{b['eta_h']:.0f} h, before Tier 1 boom can be on scene (~T+{lt.get('lead_h', '?')} h)",
                "MECH-6, R6", conf_p, p_oil, b["lon"], b["lat"])
            continue
        road = f", {st['km']} km / ~{st.get('travel_h')} h by road" if st.get("km") is not None else ""
        add(b["deploy_by_h"], "boom_on_scene",
            f"{b.get('label', 'Boom')} on scene at {where}",
            where, (f"From {st['name']}; " if st.get("name") else "") + f"Tier 1 lead time {lt.get('lead_h', '?')} h ({'port area' if lt.get('hvp') else 'outside port areas'}"
            f"{road}); "
            f"ESI {b.get('esi')} {b.get('esi_label', '')}; current {b.get('across_kt', '?')} kt across the boom",
            "MECH-6, MECH-1, R5", conf_p, p_oil, b["lon"], b["lat"])
        dl = f.get("deploy_by_h")
        if dl is not None:
            add(dl, "deadline", f"Deadline: boom in place at {where}", where,
                f"Earliest plausible arrival (P10 ETA T+{dl:.0f} h)" + (f", median T+{f['eta_p50_h']:.0f} h" if f.get("eta_p50_h") else ""),
                "R6, FCST-2", conf_p, p_oil, b["lon"], b["lat"])
        elif b.get("eta_earliest_h") is not None:
            add(b["eta_earliest_h"], "deadline", f"Deadline: boom in place at {where}", where,
                f"Earliest arrival in the 1-4 % windage envelope (best guess T+{b['eta_h']:.0f} h)", "R1, FCST-2, FCST-4",
                None, None, b["lon"], b["lat"])
    for i in lay.get("inlets") or []:
        add(max(0.0, i["deploy_by_h"]), "boom_on_scene", f"Boom across {i['name']}" +
            (" (oil arrives before it can be in place)" if i.get("late") else ""), i["name"],
            f"Channel entrance within 25 km of the forecast oil (oil near ~T+{i['eta_h']:.0f} h)", "MECH-6, R5",
            None, None, i["lon"], i["lat"])

    # --- Tier 2 / 3 -----------------------------------------------------------------------------------------
    if out.get("priority_shorelines") or booms:
        k = 0 if hvp else 1
        add(TIER2_H[k], "tier", "Tier 2 resources on scene (planning standard)", hvp or "outside higher-volume port areas",
            "33 CFR 154.1045 Tier 2 arrival time", "MECH-6", "protocol")
        add(TIER3_H[k], "tier", "Tier 3 resources on scene (planning standard)", hvp or "outside higher-volume port areas",
            "33 CFR 154.1045 Tier 3 arrival time", "MECH-6", "protocol")

    # --- tactic windows (hour-level protocol gates) ----------------------------------------------------------
    for key, t in (out.get("tactics") or {}).items():
        label, rules, need = TACTIC.get(key, (t.get("tactic", key), "", ""))
        if t.get("not_applicable"):
            continue
        for w in t.get("windows") or []:
            pm = w.get("p_members")
            conf = f"gates pass in {_pct(pm)} of members" if pm is not None else None
            add(w["from_h"], "tactic_open", f"{label} window opens ({w['hours']:.0f} h)", "slick (see map zone)",
                f"All hour-level gates pass ({need})", rules, conf, pm)
            add(min(horizon, w["to_h"] + 1), "tactic_close", f"{label} window closes", "slick",
                "A protocol gate closes (night, wind, emulsion, distance, ...): see the tactic's gates", rules, conf, pm)

    # --- shoreline arrival (ensemble P10/P50, else best guess + envelope) --------------------------------------
    for s in (out.get("priority_shorelines") or [])[:8]:
        where = f"{s.get('name') or 'coast'} (ESI {s.get('esi_most_sensitive')})"
        p = s.get("p_oiled_72h")
        conf = f"P(oiled by 72 h) {_pct(p)}" if p is not None else None
        tac = s.get("shoreline_tactic", "")
        if p is not None:
            if s.get("eta_p10_h") is not None:
                add(s["eta_p10_h"], "eta_p10", f"Earliest plausible oil at {s.get('name')}: shoreline crews and SCAT ready",
                    where, f"P10 arrival over {nmem} members (1 in 10 members earlier). {tac}", "FCST-2, ESI-1", conf, p,
                    s["lon"], s["lat"])
            if s.get("eta_p50_h") is not None:
                add(s["eta_p50_h"], "eta_p50", f"Median oil arrival at {s.get('name')}", where,
                    f"P50 arrival over {nmem} members", "FCST-2", conf, p, s["lon"], s["lat"])
        else:
            if s.get("eta_earliest_h") is not None and s["eta_earliest_h"] < s["eta_h"] - 0.5:
                add(s["eta_earliest_h"], "eta_p10", f"Earliest possible oil at {s.get('name')} (uncertainty envelope)",
                    where, f"1-4 % windage envelope reaches it. {tac}", "R1, FCST-2, FCST-4", None, None, s["lon"], s["lat"])
            add(s["eta_h"], "landfall" if s.get("impact") == "landfall" else "eta_p50",
                f"{'Oil ashore' if s.get('impact') == 'landfall' else 'Oil within 10 km'} at {s.get('name')} (best guess)",
                where, f"{s.get('particles_pct', '?')} % of particles; {tac}", "ESI-1, R8", None, None, s["lon"], s["lat"])

    fl = out.get("first_landfall") or {}
    if nmem and out.get("eta_first_landfall_h") is not None:
        add(out["eta_first_landfall_h"], "landfall", f"First landfall in the best-guess run: {fl.get('name') or 'coast'}",
            fl.get("name") or "", "Control run (unperturbed parameters): first stranded particles", "ESI-1, R8",
            None, None, fl.get("lon"), fl.get("lat"))

    # --- closures ---------------------------------------------------------------------------------------------
    for c in out.get("closures") or []:
        if c.get("type") == "safety_zone":
            continue
        if c.get("eta_h") is not None:
            add(max(P.NOTIFY_H, c["eta_h"] - 12), "closure", c["what"], c.get("who") or "",
                f"Surface oil within 15 km at ~T+{c['eta_h']:.0f} h (act 12 h ahead)", "R9, GAP-15", None)
        else:
            first = out.get("eta_first_landfall_h")
            add(max(P.NOTIFY_H, (first - 12) if first else P.NOTIFY_H), "closure", c["what"], c.get("who") or "",
                c.get("basis") or "Persistent oil / landfall forecast", "R9")

    # --- re-forecast checkpoints -------------------------------------------------------------------------------
    h = REFORECAST_DAY1_H
    while h < horizon - 0.5:
        add(h, "reforecast", "Re-forecast with the new wind/current cycle + latest overflight; update this action list",
            "incident command post", "Forecast winds and currents are updated every 6 h (GFS) and daily (RTOFS); "
            "observations recalibrate the trajectory", "FCST-1, FCST-3, MON-1, ICS-2", "protocol")
        h += REFORECAST_DAY1_H if h < 24 else REFORECAST_LATER_H

    add(horizon, "end", "End of this forecast: no positions beyond this time", src,
        f"Forecast horizon {horizon:.0f} h (forcing ends or requested length)", "FCST-3", "n/a")

    rows.sort(key=lambda r: (r["h"], ORDER.get(r["kind"], 99)))
    for r in rows:
        t = t0 + timedelta(hours=r["h"])
        r["t_utc"] = t.strftime("%Y-%m-%dT%H:%MZ")
        lt = t.astimezone(tz)
        r["t_local"] = lt.strftime("%a %d %b %H:%M ") + (lt.tzname() or "")
        r["tplus"] = f"T+{r['h']:.1f} h" if r["h"] % 1 else f"T+{r['h']:.0f} h"
    return {
        "version": VERSION, "disclaimer": DISCLAIMER, "run_id": out.get("run_id"), "spill_id": out.get("spill_id"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "spill": {k: sp.get(k) for k in ("lat", "lon", "start", "horizon_h", "volume_bbl", "oil_type", "oil_class_label",
                                          "model", "particles")},
        "ensemble": ens, "timezone": str(tz), "eta_first_landfall_h": out.get("eta_first_landfall_h"),
        "headline": out.get("headline") or [],
        "columns": ["tplus", "t_utc", "t_local", "action", "where", "why", "rules", "confidence"],
        "rows": rows, "rules_doc": P.DOC_URL,
    }


# --- exports ----------------------------------------------------------------------------------------------

def _title(a: dict) -> str:
    sp = a["spill"]
    name = a.get("spill_id") or f"{sp['lat']:.2f} N {abs(sp['lon']):.2f} W"
    return f"OilFlood action timeline: {name} ({sp['start']})"


def to_markdown(a: dict) -> str:
    sp = a["spill"]
    md = [f"# {_title(a)}", "", f"> **{a['disclaimer']}**", "",
          f"- Spill: {float(sp.get('volume_bbl') or 0):,.0f} bbl {sp.get('oil_class_label') or sp.get('oil_type')}, "
          f"{sp['lat']:.3f}, {sp['lon']:.3f}; start {sp['start']}; horizon {sp['horizon_h']:.0f} h",
          f"- Model: {'OilFlood (ours)' if sp.get('model') == 'ours' else 'OpenDrift OpenOil'}"
          + (f", ensemble of {a['ensemble']['members']} members" if a.get("ensemble") else ", single run (no ensemble)"),
          f"- Local time zone: {a['timezone']}. Generated {a['generated_at']} ({a['version']}). Rule IDs: {a['rules_doc']}",
          ""]
    if a.get("headline"):
        md += ["**Forecast summary:** " + " ".join(a["headline"]), ""]
    md += ["| Time | UTC | Local | Action | Where | Why (forecast + rule) | Confidence |",
           "|---|---|---|---|---|---|---|"]
    esc = lambda s: str(s or "").replace("|", "\\|").replace("\n", " ")  # noqa: E731
    for r in a["rows"]:
        conf = r["confidence"]
        md.append(f"| {r['tplus']} | {r['t_utc']} | {esc(r['t_local'])} | {esc(r['action'])} | {esc(r['where'])} | "
                  f"{esc(r['why'])} [{esc(r['rules'])}] | {esc(conf)} |")
    md += ["", f"_{a['disclaimer']}_", ""]
    return "\n".join(md)


def to_csv(a: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"# {a['disclaimer']}"])
    w.writerow(["t_plus_h", "time_utc", "time_local", "kind", "action", "where", "lon", "lat", "why", "rules",
                "confidence", "p"])
    for r in a["rows"]:
        w.writerow([r["h"], r["t_utc"], r["t_local"], r["kind"], r["action"], r["where"], r["lon"], r["lat"], r["why"],
                    r["rules"], r["confidence"], "" if r["p"] is None else r["p"]])
    return buf.getvalue()


def to_html(a: dict) -> str:
    e = html.escape
    sp = a["spill"]
    rows = "".join(
        f"<tr class='k-{e(r['kind'])}'><td>{e(r['tplus'])}</td><td>{e(r['t_utc'])}</td><td>{e(r['t_local'])}</td>"
        f"<td><b>{e(r['action'])}</b></td><td>{e(r['where'])}</td><td>{e(r['why'])} <small>[{e(r['rules'])}]</small></td>"
        f"<td>{e(r['confidence'])}</td></tr>" for r in a["rows"])
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{e(_title(a))}</title>
<style>
body{{font:12px/1.35 system-ui,sans-serif;color:#111;margin:18px}} h1{{font-size:17px;margin:0 0 6px}}
.disc{{border:2px solid #000;padding:6px 8px;font-weight:600;margin:8px 0}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #999;padding:3px 5px;vertical-align:top;text-align:left}}
th{{background:#eee}} tr{{page-break-inside:avoid}} small{{color:#555}}
tr.k-deadline td,tr.k-late td{{background:#fde8e8}} tr.k-eta_p10 td,tr.k-landfall td{{background:#fff4dc}}
tr.k-reforecast td{{color:#555}} @media print{{body{{margin:8mm}} .noprint{{display:none}}}}
</style></head><body>
<button class="noprint" onclick="print()">Print</button>
<h1>{e(_title(a))}</h1>
<div class="disc">{e(a['disclaimer'])}</div>
<div>{float(sp.get('volume_bbl') or 0):,.0f} bbl {e(sp.get('oil_class_label') or sp.get('oil_type') or '')} at
{sp['lat']:.3f}, {sp['lon']:.3f}; start {e(sp['start'])}; horizon {sp['horizon_h']:.0f} h;
{'ensemble of ' + str(a['ensemble']['members']) + ' members' if a.get('ensemble') else 'single run'}; local time {e(a['timezone'])}.</div>
<p>{e(' '.join(a.get('headline') or []))}</p>
<table><thead><tr><th>Time</th><th>UTC</th><th>Local</th><th>Action</th><th>Where</th><th>Why (forecast + rule ID)</th>
<th>Confidence</th></tr></thead><tbody>{rows}</tbody></table>
<p><small>Rule IDs: <a href="{e(a['rules_doc'])}">docs/PROTOCOLS.md</a>. Generated {e(a['generated_at'])} ({e(a['version'])}).</small></p>
<div class="disc">{e(a['disclaimer'])}</div>
</body></html>"""
