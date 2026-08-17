#!/usr/bin/env bash
# Tests for deploy/fix-workspace-ownership.sh.
#
# The 2026-08-11 dev outage came out of this script: it handed 2.2M files to a
# new uid and then aborted the deploy on a credential file it refused to move,
# leaving a 1001 backend on a 1000 tree — a project-wide 422. What is asserted
# here is the shape that prevents a repeat: secrets move too, exactly one uid
# marker survives a handover, and nothing takes ownership of a device node.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FAKE_BIN="$ROOT/deploy/tests/fakes/ownership"
SCRIPT="$ROOT/deploy/fix-workspace-ownership.sh"
CASE="${1:-all}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# The suite runs unprivileged, so "a file somebody else owns" has to be
# expressed as a target uid this process is not. Derived, never hardcoded: a
# literal 1001 is the runner's own uid on GitHub's ubuntu images, which turns
# every handover assertion into a silent "already owned, nothing to do".
OTHER_UID="$(( $(id -u) + 1 ))"
OTHER_GID="$(( $(id -g) + 1 ))"

new_run_dir() {
  mkdir -p "$ROOT/tmp"
  local dir
  dir="$(mktemp -d "$ROOT/tmp/ownership.XXXXXX")"
  # Pre-create it so a `! grep` assertion proves the call was absent rather than
  # the log file was.
  : > "$dir/docker.log"
  printf '%s' "$dir"
}

# Runs the script with the fake docker on PATH. Echoes nothing; the caller reads
# "$run_dir/docker.log" and the tree the script touched.
run_ownership() {
  local run_dir="$1"
  shift
  PATH="$FAKE_BIN:$PATH" \
    OWNERSHIP_DOCKER_LOG="$run_dir/docker.log" \
    "$@"
}

test_secret_file_is_handed_over() {
  run_dir="$(new_run_dir)"
  secret="$run_dir/git-credentials"
  printf 'https://x:y@github.com\n' > "$secret"
  chmod 600 "$secret"

  run_ownership "$run_dir" \
    env AGENT_UID="$OTHER_UID" AGENT_GID="$OTHER_GID" SECRET_FILE_PATHS="$secret" \
    "$SCRIPT" fake/image "$run_dir/absent-tree" >/dev/null \
    || fail "handover of a readable secret should succeed"

  grep -q "SECRET-CHOWN user=0 path=$secret" "$run_dir/docker.log" \
    || fail "the secret file was not handed over (this is the 2026-08-11 abort)"
  [ "$(stat -c '%a' "$secret")" = 600 ] \
    || fail "the secret's mode changed; only the owner may move"
  rm -rf "$run_dir"
  echo "PASS: a secret file is handed over with its mode untouched"
}

test_dev_null_is_never_chowned() {
  run_dir="$(new_run_dir)"
  # AGENT_UID is deliberately not root's: /dev/null is 0:0, so the only thing
  # standing between it and a chown is the regular-file guard.
  run_ownership "$run_dir" \
    env AGENT_UID="$OTHER_UID" AGENT_GID="$OTHER_GID" SECRET_FILE_PATHS=/dev/null \
    "$SCRIPT" fake/image "$run_dir/absent-tree" >/dev/null \
    || fail "the feature-off default (/dev/null) must pass"

  ! grep -q 'SECRET-CHOWN' "$run_dir/docker.log" \
    || fail "took ownership of /dev/null — a device node is not ours to move"
  rm -rf "$run_dir"
  echo "PASS: /dev/null (feature off) is skipped, never chowned"
}

test_unreadable_secret_still_fails_loudly() {
  run_dir="$(new_run_dir)"
  secret="$run_dir/git-credentials"
  printf 'https://x:y@github.com\n' > "$secret"

  # A chown that cannot fix it — a mount option, an unreadable parent — must
  # still stop the deploy. The check did not go away, it moved after the move.
  if out="$(run_ownership "$run_dir" \
    env SECRET_FILE_PATHS="$secret" OWNERSHIP_PROBE_FAILS=1 \
    "$SCRIPT" fake/image "$run_dir/absent-tree" 2>&1)"; then
    fail "an unreadable secret must fail the deploy"
  fi
  printf '%s' "$out" | grep -q 'still not readable' \
    || fail "the failure must say the handover already ran; got: $out"
  rm -rf "$run_dir"
  echo "PASS: a secret unreadable after the handover still fails loudly"
}

