# OilFlood build plan (set by Erwin, 2026-11-21 before flight)
Goal: simple, minimal. Real geographic time series of currents + past oil spills.
Core question: "If X barrels of oil drop here, how will it spread?" Then: what should first responders do?

Rules: real data only (no mocks). Prediction mode = LIVE forecast forcing (current forecast model RTOFS/HYCOM forecast + weather forecast GFS), never historical data. Historical replays use reanalysis of that date. Minimal UI: one draggable panel (T11).

Queue (agents take the top unclaimed item; one branch per item; claim in SYNC.md):
P1  T11 real data + single panel (A5) and T10 global currents (A4): finish, merge to main, deploy to oilflood.xyz.
P2  Live prediction pipeline: auto-fetch latest RTOFS/HYCOM forecast currents + GFS winds (+ waves if free) for 0–72 h, refresh on a schedule (cron/systemd timer on tower), prediction mode always uses latest forecast; user picks oil amount (bbl) and type.
P3  Models: (a) baseline copied from established practice: OpenDrift OpenOil (NOAA GNOME-style: advection by currents + ~3% wind drift + Stokes, random-walk diffusion, evaporation/emulsification, beaching). (b) Our own model in backend/app/ourmodel/: Lagrangian particles, same forcing, Fay spreading, simple weathering, beaching on coastline, plus an ML drift correction trained on NOAA Global Drifter Program tracks vs forcing (fine-tune/pre-existing model per hackathon rules). UI toggle to compare models.
P4  Spill database data/spills/: for each Gulf spill (DWH, Texas City Y, Ixtoc I, Mega Borg, Burmah Agate, DBL 152, MC-20/Taylor, etc.): location, volume, oil type, dates, geo/time evolution (footprints/outlines where available) AND a response log (timeline of actions: booms, skimmers, dispersants, in-situ burns, shoreline cleanup, closures, volumes recovered, outcome, sources/citations from NOAA IncidentNews, ORR, USCG reports). Schema in docs/ARCHITECTURE.md. Show in historical mode panel.
P5  Backtest: our model vs OpenOil vs observed (DWH NESDIS outlines, Texas City Y), Liu–Weisberg skill + separation at 24/48/72 h. Results in docs/BACKTEST.md and a small panel view.
P6  Response outline (step B): from predicted spread + shoreline sensitivity (NOAA ESI) + time-to-shore + response-log precedents, generate an action outline for first responders (where to boom first, priority shorelines, skimmer/dispersant windows, ETA to landfall). Rule-based core following NOAA/USCG guidance, summarized by an LLM. Show in panel.
P7  Polish: README setup steps, perf, bug fixes, demo-ready default view (Houston/Galveston).
