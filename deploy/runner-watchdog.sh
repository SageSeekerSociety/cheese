#!/usr/bin/env bash
# Restart a self-hosted GitHub Actions runner that is "active" to systemd but
# has stopped talking to GitHub.
#
# A runner can hang on an HTTPS call that never returns (seen on the dev box:
# it acknowledged a job, asked for a token, and logged nothing for 97 minutes
# while GitHub showed it offline and every deploy queued). systemd only restarts
# a process that exits, so a hung listener stays "active (running)" forever.
#
# Liveness: the listener writes its newest _diag/Runner_*.log at least once per
# credential cycle, about every 50 minutes even when idle (a month of logs on the
# dev box never went quieter than that except for the hang). Silence beyond
# RUNNER_WATCHDOG_STALE_SECONDS means it is stuck. A running Runner.Worker means
# a job is in flight, which is never interrupted; a unit that is not active was
# stopped on purpose and is left alone; restarts are rate-limited.
set -euo pipefail

root=${RUNNER_WATCHDOG_ROOT:?runner root directory}
unit=${RUNNER_WATCHDOG_UNIT:?runner systemd unit}
stale=${RUNNER_WATCHDOG_STALE_SECONDS:-4200}
min_gap=${RUNNER_WATCHDOG_MIN_RESTART_GAP_SECONDS:-1800}
state=${RUNNER_WATCHDOG_STATE_DIR:-/var/lib/cheese-runner-watchdog}

say() { logger -t cheese-runner-watchdog -- "$*"; printf "%s\n" "$*"; }

if ! systemctl is-active --quiet "$unit"; then
  say "skip: $unit is not active (stopped on purpose or restarting)"
  exit 0
fi

newest=$(ls -1t "$root"/_diag/Runner_*.log 2>/dev/null | head -n 1 || true)
if [ -z "$newest" ]; then
  say "skip: no Runner_*.log under $root/_diag"
  exit 0
fi

now=$(date +%s)
age=$(( now - $(date -r "$newest" +%s) ))
if [ "$age" -le "$stale" ]; then
  exit 0
fi

if pgrep -f "$root/.*Runner\.Worker" >/dev/null 2>&1; then
  say "skip: runner log silent for ${age}s but a job is running"
  exit 0
fi

mkdir -p "$state"
last=$(cat "$state/last-restart" 2>/dev/null || echo 0)
if [ $(( now - last )) -lt "$min_gap" ]; then
  say "skip: runner log silent for ${age}s but it was restarted $(( now - last ))s ago"
  exit 0
fi

say "restart: $unit has written nothing to $(basename "$newest") for ${age}s"
printf "%s\n" "$now" > "$state/last-restart"
systemctl restart "$unit"
