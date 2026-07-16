# Backup & disaster recovery — cheese dev/test box

## What runs

| Piece | Where | Cadence |
|---|---|---|
| `db-backup.sh` (pg_dump -Fc + verify + prune) | on the box, `~/ops/db-backup.sh` | hourly (`cheese-db-backup.timer`) |
| Backup freshness alert | `.github/workflows/backup-freshness.yml`, on-box runner | daily; fails if last backup > 26h |
| Box-down alert | `.github/workflows/box-uptime.yml`, GitHub-hosted | hourly; fails if the box's runner is offline |

Backups: `~/backups/cheese-<ts>.dump` (compressed custom format), 30-day retention.
The DB (`cheese-dev-env1-postgresql`, 192.168.16.7) is a **separate host**, so these
dumps already survive a DB-host loss.

## Restore (run on the box)

```bash
DBURL=$(grep '^DATABASE_URL=' ~/cheese-backend-py/backend/.env | cut -d= -f2- | sed 's#+asyncpg##')
# inspect a dump:
pg_restore --list ~/backups/cheese-<ts>.dump | head
# restore into the live DB (DESTRUCTIVE — drops/recreates objects):
pg_restore --clean --if-exists --no-owner -d "$DBURL" ~/backups/cheese-<ts>.dump
```

Test-restore into a scratch DB periodically — an untested backup is not a backup.

## Gaps / follow-ups

- **Off-site (3-2-1):** dumps live only on the app box. Provision an S3 bucket
  (the `.env` `S3_*` values are empty placeholders) or a remote host, then add an
  upload step to `db-backup.sh`.
- **PITR (second-level RPO):** hourly logical dumps are the client-side floor.
  True point-in-time recovery needs WAL archiving **on the PG host**, which
  requires OS access to `192.168.16.7` (SSH is currently closed) and a
  superuser/replication role (the `cheese` role is neither). To enable, on the PG
  host set `wal_level=replica` (already), `archive_mode=on`, an `archive_command`
  to durable storage, and run pgBackRest/Barman for base backups + WAL. Blocked
  until that access is granted.
