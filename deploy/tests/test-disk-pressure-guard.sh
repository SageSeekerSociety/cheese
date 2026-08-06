#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$ROOT/deploy/cheesex-disk-pressure-guard.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheesex-disk-guard-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
CALLS="$RUN_DIR/docker.calls"
OUT="$RUN_DIR/guard.out"
TIER_STATE="$RUN_DIR/tier.state"
mkdir -p "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/fake 100 90 10 %s%% /\n' "${FAKE_USAGE_PERCENT:?}"
EOF

cat > "$FAKE_BIN/curl" <<'EOF'
#!/bin/sh
if test "${FAKE_HEALTH_INVALID:-0}" = 1; then
  printf 'not-json\n'
else
  printf '{"data":{"active_turns":%s}}\n' "${FAKE_ACTIVE_TURNS:?}"
fi
EOF

cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
case "$1" in
  ps) printf 'abc123\ndef456\n' ;;
  inspect) exit 0 ;;
  rm|exec) exit 0 ;;
  *) exit 1 ;;
esac
EOF

cat > "$FAKE_BIN/logger" <<'EOF'
#!/bin/sh
exit 0
EOF

chmod +x "$FAKE_BIN"/*

run_guard() {
  : > "$CALLS"
  : > "$OUT"
  env \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    FAKE_DOCKER_CALLS="$CALLS" \
    CHEESEX_DISK_GUARD_SETTLE_SECONDS=0 \
    CHEESEX_DISK_GUARD_TIER_STATE_FILE="$TIER_STATE" \
    "$GUARD" > "$OUT" 2>&1
}

FAKE_USAGE_PERCENT=84 FAKE_ACTIVE_TURNS=0 run_guard
test ! -s "$CALLS" || fail "Docker was touched below the pressure threshold"
echo "PASS: below-threshold run is a no-op"

FAKE_USAGE_PERCENT=90 FAKE_ACTIVE_TURNS=1 run_guard
test ! -s "$CALLS" || fail "cleanup ran while a turn was active"
echo "PASS: active turns defer cleanup"

FAKE_USAGE_PERCENT=90 FAKE_ACTIVE_TURNS=0 run_guard
grep -Fxq 'ps -aq --filter label=cheesex-tmux=1' "$CALLS" \
  || fail "label-scoped sandbox query was not issued"
grep -Fxq 'rm -f abc123 def456' "$CALLS" \
  || fail "only the selected sandbox IDs were not removed"
grep -Fxq 'inspect cheese-backend-1' "$CALLS" \
  || fail "backend temp cleanup did not verify the container"
grep -Fq 'exec cheese-backend-1 sh -c find /tmp' "$CALLS" \
  || fail "old backend temp directories were not targeted"
echo "PASS: pressure cleanup is label- and path-scoped"

FAKE_USAGE_PERCENT=90 FAKE_ACTIVE_TURNS=0 FAKE_HEALTH_INVALID=1 run_guard
test ! -s "$CALLS" || fail "cleanup ran after an invalid health response"
echo "PASS: invalid health fails closed"

# --- Early-warning tiers (WARN/HIGH), strictly below the 85% cleanup gate ---
rm -f "$TIER_STATE"

FAKE_USAGE_PERCENT=65 FAKE_ACTIVE_TURNS=0 run_guard
grep -qi 'tier' "$OUT" && fail "usage below the warn tier should stay quiet"
echo "PASS: usage below warn tier is silent"

FAKE_USAGE_PERCENT=72 FAKE_ACTIVE_TURNS=0 run_guard
grep -qi 'WARN tier' "$OUT" || fail "entering warn tier was not logged"
test ! -s "$CALLS" || fail "warn tier must not touch docker"
test "$(cat "$TIER_STATE")" = "warn" || fail "tier state file was not updated to warn"
echo "PASS: entering warn tier is logged and docker-free"

FAKE_USAGE_PERCENT=73 FAKE_ACTIVE_TURNS=0 run_guard
grep -qi 'tier' "$OUT" && fail "repeated same-tier runs should not re-log"
echo "PASS: repeated warn-tier runs do not spam the log"

FAKE_USAGE_PERCENT=82 FAKE_ACTIVE_TURNS=0 run_guard
grep -qi 'HIGH tier' "$OUT" || fail "escalating to high tier was not logged"
test ! -s "$CALLS" || fail "high tier must not touch docker"
test "$(cat "$TIER_STATE")" = "high" || fail "tier state file was not updated to high"
echo "PASS: escalating to high tier is logged and docker-free"

FAKE_USAGE_PERCENT=60 FAKE_ACTIVE_TURNS=0 run_guard
grep -qi 'OK tier' "$OUT" || fail "recovery to ok tier was not logged"
test "$(cat "$TIER_STATE")" = "ok" || fail "tier state file was not updated to ok"
echo "PASS: recovery to ok tier is logged"

rm -f "$TIER_STATE"
FAKE_USAGE_PERCENT=90 FAKE_ACTIVE_TURNS=0 run_guard
grep -qi 'WARN tier\|HIGH tier' "$OUT" && fail "jumping straight to critical should not also log warn/high"
test "$(cat "$TIER_STATE")" = "critical" || fail "tier state file was not updated to critical"
echo "PASS: jumping straight to critical skips the warn/high tier logs"

FAKE_USAGE_PERCENT=90 CHEESEX_DISK_GUARD_WARN_PERCENT=bogus FAKE_ACTIVE_TURNS=1 run_guard
grep -qi 'invalid warn tier threshold' "$OUT" || fail "invalid warn threshold was not reported"
test ! -s "$CALLS" || fail "invalid tier threshold must not touch docker"
echo "PASS: invalid tier threshold is reported without touching cleanup"
