"""The namespace a session is entered into, where the project sits at the
executor's path (`enter`).

Claude Code prints its working directory — in what it tells the model, in a
refusal to run a command, when it resets the shell's directory — so a session
runs in a user and mount namespace of its own, with this host's view of the
project mounted where the executor holds it. Shipped beside `client.py`
(`release.sources`), which runs `enter` for its `enter` mode.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import shutil
import sys
from pathlib import Path

# The namespace the session runs in (`enter`): mount(2), umount2(2) and
# pivot_root(2) flags and numbers, which the standard library does not name.
_MS_NOSUID, _MS_NODEV = 2, 4
_MS_BIND, _MS_REC, _MS_PRIVATE = 4096, 16384, 1 << 18
_CLONE_NEWNS, _CLONE_NEWUSER = 0x00020000, 0x10000000
_MNT_DETACH = 2
_SYS_PIVOT_ROOT = {"x86_64": 155, "aarch64": 41}


def enter(target_path, argv):
    """Run the session with the project at the executor's own path.

    Claude Code prints its working directory — in what it tells the model
    about the session, in a refusal to run a command, when it resets the
    shell's directory — and what it prints has to be what it prints running on
    the executor. So the session gets a user and mount namespace of its own,
    in which this host's view of the project is mounted at exactly the path
    the executor holds it at, and runs there. Nothing else in it moves: every
    other path is this host's own, bound into place. The directories made to
    lead down to the project are the session's own: what is already in them
    is this host's, and what the session creates directly in one (a socket in
    `/tmp` when the project is under it) stays in the session, as in a private
    `/tmp`.

    A path that cannot be placed so is refused, loudly: one that is not an
    absolute path, one under the kernel's own filesystems, and one that would
    cover something the session itself needs.
    """
    target = json.loads(Path(target_path).read_text())
    seen = target["session_workspace"]
    view = target["central_workspace"]
    program = shutil.which(argv[0])
    if not program:
        raise SystemExit(f"{argv[0]}: not found")
    program = os.path.abspath(program)
    needed = [
        str(Path(target_path).parent),
        target["central_config"],
        target["central_tmp"],
        os.environ.get("HOME", ""),
        str(Path(__file__).resolve().parent),
        sys.executable,
        program,
        view,
    ]
    problem = _unplaceable(seen, needed)
    if problem:
        raise SystemExit(
            f"This session cannot see its project at {seen!r}, the executor's "
            f"path: {problem}"
        )
    _place(seen, view, Path(target_path).parent / "namespace-root")
    tasks = target.get("session_task_root")
    if tasks:
        # The task checkouts' directory, empty: commands run on the executor,
        # and the build needs only the directory a command ends in to exist
        # here (`shell_prefix._report_directory` makes each one). It keeps a
        # directory given at launch only if it exists then. One this namespace
        # cannot make leaves the shell reset to the project, as before.
        with contextlib.suppress(OSError):
            os.makedirs(tasks, exist_ok=True)
    os.chdir(seen)
    os.environ["PWD"] = seen
    os.execv(program, argv)


def namespace_task_root(seen, task_root):
    """`task_root`, the executor's task checkouts directory, if the session's
    namespace can hold it as a directory of its own; otherwise None.

    `enter` makes it, and the shell prefix makes each checkout in it, so it
    has to come out of the session's own tmpfs: where it parts from the way
    down to `seen`, nothing of this host may be there already, or what is made
    would be made in this host's directory, bound in. The executor reports the
    path, so it is checked as `seen` is."""
    if (
        not task_root.startswith("/")
        or task_root.startswith("//")
        or os.path.normpath(task_root) != task_root
        or task_root == "/"
        or task_root.split("/")[1] in ("proc", "sys", "dev")
    ):
        return None
    common = os.path.commonpath([seen, task_root])
    if common == task_root or task_root.startswith(seen + "/"):
        return None  # Inside the project, or above it.
    parting = task_root[len(common) :].lstrip("/").split("/")[0]
    if os.path.lexists(os.path.join(common, parting)):
        return None
    return task_root


def _unplaceable(seen, needed):
    """Why `seen` cannot hold the project in the session's namespace, or None."""
    if sys.platform != "linux":
        return "the session host must be Linux, for its user and mount namespaces"
    if not seen.startswith("/") or seen.startswith("//"):
        return "it is not an absolute path"
    if os.path.normpath(seen) != seen or seen == "/":
        return "it is not a normalized path below /"
    if seen.split("/")[1] in ("proc", "sys", "dev"):
        return "it is under a kernel filesystem"
    for path in needed:
        for spelled in {path, os.path.realpath(path)} if path else ():
            if spelled == seen or spelled.startswith(seen + "/"):
                return f"it would cover {spelled}, which the session needs"
    return None


