#!/bin/bash
# cheese dev DB: SQL_ASCII -> UTF8 rebuild (issue #233) — PLAN A.
#
# RUN THIS ON THE DB HOST 192.168.16.7, AS THE postgres OS USER:
#     sudo -u postgres bash cutover-on-db-host.sh
#
# Why this script exists: the earlier plan (cutover.sh, run over TCP from the
# app box 192.168.16.5) CANNOT work. pg_hba.conf on 192.168.16.7 admits exactly
# one pair from 192.168.16.5 — user `cheese`, database `cheese`. Everything
# else is refused at the pg_hba stage, before authentication:
#     cheese  @ postgres/template1/cheese_utf8  -> no pg_hba.conf entry
#     postgres@ anything                        -> no pg_hba.conf entry
# CREATE DATABASE, the restore, and the two renames all require a connection to
# some database other than `cheese`. So no amount of role privilege (CREATEDB)
# unblocks it from the app box; it needs either this script here, or a pg_hba
# change (see PLAN B in cutover.sh).
#
# WHAT IT NEVER DOES: it never drops or truncates anything. The old database is
# renamed aside, never removed. Every failure path leaves the old data intact.
#
# IF SOMETHING KILLS THIS SCRIPT OUTRIGHT (kill -9, the machine reboots, the
# power goes) the recovery handler cannot run, and dev stays frozen — the
# symptom is: FATAL: permission denied for database "cheese", User does not have
# CONNECT privilege. One command fixes it, run as postgres on this host:
#
#     psql -d postgres -c 'GRANT CONNECT ON DATABASE cheese TO PUBLIC, cheese'
#
# Then restart the backend on 192.168.16.5. No data is at risk in that state;
# the database is merely refusing new connections.
#
# BLAST RADIUS (measured on the cluster, 2026-08-16): this cluster holds exactly
# one user database (`cheese`) and two login roles (`cheese`, `postgres`); there
# are no replication slots, no standbys and archive_mode is off. Both the
# CONNECT revoke and the terminate below are scoped to datname='cheese', so
# nothing outside this one database is touched.
#
# OWNERSHIP — the reason this script does NOT pass --no-owner: every one of the
# 102 tables and 65 sequences in public is owned by `cheese`. This script
# restores as the postgres superuser, so with --no-owner every object would come
# back owned by `postgres` and the backend's `cheese` login would hit
# `permission denied for table ...` on its first query. Worse, every check would
# still pass, because they all run as postgres. So the dump keeps its
# ALTER ... OWNER TO / GRANT statements (a superuser can apply them), and step 8
# verifies ownership and per-object privileges explicitly.
#
# ---------------------------------------------------------------------------
# REUSING THIS ON PRODUCTION — what to change, and what NOT to.
#
# This file is archived verbatim as it ran on dev on 2026-08-16. Its value is
# that it was executed end-to-end against real data and then survived five
# injected failures (see deploy/README-utf8-cutover.md). Anything you edit is,
# by definition, no longer covered by that evidence — so edit only what the list
# below names, and re-read the "do not touch" section of the runbook first.
#
#   1. HOST / USER. Production's DB host is 192.168.16.10, reached from the prod
#      app box 192.168.16.8 (NOT 192.168.16.5 — that is the dev app box, and it
#      cannot reach the prod DB host at all). Run as the postgres OS user there.
#      Occurrences to update: the RUN THIS ON THE DB HOST line above, the
#      pg_hba paragraph, and the two "NEXT, on 192.168.16.5" lines near the end.
#   2. RESTART COMMAND. The trailing hint says `docker restart cheese-backend-1`,
#      which is the dev box's container. Production is bare-metal systemd —
#      `sudo systemctl restart cheese-backend-py` — see docs/infrastructure.md.
#   3. DATABASE NAMES. `cheese`, `cheese_utf8`, `cheese_sqlascii_old` are
#      hardcoded on purpose (a parameterised version would not be the version
#      that was tested). If prod's live database is not called `cheese`, change
#      all three consistently and re-read every psql line before running.
#   4. WORK DIR. `WORK` defaults to /var/lib/postgresql/cheese-utf8-rebuild.
#      Override with the WORK env var if that filesystem is tight.
#   5. DISK. Preflight demands > 2048 MB free on BOTH the work dir and PGDATA.
#      As of 2026-08-16 the prod box had ~14 GB free — check again before you
#      start; a full PGDATA filesystem PANICs the whole instance.
#   6. BASELINE FACTS. The "102 tables / 65 sequences" and the `blocks`
#      fingerprint are dev's numbers, quoted in comments only — the script reads
#      the real values at run time. But the step-2 sanity floor (`-ge 50`
#      tables) is a hardcoded guess at cluster shape; confirm prod actually has
#      more than 50 tables in public, or that check will abort a good run.
#   7. #232 STOPGAP. Confirm the release running on prod contains the
#      `_json_dumps_utf8` fix (backend/app/core/db.py). Without it, prod keeps
#      500'ing on non-ASCII jsonb right up until this cutover completes.
# ---------------------------------------------------------------------------
set -euo pipefail

