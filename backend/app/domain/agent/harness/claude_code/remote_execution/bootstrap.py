"""Prepare a room executor using platform helpers, without starting a model."""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

if sys.platform != "win32":
    import fcntl

VERSION = "2.1.299"
# The platform's own directory inside a room's home, and the one it used before.
# A harness's config dir is the harness's; everything the platform installs —
# the executor, its helpers, the CLI, the environment runner — lives here.
# A copy of `place.session_platform_dirs()`, not a second answer: this is exec'd
# on the machine out of a string, so it cannot import the original; the copy is
# held to it by test_footprint_root.py.
PLATFORM_DIR = ".cheese"
PREVIOUS_PLATFORM_DIR = ".claude"

# The checkout this room's agent works in, inside the room's home. A copy of
# `place.CHECKOUT_DIR` for the same reason as the pair above — this file is
# exec'd on the machine out of a string — and held to it by test_footprint_root.py.
# This is the side that CREATES the directory, so drift from the platform's own
# name for it would leave the platform writing into a checkout it believes it
# is staying out of.
CHECKOUT_DIR = "room"

# The room's Claude config directory inside its home, where the platform's
# skills are planted (`plant_native_skills`) and `CLAUDE_CONFIG_DIR` points.
CONFIG_DIR = ".claude"

# Which rooms run in a sandbox, under the machine's footprint (`record_sandbox`).
# A copy of `place.SANDBOXES_DIR` for the same reason as the names above, held
# to it by test_footprint_root.py.
SANDBOXES = "sandboxes"


def process_servers(config):
    """The `.mcp.json` servers this machine runs: the stdio ones. An entry with
    a `url` is a remote server, which the platform calls itself with the
    project's credential (`app.domain.remote_mcp`) — not something to start
    here, and not something to bridge to here."""
    return [
        name
        for name, spec in config["mcp_servers"].items()
        if not (isinstance(spec, dict) and isinstance(spec.get("url"), str))
    ]


class SandboxUnavailable(RuntimeError):
    """This machine cannot give the room the isolated environment it runs in.
    `key` names the sentence its owner is shown, in their language, about what
    would let it (the frontend's `apiError` catalog); `params` fill it."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.said = {"key": key, "params": params}


# How the install exits when it refuses a room for `SandboxUnavailable`, with
# its sentence as the last line of stderr, as JSON: the backend keeps it for
# the owner.
SANDBOX_UNAVAILABLE_EXIT = 78


class UpgradeDeferred(Exception):
    def __init__(self, info):
        self.info = info


# A write refused for want of space: the disk's, or on a platform host the
# room's own quota (`sandbox_host.py`), which XFS also answers ENOSPC.
FULL = {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}


def free_mb(path):
    """What is left at `path`, in MB: by its own quota where it has one, as
    a `statvfs` of a directory in an XFS project answers."""
    if hasattr(os, "statvfs"):
        found = os.statvfs(path)
        return found.f_bavail * found.f_frsize // 10**6
    return shutil.disk_usage(path).free // 10**6


def spared(disk, home, write):
    """Run `write`, which the executor can do without (skills, the preview
    token, the environment's config). Refused for want of space, it is
    skipped and what is left noted in `disk`, which the room is told
    (`session_work`): a room that filled its disk still gets its executor,
    and its agent the chance to make room."""
    try:
        write()
    except OSError as error:
        if error.errno not in FULL:
            raise
        disk["disk_full_mb"] = free_mb(home)
        print(f"skipped for want of space: {error}", file=sys.stderr)


def kept_on_full_disk(owner, home, payload, anchors, error):
    """No room to stage this payload's release: keep the running executor, as
    a busy one is kept (`UpgradeDeferred`), when it speaks this payload's
    protocol and runs as this payload asks, sandboxed or not and with the
    same limits. Whether it is sandboxed and its release are read from the
    record made outside the room (`record_sandbox`), never from the config a
    sandboxed room writes, and the release must be one of the store's.
    Raises `error` when there is none to keep."""
    marker = execution_root(owner) / SANDBOXES / home.parent.name / home.name
    try:
        previous = read_json_beneath(home, Path(PLATFORM_DIR) / "executor/config.json")
        if previous is None:
            raise error
        named = marker.read_text() if marker.exists() else previous["release"]
    except (OSError, ValueError, TypeError, KeyError):
        raise error from None
    sandbox = payload.get("sandbox") or False
    if marker.exists() != bool(sandbox) or previous.get("sandbox", False) != sandbox:
        raise error
    release = Path(named.strip())
    if (
        release.name in ("", ".", "..")
        or release.parent != release_store(owner)
        or not release.is_dir()
    ):
        raise error
    state = executor_state(home, anchors, bool(payload.get("sandbox")))
    runtime = runpy.run_path(str(release / "remote-execution/runtime.py"))
    try:
        info = runtime["request"](state, "ping")
    except (ConnectionError, FileNotFoundError):
        raise error from None
    if info.get("protocol_version", 1) != payload.get("protocol_version", 1):
        raise error
    refreshed = {**previous.get("env", {}), **payload["env"]}
    runtime["request"](
        state,
        "configure",
        {"env": refreshed, "env_reset": previous.get("env_reset", {})},
    )
    info.update(
        state=str(home / PLATFORM_DIR / "executor"),
        mcp_servers=process_servers({"mcp_servers": previous.get("mcp_servers", {})}),
        upgrade_pending=True,
        desired_release=None,
        # What ran out is where releases are staged, not the room's own.
        disk_full_mb=free_mb(release_store(owner)),
    )
    return info


def predecessor(release):
    """The executor that already owns a room's state, as an install sees it."""
    return runpy.run_path(str(Path(release) / "remote-execution/predecessor.py"))


def lock(file):
    """flock(LOCK_EX). This file arrives on stdin before any release is on disk,
    so it cannot load portable.py for the Windows lock; this is the same one."""
    if sys.platform == "win32":
        import msvcrt  # deferred-import: Windows-only module; absent on other platforms

        while True:
            os.lseek(file.fileno(), 0x7FFFFFF0, os.SEEK_SET)
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.05)
    fcntl.flock(file, fcntl.LOCK_EX)


def portable(release):
    return runpy.run_path(str(Path(release) / "remote-execution/portable.py"))


def shipped(release, name):
    """A policy module shipped beside this file (`app/domain/agent/<name>.py`),
    read from the source tree when this runs from a checkout the way
    `runtime.project_hooks` reads its own."""
    beside = Path(release) / "remote-execution" / f"{name}.py"
    if not beside.exists():
        beside = Path(__file__).resolve().parents[6] / f"app/domain/agent/{name}.py"
    return runpy.run_path(str(beside))


def windows_shim(name, data):
    """A `.cmd` that runs an extension-less script, or None for anything else.

    Git Bash runs a script by its `#!` line; cmd.exe and CreateProcess only
    run what PATHEXT names, so beside every such script sits a shim that hands
    it to the interpreter its first line asks for, looked up on PATH.
    """
    if Path(name).suffix or not data.startswith(b"#!"):
        return None
    interpreter = data.split(b"\n", 1)[0][2:].decode().split()
    program = Path(interpreter[0]).name
    if program == "env" and len(interpreter) > 1:
        program = interpreter[1]
    return f'@"{program}" "%~dp0{Path(name).name}" %*\r\n'


