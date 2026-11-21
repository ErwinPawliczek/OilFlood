#!/usr/bin/env bash
# Install/refresh the systemd *user* timer that runs refresh_live.sh 4x a day (02:30, 08:30, 14:30, 20:30 UTC:
# ~2.5 h after each GFS cycle; RTOFS's daily 00z run is complete on NOMADS by mid-morning UTC).
# Re-run from the checkout you want the timer to use (e.g. ~/OilFlood after merging).
set -euo pipefail
HERE="$(dirname "$(readlink -f "$0")")"
U="$HOME/.config/systemd/user"
mkdir -p "$U"
cat > "$U/oilflood-live.service" <<UNIT
[Unit]
Description=OilFlood: refresh live forecast forcing (RTOFS/ESPC currents + GFS winds)
After=network-online.target

[Service]
Type=oneshot
ExecStart=$HERE/refresh_live.sh
TimeoutStartSec=45min
Nice=10
UNIT
cat > "$U/oilflood-live.timer" <<UNIT
[Unit]
Description=OilFlood: refresh live forecast 4x a day

[Timer]
OnCalendar=*-*-* 02,08,14,20:30:00 UTC
Persistent=true
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now oilflood-live.timer
systemctl --user list-timers oilflood-live.timer --no-pager
