# OilFlood demo video: 2:40 script + shot list

Target length **2:40** (limit 2–3 min; the 3D/builder beat at 1:10 added 20 s). Screen recording of https://oilflood.xyz with voice-over. Each block below lists the time,
what to click and what to say. The narration is ~340 words (about 135 words per minute), which leaves a few seconds of slack per block.

Rules for the narration: say only what is on screen or in the repo docs. Live-forecast numbers (ETA, shoreline names, oil
percentages) change with every forecast run, so **read them off the panel while recording**. The spots marked `[read: ...]` are
where you do that. Do not read out a number you did not see on screen.

Judging criteria: **I** = impact, **T** = technical execution, **C** = creativity, **P** = presentation.

---

## Shot list

### 0:00 – 0:15 · The problem (I)
- **Screen:** oilflood.xyz is already open. The globe has flown to Houston/Galveston, live currents are streaming, and the panel is
  on Prediction with the pulsing suggested point. Slowly zoom out to show the whole Gulf, then back in.
- **Say:** "When oil spills, responders have hours to decide where it will go, which shoreline it hits first and where to put the
  boom. OilFlood answers that in about twenty seconds, on today's real NOAA ocean and weather forecast."

### 0:15 – 0:45 · Live prediction (T, P)
- **Screen:** in the panel set *Oil type* = **Bunker fuel (IFO 380)** and *Amount* = **4000** bbl (the Texas City "Y" scale).
  In step 1 point at **Live data**. In step 3 click **Run forecast off Galveston**. The progress bar runs, then the slick plays.
  Drag the time scrubber from 0 to 72 h, then let it play.
- **Say:** "I'll release four thousand barrels of bunker fuel thirty kilometres off Galveston, starting now. It runs on NOAA's
  RTOFS currents and GFS winds, refreshed four times a day: `[read: currents issued …, valid until …]`. Two thousand particles drift, spread, evaporate and strand on the coast for 72 hours.
  The budget shows where the oil ends up: `[read: % surface / evaporated / stranded at 72 h]`."

### 0:45 – 1:10 · Two models + the ML (T, C)
- **Screen:** switch the model toggle to **Compare**. OpenOil (amber) and ours (cyan) appear together with two budgets.
  Hover the *Ours* button so its tooltip shows, or point at the one-line ML skill note.
- **Say:** "Two models. OpenOil is the established open-source model, our baseline. The cyan one is ours: our own particle physics
  plus a machine-learned correction for ocean-current error. We trained gradient-boosted trees on 42 NOAA drifter
  buoys, about fourteen thousand hourly positions. On drifters it never saw, it cuts the median 24-hour error from 21 to 18
  kilometres. Where they disagree, a responder should treat it as uncertainty."

### 1:10 – 1:30 · Your model, in 3D (C, T, P)
- **Screen:** open https://oilflood.xyz/?mode=prediction&lat=27.0&lon=-97.1&model=ours&ens=20 beforehand (Padre Island,
  20-member ensemble; it is cached after the first run). The map flies into 3D on its own and the probability columns rise.
  Drag the time slider so the columns grow and spread toward the shore. Click **Forces**, hover the blue (current) and amber
  (wind) arrows so the explanation pops up. Then open **Model → Advanced: build your model**, drag *Wind drift* to 4 %, switch on *Stokes drift*,
  press **Run with these**; when it finishes, hold on **What changed vs your last run**.
- **Say:** "One forecast is a guess, so we run twenty, each with different wind drift, wind direction and current error.
  The height is the probability of oil. These arrows are what pushes it: the current, the wind, and where they add up.
  And anyone can build their own model: more wind drift, Stokes drift on, run it, and see the plan change:
  `[read: earliest P10 landfall a → b, P(oiled) at PLACE x → y]`."
- If time is short, cut the 0:45 Compare block to 10 s.

### 1:30 – 1:50 · Response outline (I, C)
- **Screen:** scroll the panel to **Response outline** and expand it. Show the disclaimer line, the headline (first landfall),
  then **Priority shorelines**: click #1 so the globe flies to the numbered marker. Then **Boom first**, **Windows**
  (dispersant / in-situ burn / boom + skimmers), and open one **Precedents** entry so its quote and IncidentNews link show.
- **Say:** "Then it turns the probabilities into an action plan. `[read: top shoreline, P(oiled) and P10 arrival]`; boom has to be there before the P10 arrival. Shorelines are ranked by
  NOAA's Environmental Sensitivity Index and arrival time, so marshes the oil reaches first come first. It says where to boom
  first, when dispersants, burning or skimming are possible, and what responders did at similar Gulf spills, with the source
  quote. It's a planning aid; the On-Scene Coordinator decides."

