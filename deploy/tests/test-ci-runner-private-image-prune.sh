#!/usr/bin/env bash
# The private-image prune: two runner slots on one box finishing at once must
# both succeed, because the Docker daemon refuses a second concurrent image
# prune ("a prune operation is already running"); and it removes only the
# untagged images carrying the private build's label.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PRUNE="$ROOT/deploy/ci-runner/prune-private-images.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-ci-private-prune-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
mkdir -p "$FAKE_BIN"
export CHEESE_DOCKER_PRUNE_LOCK="$RUN_DIR/prune.lock"
export FAKE_DOCKER_CALLS="$RUN_DIR/docker.calls"
export FAKE_PRUNING="$RUN_DIR/pruning"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# A daemon that, like the real one, refuses an image prune while another is
# still running. The prune takes long enough for two callers to overlap.
cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
if ! mkdir "${FAKE_PRUNING:?}" 2>/dev/null; then
  echo "Error response from daemon: a prune operation is already running" >&2
  exit 1
fi
sleep 1
rmdir "$FAKE_PRUNING"
EOF
chmod +x "$FAKE_BIN/docker"
export PATH="$FAKE_BIN:$PATH"

# The fake really does refuse an overlapping prune, so the case below would
# fail without the lock.
: > "$FAKE_DOCKER_CALLS"
docker image prune & first=$!
until [ -d "$FAKE_PRUNING" ]; do sleep 0.05; done
if docker image prune 2>/dev/null; then fail "the fake daemon allowed two prunes at once"; fi
wait "$first"

# 1. Two slots finishing together: both prunes run, one after the other.
: > "$FAKE_DOCKER_CALLS"
bash "$PRUNE" > "$RUN_DIR/a.out" 2>&1 & a=$!
bash "$PRUNE" > "$RUN_DIR/b.out" 2>&1 & b=$!
wait "$a" || fail "first slot's prune failed: $(cat "$RUN_DIR/a.out")"
wait "$b" || fail "second slot's prune failed: $(cat "$RUN_DIR/b.out")"
[ "$(wc -l < "$FAKE_DOCKER_CALLS")" -eq 2 ] || fail "expected two prunes, got: $(cat "$FAKE_DOCKER_CALLS")"

# 2. Only untagged images with the private build's label: no --all, no other filter.
while read -r call; do
  [ "$call" = "image prune --force --filter label=cheese.ci.private-executor=true" ] ||
    fail "unexpected docker call: $call"
done < "$FAKE_DOCKER_CALLS"

echo "test-ci-runner-private-image-prune: ok"
