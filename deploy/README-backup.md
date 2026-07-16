# Backup & disaster recovery — cheese dev/test box

## What runs

| Piece | Where | Cadence |
|---|---|---|
| `db-backup.sh` (pg_dump -Fc + verify + prune + off-site) | on the box, `~/ops/db-backup.sh` | hourly (`cheese-db-backup.timer`) |
| `r2-upload.py` (off-site copy to Cloudflare R2) | on the box, `~/ops/r2-upload.py` | each backup, right after local verify |
| Backup freshness alert | `.github/workflows/backup-freshness.yml`, on-box runner | daily; fails if last backup > 26h |
| Box-down alert | `.github/workflows/box-uptime.yml`, GitHub-hosted | hourly; fails if the box's runner is offline |

Backups: `~/backups/cheese-<ts>.dump` (compressed custom format), 30-day retention.
The DB (`cheese-dev-env1-postgresql`, 192.168.16.7) is a **separate host**, so these
dumps already survive a DB-host loss.

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

Restore from R2 (run on the box, needs `~/ops/r2.env`):

```bash
set -a; . ~/ops/r2.env; set +a
~/cheese-backend-py/backend/.venv/bin/python - <<'PY'
import os, boto3
s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
for o in s3.list_objects_v2(Bucket=os.environ["R2_BUCKET"], Prefix="db/").get("Contents", []):
    print(o["LastModified"], o["Size"], o["Key"])
PY
# then download the chosen key and pg_restore as below.
```

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

- **PITR (second-level RPO):** hourly logical dumps are the client-side floor.
  True point-in-time recovery needs WAL archiving **on the PG host**, which
  requires OS access to `192.168.16.7` (SSH is currently closed) and a
  superuser/replication role (the `cheese` role is neither). To enable, on the PG
  host set `wal_level=replica` (already), `archive_mode=on`, an `archive_command`
  to durable storage, and run pgBackRest/Barman for base backups + WAL. Blocked
  until that access is granted.