### 1:50 – 2:20 · History and honesty (T, I)
- **Screen:** switch to the **Past spills** tab, pick **Deepwater Horizon**. The NESDIS satellite outlines and the hindcast appear; press play
  for a few seconds. Open **Response log** and click one date so the clock jumps. Then open **Backtest** and hold on the table.
- **Say:** "Historical mode replays nine real Gulf spills on the ocean reanalysis for their dates, with 280 logged responder
  actions, each with a verbatim source quote. For Deepwater Horizon we scored both models against 94 days of NOAA satellite
  outlines. Honest result: neither model beats assuming the slick stays put, mostly because the well kept leaking and we don't
  model that yet. And our ML helps on drifters but not yet on oil. That's the next thing to fix."

### 2:20 – 2:40 · Close (P, I)
- **Screen:** zoom out to the full globe with global currents streaming, panel collapsed (double-click its header). End card text
  overlay: **oilflood.xyz · github.com/ErwinPawliczek/OilFlood**.
- **Say:** "Everything is real public data: NOAA, HYCOM and the Global Drifter Program. It's free and needs no account. OilFlood: where
  the oil goes in the next 72 hours, and what to protect first."

---

## Criteria coverage

| Criterion | Where in the video |
|---|---|
| Impact | 0:00 problem, 1:30 probabilistic response outline, 2:20 free + no account |
| Technical execution | 0:15 live RTOFS/GFS pipeline, 0:45 two models + trained ML with held-out numbers, 1:10 ensemble + forces + build your model, 1:50 backtest |
| Creativity | 0:45 Compare view + learned drift correction, 1:10 3D probability columns + build-your-own model, 1:30 outline that cites past responders |
| Presentation | one continuous take on the live site, one panel, honest limits stated on camera |
| AI model requirement | 0:45 (trained gradient-boosting drift correction; OpenOil baseline) |

---

## Recording checklist

**Setup**
- [ ] 1920×1080, 30 or 60 fps. Desktop Chrome or Edge with hardware acceleration on (check `chrome://gpu`). Browser window
      full-screen (F11), zoom 100 %, bookmarks bar hidden, other tabs closed, notifications off.
- [ ] Clear site data once or open a fresh profile, so the panel sits in its default spot and the default view (fly to
      Galveston) plays. After that, set *Oil type* = IFO 380 and *Amount* = 4000 (they are saved in localStorage).
- [ ] Mic check; record the voice-over separately if the screen take needs retries.

**Preload (≈ 10 min before recording)**
- [ ] Check `https://oilflood.xyz/api/live`: `stale` is false and `hours_available` is close to 72. The forecast refreshes at
      02/08/14/20:30 UTC. Record **after** a refresh, not across one: runs are cached per forecast, and a new forecast means a
      new ~20 s run on camera.
- [ ] Do the whole prediction once off camera: **Run forecast off Galveston** with IFO 380 / 4000 bbl, then **Compare**, then open
      **Response outline**. On camera the same click then loads from cache.
- [ ] Read the outline. If it shows **no landfall** within 72 h for this forecast, either say "no landfall forecast in 72 hours,
      these shorelines are threatened" (if threatened stretches are listed) or click a point closer to shore and preload that
      instead. Write down the numbers to read: forecast issued/valid-until, 72 h budget, first landfall ETA + place.
- [ ] Open **Historical → Deepwater Horizon** once, with **Compare**, so both hindcasts and the outlines are cached. Open the
      **Backtest** section once.
- [ ] Backup for the response-outline shot: **Historical → Texas City "Y"** with OpenOil. Its outline is cached and was checked on
      2026-11-22: "First landfall in ~1 h near Bayou Vista, TX (ESI 10A: Salt and Brackish Water Marshes)". Use it if the live
      prediction gives no landfall.
- [ ] If the T18 QA branch changes the ML default (ML off or labelled "experimental") before recording, show that label at
      0:45 and say "experimental" instead of implying it is on by default.

**Do not**
- Don't show any terminal, `.env`, API key or the server.
- Don't say the LLM briefing is live unless a key has been set and the briefing text appears in the panel.
- Don't claim our model or the ML beats OpenOil. The backtest says they are about equal, and the ML does not help on oil.

**After**
- [ ] Trim to 2:20–2:40, add the end card, export 1080p MP4, upload to YouTube (unlisted or public) and paste the link into Devpost.
- [ ] Take 2–3 stills from the recording for the Devpost gallery (prediction + Compare, response outline, DWH backtest).
