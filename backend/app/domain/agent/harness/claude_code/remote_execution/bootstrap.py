"""Prepare a room executor using platform helpers, without starting a model."""

from __future__ import annotations

import base64
import contextlib
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

VERSION = "2.1.282"
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
    """This machine cannot give the room the isolated environment it runs in,
    and the message says what would let it."""


# How the install exits when it refuses a room for `SandboxUnavailable`, with
# the message alone on stderr: the backend tells the room that, not a trace.
SANDBOX_UNAVAILABLE_EXIT = 78


class UpgradeDeferred(Exception):
    def __init__(self, info):
        self.info = info


def lock(file):
    """flock(LOCK_EX). This file arrives on stdin before any release is on disk,
    so it cannot load portable.py for the Windows lock; this is the same one."""
    if sys.platform == "win32":
        import msvcrt

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
    run. Readable by the owner alone."""
    relative = Path(relative)
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

    Only rewritten when the bytes differ: this runs on every prepare,
    including the ones that change nothing.
    """
    for name, content in sorted(skills.items()):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Native skill file must be inside the config directory")
        relative = Path(CONFIG_DIR) / relative
        try:
            if read_beneath(home, relative) == content:
                continue
        except (OSError, UnicodeDecodeError):
            pass
        write_beneath(home, relative, content)


def release_store(owner):
    """Where every room's executor releases are kept on this machine, by digest.

    Beside the rooms, never inside one: a sandboxed room can write all of its
    own home, and programs from here also run outside its sandbox — the
    install below, the teardown, the environment reset (`record_sandbox`). A
    sandbox is shown this directory read-only (`sandbox_argv`), so what is here
    is what the platform put here."""
    return owner / ".cheese/executor-releases"


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
        import fcntl

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


