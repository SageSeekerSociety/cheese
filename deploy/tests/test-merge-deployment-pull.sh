#!/usr/bin/env bash
# The deployment merge (deploy/merge-deployment.sh) starts its scratch
# databases from images a deploy may have pruned, over a link to the registry
# that resets single connections. A reset on the way to those images must not
# end the merge, and a box that still has them must not need the registry.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/merge-deployment-pull-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
EXPORT="$RUN_DIR/export"
mkdir -p "$FAKE_BIN" "$EXPORT" "$RUN_DIR/backups"
: > "$EXPORT/database.dump"
: > "$RUN_DIR/present"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Stands in for docker on a box whose local images are listed in
# $FAKE_PRESENT, and whose next $(cat $FAKE_RESETS) registry fetches are reset
# the way the dev box's link resets them. Any image reached by its id is the
# running backend's and is always present.
cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
set -u
present() { case "$1" in sha256:*) return 0 ;; esac; grep -qxF "$1" "$FAKE_PRESENT"; }
fetch() {
  local left
  left="$(cat "$FAKE_RESETS")"
  if [ "$left" -gt 0 ]; then
    echo $((left - 1)) > "$FAKE_RESETS"
    echo "Error response from daemon: failed to fetch anonymous token: Get \"https://auth.docker.io/token\": read tcp 10.0.0.2:41234->3.94.224.37:443: read: connection reset by peer" >&2
    return 1
  fi
  echo "$1" >> "$FAKE_PRESENT"
}
case "$1" in
  ps) echo "backend cheese-backend-1" ;;
  inspect)
    case "$*" in
      *'{{.Image}}'*) echo sha256:backend ;;
      *) echo DATA_ENCRYPTION_KEY=target-key ;;
    esac
    ;;
  image) [ "$2" = inspect ] && present "$3" ;;
  pull) fetch "$2" || exit 1 ;;
  run)
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        -d|--rm) shift ;;
        -*) shift 2 ;;
        *) break ;;
      esac
    done
    if ! present "$1"; then
      echo "Unable to find image '$1' locally" >&2
      fetch "$1" || { echo "docker: see 'docker run --help'." >&2; exit 125; }
    fi
    echo container-id
    ;;
  exec)
    case "$*" in
      *alembic_version*) echo head ;;
      *information_schema*) echo '{}' ;;
    esac
    ;;
  rm) ;;
  *) echo "unexpected docker $*" >&2; exit 99 ;;
esac
EOF
cat > "$FAKE_BIN/sleep" <<'EOF'
#!/bin/sh
EOF
chmod +x "$FAKE_BIN/docker" "$FAKE_BIN/sleep"

# merge <registry fetches to reset> [VAR=value…] — runs a dry run with the
# given environment and prints its exit status.
merge() {
  echo "$1" > "$RUN_DIR/resets"
  shift
  local rc=0
  env "$@" PATH="$FAKE_BIN:$PATH" FAKE_PRESENT="$RUN_DIR/present" FAKE_RESETS="$RUN_DIR/resets" \
    CHEESE_BACKUP_DIR="$RUN_DIR/backups" \
    bash "$ROOT/deploy/merge-deployment.sh" dry-run batch-1 "$EXPORT" \
    > "$RUN_DIR/out" 2>&1 || rc=$?
  echo "$rc"
}

# 1. The scratch images were pruned and the first fetch is reset: the merge
#    still runs to the end.
rc="$(merge 1)"
[ "$rc" = 0 ] || fail "a single connection reset ended the merge (exit $rc): $(cat "$RUN_DIR/out")"

# 2. The images are on the box, the registry cannot be reached at all, and
#    there is no time to wait for it: the merge does not need it.
rc="$(merge 1000 DEPLOY_PULL_BUDGET_SECONDS=0)"
[ "$rc" = 0 ] || fail "a merge with its images on the box needed the registry (exit $rc): $(cat "$RUN_DIR/out")"

echo "merge-deployment pull: all cases passed"
