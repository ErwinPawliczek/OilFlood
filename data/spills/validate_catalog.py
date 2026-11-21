#!/usr/bin/env python3
"""Check data/spills/catalog.json against the schema in docs/ARCHITECTURE.md.

    python3 data/spills/validate_catalog.py [path/to/catalog.json]

Exits non-zero and lists problems if any record is malformed. Stdlib only."""
import json
import re
import sys
from pathlib import Path

CATS = {"notification", "assessment", "source_control", "boom", "skimming", "dispersant", "in_situ_burn",
        "shoreline_cleanup", "wildlife", "closure", "waste", "recovery_total", "outcome", "other"}
MEASURES = {"skimming", "shoreline_cleanup", "bioremediation", "dispersant", "in_situ_burn"}
DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
REQUIRED = ["id", "name", "date_start", "lat", "lon", "oil_type", "source_urls", "incidentnews_id"]


def check(cat: list) -> list[str]:
    errs: list[str] = []
    ids = set()
    for s in cat:
        sid = s.get("id", "?")
        e = lambda m: errs.append(f"{sid}: {m}")
        for k in REQUIRED:
            if s.get(k) in (None, "", []):
                e(f"missing {k}")
        if sid in ids:
            e("duplicate id")
        ids.add(sid)
        for kind, text_key in (("evolution", "note"), ("response", "action")):
            items = s.get(kind)
            if not isinstance(items, list):
                e(f"{kind} missing or not a list")
                continue
            prev = ""
            for i, it in enumerate(items):
                where = f"{kind}[{i}]"
                if not DATE.match(str(it.get("date", ""))):
                    e(f"{where} bad date {it.get('date')!r}")
                elif it["date"] < prev:
                    e(f"{where} out of order ({it['date']} < {prev})")
                else:
                    prev = it["date"]
                if not str(it.get(text_key, "")).strip():
                    e(f"{where} empty {text_key}")
                if not str(it.get("src", "")).startswith("http"):
                    e(f"{where} src must be a URL")
                if kind == "response" and it.get("category") not in CATS:
                    e(f"{where} bad category {it.get('category')!r}")
        rs = s.get("response_summary")
        if not isinstance(rs, dict):
            e("response_summary missing")
            continue
        m = rs.get("measures", {})
        if set(m) != MEASURES or any(v not in (True, False, None) for v in m.values()):
            e("response_summary.measures malformed")
        if not isinstance(rs.get("gaps", []), list):
            e("response_summary.gaps must be a list")
        if s.get("has_observed_outlines") and not s.get("observed_outlines"):
            e("has_observed_outlines but no observed_outlines pointer")
    return errs


def main() -> int:
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("catalog.json")
    cat = json.loads(p.read_text())
    errs = check(cat)
    for x in errs:
        print("ERROR", x)
    n_r = sum(len(s.get("response", [])) for s in cat)
    n_e = sum(len(s.get("evolution", [])) for s in cat)
    print(f"{len(cat)} spills, {n_r} response actions, {n_e} evolution notes, {len(errs)} problems")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
