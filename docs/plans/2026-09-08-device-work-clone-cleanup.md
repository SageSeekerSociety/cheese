# Device work clone retirement

Every device execution uses a checkout at
`$HOME/.cheese/work/<project_id>/<place_id>`. Previously, removal depended on the
backend still remembering the place's screen. A restart or reconnect could leave
the checkout behind indefinitely. The September 8 inventory found 103 GB in 98
such directories on dev.

## Sweep behavior

`sweep_retired_storage` now inventories both `.cheese/home` and `.cheese/work`
through one shell command per online device. `list_device_storage` reports the
root, project and place for each directory. The launcher and remover share
`device_work_dir` as the work path definition.

Both roots use `_place_archival` and `_verdict`:

- Active places and places still within the configured retention remain.
- Archived places past `topic_home_retention_days` become eligible for removal.
- A place absent from both the topics and tasks tables is treated as an orphan
  and becomes eligible immediately, following the existing retention policy.
- Malformed directory identifiers remain. Project and place symlinks are not
  followed into other device data.

The sweep resolves historical threads through the tasks table, so a live thread
is not mistaken for a deleted room. It does not use filesystem modification times.
The shared listing uses portable shell operations supported on macOS and Linux.
An absent HOME root does not hide the work root.

Each decision to remove or retain a work directory produces a `sweep: work=` log
entry with the reason. A failed
removal is counted as `work_left` and retried on a later sweep. Offline devices
remain untouched until a sweep after reconnect. A failed or truncated listing
skips that device's pass.

Homes still upload their raw transcripts before removal under the existing
archive rules. Work clones hold code; their removal does not upload transcripts
or create a source backup. Published commits remain in Git storage. As with the existing
archive policy, deleting an eligible checkout discards its local uncommitted or
unpushed work. Review the actual candidates before enabling this expanded sweep
on a device with historical data.

## Verification

`backend/tests/integration/test_archive_retires_storage.py` covers active and
recently archived rooms, expired archives, deleted places, live and closed
historical threads, failed deletion with retry, and offline reconnect. Its shell
fixtures exercise missing roots and symlinks on real temporary directories. The
existing transcript tests still verify upload before HOME removal.

For dev rollout, record the directory count and size before and after, then match
changes to the per-directory log:

```sh
sudo find /home/nictheboy/.cheese/work -mindepth 2 -maxdepth 2 -type d | wc -l
sudo du -xsh /home/nictheboy/.cheese/work
sudo journalctl CONTAINER_NAME=cheese-backend-1 --since '1 hour ago' -o cat | grep 'sweep: work='
```

## Follow-up design

This change fills a discovery gap in the existing retention policy. Five-minute
idle release, independent scheduling, fresh-session continuation and readable
transcript retrieval require the separate lifecycle design. They are not enabled
by this sweep. Sandbox directories under
`<workspace_root>/.sessions/<project>/<hex8>` also remain a separate cleanup task.
