# Device work clones are never cleaned up

Status: not started, handed to a dedicated owner on 2026-09-08. The two neighbouring
classes of leftover — the backend's own topic checkouts and the device's per-topic
home — were closed by #675 and #690. This is the third one, and on the dev box it is
now the largest.

## What is leaking

Every topic that runs on a device gets its own checkout there, at
`$HOME/.cheese/work/<project_id>/<topic_id>` (`device_provider._work_dir`). It is the
screen's working directory: the place the agent edits code, and the clone it pushes
from. One directory per topic, per device.

Measured on the dev box on 2026-09-08: **103 GB in 98 directories**, all under one
project, against 342 GB used of a 504 GB disk. Almost all of them belong to topics
that ended weeks ago.

## Why nothing removes them

There is exactly one remover, and it only fires while the backend still remembers the
screen. `DeviceChannel.release_topic` walks `hub.screens_for_topic(topic_id)` — the
screens the running process holds in memory — closes each one, and then removes that
screen's work directory (`_remove_work_dir`). Archive calls it through
`release_topic_screen`, and so does the idle reaper.

So a topic whose screen the hub no longer knows about keeps its clone forever: the
backend restarted since the turn, the device reconnected under a new session, or the
topic was archived before any of this code existed. Nothing sweeps for them
afterwards.

The hourly sweep that covers the other two classes does not look here.
`sweep_retired_storage` (`backend/app/domain/topic/retire.py`) runs `_sweep_worktrees`
over the backend's checkouts and `_sweep_homes` over
`$HOME/.cheese/home/<project_id>/<place_id>` on every online device. There is no
`_sweep_work`.

## What done looks like

The sweep resolves every `<project>/<place>` directory under the work root on each
online device, exactly as `_sweep_homes` resolves the home root, and reaches the same
verdict per directory:

- the place row is gone → remove now;
- archived longer ago than `topic_home_retention_days` (30) → remove;
- anything else → keep.

One log line per directory with its reason, in the shape the home sweep already
writes, so the dev box can be audited from the journal alone.

Share the device round trip with `_sweep_homes` instead of adding a second one. That
pass already issues one `sh -lc` per device and one removal per directory; listing a
second root in the same command costs nothing extra, and a device that is slow or
offline then fails one pass rather than two.

`device_home_dir` has no counterpart for the work root — the path is built inline in
`_work_dir`, which is a method on the provider. A module-level `device_work_dir()`
beside `device_home_dir()` is the smallest thing that lets both the sweep and the
provider name the same path.

## Traps

1. **An active topic's clone is live state.** It holds uncommitted edits and the
   branch the agent is working on. The verdict above never removes one, but a bug
   that widens it destroys work that exists nowhere else. Archived is different: an
   archive abandons uncommitted changes by definition, which is what `retire.py`
   already says for the backend's own checkout.
2. **A thread runs under its own id**, which is a row in `tasks`, not `topics`.
   `_place_archival` already resolves both kinds; call it rather than deriving the
   archival state again.
3. **`list_device_homes` avoids `find -printf` on purpose** — it is GNU-only and a
   device may be a Mac. Whatever lists the work root inherits that constraint.
4. **An offline device raises `DeviceOffline`.** The home sweep leaves that device's
   directories alone and picks them up on a later tick; do the same, and log it.
5. **No transcript upload belongs here.** `_store_transcripts` exists because a home
   holds the only copy of the raw session files. A work clone holds a git checkout
   whose commits are already on a branch.
6. **Do not delete by mtime.** A directory's age says nothing about whether its topic
   is still open, and the sweep has the database.

## How to verify

On the dev box, before and after:

```sh
sudo find /home/nictheboy/.cheese/work -mindepth 2 -maxdepth 2 -type d | wc -l
sudo du -xsh /home/nictheboy/.cheese/work
sudo journalctl CONTAINER_NAME=cheese-backend-1 --since '1 hour ago' -o cat | grep 'sweep: work='
```

`backend/tests/integration/test_archive_retires_storage.py` holds nine tests that
build real git worktrees under a temporary workspace root and attach a fake connector
to the real `DeviceHub`, which answers `exec` frames. The work-clone cases go beside
the home ones: removed when the place is gone, removed when archived past the
retention, kept while active, left alone when the device is offline.

## Adjacent, out of scope

The sandbox session directories under `<workspace_root>/.sessions/<project>/<hex8>`
are a fourth class nobody sweeps, about 3 GB on the dev box. Worth a look after this,
not worth bundling into it.
