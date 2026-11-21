# Agent A1: spill model backend
Repo: OilFlood. Read SYNC.md and docs/ARCHITECTURE.md and docs/RESEARCH.md first. Follow SYNC.md rules (small commits, push often to YOUR branch `a1-model`, never force-push, never commit secrets, update SYNC.md Done log when finished). Hackathon deadline Sun Nov 22 12:00 PM PST. All code must be fresh.

Goal: FastAPI backend in `backend/` that implements the Step A API contract in docs/ARCHITECTURE.md on port 5200.
- `POST /api/spill` runs OpenDrift **OpenOil** in a background worker (thread/process), seeding at lat/lon with oil type (default IFO 380 from ADIOS db), ~2000 elements, 72 h, output every 30 min. Stokes on, wind_drift_factor ~0.02, horizontal diffusivity on, weathering on, coastline stranding on.
- Forcing: use readers from `data/cache/` produced by agent A2 (HYCOM currents, GFS/ERA winds, NetCDF). Until those exist, fall back to OpenDrift's reader_constant / generic readers or the `basemap`/landmask reader so the API works end to end today. Make the forcing source swappable via config.
- `GET /api/spill/{id}` status/progress, `GET /api/spill/{id}/tracks` JSON exactly per contract (paths as [lon,lat], unix timestamps, end status, oil budget series). Cache by request hash under `backend/runs/`.
- Serve `web/dist` statically if it exists.
- Provide `backend/requirements.txt` (or environment.yml; OpenDrift installs best via conda-forge on this machine: miniforge3 is installed) and a README section with exact setup + run commands.
- Test: a script `backend/smoke_test.py` that posts a spill at 29.36N, 94.80W (Texas City Y, 2014-03-22) and asserts tracks come back.
Report in SYNC.md what works and any blockers.
