#!/usr/bin/env bash
# End-to-end tests for deploy/viking-backup.sh — the openviking memory backup.
#
# Drives the real script against a real temp tree with real tar, and swaps in a
# fake tar only where a failure has to be injected (a torn archive, a tar that
# dies). The off-site leg is a fake python that records how it was called.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/deploy/viking-backup.sh"
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cheese-viking-backup-test.XXXXXX")"
trap 'rm -rf "$RUN_DIR"' EXIT

REAL_TAR="$(command -v tar)"
FAKE_BIN="$RUN_DIR/bin"
VIKING="$RUN_DIR/viking"
BACKUPS="$RUN_DIR/backups"
OUT="$RUN_DIR/run.out"
R2_CALLS="$RUN_DIR/r2.calls"
TAR_COUNT="$RUN_DIR/tar.count"
mkdir -p "$FAKE_BIN"

fail() {
  echo "FAIL: $*" >&2
  echo "--- last run output ---" >&2
  cat "$OUT" >&2 || true
  exit 1
}

# Fake tar. Creation mode is driven by FAKE_TAR_MODE; listing always delegates
# to the real tar so the script's integrity check stays honest.
cat > "$FAKE_BIN/tar" <<'EOF'
#!/bin/sh
for a in "$@"; do
  case "$a" in
    -tzf|-t*) exec "$REAL_TAR" "$@" ;;
  esac
done
n=0
if test -f "$FAKE_TAR_COUNT"; then n=$(cat "$FAKE_TAR_COUNT"); fi
n=$((n + 1))
echo "$n" > "$FAKE_TAR_COUNT"
# The output path is the argument right after -czf.
out=""
prev=""
for a in "$@"; do
  if test "$prev" = "-czf"; then out="$a"; fi
  prev="$a"
done
case "${FAKE_TAR_MODE:-real}" in
  fatal)
    echo "fake tar: exploding" >&2
    exit 2
    ;;
  garbage)
    printf 'not a gzip stream at all\n' > "$out"
    exit 0
    ;;
  churn)
    "$REAL_TAR" "$@" || exit $?
    # Write into the tree AFTER the archive is closed: the manifest taken after
    # tar will differ, which is exactly what a live backend does.
    date +%s%N > "$FAKE_TAR_TREE/churned-$n"
    exit 0
    ;;
  churn-once)
    "$REAL_TAR" "$@" || exit $?
    if test "$n" -eq 1; then date +%s%N > "$FAKE_TAR_TREE/churned-once"; fi
    exit 0
    ;;
  churn-conf)
    "$REAL_TAR" "$@" || exit $?
    # Only the excluded config moves. The backend rewrites it on every start,
    # and it is not in the archive, so it must not count as the tree changing.
    printf '{"vlm":{"api_key":"ROTATED-%s"}}\n' "$n" > "$FAKE_TAR_TREE/ov.conf"
    exit 0
    ;;
  *)
    exec "$REAL_TAR" "$@"
    ;;
esac
EOF

# Stand-in for backend/.venv/bin/python running ops/r2-upload.py.
cat > "$FAKE_BIN/fake-python" <<'EOF'
#!/bin/sh
printf 'prefix=%s args=%s\n' "${R2_PREFIX:-unset}" "$*" >> "$FAKE_R2_CALLS"
exit "${FAKE_R2_EXIT:-0}"
EOF

