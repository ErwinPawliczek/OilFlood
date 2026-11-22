# Response outline (T16, PLAN P6)

After a prediction run (or a historical hindcast), OilFlood builds a **first-responder action outline**:
time to first landfall, priority shorelines ranked by NOAA ESI sensitivity and arrival time, where to boom first,
dispersant / in-situ burn / skimming windows, closures, and cited precedents from past Gulf spills.

> **Not operational guidance.** It is a planning aid generated from one model forecast. The Federal On-Scene
> Coordinator / Unified Command, the Area Contingency Plan and the Regional Response Team decide every response
> action. The panel and the JSON both carry this disclaimer.

## API
- `GET /api/spill/{run_id}/response` – for a prediction run (`id` from `POST /api/spill`). 202 + status while the run is
  still going, 409 if it failed.
- `GET /api/spills/{spill_id}/response?model=openoil|ours` – for a catalog spill's hindcast (starts it if needed).
  That spill's own response log is left out of its precedents.
- `?brief=0` skips the LLM layer.
- Cached as `backend/runs/<id>/response.json` (rebuilt when `response.VERSION` changes); LLM text in `briefing.json`.

Main keys: `disclaimer`, `headline[]`, `spill` (oil class, volume, horizon, forecast), `eta_first_landfall_h`,
`first_landfall`, `budget[]` (% surface / evaporated / dispersed / stranded at 6/12/24/48/72 h),
`priority_shorelines[]`, `boom_first[]`, `tactics.{dispersant,in_situ_burn,mechanical}` (`windows[]` in hours after
release + UTC, `not_applicable`, `conditions[]`), `conditions` (wind/current along the slick, 3-hourly series),
`closures[]`, `precedents` (`similar_spills[]`, `by_tactic.{category: [{spill, date, action, quote, src}]}`),
`sources[]`, `limits[]`, `briefing` (`text` or `null` + `note`).

Code: `backend/app/response.py` (rules), `backend/app/briefing.py` (LLM), `web/src/outline.ts` (panel section +
numbered shoreline markers on the globe).

## Inputs (all real)
| Input | Source |
|---|---|
| Particle tracks, oil budget | the run's `tracks.json` (OpenOil or our model) |
| Winds, currents along the slick | the run's own forcing files (live: GFS 0.25° winds + RTOFS currents; hindcast: HYCOM-TSIS), sampled hourly at the slick centroid |
| Shoreline sensitivity | NOAA OR&R **National ESI Shoreline** (Gulf/Atlantic ESIL, 2017 compilation of the state ESI atlases 1996–2017), `NationalESI_Shoreline.zip` from https://response.restoration.noaa.gov/esi_download |
| Place names | US Census Bureau 2023 Gazetteer (places) |
| Precedents | `data/spills/catalog.json` response logs (NOAA IncidentNews posts, verbatim quotes, T13) |

### ESI derivative (`data/esi/gulf_esi_cells.json`, 127 KB, committed)
`data/build_esi.py` reads the 413k Gulf ESI shoreline features (TX, LA, MS, AL and the Florida Gulf coast incl.
Florida Bay/Keys; estuarine/marine shoreline lines only) and bins them into 0.05° cells (~5 km): 2,686 cells,
127,500 km of mapped shoreline (most of it Louisiana marsh). Per cell: length-weighted centroid, shoreline km,
**most sensitive ESI class**, length-dominant class, km of ESI 10 wetlands, state atlas + year. ESI labels come from the
data's own `LANDWARD_SHORETYPE` strings. The 237 MB download + 393 MB unzipped GDB stay in `data/raw/` (gitignored).
`data/esi/gulf_places.json` (24 KB): 632 Census places within ~12 km of that shoreline.

## Rules
### Landfall, threat and priority shorelines
- A **stranded** particle's last position is matched to the nearest ESI cell within 6 km → landfall, ETA = stranding
  time. Mass per stranded particle = stranded mass at the end of the run / stranded particles.
- **Threatened**: surface oil (hourly positions) within 10 km of a shoreline cell (nearest cell only, so the outer
  coast, not the bay behind it), ETA = first approach.
- Cells are grouped into stretches of ≤ ~30 km, seeded at the most-oiled cells, and named by the nearest Census place.
- Sensitivity tier from the stretch's most sensitive ESI class (NOAA ESI guidelines: higher = more sensitive, longer
  oil persistence, harder cleanup): **tier 1** ESI 9–10 (sheltered flats, marshes, swamps, mangroves), **tier 2** ESI 7–8
  (exposed flats, sheltered rocky/riprap/structures), **tier 3** ESI 1–6 (beaches, exposed/armored shores).
- Ranking: landfall (≥ 0.5 % of particles) before merely threatened → tier → earliest arrival. This follows the
  usual ACP protection-priority logic (protect the most sensitive habitat the oil reaches first).
