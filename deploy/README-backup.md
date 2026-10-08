# Backup & disaster recovery — cheese boxes

The same scripts run on every environment; only `~/ops/r2.env` (the R2 prefix)
and which host they run on differ. Both the dev/test box and the production box
share this setup.

| Box | App host | DB host | R2 prefixes |
|---|---|---|---|
| dev/test | `cheese-dev-env1-app` (192.168.16.5) | `cheese-dev-env1-postgresql` (192.168.16.7) | `db/` |
| **production** (`cheese.ruc.edu.cn`) | `cheese-prod-app` (192.168.16.8) | `cheese-prod-postgresql` (192.168.16.10) | `prod-db/`, `prod-uploads/` |
| **etrip** (`etrip.cn`, Aliyun HK, Dockerized) | `etrip` (8.217.1.152) | in-container `cheese_prod_postgres` + `cheesex-pg` | `etrip/` |

Every box runs Docker Compose. dev and prod share `deploy/compose/`; **etrip has
its own stack** (`cheese-backend-py:main` + a `cheesex` agent stack), so it also
has its own `etrip-backup.sh` that dumps via `docker exec` and tars the uploads
Docker volume. Everything else (R2 upload, verify, off-site) is shared.

## What runs

| Piece | Where | Cadence |
|---|---|---|
| `db-backup.sh` (pg_dump -Fc + verify + prune + off-site) | on the box, `~/ops/db-backup.sh` (`cheese-db-backup.timer`) | hourly, :00 |
| `r2-upload.py` (off-site DB dump copy to R2) | on the box, `~/ops/r2-upload.py` | each DB backup, right after local verify |
| `r2-sync-uploads.py` (incremental mirror of `uploads/` to R2) | on the box, `~/ops/r2-sync-uploads.py` (`cheese-uploads-mirror.timer`) | hourly, :30 |
| `r2-sync-uploads.py` again, over the transcript archives (already `.tar.gz`, so mirrored as they are) | on the box, same script (`cheese-transcripts-mirror.timer`, `UPLOADS_PREFIX=transcripts`) | hourly, :45 |
| Backup freshness alert | `.github/workflows/backup-freshness.yml`, dev runner | daily; local DB > 26h, R2/ uploads markers and DB/tar objects > 6h fail |
| Recovery proof | `.github/workflows/backup-restore-test.yml`, dev runner | weekly; local DB, R2 DB, uploads restore + verify |
| Box-down alert | `.github/workflows/box-uptime.yml`, GitHub-hosted | hourly; fails if the box's runner is offline |

Backups: `~/backups/cheese-<ts>.dump` (compressed custom format), 30-day retention.
Each DB is on a **separate host** from its app box, so the DB dumps already
survive a DB-host loss; the off-site R2 copy survives loss of the app box too.

### uploads/ (STORAGE_TYPE=local user files, e.g. PDF 赛题)

Production stores uploaded files on the app box's disk (`backend/uploads/`, not
in object storage). `r2-sync-uploads.py` mirrors them to R2 `prod-uploads/`
**additively** — only new/changed files upload each run, and nothing is deleted
remotely, so a file removed locally stays backed up. First run mirrors
everything; subsequent runs upload only newly-added files (new 赛题 land off-site
within the hour).

Restore uploads into a **new** directory, then compare every recovered file
with the remote bytes (SHA-256). Run from the repository root with a Python
that has boto3 installed; config is read from `~/ops/r2.env` (override with
`CHEESE_R2_ENV`). Environment values override the file.

```bash
python3 deploy/r2-sync-uploads.py --restore /path/to/new-recovery/uploads \
  --verify /path/to/new-recovery/uploads
# Recheck an existing recovered copy without writing:
python3 deploy/r2-sync-uploads.py --verify /path/to/new-recovery/uploads
```

`UPLOADS_PREFIX` defaults to `prod-uploads`. Recovery refuses an existing
destination, escaping object paths, empty mirrors and truncated downloads.
Verification detects same-size corruption and never writes a mirror success
marker. These operations do not replace the live uploads directory.

The additive writer records `~/backups/.last-uploads-mirror-success` only after
every requested upload and size check succeeds (including no-change runs).
`CHEESE_BACKUP_DIR` or `CHEESE_UPLOADS_MIRROR_MARKER` override that location.
Other prefixes have separate markers: the transcript timer cannot refresh the
uploads marker. No remote deletion or backup failure policy changes.

### Off-site (3-2-1)

Each verified dump is also pushed to Cloudflare R2 bucket `cheese-db-backups`
(key `db/cheese-<ts>.dump`), so a copy survives loss of the app box itself.
Off-site retention is a **30-day R2 bucket lifecycle rule** (server-side, so the
box needs no delete permission). The upload uses a **scoped R2 API token** (Object
Read & Write, that bucket only) stored in `~/ops/r2.env` (chmod 600, NOT in git —
see `r2.env.example`). The off-site leg is best-effort: if it fails, the local
backup still counts as a success and the failure is logged as `WARN` in
`~/backups/backup.log`; `~/backups/.last-offsite-success` records the last good
upload.

