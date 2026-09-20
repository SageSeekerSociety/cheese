# Device work clone retirement

The September 8 investigation found 103 GB in 98 device checkouts. Their removal
depended on the backend still remembering a room's screen, so restarts could leave
them behind. Discovery now lists `.cheese/home` and `.cheese/work` in one portable
shell visit, including when only one root exists. Project and resource symlinks are
excluded. Historical thread IDs are resolved through the tasks table.

Deletion follows the archived-room lifecycle in
[`docs/where-a-turn-runs.md`](../where-a-turn-runs.md#八归档退掉什么), replacing the
former 30-day/orphan policy. An unresolved directory is retained for a separate
inventory decision. A known room is eligible only through its persisted archival
operation, after writer, publication and transcript checks. Open-room agents and
environments remain allocated.

`test_archive_lifecycle.py` covers cancellation, persistent deadlines, retry after
storage failure, and reopening while old deletion is incomplete.
`test_archive_retires_storage.py` retains tests for portable inventory and uploads
in the existing archive format. `test_resource_cleanup.py` exercises filesystem checks and a
stop subprocess surviving the death of its caller.

Historical deletion requires a reviewed inventory; no migration schedules old
directories for removal. Backend sandbox directories under
`<workspace_root>/.sessions/<project>/<hex8>` remain outside this cleanup.
