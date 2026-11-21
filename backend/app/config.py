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

# Forcing source:
#   auto     -> NetCDF readers from data/cache that cover the run window, constant fallback elsewhere
#   cache    -> NetCDF readers only (error if none cover the window)
#   constant -> constant currents/wind only (always works, offline)
FORCING_MODE = os.environ.get("OILFLOOD_FORCING", "auto")

# Constant fallback forcing (m/s). Defaults: weak down-coast (SW) Texas shelf
# current and a moderate SE onshore wind, a typical Galveston spring pattern.
CONST_CURRENT_U = float(os.environ.get("OILFLOOD_CONST_CURRENT_U", "-0.08"))
CONST_CURRENT_V = float(os.environ.get("OILFLOOD_CONST_CURRENT_V", "-0.05"))
CONST_WIND_U = float(os.environ.get("OILFLOOD_CONST_WIND_U", "-3.0"))
CONST_WIND_V = float(os.environ.get("OILFLOOD_CONST_WIND_V", "4.0"))

MAX_WORKERS = int(os.environ.get("OILFLOOD_WORKERS", "4"))
TIME_STEP_MIN = int(os.environ.get("OILFLOOD_TIME_STEP_MIN", "15"))
OUTPUT_STEP_MIN = int(os.environ.get("OILFLOOD_OUTPUT_STEP_MIN", "30"))

RUNS_DIR.mkdir(parents=True, exist_ok=True)
