# OilFlood: Devpost write-up (EvoHacks 2, Projects for Social Good)

Ready-to-paste sections. Every number below comes from the repo docs (README.md, docs/OURMODEL.md, docs/BACKTEST.md,
docs/RESPONSE.md, SYNC.md) or the live API on https://oilflood.xyz. Re-check the live-forecast lines on the day of submission;
they change every 6 h.

- **Project name:** OilFlood
- **Live demo:** https://oilflood.xyz (desktop Chrome/Edge/Firefox with a GPU)
- **Code:** https://github.com/ErwinPawliczek/OilFlood (setup steps in README.md)

---

## Tagline (< 200 chars)

> Click the Gulf of Mexico, spill oil there now, and see where it goes in 72 h on live NOAA currents and winds: two models, threatened shorelines, and a response outline.

(169 characters.)

---

## Inspiration

In the first hours of an oil spill, responders have to decide where the oil will go, which shoreline it reaches first, and where
to put a limited amount of boom. NOAA does this with GNOME and trained modellers. A local emergency manager, a small port, or a
student usually has no quick way to ask "if X barrels spill here, now, where does it go?"

The 2014 Texas City "Y" collision is a good example: a ship hit a barge carrying heavy fuel oil (IFO 380) in Galveston Bay, up to
4,000 barrels were at risk, the Houston Ship Channel was closed, oil left the bay and was seen in the Gulf on the next days, and it
came ashore on Matagorda Island on March 27. We wanted a tool that answers the first questions in seconds, on real forecast data,
and is honest about how well it does on spills that already happened.

## What it does

OilFlood is a 3D globe in the browser with one draggable panel and two modes.

**Prediction mode (live).** Click anywhere in the Gulf of Mexico (or press "Release it off Galveston", a suggested point about
30 km SE of Galveston). Choose the amount in barrels and one of 9 oil types from NOAA's ADIOS database (light/medium/heavy crude,
IFO 380 bunker fuel, No. 6 fuel oil, diesel, jet fuel, gasoline, condensate). The spill starts at the current hour and runs for up
to 72 h on the newest **NOAA RTOFS** ocean-current forecast and **NCEP GFS** winds, which our server refreshes 4 times a day. The
panel says which forecast run was used and how far ahead it is valid. A 72 h run with 2,000 particles takes about 15–25 s. Then
the slick plays back on the globe: drift, evaporation, dispersion, emulsification and oil stranding on the coast, with an oil
budget (surface / evaporated / dispersed / stranded).

**Two models side by side.** "OpenOil" is the established baseline (OpenDrift OpenOil). "Ours" is our own particle model with an
optional machine-learned drift correction: **experimental and OFF by default** (our backtest found no gain on Deepwater Horizon
and false stranding at Main Pass 2023), switched on only with the "[EXPERIMENTAL]" checkbox. "Compare" draws both at once (ours as a black slick, OpenOil as a dashed outline, both labelled on the map) with separate budgets, so you can see
where they disagree.

**Response outline.** From the forecast, OilFlood builds a first-responder outline:
- time to first landfall,
- priority shorelines ranked by NOAA Environmental Sensitivity Index (ESI) class and arrival time, named after the nearest town
  and shown as numbered markers on the globe,
- where to boom first, with the real USCG Tier 1 lead time (6 h in the federal "higher-volume port areas" such as Galveston
  Bay, 12 h elsewhere, 33 CFR 154.1045) compared with the oil's best-guess and earliest plausible arrival,
- hourly go/no-go gates for dispersants, in-situ burning and boom + skimmers taken from the real Gulf protocols (RRT-6
  dispersant pre-approval: >= 3 nm AND >= 10 m deep, wind <= 25 kt, daylight, API gravity band; RRT-6 burn policy: water in
  emulsion <= 25 %, > 500 bbl per burn, wind <= 25 mph, current <= 0.75 kt, sun > 15°), each shown as a chip with its source,
- "best guess + uncertainty" like NOAA: the forecast plus a dashed envelope of where oil could go if the wind drift were 1-4 %,
- closures to consider (safety zone, channel entrances, fisheries),
- precedents: what responders actually did at similar past Gulf spills, each with a verbatim quote and the NOAA IncidentNews link,
  and next to every recommended action what Deepwater Horizon and Texas City "Y" responders did (NOAA OSC report, NTSB),