TS=$(date +%Y%m%d-%H%M%S)
WORK=${WORK:-/var/lib/postgresql/cheese-utf8-rebuild}
mkdir -p "$WORK"
cd "$WORK"
LOG=cutover-$TS.log
# The `trap ''` inside the process substitution is load-bearing. Ctrl-C reaches
# the whole process group, so a plain `tee` dies with the script — and then the
# recovery handler below writes into a broken pipe, takes SIGPIPE, and dies
# before it can un-freeze the database. Measured 2026-08-16: without this, an
# interrupted run left dev stuck on "permission denied for database cheese".
exec > >(trap '' INT TERM HUP QUIT PIPE; exec tee -a "$LOG") 2>&1

DUMP=cheese-final-$TS.sql
say() { echo "[$(date +%T)] $*"; }
die() { say "ABORT: $*"; exit 1; }
psq() { psql -v ON_ERROR_STOP=1 "$@"; }

# --- safety net -------------------------------------------------------------
# Once writes are frozen (step 2), any abort must hand the database back, or dev
# stays hard-down with no obvious cause. The handler does not trust its own
# bookkeeping about how far we got — it asks the catalog what exists right now,
# which also covers the nastiest case: the first rename succeeding and the
# second failing, leaving no database called `cheese` at all.
FROZEN=0
on_signal() {
  trap '' INT TERM HUP        # a second Ctrl-C must not interrupt the recovery
  echo; say "!! interrupted by a signal (Ctrl-C, or the ssh session dropped)"
  exit 130
}
on_exit() {
  local rc=$?
  set +e
  trap '' INT TERM HUP        # recovery below runs to completion, uninterruptible
  [ "$rc" = 0 ] && exit 0
  echo
  say "!! FAILED (exit $rc)"
  local hc ho hu
  hc=$(psql -tAc "select count(*) from pg_database where datname='cheese'" -d postgres 2>/dev/null || echo "?")
  ho=$(psql -tAc "select count(*) from pg_database where datname='cheese_sqlascii_old'" -d postgres 2>/dev/null || echo "?")
  hu=$(psql -tAc "select count(*) from pg_database where datname='cheese_utf8'" -d postgres 2>/dev/null || echo "?")
  say "   catalog right now: cheese=$hc  cheese_sqlascii_old=$ho  cheese_utf8=$hu"

  if [ "$hc" = "?" ]; then
    say "   !! cannot reach the server to assess state — check it by hand before anything else"
  elif [ "$hc" = 0 ] && [ "$ho" = 1 ]; then
    say "   !! the rename got half-way: there is NO database named cheese right now."
    say "   rolling back to the original database..."
    psql -d postgres -c "ALTER DATABASE cheese_sqlascii_old RENAME TO cheese"
    psql -d postgres -c "GRANT CONNECT ON DATABASE cheese TO PUBLIC, cheese"
    say "   rolled back — dev is on the OLD SQL_ASCII database again."
  elif [ "$hc" = 1 ] && [ "$ho" = 1 ]; then
    say "   the swap already completed — the live DB is the NEW UTF8 one, a later step failed."
    say "   it is serving; roll back only if you have a reason:"
    say "     ALTER DATABASE cheese RENAME TO cheese_utf8;"
    say "     ALTER DATABASE cheese_sqlascii_old RENAME TO cheese;"
  elif [ "$FROZEN" = 1 ]; then
    say "   old DB untouched; restoring CONNECT so dev comes back up"
    psql -d postgres -c "GRANT CONNECT ON DATABASE cheese TO PUBLIC, cheese" \
      || say "   !! could not restore CONNECT — run it by hand, dev is down until you do"
    [ "$hu" = 1 ] && say "   leftover cheese_utf8 exists; drop it before rerunning: DROP DATABASE cheese_utf8;"
  else
    say "   nothing was changed."
  fi
  say "   next, on 192.168.16.5:  docker restart cheese-backend-1"
  say "   log: $WORK/$LOG"
  exit "$rc"
}
trap on_exit EXIT
# Without these, a Ctrl-C or a dropped ssh session kills bash outright, the EXIT
# trap never runs, and the database stays frozen with nobody knowing why.
# on_signal exits non-zero so the EXIT handler above takes the recovery path.
trap on_signal INT TERM HUP

