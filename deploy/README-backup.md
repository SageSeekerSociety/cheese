# Backup & disaster recovery — cheese boxes

dev 和 production 使用 `db-backup.sh`，etrip 使用独立备份脚本。dev 发布流水线将备份脚本安装到 `~/ops`；production 和 etrip 保持各自已部署的脚本及上传频率。

| Box | App host | DB host | R2 prefixes |
|---|---|---|---|
| dev/test | `cheese-dev-env1-app` (192.168.16.5) | `cheese-dev-env1-postgresql` (192.168.16.7) | `db/`, `dev-uploads/` |
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
| `r2-upload.py`（数据库备份上传 R2） | `~/ops/r2-upload.py` | dev：距上次成功至少 6 小时；prod／etrip：每次数据库备份 |
| `r2-prune-dumps.py`（本地分层保留） | dev 的 `~/ops/r2-prune-dumps.py`，由 `~/ops/backup.env` 中的 `CHEESE_BACKUP_HOURLY_DAYS` 启用 | 每次数据库备份结束时 |
| `r2-sync-uploads.py` (incremental mirror of `uploads/` to R2) | on the box, `~/ops/r2-sync-uploads.py` (`cheese-uploads-mirror.timer`); on dev it mirrors `/home/nictheboy/shared/uploads` to `dev-uploads/` (see "The dev mirror" below) | hourly, :30 |
| `r2-sync-uploads.py` again, over the old transcript archives (already `.tar.gz`, so mirrored as they are; nothing writes new ones there: room cleanup uploads archived rooms' transcripts to the private bucket under `transcripts/rooms/`, see [`README-room-cleanup.md`](README-room-cleanup.md)) | on the box, same script (`cheese-transcripts-mirror.timer`, `UPLOADS_PREFIX=transcripts`) | hourly, :45 |
| 备份新鲜度告警 | `.github/workflows/backup-freshness.yml`，dev runner | 每天；本地数据库超过 26 小时、dev R2 数据库超过上传间隔加 2 小时（默认 8 小时）、其他 R2 标记或对象超过 6 小时则失败 |
| Recovery proof | `.github/workflows/backup-restore-test.yml`, dev runner | weekly; local DB, R2 DB, uploads restore + verify |
| `gateway-db-backup.sh` (LLM gateway DB: pg_dump -Fc via `docker exec`, verify, keep newest 8, then `r2-upload.py` to `db/gateway-litellm-<ts>.dump`) | `.github/workflows/gateway-db-backup.yml`, dev runner; dumps in `~/backups/gateway/`, restore in [`gateway/README.md`](gateway/README.md#backups) | every 6h, :23 UTC |
| Box-down alert | `.github/workflows/box-uptime.yml`, GitHub-hosted | hourly; fails if the box's runner is offline |

备份写入 `~/backups/cheese-<ts>.dump`，使用压缩的 PostgreSQL 自定义格式，保留 30 天。
`~/ops/backup.env` 设置了 `CHEESE_BACKUP_HOURLY_DAYS` 的机器（dev 为 `7`）使用
`r2-prune-dumps.py --local-only`：最近 7 天保留全部小时备份，之后每天保留最后一份通过
`pg_restore --list` 校验的备份，直到 30 天。其余文件按本地规则删除，包括有意未上传的小时备份。
此模式不读写 R2；某天没有可读备份时，该天文件保留至原有 30 天清理边界，并记录警告。
普通 30 天清理始终在其后执行；分层清理程序因配置错误或脚本缺失等原因失败时，服务以非零状态退出。
没有此配置文件的 prod 保持普通 30 天清理。清理器要求 `~/ops` 中存在配套的 `r2-common.py`。
Each DB is on a **separate host** from its app box, so the DB dumps already
survive a DB-host loss; the off-site R2 copy survives loss of the app box too.

### uploads/ (STORAGE_TYPE=local user files, e.g. PDF 赛题)

Two settings decide where a user's bytes go, and they are separate:
`STORAGE_TYPE` covers uploads served under `/uploads` (avatars, attachments,
赛题 PDFs), written to `STORAGE_LOCAL_PATH` when it is `local`; `FILES_STORE`
covers project files (the library, `app/core/blob_store.py`), which go to the
files bucket when it is `bucket`. Dev runs `STORAGE_TYPE=local` and
`FILES_STORE=bucket` (as of 2026-10-21): its uploads are on the box's disk, its
new library files are in the files bucket. Nothing mirrors the files bucket
(off-site copy of it is open under #2114), and switching `FILES_STORE` moved no
existing bytes (#4545).

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

#### The dev mirror

On dev the uploads are where the compose file mounts them into the backend,
`/home/nictheboy/shared/uploads` (`UPLOADS_HOST_PATH`, mounted at
`/data/uploads`), not `backend/uploads/`, which is the script's default source
and holds only legacy files on dev. `prod-uploads/` in the shared bucket is
production's, so dev mirrors to its own prefix. Installed 2026-10-21 as
`/etc/systemd/system/cheese-uploads-mirror.service` and `.timer`:

- service: oneshot, `User=nictheboy`, `Environment=UPLOADS_PREFIX=dev-uploads`,
  `ExecStart=/home/nictheboy/cheese-backend-py/backend/.venv/bin/python /home/nictheboy/ops/r2-sync-uploads.py /home/nictheboy/shared/uploads`
- timer: hourly at :30, `RandomizedDelaySec=120`, `Persistent=true`

Its first run uploaded 565 files (92.5 MB) to `r2://cheese-db-backups/dev-uploads/`
with no failures.

The freshness check's receipt reads the service's `ExecMainExitTimestamp`, and
a oneshot service run by hand before its timer is enabled is unloaded once it
exits and loses that timestamp. So the receipt works only from the first run
after the timer is enabled.

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

dev 在 `~/backups/.last-offsite-success` 距今至少达到
`CHEESE_BACKUP_OFFSITE_INTERVAL_HOURS` 小时时，将已校验备份上传到 Cloudflare R2 的
`cheese-db-backups` 桶，键为 `db/cheese-<ts>.dump`。此设置写在 `~/ops/backup.env`，默认 `6`。
标记缺失、无效或在未来时尝试上传。只有上传成功才更新标记，失败后在下一次小时任务重试。
production 和 etrip 保持现有脚本，每次备份均上传。

dev 本地恢复点仍为每小时；整机丢失时，异地恢复点目标（RPO）为约 6 小时。
由于每小时才检查一次是否到期，实际窗口可能接近 7 小时，另加备份和上传耗时；连续失败会继续延长窗口。
新鲜度检查的额外 2 小时用于告警容差。
Off-site retention of the dumps is the bucket's one lifecycle rule,
`expire-db-dumps-30d`: objects under `db/` expire 30 days after upload (maxAge
2592000 s, read from the Cloudflare API on 2026-10-19). It runs server-side, so
the box needs no delete permission. The rule reaches `db/` and nothing else:
nothing under `session-records/`, `transcripts/`, `archive/` or a `prod-*`
prefix expires, and no rule may be added that reaches them. The upload uses a **scoped R2 API token** (Object
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
restore plus byte verification of the configured uploads mirror: the prefix in
`~/ops/r2.env` (`UPLOADS_PREFIX`, `prod-uploads` when unset). On dev that is
production's `prod-uploads/` on purpose: the dev runner is where the
`prod-uploads` restore is proven (see Gaps below). Failures in
one drill do not skip the other R2 drills. All recovery destinations are
throwaway; the live DB and uploads directory are never opened for writing.

`db-restore-test.sh` resolves the image and the startup command from the
`postgres` service in `docker-compose.yml`, so the drill preloads exactly what
dev does — including `pg_cron`, which the image's entrypoint needs and which a
hand-written `-c shared_preload_libraries` would replace rather than extend. It
restores with `--clean --if-exists`, as production restores do, because the
throwaway database is initialised by that same entrypoint and already carries
the `paradedb` schema and pg_search the dump also creates. It fails if the dump
TOC lacks the extension, if the extension is not restored, on any `pg_restore`
error, on an incomplete schema, or on zero rows across the critical `user`,
`projects`, `topics`, and `blocks` tables, and it reports the container's own
log when the throwaway postgres exits before it listens. `CHEESE_PG_IMAGE` is an
explicit experiment override, not a second default. Containers publish no ports
and have no network access. Restore logs and TOC remain in `.tmp/restore-tests/`.

每天的新鲜度检查要求本地数据库成功标记不超过 26 小时。配置 R2 时，dev 异地成功标记和
dev R2 数据库对象的阈值为上传间隔加 2 小时（默认 8 小时），容纳小时 timer 的抖动和一次延迟运行。
检查读取与备份脚本相同的 `~/ops/backup.env`。uploads 标记、prod 数据库、etrip 的两个数据库
和 uploads tar 保持 6 小时阈值；R2 对象必须非空。年龄按秒计算。
未配置 R2 时明确跳过；部分配置、标记缺失、时间戳无效、对象过旧、前缀错误或 R2 读取失败均报错。

应用发布成功后，`.github/workflows/deploy-dev.yml` 从同一发布提交安装 `db-backup.sh`、
`r2-upload.py`、`r2-prune-dumps.py` 和 `r2-common.py` 至 dev 的 `~/ops`。
旧文件保存到 `~/ops/backup-rollouts/<release-sha>/`，依赖先安装，各文件通过重命名原子替换。
小时 timer、凭据和 `backup.env` 保持原值；此步骤只在 dev 执行。
历史发布回滚时，只安装该发布实际包含的备份脚本。早期 writer 不调用后续引入的 helper，机器上已有的这些文件保持原值。

dev uploads 的 CI 检查读取 `cheese-uploads-mirror.service` 最近一次成功退出记录
（`Result=success`、正常退出、状态为 `0`），以服务退出时间写入 uploads 标记。
服务停止、失败或从未运行时，无法产生新的成功记录。此检查无需更新已部署的 uploads 脚本。

在其他机器安装支持成功标记的 uploads 脚本，需要单独授权的运维发布。

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
- On dev the freshness check's uploads marker is the default
  `~/backups/.last-uploads-mirror-success`, refreshed from the dev unit's last
  successful exit; that unit mirrors `dev-uploads/`, and no check reads the
  age of `dev-uploads/` objects. The weekly uploads drill restores
  `prod-uploads/`, so dev's own mirror has no scheduled restore proof.
- These jobs run on dev. The existing separate box uptime check is still needed
  to detect loss of the runner; R2 checks do not replace it.

- **PITR (second-level RPO):** hourly logical dumps are the client-side floor.
  True point-in-time recovery needs WAL archiving **on the PG host**, which
  requires OS access to `192.168.16.7` (SSH is currently closed) and a
  superuser/replication role (the `cheese` role is neither). To enable, on the PG
  host set `wal_level=replica` (already), `archive_mode=on`, an `archive_command`
  to durable storage, and run pgBackRest/Barman for base backups + WAL. Blocked
  until that access is granted.
