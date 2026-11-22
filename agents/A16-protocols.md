# Agent A16: apply real response protocols to the outline + map layer (task T23)
Repo: OilFlood. You are in worktree ~/OilFlood-a15-layer on branch `a15-layer`, right after agent A15 built the first-responder map layer (T21). First: `git fetch && git merge origin/main` (brings docs/PROTOCOLS.md + data/response/protocols.json from T22; resolve SYNC.md conflicts by keeping both sides). Read SYNC.md, docs/PROTOCOLS.md (esp. section 3 rules R1–R10 and section 5 gap table), docs/RESPONSE.md, backend/app/response.py, web/src/outline.ts and A15's layer code.
Rules: small commits, push often to `a15-layer`, never force-push, no secrets. Don't touch production port 5200; test on 5220 (web dev 5290). REAL DATA ONLY, every threshold must cite a source from PROTOCOLS.md / protocols.json.
Goal: make the model -> action logic follow the real protocols:
- Dispersant zone: >=10 m depth AND >=3 nm (RRT-6); aerial limits wind <=25 kt, visibility, daylight. Use real bathymetry if cached (else document the gap).
- Replace invented oil-age windows with the real gates (API gravity/pour point, emulsion <=25%, slick thickness); burn gates: thickness 2–3 mm, wind <=25 mph hard / 20 mph preferred, waves <=3 ft, current <=0.75 kt, sun >15°, >=500 bbl per burn if sourced; exclusion zones only if real data available.
- Boom: perpendicular current at each boom site (not slick centroid); exclusion boom <0.7–0.75 kt, deflection boom above, "won't hold" >1 kt.
- Lead time by area type (Tier 1: 6 h higher-volume ports, 12 h elsewhere) instead of flat 6 h.
- Show what real responders did in similar cases (DWH, Texas City Y) as cited precedents next to each recommended action.
- Uncertainty: if feasible, show a simple envelope (e.g. spread of particles / ensemble) and label the outline "best guess + uncertainty" like NOAA.
- In the panel, a short "How we go from model to action" explainer (forecast -> ICS-232 priority sites -> tactic gates -> lead time vs arrival -> assignments), linking docs/PROTOCOLS.md.
Bump response.VERSION. Verify headless Chrome on 5290/5220 for a Galveston live prediction and DWH historical. Update docs/RESPONSE.md and docs/DEVPOST.md. Update SYNC.md (mark T23 done, Done log, Blockers), commit, push. End with a short summary of what is ready to merge.
