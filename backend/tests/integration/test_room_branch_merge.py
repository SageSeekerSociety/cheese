"""一个房间一条分支一个 PR: a task forks the room's branch and folds back into it.

Two halves, and this file covers the git one:

- a task's workspace starts from the ROOM's branch, not the base branch, so it
  sees what the room already built;
- when its conclusion is 采信'd, its commits go onto the ROOM's branch — the one
  the room's accept card and PR ride — instead of reaching main through a PR of
  its own.

The three ways that can go wrong all have to be visible rather than silent, so
each gets a test: a conflict is reported with the paths, a room somebody is
editing in is left alone, and neither case is allowed to look like success.
"""

import subprocess
import uuid

from app.domain.workspace import service as ws


def _mkproject(client) -> uuid.UUID:
    resp = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()
    return uuid.UUID(resp["data"]["id"])


def _native_edit(pid: uuid.UUID, topic_id: uuid.UUID, path: str, content: str) -> None:
    """Simulate a turn: native tools write into the topic's jj workspace, then
    the platform snapshots (as converse does at the end of a turn)."""
    wt = ws.topic_worktree(pid, topic_id)
    target = wt / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    ws.snapshot_worktree(pid, topic_id)


def _branch_files(pid: uuid.UUID, branch: str) -> set[str]:
    repo = ws.ensure_repo(pid)
    out = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return set(out.stdout.split())


def _split_task(room_id: uuid.UUID) -> uuid.UUID:
    """A task in `room_id`, registered the way `split_to_subtopic` registers it."""
    task_id = uuid.uuid4()
    ws.bind_branch_parent(task_id, room_id)
    return task_id


def test_task_workspace_starts_from_the_room_branch(client):
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "built by the room\n")

    task = _split_task(room)
    wt = ws.topic_worktree(pid, task)

    # The room's work is already here — a task that cannot see it would redo it.
    assert (wt / "room.txt").read_text() == "built by the room\n"
    # ...and it is NOT on the base branch, so this came from the room, not main.
    assert "room.txt" not in _branch_files(pid, "main")


def test_a_room_still_forks_the_base_branch(client):
    """No marker → the pre-room behaviour, unchanged: rooms stand on main."""
    pid = _mkproject(client)
    room_a, room_b = uuid.uuid4(), uuid.uuid4()
    _native_edit(pid, room_a, "a.txt", "from room a\n")

    wt = ws.topic_worktree(pid, room_b)
    assert not (wt / "a.txt").exists()


def test_accepted_task_commits_land_on_the_room_branch(client):
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "room\n")
    task = _split_task(room)
    _native_edit(pid, task, "task.txt", "done by the task\n")

    result = ws.merge_subtopic_into_room(pid, task, room)

    assert result["merged"] is True
    assert result["commits"] >= 1
    room_branch = ws.branch_for_topic(room)
    assert "task.txt" in _branch_files(pid, room_branch)
    # The base branch is untouched: the work reaches main through the ROOM's
    # accept card, not through the task.
    assert "task.txt" not in _branch_files(pid, "main")
    # The room's own workspace shows it, or the room's next snapshot would set
    # the bookmark back off these commits.
    assert (ws.topic_worktree(pid, room) / "task.txt").exists()


def test_folding_twice_is_a_noop_not_a_second_merge(client):
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "room\n")
    task = _split_task(room)
    _native_edit(pid, task, "task.txt", "work\n")

    assert ws.merge_subtopic_into_room(pid, task, room)["merged"] is True
    again = ws.merge_subtopic_into_room(pid, task, room)
    assert again["merged"] is False
    assert again["noop"] is True


def test_a_task_that_wrote_no_code_is_a_noop(client):
    """调研类子话题 produce a conclusion and no diff — the normal case."""
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "room\n")
    task = _split_task(room)

    result = ws.merge_subtopic_into_room(pid, task, room)
    assert result["merged"] is False
    assert result["noop"] is True


def test_conflict_names_the_files_instead_of_failing_silently(client):
    """冲突提前了: two tasks touching one file collide on the room's branch —
    days earlier than they would have collided between two PRs."""
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "shared.txt", "original\n")

    first, second = _split_task(room), _split_task(room)
    _native_edit(pid, first, "shared.txt", "first task's version\n")
    _native_edit(pid, second, "shared.txt", "second task's version\n")

    assert ws.merge_subtopic_into_room(pid, first, room)["merged"] is True
    clash = ws.merge_subtopic_into_room(pid, second, room)

    assert clash["merged"] is False
    assert clash.get("noop") is not True  # NOT "nothing to do"
    assert "shared.txt" in clash["conflicts"]
    # The room's branch keeps the first task's work — never a half-merge.
    repo = ws.ensure_repo(pid)
    assert (
        "first task's version"
        in subprocess.run(
            ["git", "show", f"{ws.branch_for_topic(room)}:shared.txt"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


def test_uncommitted_edits_in_the_room_are_never_swept_away(client):
    """人的未提交编辑绝不能被机器扫掉 —— the merge queues instead."""
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "room\n")
    task = _split_task(room)
    _native_edit(pid, task, "task.txt", "work\n")

    # Somebody is editing in the room right now, with no turn to snapshot it.
    room_wt = ws.topic_worktree(pid, room)
    (room_wt / "room.txt").write_text("a human is mid-edit\n", encoding="utf-8")

    result = ws.merge_subtopic_into_room(pid, task, room)

    assert result["merged"] is False
    assert result["deferred"] is True
    assert (room_wt / "room.txt").read_text() == "a human is mid-edit\n"
    assert "task.txt" not in _branch_files(pid, ws.branch_for_topic(room))

    # Once that edit is committed, the queued merge goes through.
    ws.snapshot_worktree(pid, room)
    assert ws.merge_subtopic_into_room(pid, task, room)["merged"] is True
    assert "task.txt" in _branch_files(pid, ws.branch_for_topic(room))


def test_a_tasks_diff_shows_its_own_work_not_the_rooms(client):
    """改动 tab: a task forks the room, so its changes are measured from there.
    Against main it would report every file the room had already touched."""
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "the room did this\n")
    task = _split_task(room)
    _native_edit(pid, task, "task.txt", "the task did this\n")

    assert ws.topic_changed_files(pid, task) == ["task.txt"]
    assert "the room did this" not in ws.topic_diff(pid, task)
    assert "the task did this" in ws.topic_diff(pid, task)
    # The room still measures itself against the base branch.
    assert ws.topic_changed_files(pid, room) == ["room.txt"]


def test_folding_does_not_repoint_the_shared_checkout(client):
    """The project-level file panel mirrors the BASE tip. A room's branch taking
    in one of its tasks must not make that panel show the room's work."""
    pid = _mkproject(client)
    room = uuid.uuid4()
    _native_edit(pid, room, "room.txt", "room\n")
    task = _split_task(room)
    _native_edit(pid, task, "task.txt", "work\n")

    assert ws.merge_subtopic_into_room(pid, task, room)["merged"] is True

    project_files = {f["path"] for f in ws.list_files(pid)}
    assert "task.txt" not in project_files
    assert "room.txt" not in project_files
