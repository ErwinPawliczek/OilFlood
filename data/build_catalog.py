#!/usr/bin/env python
"""Build data/spills/catalog.json (committed) from NOAA IncidentNews.

Downloads https://incidentnews.noaa.gov/raw/incidents.csv to data/raw/incidentnews/
and merges it with a curated list of Gulf of Mexico oil spills below. IncidentNews
gives id, name, open date, lat/lon and narrative; the curated entries add the
spilled-volume estimate (from the narrative / official reports, not the CSV's
"max potential release"), oil type, playback window and forcing availability.

  python data/build_catalog.py            # writes data/spills/catalog.json
"""
from __future__ import annotations

import csv
import json

from common import RAW, SPILLS, download, log

CSV_URL = "https://incidentnews.noaa.gov/raw/incidents.csv"
HYCOM_GOMB = ("2001-01-01", "2024-08-31")

# id, IncidentNews id, start (UTC), end (UTC), volume_bbl, oil_type, volume_note, extra
CURATED = [
    ("dwh", 8220, "2010-04-21T03:00:00Z", "2010-07-15T19:00:00Z", 3_190_000, "Louisiana light sweet crude (MC252)",
     "3.19M bbl into the Gulf (US District Court, Phase Two findings, 2015)",
     {"has_observed_outlines": True, "release": "subsea blowout, 1500 m depth, 87 days",
      "source_urls": ["https://www.ncei.noaa.gov/access/metadata/landing-page/bin/iso?id=gov.noaa.nodc:ORR-DWH-ERMA-GIS"]}),
    ("texas_city_y", 8741, "2014-03-22T17:30:00Z", "2014-03-26T00:00:00Z", 4_000, "IFO 380",
     "~168,000 gal (NOAA OR&R); barge Kirby 27706 held 924,000 gal",
     {"release": "barge collision, Houston Ship Channel at Texas City Y",
      "forcing_window": {"start": "2014-03-21", "end": "2014-03-26", "stride_h": 1},
      "source_urls": ["https://response.restoration.noaa.gov/oil-and-chemical-spills/significant-incidents/texas-city-y-oil-spill"]}),
    ("mc209_2017", 9601, "2017-10-12T12:00:00Z", "2017-10-20T00:00:00Z", 8_650, "Crude oil",
     "7,950-9,350 bbl (updated estimate in IncidentNews)", {"release": "subsea pipeline, Mississippi Canyon 209"}),
    ("dbl_152", 1311, "2005-11-11T12:00:00Z", "2005-11-18T00:00:00Z", 7_200, "Fuel Oil No. 6 / slurry oil",
     "~7,200 bbl lost from #1 starboard tank (mostly sank)", {"release": "barge holed on submerged wreck, 32 mi off W Louisiana"}),
    ("eugene_island_2009", 8061, "2009-07-25T20:00:00Z", "2009-07-30T00:00:00Z", 1_500, "Crude oil (API 33.7)",
     "~63,000 gal (IncidentNews)", {"release": "pipeline, 33 mi offshore Louisiana"}),
    ("main_pass_2023", 10692, "2023-11-16T14:00:00Z", "2023-11-22T00:00:00Z", 26_000, "Louisiana sweet crude",
     "IncidentNews max potential 1.1M gal; actual release estimated lower", {"release": "pipeline, 19 mi off Mississippi River Delta"}),
    ("mega_borg", 6748, "1990-06-09T04:30:00Z", "1990-06-16T00:00:00Z", 100_000, "Angolan Palanca crude",
     "~100,000 bbl burned or released over 7 days (IncidentNews)", {"release": "tanker explosion and fire, 57 mi SE of Galveston"}),
    ("burmah_agate", 6253, "1979-11-01T12:00:00Z", "1980-01-08T00:00:00Z", 255_000, "Nigerian crude",
     "IncidentNews 10.7M gal potential; most burned", {"release": "tanker collision and fire off Galveston entrance"}),
    ("ixtoc_i", 6250, "1979-06-03T12:00:00Z", "1980-03-23T00:00:00Z", 3_300_000, "IXTOC I crude",
     "~3.0-3.5M bbl over 290 days", {"release": "shallow-water well blowout (50 m), Bahia de Campeche"}),
]


NAMES = {"texas_city_y": "Texas City \"Y\" collision", "mc209_2017": "Mississippi Canyon 209 pipeline",
         "dbl_152": "Barge DBL 152", "eugene_island_2009": "Eugene Island pipeline", "main_pass_2023": "Main Pass pipeline",
         "mega_borg": "M/V Mega Borg", "ixtoc_i": "Ixtoc I"}


def main():
    f = download(CSV_URL, RAW / "incidentnews" / "incidents.csv", timeout=300)
    rows = {int(r["id"]): r for r in csv.DictReader(open(f, newline="", encoding="utf-8"))}
    out = []
    for sid, inid, start, end, vol, oil, note, extra in CURATED:
        r = rows.get(inid)
        if r is None:
            log(f"IncidentNews {inid} missing; skipping {sid}")
            continue
        hycom = HYCOM_GOMB[0] <= start[:10] <= HYCOM_GOMB[1]
        has_obs = bool(extra.pop("has_observed_outlines", False))
        urls = [f"https://incidentnews.noaa.gov/incident/{inid}"] + extra.pop("source_urls", [])
        desc = " ".join((r.get("description") or "").split())
        out.append({
            "id": sid,
            "name": NAMES.get(sid) or r["name"].split(";")[0].strip(),
            "location": r.get("location") or r["name"].split(";")[-1].strip(),
            "date_start": start,
            "date_end": end,
            "lat": round(float(r["lat"]), 5),
            "lon": round(float(r["lon"]), 5),
            "volume_bbl": vol,
            "volume_note": note,
            "oil_type": oil,
            "commodity": r.get("commodity") or None,
            "incidentnews_id": inid,
            "source_urls": urls,
            "has_observed_outlines": has_obs,
            "forcing": "hycom_gomb0.04" if hycom else None,
            "summary": desc[:400] + ("..." if len(desc) > 400 else ""),
            **extra,
        })
    SPILLS.mkdir(parents=True, exist_ok=True)
    dest = SPILLS / "catalog.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    log(f"wrote {dest} with {len(out)} spills: {', '.join(s['id'] for s in out)}")


if __name__ == "__main__":
    main()
