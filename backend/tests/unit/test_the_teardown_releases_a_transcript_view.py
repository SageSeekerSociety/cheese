"""The teardown has to undo a transcript view, and to be able to trust it.

The helper runs outside the room's sandbox, so no part of the ordinary teardown
reaches it — and what it mounts sits inside the room's own home, where a mount
that is left makes the home unremovable. The record it writes is under that same
home, which a sandboxed room can write all of, so its contents are a claim the
teardown has to check before acting on.
"""

import json

from app.domain.agent import resource_cleanup


def _room(tmp_path, monkeypatch, *, pid=4321, mounts=("/host/projects/a.jsonl",)):
    home = tmp_path / "room"
    (home / ".cheese").mkdir(parents=True)
    (home / ".cheese" / "transcript-view.json").write_text(
        json.dumps({"pid": pid, "mounts": list(mounts)})
    )
    return home


def test_a_mount_that_cannot_be_released_keeps_its_record(tmp_path, monkeypatch):
    """A failed unmount must stay findable.

    `remove_tree` failing on the mount is what leads to a second attempt; if
    the record were deleted here, that attempt would have nothing left that
    names the mount and it could never be released at all.
    """
    home = _room(tmp_path, monkeypatch)
    monkeypatch.setattr(resource_cleanup, "mounted_at", lambda _path: True)
    monkeypatch.setattr(resource_cleanup, "run_command", lambda *a, **k: None)
    monkeypatch.setattr(resource_cleanup, "_is_this_rooms_view", lambda *a: False)

    resource_cleanup.stop_transcript_view(home)

    record = home / ".cheese" / "transcript-view.json"
    assert record.exists()
    assert json.loads(record.read_text())["mounts"] == ["/host/projects/a.jsonl"]


def test_a_released_mount_leaves_nothing_behind(tmp_path, monkeypatch):
    home = _room(tmp_path, monkeypatch)
    released = []

    def unmount(*_args, **_kwargs):
        released.append(True)
        monkeypatch.setattr(resource_cleanup, "mounted_at", lambda _path: False)

    monkeypatch.setattr(resource_cleanup, "mounted_at", lambda _path: True)
    monkeypatch.setattr(resource_cleanup, "run_command", unmount)
    monkeypatch.setattr(resource_cleanup, "_is_this_rooms_view", lambda *a: False)

    resource_cleanup.stop_transcript_view(home)

    assert released, "nothing tried to unmount the view"
    assert not (home / ".cheese" / "transcript-view.json").exists()


def test_a_record_that_cannot_be_rewritten_does_not_stop_the_teardown(
    tmp_path, monkeypatch
):
    """A bookkeeping write must not abort the teardown around it.

    This runs first in `stop_executor` and in the `remove` action. An `OSError`
    out of it — a read-only or half-removed home — would take `sandbox_release`,
    `check_no_writers` and `remove_tree` down with it, leaving the room in
    pieces over a file that only records what is left. The mounts stay named by
    the previous version, which is what the next attempt reads.
    """
    home = _room(tmp_path, monkeypatch)
    monkeypatch.setattr(resource_cleanup, "mounted_at", lambda _path: True)
    monkeypatch.setattr(resource_cleanup, "run_command", lambda *a, **k: None)
    monkeypatch.setattr(resource_cleanup, "_is_this_rooms_view", lambda *a: False)
    monkeypatch.setattr(
        resource_cleanup.Path,
        "write_text",
        lambda self, _data: (_ for _ in ()).throw(OSError("read-only")),
    )

    resource_cleanup.stop_transcript_view(home)

    record = home / ".cheese" / "transcript-view.json"
    assert json.loads(record.read_text())["mounts"] == ["/host/projects/a.jsonl"]


def test_a_record_of_the_wrong_shape_does_not_stop_the_teardown(tmp_path, monkeypatch):
    """The room writes this file, so its *shape* is a claim too.

    `json.loads` accepts `[]`, `5` and `"x"` as readily as an object, and a
    `mounts` that is not a list parses just as well. `.get` on the first raises
    `AttributeError` and iterating the second raises `TypeError` — either of
    which aborts everything after it, because this is the first step of
    `stop_executor` and of the `remove` action. Nothing is left to undo in a
    record of the wrong shape, so the call has to return and let the teardown
    continue.
    """
    for bad in (
        "[]",
        "5",
        '"x"',
        "null",
        '{"mounts": 5}',
        '{"mounts": "a"}',
        '{"pid": "abc"}',
    ):
        home = tmp_path / f"room-{abs(hash(bad))}"
        (home / ".cheese").mkdir(parents=True)
        (home / ".cheese" / "transcript-view.json").write_text(bad)
        monkeypatch.setattr(resource_cleanup, "mounted_at", lambda _path: False)
        monkeypatch.setattr(resource_cleanup, "run_command", lambda *a, **k: None)

        resource_cleanup.stop_transcript_view(home)  # must not raise


def test_a_pid_the_record_names_is_not_signalled_unchecked(tmp_path, monkeypatch):
    """The record is the room's to write, so its pid is only a claim.

    A room that wrote another room's pid would otherwise have the teardown
    end that room's helper — they run under the same account.
    """
    home = _room(tmp_path, monkeypatch)
    killed = []
    monkeypatch.setattr(resource_cleanup, "mounted_at", lambda _path: False)
    monkeypatch.setattr(resource_cleanup.os, "kill", lambda *a: killed.append(a))
    monkeypatch.setattr(resource_cleanup, "_is_this_rooms_view", lambda *a: False)

    resource_cleanup.stop_transcript_view(home)

    assert killed == []


def test_the_helpers_command_line_is_what_identifies_it(tmp_path, monkeypatch):
    """A pid whose command line is this room's `transcript_view.py` is it."""
    home = _room(tmp_path, monkeypatch, pid=99)
    view = str(home / ".cheese" / "transcript-view")
    monkeypatch.setattr(
        resource_cleanup.Path,
        "read_bytes",
        lambda self: (
            f"/usr/bin/python\0/release/transcript_view.py\0{view}\0".encode()
            if str(self).endswith("cmdline")
            else b""
        ),
    )

    assert resource_cleanup._is_this_rooms_view(99, home) is True


def test_the_stop_runs_before_anything_walks_the_home(tmp_path, monkeypatch):
    """`stop_executor` is the teardown's first step for a room with an executor.

    Order matters: what the view holds is inside the home that every later
    step walks and, at the end, deletes.
    """
    home = _room(tmp_path, monkeypatch)
    order = []
    monkeypatch.setattr(
        resource_cleanup, "stop_transcript_view", lambda _home: order.append("view")
    )
    monkeypatch.setattr(
        resource_cleanup, "sandbox_release", lambda _home: order.append("probe") or None
    )
    monkeypatch.setattr(
        resource_cleanup,
        "platform_dir",
        lambda _home: order.append("marker") or (home / ".cheese"),
    )
    monkeypatch.setattr(
        resource_cleanup.Path, "exists", lambda self: order.append("marker") or False
    )

    resource_cleanup.stop_executor(home, "resource")

    assert order and order[0] == "view", order
