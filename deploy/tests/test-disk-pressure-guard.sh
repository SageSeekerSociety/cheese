#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$ROOT/deploy/cheesex-disk-pressure-guard.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheesex-disk-guard-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
CALLS="$RUN_DIR/docker.calls"
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
  env \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    FAKE_DOCKER_CALLS="$CALLS" \
    CHEESEX_DISK_GUARD_SETTLE_SECONDS=0 \
    "$GUARD"
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