- a short "How we go from model to action" explainer (forecast -> priority sites -> tactic gates -> lead time vs arrival ->
  assignments) linking docs/PROTOCOLS.md, where every threshold is sourced.

**Response map layer.** The same outline is drawn on the globe for whichever run is on screen (live prediction or historical
hindcast, OpenOil or ours) and follows the time slider:
- the threatened coastline itself (real NOAA ESI shoreline lines) coloured by predicted oil arrival (< 12 h red, 12–24 h orange,
  24–48 h yellow, 48–72 h pale) and brightening when the oil gets there, plus offshore arrival outlines at T+6/12/24/48/72 h;
- a boom plan on the actual shoreline where the oil comes ashore, typed by the forecast current ACROSS each boom line:
  exclusion boom up to 0.7 kt, deflection boom angled to the current at 0.7-1 kt, "boom won't hold" above 1 kt (shoreline crews
  + skimmers downstream); beach crews on exposed beaches, boom across nearby channel entrances; each with its Tier 1 "on scene"
  time and the nearest real staging base (USCG stations, MSRC / Clean Gulf / Texas GLO equipment depots, cited);
- dispersant and in-situ burn zones every 3 h, only where every protocol gate passes and inside the RRT-6 pre-approval zone
  (10 m depth line or 3 nmi from shore, whichever is farther; real ETOPO1 bathymetry), the 10 nm RRT-6 planning circle around
  the source, the dashed uncertainty envelope, and skimmer targets on the densest patches of surface oil;
- a "Next actions" list in the panel (T+ hours, click to fly there) and a Response layer on/off toggle with a legend;
- playback: boom sites go PENDING → EN ROUTE (crews and vessels move from their staging base at road/water speed) → DEPLOYED,
  shorelines flash in the 6 h before the oil arrives.

**Action timeline.** Step 5 turns the forecast into a time-series document: one row per thing to do (notification, Tier 1/2/3,
boom on scene and its deadline = P10 arrival, each tactic window opening/closing, P10/P50 shoreline arrival, re-forecast
checkpoints every 6–12 h), with UTC + local time, where, why (the forecast number + the protocol rule ID) and the confidence
(ensemble probability or share of members whose gates pass). Click a row to move the clock; export it as Markdown, CSV or a
printable page. The oil itself is now drawn as a black slick (merged polygons, darker where thicker) instead of dots.

It is labelled as a planning aid, not operational guidance. An optional LLM summary can be added on top (see below).