# Two concurrent runs would be genuinely destructive: the second one's failure
# handler would GRANT CONNECT back while the first is still dumping, letting the
# backend write rows that the dump has already passed. Serialize hard.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$WORK/.cutover.lock"
  flock -n 9 || { echo "another cutover run is already in progress — refusing to start"; exit 1; }
else
  echo "WARNING: flock not available; make sure no second copy of this script is running"
fi

say "step 0: preflight (read-only — nothing is changed until this passes)"
psq -d postgres -tAc "select 1" >/dev/null
say "  connected as $(psq -d postgres -tAc 'select current_user')"
psq -d postgres -tAc "select usesuper from pg_user where usename=current_user" | grep -qx t \
  || die "not a superuser — run as the postgres OS user"
CUR=$(psq -d postgres -tAc "select pg_encoding_to_char(encoding) from pg_database where datname='cheese'")
say "  current encoding of cheese = $CUR"
[ "$CUR" = "SQL_ASCII" ] || die "expected SQL_ASCII, got $CUR — already rebuilt?"
psq -d postgres -tAc "select count(*) from pg_database where datname='cheese_utf8'" | grep -qx 0 \
  || die "cheese_utf8 already exists — inspect and drop it before rerunning"
psq -d postgres -tAc "select count(*) from pg_database where datname='cheese_sqlascii_old'" | grep -qx 0 \
  || die "cheese_sqlascii_old already exists — a previous run got through the swap"

# Disk is the one thing that can genuinely crash the server: if PGDATA's
# filesystem fills up mid-restore, Postgres PANICs on the WAL write and the
# whole instance goes down. Budget = 46 MB dump + ~65 MB new DB + WAL churn
# (max_wal_size is 1 GB on this cluster), so demand 2 GB with room to spare, on
# BOTH the work dir and PGDATA (they are usually the same filesystem, but check).
PGDATA_DIR=$(psq -d postgres -tAc "show data_directory")
for d in "$WORK" "$PGDATA_DIR"; do
  free_kb=$(df -Pk "$d" | awk 'NR==2{print $4}')
  say "  free space on $d: $((free_kb/1024)) MB"
  [ "$free_kb" -gt 2097152 ] || die "need > 2048 MB free on $d (dump + new DB + WAL); a full disk PANICs the server"
done

