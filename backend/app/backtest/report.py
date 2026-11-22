"""Markdown tables from data/backtest/results.json (pasted into docs/BACKTEST.md).

  cd backend && ~/miniforge3/envs/oilflood/bin/python -m app.backtest.report
"""
from __future__ import annotations

import json

from .run import OUT_DIR

ORDER = ["persistence", "openoil", "ours_ml", "ours_noml", "ours_ml_nodwh", "openoil_src", "ours_ml_src", "ours_noml_src"]
SRC_BASE = {"openoil_src": "openoil", "ours_ml_src": "ours_ml", "ours_noml_src": "ours_noml"}


def fmt(x, d=0):
    if x is None:
        return "–"
    if isinstance(x, str):
        return x
    return f"{x:.{d}f}"


def pct(x):
    return "–" if x is None else f"{round(100 * x)} %"


def main():
    res = json.loads((OUT_DIR / "results.json").read_text())
    names = res["models"]
    out = [f"_Generated {res['generated_at']} · {res['n_particles']} particles · raster {res['cell_deg']}° · "
           f"FSS window {res['fss_window_cells']} cells_\n"]
    for c in res["cases"]:
        out.append(f"### {c['name']} (`{c['id']}`)\n")
        if c["kind"] == "outlines":
            s = c["summary"]
            n = s["openoil"]["n"]
            out.append(f"{n} start days ({c['starts'][0]} … {c['starts'][-1]}). Medians over start days "
                       "(means in brackets).\n")
            out.append("| model | sep 24 h (km) | sep 48 h | sep 72 h | LW 24 h | LW 48 h | LW 72 h | IoU 72 h | "
                       "FSS 24 h | FSS 72 h | closer than persistence (72 h) |")
            out.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for k in ORDER:
                if k not in s:
                    continue
                m = s[k]
                sep = [f"{fmt(a)} ({fmt(b)})" for a, b in zip(m["sep_km_median"], m["sep_km_mean"])]
                lw = [fmt(a, 2) for a in m["lw_median"]]
                bp = pct(m["beats_persistence_sep"][2]) if "beats_persistence_sep" in m else "–"
                out.append(f"| {names.get(k, k)} | {' | '.join(sep)} | {' | '.join(lw)} | "
                           f"{fmt(m['iou_median'][2], 2)} | {fmt(m['fss_median'][0], 2)} | {fmt(m['fss_median'][2], 2)} | {bp} |")
            out.append("")
            for key, lab in (("ml_closer_share", "Ours + ML"), ("ml_nodwh_closer_share", "Ours + ML (DWH held out)")):
                if key in s:
                    out.append(f"- {lab} ends closer to the observed centroid than ours without ML on "
                               f"{pct(s[key][0])} / {pct(s[key][1])} / {pct(s[key][2])} of start days (24 / 48 / 72 h).")
            for k, v in s.get("source_closer_share", {}).items():
                out.append(f"- {names.get(k, k)} ends closer to the observed centroid than {names.get(SRC_BASE[k], k)} "
                           f"(no source) on {pct(v[0])} / {pct(v[1])} / {pct(v[2])} of start days (24 / 48 / 72 h).")
            for k in ("ours_ml", "ours_ml_nodwh"):
                if k in s and "ml_coverage_mean" in s[k]:
                    out.append(f"- {names.get(k, k)}: mean share of particle-steps inside the ML coverage mask "
                               f"{pct(s[k]['ml_coverage_mean'])}.")
            st = {k: s[k].get("stranded_mean") for k in ORDER if k in s and "stranded_mean" in s[k]}
            out.append("- Mean stranded share at 72 h: " + ", ".join(f"{names.get(k, k)} {pct(v[2])}" for k, v in st.items()) + ".")
            out.append("")
        elif c["kind"] == "points":
            out.append(f"Observed: {c['observed']}.\n")
            for fx in c["fixes"]:
                out.append(f"- {fx['label']} (assumed {fx['t']} UTC) — {fx.get('src') or ''}")
            out.append("")
            for v in c["variants"]:
                out.append(f"**{v['label']}** — seed {v['seed'][1]:.3f}N {abs(v['seed'][0]):.3f}W "
                           f"(moved {fmt(v.get('seed_moved_km'), 1)} km to model water), {fmt(v['duration_h'])} h.\n")
                out.append("| model | Mar 24: nearest floating oil (km) | centroid (km) | Mar 25: nearest (km) | centroid (km) | "
                           "stranded at end | stranded centroid |")
                out.append("|---|---|---|---|---|---|---|")
                for k in ORDER:
                    m = v["models"].get(k)
                    if not m:
                        continue
                    cells = []
                    for fx in m["fixes"]:
                        if fx is None:
                            cells += ["after run end", "–"]
                        elif "nearest_km" not in fx:
                            cells += ["all oil ashore", "–"]
                        else:
                            cells += [fmt(fx["nearest_km"], 1), fmt(fx["centroid_km"], 1)]
                    sc = m.get("stranded_centroid")
                    out.append(f"| {names.get(k, k)} | {' | '.join(cells)} | {pct(m['stranded_end'])} | "
                               f"{f'{sc[1]:.2f}N {abs(sc[0]):.2f}W' if sc else '–'} |")
                out.append("")
            out.append(f"Not scored: {c['not_scored']}\n")
        else:
            out.append(f"Observed: {c['observed']}\n")
            out.append("| model | centroid bearing 24 / 48 / 72 h (°) | toward SW (180–270°) | centroid distance 72 h (km) | "
                       "stranded 72 h | first stranding (h) | slick length 24 h (km) |")
            out.append("|---|---|---|---|---|---|---|")
            out.append("| **observed** | SW | yes | – | 0 % (no shoreline impact) | none | > 64 |")
            for k in ORDER:
                m = c["models"].get(k)
                if not m:
                    continue
                out.append(f"| {names.get(k, k)} | {' / '.join(str(b) for b in m['bearing_deg'])} | "
                           f"{'yes' if all(m['toward_sw']) else 'no'} | {fmt(m['dist_km'][-1])} | {pct(m['stranded'][-1])} | "
                           f"{fmt(m.get('first_stranding_h'))} | {fmt(m.get('length_24h_km'))} |")
            out.append("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
