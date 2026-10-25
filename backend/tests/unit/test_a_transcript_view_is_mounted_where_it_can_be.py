"""What a machine has to have before a transcript view is mounted on it.

The view is mounted by the room's user and read by root through a bind, so FUSE
needs `user_allow_other` in `/etc/fuse.conf` or it refuses the second reader.
That line is written as root *on a machine the platform provisioned* and
nowhere else: a machine a person enrolled is theirs, and the room runs with the
transcript unreachable exactly as it did before any of this existed.
"""

from app.domain.agent.harness.claude_code.remote_execution import bootstrap


def _machine(monkeypatch, tmp_path, *, fuse=True, fusermount=True, allow=None):
    """A machine with the FUSE pieces `fuse` and `fusermount` say, and a
    `/etc/fuse.conf` whose `user_allow_other` line `allow` decides.

    `allow` is True (already there), False (absent) or None (no file at all).
    """
    conf = tmp_path / "fuse.conf"
    if allow is not None:
        conf.write_text("# comment\n" + ("user_allow_other\n" if allow else ""))
    monkeypatch.setattr(
        bootstrap.Path, "exists", lambda self: fuse and str(self) == "/dev/fuse"
    )
    monkeypatch.setattr(
        bootstrap.shutil,
        "which",
        lambda name: (
            "/usr/bin/fusermount"
            if fusermount and name.startswith("fusermount")
            else None
        ),
    )
    monkeypatch.setattr(bootstrap, "FUSE_CONF", str(conf))
    return conf


def test_a_machine_already_allowing_other_readers_needs_no_root(monkeypatch, tmp_path):
    calls = []
    _machine(monkeypatch, tmp_path, allow=True)
    monkeypatch.setattr(bootstrap, "sudo", lambda *a, **k: calls.append(a))

    assert bootstrap.fuse_is_mountable(platform_machine=False) is True
    assert calls == []


def test_a_machines_own_computer_never_asks_for_root(monkeypatch, tmp_path):
    """The condition that keeps the platform off people's machines.

    No `user_allow_other` and not a platform machine: the answer is no, and
    nothing was run — no `sudo`, no package manager, no write to `/etc`.
    """
    calls = []
    _machine(monkeypatch, tmp_path, allow=False)
    monkeypatch.setattr(bootstrap, "sudo", lambda *a, **k: calls.append(a))

    assert bootstrap.fuse_is_mountable(platform_machine=False) is False
    assert calls == []


def test_a_platform_machine_is_granted_it_once(monkeypatch, tmp_path):
    conf = _machine(monkeypatch, tmp_path, allow=False)
    calls = []

    def fake_sudo(command, failure):
        calls.append(command)
        conf.write_text(conf.read_text() + "user_allow_other\n")
        return ""

    monkeypatch.setattr(bootstrap, "sudo", fake_sudo)

    assert bootstrap.fuse_is_mountable(platform_machine=True) is True
    assert len(calls) == 1
    # Through `sh`, so the read and the append are one step and two rooms
    # starting at once cannot add the line twice.
    assert calls[0][0] == "sh"
    assert "user_allow_other" in calls[0][-1]


def test_a_machine_that_refuses_root_is_left_alone(monkeypatch, tmp_path):
    """`sudo -n` on a machine that will not give it: the view is skipped."""
    _machine(monkeypatch, tmp_path, allow=False)

    def refuse(command, failure):
        raise RuntimeError(failure)

    monkeypatch.setattr(bootstrap, "sudo", refuse)

    assert bootstrap.fuse_is_mountable(platform_machine=True) is False


def test_no_dev_fuse_is_no_view(monkeypatch, tmp_path):
    calls = []
    _machine(monkeypatch, tmp_path, fuse=False, allow=True)
    monkeypatch.setattr(bootstrap, "sudo", lambda *a, **k: calls.append(a))

    assert bootstrap.fuse_is_mountable(platform_machine=True) is False
    assert calls == []


def test_no_fusermount_is_no_view(monkeypatch, tmp_path):
    calls = []
    _machine(monkeypatch, tmp_path, fusermount=False, allow=True)
    monkeypatch.setattr(bootstrap, "sudo", lambda *a, **k: calls.append(a))

    assert bootstrap.fuse_is_mountable(platform_machine=True) is False
    assert calls == []