# Baseline facts about the old DB, captured before anything moves.
OWNERS_OLD=$(psq -d cheese -tAc "
  select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind in ('r','S','v','m','p')
    and pg_get_userbyid(c.relowner) <> 'cheese'")
say "  objects in public NOT owned by cheese, in the old DB: $OWNERS_OLD"
[ "$OWNERS_OLD" = 0 ] || die "old DB has mixed ownership — this script assumes all-cheese"

HAS_BLOCKS=$(psq -d cheese -tAc "select count(*) from pg_tables where schemaname='public' and tablename='blocks'")
[ "$HAS_BLOCKS" = 1 ] || say "  WARNING: table 'blocks' not found — the content fingerprint check will be skipped"
say "  preflight passed"

# ORDER MATTERS FROM HERE ON. Every baseline below (row counts, fingerprint) is
# compared against the restored copy, so it must be taken from a database that
# can no longer change. dev's backend writes constantly — taking the counts
# before the freeze guarantees they drift from the dump and the script aborts on
# a mismatch that was never a real problem, costing an outage for nothing.
say "step 1: freeze writes (revoke CONNECT + terminate sessions on 'cheese' only)"
psq -d postgres -c "REVOKE CONNECT ON DATABASE cheese FROM PUBLIC, cheese"
FROZEN=1
psq -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='cheese' AND pid <> pg_backend_pid()" >/dev/null
say "  writes frozen — the backend on 192.168.16.5 errors from now until it is restarted"

say "step 2: capture the baseline from the frozen DB"
psq -d cheese -tAc "
  select relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where c.relkind='r' and n.nspname='public' order by 1" > tables-$TS.txt
NTAB=$(wc -l < tables-$TS.txt)
say "  tables in public: $NTAB"
# Guard against the silent-pass path: if this listing came back empty, step 7
# would loop zero times and cheerfully report "all tables match".
[ "$NTAB" -ge 50 ] || die "only $NTAB tables listed — expected ~102; refusing to proceed on a bad listing"
: > counts-old-$TS.txt
while read -r t; do
  printf '%s\t%s\n' "$t" "$(psq -d cheese -tAc "select count(*) from public.\"$t\"")" >> counts-old-$TS.txt
done < tables-$TS.txt
awk -F'\t' '$2 !~ /^[0-9]+$/ {print "  bad count line: " $0; bad=1} END{exit bad?1:0}' counts-old-$TS.txt \
  || die "some row counts came back empty — a query failed, do not proceed"
say "  captured $(wc -l < counts-old-$TS.txt) table counts"
if [ "$HAS_BLOCKS" = 1 ]; then
  # Content fingerprint: md5() hashes the on-disk BYTES of the text, so the same
  # bytes must produce the same digest under SQL_ASCII and under UTF8. If the
  # restore mangled a single character anywhere in blocks, this changes.
  FP_OLD=$(psq -d cheese -tAc "select md5(string_agg(md5(coalesce(content,'')), '' order by id)) from blocks")
  # '[\x80-\xFF]' is a byte-range test that only parses under SQL_ASCII; the new
  # DB gets the encoding-aware equivalent (octet_length <> length) in step 9.
  MB_OLD=$(psq -d cheese -tAc "select count(*) from blocks where content ~ '[\x80-\xFF]'")
  say "  blocks content fingerprint: $FP_OLD"
  say "  blocks rows containing non-ASCII bytes: $MB_OLD"
  [ "$MB_OLD" -gt 0 ] || die "no non-ASCII rows found in blocks — that contradicts every earlier measurement, stop and look"
else
  FP_OLD=""; MB_OLD=""
fi

say "step 3: final dump (owners + grants KEPT on purpose — see header)"
pg_dump -d cheese -Fp -f "$DUMP"
say "  dump size: $(du -h "$DUMP" | cut -f1)"
grep -q "^COPY public\." "$DUMP" || die "dump has no COPY data sections — refusing to restore from it"
if command -v iconv >/dev/null 2>&1; then
  iconv -f UTF-8 -t UTF-8 <"$DUMP" >/dev/null 2>&1 \
    && say "  dump is clean UTF-8 (no historical dirty bytes)" \
    || die "dump contains bytes that are not valid UTF-8 — do NOT proceed, investigate"