**Probabilities in 3D, and build your own model.** One forecast is a guess; responders need to know how sure it is. Our
model runs as an ensemble (20 members by default, up to 30): each member draws its own windage (1–4 %, NOAA's GNOME range), wind
direction error (σ 20°), current-speed error (σ 20 %) and diffusivity. Tilt the map to 3D and P(oil present) rises out of the
sea as smooth columns that animate with the clock, with the 50 % and 90 % contours draped underneath and every threatened
shoreline coloured by P(oiled) and labelled with its P10 / P50 / P90 arrival time. A forces view puts arrows at the slick centre
for each push (current, wind drift, Stokes, ML correction) chained into the net drift, with a hover explanation and equation,
plus charts of drift by force and the oil budget over time. The "Build your model" panel exposes every parameter (units,
default, cited source); change windage or switch on Stokes drift, press *Run my model*, and the panel shows how the probability
map and the action plan moved (for example: earliest plausible landfall 42 h → 38 h, P(oiled) at Port Mansfield 60 % → 80 %).
A share link reproduces the exact model. With an ensemble the action plan becomes probabilistic: shorelines ranked by
P(oiled) × ESI sensitivity, boom "deploy by" = the P10 arrival (in place in time in 9 of 10 futures), and each tactic window
shows the share of members whose protocol gates pass. A /methodology page explains the whole pipeline with the equations and
the validation numbers, including the honest part: on 12 DWH days the P ≥ 10 % area holds ~70 % of the observed slick vs ~35 %
for a single run, but the centroid ellipses are too narrow.

**Historical mode.** 9 Gulf of Mexico spills from NOAA IncidentNews, 1979–2023 (Deepwater Horizon, Ixtoc I, Texas City "Y",
Mega Borg, Burmah Agate, Barge DBL 152, MC 209, Eugene Island, Main Pass). For the 6 spills after 2001, a hindcast runs on the
HYCOM ocean reanalysis for those exact dates. Deepwater Horizon also shows 94 daily NOAA/NESDIS satellite slick outlines. Each
spill has a response log: 280 dated responder actions (boom, skimming, dispersants, burns, closures, wildlife, ...) and 120 notes on
where the oil went, each with a verbatim quote from its source. Clicking a date moves the clock there. A Backtest section shows how
each model scored against what was observed.

Underneath everything are animated ocean currents (windy.com style): the Gulf at 0.1° on top of global RTOFS (live) or HYCOM
(historical), coloured by speed and synced to the clock.

## How we built it

**Data (all real, no mocks).**
- Live currents: NOAA NCEP RTOFS global 1/12° (NOMADS), with HYCOM ESPC-D-V02 as automatic fallback. Live winds: NCEP GFS 0.25°.
  A systemd timer on our server fetches both 4×/day (02/08/14/20:30 UTC), cuts the Gulf box out of the global files and builds the
  flow-layer textures.
- Hindcasts: HYCOM-TSIS GOMb0.04 Gulf reanalysis currents + its 10 m winds; HYCOM GOFS 3.1 for the global historical layer.
- Spills and response logs: NOAA IncidentNews. Every quote in the catalog was machine-checked against the downloaded post text/PDFs
  (0 misses). What the sources don't say is listed as a "gap" instead of being filled in.
- Observed slicks: 94 NOAA/NESDIS Deepwater Horizon outlines (May 2 – Aug 25, 2010).
- Drifters: NOAA AOML Global Drifter Program hourly data (ERDDAP).
- Shoreline sensitivity: NOAA OR&R National ESI Shoreline (237 MB download → 2,686 cells of 0.05° on the Gulf coast, TX to the
  Florida Keys). Place names: US Census 2023 Gazetteer. Oil properties: NOAA ADIOS. Coastline: GSHHG.

**Baseline model (pre-existing): OpenDrift OpenOil** (MET Norway), set up the way GNOME-style responders use it: currents + 2 %
wind drift + Stokes drift, random-walk diffusion, NOAA weathering (evaporation, emulsification, dispersion) and stranding on the
coastline.

**Our own model** (`backend/app/ourmodel/`, numpy). Same forcing files, same oil record, same seed and output format as OpenOil, so
the two can be compared directly. Lagrangian particles with RK2 advection (current + optional ML correction + 3 % wind), random-walk
diffusion, Fay spreading written as an equivalent diffusivity, Fingas evaporation from the ADIOS distillation cuts, Mackay
emulsification and natural dispersion, and beaching on GSHHG or ocean-model land. A 72 h, 2,000-particle run takes ~12–17 s
(OpenOil ~21–26 s).

**ML drift correction (the AI model we trained).** Ocean models get surface currents wrong. We trained two scikit-learn
HistGradientBoosting regressors (east and north) to predict that error: target = real drifter velocity minus the model current.
- Training data: 42 NOAA drifters, 14,089 hourly fixes in the Gulf, paired with the HYCOM reanalysis forcing the drifters actually
  saw. Only 3 of our reanalysis windows have drifters (DWH 2010 is 88 % of the fixes; MC 209 2017; Eugene Island 2009).
- Features: current, wind, speeds, current vorticity and divergence, drogue flag.
- Validation: 5-fold cross-validation grouped by drifter (a drifter is never in both train and test). Velocity RMSE
  0.410 → 0.358 m/s. Virtual drifters released along held-out tracks: median 24 h separation 21.2 → 18.1 km (mean 25.4 → 22.0),
  48 h 38.0 → 34.5 km.
- For oil we average the drogued and undrogued predictions so the oil's 3 % windage is not counted twice, and apply the correction
  only inside the area the training drifters covered.
- In the app the correction is **OFF by default and labelled experimental** (`ml=false` in the API; a checkbox turns it on),
  because the oil backtest below shows no gain.

**Backtest** (`python -m app.backtest`, results in docs/BACKTEST.md). OpenOil vs ours (ML on and off) vs observed, on the same
reanalysis forcing. Metrics: centroid separation and Liu–Weisberg skill at 24/48/72 h, IoU and fractions skill score against the
satellite outlines. Deepwater Horizon: 52 start days, each seeded from that day's NESDIS outline and compared with the next 3 days.
Also Texas City "Y" (overflight positions) and Main Pass 2023 (reported direction, length, no shoreline impact).

**Response outline** (`backend/app/response.py`, rule-based). Stranded particles are matched to ESI cells (6 km) for landfall;
surface oil within 10 km of shore counts as threatened. Shore stretches are ranked landfall > threatened, then ESI tier
(9–10 marshes and sheltered flats first), then arrival time. Tactic windows apply the gates of docs/PROTOCOLS.md
(`backend/app/protocols.py`, every threshold with a rule ID and source): API gravity and pour point from the ADIOS oil record,
emulsion water content from Mackay's formula on the forecast wind, surface oil per ~6 km patch for the 500 bbl burn minimum, the
run's forecast wind and current at the slick, solar elevation, distance from shore and ETOPO1 depth. Boom lead time follows
33 CFR 154.1045 (notify 30 min, en route 2 h, Tier 1 6 h / 12 h); the uncertainty envelope re-drifts the particles with GNOME's
1-4 % windage range. Precedents are past catalog spills scored by oil group, size and distance.
The map layer (`backend/app/response_layer.py`) reads the real ESI shoreline geometry for the oiled stretches, intersects the
particle positions with the regulatory envelope on a ~2 km grid every 3 h, and uses a cited list of 54 Gulf staging bases
(`data/response/staging_bases.json`); the rules and their sources (RRT-6/RRT-4 pre-authorization, NOAA ORR, ITOPF TIP 3, 33 CFR
155/154) are in docs/RESPONSE.md and docs/RESPONSE_RULES_SOURCES.md.

**Optional LLM briefing** (`backend/app/briefing.py`). The compacted outline JSON is sent to an LLM (Anthropic, OpenAI or xAI,
whichever key is set) with the instruction to use only those facts, ≤ 170 words, most urgent first. Without a key it is skipped
and the panel shows the rule-based outline only. *(Be honest here: as of writing no key is set on the live site, so the
briefing is not shown on oilflood.xyz.)*

**Stack.** Frontend: Vite + TypeScript, MapLibre GL v5 globe + deck.gl 9 (no framework). Backend: FastAPI, OpenDrift 1.14,
xarray, numpy, scikit-learn, a 4-process worker pool with a run cache keyed by request + forcing files. Hosted on our own Linux
machine behind a Cloudflare tunnel at oilflood.xyz.

## Challenges we ran into

- **Our ML helps on drifters but not on oil.** On held-out drifters it cuts the 24 h error by ~3 km. On the oil backtest it does
  not help: on Deepwater Horizon it ends closer than the no-ML run on only about half the start days (52 / 54 / 48 % at
  24/48/72 h) and is a little worse in the medians. At Main Pass 2023 it strands 62 % of the oil on the Mississippi Delta within
  9 h, which did not happen (OpenOil and ours without ML strand 0 %, as observed). Drifters are not oil, and 88 % of the training
  data is one summer in deep water. We kept the result in the docs instead of hiding it.
- **Nothing beats persistence on Deepwater Horizon.** Over 52 start days, "the slick stays where it was seen" has the smallest
  median centroid error (23 / 31 / 39 km at 24 / 48 / 72 h) vs OpenOil 27 / 36 / 44 km, ours + ML 25 / 36 / 46 km and ours without
  ML 25 / 33 / 42 km. We suspected the missing wellhead release, so we added it to both models (official federal flow estimate
  minus the oil collected at the well) and re-ran: medians moved by 0–2 km (OpenOil 44 → 43 km, ours + ML 46 → 44, ours without ML
  42 → 42 at 72 h) and persistence still wins. The source term was not the explanation.
- **Bays are land in the ocean models.** At ~4 km resolution HYCOM treats Galveston Bay as land, so oil released at the Texas City
  "Y" collision point strands 100 % in the bay within the run. Seeded at the bay entrance instead, OpenOil gets within 15 km and
  8 km of the two overflight positions on Mar 24 and 25.
- **Getting a live forecast that reaches 72 h.** The newest HYCOM ESPC run on the public server was 5 days old and ended short of
  now + 72 h. We switched live currents to NOAA RTOFS (newest complete daily run on NOMADS) and cut the Gulf out of the 148 MB
  global files; HYCOM stays as fallback. If a forecast is short, the run stops at its end and the panel says so.
- **Rendering.** Some deck.gl layers misrender on the MapLibre globe; we used binary PathLayers and HTML labels instead. A cached
  layer instance re-added after a camera jump triggered a deck.gl assertion; we fixed it by building a fresh layer each frame on
  memoized data.
- **Real data only.** We removed every mock and synthetic fallback. Spills before 2001 (Ixtoc I, Burmah Agate, Mega Borg) have no
  ocean reanalysis, so they show the record and response log but no hindcast.

## Accomplishments that we're proud of

- A live pipeline: click, and a 72 h forecast on the latest NOAA currents and winds comes back in about 20 s.
- Our own physics model that runs on the same inputs as OpenOil and can be compared to it on the same screen.
- A trained ML model with a proper hold-out test (grouped by drifter), and a backtest that reports where it fails.
- A spill + response database of 9 Gulf spills with 280 dated responder actions and 120 movement notes, every one with a source
  link and a verbatim quote that was checked against the source.
- A response outline built on the real NOAA ESI shoreline data (2,686 coastal cells, TX to the Florida Keys) that cites what
  responders did at similar spills.
- The README setup was tested in a fresh clone with a new conda environment.

## What we learned

- Oil-spill trajectory physics: wind drift factors (~3 % of the 10 m wind including Stokes drift), Fay spreading, Fingas
  evaporation, Mackay emulsification, and why weathering changes where oil goes.
- How to score a trajectory forecast: Liu–Weisberg skill, centroid separation, fractions skill score, and why you need a
  persistence baseline before claiming skill.
- An ML model can improve the thing it was trained on (drifters) and still not improve the thing you care about (oil). The
  backtest is what told us.
- How responders actually work: ESI sensitivity ranks, dispersant and in-situ burn windows, RRT pre-authorization zones, and the
  rule that the On-Scene Coordinator decides.
- Practical NOAA/HYCOM data plumbing: NOMADS, THREDDS, ERDDAP, CF NetCDF, and that servers hang.

## What's next

- **Continuous release in the app**: both models already take an optional point source (used in the DWH backtest); expose it in
  prediction mode for wellheads and leaking pipelines, with a subsurface (plume) release instead of a surface one.
- **More ML training data**: more reanalysis windows with drifters (e.g. 2012, 2015–2016) so the correction is not one summer of
  deep water; ensembles instead of a single run.
- **Bays and nearshore**: higher-resolution coastal models (e.g. NOAA's operational coastal forecast systems) so spills inside
  Galveston Bay don't strand immediately.
- **Waves** in the response windows (in-situ burn and boom limits depend on them; depth is now checked, waves are not), and
  the real Geographic Response Plan boom sites (Texas GLO / Louisiana ACP) instead of our generic boom rule.
- **Beyond the Gulf**: the global current layer already works; the forcing for predictions is only cached for the Gulf.
- Testing with actual responders before anyone uses the outline for planning.

## Built with

`python` `typescript` `fastapi` `uvicorn` `opendrift` `numpy` `xarray` `netcdf` `scikit-learn` `gradient-boosting`
`machine-learning` `maplibre` `deck.gl` `webgl` `vite` `noaa-rtofs` `hycom` `gfs` `noaa-global-drifter-program` `noaa-esi`
`noaa-incidentnews` `noaa-adios` `gshhg` `systemd` `cloudflare-tunnel` `conda` `linux`

(Optional, only if a key is set on the live site at submission time: `anthropic` / `openai` / `xai` for the LLM briefing.)

---

### Judging criteria checklist (for us, not for pasting)

| Criterion | Where it shows |
|---|---|
| Impact | first-hours questions for responders and local emergency managers; free, no account; ESI shorelines + cited precedents |
| Technical execution | live RTOFS/GFS pipeline, two models on the same forcing, ML with grouped CV, 52-start-day DWH backtest |
| Creativity | ML drift correction learned from drifters; Compare view; response outline that quotes what past responders did |
| Presentation | globe + one panel; demo script in docs/DEMO.md; honest limits in README and BACKTEST |
| Uses / trains an AI model | trained HistGradientBoosting drift correction; OpenOil baseline; optional LLM briefing |
