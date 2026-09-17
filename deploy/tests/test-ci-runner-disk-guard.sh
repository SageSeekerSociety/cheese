#!/usr/bin/env bash
# The disk guard: reclaim only when the machine is actually running out, reclaim
# in the order that costs the next job least, and say so when it does. Twice on
# 2026-09-15 a pool machine reached 100% and dropped out of the pool — the runner
# listener cannot write its own log file, so it crash-loops while its service
# still reports active.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GUARD="$ROOT/deploy/ci-runner/disk-guard.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-ci-disk-guard-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
CALLS="$RUN_DIR/docker.calls"
SUDO_CALLS="$RUN_DIR/sudo.calls"
mkdir -p "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# `df -BG --output=avail /` prints a header and one figure; the guard reads the
# digits out of the second line. The reading changes once something has actually
# been reclaimed, not on a call count: the guard checks free space between tiers
# to decide whether to run the next one, so a df that changed merely because it
# was asked twice would let a tier pass its own precondition.
cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
printf 'Avail\n'
if test -f "${FAKE_RECLAIMED:?}"; then
  printf '%sG\n' "${FAKE_FREE_GB_AFTER:-$FAKE_FREE_GB}"
else
  printf '%sG\n' "${FAKE_FREE_GB:?}"
fi
EOF

cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
: > "${FAKE_RECLAIMED:?}"
exit 0
EOF

cat > "$FAKE_BIN/sudo" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_SUDO_CALLS:?}"
exit 0
EOF