else
  say "  (iconv absent, skipping the UTF-8 byte check — the 08-12 rehearsal dump was clean)"
fi

say "step 4: create cheese_utf8 (UTF8)"
# PG 17's builtin locale provider gives Unicode-correct lower()/upper() with
# deterministic C-like ordering and no dependency on an OS locale. Fall through
# the libc spellings; the last resort (plain C) is loud, because it silently
# breaks upper()/lower() on non-ASCII.
mkdb() { psq -d postgres -c "CREATE DATABASE cheese_utf8 OWNER cheese ENCODING 'UTF8' TEMPLATE template0 $1"; }
if mkdb "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8'" 2>/dev/null; then
  say "  created with locale_provider=builtin, C.UTF-8"
elif mkdb "LC_COLLATE 'C.utf8' LC_CTYPE 'C.utf8'" 2>/dev/null; then
  say "  created with libc C.utf8"
elif mkdb "LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8'" 2>/dev/null; then
  say "  created with libc C.UTF-8"
else
  say "  WARNING: no C.UTF-8 locale available, falling back to plain C"
  say "           (upper()/lower() will not work on non-ASCII — acceptable here, but note it)"
  mkdb "LC_COLLATE 'C' LC_CTYPE 'C'"
fi
psq -d postgres -tAc "select pg_encoding_to_char(encoding), datcollate, datctype from pg_database where datname='cheese_utf8'"

say "step 5: restore"
# Drop whatever client_encoding the dump declares (it says SQL_ASCII) and force
# UTF8 ourselves, so this does not hinge on pg_dump's exact wording. The SET is
# injected ahead of the dump's \restrict line, where plain SQL is still allowed.
export PGCLIENTENCODING=UTF8
{ echo "SET client_encoding = 'UTF8';"; sed "/^SET client_encoding = /d" "$DUMP"; } \
  | psq -q -d cheese_utf8 >/dev/null
say "  restore finished without error"

say "step 6: confirm the new DB really is UTF8"
NEWENC=$(psq -d postgres -tAc "select pg_encoding_to_char(encoding) from pg_database where datname='cheese_utf8'")
[ "$NEWENC" = "UTF8" ] || die "cheese_utf8 came out as $NEWENC"
say "  encoding = $NEWENC"

say "step 7: verify per-table row counts"
FAIL=0
while IFS=$'\t' read -r t n; do
  m=$(psq -d cheese_utf8 -tAc "select count(*) from public.\"$t\"")
  if [ "$n" != "$m" ]; then echo "  MISMATCH $t: old=$n new=$m"; FAIL=1; fi
done < counts-old-$TS.txt
[ "$FAIL" = 0 ] || die "row counts differ — old DB untouched, investigate before swapping"
say "  all $(wc -l < counts-old-$TS.txt) tables match"