chmod +x "$FAKE_BIN"/*

seed_tree() {
  rm -rf "$VIKING"
  mkdir -p "$VIKING/data/memories"
  echo "a remembered fact" > "$VIKING/data/memories/fact-1.md"
  echo "another one" > "$VIKING/data/memories/fact-2.md"
  printf '{"vlm":{"api_key":"SUPERSECRETKEY"}}\n' > "$VIKING/ov.conf"
}

# Runs the script with the fakes on PATH. Extra env comes from the caller.
run_backup() {
  : > "$OUT"
  set +e
  env \
    PATH="$FAKE_BIN:/usr/bin:/bin" \
    REAL_TAR="$REAL_TAR" \
    FAKE_TAR_COUNT="$TAR_COUNT" \
    FAKE_TAR_TREE="$VIKING" \
    FAKE_R2_CALLS="$R2_CALLS" \
    VIKING_HOST_PATH="$VIKING" \
    CHEESE_BACKUP_DIR="$BACKUPS" \
    CHEESE_R2_ENV="$RUN_DIR/absent-r2.env" \
    CHEESE_R2_UPLOAD="$RUN_DIR/absent-upload.py" \
    CHEESE_VENV_PY="$RUN_DIR/absent-python" \
    "$@" \
    "$SCRIPT" > "$OUT" 2>&1
  rc=$?
  set -e
  return "$rc"
}

reset_state() {
  rm -rf "$BACKUPS"
  rm -f "$TAR_COUNT" "$R2_CALLS"
  mkdir -p "$BACKUPS"
}

archives() {
  find "$BACKUPS" -maxdepth 1 -name 'cheese-viking-*.tar.gz' -type f | LC_ALL=C sort
}

# --- 1. no directory at all -------------------------------------------------
reset_state
rm -rf "$VIKING"
run_backup || fail "a missing memory dir must not be an error"
grep -q 'does not exist' "$OUT" || fail "missing dir was not explained in the log"
test -z "$(archives)" || fail "an archive was written for a dir that does not exist"
test ! -f "$BACKUPS/.viking-last-success" || fail "freshness marker written with nothing backed up"
echo "PASS: a missing memory dir is a no-op, not a failure"

# --- 2. mounted but empty (MEMORY_BACKEND=db, the steady state today) --------
reset_state
rm -rf "$VIKING"; mkdir -p "$VIKING"
run_backup || fail "an empty memory dir must not be an error"
grep -q 'no memory data yet' "$OUT" || fail "empty dir was not explained in the log"
test -z "$(archives)" || fail "an empty tree produced an archive"
echo "PASS: an empty memory tree is skipped, not archived"

# --- 3. a dir holding only the config still counts as empty -----------------
reset_state
rm -rf "$VIKING"; mkdir -p "$VIKING"
printf '{"vlm":{"api_key":"SUPERSECRETKEY"}}\n' > "$VIKING/ov.conf"
run_backup || fail "a config-only dir must not be an error"
test -z "$(archives)" || fail "a config-only tree produced an archive of nothing but secrets"
echo "PASS: a tree holding only ov.conf is not archived"

# --- 4. happy path ----------------------------------------------------------
reset_state
seed_tree
run_backup || fail "a quiet backup should succeed"
mapfile -t made < <(archives)
test "${#made[@]}" -eq 1 || fail "expected exactly one archive, got ${#made[@]}"
case "${made[0]}" in
  *-hot.tar.gz) fail "a quiet tree produced a hot snapshot" ;;
esac
"$REAL_TAR" -tzf "${made[0]}" > "$RUN_DIR/listing" || fail "the archive is not readable"
grep -q 'viking/data/memories/fact-1.md' "$RUN_DIR/listing" || fail "memory files missing from the archive"
grep -q 'ov.conf' "$RUN_DIR/listing" && fail "ov.conf (plaintext API keys) was archived"
"$REAL_TAR" -xzOf "${made[0]}" > "$RUN_DIR/content" 2>/dev/null || true
grep -q 'SUPERSECRETKEY' "$RUN_DIR/content" && fail "the model API key ended up inside the archive"
grep -q 'a remembered fact' "$RUN_DIR/content" || fail "memory content missing from the archive"
test -f "$BACKUPS/.viking-last-success" || fail "freshness marker not written"
test -z "$(find "$BACKUPS" -name '*.partial')" || fail "a .partial file was left behind"
grep -q 'verified' "$BACKUPS/backup.log" || fail "the shared backup log was not appended to"
echo "PASS: a quiet backup archives the tree, verifies it, and leaves the secrets out"

# --- 5. tar dies -> non-zero, nothing left behind ---------------------------
reset_state
seed_tree
if run_backup FAKE_TAR_MODE=fatal; then fail "a failed tar reported success"; fi
test -z "$(archives)" || fail "a failed tar still published an archive"
test -z "$(find "$BACKUPS" -name '*.partial')" || fail "a failed tar left a .partial behind"
test ! -f "$BACKUPS/.viking-last-success" || fail "freshness marker written after a failed tar"
test "$(cat "$TAR_COUNT")" -eq 1 \
  || fail "a broken tar was retried $(cat "$TAR_COUNT") times; a fatal exit is not churn"
echo "PASS: a failing tar exits non-zero, retries nothing, and publishes nothing"

# --- 6. archive is unreadable -> non-zero, nothing published ----------------
reset_state
seed_tree
if run_backup FAKE_TAR_MODE=garbage; then fail "an unreadable archive reported success"; fi
grep -q 'unreadable' "$OUT" || fail "the unreadable archive was not named as the reason"
test -z "$(archives)" || fail "an unreadable archive was published anyway"
test -z "$(find "$BACKUPS" -name '*.partial')" || fail "a .partial file was left behind"
test ! -f "$BACKUPS/.viking-last-success" || fail "freshness marker written for an unreadable archive"
echo "PASS: an archive that cannot be read is not a backup"

# --- 7. the backend writes during every attempt -> kept, but marked hot -----
reset_state
seed_tree
run_backup FAKE_TAR_MODE=churn CHEESE_VIKING_QUIESCE_ATTEMPTS=2 \
  || fail "a busy tree must still produce a backup"
mapfile -t made < <(archives)
test "${#made[@]}" -eq 1 || fail "expected exactly one archive, got ${#made[@]}"
case "${made[0]}" in
  *-hot.tar.gz) : ;;
  *) fail "a torn snapshot was not named -hot: ${made[0]}" ;;
esac
test "$(cat "$TAR_COUNT")" -eq 2 || fail "the quiesce retry did not use every attempt"
grep -q 'no quiet window' "$OUT" || fail "the hot snapshot was not called out in the log"
test -f "$BACKUPS/.viking-last-success" || fail "a hot snapshot is still a backup; marker missing"
echo "PASS: a tree written to during every attempt yields a kept, clearly-marked hot snapshot"

# --- 8. one churned attempt, then quiet -> retried into a clean snapshot ----
reset_state
seed_tree
run_backup FAKE_TAR_MODE=churn-once CHEESE_VIKING_QUIESCE_ATTEMPTS=3 \
  || fail "the retry path should succeed"
mapfile -t made < <(archives)
test "${#made[@]}" -eq 1 || fail "expected exactly one archive, got ${#made[@]}"
case "${made[0]}" in
  *-hot.tar.gz) fail "a retried-into-quiet snapshot was still marked hot" ;;
esac
test "$(cat "$TAR_COUNT")" -eq 2 || fail "expected exactly one retry, saw $(cat "$TAR_COUNT") attempts"
echo "PASS: a single busy attempt is retried into a clean snapshot"

# --- 8b. only ov.conf moves -> not churn, no pointless retry ----------------
reset_state
seed_tree
run_backup FAKE_TAR_MODE=churn-conf CHEESE_VIKING_QUIESCE_ATTEMPTS=3 \
  || fail "a rewritten ov.conf should not fail the backup"
mapfile -t made < <(archives)
case "${made[0]}" in
  *-hot.tar.gz) fail "a rewritten ov.conf was mistaken for the tree changing" ;;
esac
test "$(cat "$TAR_COUNT")" -eq 1 || fail "the backup retried over a file it does not archive"
echo "PASS: a rewritten ov.conf is not mistaken for the memory tree moving"

# --- 9. off-site leg: configured and working -------------------------------
reset_state
seed_tree
: > "$RUN_DIR/r2.env"
: > "$RUN_DIR/r2-upload.py"
run_backup \
  CHEESE_R2_ENV="$RUN_DIR/r2.env" \
  CHEESE_R2_UPLOAD="$RUN_DIR/r2-upload.py" \
  CHEESE_VENV_PY="$FAKE_BIN/fake-python" \
  CHEESE_VIKING_R2_PREFIX=prod-viking \
  || fail "the off-site leg should not have failed the backup"
grep -q 'prefix=prod-viking' "$R2_CALLS" || fail "the memory archive did not get its own R2 prefix"
mapfile -t made < <(archives)
grep -Fq "${made[0]}" "$R2_CALLS" || fail "the uploaded path is not the published archive"
grep -q 'partial' "$R2_CALLS" && fail "the off-site leg uploaded the partial file"
test -f "$BACKUPS/.viking-last-offsite-success" || fail "off-site marker not written"
echo "PASS: the off-site copy goes to the memory prefix and uploads the published archive"

# --- 10. the r2.env prefix must not win over ours --------------------------
reset_state
seed_tree
printf 'R2_PREFIX=prod-db\n' > "$RUN_DIR/r2.env"
run_backup \
  CHEESE_R2_ENV="$RUN_DIR/r2.env" \
  CHEESE_R2_UPLOAD="$RUN_DIR/r2-upload.py" \
  CHEESE_VENV_PY="$FAKE_BIN/fake-python" \
  CHEESE_VIKING_R2_PREFIX=prod-viking \
  || fail "the off-site leg should not have failed the backup"
grep -q 'prefix=prod-viking' "$R2_CALLS" \
  || fail "r2.env's DB prefix overrode ours — memory archives would land in the db keyspace"
echo "PASS: sourcing r2.env does not drag the memory archive into the DB keyspace"

# --- 11. off-site leg fails -> WARN, local backup still succeeds ------------
reset_state
seed_tree
: > "$RUN_DIR/r2.env"
run_backup \
  CHEESE_R2_ENV="$RUN_DIR/r2.env" \
  CHEESE_R2_UPLOAD="$RUN_DIR/r2-upload.py" \
  CHEESE_VENV_PY="$FAKE_BIN/fake-python" \
  FAKE_R2_EXIT=1 \
  || fail "a failed off-site upload must not fail the whole backup"
grep -q 'WARN' "$OUT" || fail "the off-site failure was not logged as a WARN"
test -n "$(archives)" || fail "the local archive was discarded over an off-site failure"
test -f "$BACKUPS/.viking-last-success" || fail "local success marker missing"
test ! -f "$BACKUPS/.viking-last-offsite-success" || fail "off-site marker written after a failed upload"
echo "PASS: a failed off-site upload is a WARN, not a failed backup"

# --- 12. off-site not configured -> skipped quietly ------------------------
reset_state
seed_tree
run_backup || fail "an unconfigured off-site leg must not fail the backup"
grep -q 'off-site not configured' "$OUT" || fail "the skipped off-site leg was not explained"
test ! -f "$BACKUPS/.viking-last-offsite-success" || fail "off-site marker written with no uploader"
echo "PASS: an unconfigured off-site leg is skipped, not failed"

# --- 13. retention -----------------------------------------------------------
reset_state
seed_tree
: > "$BACKUPS/cheese-viking-20200101-000000.tar.gz"
: > "$BACKUPS/cheese-viking-20200102-000000-hot.tar.gz"
: > "$BACKUPS/cheese-20200101-000000.dump"
touch -d '90 days ago' \
  "$BACKUPS/cheese-viking-20200101-000000.tar.gz" \
  "$BACKUPS/cheese-viking-20200102-000000-hot.tar.gz" \
  "$BACKUPS/cheese-20200101-000000.dump"
run_backup CHEESE_VIKING_RETENTION_DAYS=14 || fail "backup failed during the retention case"
test ! -f "$BACKUPS/cheese-viking-20200101-000000.tar.gz" || fail "an expired archive survived pruning"
test ! -f "$BACKUPS/cheese-viking-20200102-000000-hot.tar.gz" || fail "an expired hot archive survived pruning"
test -f "$BACKUPS/cheese-20200101-000000.dump" || fail "pruning ate a DB dump it does not own"
test -n "$(archives)" || fail "pruning removed the archive just taken"
echo "PASS: retention drops expired memory archives and leaves the DB dumps alone"

echo
echo "ALL PASS"
