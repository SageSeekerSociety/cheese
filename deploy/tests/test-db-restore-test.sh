#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CASE_ROOT="$ROOT/.tmp/db-restore-test-regression.$$"
FAKE_BIN="$CASE_ROOT/bin"
OUTPUT="$CASE_ROOT/output.log"
DUMP="$CASE_ROOT/cheese-test.dump"

cleanup() { rm -rf "$CASE_ROOT"; }
trap cleanup EXIT
mkdir -p "$FAKE_BIN"
: > "$DUMP"

cat > "$FAKE_BIN/docker" <<'FAKE_DOCKER'
#!/usr/bin/env bash
set -u

case "${1:-}" in
  compose)
    printf '{"services":{"postgres":{"image":"%s"}}}\n' "$RESTORE_TEST_COMPOSE_IMAGE"
    ;;
  run)
    printf '%s\n' "$*" > "$RESTORE_TEST_RUN_LOG"
    # Model a server that cannot load the required extension from vanilla PG.
    case "$*" in
      *postgres:17*|*shared_preload_libraries=pg_search*) ;;
      *) echo 'missing pg_search preload' >&2; exit 96 ;;
    esac
    if [[ "$*" == *postgres:17* ]]; then
      echo 'could not access file pg_search' >&2
      exit 95
    fi
    echo restore-test-container
    ;;
  cp|stop)
    ;;
  exec)
    shift
    shift
    case "${1:-}" in
      pg_isready)
        ;;
      pg_restore)
        if [[ "$*" == *--list* ]]; then
          [ "${RESTORE_TEST_NO_EXTENSION:-0}" = 1 ] || echo '5; 3079 16385 EXTENSION - pg_search'
          exit 0
        fi
        if [ "${RESTORE_TEST_PG_RESTORE_STATUS:-0}" -ne 0 ]; then
          echo "simulated restore failure" >&2
          exit "$RESTORE_TEST_PG_RESTORE_STATUS"
        fi
        ;;
      psql)
        if [ "${RESTORE_TEST_QUERY_STATUS:-0}" -ne 0 ]; then
          echo "simulated query failure" >&2
          exit "$RESTORE_TEST_QUERY_STATUS"
        fi
        case "$*" in
          *pg_extension*) echo 1 ;;
          *information_schema.tables*) echo 50 ;;
          *alembic_version*) echo 1 ;;
          *'from "user"'*|*'from projects'*|*'from topics'*|*'from blocks'*)
            echo "${RESTORE_TEST_BUSINESS_ROWS:-7}"
            ;;
          *) echo 0 ;;
        esac
        ;;
      *)
        echo "unexpected docker exec command: $*" >&2
        exit 97
        ;;
    esac
    ;;
  *)
    echo "unexpected docker command: $*" >&2
    exit 98
    ;;
esac
FAKE_DOCKER
chmod +x "$FAKE_BIN/docker"

run_restore() {
  local restore_status="$1"
  local business_rows="$2"
  local allow_empty="${3:-0}"
  local query_status="${4:-0}"
  set +e
  PATH="$FAKE_BIN:$PATH" \
    RESTORE_TEST_COMPOSE_IMAGE="${RESTORE_TEST_COMPOSE_IMAGE:-paradedb/paradedb:next-pg17}" \
    RESTORE_TEST_RUN_LOG="$CASE_ROOT/run.log" \
    RESTORE_TEST_PG_RESTORE_STATUS="$restore_status" \
    RESTORE_TEST_BUSINESS_ROWS="$business_rows" \
    RESTORE_TEST_QUERY_STATUS="$query_status" \
    CHEESE_RESTORE_ALLOW_EMPTY="$allow_empty" \
    CHEESE_RESTORE_LOG_DIR="$CASE_ROOT/logs" \
    bash "$ROOT/deploy/db-restore-test.sh" "$DUMP" > "$OUTPUT" 2>&1
  RUN_STATUS=$?
  set -e
}

test_restore_failure() {
  run_restore 42 7
  if [ "$RUN_STATUS" -eq 0 ]; then
    cat "$OUTPUT"
    echo "FAIL: pg_restore exit 42 was reported as success" >&2
    return 1
  fi
  grep -q "RESTORE-TEST FAIL: pg_restore exited 42" "$OUTPUT"
  grep -q "simulated restore failure" "$OUTPUT"
  echo "PASS: pg_restore failure is observable"
}

test_populated_restore() {
  run_restore 0 7
  if [ "$RUN_STATUS" -ne 0 ]; then
    cat "$OUTPUT"
    echo "FAIL: populated successful restore was rejected" >&2
    return 1
  fi
  grep -q "critical business rows=7" "$OUTPUT"
  grep -q "RESTORE-TEST PASS" "$OUTPUT"
  echo "PASS: populated successful restore passes"
}

test_empty_restore_rejected() {
  run_restore 0 0
  if [ "$RUN_STATUS" -eq 0 ]; then
    cat "$OUTPUT"
    echo "FAIL: empty restore passed without an override" >&2
    return 1
  fi
  grep -q "critical business rows=0" "$OUTPUT"
  grep -q "RESTORE-TEST FAIL" "$OUTPUT"
  echo "PASS: empty restore is observable by default"
}

test_empty_restore_allowed() {
  run_restore 0 0 1
  if [ "$RUN_STATUS" -ne 0 ]; then
    cat "$OUTPUT"
    echo "FAIL: explicit empty-installation override was rejected" >&2
    return 1
  fi
  grep -q "empty application data accepted by CHEESE_RESTORE_ALLOW_EMPTY=1" "$OUTPUT"
  grep -q "RESTORE-TEST PASS" "$OUTPUT"
  echo "PASS: legitimate empty restore requires a visible override"
}

test_query_failure() {
  run_restore 0 7 0 43
  if [ "$RUN_STATUS" -eq 0 ]; then
    cat "$OUTPUT"
    echo "FAIL: failed verification query was reported as success" >&2
    return 1
  fi
  grep -q "RESTORE-TEST FAIL: could not query pg_search extension" "$OUTPUT"
  grep -q "simulated query failure" "$OUTPUT"
  echo "PASS: failed verification query is observable"
}

test_extension_and_image() {
  # A composition update must select the new image, not a second pinned copy.
  RESTORE_TEST_COMPOSE_IMAGE=paradedb/paradedb:future-pg17 run_restore 0 7
  [ "$RUN_STATUS" -eq 0 ] || { cat "$OUTPUT"; return 1; }
  grep -q 'paradedb/paradedb:future-pg17' "$CASE_ROOT/run.log"
  grep -q 'shared_preload_libraries=pg_search' "$CASE_ROOT/run.log"
  RESTORE_TEST_NO_EXTENSION=1 run_restore 0 7
  [ "$RUN_STATUS" -ne 0 ]
  grep -q 'dump does not contain the pg_search extension' "$OUTPUT"
  CHEESE_PG_IMAGE=postgres:17 run_restore 0 7
  [ "$RUN_STATUS" -ne 0 ]
  grep -q 'RESTORE-TEST FAIL' "$OUTPUT"
  echo 'PASS: composition changes propagate, missing extension and wrong image fail'
}

case "${1:-all}" in
  restore_failure)
    test_restore_failure
    ;;
  all)
    test_restore_failure
    test_populated_restore
    test_empty_restore_rejected
    test_empty_restore_allowed
    test_query_failure
    test_extension_and_image
    ;;
  *)
    echo "unknown test case: $1" >&2
    exit 2
    ;;
esac