Prove the latest R2 DB dump restores into a throwaway container:

```bash
python3 deploy/r2-restore.py
# The prefix can be selected without changing any backup writer:
python3 deploy/r2-restore.py --prefix prod-db
```

The script uses the `r2-upload.py` contract (`R2_ENDPOINT`, `R2_BUCKET`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, optional `R2_PREFIX`, default `db`).
It reads `~/ops/r2.env`, paginates objects, selects the newest `cheese-*.dump`
by LastModified, verifies the downloaded size and runs `db-restore-test.sh`.
The downloaded application data is removed on exit. It never restores into
an existing deployment or live database.

## Restore (run on the box)

```bash
DBURL=$(grep '^DATABASE_URL=' ~/cheese-backend-py/backend/.env | cut -d= -f2- | sed 's#+asyncpg##')
# inspect a dump:
pg_restore --list ~/backups/cheese-<ts>.dump | head
# restore into the live DB (DESTRUCTIVE — drops/recreates objects):
pg_restore --clean --if-exists --no-owner -d "$DBURL" ~/backups/cheese-<ts>.dump
```

The weekly workflow runs three independent drills on the dev runner: the
newest local DB dump, the latest R2 DB dump under the configured prefix, and
restore plus byte verification of the configured uploads mirror. Failures in
one drill do not skip the other R2 drills. All recovery destinations are
throwaway; the live DB and uploads directory are never opened for writing.

`db-restore-test.sh` resolves its default image from the `postgres` service in
`docker-compose.yml` and preloads `pg_search`. It fails if the dump TOC lacks
the extension, if the extension is not restored, on any `pg_restore` error,
on an incomplete schema, or on zero rows across the critical `user`,
`projects`, `topics`, and `blocks` tables. `CHEESE_PG_IMAGE` is an explicit
experiment override, not a second default. Containers publish no ports and
have no network access. Restore logs and TOC remain in `.tmp/restore-tests/`.

The daily freshness workflow checks the local DB success at 26h and, if R2 is
configured, both off-site and uploads success markers at 6h. It also reads R2
objects for the dev DB, prod DB, both etrip DBs and etrip uploads tar; each
must be nonempty and no older than 6h. Checks use exact seconds, not rounded
hours. No R2 config is an explicit skip; partial config, missing markers,
invalid timestamps, stale objects, wrong prefixes or failed R2 reads are red.

The CI changes do **not** install scripts into any box's `~/ops`. The new
mirror writer and `r2-common.py` must be deployed together in a separately
authorized ops rollout for that box's uploads success marker to advance.
Until then, a configured box with no uploads marker is deliberately red.

A deliberately empty fresh installation can exercise the schema-only path with
`CHEESE_RESTORE_ALLOW_EMPTY=1 bash deploy/db-restore-test.sh <dump>`. The script
prints that override in its output; scheduled production drills must not set it.

## Related: the one-off SQL_ASCII → UTF8 rebuild

Not a backup procedure, but it lives next door and uses the same dump/restore
muscles: `cutover-sqlascii-to-utf8.sh` rebuilds a database whose server encoding
is `SQL_ASCII` (which makes PostgreSQL reject non-ASCII `\uXXXX` inside `jsonb`,
issue #233). Dev was rebuilt on 2026-08-16; **production is still `SQL_ASCII`**.
Runbook — including the failure modes and the parts of the script that must not
be "cleaned up" — in [`README-utf8-cutover.md`](README-utf8-cutover.md).

## Gaps / follow-ups

- Production's unchanged, additive uploads mirror has no scheduler heartbeat.
  Old object timestamps are not a freshness signal when no files change.
  The dev runner can prove `prod-uploads` restores, but cannot prove the prod
  mirror timer keeps running until the marker-enabled writer is deployed there.
- The scheduled DB drill restores the configured dev prefix only. `--prefix
  prod-db` provides a manual isolated entry; prod and the two etrip DBs get R2
  age/size checks, not scheduled full schema recovery. etrip's main DB uses
  ParadeDB pg16, unlike dev's pg17. Its uploads tar gets an age/size check, not
  archive extraction verification. No etrip or prod box script changes here.
- These jobs run on dev. The existing separate box uptime check is still needed
  to detect loss of the runner; R2 checks do not replace it.

- **PITR (second-level RPO):** hourly logical dumps are the client-side floor.
  True point-in-time recovery needs WAL archiving **on the PG host**, which
  requires OS access to `192.168.16.7` (SSH is currently closed) and a
  superuser/replication role (the `cheese` role is neither). To enable, on the PG
  host set `wal_level=replica` (already), `archive_mode=on`, an `archive_command`
  to durable storage, and run pgBackRest/Barman for base backups + WAL. Blocked
  until that access is granted.
