#!/usr/bin/env bash
# The nightly cache reclaim, and the one filesystem it used to be blind to.
#
# On 2026-09-22 the dev box could not open a new topic: `/` was at 51% while
# `/tmp` — a 32G tmpfs of its own, not the disk — was at 100%, so every write
# under `/tmp` failed with ENOSPC and the environment-preparation step for each
# new room died at startup. The nightly unit never ran: its condition asked
# `df /`. These cases pin the two halves of that — the condition has to be able
# to say yes about a temp filesystem, and the reclaim has to actually take the
# trees that fill it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLEANUP="$ROOT/deploy/dev-box-disk-cleanup.sh"
SERVICE="$ROOT/deploy/systemd/cheese-disk-cleanup.service"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dev-box-cleanup-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
DOCKER_CALLS="$RUN_DIR/docker.calls"
SUDO_CALLS="$RUN_DIR/sudo.calls"
TMP_ROOT="$RUN_DIR/tmp"
mkdir -p "$FAKE_BIN" "$TMP_ROOT"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# `df` is the whole question here: the script asks it both what a path's mount
# point is and how full that mount is. The fake answers from two numbers so a
# case can put the root filesystem and the temp filesystem wherever it likes —
# which is the situation that broke, and the one a real box takes days to reach.
# Everything under $FAKE_TMP_ROOT is one mount, so a case can point both `TMPDIR`
# and `CHEESE_TEMP_ROOTS` at different directories inside it and still have the
# script see the single temp filesystem it would see on a real box.
cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
field="${1#--output=}"
path="$2"
case "$path" in
  "${FAKE_TMP_ROOT}"|"${FAKE_TMP_ROOT}"/*) mount="${FAKE_TMP_MOUNT}" ;;
  *) mount="/" ;;
esac
case "$field" in
  target) printf 'Mounted on\n%s\n' "$mount" ;;
  pcent)
    if [ "$mount" = "${FAKE_TMP_MOUNT}" ]; then
      printf 'Use%%\n%s%%\n' "${FAKE_TMP_PCT}"
    else
      printf 'Use%%\n%s%%\n' "${FAKE_ROOT_PCT}"
    fi
    ;;
  *) exit 1 ;;
esac
EOF

# The reclaim tiers other than the pytest one are not what these cases are
# about, and the CI runner has no docker; both fakes are recorded so a case can
# still assert nothing foreign was touched.
cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
printf 'Type\tReclaimable\n'
exit 0
EOF
cat > "$FAKE_BIN/sudo" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_SUDO_CALLS:?}"
exit 0
EOF

# The tiers that are not the subject of these cases, and the two prunes the
# script runs at the end of an `--apply`. Each one is a command that would do
# real work on the machine running the test — `uv cache prune` alone walks the
# whole shared uv store — so they are shadowed rather than allowed to run. A
# test that reaches outside its own directory is slow at best.
for name in go uv journalctl; do
  cat > "$FAKE_BIN/$name" <<'EOF'
#!/bin/sh
exit 0
EOF
done
chmod +x "$FAKE_BIN"/*

# `CHEESE_TEMP_ROOTS` is set for EVERY case, not just the ones about the
# reclaim. Without it the script sweeps the box's own temp roots — on a machine
# that has been running suites all day that is gigabytes of real trees, so the
# test would crawl, and a case that passed `--apply` would DELETE another user's
# temporary files. A test may not reach outside its own directory.
run() { # extra env assignments…; then the script's own arguments
  : > "$DOCKER_CALLS"
  : > "$SUDO_CALLS"
  env PATH="$FAKE_BIN:$PATH" \
    FAKE_DOCKER_CALLS="$DOCKER_CALLS" FAKE_SUDO_CALLS="$SUDO_CALLS" \
    FAKE_TMP_ROOT="$RUN_DIR" FAKE_TMP_MOUNT="$RUN_DIR" \
    FAKE_ROOT_PCT="${FAKE_ROOT_PCT:-50}" FAKE_TMP_PCT="${FAKE_TMP_PCT:-10}" \
    TMPDIR="$TMP_ROOT" CHEESE_TEMP_ROOTS="$TMP_ROOT" \
    "$@"
}

# 1. The whole bug, as a case: the root filesystem is healthy and the temp
#    filesystem is full. The nightly condition has to say yes — it is the temp
#    one that stops new topics from starting.
set +e
run FAKE_ROOT_PCT=50 FAKE_TMP_PCT=100 bash "$CLEANUP" --needed
rc=$?
set -e
[ "$rc" -eq 0 ] \
  || fail "--needed said no with /tmp at 100% and / at 50% (exit $rc) — the bug this test exists for"

# 2. And no when neither is at the mark, so the reclaim is not paid for on a
#    healthy box: every entry deleted is one the next build recreates.
set +e
run FAKE_ROOT_PCT=50 FAKE_TMP_PCT=10 bash "$CLEANUP" --needed
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "--needed said yes on a healthy box (exit $rc)"

# 3. The root filesystem alone still counts — the temp filesystem is an addition
#    to what this watches, not a replacement for it.
set +e
run FAKE_ROOT_PCT=90 FAKE_TMP_PCT=10 bash "$CLEANUP" --needed
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "--needed stopped watching / (exit $rc)"

# 4. The report names every filesystem it watched. The original failure was a
#    run that only ever printed one number, so a log could not say which one had
#    filled up.
out="$(run FAKE_ROOT_PCT=50 FAKE_TMP_PCT=100 bash "$CLEANUP")"
grep -q "^  /  *50% used" <<<"$out" || fail "the report does not name / : $out"
grep -qE "^  $RUN_DIR +100% used" <<<"$out" || fail "the report does not name the temp filesystem: $out"
grep -q "stale pytest temp trees" <<<"$out" || fail "the pytest row is not in the report: $out"

# 5. The reclaim takes the stale trees and leaves the newest ones. pytest keeps
#    its own newest few for the same reason: one of them is the run that is
#    happening right now.
seed_trees() {
  rm -rf "$RUN_DIR/sweep"
  mkdir -p "$RUN_DIR/sweep/pytest-of-someone/pytest-1" \
           "$RUN_DIR/sweep/pytest-of-someone/pytest-2" \
           "$RUN_DIR/sweep/pytest-of-someone/pytest-3"
  # Enough bytes that the row is not 0B, so the tier proves it ran at all.
  head -c 1000000 /dev/zero > "$RUN_DIR/sweep/pytest-of-someone/pytest-1/blob"
  # `pytest-current` is a symlink to the newest run; `-type d` must leave it be,
  # because a dangling link is worse than a stale directory.
  ln -s "$RUN_DIR/sweep/pytest-of-someone/pytest-3" "$RUN_DIR/sweep/pytest-current"
  touch -d '1 day ago' "$RUN_DIR/sweep/pytest-of-someone/pytest-1" \
                       "$RUN_DIR/sweep/pytest-of-someone/pytest-2" \
                       "$RUN_DIR/sweep/pytest-of-someone/pytest-3"
}
seed_trees
out="$(run CHEESE_TEMP_ROOTS="$RUN_DIR/sweep" bash "$CLEANUP")"
grep -q "stale pytest temp trees  *0B" <<<"$out" \
  && fail "the dry run sized the stale tree at zero, so the tier would not have run: $out"
[ -d "$RUN_DIR/sweep/pytest-of-someone/pytest-1" ] || fail "the dry run deleted something"

run CHEESE_TEMP_ROOTS="$RUN_DIR/sweep" bash "$CLEANUP" --apply >/dev/null
[ ! -d "$RUN_DIR/sweep/pytest-of-someone/pytest-1" ] \
  || fail "the oldest tree survived --apply"
[ -d "$RUN_DIR/sweep/pytest-of-someone/pytest-2" ] \
  || fail "--apply took a tree inside the keep-count"
[ -d "$RUN_DIR/sweep/pytest-of-someone/pytest-3" ] \
  || fail "--apply took the newest tree"
[ -L "$RUN_DIR/sweep/pytest-current" ] \
  || fail "--apply removed pytest-current, the symlink to the newest run"

# 6. A tree inside the age window is not a candidate even when it has fallen
#    outside the keep-count — the second half of the selector, on its own. With
#    the keep-count at zero it protects nothing, so only age can be what saves
#    the fresh tree.
seed_trees
mkdir -p "$RUN_DIR/sweep/pytest-of-someone/pytest-4"
out="$(run CHEESE_TEMP_ROOTS="$RUN_DIR/sweep" CHEESE_PYTEST_TEMP_KEEP=0 bash "$CLEANUP" --apply)"
[ -d "$RUN_DIR/sweep/pytest-of-someone/pytest-4" ] \
  || fail "a tree pytest is writing right now was taken"
[ ! -d "$RUN_DIR/sweep/pytest-of-someone/pytest-1" ] \
  || fail "with the keep-count at zero the stale trees should all have gone"

# 7. Nothing else was touched: the pointer to `/var/tmp` is not under the temp
#    root this run was given, and the reclaim may not wander out of it.
grep -q "apt-get clean" "$SUDO_CALLS" \
  && fail "the run reached outside the temp root it was given"

# 8. The unit asks the script rather than spelling out a `df` of its own. This
#    is the regression guard: putting `df /` back into the unit file restores
#    the blind spot exactly, and nothing else in this file would notice.
grep -q '^ExecCondition=.*--needed' "$SERVICE" \
  || fail "the unit does not ask the script whether it is needed"
grep -q '^ExecCondition=.*df ' "$SERVICE" \
  && fail "the unit asks df about one filesystem again"

echo "dev-box disk cleanup: all cases passed"
