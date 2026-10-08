#!/usr/bin/env bash
# Which commit deploy-drift fires when the box has drifted from main.
#
# Firing main is right almost always, and wrong in exactly one state: main's
# tree no longer has the two slots while the `-b` slots serve, where a release
# of main refuses and re-firing it hourly is a red loop that changes nothing
# (deploy/tests/test-pre-slot-release.sh is the other half — it runs the old
# script and shows the refusal). This drives the choice with fakes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/deploy/converge-deploy-drift.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/converge-drift-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

FAKE_BIN="$RUN_DIR/bin"
GH_CALLS="$RUN_DIR/gh.calls"
DOCKER_CALLS="$RUN_DIR/docker.calls"
FIXTURES="$RUN_DIR/fixtures"
mkdir -p "$FAKE_BIN" "$FIXTURES"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

# Stands in for gh. FAKE_COMMITS is the sha list the commits API answers with,
# newest first; FAKE_FIXTURES/<sha> is what the contents API answers for that
# ref (a missing file is the 404 for a ref that does not have the path).
cat > "$FAKE_BIN/gh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_GH_CALLS:?}"
args="$*"
case "$args" in
  api*contents*)
    ref="${args##*ref=}"
    [ -f "${FAKE_FIXTURES:?}/$ref" ] || exit 1
    cat "$FAKE_FIXTURES/$ref"
    ;;
  api*commits*) printf '%s\n' ${FAKE_COMMITS:-} ;;
  workflow\ run*) exit 0 ;;
  *)
    echo "fake gh: unhandled arguments: $args" >&2
    exit 1
    ;;
esac
EOF
chmod +x "$FAKE_BIN/gh"

# Stands in for docker: FAKE_BACKEND_SERVICES names the running backend slots,
# one per line — `backend`, `backend-b`, both (a release in flight), or none.
cat > "$FAKE_BIN/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_DOCKER_CALLS:?}"
printf '%s\n' "${FAKE_BACKEND_SERVICES:-}"
EOF
chmod +x "$FAKE_BIN/docker"

SLOTS_TREE="$FIXTURES/main-with-slots"
printf 'backend_services() { printf "backend backend-b\\n"; }\n' > "$SLOTS_TREE"
BEFORE_SLOTS_TREE="$FIXTURES/main-before-slots"
printf 'switch_active_backend() { :; }\n' > "$BEFORE_SLOTS_TREE"
# `main` itself, i.e. the working tree deploy-drift checked out.
MAIN_SCRIPT="$RUN_DIR/main-deploy-docker.sh"

run_converge() {
  : > "$GH_CALLS"
  : > "$DOCKER_CALLS"
  MAIN_DEPLOY_SCRIPT="$MAIN_SCRIPT" \
  GITHUB_REPOSITORY="example/app" \
    PATH="$FAKE_BIN:$PATH" \
    FAKE_GH_CALLS="$GH_CALLS" \
    FAKE_DOCKER_CALLS="$DOCKER_CALLS" \
    FAKE_FIXTURES="$FIXTURES" \
    FAKE_BACKEND_SERVICES="${FAKE_BACKEND_SERVICES:-}" \
    FAKE_COMMITS="${FAKE_COMMITS:-}" \
    bash "$SCRIPT" > "$RUN_DIR/out" 2>&1
}

dispatched() {
  grep '^workflow run' "$GH_CALLS" || true
}

# main's tree has the slots: the ordinary convergence, which this must not
# change. The box's slots do not even have to be asked about.
cp "$SLOTS_TREE" "$MAIN_SCRIPT"
FAKE_BACKEND_SERVICES=backend-b run_converge
[ "$(dispatched)" = "workflow run deploy-dev.yml --ref main" ] \
  || fail "main with the slots did not re-dispatch plain main: $(dispatched)"

# main's tree predates the slots and the -b slot serves: firing main refuses.
# Fire the newest commit that still has the slots instead — it is the parent of
# the revert, i.e. the second entry here, not the first.
cp "$BEFORE_SLOTS_TREE" "$MAIN_SCRIPT"
cp "$BEFORE_SLOTS_TREE" "$FIXTURES/aaaaaaa"
cp "$SLOTS_TREE" "$FIXTURES/bbbbbbb"
FAKE_BACKEND_SERVICES=backend-b FAKE_COMMITS="aaaaaaa bbbbbbb" run_converge || {
  cat "$RUN_DIR/out"; fail "the cross-slot case did not converge"; }
[ "$(dispatched)" = "workflow run deploy-dev.yml --ref main -f ref=bbbbbbb" ] \
  || fail "did not release the slot-bearing commit: $(dispatched)"
grep -q 'the newest commit on main with the slots' "$RUN_DIR/out" \
  || fail "did not say which commit it released and why"

# The same state, but no commit on main has the slots: do not re-dispatch a
# release that will refuse, and say what to run by hand.
FAKE_BACKEND_SERVICES=backend-b FAKE_COMMITS="aaaaaaa" run_converge \
  && fail "re-dispatched a release that cannot converge"
[ -z "$(dispatched)" ] || fail "re-dispatched anyway: $(dispatched)"
grep -q '::error::' "$RUN_DIR/out" || fail "the refusal was not an error annotation"
grep -q 'keep refusing' "$RUN_DIR/out" || fail "the refusal did not say why"

# The first slots serve: main's older script does release from there, so this
# is the ordinary path and must not be turned into the slot-bearing release.
FAKE_BACKEND_SERVICES=backend FAKE_COMMITS="aaaaaaa bbbbbbb" run_converge \
  || { cat "$RUN_DIR/out"; fail "the first-slot case failed"; }
[ "$(dispatched)" = "workflow run deploy-dev.yml --ref main" ] \
  || fail "the first slots did not re-dispatch plain main: $(dispatched)"

# No backend at all: bootstrap, which is main's own release like any other.
FAKE_BACKEND_SERVICES= FAKE_COMMITS="aaaaaaa bbbbbbb" run_converge \
  || { cat "$RUN_DIR/out"; fail "the bootstrap case failed"; }
[ "$(dispatched)" = "workflow run deploy-dev.yml --ref main" ] \
  || fail "no running backend did not re-dispatch plain main: $(dispatched)"

# Both backends running: a release is in flight, and a decision taken against
# a half-switched box would act on a slot that is about to stop.
FAKE_BACKEND_SERVICES=$'backend\nbackend-b' FAKE_COMMITS="aaaaaaa bbbbbbb" run_converge \
  || { cat "$RUN_DIR/out"; fail "the in-flight case failed"; }
[ -z "$(dispatched)" ] || fail "acted while a release was in flight: $(dispatched)"
grep -q 'in flight' "$RUN_DIR/out" || fail "did not say a release was in flight"

echo "PASS: deploy drift fires main, or the newest slot-bearing commit across the two-slot boundary, and never re-dispatches a release that would refuse"
