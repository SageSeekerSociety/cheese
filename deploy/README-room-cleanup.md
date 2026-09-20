# Archived-room cleanup

Unarchived rooms retain their running environment. Archival creates a durable
cleanup operation; it does not immediately destroy a machine. The default grace
period is five minutes. A failed publication or transcript check retains the
resource and records the reason.

Final verification reads one stored chunk per request and saves progress, so a
worker restart does not restart verification of a long transcript. Cloud deletion
also requires an empty room-directory inventory; unknown directories retain the VM.

## Configuration

Set these in the backend environment file before deploying:

```dotenv
TOPIC_ARCHIVE_CLEANUP_DELAY_S=300
TRANSCRIPT_S3_BUCKET=cheese-db-backups
```

The named bucket must be private. The collector uses `S3_ENDPOINT_URL`,
`S3_ACCESS_KEY`, `S3_SECRET_KEY` and `S3_REGION` for the connection. These credentials
need read/write access to `transcripts/raw/`. No public uploads bucket fallback is
provided. Confirm bucket privacy and authenticated download before enabling use.
Changing the grace period affects future archives only.

Existing archives in `TRANSCRIPTS_DIR` and their additive R2 backup remain intact.
The migration does not backfill cleanup operations for historical archives or
unknown directories. Inventory and approve those separately.

## Independent trigger

On systemd hosts, `deploy-docker.sh` installs and enables
`cheese-room-cleanup.timer`. The timer invokes `trigger-room-cleanup.sh` every minute.
The script selects only containers carrying the `li.zhifei.cheese.room-cleanup=true` label.
The trigger runs inside the container and uses its server credential against
loopback; no credential is placed in the host command line or journal.

For an existing installation, the same setup can be run directly:

```sh
bash deploy/install-room-cleanup-timer.sh
systemctl is-active cheese-room-cleanup.timer
systemctl list-timers cheese-room-cleanup.timer
journalctl -u cheese-room-cleanup.service --since '1 hour ago'
```

On a host without systemd, schedule `deploy/trigger-room-cleanup.sh` every minute
with the host scheduler. API startup and device reconnect also retry overdue
operations, but are not a replacement for the independent clock.

## Verification and pending work

Use a disposable owner-managed room to verify archive, deadline persistence across
a backend restart, transcript download, and unarchive after cleanup. Check the
downloaded main-session and subagent files against their original bytes. Collection
is asynchronous; a device lost before upload can still lose its latest unsent bytes.

`GET /topics/{id}/cleanup` reports the stage, deadline, completed resource count and
the latest failure. Backend logs include the cleanup operation and room IDs. Device
collector failures are timestamped in the room's `.claude/cheese-drain.log`.
Existing sessions upgrade their sender when the next turn reuses the session;
this preserves the native agent process. Cleanup always ships the current collector
for its final flush, including rooms last used before this deployment.

The device needs Python 3, curl, tmux and lsof. Missing tooling, offline devices,
active writers, unpublished Git changes, outstanding hook events or incomplete
storage verification retain the original resource. A stop whose outcome is unknown
must be reconciled before that same environment can be resumed. A backend worktree
already moved aside stays owned by the unfinished cleanup until it completes.

Private object storage contains a `source.json` identity record and immutable
offset/hash chunks for every raw file. PostgreSQL holds the normal read index;
restoring an older DB backup may require rebuilding missing index rows from those
objects. Keep the existing database backup schedule.
