#!/usr/bin/env bash
# Refresh the live forecast window: Gulf currents + GFS winds for prediction mode, the Gulf flow-layer
# fields, and the global RTOFS flow layer. Safe to run any time (every step skips if nothing is newer).
# Run by the systemd user timer oilflood-live.timer (see data/README.md, install_live_timer.sh).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"
PY="${OILFLOOD_PY:-$HOME/miniforge3/envs/oilflood/bin/python}"
exec 9>"${TMPDIR:-/tmp}/oilflood-refresh-live.lock"
flock -n 9 || { echo "another refresh is running"; exit 0; }
rc=0
# --keep-raw: fetch_global.py reuses the RTOFS files instead of downloading them again
"$PY" fetch_live.py --keep-raw || rc=$?
"$PY" export_fields.py live --step 0.1 --every-h 3 || rc=$?
# Globe layer: skips if this RTOFS run is already exported. --max-h 96 so it covers now+72 h all day.
"$PY" fetch_global.py live --max-h 96 || rc=$?
# leftover RTOFS raw files (fetch_global deletes the ones it used)
rm -rf raw/global_live/*/ 2>/dev/null
echo "refresh_live done rc=$rc"
exit $rc
