#!/usr/bin/env bash
# The eviction runs `docker rm -f` against production, so what it must NOT
# remove matters more than what it must.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
EVICT="$ROOT/deploy/evict-foreign-container.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/evict-foreign-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
CALLS="$RUN_DIR/docker.calls"
mkdir -p "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Stands in for docker. FAKE_OWNER is what `inspect --format` prints for the
# compose-project label; FAKE_MISSING makes inspect fail the way it does for a
# container that does not exist — the two cases this script has to tell apart.
cat > "$FAKE_BIN/docker" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
case "$1" in
  inspect)
    if test "${FAKE_MISSING:-0}" = 1; then
      echo "Error: No such object" >&2
      exit 1
    fi
    printf '%s\n' "${FAKE_OWNER-}"
    ;;
  rm) exit 0 ;;
esac
EOF
chmod +x "$FAKE_BIN/docker"

run_evict() {
  : > "$CALLS"
  PATH="$FAKE_BIN:$PATH" FAKE_DOCKER_CALLS="$CALLS" "$@" bash "$EVICT" \
    cheese-browser-render cheese
}

removed() {
  grep -q '^rm -f cheese-browser-render$' "$CALLS"
}

# 1. Ours. Compose recreates its own containers; removing one here would delete
#    a running service on every deploy.
run_evict env FAKE_OWNER=cheese >/dev/null
! removed || fail "removed a container this deployment owns"

# 2. Another compose project — the case that has been blocking browser-render.
out="$(run_evict env FAKE_OWNER=browser-render)"
removed || fail "left a foreign compose project holding the name"
case "$out" in
  *browser-render*) : ;;
  *) fail "said nothing about who was holding the name: $out" ;;
esac

# 3. No compose labels at all: a plain `docker run --name …`. It holds the name
#    just as firmly, and an earlier draft skipped it by testing for an empty
#    label and treating that as "no such container".
run_evict env FAKE_OWNER= >/dev/null
removed || fail "left an unlabelled container holding the name"

# 4. No such container. Nothing to do, and nothing removed — this runs on every
#    deploy, including the ones where the name is free.
run_evict env FAKE_MISSING=1 >/dev/null
! removed || fail "tried to remove a container that does not exist"

# 5. Exits 0 in every case above, so the caller can run it unconditionally.
for case_env in "FAKE_OWNER=cheese" "FAKE_OWNER=other" "FAKE_MISSING=1"; do
  run_evict env "$case_env" >/dev/null \
    || fail "non-zero exit for $case_env would abort an otherwise fine deploy"
done

echo 'PASS: foreign-container eviction'
