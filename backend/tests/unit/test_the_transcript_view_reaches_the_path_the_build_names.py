"""The executor binds its transcript view where the build says the file is.

After a compaction the build tells the model to read a path, and that path is
on the session host — a machine the executor is not. So the executor mounts a
read-only view there, and these are the rules that mounting has to keep:

* the path comes from the platform, never from anything the view decides: the
  view is told where to mount, it does not go looking;
* a path whose chain cannot be made is reported once and never retried into a
  loop — a room that cannot be given the file still runs;
* a mount whose server has died reads as not mounted, so a helper starting
  over can take its place.

The mounting itself needs a machine and root, so what is held here is the
decision each step makes, not the kernel's answer.
"""

from app.domain.agent.harness.claude_code.remote_execution import transcript_view


class _Calls:
    """Records every command run, and answers each the way `made` says."""

    def __init__(self, made=True):
        self.made = made
        self.commands = []

    def __call__(self, command, *, timeout=30.0):
        self.commands.append(list(command))
        return self.made


def test_a_view_that_is_not_mounted_yet_is_mounted_at_the_named_path(
    monkeypatch, tmp_path
):
    view = tmp_path / "view-transcript.jsonl"
    view.write_bytes(b'{"a":1}\n')
    path = tmp_path / "host" / "projects" / "slug" / "id.jsonl"
    calls = _Calls()
    monkeypatch.setattr(transcript_view, "_run", calls)
    monkeypatch.setattr(transcript_view, "_mounted", lambda _p: False)

    assert transcript_view._remount(view, str(path)) is True

    binds = [c for c in calls.commands if "mount" in c]
    assert [c[-1] for c in binds] and all(c[-1] == str(path) for c in binds)
    assert all(str(view) in c for c in binds)


def test_a_path_whose_chain_cannot_be_made_is_reported_not_retried(
    monkeypatch, tmp_path
):
    """A machine the helper cannot write the chain on: no mount is attempted.

    Nothing here loops or throws. The answer is False, which the caller logs
    once; the room runs with the transcript at the unreachable path it always
    had.
    """
    path = tmp_path / "host" / "projects" / "slug" / "id.jsonl"
    calls = _Calls(made=False)
    monkeypatch.setattr(transcript_view, "_run", calls)
    monkeypatch.setattr(transcript_view, "_mounted", lambda _p: False)

    assert transcript_view._remount(tmp_path / "view", str(path)) is False
    assert not [c for c in calls.commands if "mount" in c]


def test_a_live_mount_at_the_path_is_left_alone(monkeypatch, tmp_path):
    path = tmp_path / "id.jsonl"
    path.write_bytes(b"there")
    calls = _Calls()
    monkeypatch.setattr(transcript_view, "_run", calls)
    monkeypatch.setattr(transcript_view, "_mounted", lambda _p: True)

    assert transcript_view._remount(tmp_path / "view", str(path)) is True
    assert calls.commands == []


def test_a_mount_whose_server_died_is_not_read_as_done(monkeypatch, tmp_path):
    """A dead mount reads as not mounted, so a helper starting over can mount.

    The path stays occupied by the dead mount and cannot be mounted over until
    it is released, so the release happens first.
    """
    view = tmp_path / "view-transcript.jsonl"
    view.write_bytes(b"bytes")
    path = tmp_path / "id.jsonl"
    path.write_bytes(b"old")
    calls = _Calls()
    monkeypatch.setattr(transcript_view, "_run", calls)
    monkeypatch.setattr(transcript_view, "_mounted", lambda _p: True)

    # Only this module's own call: patching `os.stat` itself would reach
    # pytest's own machinery, which asks it `follow_symlinks=`.
    monkeypatch.setattr(transcript_view, "_live", lambda _p: False)

    assert transcript_view._remount(view, str(path)) is True

    released = [c for c in calls.commands if "fusermount" in c]
    assert released and "-uz" in released[0]
    assert [c for c in calls.commands if "mount" in c]


def test_a_dead_mount_is_seen_as_a_mount(monkeypatch, tmp_path):
    """`ismount` answers False for exactly the mount that has to be released.

    Every stat on a dead FUSE mount fails and `ismount` swallows that and says
    nothing is mounted — so a helper starting over mounts onto a path that
    cannot take one and reports the room given the file when it was not. The
    kernel's own list says both are mounted.
    """
    path = tmp_path / "id.jsonl"
    path.touch()
    monkeypatch.setattr(
        transcript_view,
        "_read_mountinfo",
        lambda: f"36 1 0:33 / {path} rw - fuse.cheese cheese ro\n",
    )

    assert transcript_view._mounted(path) is True


def test_a_path_nothing_is_mounted_at_is_not_a_mount(monkeypatch, tmp_path):
    path = tmp_path / "id.jsonl"
    path.touch()
    monkeypatch.setattr(transcript_view, "_read_mountinfo", lambda: "")

    assert transcript_view._mounted(path) is False
