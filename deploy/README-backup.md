# Backup & disaster recovery — cheese boxes

The same scripts run on every environment; only `~/ops/r2.env` (the R2 prefix)
and which host they run on differ. Both the dev/test box and the production box
share this setup.

| Box | App host | DB host | R2 prefixes |
|---|---|---|---|
| dev/test | `cheese-dev-env1-app` (192.168.16.5) | `cheese-dev-env1-postgresql` (192.168.16.7) | `db/` |
| **production** (`cheese.ruc.edu.cn`) | `cheese-prod-app` (192.168.16.8) | `cheese-prod-postgresql` (192.168.16.10) | `prod-db/`, `prod-uploads/` |

## What runs

| Piece | Where | Cadence |
|---|---|---|
| `db-backup.sh` (pg_dump -Fc + verify + prune + off-site) | on the box, `~/ops/db-backup.sh` (`cheese-db-backup.timer`) | hourly, :00 |
| `r2-upload.py` (off-site DB dump copy to R2) | on the box, `~/ops/r2-upload.py` | each DB backup, right after local verify |
| `r2-sync-uploads.py` (incremental mirror of `uploads/` to R2) | on the box, `~/ops/r2-sync-uploads.py` (`cheese-uploads-mirror.timer`) | hourly, :30 |
| Backup freshness alert | `.github/workflows/backup-freshness.yml`, on-box runner | daily; fails if last backup > 26h |
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

Restore uploads (run on the box, needs `~/ops/r2.env`):

```bash
set -a; . ~/ops/r2.env; set +a
~/cheese-backend-py/backend/.venv/bin/python - <<'PY'
import os, boto3
s3 = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], region_name="auto")
dst = os.path.expanduser("~/cheese-backend-py/backend/uploads")
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=os.environ["R2_BUCKET"], Prefix="prod-uploads/"):
    for o in page.get("Contents", []):
        rel = o["Key"].split("/", 1)[1]
        p = os.path.join(dst, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        s3.download_file(os.environ["R2_BUCKET"], o["Key"], p)
        print("restored", rel)
PY
```

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
