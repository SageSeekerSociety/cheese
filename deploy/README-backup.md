# Backup & disaster recovery — cheese boxes

The same scripts run on every environment; only `~/ops/r2.env` (the R2 prefix)
and which host they run on differ. Both the dev/test box and the production box
share this setup.

| Box | App host | DB host | R2 prefixes |
|---|---|---|---|
| dev/test | `cheese-dev-env1-app` (192.168.16.5) | `cheese-dev-env1-postgresql` (192.168.16.7) | `db/` |
| **production** (`cheese.ruc.edu.cn`) | `cheese-prod-app` (192.168.16.8) | `cheese-prod-postgresql` (192.168.16.10) | `prod-db/`, `prod-uploads/` |
| **etrip** (`etrip.cn`, Aliyun HK, Dockerized) | `etrip` (8.217.1.152) | in-container `cheese_prod_postgres` + `cheesex-pg` | `etrip/` |

The dev and prod boxes are bare-metal (systemd + local `.venv`); **etrip is a Docker
Compose stack** (`cheese-backend-py:main` + a `cheesex` agent stack), so it has its
own `etrip-backup.sh` that dumps via `docker exec` and tars the uploads Docker
volume. Everything else (R2 upload, verify, off-site) is shared.

## What runs

| Piece | Where | Cadence |
|---|---|---|
| `db-backup.sh` (pg_dump -Fc + verify + prune + off-site) | on the box, `~/ops/db-backup.sh` (`cheese-db-backup.timer`) | hourly, :00 |
| `r2-upload.py` (off-site DB dump copy to R2) | on the box, `~/ops/r2-upload.py` | each DB backup, right after local verify |
| `r2-sync-uploads.py` (incremental mirror of `uploads/` to R2) | on the box, `~/ops/r2-sync-uploads.py` (`cheese-uploads-mirror.timer`) | hourly, :30 |
| `viking-backup.sh` (tar of the openviking memory tree + verify + prune + off-site) | on the box, `~/ops/viking-backup.sh` (`cheese-viking-backup.timer`) | every 6h, :15 |
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

### The openviking memory tree (`VIKING_HOST_PATH`)

On `MEMORY_BACKEND=openviking` the host directory bind-mounted at `/data/viking`
(default `/home/nictheboy/cheese-viking`) **is a database** — the AGFS store plus
its vector index. It is not in Postgres, not in the image, and not in the uploads
mirror, so `viking-backup.sh` is the only thing standing between a box rebuild
and every memory the platform has.

Archives: `~/backups/cheese-viking-<ts>.tar.gz`, 14-day retention. Each run
stores a **full** copy — there is no incremental leg — which is why the cadence
is 6-hourly rather than hourly: the disk cost is cadence × retention × tree size,
on a box that already needs a disk-pressure guard. Turn the knobs with
`CHEESE_VIKING_RETENTION_DAYS` and the timer's `OnCalendar`. Off-site copies go
to R2 under `viking/` (dev) / `prod-viking/` (prod), set per box by
`CHEESE_VIKING_R2_PREFIX` in the systemd unit, and expire under the same
bucket-wide 30-day lifecycle rule as the DB dumps.

`ov.conf` is excluded on purpose: it holds the plaintext model API keys and the
backend rewrites it from settings on every start, so archiving it would ship
secrets off-site to buy nothing. Restoring without it is correct.

**Hot backups, and what a torn one costs.** There is no way to snapshot this tree
atomically from outside the process that owns it, and stopping the backend to
take a backup is not on the table. So the archive is taken live and the tree is
fingerprinted (path, size, mtime) before and after `tar`:

- Nothing moved → the snapshot is coherent → `cheese-viking-<ts>.tar.gz`.
- The backend wrote during *every* attempt (3 by default) → the archive is still
  kept, because a possibly-torn copy beats no copy, but it lands as
  **`cheese-viking-<ts>-hot.tar.gz`** and the run logs `WARN: no quiet window`.

A `-hot` archive can hold an AGFS file and a vector-index segment written at
different instants: expect it to restore and mostly work, with the newest
memories possibly unsearchable (index missing an entry the store has, or the
reverse). **Prefer a plain `.tar.gz` when one is available**; reach for a `-hot`
one only when it is all there is. In practice memory writes are bursty and idle
most of the time, so quiet windows are the normal case — if `-hot` becomes the
norm on a box, that is the signal to move to a filesystem-level snapshot rather
than to trust the tars.

Restore (run on the box, as the box user):

```bash
sudo systemctl stop cheese-viking-backup.timer      # don't race a backup
docker stop cheese-backend-1                        # the backend owns this tree
VIKING=/home/nictheboy/cheese-viking
mv "$VIKING" "$VIKING.broken-$(date +%s)"           # keep the wreck to look at
tar -xzf ~/backups/cheese-viking-<ts>.tar.gz -C "$(dirname "$VIKING")"
# The container runs as uid 1000. Wrong owner here means the backend cannot
# write and the memory backend silently stops recording anything.
sudo chown -R 1000:1000 "$VIKING"
docker start cheese-backend-1
sudo systemctl start cheese-viking-backup.timer
```

The tar stores the directory under its own basename, so extracting into the
parent recreates `cheese-viking/` in place. `ov.conf` is regenerated by the
backend on first use after the restart — do not hand-write one.

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

The weekly workflow runs `deploy/db-restore-test.sh` against the newest dump in
a throwaway container. The drill fails on any `pg_restore` error, an incomplete
schema, or zero rows across the critical `user`, `projects`, `topics`, and
`blocks` tables. It retains the restore log under `tmp/restore-tests/` and
prints the path and tail when the restore fails.

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

- **No freshness tripwire on the memory backup.** `backup-freshness.yml` reads
  `~/backups/.last-success` (the DB marker) only. `viking-backup.sh` writes
  `~/backups/.viking-last-success`, but nothing alerts on it going stale. Adding
  that check is only meaningful once a box actually runs
  `MEMORY_BACKEND=openviking`: on the `db` backend the tree is empty, the run
  correctly skips, and the marker never appears — so the check would be red
  everywhere for a reason that is not a failure.

- **PITR (second-level RPO):** hourly logical dumps are the client-side floor.
  True point-in-time recovery needs WAL archiving **on the PG host**, which
  requires OS access to `192.168.16.7` (SSH is currently closed) and a
  superuser/replication role (the `cheese` role is neither). To enable, on the PG
  host set `wal_level=replica` (already), `archive_mode=on`, an `archive_command`
  to durable storage, and run pgBackRest/Barman for base backups + WAL. Blocked
  until that access is granted.
