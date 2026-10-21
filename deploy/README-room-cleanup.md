# Archived-room cleanup

Unarchived rooms retain their running environment. Archival creates a durable
cleanup operation; it does not immediately destroy a machine. The default grace
period is five minutes. A failed publication check retains the resource and
records the reason. Cloud deletion also requires an empty room-directory
inventory; unknown directories retain the VM.

## Configuration

Set this in the backend environment file before deploying:

```dotenv
TOPIC_ARCHIVE_CLEANUP_DELAY_S=300
```

Changing the grace period affects future archives only.

## Transcripts

Every session runs on the central session host (`AGENT_SESSION_DEVICE_ID`), so a
room's transcripts are there: Claude Code's in the room's home
(`.claude/projects`, the main session files and each subagent's), Codex's and
pi's under the resource's harness state (`~/.cheese/harness/<project>/<resource>/`,
Codex's `CODEX_HOME/sessions`, pi's `sessions/` and each subagent's). When cleanup
removes that home, it first compresses all of them into
`~/.cheese/transcripts/<project>/<room>/<resource>.tar.gz` on the same host,
readable only by the host user. A link in the tree is stored as a link and
never followed. Other devices keep no transcripts.

The archive is then sent to the private bucket (`TRANSCRIPT_S3_BUCKET`) under
`transcripts/rooms/<project>/<room>/<resource>.tar.gz`, by a presigned PUT, and
the backend checks the bucket holds it at the size and MD5 the host sent. That
copy is kept for good; nothing deletes under `transcripts/rooms/` (Zhifei's
ruling on #4398, 2026-10-19, which supersedes #1611's "not uploaded").

The operation then waits in the `retained` state. Thirty days after the home was
removed (`TRANSCRIPT_RETENTION` in `backend/app/domain/room_cleanup/retire.py`), the same
timer deletes the host's archive and the operation becomes `complete`, but only
once the bucket's copy is confirmed: an archive not yet sent (a send that failed,
or one retained before sending existed) is sent first, and one that cannot be
sent stays on the host and the operation is tried again later.

Unarchiving a room while its transcripts are retained restores nothing. The room
gets a new resource generation and its agents start new sessions, as after any
completed cleanup; the retained archive still expires on schedule. Archiving the
room again retains the new generation's transcripts in a second archive beside
the first.

A room whose row is gone while its cleanup is unfinished, such as an archived
room that became a closed task, is cleaned up all the same: the operation holds
its project, generation and resources, and its machine homes stay recorded under
the room's id, which is the task's conversation.

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
a backend restart, the retained transcript archive on the session host, and
unarchive after cleanup.

`GET /topics/{id}/cleanup` reports the stage, deadline, completed resource count and
the latest failure. Backend logs include the cleanup operation and room IDs.
Before removing a home, cleanup delivers the room's outstanding hook events with
the sender the backend ships today, including for rooms last used before a
deployment.

The device needs Python 3, curl, tmux and lsof. Missing tooling, offline devices,
active writers or outstanding hook events retain the original resource. Work that
was not pushed does not: each session gets one best-effort checkpoint first, and
an old room's non-git `room/` files go to the private bucket for thirty days
before its home is removed (`scripts/retained_files.py rooms` fetches them). A stop whose outcome is unknown must be reconciled before that
same environment can be resumed. A backend worktree already moved aside stays
owned by the unfinished cleanup until it completes. An offline session host keeps
a retained operation waiting until it is back.
