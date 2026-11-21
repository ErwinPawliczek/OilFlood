"""Runtime configuration (env-var driven so nothing secret lives in git)."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"

# Where forcing NetCDF + spill catalog live. Colon-separated list allowed so the
# backend can read another worktree's cache during development.
DATA_DIRS = [Path(p) for p in os.environ.get("OILFLOOD_DATA_DIR", str(REPO_ROOT / "data")).split(":") if p]

RUNS_DIR = Path(os.environ.get("OILFLOOD_RUNS_DIR", str(BACKEND_DIR / "runs")))
WEB_DIST = Path(os.environ.get("OILFLOOD_WEB_DIST", str(REPO_ROOT / "web" / "dist")))

# Forcing: only real cached NetCDF (data/cache/**/*.nc). There is no synthetic
# fallback: a run whose place/time has no cached currents + winds is rejected,
# and particles that drift out of the forcing domain are deactivated ("no_data").

# Seed snapping: if the requested point is a land cell in the forcing grid (e.g.
# Galveston Bay is land in HYCOM), move it to the nearest model water cell
# within this distance, else reject the run as "on land".
SNAP_KM_PREDICTION = float(os.environ.get("OILFLOOD_SNAP_KM", "8"))
SNAP_KM_HINDCAST = float(os.environ.get("OILFLOOD_SNAP_KM_HINDCAST", "30"))
MIN_RUN_H = 3.0  # shortest run worth doing when forcing ends soon after the start

MAX_WORKERS = int(os.environ.get("OILFLOOD_WORKERS", "4"))
TIME_STEP_MIN = int(os.environ.get("OILFLOOD_TIME_STEP_MIN", "15"))
OUTPUT_STEP_MIN = int(os.environ.get("OILFLOOD_OUTPUT_STEP_MIN", "30"))

RUNS_DIR.mkdir(parents=True, exist_ok=True)