def temporary_beside(destination):
    """A fresh name next to `destination`. On Windows it keeps `.exe` last,
    since that is what makes a file something CreateProcess will start."""
    name = destination.name + "." + uuid.uuid4().hex
    return destination.with_name(name + (".exe" if sys.platform == "win32" else ""))


PROJECT_SKILLS_MANIFEST = "skills/.cheese-project-skills.json"
PLATFORM_SKILLS_MANIFEST = "skills/.cheese-platform-skills.json"


def beneath(root, relative, create=False):
    """`root / relative` opened as a directory, every step taken without
    following a link, made where missing when `create`. A sandboxed room can
    put a link anywhere in its home, and the install runs outside the sandbox,
    where a link reaches the owner's files and other rooms'. None on Windows,
    where no room is sandboxed and a path is opened as it is."""
    if sys.platform == "win32":
        if create:
            (Path(root) / relative).mkdir(parents=True, exist_ok=True)
        return None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in Path(relative).parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=descriptor)
            following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def read_beneath(root, relative):
    """The text of `root / relative` reached as `beneath` reaches it."""
    relative = Path(relative)
    directory = beneath(root, relative.parent)
    if directory is None:
        return (Path(root) / relative).read_text(encoding="utf-8")
    try:
        file = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(file, encoding="utf-8") as stream:
        return stream.read()


def write_beneath(root, relative, text):
    """Write `root / relative` as `beneath` reaches it, through a temporary
    beside it: a half-written `office.py` is a script the agent is told to
    run. Readable by the owner alone, and written only where the text is not
    already there: this runs before every tool call of a room, and on a machine
    whose disk or inode supply is used up, the rename is what turns `echo ok`
    into `ENOSPC`."""
    relative = Path(relative)
    with contextlib.suppress(OSError, UnicodeDecodeError):
        if read_beneath(root, relative) == text:
            return
    directory = beneath(root, relative.parent, create=True)
    temporary = relative.name + ".next"
    if directory is None:
        path = Path(root) / relative
        path.with_name(temporary).write_text(text, encoding="utf-8")
        path.with_name(temporary).replace(path)
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=directory)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        file = os.open(temporary, flags, 0o600, dir_fd=directory)
        with os.fdopen(file, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, relative.name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        os.close(directory)


def remove_beneath(directory, name):
    """Remove `name` in the directory open as `directory`, and everything
    under it, without following a link anywhere: a link is removed, never
    what it names. Walked by descriptor so a directory the room swaps for a
    link halfway through is not entered either. Missing is already removed.

    By hand rather than `shutil.rmtree(dir_fd=...)`, which needs Python 3.11:
    this runs on whatever Python 3 the machine has."""
    try:
        child = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
        )
    except FileNotFoundError:
        return
    except OSError:
        # Not a directory, or a link to one: the entry itself goes.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name, dir_fd=directory)
        return
    try:
        for entry in os.listdir(child):
            remove_beneath(child, entry)
    finally:
        os.close(child)
    with contextlib.suppress(FileNotFoundError):
        os.rmdir(name, dir_fd=directory)


def _remove_skill(home, name):
    """Remove one skill folder from the room's Claude config directory, links
    in it as links (`remove_beneath`)."""
    if not isinstance(name, str) or not name or "/" in name or name in (".", ".."):
        return
    try:
        skills = beneath(home, Path(CONFIG_DIR) / "skills")
    except OSError:
        return
    if skills is None:
        shutil.rmtree(Path(home) / CONFIG_DIR / "skills" / name, ignore_errors=True)
        return
    try:
        remove_beneath(skills, name)
    finally:
        os.close(skills)


def _shipped_last_time(home, manifest, otherwise):
    try:
        return json.loads(read_beneath(home, manifest))
    except (OSError, ValueError):
        return list(otherwise)


def prune_project_skills(home, names, platform):
    """Remove the project skills planted last time that are no longer shipped
    from the room's Claude config directory. The platform's own folders are
    never touched here."""
    manifest = Path(CONFIG_DIR) / PROJECT_SKILLS_MANIFEST
    for name in (
        set(_shipped_last_time(home, manifest, [])) - set(names) - set(platform)
    ):
        _remove_skill(home, name)
    write_beneath(home, manifest, json.dumps(sorted(names)))


def prune_platform_skills(home, names, project, before_the_list):
    """Remove the platform skills planted last time that are no longer shipped.

    Files are only ever written here, so a retired skill would otherwise stay
    for good. A machine with no list yet is taken to have been shipped
    ``before_the_list``. A project skill of the same name stays."""
    manifest = Path(CONFIG_DIR) / PLATFORM_SKILLS_MANIFEST
    previous = _shipped_last_time(home, manifest, before_the_list)
    for name in set(previous) - set(names) - set(project):
        _remove_skill(home, name)
    write_beneath(home, manifest, json.dumps(sorted(names)))


def plant_native_skills(home, skills):
    """Write the platform's skills where this machine's shell will look for
    them, in the room's Claude config directory.

    The agent reads a skill's text, then runs the command that text gives it —
    here, on the executor machine. Everything else that installs these files
    does it beside the claude it starts (the container launch, the
    device-hosted launch); this path starts no claude at all, so nothing
    installed them and `$CLAUDE_CONFIG_DIR/skills/documents/scripts/office.py`
    named a file that was not on the machine. The room's own answer to that was
    to scavenge a copy out of another room's cache, which is not a mechanism.
    """
    for name, content in sorted(skills.items()):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Native skill file must be inside the config directory")
        write_beneath(home, Path(CONFIG_DIR) / relative, content)


def execution_root(owner):
    return owner / os.environ.get("CHEESE_PROFILE_ROOT", ".cheese")


def release_store(owner):
    """Where every room's executor releases are kept on this machine, by digest.

    Beside the rooms, never inside one: a sandboxed room can write all of its
    own home, and programs from here also run outside its sandbox — the
    install below, the teardown, the environment reset (`record_sandbox`). A
    sandbox is shown this directory read-only (`confinement.sandbox_argv`), so what is here
    is what the platform put here."""
    return execution_root(owner) / "executor-releases"