test_handover_drops_other_uid_markers() {
  run_dir="$(new_run_dir)"
  tree="$run_dir/workspaces"
  mkdir -p "$tree"
  : > "$tree/.cheese-uid-1001"
  : > "$tree/some-file"

  run_ownership "$run_dir" \
    env AGENT_UID=1000 AGENT_GID=1000 \
    "$SCRIPT" fake/image "$tree" >/dev/null \
    || fail "handover of a real tree should succeed"

  [ -e "$tree/.cheese-uid-1000" ] \
    || fail "the new uid's marker was not written"
  [ ! -e "$tree/.cheese-uid-1001" ] \
    || fail "a stale 1001 marker survived; the next deploy would skip the walk"
  rm -rf "$run_dir"
  echo "PASS: a handover leaves exactly the new uid's marker"
}

test_marker_makes_a_second_run_a_noop() {
  run_dir="$(new_run_dir)"
  tree="$run_dir/workspaces"
  mkdir -p "$tree"
  : > "$tree/.cheese-uid-1000"

  run_ownership "$run_dir" \
    env AGENT_UID=1000 AGENT_GID=1000 OWNERSHIP_REPORT_FILE="$run_dir/report" \
    "$SCRIPT" fake/image "$tree" >/dev/null \
    || fail "a already-migrated tree should succeed"

  ! grep -q 'HANDOVER' "$run_dir/docker.log" \
    || fail "walked a tree that already carries the marker"
  [ "$(cat "$run_dir/report")" = no ] \
    || fail "a no-op run must report 'no' so a rollback does not hand anything back"
  rm -rf "$run_dir"
  echo "PASS: the marker makes a second run a no-op, reported as such"
}

test_report_records_a_real_handover() {
  run_dir="$(new_run_dir)"
  tree="$run_dir/workspaces"
  mkdir -p "$tree"

  run_ownership "$run_dir" \
    env AGENT_UID=1000 AGENT_GID=1000 OWNERSHIP_REPORT_FILE="$run_dir/report" \
    "$SCRIPT" fake/image "$tree" >/dev/null \
    || fail "handover of a fresh tree should succeed"

  [ "$(cat "$run_dir/report")" = yes ] \
    || fail "a real handover must report 'yes' so a rollback hands the tree back"
  rm -rf "$run_dir"
  echo "PASS: a real handover is reported to the caller"
}

test_report_survives_a_failing_run() {
  run_dir="$(new_run_dir)"
  tree="$run_dir/workspaces"
  mkdir -p "$tree"
  secret="$run_dir/git-credentials"
  : > "$secret"

  # Trees move, then the secret check fails: exactly the 2026-08-11 sequence.
  # The caller still has to learn the trees moved, or a rollback skips the
  # hand-back and leaves the box in the state that outage was made of.
  run_ownership "$run_dir" \
    env AGENT_UID=1000 AGENT_GID=1000 \
    SECRET_FILE_PATHS="$secret" OWNERSHIP_PROBE_FAILS=1 \
    OWNERSHIP_REPORT_FILE="$run_dir/report" \
    "$SCRIPT" fake/image "$tree" >/dev/null 2>&1 \
    && fail "the run was supposed to fail on the unreadable secret"

  [ "$(cat "$run_dir/report")" = yes ] \
    || fail "a half-finished run must still report that files moved"
  rm -rf "$run_dir"
  echo "PASS: a failing run still reports the files it already moved"
}

test_absent_path_is_skipped() {
  run_dir="$(new_run_dir)"
  run_ownership "$run_dir" \
    "$SCRIPT" fake/image "$run_dir/nowhere" >/dev/null \
    || fail "an absent bind-mount path must not fail the deploy"
  rm -rf "$run_dir"
  echo "PASS: a path absent on this box is skipped"
}

for name in \
  test_secret_file_is_handed_over \
  test_dev_null_is_never_chowned \
  test_unreadable_secret_still_fails_loudly \
  test_handover_drops_other_uid_markers \
  test_marker_makes_a_second_run_a_noop \
  test_report_records_a_real_handover \
  test_report_survives_a_failing_run \
  test_absent_path_is_skipped; do
  if [ "$CASE" = all ] || [ "$CASE" = "$name" ]; then
    "$name"
  fi
done