def binary(owner, api):
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
    import platform
    from urllib.request import urlopen

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
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_beside(destination)
    with urlopen(
        f"{api}/connector/claude/{VERSION}/{target}/claude{executable}", timeout=300
    ) as source:
        with temporary.open("xb") as output:
            shutil.copyfileobj(source, output)
    temporary.chmod(0o700)
    result = subprocess.run(
        [str(temporary), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    if result.stdout.split()[0] != VERSION:
        raise RuntimeError("The executor binary does not match the verified version")
    temporary.replace(destination)
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


# Where `sandbox_host.py` is installed, root's and outside every release: the
# connector user runs it through `sudo -n`, and only root may change it.
SANDBOX_HOST = "/usr/local/libexec/cheese-sandbox"
# The programs a sandbox needs from the machine, by the package that has them.
SANDBOX_PACKAGES = {
    "bwrap": "bubblewrap",
    "ip": "iproute2",
    "iptables-restore": "iptables",
}
SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def sudo(command, failure):
    result = subprocess.run(
        ["sudo", "-n", *command],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    if result.returncode:
        raise RuntimeError(failure + ": " + result.stderr.strip()[-500:])
    return result.stdout


# What an install is told when this machine's system has no isolated
# environment for a room. The backend refuses such a room before it gets here
# (`device.supply.sandbox_unavailable`, the same sentences) for a machine it
# has heard from; these are for one it has not. A system gains an environment
# by leaving this table in the same change that teaches `sandbox_tools` and
# `sandbox_argv` to build it.
NO_SANDBOX = {
    "win32": (
        "This room runs in an isolated environment, which Windows machines do "
        "not have. Install WSL, enroll the WSL environment as a machine and "
        "choose it for this room, or have the machine's owner give this room "
        "full machine access."
    ),
}
FULL_ACCESS_INSTEAD = (
    "Or have the machine's owner give this room full machine access, which "
    "runs it without isolation."
)


def sandbox_tools(release, platform_machine):
    """Where `bwrap` is, once the machine has what a sandbox needs; asked
    before anything of the room's is touched. On macOS, `sandbox-exec`
    instead (`confinement.start_seatbelt`).

    Other systems have no isolated environment yet (`NO_SANDBOX`). A machine
    the platform provisioned (`platform_machine`) needs bubblewrap, `ip` and
    iptables, and this release's `sandbox_host.py` as `SANDBOX_HOST`; one
    enrolled before the platform needed them gets them here. A machine a
    person enrolled is theirs: nothing is installed there, its sandbox has no
    network or limits of its own (that takes root), and it needs only a
    bubblewrap that can make a user namespace, or the room is refused with
    what to install or change. Either way a room meant to be isolated never
    runs over the whole machine instead."""
    if sys.platform == "darwin":
        return seatbelt()
    if not sys.platform.startswith("linux"):
        raise SandboxUnavailable(
            NO_SANDBOX.get(
                sys.platform,
                "This room runs in an isolated environment, which this "
                "machine's system does not have. " + FULL_ACCESS_INSTEAD,
            )
        )
    if not platform_machine:
        return enrolled_bubblewrap()
    missing = [
        package
        for program, package in SANDBOX_PACKAGES.items()
        if shutil.which(program, path=SYSTEM_PATH) is None
    ]
    if missing:
        install = ["apt-get", "install", "-y", "-q", *missing]
        try:
            sudo(install, "installing " + " ".join(missing) + " failed")
        except RuntimeError:
            sudo(["apt-get", "update", "-q"], "apt-get update failed")
            sudo(install, "installing " + " ".join(missing) + " failed")
    source = release / "remote-execution/sandbox_host.py"
    try:
        current = Path(SANDBOX_HOST).read_bytes() == source.read_bytes()
    except OSError:
        current = False
    if not current:
        # Beside it, then renamed over it: a sandbox starting meanwhile runs
        # the old one or the new one, never half of either.
        staged = SANDBOX_HOST + "." + uuid.uuid4().hex
        failure = "installing the sandbox helper failed"
        sudo(
            ["install", "-D", "-o", "root", "-g", "root", "-m", "0755"]
            + [str(source), staged],
            failure,
        )
        sudo(["mv", "-f", staged, SANDBOX_HOST], failure)
    return shutil.which("bwrap", path=SYSTEM_PATH)


SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def seatbelt():
    """Where `sandbox-exec` is: every macOS has it, so a Mac that lacks it is
    one the room is refused on, not one it runs over whole."""
    if not os.access(SANDBOX_EXEC, os.X_OK):
        raise SandboxUnavailable(
            "This room runs in an isolated environment, which on macOS needs "
            "/usr/bin/sandbox-exec, and this Mac does not have it. "
            + FULL_ACCESS_INSTEAD
        )
    return SANDBOX_EXEC


def enrolled_bubblewrap():
    """Where `bwrap` is on a machine a person enrolled, once it is known to
    make a sandbox there, as the person and not as root."""
    found = shutil.which("bwrap")
    if not found:
        raise SandboxUnavailable(
            "This room runs in an isolated environment, which needs "
            "bubblewrap, and bubblewrap is not installed on this machine. "
            "Install it (Debian and Ubuntu: sudo apt install bubblewrap; "
            "Fedora: sudo dnf install bubblewrap; Arch: sudo pacman -S "
            "bubblewrap) and try again. " + FULL_ACCESS_INSTEAD
        )
    probe = subprocess.run(
        [found, "--unshare-user", "--unshare-pid", "--ro-bind", "/", "/"]
        + ["--proc", "/proc", "--dev", "/dev", "true"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode:
        raise SandboxUnavailable(
            "This room runs in an isolated environment, and bubblewrap cannot "
            "make one on this machine ("
            + probe.stderr.strip()[-300:]
            + "). "
            + user_namespaces_advice()
            + " "
            + FULL_ACCESS_INSTEAD
        )
    return found


def user_namespaces_advice():
    """What to change on this machine for bubblewrap to make a sandbox, which
    it does in a user namespace of its own, as the user and not as root."""

    def setting(path):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    keep = (
        " (put the same line in a file under /etc/sysctl.d/ to keep it after a reboot)"
    )
    if setting("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") == "1":
        return (
            "AppArmor here restricts unprivileged user namespaces, as Ubuntu "
            "does from 23.10 on. Allow them for bubblewrap with an AppArmor "
            "profile for it, or for every program with: sudo sysctl -w "
            "kernel.apparmor_restrict_unprivileged_userns=0" + keep + "."
        )
    if setting("/proc/sys/kernel/unprivileged_userns_clone") == "0":
        return (
            "Unprivileged user namespaces are turned off here. Turn them on "
            "with: sudo sysctl -w kernel.unprivileged_userns_clone=1" + keep + "."
        )
    if setting("/proc/sys/user/max_user_namespaces") == "0":
        return (
            "User namespaces are turned off here. Turn them on with: sudo "
            "sysctl -w user.max_user_namespaces=15000" + keep + "."
        )
    return "It needs unprivileged user namespaces, which this machine does not allow."


def record_sandbox(owner, home, release):
    """Record, outside the room, that it runs in a sandbox started from
    `release`. The teardown and the environment reset run outside the sandbox;
    this is how they know to run only what is in the release store, and to
    run git in the room's checkouts sandboxed too."""
    marker = owner / ".cheese" / SANDBOXES / home.parent.name / home.name
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
    record = owner / ".cheese" / SANDBOXES / home.parent.name / (home.name + ".process")
    temporary = record.with_name(record.name + "." + uuid.uuid4().hex)
    temporary.write_text(
        json.dumps({"pid": pid, "identity": f"linux:{boot_id}:{fields[19]}"})
    )
    temporary.replace(record)


def forget_sandbox(owner, home):
    """Remove `record_sandbox`'s and `record_sandbox_process`'s records."""
    marker = owner / ".cheese" / SANDBOXES / home.parent.name / home.name
    marker.unlink(missing_ok=True)
    marker.with_name(home.name + ".process").unlink(missing_ok=True)


# Where the machine keeps what is not the system's: mounted disks, and on WSL
# the Windows drives and the socket that runs Windows programs. Hidden in a
# sandbox like the owner's home: they hold the owner's files, and a Windows
# program would run outside it.
HIDDEN_MOUNTS = ("/mnt", "/media", "/run/media", "/run/WSL")


def sandbox_argv(argv, *, bwrap, owner, home, claude, sockets, fds, network=True):
    """`argv` run in a bubblewrap sandbox: the room's processes see the machine
    read-only, and of the owner's home only what is bound back below.

    Writable: the room's home and its project's package store. Read-only: the
    machine's toolchain, the release store and the Claude build. Hidden: every
    other room, every other project's store, the owner's own files (the
    connector's credential is among them), the owner's runtime directory,
    whose user bus would start processes outside, and the Docker daemon's
    socket. `/tmp` and `/var/tmp` are a
    directory of the room's own on disk, with the one directory of the
    machine's /tmp that holds this executor's sockets bound back, so the
    connector can still reach it.

    Its own pid namespace too: no other room's processes to signal, trace, or
    reach a filesystem through via /proc. That makes the pid an executor
    reports meaningless outside it, which is why it is stopped by request.

    And, with `network`, its own network namespace, empty until
    `start_sandbox` has the machine connect it (`sandbox_host.py`). Without
    it, on a machine a person enrolled, the sandbox shares the machine's
    network. Bubblewrap reports the sandbox's first process on `fds["info"]`
    and waits on `fds["block"]` before running `argv`, under the seccomp
    program on `fds["seccomp"]`.

    On a machine a person enrolled the owner's home holds everything they
    installed there too (nvm, `~/.cargo/bin`, `~/.local/bin`), which a
    session does not see; mounted disks are hidden as well (`HIDDEN_MOUNTS`).
    """
    store = owner / ".cheese/store" / home.parent.name
    toolchain = owner / ".cheese/toolchain"
    tmp = home / PLATFORM_DIR / "tmp"
    for directory in (store, toolchain, tmp):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    sockets.mkdir(exist_ok=True, mode=0o700)
    command = [
        bwrap,
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        # No cgroup namespace of its own: one is made when the sandbox
        # starts, before `SANDBOX_HOST` moves it into its limited cgroup, and
        # would leave a runtime that sizes itself by its limit (Node, the JVM)
        # reading a path it cannot resolve.
        *(["--unshare-net"] if network else []),
        "--info-fd",
        str(fds["info"]),
        "--block-fd",
        str(fds["block"]),
        "--seccomp",
        str(fds["seccomp"]),
        "--ro-bind",
        "/",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        # The boot id as the machine reports it. A container's lxcfs gives it
        # its own, which a fresh /proc does not show, and the environment
        # runner's record of a process (`process_identity`) made inside would
        # then never match the same process read from outside.
        "--ro-bind",
        "/proc/sys/kernel/random/boot_id",
        "/proc/sys/kernel/random/boot_id",
        # Before anything under the owner's home: a home kept under /tmp would
        # otherwise be covered by the session's own /tmp.
        "--bind",
        str(tmp),
        "/tmp",
        "--bind",
        str(tmp),
        "/var/tmp",
    ]
    # Before the owner's home too, which may be kept on one of them.
    for mount in HIDDEN_MOUNTS:
        if Path(mount).is_dir():
            command += ["--tmpfs", mount]
    command += ["--tmpfs", str(owner)]
    names = resolv_conf()
    if network and names != "/etc/resolv.conf":
        command += ["--ro-bind", names, "/etc/resolv.conf"]
    runtime_dir = Path(f"/run/user/{os.getuid()}")
    if runtime_dir.is_dir():
        command += ["--tmpfs", str(runtime_dir)]
    # The docker group's socket is root on the machine, and a user namespace
    # keeps the owner's groups: a session would reach it through a read-only
    # mount as well, since connecting to a socket is not a write to the mount.
    if Path("/run/docker.sock").exists():
        command += ["--ro-bind", "/dev/null", "/run/docker.sock"]
    # The Python the executor runs on, when the person keeps it in their home
    # (pyenv, uv, conda): shown read-only, as the machine's own would be, at
    # the path it is run by as well as where a link there leads.
    prefixes = {Path(sys.base_prefix), Path(sys.prefix)}
    for prefix in sorted(prefixes | {path.resolve() for path in prefixes}):
        if owner in prefix.parents:
            command += ["--ro-bind", str(prefix), str(prefix)]
    for option, path in (
        ("--bind", home),
        ("--bind", store),
        ("--ro-bind", toolchain),
        ("--ro-bind", release_store(owner)),
        ("--ro-bind", claude.parent),
    ):
        command += [option, str(path), str(path)]
    command += [
        "--bind",
        str(sockets),
        str(sockets),
        "--chdir",
        str(home / CHECKOUT_DIR),
        "--",
    ]
    return command + argv


# A copy of `sandbox_host.RESOLV_CONFS`, held to it by test_footprint_root.py.
RESOLV_CONFS = ("/etc/resolv.conf", "/run/systemd/resolve/resolv.conf")


def resolv_conf():
    """The resolv.conf a sandbox reads: the machine's, unless that names only
    a loopback stub, which inside the sandbox is the sandbox's own loopback
    and answers nothing; then the stub's upstream list, the one
    `sandbox_host.py` lets the sandbox reach."""
    for path in RESOLV_CONFS:
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            fields = line.split()
            if (
                len(fields) >= 2
                and fields[0] == "nameserver"
                and "." in fields[1]
                and not fields[1].startswith("127.")
            ):
                return path
    return RESOLV_CONFS[0]


def loopback_ports(env):
    """The machine's loopback ports a sandbox must reach as its own: the
    backend's, where the machine reaches it through a loopback forward
    (`device_provider.device_api_base`), and the preview tunnel's beside it."""
    from urllib.parse import urlsplit

    ports = []
    for name in ("CHEESE_API", "CHEESE_PREVIEW_URL"):
        address = urlsplit(env.get(name) or "")
        if address.hostname in ("127.0.0.1", "localhost") and address.port:
            if address.port not in ports:
                ports.append(address.port)
    return ports


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
    **options,
):
    """Start `argv` in a sandbox (`sandbox_argv`) and, with `connect`, give
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
            store=owner / ".cheese/store" / home.parent.name,
            tmp=home / PLATFORM_DIR / "tmp",
            readable=[owner / ".cheese/toolchain", release_store(owner), claude.parent],
            sockets=sockets,
            env=env,
            **options,
        )
    info, report = os.pipe()
    wait, go = os.pipe()
    seccomp, program = os.pipe()
    confinement = runpy.run_path(str(release / "remote-execution/confinement.py"))
    os.write(program, confinement["seccomp_filter"]())
    os.close(program)
    fds = {"info": report, "block": wait, "seccomp": seccomp}
    try:
        process = subprocess.Popen(
            sandbox_argv(
                argv,
                bwrap=bwrap,
                owner=owner,
                home=home,
                claude=claude,
                sockets=sockets,
                fds=fds,
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
            command = [SANDBOX_HOST, "up", home.name, str(first)]
            for name in ("memory_mb", "swap_mb", "cpus", "pids"):
                command += ["--" + name.replace("_", "-"), str(limits[name])]
            for port in loopback_ports(env):
                command += ["--forward", str(port)]
            answer = json.loads(
                sudo(command, "The sandbox's network could not be set up")
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
def prepared(payload, owner, *, refresh_runtime=False, fetch_toolchain=True):
    project, resource = (
        str(uuid.UUID(payload["project"])),
        str(uuid.UUID(payload["resource"])),
    )
    home = owner / ".cheese/home" / project / resource
    store = owner / ".cheese/store" / project
    toolchain = owner / ".cheese/toolchain"
    package_env = {
        "UV_CACHE_DIR": str(store / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(store / "uv-python"),
        "npm_config_store_dir": str(store / "pnpm-store"),
        "npm_config_cache": str(store / "npm-cache"),
        "PIP_CACHE_DIR": str(store / "pip-cache"),
    }
    if payload.get("sandbox"):
        # The store and the room's home are two mounts inside the sandbox, and
        # a hard link cannot cross mounts (EXDEV): uv's default would try one
        # per file into every venv before copying it anyway.
        package_env["UV_LINK_MODE"] = "copy"
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
        prune_platform_skills(
            home, platform, project, payload.get("skills_before_list") or []
        )
        prune_project_skills(home, project, platform)
        plant_native_skills(home, payload.get("skills") or {})
        release, contents = stage_release(release_store(owner), platform_dir, payload)
        stop_previous_root(home, release)
        env = dict(os.environ)
        for name in list(env):
            # Except, on Windows, the one the connector sets for every command:
            # Claude Code finds its shell there, and without it takes whatever
            # Git the machine happens to have installed, or none.
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
            "claude": binary(owner, env["CHEESE_API"]),
            "env": scoped_env,
            "mcp_servers": {},
            "release": str(release),
            "sandbox": payload.get("sandbox") or False,
        }
        mcp = read_json_beneath(home, Path(CHECKOUT_DIR) / ".mcp.json")
        if mcp is not None:
            config["mcp_servers"] = mcp.get("mcpServers", {})
        recorded = platform_dir / "executor"
        state = executor_state(home, anchors, bool(payload.get("sandbox")))
        write_beneath(
            home, Path(PLATFORM_DIR) / "cheese-preview.token", env["CHEESE_TOKEN"]
        )
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
                unchanged = {"env", "claude", "release", "sandbox"}
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
                        runtime["request"](state, "configure", {"env": refreshed})
                        info.update(
                            state=str(recorded),
                            mcp_servers=process_servers(
                                {"mcp_servers": previous.get("mcp_servers", {})}
                            ),
                            upgrade_pending=True,
                            desired_release=str(release),
                        )
                        if payload.get("environment"):
                            runner = runpy.run_path(
                                str(release / "cheese-environment.py")
                            )
                            info["environment_status"] = runner["read_status"](
                                home / ".cheese-environment"
                            )["state"]
                        raise UpgradeDeferred(info)
                    subprocess.run(
                        [sys.executable, str(source), "stop", "--state", str(state)],
                        check=True,
                        timeout=30,
                        pass_fds=held(state),
                    )
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
            write_beneath(
                home,
                Path(".cheese-environment/config.json"),
                json.dumps(payload["environment"]),
            )
        yield home, config, state, env


def configure(payload):
    try:
        configure_idle(payload)
    except UpgradeDeferred as deferred:
        print(json.dumps(deferred.info))
    except SandboxUnavailable as refused:
        print(refused, file=sys.stderr)
        raise SystemExit(SANDBOX_UNAVAILABLE_EXIT) from None


def configure_idle(payload):
    platform_machine = bool(payload.get("platform_machine"))
    # Refused before anything of the room's is touched where the machine
    # cannot make the sandbox; a Cloud machine is given what it lacks below,
    # from the release staged for it.
    bwrap = None
    if payload.get("sandbox") and not platform_machine:
        bwrap = sandbox_tools(None, platform_machine=False)
    with prepared(payload, Path.home(), refresh_runtime=True) as (
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
            if info:
                runtime["request"](state, "configure", {"env": scoped_env})
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
                # sandbox; the directory it is in is hidden too (`HIDDEN_MOUNTS`).
                env = {k: v for k, v in env.items() if k != "WSL_INTEROP"}
                directory = Path(runtime["socket_directory"](state))
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
