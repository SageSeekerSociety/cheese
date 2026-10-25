"""Keep this session's transcript readable where the build says it is.

Claude Code writes a session's transcript on the *session host* and, after a
compaction, tells the model to read that file for detail — naming a path on
that host. The model's tools run on the *executor*, a different machine, where
that path does not exist, so the read fails and the model is sent looking for
a file that was never there.

This helper closes that gap. It runs on the executor but *outside* the room's
sandbox, where `/dev/fuse` and enough privilege are: it serves the transcript
read-only through `forwarded_transcript` — the bytes come from the session
host through the platform (`topic_transcript_chunks`) — and bind-mounts that
view at exactly the path the build named. A sandbox that starts later, or is
already running, sees it: bubblewrap shows the machine read-only and mount
propagation carries what is added to that tree afterwards.

The path is not known when the sandbox starts — it names the session, which
does not exist yet — so this asks the platform until it answers
(`RemoteClient.transcript_path`), and mounts there the first time it is told.
Which file that is was never this helper's to decide: the platform answers
with this session's own path, from this session's own credential.

Started by `bootstrap`, one per executor (`configure_idle`), stdlib and the
`fuse.py` this release ships beside it.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

#: How often to ask the platform where the transcript is, until it answers.
ASK_EVERY_S = 15.0
#: How long the view has to come up before this says it did not.
MOUNT_S = 10.0
#: What the view serves, as a name under the directory it is mounted at.
VIEW_FILE = "transcript.jsonl"
#: Where a helper records what it is holding: its own pid and every mount it
#: made. Written beside it in the room's platform directory, read by the
#: teardown (`resource_cleanup.stop_transcript_view`), which has to release the
#: view before the room's home can be removed and end a helper the executor
#: never knew about.
RECORD = "transcript-view.json"


def _run(command, *, timeout=30.0):
    """`command` run quietly; whether it succeeded. What it prints is dropped:
    this runs before anything of the room's is up, and a room that cannot be
    given the file still runs."""
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _read_mountinfo():
    """The kernel's list of what is mounted, or None where there is no /proc."""
    try:
        return Path("/proc/self/mountinfo").read_text()
    except OSError:
        return None


def _mounted(path):
    """Whether anything is mounted at `path` — live or dead.

    Not `os.path.ismount`, which is wrong exactly where it matters. A mount
    whose server died still occupies the path, and every stat on it fails; the
    failure is EIO (`Input/output error`) — the kernel's "Transport endpoint is
    not connected" never reaches it — and `ismount` swallows whatever it is and
    answers "nothing is mounted". This helper would then try to mount over a
    path that cannot take one, and report the room given the file when it was
    not. The kernel's own list says what is true of both.
    """
    listed = _read_mountinfo()
    if listed is None:
        # No /proc (macOS, Windows): nothing here can leave a dead mount, so
        # the ordinary answer is the right one.
        return os.path.ismount(path)
    real = os.path.realpath(path)
    for line in listed.splitlines():
        fields = line.split()
        if len(fields) > 4 and _unescaped(fields[4]) == real:
            return True
    return False


#: How the kernel writes a space, tab, newline and backslash in a mount
#: point: octal, three digits. A path the session host named can contain a
#: space, so the field is read back rather than compared as written.
_MOUNT_ESCAPES = {"\\040": " ", "\\011": "\t", "\\012": "\n", "\\134": "\\"}


def _unescaped(field):
    for escaped, plain in _MOUNT_ESCAPES.items():
        field = field.replace(escaped, plain)
    return field


def _live(path):
    """Whether the view at `path` answers. A mount whose server is gone still
    occupies the path and refuses every stat, so `_mounted` alone would read it
    as done and never mount the working one over it."""
    try:
        os.stat(path)
    except OSError:
        return False
    return True


def _record(record, mounts):
    """Write down what this helper is holding, so the teardown can undo it.

    Written through a temporary beside it: the teardown reads this to find the
    mounts, and a half-written file would release none of them.
    """
    if not record:
        return
    try:
        temporary = Path(str(record) + ".next")
        temporary.write_text(
            json.dumps({"pid": os.getpid(), "mounts": [str(m) for m in mounts]})
        )
        temporary.replace(record)
    except OSError:
        pass