def _place(seen, view, root):
    """Enter a user and mount namespace whose root is this host's, with `view`
    mounted at `seen`.

    The new root is a tmpfs holding this host's top-level entries, each bound
    into place. Every directory on the way down to `seen` is rebuilt the same
    way, with its other entries bound in and the next one made anew, since an
    unprivileged namespace can mount over a directory but not create one in a
    directory it does not own. The user namespace maps this user to itself,
    so files keep their owner, and the capabilities it grants end at exec.
    """

    libc = ctypes.CDLL(None, use_errno=True)

    def check(result, what):
        if result != 0:
            number = ctypes.get_errno()
            # OSError(errno, ...) is the subclass the errno names, so a source
            # that vanished is a FileNotFoundError.
            raise OSError(number, f"{what}: {os.strerror(number)}")

    def mount(source, place, kind, flags, data=None):
        check(
            libc.mount(
                source.encode() if source else None,
                place.encode(),
                kind.encode() if kind else None,
                flags,
                data.encode() if data else None,
            ),
            f"mount {place}",
        )

    uid, gid = os.getuid(), os.getgid()
    root = str(root)
    os.makedirs(root, exist_ok=True)
    check(libc.unshare(_CLONE_NEWUSER | _CLONE_NEWNS), "unshare")
    for name, value in (
        ("setgroups", "deny"),
        ("uid_map", f"{uid} {uid} 1"),
        ("gid_map", f"{gid} {gid} 1"),
    ):
        with open(f"/proc/self/{name}", "w") as stream:
            stream.write(value)
    mount(None, "/", None, _MS_REC | _MS_PRIVATE)
    # Bounded: it is memory, on the host every session runs on.
    mount("tmpfs", root, "tmpfs", _MS_NOSUID | _MS_NODEV, "mode=755,size=64m")

    def rebuild(real, copy, skip):
        # A bind that is not recursive is refused in a user namespace when the
        # directory has mounts under it, so every bind here is recursive.
        for entry in os.scandir(real):
            if entry.name == skip:
                continue
            place = os.path.join(copy, entry.name)
            try:
                if entry.is_symlink():
                    os.symlink(os.readlink(entry.path), place)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    os.mkdir(place)
                else:
                    open(place, "w").close()
                mount(entry.path, place, None, _MS_BIND | _MS_REC)
            except FileNotFoundError:
                # Gone since it was listed (a busy /tmp): not there to show.
                if os.path.isdir(place) and not os.path.islink(place):
                    os.rmdir(place)
                elif os.path.lexists(place):
                    os.unlink(place)

    names = seen.strip("/").split("/")
    real, copy = "/", root
    for name in names:
        if real is not None:
            rebuild(real, copy, name)
        copy = os.path.join(copy, name)
        os.mkdir(copy)
        below = os.path.join(real, name) if real is not None else None
        # A directory reached through a symlink (`/lib` on a merged-/usr
        # system) is rebuilt from where it leads, so nothing in it goes missing.
        real = (
            os.path.realpath(below)
            if below is not None and os.path.isdir(below)
            else None
        )
    mount(view, copy, None, _MS_BIND | _MS_REC)
    old = os.path.join(root, ".host-root")
    os.mkdir(old)
    os.chdir(root)
    number = _SYS_PIVOT_ROOT.get(os.uname().machine)
    if number is None:
        raise OSError(f"pivot_root: no syscall number for {os.uname().machine}")
    check(libc.syscall(number, b".", b".host-root"), "pivot_root")
    os.chdir("/")
    check(libc.umount2(b"/.host-root", _MNT_DETACH), "umount /.host-root")
    os.rmdir("/.host-root")