chmod +x "$FAKE_BIN"/*

# A home directory holding one of everything the guard has an opinion about.
# `old` is past every retention window here, `new` is inside all of them.
seed_home() {
  HOME_DIR="$RUN_DIR/home"
  rm -rf "$HOME_DIR"
  mkdir -p "$HOME_DIR"/actions-runner/_diag \
           "$HOME_DIR"/actions-runner-1/_diag \
           "$HOME_DIR"/actions-runner/_work/uv-venv/test \
           "$HOME_DIR"/.npm/_cacache \
           "$HOME_DIR"/.cache/uv/wheels \
           "$HOME_DIR"/.cache/ms-playwright/chromium \
           "$HOME_DIR"/setup-pnpm/node_modules \
           "$HOME_DIR"/.rustup/toolchains
  : > "$HOME_DIR/actions-runner/_diag/Worker_old.log"
  : > "$HOME_DIR/actions-runner/_diag/Worker_new.log"
  : > "$HOME_DIR/actions-runner-1/_diag/Runner_old.log"
  : > "$HOME_DIR/actions-runner/_work/uv-venv/test/pyvenv.cfg"
  : > "$HOME_DIR/.npm/_cacache/index"
  : > "$HOME_DIR/.cache/uv/wheels/numpy.whl"
  : > "$HOME_DIR/.cache/ms-playwright/chromium/headless"
  : > "$HOME_DIR/setup-pnpm/node_modules/.bin"
  : > "$HOME_DIR/.rustup/toolchains/stable"
  touch -t 202601010000 "$HOME_DIR/actions-runner/_diag/Worker_old.log" \
                        "$HOME_DIR/actions-runner-1/_diag/Runner_old.log"
}

run_guard() {
  : > "$CALLS"
  : > "$SUDO_CALLS"
  rm -f "$RUN_DIR/reclaimed"
  PATH="$FAKE_BIN:$PATH" FAKE_DOCKER_CALLS="$CALLS" FAKE_SUDO_CALLS="$SUDO_CALLS" \
    FAKE_RECLAIMED="$RUN_DIR/reclaimed" \
    FAKE_FREE_GB="$1" FAKE_FREE_GB_AFTER="${2:-$1}" \
    CHEESE_CI_FREE_FLOOR_GB="${3:-10}" \
    HOME="${HOME_DIR:?seed_home must run before any guard run}" \
    bash "$GUARD"
}

# 1. Plenty of room: one df, nothing else, and nothing said.
seed_home
out="$(run_guard 30)"
[ -z "$out" ] || fail "the guard spoke on a machine with room: $out"
[ ! -s "$CALLS" ] || fail "the guard pruned a machine with room: $(cat "$CALLS")"

# 2. Below the floor: reclaim, and report both readings so a log says what it won.
seed_home
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

# 3. Docker was enough, so nothing under HOME was touched. Every tier deleted is
#    one the next job pays to rebuild.
[ -f "$HOME_DIR/actions-runner/_diag/Worker_old.log" ] \
  || fail "a month-old runner log went while docker alone had already cleared the floor"
[ -f "$HOME_DIR/.npm/_cacache/index" ] \
  || fail "the npm cache went while docker alone had already cleared the floor"

# 4. Docker did not clear it: on runner-2 at 9G free the guard fired before every
#    job and reported `9G -> 9G`, because /var/lib/docker held 6G of a 29G-used
#    disk while /home held 18G. The later tiers are what that machine needed.
seed_home
out="$(run_guard 4 4)"
grep -q "runner logs" <<<"$out" || fail "the guard stopped at docker: $out"
[ ! -f "$HOME_DIR/actions-runner/_diag/Worker_old.log" ] || fail "a month-old runner log survived"
[ ! -f "$HOME_DIR/actions-runner-1/_diag/Runner_old.log" ] \
  || fail "the second runner instance's logs were not reclaimed"
# Today's log is how a machine that drops out of the pool gets diagnosed.
[ -f "$HOME_DIR/actions-runner/_diag/Worker_new.log" ] || fail "today's runner log went"
grep -q "apt-get clean" "$SUDO_CALLS" || fail "apt archives were not reclaimed: $(cat "$SUDO_CALLS")"
[ ! -d "$HOME_DIR/.npm/_cacache" ] || fail "the npm cache survived"
[ ! -d "$HOME_DIR/actions-runner/_work/uv-venv" ] \
  || fail "uv-venv, the path #1104 stopped writing to, survived"
grep -q "needs a bigger disk" <<<"$out" \
  || fail "a machine still under the floor after every tier said nothing about it: $out"

# 5. What no tier may take. uv's wheel cache is the single most expensive thing
#    on the box to rebuild — deleting it costs the NEXT job 356 MiB off the
#    network and two minutes, which is exactly what #1134 stopped paying.
[ -f "$HOME_DIR/.cache/uv/wheels/numpy.whl" ] || fail "the uv wheel cache was reclaimed"
[ -f "$HOME_DIR/.cache/ms-playwright/chromium/headless" ] \
  || fail "playwright's browsers were reclaimed; nothing in e2e refetches them"
[ -f "$HOME_DIR/setup-pnpm/node_modules/.bin" ] || fail "pnpm itself was reclaimed"
[ -f "$HOME_DIR/.rustup/toolchains/stable" ] || fail "the rust toolchain was reclaimed"

# 6. The grace period is long enough to outlive a job on this pool: the longest
#    job timeout is 20 minutes, and an image a job built has to survive its own run.
period="$(grep -oE 'CHEESE_CI_KEEP_NEWER_THAN:-[0-9]+h' "$GUARD" | grep -oE '[0-9]+')"
[ -n "$period" ] || fail "the grace period is not stated in hours"
[ "$period" -ge 1 ] || fail "a grace period of ${period}h cannot outlive a job"

# 7. Exactly at the floor is not below it.
seed_home
out="$(run_guard 10)"
[ ! -s "$CALLS" ] || fail "the guard pruned at exactly the floor: $(cat "$CALLS")"

# 8. The floor is settable, so a bigger machine can hold a different line.
seed_home
out="$(run_guard 15 25 20)"
grep -q "15G free is below 20G" <<<"$out" || fail "the floor was not honoured: $out"

# 9. A df that says nothing is not a reason to prune.
: > "$CALLS"
cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$FAKE_BIN/df"
PATH="$FAKE_BIN:$PATH" FAKE_DOCKER_CALLS="$CALLS" FAKE_SUDO_CALLS="$SUDO_CALLS" \
  FAKE_RECLAIMED="$RUN_DIR/reclaimed" HOME="$HOME_DIR" bash "$GUARD" >/dev/null
[ ! -s "$CALLS" ] || fail "pruned without a reading: $(cat "$CALLS")"

echo "ci-runner disk guard: all cases passed"