def _remount(view_file, path):
    """`view_file` bound at `path`, its parent chain made first. Whether the
    view answers there afterwards.

    `path` is the session host's own spelling, so its parents do not exist
    here and are this helper's to make. The first of them under the owner's
    home is writable by this process; one under `/home` itself is root's, and
    the machine a room runs on gives this helper `sudo -n` for it. The bind is
    made by root for the same reason — and the view is mounted `allow_other`
    (`_serve`) so root, and every account that reads it, may reach what the
    owner mounted. A path whose chain cannot be made is left alone and
    reported, never retried into a loop.
    """
    if _mounted(path):
        if _live(path):
            return True
        # A mount whose server died still occupies the path and cannot be
        # mounted over, so without this a helper that restarted can never take
        # the place of the one before it.
        _run(["fusermount", "-uz", path], timeout=10.0)
        _run(["sudo", "-n", "fusermount", "-uz", path], timeout=10.0)
    parent = str(Path(path).parent)
    if not any(
        _run(command)
        for command in (
            ["mkdir", "-p", parent],
            ["sudo", "-n", "mkdir", "-p", parent],
        )
    ):
        return False
    if not Path(path).exists() and not any(
        _run(command) for command in (["touch", path], ["sudo", "-n", "touch", path])
    ):
        return False
    return any(
        _run(command)
        for command in (
            ["sudo", "-n", "mount", "--bind", str(view_file), path],
            ["mount", "--bind", str(view_file), path],
        )
    )


def _serve(view_dir, client):
    """The view itself, in this process, until this process ends.

    Mounted `allow_other`, because the readers are not the mounter: the bind
    at the build's path is root's (`_remount`), and the room's own tools read
    it inside the sandbox as the machine's user. Without it FUSE answers them
    EACCES — only the mounting process may look — and the file is as
    unreachable as it was. That needs `user_allow_other` in `/etc/fuse.conf`,
    which the host's install writes beside `fuse` itself
    (`deploy/deploy-docker.sh`)."""
    # deferred-import: deferred: bare names, shipped beside this file
    from forwarded_transcript import TranscriptView
    from fuse import FUSE, Operations

    class Serve(TranscriptView, Operations):
        pass

    FUSE(
        Serve(lambda params: client.transcript_chunk(params)),
        view_dir,
        foreground=True,
        ro=True,
        nothreads=False,
        direct_io=True,
        attr_timeout=0,
        entry_timeout=0,
        negative_timeout=0,
        allow_other=True,
    )


def main(view_dir, *, ask_every_s=ASK_EVERY_S):
    """Serve this session's transcript and bind it where the build names it.

    The credential and the room come from the environment this process is
    started with — the room's own scoped environment (`bootstrap`), the same
    one its tools get — so nothing here names a file, and the platform answers
    with this session's own transcript or nothing.
    """
    try:
        # deferred-import: fusepy needs libfuse, which is not installed
        # everywhere; a machine without it keeps the transcript at the path it
        # always was — unreachable — rather than failing the room.
        import fuse  # noqa: F401
    except ImportError as error:
        print(f"transcript view not served: {error}", file=sys.stderr)
        return 0

    # deferred-import: deferred: bare name, shipped beside this file
    from client import RemoteClient

    client = RemoteClient({})
    Path(view_dir).mkdir(parents=True, exist_ok=True)
    threading.Thread(target=_serve, args=(view_dir, client), daemon=True).start()
    deadline = time.monotonic() + MOUNT_S
    while not _live(view_dir) and time.monotonic() < deadline:
        time.sleep(0.05)
    if not _live(view_dir):
        print("transcript view did not come up", file=sys.stderr)
        return 0

    view_file = Path(view_dir) / VIEW_FILE
    record = Path(view_dir).with_name(RECORD)
    # The view's own directory is a mount from the moment the server came up,
    # and it lives inside the room's home. Written down with nothing else, it
    # is what a teardown can still release when this helper never bound a
    # transcript at all — a room torn down before its first turn, or a machine
    # that refused the bind. Without it the teardown finds the process and its
    # mount nowhere, and the mount stays over a home that cannot be removed.
    mounted = [str(view_dir)]
    _record(record, mounted)
    while True:
        try:
            path = client.transcript_path()
        except Exception as error:  # the session has not written one yet
            print(f"transcript path not known yet: {error!r}", flush=True)
            path = None
        if path and path not in mounted and _remount(view_file, path):
            mounted.append(path)
            _record(record, mounted)
            print(f"transcript readable at {path}", flush=True)
        time.sleep(ask_every_s)


def release(view_dir, mounted=()):
    """Unmount the view and everything bound from it, so a helper starting over
    can mount its own and the room's home can be removed."""
    for path in (*mounted, view_dir):
        _run(["fusermount", "-u", str(path)], timeout=10.0)
        _run(["fusermount", "-uz", str(path)], timeout=10.0)


if __name__ == "__main__":
    view = sys.argv[1]

    def stop(*_):
        record = Path(view).with_name(RECORD)
        try:
            held = json.loads(record.read_text()).get("mounts") or ()
        except (OSError, ValueError):
            held = ()
        release(view, held)
        record.unlink(missing_ok=True)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    raise SystemExit(main(view))
