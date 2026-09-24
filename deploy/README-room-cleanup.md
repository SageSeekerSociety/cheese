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

Transcripts are not uploaded. Every Claude Code session runs on the central
session host (`AGENT_SESSION_DEVICE_ID`), so a room's transcripts are in its home
there. When cleanup removes that home, it first compresses the home's
`.claude/projects` tree — the main session files and each subagent's — into
`~/.cheese/transcripts/<project>/<room>/<resource>.tar.gz` on the same host,
readable only by the host user. A link in the tree is stored as a link and
never followed. Other devices keep no transcripts.

The operation then waits in the `retained` state. Thirty days after the home was
removed (`TRANSCRIPT_RETENTION` in `backend/app/domain/topic/retire.py`), the same
timer deletes the archive and the operation becomes `complete`. The copy exists for
debugging what an agent did after the fact; nothing reads it. Handing the work on
does not need it: the room's chat, its living doc and its action timeline stay
with the room.

Unarchiving a room while its transcripts are retained restores nothing. The room
gets a new resource generation and its agents start new sessions, as after any
completed cleanup; the retained archive still expires on schedule. Archiving the
room again retains the new generation's transcripts in a second archive beside
the first.

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
active writers, unpublished Git changes or outstanding hook events retain the
original resource. A stop whose outcome is unknown must be reconciled before that
same environment can be resumed. A backend worktree already moved aside stays
owned by the unfinished cleanup until it completes. An offline session host keeps
a retained operation waiting until it is back.