say "step 8: verify the cheese role can actually use the new DB"
# This is the check a --no-owner restore would have failed. Everything above
# runs as postgres and would have passed either way.
BAD_OWNER=$(psq -d cheese_utf8 -tAc "
  select count(*) from pg_class c join pg_namespace n on n.oid=c.relnamespace
  where n.nspname='public' and c.relkind in ('r','S','v','m','p')
    and pg_get_userbyid(c.relowner) <> 'cheese'")
say "  objects in public NOT owned by cheese: $BAD_OWNER (must be 0)"
[ "$BAD_OWNER" = 0 ] || {
  psq -d cheese_utf8 -tAc "
    select c.relkind, c.relname, pg_get_userbyid(c.relowner) from pg_class c
    join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relkind in ('r','S','v','m','p')
      and pg_get_userbyid(c.relowner) <> 'cheese' limit 20"
  die "ownership did not carry over — the backend would get permission denied"; }

NO_PRIV=$(psq -d cheese_utf8 -tAc "
  select count(*) from pg_tables t where t.schemaname='public' and not (
    has_table_privilege('cheese', format('%I.%I',t.schemaname,t.tablename), 'SELECT') and
    has_table_privilege('cheese', format('%I.%I',t.schemaname,t.tablename), 'INSERT') and
    has_table_privilege('cheese', format('%I.%I',t.schemaname,t.tablename), 'UPDATE') and
    has_table_privilege('cheese', format('%I.%I',t.schemaname,t.tablename), 'DELETE'))")
say "  tables cheese cannot fully read/write: $NO_PRIV (must be 0)"
[ "$NO_PRIV" = 0 ] || die "table privileges missing"

# OFFSET 0 is load-bearing: without it the planner hoists has_sequence_privilege()
# above the relkind='S' filter and errors with "... is not a sequence".
NO_SEQ=$(psq -d cheese_utf8 -tAc "
  select count(*) from (
    select c.oid from pg_class c join pg_namespace n on n.oid=c.relnamespace
    where n.nspname='public' and c.relkind='S' offset 0
  ) s where not has_sequence_privilege('cheese', s.oid, 'USAGE')")
say "  sequences cheese cannot use: $NO_SEQ (must be 0)"
[ "$NO_SEQ" = 0 ] || die "sequence privileges missing — inserts would fail"

# alembic needs CREATE on the schema, not just USAGE.
psq -d cheese_utf8 -tAc "select has_schema_privilege('cheese','public','USAGE'), has_schema_privilege('cheese','public','CREATE')" \
  | grep -qx 't|t' || die "cheese lacks USAGE/CREATE on schema public — migrations would fail"
say "  schema public: cheese has USAGE + CREATE"

say "step 9: verify the text itself survived byte-for-byte"
if [ -n "$FP_OLD" ]; then
  FP_NEW=$(psq -d cheese_utf8 -tAc "select md5(string_agg(md5(coalesce(content,'')), '' order by id)) from blocks")
  say "  blocks fingerprint old=$FP_OLD"
  say "                     new=$FP_NEW"
  [ "$FP_OLD" = "$FP_NEW" ] || die "blocks content changed during the rebuild — do NOT swap"
  MB_NEW=$(psq -d cheese_utf8 -tAc "select count(*) from blocks where octet_length(content) <> length(content)")
  say "  rows with multibyte text: old(bytes)=$MB_OLD new(chars)=$MB_NEW (must match)"
  [ "$MB_OLD" = "$MB_NEW" ] || die "multibyte row count changed — the encoding conversion is wrong"
  psq -d cheese_utf8 -tAc "select left(content,60) from blocks where octet_length(content) <> length(content) limit 3"
fi
# The write that started this whole issue: Chinese inside jsonb, as the app's role.
psq -d cheese_utf8 -c "SET ROLE cheese" \
                  -c "create temp table _t (j jsonb)" \
                  -c "insert into _t values ('{\"name\":\"马霄宇\"}'::jsonb)" >/dev/null
say "  Chinese jsonb insert as role cheese: OK"

say "step 10: swap names"
psq -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('cheese','cheese_utf8') AND pid <> pg_backend_pid()" >/dev/null
psq -d postgres -c "ALTER DATABASE cheese RENAME TO cheese_sqlascii_old"
psq -d postgres -c "ALTER DATABASE cheese_utf8 RENAME TO cheese"
psq -d postgres -c "GRANT CONNECT ON DATABASE cheese TO PUBLIC, cheese"
psq -d postgres -tAc "select datname, pg_encoding_to_char(encoding), datcollate from pg_database where datname like 'cheese%'"

say "DONE."
say "  old data kept intact as cheese_sqlascii_old — nothing was dropped."
say "  drop it only after a few days of clean running:  DROP DATABASE cheese_sqlascii_old;"
say "  note: the old DB still has CONNECT revoked for cheese — deliberate, so"
say "        nothing silently reconnects to the stale copy."
say "  NEXT, on 192.168.16.5:  docker restart cheese-backend-1"
say "  log: $WORK/$LOG"