def stage_release(store, platform_dir, payload):
    contents = {
        name: (
            base64.b64decode(payload["files"][name])
            if name in payload["files"]
            else (platform_dir / name).read_bytes()
        )
        for name in payload.get("file_names", payload["files"])
    }
    digest = hashlib.sha256(
        json.dumps(
            {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    store.mkdir(parents=True, exist_ok=True)
    release = store / digest
    if not release.exists():
        staged = store / (digest + "." + uuid.uuid4().hex)
        staged.mkdir(mode=0o700)
        try:
            for name, data in contents.items():
                relative = Path(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("Executor file must be inside its release")
                path = staged / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o700)
                shim = windows_shim(name, data) if sys.platform == "win32" else None
                if shim:
                    path.with_name(path.name + ".cmd").write_text(shim)
            (staged / "executor-files.json").write_text(json.dumps(list(contents)))
        except BaseException:
            # Half a release, on a disk with no room for the whole.
            shutil.rmtree(staged, ignore_errors=True)
            raise
        try:
            staged.rename(release)
        except OSError:
            # Another room on this machine staged the same release first.
            if not release.exists():
                raise
            shutil.rmtree(staged)
    return release, contents


def activate_release(platform_dir, release, names):
    """Point the stable entrypoints in `platform_dir` at `release`; a running
    executor uses the path its config records. Each link is made beside its
    entrypoint and renamed over it, under directories reached as `beneath`
    reaches them: a sandboxed room writes its own platform directory, and a
    link it left there would otherwise carry these into another room."""
    for name in [*names, "executor-files.json"]:
        relative = Path(name)
        if sys.platform == "win32":
            destination = platform_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".next")
            temporary.unlink(missing_ok=True)
            # A symlink needs Developer Mode or an administrator there.
            shutil.copyfile(release / name, temporary)
            temporary.replace(destination)
            continue
        directory = beneath(platform_dir, relative.parent, create=True)
        temporary = relative.name + ".next"
        try:
            # Already there: a prepare that changes nothing writes nothing,
            # and so still goes through in a room whose disk is full.
            with contextlib.suppress(OSError):
                if os.readlink(relative.name, dir_fd=directory) == str(release / name):
                    continue
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory)
            os.symlink(release / name, temporary, dir_fd=directory)
            os.replace(
                temporary, relative.name, src_dir_fd=directory, dst_dir_fd=directory
            )
        finally:
            os.close(directory)


def executor_state(home, stack, sandboxed):
    """The room's executor state directory, as the path to reach its executor
    by. For a sandboxed room it is made and opened as `beneath` does and named
    through that descriptor (`/proc/self/fd`), which `stack` closes: the
    socket an executor is reached on is derived from its state's resolved
    path (`runtime.socket_directory`), and a room that swapped its state for
    a link to another room's would otherwise have this install configure,
    upgrade or stop that room's executor with this room's credential."""
    state = home / PLATFORM_DIR / "executor"
    if not sandboxed:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        return state
    descriptor = beneath(home, Path(PLATFORM_DIR) / "executor", create=True)
    stack.callback(os.close, descriptor)
    if sys.platform == "darwin":
        # No /proc on macOS: the directory the descriptor holds, by the path
        # the kernel resolved when it was opened. A room that turns a
        # directory above it into a link afterwards is not stopped here as it
        # is on Linux; on a person's own Mac the other rooms are theirs too.
        import fcntl  # deferred-import: POSIX-only module; absent on Windows

        named = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
        return Path(named.split(b"\0", 1)[0].decode())
    return Path(f"/proc/self/fd/{descriptor}")


def held(state):
    """The descriptor an `executor_state` path names, for a child to keep."""
    if str(state).startswith("/proc/self/fd/"):
        return (int(Path(state).name),)
    return ()


def read_json_beneath(root, relative):
    """The JSON at `root / relative` as `read_beneath` reads it, or None."""
    try:
        return json.loads(read_beneath(root, relative))
    except FileNotFoundError:
        return None


def identity(path):
    """What the pin was when it last proved its version. Replacing it, writing
    it in place or touching it each change this."""
    stat = path.stat()
    return [
        VERSION,
        stat.st_dev,
        stat.st_ino,
        stat.st_mode,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    ]


def remember(destination, record):
    """Keep the proof that `destination` runs as VERSION, beside it."""
    temporary = temporary_beside(record)
    try:
        temporary.write_text(json.dumps(identity(destination)))
        temporary.replace(record)
    except OSError:
        # A sandboxed executor is shown this directory read-only. The install
        # outside its sandbox recorded the pin before starting it.
        temporary.unlink(missing_ok=True)


def binary(owner, api, release):
    windows = sys.platform == "win32"
    executable = ".exe" if windows else ""
    destination = owner / ".cheese/claude/versions" / (VERSION + executable)
    # Every room preparation asks for the pin, and a run of it is a 240 MB
    # program paged in on the room's own machine, which the room may be keeping
    # busy: under a full test suite on two cores a run outlasted 15 s. So the
    # pin is run once, and what it was then is kept beside it.
    record = destination.with_name(destination.name + ".verified")
    try:
        if json.loads(record.read_text()) == identity(destination):
            return str(destination)
    except (OSError, ValueError):
        pass
    candidates = [destination, owner / ".local/bin" / ("claude" + executable)]
    installed = shutil.which("claude")
    if installed:
        candidates.append(Path(installed))
    for candidate in candidates:
        # Every file passes X_OK on Windows, and what npm installs there is a
        # `claude.cmd` over node: only the binary itself is worth copying.
        if windows and candidate.suffix.lower() != ".exe":
            continue
        if candidate.is_file() and os.access(candidate, os.X_OK):
            result = subprocess.run(
                [str(candidate), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.split()[0] == VERSION:
                if candidate != destination:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = temporary_beside(destination)
                    shutil.copyfile(candidate, temporary)
                    temporary.chmod(0o700)
                    temporary.replace(destination)
                remember(destination, record)
                return str(destination)
    # Warm room preparation needs neither download handling nor TLS setup.
    import platform  # deferred-import: warm-room prep skips this

    architecture = {
        "x86_64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
        # What Windows calls them.
        "AMD64": "x64",
        "ARM64": "arm64",
    }[platform.machine()]
    system = {"Linux": "linux", "Darwin": "darwin", "Windows": "win32"}[
        platform.system()
    ]
    target = f"{system}-{architecture}"
    if system == "linux" and platform.libc_ver()[0] == "musl":
        target += "-musl"
    # Shipped beside the runtime (`runtime_core.RELEASE_FILES`). One install at
    # a time on this machine, whoever started it — another room, a launch, the
    # desktop app fetching it ahead — and one that waited uses what it placed.
    downloader = Path(release) / "remote-execution/executor_download.py"
    runpy.run_path(str(downloader))["install"](
        f"{api}/connector/claude/{VERSION}/{target}/claude{executable}",
        destination,
        VERSION,
    )
    remember(destination, record)
    return str(destination)


def stop_previous_root(home, release):
    """Take down an executor the platform started under its previous directory.

    The executor is a detached daemon: it outlives the screen that asked for it,
    and nothing that closes a session closes it. So a room's executor survives a
    change of install root, and the code that would otherwise stop it — the
    "already running" branch below — looks for its state under the new root and
    finds nothing. Two daemons then share one HOME and one environment status
    file. Whoever moves the root has to come back for what the old one started.
    """
    previous = home / PREVIOUS_PLATFORM_DIR
    state = previous / "executor"
    # The previous root is also the room's Claude config directory, which a
    # sandboxed room writes: a link there would have this stop and delete
    # another room's executor.
    if previous.is_symlink() or state.is_symlink():
        return
    if not (state / "config.json").exists():
        return
    # Asked with the release being installed now, never with the programs the
    # old root holds: those sit in the room's home, which a sandboxed room
    # writes, and this runs outside its sandbox. The stop it sends is one every
    # executor answers (`runtime.terminate_unrequested`). Ask whether it is
    # there before telling it to go — a room whose machine rebooted has this
    # state on disk with nothing behind it, and that is not a reason to refuse
    # the room. A stop that is asked for and fails IS a reason: the whole point
    # is that two of these must not run over one home.
    runner = release / "remote-execution/runtime.py"
    runtime = runpy.run_path(str(runner))
    try:
        alive = runtime["request"](state, "ping")
    except (OSError, RuntimeError):
        alive = None
    if alive:
        subprocess.run(
            [sys.executable, str(runner), "stop", "--state", str(state)],
            check=True,
            timeout=30,
        )
    shutil.rmtree(state, ignore_errors=True)
    # Every marker that says a room is installed here goes with it. What stays
    # under the previous root is inert copies of programs; anything that still
    # ANSWERS "the executor is over here" would go on being believed, by the
    # teardown path most of all — it reads these to find what to stop.
    (previous / "execution-owner.json").unlink(missing_ok=True)
    (previous / "remote-target.json").unlink(missing_ok=True)


def platform_file(platform_dir, name):
    """A file of the platform's in a room's own directory, opened to append,
    and never through a link. A sandboxed room can put a link anywhere in its
    home, and this runs outside the sandbox, where a link reaches the owner's
    files: the executor's output appended to `~/.ssh/authorized_keys` would be
    a way out."""
    if sys.platform == "win32":
        return (platform_dir / name).open("a")
    directory = os.open(platform_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        return os.fdopen(os.open(name, flags, 0o600, dir_fd=directory), "a")
    finally:
        os.close(directory)


#: Where `transcript_view.py` mounts this session's transcript, before it is
#: bound at the path the build names. Beside the room's own platform directory,
#: so the mount belongs to this room and the teardown can find it.
TRANSCRIPT_VIEW = "transcript-view"
#: The FUSE devices and config a machine needs before a view can be mounted,
#: and the file the `user_allow_other` line goes in (`fuse_is_mountable`).
DEV_FUSE = "/dev/fuse"
FUSE_CONF = "/etc/fuse.conf"


def start_transcript_view(home, release, config, platform_machine):
    """Serve this session's transcript out here, and bind it where the build
    names it (`transcript_view`). Started before the sandbox, and never waited
    for: a machine without FUSE, or a session whose transcript is not written
    yet, keeps the transcript at the path it always was — unreachable — and
    the room runs exactly as it did.

    Out here rather than inside, because a bubblewrap sandbox has no
    `/dev/fuse` of its own: the view is this side's, and the sandbox reads it
    through the machine's own read-only `/home`. It is given the room's scoped
    environment, which is what names the room and carries the credential the
    relay is asked with — the same environment the room's tools get.

    Started wherever the room is, not only where it is sandboxed: a room given
    the whole machine runs its tools here too, and the transcript is on the
    session host either way. What the view needs is FUSE, not a sandbox."""
    view = home / PLATFORM_DIR / TRANSCRIPT_VIEW
    script = release / "remote-execution/transcript_view.py"
    if not script.exists():
        return
    if not fuse_is_mountable(platform_machine):
        return
    with platform_file(home / PLATFORM_DIR, "transcript-view.log") as log:
        subprocess.Popen(
            [sys.executable, str(script), str(view)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env={**os.environ, **config["env"], **transcript_view_env()},
            start_new_session=True,
        )


def fuse_is_mountable(platform_machine):
    """Whether this machine can mount a FUSE view at all, making it able where
    this may.

    Three things the view needs that a machine need not have: `/dev/fuse`, a
    `fusermount` (the library calls it by name to mount), and `user_allow_other`
    in `/etc/fuse.conf` — the view is mounted by this room's user and read by
    root through a bind, and FUSE refuses a second reader without it.

    The library itself is not checked here: it is handed over as a path
    (`transcript_view_env`) from the copy cheesehost places (`devenv.Ensure`
    places `fuse-runtime`, pinned in `agent/toolchain.py`), and a machine
    without it fails into the view's own silent exit.

    The `user_allow_other` line is written as root where `platform_machine`
    says the platform owns the machine. That line used to be installed by
    `deploy/deploy-docker.sh` on the box that runs a deploy — which is the
    *session* host, and the view is never mounted there: the executor is a pool
    host, a machine the deploy script never touches. So it belongs here, on the
    machine that mounts, and only on one the platform provisioned. A machine a
    person enrolled is left alone: without the line the view is simply not
    mounted and the room runs as it always did."""
    if not Path(DEV_FUSE).exists():
        return False
    if shutil.which("fusermount") is None and shutil.which("fusermount3") is None:
        return False
    if _user_allow_other():
        return True
    if not platform_machine:
        return False
    return _grant_user_allow_other()


def _user_allow_other():
    try:
        text = Path(FUSE_CONF).read_text()
    except OSError:
        return False
    return any(
        line.strip() == "user_allow_other"
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )


def _grant_user_allow_other():
    """Append `user_allow_other`, as root, where the platform owns the machine.

    Through `sudo -n`, like every other root step here (`sandbox_tools`): a
    machine that does not give this process root answers with an error rather
    than a password prompt, and the room keeps running without the view. The
    line is appended only when it is absent, read back through `sh` so there is
    no window in which two rooms both decide to add it.

    `subprocess.SubprocessError` is caught with the rest: `sudo` runs with a
    `timeout`, and a root call that hangs raises `TimeoutExpired` — not an
    `OSError` — out of a step that runs on every room start and must never be
    what fails one. The view is optional; the room is not."""
    try:
        sudo(
            [
                "sh",
                "-c",
                "grep -qxF user_allow_other /etc/fuse.conf 2>/dev/null"
                " || echo user_allow_other >> /etc/fuse.conf",
            ],
            "allowing the transcript view to be read",
        )
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False
    return _user_allow_other()


def transcript_view_env():
    """What the view needs in its environment to find FUSE on this machine.

    `fusepy` looks for `libfuse` through `find_library`, which asks the loader
    for a name in the machine's own library paths — and `ldconfig` is root's to
    change. So a host the platform did not provision has no way to be given it
    there, and this is the one that works without root: `FUSE_LIBRARY_PATH`,
    which `fusepy` honours ahead of the search, pointed at the copy cheesehost
    placed under the platform's own runtime. A machine that has neither the
    runtime nor a system libfuse gets nothing here, and the view's own import
    fails into its silent exit (`transcript_view.main`).

    Nothing else comes from here. `fusermount` is found on PATH and `/dev/fuse`
    is the kernel's; the runtime supplies only the library."""
    library = placed_libfuse()
    return {"FUSE_LIBRARY_PATH": str(library)} if library else {}


def placed_libfuse():
    """The placed `libfuse.so.2`, or None.

    Under the platform's runtime beside bwrap and the host tools, which is
    where cheesehost places what a machine lacks (`cli/internal/devenv`): one
    directory per tool, the archive's own top level removed, so the file is
    directly inside. A copy built for another architecture would not load, but
    the directory is per machine and cheesehost places the one for the machine
    it runs on, so its own name is the whole lookup.

    `devenv.Ensure` places it beside those two, from the release
    `agent/toolchain.py` pins; a machine that could not fetch it answers None
    here, which is the same path as a machine that has no FUSE at all."""
    root = execution_root(Path.home()) / "runtime"
    candidate = root / "fuse-runtime" / "libfuse.so.2"
    return candidate if candidate.exists() else None


# Where `sandbox_host.py` is installed, root's and outside every release: the
# connector user runs it through `sudo -n`, and only root may change it.
SANDBOX_HOST = "/usr/local/libexec/cheese-sandbox"
# Root's copies of the programs it runs, from the host tools cheesehost places
# (`cli/internal/devenv`): it runs nothing the connector user could replace. A
# copy of `sandbox_host.TOOLS`, held to it by test_footprint_root.py.
SANDBOX_TOOLS = "/usr/local/libexec/cheese-sandbox-tools"
HOST_TOOLS = ("ip", "xtables-nft-multi", "xfs_quota")


def sudo(command, failure):
    result = subprocess.run(
        ["sudo", "-n", *command],
        capture_output=True,
        text=True,
        timeout=300,
        env=os.environ,
    )
    if result.returncode:
        raise RuntimeError(failure + ": " + result.stderr.strip()[-500:])
    return result.stdout


# The sentence for a system with no isolated environment for a room. The
# machine says the same when asked as it connects (`device.sandbox`), and the
# backend refuses such a room before it gets here. A system gains an
# environment by leaving this table in the same change that teaches
# `sandbox_tools` and `confinement.sandbox_argv` to build it.
NO_SANDBOX = {"win32": "sandboxNoneOnWindows"}


def sandbox_tools(release, platform_machine):
    """Where `bwrap` is, once the machine has what a sandbox needs; asked
    before anything of the room's is touched. On macOS, `sandbox-exec`
    instead (`confinement.start_seatbelt`).

    Other systems have no isolated environment yet (`NO_SANDBOX`). On Linux
    the sandbox is made with the bwrap cheesehost places (`placed_bubblewrap`),
    whatever the system has. A machine the platform provisioned
    (`platform_machine`) also needs this release's `sandbox_host.py` as
    `SANDBOX_HOST` and root's copies of the host tools it runs, which
    cheesehost placed beside bwrap (`SANDBOX_TOOLS`), installed here: nothing
    comes from the system's packages. A machine a person
    enrolled is theirs: nothing is installed there, its sandbox has no network
    or limits of its own (that takes root), and its bwrap must be able to make a
    user namespace, or the room is refused with what to change. Either way a
    room meant to be isolated never runs over the whole machine instead."""
    if sys.platform == "darwin":
        return seatbelt()
    if not sys.platform.startswith("linux"):
        key = NO_SANDBOX.get(sys.platform, "sandboxNoneOnSystem")
        raise SandboxUnavailable(key, system=sys.platform)
    if not platform_machine:
        return enrolled_bubblewrap()
    placed = execution_root(Path.home()) / "runtime/host-tools/bin"
    for name in HOST_TOOLS:
        if not os.access(placed / name, os.X_OK):
            # i18n-exempt: a platform host's install failure, read by operators in its log
            raise RuntimeError(f"cheesehost has not placed {name} at {placed}")
    # The tools before the helper that runs them: a helper installed first
    # would meanwhile run tools that are not there yet.
    for source, target in (
        *((placed / name, f"{SANDBOX_TOOLS}/{name}") for name in HOST_TOOLS),
        (release / "remote-execution/sandbox_host.py", SANDBOX_HOST),
    ):
        install_as_root(source, target)
    return placed_bubblewrap()


def install_as_root(source, target):
    """`source` at `target`, root's, unless it is there already. Beside it,
    then renamed over it: a sandbox starting meanwhile runs the old one or the
    new one, never half of either."""
    try:
        if Path(target).read_bytes() == Path(source).read_bytes():
            return
    except OSError:
        pass
    staged = target + "." + uuid.uuid4().hex
    failure = "installing " + target + " failed"
    sudo(
        [
            "install",
            "-D",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0755",
            str(source),
            staged,
        ],
        failure,
    )
    sudo(["mv", "-f", staged, target], failure)


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def seatbelt():
    """Where `sandbox-exec` is: every macOS has it, so a Mac that lacks it is
    one the room is refused on, not one it runs over whole."""
    if not os.access(SANDBOX_EXEC, os.X_OK):
        raise SandboxUnavailable("sandboxNoSeatbelt")
    return SANDBOX_EXEC


def placed_bubblewrap():
    """The bwrap cheesehost places when it starts (`cli/internal/devenv`)."""
    found = execution_root(Path.home()) / "runtime/bubblewrap/bwrap"
    if not os.access(found, os.X_OK):
        raise SandboxUnavailable("sandboxNotPlaced")
    return str(found)


def enrolled_bubblewrap():
    """Where `bwrap` is on a machine a person enrolled, once it is known to
    make a sandbox there, as the person and not as root."""
    found = placed_bubblewrap()
    probe = subprocess.run(
        [found, "--unshare-user", "--unshare-pid", "--ro-bind", "/", "/"]
        + ["--proc", "/proc", "--dev", "/dev", "true"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode:
        raise SandboxUnavailable(
            user_namespaces_advice(), detail=probe.stderr.strip()[-300:]
        )
    return found


def user_namespaces_advice():
    """Which change this machine needs for bubblewrap to make a sandbox, which
    it does in a user namespace of its own, as the user and not as root."""

    def setting(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    # Ubuntu from 23.10: only a program a profile names (`cheesehost sandbox allow`).
    if setting("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") == "1":
        return "sandboxAppArmor"
    if setting("/proc/sys/kernel/unprivileged_userns_clone") == "0":
        return "sandboxUsernsCloneOff"
    if setting("/proc/sys/user/max_user_namespaces") == "0":
        return "sandboxUserNamespacesOff"
    return "sandboxNoUserNamespaces"


def record_sandbox(owner, home, release):
    """Record, outside the room, that it runs in a sandbox started from
    `release`. The teardown and the environment reset run outside the sandbox;
    this is how they know to run only what is in the release store, and to
    run git in the room's checkouts sandboxed too."""
    marker = execution_root(owner) / SANDBOXES / home.parent.name / home.name
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary = marker.with_name(marker.name + "." + uuid.uuid4().hex)
    temporary.write_text(str(release))
    temporary.replace(marker)


def record_sandbox_process(owner, home, pid):
    """Record, beside `record_sandbox`'s marker, which process is the room's
    sandbox, by pid and start time: its first process inside, the init of its
    pid namespace, which bubblewrap reports on its info descriptor. Every
    process the room starts descends from it and outlives the executor with
    it, while bubblewrap itself exits with the executor. That is how the
    environment reset and the teardown, outside the sandbox, tell the room's
    processes from every other process of the machine's user on a machine
    without the sandbox helper's cgroups (`environment_runner.
    sandbox_process`): a pid the room wrote down itself names nothing they
    can trust. Its start time is the one the runner records
    (`environment_runner.process_identity`)."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return
    record = (
        execution_root(owner) / SANDBOXES / home.parent.name / (home.name + ".process")
    )
    temporary = record.with_name(record.name + "." + uuid.uuid4().hex)
    temporary.write_text(
        json.dumps({"pid": pid, "identity": f"linux:{boot_id}:{fields[19]}"})
    )
    temporary.replace(record)


def forget_sandbox(owner, home):
    """Remove `record_sandbox`'s and `record_sandbox_process`'s records."""
    marker = execution_root(owner) / SANDBOXES / home.parent.name / home.name
    marker.unlink(missing_ok=True)
    marker.with_name(home.name + ".process").unlink(missing_ok=True)


def start_sandbox(
    argv,
    *,
    bwrap,
    owner,
    home,
    claude,
    sockets,
    limits,
    env,
    connect,
    release,
    platform=None,
    **options,
):
    """Start `argv` in a sandbox (`confinement.sandbox_argv`) and, with `connect`, give
    it, before it runs anything, its network and its limits (`SANDBOX_HOST
    up`). A sandbox the machine cannot connect or limit is killed, never left
    to run without. Without `connect` (a machine a person enrolled, where
    there is no root to do either) it shares the machine's network. Answers
    the process and what the helper said, with the sandbox's first process
    as `first`.

    On macOS `bwrap` is `sandbox-exec`, run by this release's `confinement.py`:
    the process it starts is the first, and there is no network or limit of
    the sandbox's own to give it."""
    if sys.platform == "darwin":
        confinement = runpy.run_path(str(release / "remote-execution/confinement.py"))
        return confinement["start_seatbelt"](
            argv,
            sandbox_exec=bwrap,
            owner=owner,
            home=home,
            store=execution_root(owner) / "store" / home.parent.name,
            tmp=home / PLATFORM_DIR / "tmp",
            readable=[
                execution_root(owner) / "toolchain",
                release_store(owner),
                claude.parent,
            ],
            sockets=sockets,
            platform=platform,
            env=env,
            **options,
        )
    confinement = runpy.run_path(str(release / "remote-execution/confinement.py"))
    info, report = os.pipe()
    wait, go = os.pipe()
    seccomp, program = os.pipe()
    os.write(program, confinement["seccomp_filter"]())
    os.close(program)
    fds = {"info": report, "block": wait, "seccomp": seccomp}
    site = connect and confinement["site_hosts"](env, fds)
    try:
        process = subprocess.Popen(
            confinement["sandbox_argv"](
                argv,
                bwrap=bwrap,
                owner=owner,
                home=home,
                store=execution_root(owner) / "store" / home.parent.name,
                toolchain=execution_root(owner) / "toolchain",
                tmp=home / PLATFORM_DIR / "tmp",
                releases=release_store(owner),
                workdir=home / CHECKOUT_DIR,
                claude=claude,
                sockets=sockets,
                platform=platform,
                fds=fds,
                programs=[execution_root(owner) / "runtime/git"],
                network=connect,
            ),
            start_new_session=True,
            pass_fds=tuple(fds.values()),
            env=env,
            **options,
        )
    except BaseException:
        os.close(info)
        os.close(go)
        raise
    finally:
        for descriptor in fds.values():
            os.close(descriptor)
    try:
        with os.fdopen(info, "rb") as stream:
            first = json.loads(stream.read() or b"{}").get("child-pid")
        if not first:
            raise RuntimeError("The sandbox did not start; see executor-bootstrap.log")
        answer = {}
        if connect:
            command = [SANDBOX_HOST, "up", home.name, str(first), "--home", str(home)]
            for name in ("memory_mb", "swap_mb", "cpus", "pids", "disk_mib", "inodes"):
                command += ["--" + name.replace("_", "-"), str(limits[name])]
            for port in confinement["loopback_ports"](env):
                command += ["--forward", str(port)]
            command += ["--site", str(site)] * bool(site)
            answer = json.loads(
                sudo(command, "The sandbox's network and limits could not be set up")
            )
        answer["first"] = first
    except BaseException:
        os.close(go)
        process.kill()
        process.wait()
        raise
    os.write(go, b"1")
    os.close(go)
    return process, answer


@contextlib.contextmanager
def prepared(payload, owner, *, refresh_runtime=False, fetch_toolchain=True, disk=None):
    payload.pop("proxy_proof", None)
    if sys.platform != "win32":  # Before every path below: its room holds none (docs).
        payload["env"]["CHEESE_TOKEN"] = ""
    project, resource = (
        str(uuid.UUID(payload["project"])),
        str(uuid.UUID(payload["resource"])),
    )
    home = execution_root(owner) / "home" / project / resource
    store = execution_root(owner) / "store" / project
    toolchain = execution_root(owner) / "toolchain"
    package_env = {
        "UV_CACHE_DIR": str(store / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(store / "uv-python"),
        "npm_config_store_dir": str(store / "pnpm-store"),
        "npm_config_cache": str(store / "npm-cache"),
        "PIP_CACHE_DIR": str(store / "pip-cache"),
    }
    if payload.get("sandbox"):
        # No hard link crosses mounts or quotas; a reflink does on XFS, else both copy.
        package_env["UV_LINK_MODE"] = "clone"
        package_env["npm_config_package_import_method"] = "clone-or-copy"
    variables = (payload.get("environment") or {}).get("variables", {})
    package_env.update({key: variables[key] for key in package_env if key in variables})
    work = home / CHECKOUT_DIR
    platform_dir = home / PLATFORM_DIR
    config_dir = home / CONFIG_DIR
    platform_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work.mkdir(parents=True, exist_ok=True)
    with (
        contextlib.ExitStack() as anchors,
        platform_file(platform_dir, "executor-bootstrap.lock") as bootstrap_lock,
    ):
        lock(bootstrap_lock)
        # Under the lock like everything else this writes: the temporary file
        # each copy goes through is one name, and two prepares of one room
        # would otherwise be renaming the same `.next` file.
        platform = payload.get("platform_skills") or []
        project = payload.get("project_skills") or []
        # Filled with what is left when a write was skipped for want of
        # space (`spared`); a caller that tells the room passes its own.
        disk = {} if disk is None else disk

        def skills():
            before = payload.get("skills_before_list") or []
            prune_platform_skills(home, platform, project, before)
            prune_project_skills(home, project, platform)
            plant_native_skills(home, payload.get("skills") or {})

        spared(disk, home, skills)
        try:
            release, contents = stage_release(
                release_store(owner), platform_dir, payload
            )
        except OSError as error:
            if error.errno not in FULL or not refresh_runtime:
                raise
            raise UpgradeDeferred(
                kept_on_full_disk(owner, home, payload, anchors, error)
            ) from None
        stop_previous_root(home, release)
        # Where this room's package downloads come from, as a variable set for
        # the mirrors that answer this machine right now (`package_mirrors`).
        # After the room's own configured variables, not before: a project that
        # names an index of its own keeps it, which is why the value is only
        # defaulted. This is the environment an agent's tools inherit, so a
        # mirror listed here is what an agent's `pip install` goes through.
        # `governed` is the other half: each name with the value the policy puts
        # there, so only a room holding that value can be told to drop it.
        mirrors = payload.get("package_mirrors") or {}
        policy = shipped(release, "package_mirrors") if mirrors else None
        governed = policy["governed"](mirrors) if policy is not None else {}
        if policy is not None:
            for name, value in policy["environment"](mirrors, store).items():
                package_env.setdefault(name, variables.get(name, value))
        # Test workers this room may start, by CPU quota (`worker_quota`).
        quota = shipped(release, "worker_quota")
        for name, value in quota["environment"](payload.get("sandbox")).items():
            package_env.setdefault(name, variables.get(name, value))
        env = dict(os.environ)
        for name in list(env):
            # Except Windows' one from the connector: Claude Code's shell, or it
            # takes whatever Git the machine has, or none.
            if sys.platform == "win32" and name == "CLAUDE_CODE_GIT_BASH_PATH":
                continue
            if (
                name.startswith(("ANTHROPIC_", "CLAUDE_"))
                or name == "CHEESE_MODEL_PROXY"
            ):
                env.pop(name)
        env.update(payload["env"])
        env.update(package_env)
        env.update(
            HOME=str(home),
            CLAUDE_CONFIG_DIR=str(config_dir),
            CHEESE_WORK=str(work),
            CHEESE_STORE=str(store),
            CHEESE_TOOLCHAIN=str(toolchain),
            TYPST_FONT_PATHS=variables.get(
                "TYPST_FONT_PATHS",
                str(toolchain / "fonts" / payload["toolchain_fonts"]),
            ),
            CHEESE_WORKTREE_ROOT=str(work),
            CHEESE_PREVIEW_UP=str(release / "cheese-preview-up"),
            PATH=os.pathsep.join(
                (
                    str(release / "remote-execution/bin"),
                    str(release),
                    str(toolchain / "bin"),
                    env.get("PATH", ""),
                )
            ),
        )
        scoped_env = {
            name: value
            for name, value in env.items()
            if name in payload["env"]
            or name in package_env
            or name
            in {
                "HOME",
                "CLAUDE_CONFIG_DIR",
                "CHEESE_WORK",
                "CHEESE_STORE",
                "CHEESE_TOOLCHAIN",
                "TYPST_FONT_PATHS",
                "CHEESE_WORKTREE_ROOT",
                "CHEESE_PREVIEW_UP",
            }
        }
        if sys.platform == "win32":
            # HOME is what the room's POSIX programs read; Windows programs,
            # Python's own Path.home() among them, read USERPROFILE instead. The
            # room's home has to be the answer to both.
            env["USERPROFILE"] = scoped_env["USERPROFILE"] = str(home)
        config = {
            "workspace": str(work),
            "claude": binary(owner, env["CHEESE_API"], release),
            "env": scoped_env,
            # What the environment above may have taken away, and what it would
            # have put there: a room holding anything else keeps it (`runtime`).
            "env_reset": governed,
            "mcp_servers": {},
            "release": str(release),
            "sandbox": payload.get("sandbox") or False,
        }
        mcp = read_json_beneath(home, Path(CHECKOUT_DIR) / ".mcp.json")
        if mcp is not None:
            config["mcp_servers"] = mcp.get("mcpServers", {})
        recorded = platform_dir / "executor"
        state = executor_state(home, anchors, bool(payload.get("sandbox")))
        token = Path(PLATFORM_DIR) / "cheese-preview.token"
        if sys.platform == "win32":
            spared(disk, home, lambda: write_beneath(home, token, env["CHEESE_TOKEN"]))
        else:
            directory = beneath(home, PLATFORM_DIR)
            try:
                remove_beneath(directory, token.name)
            finally:
                os.close(directory)
        previous = read_json_beneath(home, Path(PLATFORM_DIR) / "executor/config.json")
        if previous is not None:
            # The running executor is reached with the release staged above,
            # never with the one its config names: a sandboxed room writes its
            # own config, and this runs outside the sandbox. What is asked of it
            # is requests every executor answers, and a stop that also stops
            # one started before stopping was a request.
            source = release / "remote-execution/runtime.py"
            runtime = runpy.run_path(str(source))
            try:
                info = runtime["request"](state, "ping")
            except (ConnectionError, FileNotFoundError):
                info = None
            if info:
                unchanged = {"env", "env_reset", "claude", "release", "sandbox"}
                if {k: v for k, v in previous.items() if k not in unchanged} != {
                    k: v for k, v in config.items() if k not in unchanged
                }:
                    raise RuntimeError(
                        "Executor configuration changed; restart the room environment"
                    )
                environment = read_json_beneath(
                    home, Path(PLATFORM_DIR) / "executor/environment.json"
                )
                if environment is not None and environment != payload.get(
                    "environment"
                ):
                    raise RuntimeError(
                        "Environment dependencies changed; "
                        "create a new execution environment"
                    )
                changed = (
                    previous.get("release") != str(release)
                    or previous.get("claude") != config["claude"]
                    or previous.get("sandbox", False) != config["sandbox"]
                    or info.get("upgrading", False)
                )
                if changed:
                    if not refresh_runtime:
                        raise RuntimeError(
                            "Executor release changed; prepare an idle upgrade"
                        )
                    if "idle_upgrade" in info.get("capabilities", []):
                        ready = runtime["request"](
                            state,
                            "begin_upgrade",
                            {"release": str(release), "claude": config["claude"]},
                        )["ready"]
                    else:
                        # Existing installations are admitted under the backend's
                        # exclusive release lock until they gain local admission.
                        tasks = runtime["request"](
                            state, "control", {"subtype": "background_tasks"}
                        )["tasks"]
                        ready = not any(task["status"] == "running" for task in tasks)
                    if not ready:
                        if info.get("protocol_version", 1) != payload.get(
                            "protocol_version", 1
                        ):
                            raise RuntimeError(
                                "Executor protocol upgrade is waiting for running work"
                            )
                        refreshed = {**previous.get("env", {}), **payload["env"]}
                        runtime["request"](
                            state,
                            "configure",
                            {"env": refreshed, "env_reset": config["env_reset"]},
                        )
                        info.update(
                            state=str(recorded),
                            mcp_servers=process_servers(
                                {"mcp_servers": previous.get("mcp_servers", {})}
                            ),
                            upgrade_pending=True,
                            desired_release=str(release),
                            **disk,
                        )
                        if payload.get("environment"):
                            runner = runpy.run_path(
                                str(release / "cheese-environment.py")
                            )
                            info["environment_status"] = runner["read_status"](
                                home / ".cheese-environment"
                            )["state"]
                        raise UpgradeDeferred(info)
                    predecessor(release)["stop_predecessor"](state)
            elif not refresh_runtime:
                raise RuntimeError("Executor release changed; prepare an idle upgrade")
            else:
                # Owns the state and does not answer: what a stop is for.
                predecessor(release)["stop_predecessor"](state)
        activate_release(platform_dir, release, contents)
        toolchain_options = {
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if fetch_toolchain and sys.platform == "win32":
            helpers = portable(release)
            helpers["popen_daemon"](
                helpers["which"](["sh", str(release / "cheese-toolchain")]),
                **toolchain_options,
            )
        elif fetch_toolchain:
            subprocess.Popen(
                ["sh", str(release / "cheese-toolchain")],
                start_new_session=True,
                **toolchain_options,
            )
        if payload.get("environment"):
            settings = json.dumps(payload["environment"])
            where = Path(".cheese-environment/config.json")
            spared(disk, home, lambda: write_beneath(home, where, settings))
        yield home, config, state, env


def start_forge_proxy(owner, platform, release, env, proxy_proof):
    """Start the host-side forge CA proxy and preview relay outside the sandbox."""
    if sys.platform == "win32":
        return
    script = release / "remote-execution/forge_proxy.py"
    if not script.is_file() or shutil.which("openssl") is None:
        return
    confinement = runpy.run_path(str(release / "remote-execution/confinement.py"))
    confinement["own_directory"](platform)
    ca_dir = execution_root(owner) / "ca"
    record = platform / "forge-proxy.json"
    if record.is_file():
        with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
            pid = int(json.loads(record.read_text())["pid"])
            os.kill(pid, 15)
    for name in ("forge.sock", "preview.sock"):
        (platform / name).unlink(missing_ok=True)
    log_path = platform / "forge-proxy.log"
    with open(log_path, "a", opener=lambda p, flags: os.open(p, flags, 0o600)) as log:
        subprocess.Popen(
            [
                sys.executable,
                str(script),
                "--platform-dir",
                str(platform),
                "--ca-dir",
                str(ca_dir),
                f"--api-url={env.get('CHEESE_API', '')}",
                f"--preview-url={env.get('CHEESE_PREVIEW_URL', '')}",
                f"--site-forward={env.get('CHEESE_SITE_FORWARD', '')}",
            ],
            cwd=str(platform),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env={**os.environ, "CHEESE_PROXY_PROOF": proxy_proof},
            start_new_session=True,
        )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if (
            (platform / "forge.sock").exists()
            and (platform / "preview.sock").exists()
            and (platform / "ca-bundle.pem").is_file()
        ):
            break
        time.sleep(0.02)


def configure(payload):
    try:
        configure_idle(payload)
    except UpgradeDeferred as deferred:
        print(json.dumps(deferred.info))
    except SandboxUnavailable as refused:
        print(json.dumps(refused.said), file=sys.stderr)
        raise SystemExit(SANDBOX_UNAVAILABLE_EXIT) from None


def configure_idle(payload):
    proxy_proof = payload.pop("proxy_proof", "")
    platform_machine = bool(payload.get("platform_machine"))
    # Refused before anything of the room's is touched where the machine
    # cannot make the sandbox; a Cloud machine is given what it lacks below,
    # from the release staged for it.
    bwrap = None
    if payload.get("sandbox") and not platform_machine:
        bwrap = sandbox_tools(None, platform_machine=False)
    disk = {}
    with prepared(payload, Path.home(), refresh_runtime=True, disk=disk) as (
        home,
        config,
        state,
        env,
    ):
        platform_dir = home / PLATFORM_DIR
        # Where the backend and the sandbox find the state; `state` is the
        # path this process reaches it by (`executor_state`).
        recorded = platform_dir / "executor"
        work = Path(config["workspace"])
        scoped_env = config["env"]
        release = Path(config["release"])
        runtime = runpy.run_path(str(release / "remote-execution/runtime.py"))

        if read_json_beneath(home, Path(PLATFORM_DIR) / "executor/config.json"):
            try:
                info = runtime["request"](state, "ping")
            except (ConnectionError, FileNotFoundError):
                info = None
            if info is None and predecessor(release)["occupied"](state, runtime):
                # Held: a predecessor is coming up, and one started over it dies.
                info = predecessor(release)["await_predecessor"](state, runtime)
            if info:
                runtime["request"](
                    state,
                    "configure",
                    {"env": scoped_env, "env_reset": config["env_reset"]},
                )
                if payload.get("environment"):
                    runner = runpy.run_path(str(release / "cheese-environment.py"))
                    info["environment_status"] = runner["read_status"](
                        home / ".cheese-environment"
                    )["state"]
                print(
                    json.dumps(
                        {
                            **info,
                            "mcp_servers": process_servers(config),
                            "state": str(recorded),
                            **disk,
                        }
                    )
                )
                return
        for name, value in (
            ("executor/config.json", config),
            ("executor/environment.json", payload.get("environment")),
            ("execution-owner.json", {"resource": home.name}),
        ):
            write_beneath(home, Path(PLATFORM_DIR) / name, json.dumps(value))
        # The session's transcript, readable at the path the build names after a
        # compaction. Started before the sandbox so the mount is in place by the
        # time a session reads it, out here because the sandbox has no
        # `/dev/fuse` of its own, and wherever the room runs: a room given the
        # whole machine reads the same path the same way.
        start_transcript_view(home, release, config, platform_machine)
        with platform_file(platform_dir, "executor-bootstrap.log") as output:
            argv = [
                sys.executable,
                str(release / "remote-execution/bootstrap.py"),
                str(recorded),
            ]
            options = {
                "cwd": work,
                "stdin": subprocess.DEVNULL,
                "stdout": output,
                "stderr": output,
            }
            if config["sandbox"]:
                if bwrap is None:
                    bwrap = sandbox_tools(release, platform_machine=True)
                record_sandbox(Path.home(), home, release)
                # WSL runs a Windows program through this socket, outside every
                # sandbox; the directory it is in is hidden too (`confinement.HIDDEN_MOUNTS`).
                env = {k: v for k, v in env.items() if k != "WSL_INTEROP"}
                directory = Path(runtime["socket_directory"](state))
                # Where the connector serves this sandbox's platform socket
                # (`cli/internal/host/machine.go`); made here, before the
                # sandbox, so the sandbox is shown it from the start.
                platform = Path(runtime["platform_socket_directory"](state))
                start_forge_proxy(Path.home(), platform, release, env, proxy_proof)
                env["CHEESE_PLATFORM_SOCKET"] = str(platform / "platform.sock")
                # The service makes this link in the /tmp it is shown, which is
                # its own; clients outside dial the machine's.
                public = Path(runtime["socket_path"](state))
                public.unlink(missing_ok=True)
                public.symlink_to(directory / "executor.sock")
                started = time.monotonic()
                process, network = start_sandbox(
                    argv,
                    bwrap=bwrap,
                    owner=Path.home(),
                    home=home,
                    claude=Path(config["claude"]),
                    sockets=directory,
                    platform=platform,
                    limits=config["sandbox"],
                    env=env,
                    connect=platform_machine,
                    release=release,
                    **options,
                )
                record_sandbox_process(Path.home(), home, network["first"])
                if platform_machine:
                    output.write(
                        f"sandbox {network['link']} {network['address']} up in "
                        f"{round((time.monotonic() - started) * 1000)} ms "
                        f"(helper {network['ms']} ms)\n"
                    )
                    output.flush()
            else:
                # A room given full machine access after running isolated:
                # what the teardown and the reset read to treat it as
                # sandboxed would now hide the processes it starts out here.
                forget_sandbox(Path.home(), home)
                if sys.platform == "win32":
                    process = portable(release)["popen_daemon"](
                        argv, env=env, **options
                    )
                else:
                    # A whole machine (a VM that is the session's own, or one
                    # its owner gave over) has no sandbox to bind the platform
                    # socket into, and needs it all the same: its model calls
                    # take no token either.
                    platform = Path(runtime["platform_socket_directory"](state))
                    runpy.run_path(str(release / "remote-execution/confinement.py"))[
                        "own_directory"
                    ](platform)
                    start_forge_proxy(Path.home(), platform, release, env, proxy_proof)
                    env["CHEESE_PLATFORM_SOCKET"] = str(platform / "platform.sock")
                    process = subprocess.Popen(
                        argv, start_new_session=True, env=env, **options
                    )
        deadline = time.monotonic() + 600
        while True:
            if process.poll() is not None:
                if payload.get("environment"):
                    runner = runpy.run_path(str(release / "cheese-environment.py"))
                    status = runner["read_status"](home / ".cheese-environment")
                    if status["state"] == "failed":
                        info = {"environment_status": "failed"}
                        break
                # Before the failed start this would otherwise read as.
                predecessor(release)["refuse_if_served"](state, runtime)
                raise RuntimeError(
                    "Executor startup failed; inspect executor-bootstrap.log"
                )
            try:
                info = runtime["request"](state, "ping")
                break
            except (ConnectionError, FileNotFoundError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Executor readiness timed out") from None
                time.sleep(0.1)
        # The caller records this rather than deriving it: the process that
        # reaches the executor later is released separately from the one that
        # installs it, so a directory named in both is a directory two builds
        # can disagree about. See `agent.execution.executor_state`.
        print(
            json.dumps(
                {
                    **info,
                    "workspace": str(work),
                    "mcp_servers": process_servers(config),
                    "state": str(recorded),
                    **disk,
                }
            )
        )


def run(state):
    configuration = json.loads((state / "environment.json").read_text())
    command = [
        sys.executable,
        str(Path(__file__).with_name("runtime.py")),
        "serve",
        "--state",
        str(state),
    ]
    if configuration:
        runner = runpy.run_path(
            str(Path(__file__).resolve().parent.parent / "cheese-environment.py")
        )
        raise SystemExit(
            runner["run"](configuration, Path.home() / ".cheese-environment", command)
        )
    if sys.platform == "win32":
        # os.exec* on Windows starts a new process and ends this one, which to
        # whoever started this one reads as the executor having exited.
        raise SystemExit(subprocess.call(command))
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__" and len(sys.argv) == 2:
    run(Path(sys.argv[1]))
