# data/ — forcing + spill catalog

Scripts that download Gulf of Mexico ocean/wind forcing as OpenDrift-ready NetCDF, the historical spill catalog, Deepwater Horizon slick outlines, and downsampled current fields for the globe's flow layer. No accounts or API keys are needed.

Box: 18N–31N, 98W–80W. Everything is **idempotent and resumable**: raw chunks are kept in `data/raw/`, finished files are skipped on re-run (`--force` rebuilds).

## Setup

```bash
cd data
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
(Any env with these packages works, e.g. the backend's conda env `oilflood` plus `pyshp pillow`.)

## Commands (run from `data/`)

| Step | Command | Time | Output |
|---|---|---|---|
| Spill catalog | `.venv/bin/python build_catalog.py` | 5 s | `spills/catalog.json` (committed) |
| DWH outlines | `.venv/bin/python fetch_dwh_footprints.py` | 1 min | `spills/dwh/footprints/dwh_YYYYMMDD.geojson` (94 days, 3.7 MB, committed) |
| Texas City Y hindcast | `.venv/bin/python fetch_hycom.py --window texas_city_y` | ~5 min | `cache/forcing/texas_city_y/{currents,winds}.nc` |
| All catalog hindcasts | `.venv/bin/python fetch_hycom.py --catalog` (or `--only dwh,mc209_2017`) | DWH ~30–60 min, others ~5 min each | `cache/forcing/<spill_id>/{currents,winds}.nc` |
| Live forecast | `.venv/bin/python fetch_live.py` | 10 s | `cache/forcing/live/{currents,winds}.nc` |
| Frontend fields | `.venv/bin/python export_fields.py --all` | ~10 s per window | `cache/fields/<window>/` |

Custom hindcast window: `fetch_hycom.py --window my_case --start 2012-06-01 --end 2012-06-04 --stride-h 3`.

Refresh the live window (e.g. on demo morning): `fetch_live.py --force && export_fields.py live --force`.

## Sources

| What | Source | Coverage | Notes |
|---|---|---|---|
| Hindcast currents | HYCOM-TSIS **GOMb0.04** reanalysis (1/25°, ~4 km, hourly), `u`/`v` at 0 m via NCSS `https://tds.hycom.org/thredds/ncss/GOMb0.04/reanalysis/<year>/3z` | 2001-01-01 – 2024-08-31 | |
| Hindcast winds | Same reanalysis, `wnd_ewd`/`wnd_nwd` (10 m wind the model was forced with) from `.../<year>/2d` | same | Saves an ERA5/CDS account |
| Live currents | HYCOM **ESPC-D-V02** global 1/12°, FMRC best time series, 3-hourly, `https://tds.hycom.org/thredds/ncss/FMRC_ESPC-D-V02_uv3z/FMRC_ESPC-D-V02_uv3z_best.ncd` | ~10 days back to the end of the latest forecast | The forecast can end a few hours short of now+72 h (the script logs it). |
| Live winds | NCEP **GFS 0.25°** 10 m u/v, UCAR THREDDS `Global_0p25deg/Best`, 3-hourly | ~3 weeks back, +7 days | |
| Spill catalog | NOAA **IncidentNews** `https://incidentnews.noaa.gov/raw/incidents.csv` + curated volumes | | |
| DWH outlines | NOAA/NESDIS SAB `https://satepsanone.nesdis.noaa.gov/pub/OMS/disasters/DeepwaterHorizon/`: daily composites (2010-05-17+), unioned MPSR shapefiles before that | 2010-05-02 – 2010-08-25 (94 days) | |

Not used: NOAA RTOFS (NOMADS retired OPeNDAP, SCN 25-81; full RTOFS files are global and very large), ERA5/CMEMS (need accounts).

## File formats

### Forcing NetCDF (`cache/forcing/<window>/`)
- `currents.nc`: `x_sea_water_velocity`, `y_sea_water_velocity` (m s-1), dims `(time, lat, lon)`, surface only.
- `winds.nc`: `x_wind`, `y_wind` (m s-1, 10 m), dims `(time, lat, lon)`.
- Coordinates `time` (CF, seconds since 1970), `lat`, `lon` (−180..180, ascending), with `standard_name`s. Land = NaN.
- Opens directly with `opendrift.readers.reader_netCDF_CF_generic.Reader(path)` (checked with OpenDrift 1.14.11 in the `oilflood` env). The backend (`backend/app/forcing.py`) finds them by scanning `data/cache/**/*.nc`. Raw chunks are kept in `data/raw/` so they don't show up twice.

### Spill catalog (`spills/catalog.json`)
A list of `{id, name, location, date_start, date_end (ISO UTC), lat, lon, volume_bbl, volume_note, oil_type, commodity, incidentnews_id, source_urls, has_observed_outlines, forcing ("hycom_gomb0.04" | null), summary, release, [forcing_window]}`.
`volume_bbl` is the best spilled estimate from the narrative/official reports, not IncidentNews' "max potential" (see `volume_note`). Spills before 2001 (Ixtoc I, Burmah Agate, Mega Borg) have `forcing: null`: no HYCOM reanalysis exists for them.

### Footprints (`spills/<id>/footprints/<id>_YYYYMMDD.geojson`)
One FeatureCollection per day, one MultiPolygon feature (WGS84, simplified ~400 m) with properties `{date, spill, kind: composite|mpsr_union, area_km2, n_files, source}`. The backend serves them at `GET /api/spills/{id}/footprints`.

### Frontend fields (`cache/fields/<window>/`)
- `index.json`: `{window, bbox, lon0, lat0, dlon, dlat, nx, ny, times: [unix_s], vmax, units, png_encoding, source}`.
- `<t>.json`: `{t, lon0, lat0, dlon, dlat, nx, ny, u: [...], v: [...]}`. Row-major, rows **south→north**, `null` = land. This is the same shape as `GET /api/currents/{t}`.
- `<t>.png`: RGBA, 181×131 at 0.1°, rows **north→south**. `R = (u/vmax+1)·127.5`, `G = (v/vmax+1)·127.5`, `B = speed/vmax·255`, `A = 255` water / `0` land, `vmax = 2 m/s`.
- One field per hour for hindcasts up to 30 days, every 6 h for longer ones (DWH), and every 3 h for live.

## Sizes (cached on the tower)
See the "Cached" table in SYNC.md for exact sizes. Typical sizes: hindcast window, hourly, 5 days ≈ 100 MB currents + 30 MB winds. Live ≈ 9 MB. Fields ≈ 0.3 MB per timestep.
