#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WATCHDOG="$ROOT/deploy/runner-watchdog.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/runner-watchdog-test.XXXXXX")"
trap "rm -rf \"$RUN_DIR\"" EXIT

FAKE_BIN="$RUN_DIR/bin"
RUNNER="$RUN_DIR/runner"
STATE="$RUN_DIR/state"
CALLS="$RUN_DIR/systemctl.calls"
mkdir -p "$FAKE_BIN" "$RUNNER/_diag"

fail() { echo "FAIL: $*" >&2; exit 1; }

cat > "$FAKE_BIN/systemctl" <<"FAKE"
#!/bin/sh
printf "%s\n" "$*" >> "${FAKE_SYSTEMCTL_CALLS:?}"
case "$1" in
  is-active) test "${FAKE_UNIT_ACTIVE:-1}" = 1 ;;
  restart) exit 0 ;;
  *) exit 1 ;;
esac
FAKE
cat > "$FAKE_BIN/pgrep" <<"FAKE"
#!/bin/sh
test "${FAKE_WORKER_RUNNING:-0}" = 1
FAKE
cat > "$FAKE_BIN/logger" <<"FAKE"
#!/bin/sh
exit 0
FAKE
chmod +x "$FAKE_BIN"/*

# Age the runner log by N seconds.
log_age() {
  touch "$RUNNER/_diag/Runner_20261004-000000-utc.log"
  python3 -c "import os,sys,time; t=time.time()-int(sys.argv[2]); os.utime(sys.argv[1],(t,t))" \
    "$RUNNER/_diag/Runner_20261004-000000-utc.log" "$1"
}

run() {
  : > "$CALLS"
  env PATH="$FAKE_BIN:/usr/bin:/bin" \
    FAKE_SYSTEMCTL_CALLS="$CALLS" \
    RUNNER_WATCHDOG_ROOT="$RUNNER" \
    RUNNER_WATCHDOG_UNIT=actions.runner.example.service \
    RUNNER_WATCHDOG_STATE_DIR="$STATE" \
    "$@" "$WATCHDOG" >/dev/null 2>&1
}
restarted() { grep -q "^restart actions.runner.example.service$" "$CALLS"; }

log_age 600
run env
restarted && fail "restarted a runner that logged 10 minutes ago"
echo "PASS: a runner inside its quiet cycle is left alone"

log_age 3000
run env
restarted && fail "restarted a runner after 50 minutes of idle quiet"
echo "PASS: the normal 50-minute idle gap is not a hang"

log_age 6000
run env FAKE_WORKER_RUNNING=1
restarted && fail "restarted while a job was running"
echo "PASS: a running job is never interrupted"

run env FAKE_UNIT_ACTIVE=0
restarted && fail "restarted a unit that was stopped"
echo "PASS: a stopped unit stays stopped"

rm -rf "$STATE"
run env
restarted || fail "a runner silent for 100 minutes was not restarted"
test -s "$STATE/last-restart" || fail "the restart was not recorded"
echo "PASS: a runner silent past the threshold is restarted"

run env
restarted && fail "restarted again inside the rate limit"
echo "PASS: restarts are rate-limited"

rm -f "$RUNNER"/_diag/Runner_*.log
rm -rf "$STATE"
run env
restarted && fail "restarted with no runner log to judge by"
echo "PASS: no log means no judgement"