- Each stretch carries a shoreline treatment note by ESI class (marsh: exclusion boom, no foot traffic or cutting,
  low-pressure flushing/sorbents, natural recovery; sand beaches: manual/mechanical removal, protect inlets behind
  them; riprap/structures: flushing, sorbents; flats: natural recovery, avoid trampling), summarised from NOAA ORR
  *Characteristics of Response Strategies* (2010) and the NOAA Shoreline Assessment Manual.

### Boom first
Top 5 landfall stretches in priority order. Action by tier (exclusion/deflection boom at inlets and marsh openings;
boom sheltered shore + stage skimmers; protect inlets behind beaches + beach crews). If oil arrives in < 6 h (assumed
boom staging time) the item says it is likely too late and to protect the next stretch / send shoreline crews.

### Windows (hourly, at the slick centroid)
Oil types map to groups (≈ ITOPF/NOAA persistence groups): gasoline, condensate, jet fuel (non-persistent),
diesel, light / medium / heavy crude, heavy fuel oil (IFO 380, No. 6).

| Tactic | Oil-age limit by type | Weather / other limits |
|---|---|---|
| Dispersant | light crude ≤ 48 h, medium ≤ 36 h, heavy crude ≤ 12 h; not for gasoline/condensate/jet/diesel (evaporate/disperse naturally); heavy fuel oil: generally too viscous (test first, RRT approval) | wind 4–25 kt, daylight, ≥ 3 nm from shore (RRT 4/6 pre-authorization zone, which also needs ≥ 10 m depth – depth is not checked) |
| In-situ burn | light crude ≤ 48 h, medium ≤ 36 h, heavy crude ≤ 24 h, heavy fuel ≤ 12 h, diesel ≤ 24 h; not for gasoline/condensate/jet | wind ≤ 20 kt, daylight, ≥ 3 nm offshore; needs fire boom to ≥ 2–3 mm, < ~25 % water in emulsion, waves < ~3 ft (not modelled) |
| Boom + skimmers | all but gasoline/condensate/jet | wind ≤ 20 kt, daylight; containment boom loses oil above ~0.75 kt relative current (mean/max current reported) |

The oil-age limits are the "window of opportunity" figures commonly given for dispersants and burning before
evaporation and emulsification raise viscosity / water content (NOAA ORR *Characteristics of Response Strategies*;
NOAA *Dispersants: A Guided Tour*; ITOPF TIP 4 *Use of dispersants*; NOAA/USCG *In-situ Burning* guidance and the
RRT 6 FOSC in-situ burn and dispersant pre-authorization plans). Daylight = solar elevation > 0 at the slick
(NOAA solar position approximation).

### Closures
- USCG safety zone over the slick extent (bbox at 24/48/72 h).
- Navigation: any surface oil within 15 km of a listed channel entrance (Brazos Santiago … Key West, approximate
  coordinates in `response.py`) → "consider traffic restriction / boom across the entrance" with ETA (precedent:
  Houston Ship Channel closure after Texas City Y, 2014).
- Fishery/shellfish: for persistent oils or any landfall: precautionary closure over the slick + oiled/threatened coast,
  with the state agencies of the affected states and NOAA Fisheries.
- Gasoline/condensate/jet: vapour/fire hazard first (LEL monitoring, keep clear, shelter-in-place downwind).

### Precedents
Catalog spills are scored by oil-group match (+2 same group, +1 neighbouring), size similarity (+≤1.5, by log ratio
of volumes) and distance (+≤1 within 400 km). For each recommended tactic the earliest matching action of the best
spills is cited with its date, verbatim quote and IncidentNews URL.

## LLM briefing (optional)
`briefing.py` sends the compacted outline JSON (no hourly series) to an LLM with instructions to use only the facts in
it, ≤ 170 words, most urgent first, ending "Planning aid only, not operational guidance." Key from the environment
only: `XAI_API_KEY` (xAI, default `grok-3-mini`), `OPENAI_API_KEY` (`gpt-4o-mini`) or `ANTHROPIC_API_KEY`
(`claude-haiku-5-5`); `OILFLOOD_LLM_MODEL` overrides the model. Also read from `backend/.env` (gitignored).
No key → `briefing.text = null` and the panel shows the rule-based outline only (no placeholder text).

## Limits
- One deterministic forecast run per model; real oil can go elsewhere. Compare OpenOil vs ours and re-run as forecasts
  update.
- Stranding happens on the model coastline (GSHHG / ocean-model land at ~1–4 km). Bays the ocean model treats as land
  (Galveston Bay, Barataria, Mobile Bay interiors) are not resolved; 6 km/10 km matching radii pick the outer coast.
- Waves, water depth, slick thickness and emulsion water content are not modelled; windows use wind, daylight,
  distance from shore and oil-age limits only. Wind/current are sampled at the slick centroid, not per patch.
- `shoreline_km` is mapped ESI shoreline length inside the stretch's cells (convoluted marsh edges make it large), not
  the length oiled.
- Boom staging time (6 h), stretch size (30 km) and port proximity (15 km) are planning assumptions.
- ESI atlases date from 1996–2017; shorelines have changed since (especially Louisiana).
