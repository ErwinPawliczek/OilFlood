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
| Global live (RTOFS) | `.venv/bin/python fetch_global.py live` | ~1.5 min | `cache/fields/global/live/` |
| Global hindcasts (HYCOM) | `.venv/bin/python fetch_global.py --catalog` (or `--only dwh,texas_city_y`) | ~1–3 min per spill, DWH ~3 min | `cache/fields/global/<spill_id>/` |

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

| Global live currents | NOAA **RTOFS** global (HYCOM 1/12°, tripolar), latest complete 00z run on NOMADS, `https://nomads.ncep.noaa.gov/pub/data/nccf/com/rtofs/prod/rtofs.YYYYMMDD/rtofs_glo_2ds_fNNN_prog.nc` (surface `u_velocity`/`v_velocity`) | run day 00z + 0–72 h (we take every 3 h) | 148 MB per file, downloaded, binned, then deleted. NOMADS has no OPeNDAP for RTOFS anymore (SCN 25-81). |
| Global hindcast currents | HYCOM **GOFS 3.1** global 1/12° via NCSS, `water_u`/`water_v` at 0 m, `horizStride=3`: `GLBv0.08/expt_53.X/data/<year>` (1994–2015), `GLBv0.08/expt_92.9` (Oct 2017–Mar 2018), `GLBy0.08/expt_93.0` (Dec 2018–Sep 2024); `expt_92.8/57.7/93.0` and ESPC-D-V02 are wired in for other dates | 1994 – now | No HYCOM global before 1994, so Ixtoc I, Burmah Agate and Mega Borg get no global layer. OSCAR is not needed. |

Not used: ERA5/CMEMS/GLORYS (need accounts).

## File formats

### Forcing NetCDF (`cache/forcing/<window>/`)
- `currents.nc`: `x_sea_water_velocity`, `y_sea_water_velocity` (m s-1), dims `(time, lat, lon)`, surface only.
- `winds.nc`: `x_wind`, `y_wind` (m s-1, 10 m), dims `(time, lat, lon)`.
- Coordinates `time` (CF, seconds since 1970), `lat`, `lon` (−180..180, ascending), with `standard_name`s. Land = NaN.
- Opens directly with `opendrift.readers.reader_netCDF_CF_generic.Reader(path)` (checked with OpenDrift 1.14.11 in the `oilflood` env). The backend (`backend/app/forcing.py`) finds them by scanning `data/cache/**/*.nc`. Raw chunks are kept in `data/raw/` so they don't show up twice.

Notes on processing:
- Latitudes in HYCOM GOMb0.04 are Mercator-spaced. They're resampled onto a uniform axis because OpenDrift's generic reader needs constant spacing.
- HYCOM's 1.27e30 fill isn't always decoded, so anything over 100 m/s is masked to NaN.
- Timestamps are snapped to 15 min because NCSS output carries float jitter.
- Values are quantized to 3 decimals.
- **Galveston Bay is land in HYCOM.** The Texas City Y point (29.363, −94.798) is NaN. The nearest water is Bolivar Roads, about 29.33, −94.72. Seed hindcasts there, or let OpenDrift fall back for in-bay particles.

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

### Global fields (`cache/fields/global/<window>/`)
Written by `fetch_global.py`. Same PNG encoding as the Gulf fields but on a regular global **cell-centred** grid (`lon0 = -180 + d/2`, `lat0 = -90 + d/2`), `vmax = 2.5 m/s`:
- `index.json`: `{window, layer:"global", bbox:[-180,-90,180,90], lon0, lat0, dlon, dlat, nx, ny, grid:"cell-centre", times, vmax, units, png_encoding, source, every_h, [run]}`.
- `<t>.png`: RGBA, rows north→south. Live is 1440×720 (0.25°), hindcasts are 720×360 (0.5°). `--step` changes it.
- `<t>.json`: per-step meta only (`{t, lon0, lat0, dlon, dlat, nx, ny, vmax, png, source}`). A global u/v JSON would be about 2M numbers, so the backend decodes the PNG instead (`GET /api/currents/{t}?layer=global`).
- Values are bin means of all native 1/12° points that fall in each cell. RTOFS u/v are rotated from grid axes to east/north north of 47°N (tripolar Arctic).
- Hindcast spacing is 3 h for windows up to 10 days, 6 h up to 30 days, and 12 h beyond (DWH: 168 fields). The window is the spill's dates ±1 day, capped at 100 days.

Measured on the tower (2026-11-21):

| Window | Source | Fields | Grid | Size | Raw download |
|---|---|---|---|---|---|
| `live` | RTOFS run 20261121 00z, f000–f072 / 3 h | 25 | 1440×720 | 27 MB (1.1 MB/PNG) | 25 × 148 MB = 3.7 GB streamed at ~40 MB/s, deleted after binning |
| `dwh` | HYCOM expt_53.X 2010 | 168 (12 h) | 720×360 | 53 MB | 1.0 GB in `raw/global/dwh` |
| `texas_city_y` | expt_53.X 2014 | 40 | 720×360 | 13 MB | 94 MB |
| `mc209_2017` | expt_92.9 | 61 | 720×360 | 20 MB | 333 MB |
| `dbl_152` | expt_53.X 2005 | 66 | 720×360 | 21 MB | 154 MB |
| `eugene_island_2009` | expt_53.X 2009 | 51 | 720×360 | 17 MB | 121 MB |
| `main_pass_2023` | GLBy0.08 expt_93.0 | 61 | 720×360 | 20 MB | 228 MB |

`data/raw/global/` can be deleted after export. To refresh live on demo morning: `fetch_global.py live --force` (it skips the run if it was already exported). Use `--every-h 6` to halve the download, and `--keep-raw` to keep the RTOFS NetCDFs.

## Sizes (cached on the tower)
See the "Cached" table in SYNC.md for exact sizes. Typical sizes: hindcast window, hourly, 5 days ≈ 100 MB currents + 30 MB winds. Live ≈ 9 MB. Fields ≈ 0.3 MB per timestep.
