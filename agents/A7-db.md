# Agent A7: spill + response database (PLAN P4)
Repo: OilFlood. Read SYNC.md, docs/PLAN.md, docs/ARCHITECTURE.md, data/spills/ first. Follow SYNC.md rules (small commits, push often to YOUR branch `a7-db`, never force-push, never commit secrets or large data). Deadline Sun Nov 22 12:00 PM PST.
Do not touch production port 5200. Do not rewrite the globe/panel internals; only extend the catalog and historical-mode panel to show the new fields.

Goal: a database of previous Gulf spills: how they unfolded geographically over time, AND how responders responded, so later models can use that as a data point.
- Extend `data/spills/catalog.json` (and schema in docs/ARCHITECTURE.md) for each spill: location, volume, oil type, start/end, sources, geo/time evolution (reuse existing DWH NESDIS footprints; add outlines or track notes where a public source exists), plus a `response` log: dated actions (booms, skimmers, dispersants, in-situ burns, shoreline cleanup, fisheries/area closures, volumes recovered, outcome) with citations (NOAA IncidentNews, ORR, USCG reports). At least: Deepwater Horizon, Texas City Y, Ixtoc I, Mega Borg, Burmah Agate, Barge DBL 152, MC-20/Taylor or the other catalog spills already present.
- Do not invent actions. If a source doesn't say, leave it unknown and note the gap.
- Backend: serve the extra fields on GET /api/spills and GET /api/spills/{id}.
- Frontend: in historical mode, the single panel shows a compact response timeline for the selected spill (dates + actions). Keep the one-panel UI.
Update SYNC.md (Done log, Blockers) when done.
