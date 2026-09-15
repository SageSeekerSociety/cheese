#!/usr/bin/env bash
# The job-started hook: release a forwarded mount whose server is gone, and leave
# alone one that still answers — with two runner slots per machine, a mount that
# answers belongs to a job running right now.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HOOK="$ROOT/deploy/ci-runner/job-started-hook.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-ci-job-hook-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
CALLS="$RUN_DIR/fusermount.calls"
mkdir -p "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# `mount` output in the shape the real one has, listing whatever the case asks for.
cat > "$FAKE_BIN/mount" <<'EOF'
#!/bin/sh
printf 'sysfs on /sys type sysfs (rw)\n'
for point in ${FAKE_FUSE_POINTS:-}; do
  printf 'FuseProject on %s type fuse (rw,nosuid,nodev)\n' "$point"
done
EOF

# A dead mount is one whose stat does not answer. The fake fails for the paths the
# case names as dead and succeeds for the rest, which is the whole distinction.
cat > "$FAKE_BIN/stat" <<'EOF'
#!/bin/sh
for dead in ${FAKE_DEAD_POINTS:-}; do
  for argument in "$@"; do
    if test "$dead" = "$argument"; then
      echo "stat: cannot statx '$argument': Transport endpoint is not connected" >&2
      exit 1
    fi
  done
done
exit 0
EOF

cat > "$FAKE_BIN/fusermount3" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_FUSERMOUNT_CALLS:?}"
exit 0
EOF

# The hook also runs the disk guard, which is its own suite's subject. Give it a
# machine with room so it stays out of these cases — otherwise this test passes or
# fails on how full the developer's laptop happens to be.
cat > "$FAKE_BIN/df" <<'EOF'
#!/bin/sh
printf 'Avail\n99G\n'
EOF

chmod +x "$FAKE_BIN"/*

run_hook() {
  : > "$CALLS"
  PATH="$FAKE_BIN:$PATH" FAKE_FUSERMOUNT_CALLS="$CALLS" \
    FAKE_FUSE_POINTS="${1:-}" FAKE_DEAD_POINTS="${2:-}" \
    bash "$HOOK"
}

# 1. A dead mount is released.
out="$(run_hook "/work/room/forwarded-project" "/work/room/forwarded-project")"
grep -q "released a dead forwarded mount at /work/room/forwarded-project" <<<"$out" \
  || fail "the hook did not report releasing the dead mount: $out"
grep -qx -- "-u /work/room/forwarded-project" "$CALLS" \
  || fail "expected a plain unmount of the dead mount, got: $(cat "$CALLS")"

# 2. A mount that still answers is left alone — another slot may be using it.
out="$(run_hook "/work/live/forwarded-project" "")"
[ ! -s "$CALLS" ] || fail "a live mount was unmounted: $(cat "$CALLS")"
[ -z "$out" ] || fail "a live mount was reported: $out"

# 3. Nothing mounted: the hook is silent and still succeeds.
out="$(run_hook "" "")"
[ -z "$out" ] || fail "the hook spoke with nothing to do: $out"

# 4. One dead among several: only that one is touched.
out="$(run_hook "/work/a/forwarded-project /work/b/forwarded-project" "/work/b/forwarded-project")"
[ "$(wc -l < "$CALLS")" -eq 1 ] || fail "expected one unmount, got: $(cat "$CALLS")"
grep -qx -- "-u /work/b/forwarded-project" "$CALLS" \
  || fail "the wrong mount was released: $(cat "$CALLS")"

echo "ci-runner job-started hook: all cases passed"
