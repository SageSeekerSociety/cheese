#!/usr/bin/env bash
# The disk guard: reclaim only when the machine is actually running out, and say
# so when it does. Twice on 2026-09-15 a pool machine reached 100% and dropped out
# of the pool — the runner listener cannot write its own log file, so it
# crash-loops while its service still reports active.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$ROOT/deploy/ci-runner/disk-guard.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-ci-disk-guard-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
CALLS="$RUN_DIR/docker.calls"
mkdir -p "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# `df -BG --output=avail /` prints a header and one figure; the guard reads the
# digits out of the second line.
cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
printf 'Avail\n'
if test -f "${FAKE_DF_CALLED:?}"; then
  printf '%sG\n' "${FAKE_FREE_GB_AFTER:-$FAKE_FREE_GB}"
else
  : > "$FAKE_DF_CALLED"
  printf '%sG\n' "${FAKE_FREE_GB:?}"
fi
EOF

cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
exit 0
EOF

chmod +x "$FAKE_BIN"/*

run_guard() {
  : > "$CALLS"
  rm -f "$RUN_DIR/df.called"
  PATH="$FAKE_BIN:$PATH" FAKE_DOCKER_CALLS="$CALLS" \
    FAKE_DF_CALLED="$RUN_DIR/df.called" \
    FAKE_FREE_GB="$1" FAKE_FREE_GB_AFTER="${2:-$1}" \
    CHEESE_CI_FREE_FLOOR_GB="${3:-10}" \
    bash "$GUARD"
}

# 1. Plenty of room: one df, nothing else, and nothing said.
out="$(run_guard 30)"
[ -z "$out" ] || fail "the guard spoke on a machine with room: $out"
[ ! -s "$CALLS" ] || fail "the guard pruned a machine with room: $(cat "$CALLS")"

# 2. Below the floor: reclaim, and report both readings so a log says what it won.
out="$(run_guard 4 22)"
grep -q "4G free is below 10G" <<<"$out" || fail "no reason given: $out"
grep -q "4G -> 22G free" <<<"$out" || fail "no before/after given: $out"
# With a grace period, not without one: a job BUILDS its own images and runs them
# a few steps later, and a bare `prune -af` deleted one in between — `docker run`
# then fails with exit 125 and there is nothing to re-pull, because the image was
# never fetched from anywhere.
grep -q -- "system prune -af --filter until=" "$CALLS" \
  || fail "expected an image prune with a grace period: $(cat "$CALLS")"
grep -qx -- "volume prune -f" "$CALLS" || fail "expected a volume prune: $(cat "$CALLS")"

# 3. The grace period is long enough to outlive a job on this pool: the longest
#    job timeout is 20 minutes, and an image a job built has to survive its own run.
period="$(grep -oE 'CHEESE_CI_KEEP_NEWER_THAN:-[0-9]+h' "$GUARD" | grep -oE '[0-9]+')"
[ -n "$period" ] || fail "the grace period is not stated in hours"
[ "$period" -ge 1 ] || fail "a grace period of ${period}h cannot outlive a job"

# 3. Exactly at the floor is not below it.
out="$(run_guard 10)"
[ ! -s "$CALLS" ] || fail "the guard pruned at exactly the floor: $(cat "$CALLS")"

# 4. The floor is settable, so a bigger machine can hold a different line.
out="$(run_guard 15 25 20)"
grep -q "15G free is below 20G" <<<"$out" || fail "the floor was not honoured: $out"

# 5. A df that says nothing is not a reason to prune.
: > "$CALLS"
rm -f "$RUN_DIR/df.called"
cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$FAKE_BIN/df"
PATH="$FAKE_BIN:$PATH" FAKE_DOCKER_CALLS="$CALLS" bash "$GUARD" >/dev/null
[ ! -s "$CALLS" ] || fail "pruned without a reading: $(cat "$CALLS")"

echo "ci-runner disk guard: all cases passed"
