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


def _skill_folder(name):
    return (
        isinstance(name, str) and name and "/" not in name and name not in (".", "..")
    )


def prune_project_skills(config_dir, names, platform):
    """Remove the project skills planted last time that are no longer shipped.
    The platform's own folders are never touched here."""
    manifest = config_dir / PROJECT_SKILLS_MANIFEST
    try:
        previous = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = []
    for name in set(previous) - set(names) - set(platform):
        if _skill_folder(name):
            shutil.rmtree(config_dir / "skills" / name, ignore_errors=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(sorted(names)), encoding="utf-8")


def prune_platform_skills(config_dir, names, project, before_the_list):
    """Remove the platform skills planted last time that are no longer shipped.

    Files are only ever written here, so a retired skill would otherwise stay
    for good. A machine with no list yet is taken to have been shipped
    ``before_the_list``. A project skill of the same name stays."""
    manifest = config_dir / PLATFORM_SKILLS_MANIFEST
    try:
        previous = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = list(before_the_list)
    for name in set(previous) - set(names) - set(project):
        if _skill_folder(name):
            shutil.rmtree(config_dir / "skills" / name, ignore_errors=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(sorted(names)), encoding="utf-8")


def plant_native_skills(config_dir, skills):
    """Write the platform's skills where this machine's shell will look for them.

    The agent reads a skill's text, then runs the command that text gives it —
    here, on the executor machine. Everything else that installs these files
    does it beside the claude it starts (the container launch, the
    device-hosted launch); this path starts no claude at all, so nothing
    installed them and `$CLAUDE_CONFIG_DIR/skills/documents/scripts/office.py`
    named a file that was not on the machine. The room's own answer to that was
    to scavenge a copy out of another room's cache, which is not a mechanism.

    Written through a temporary because a half-written `office.py` is a script
    the agent is told to run, and only rewritten when the bytes differ: this
    runs on every prepare, including the ones that change nothing.
    """
    for name, content in sorted(skills.items()):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Native skill file must be inside the config directory")
        path = config_dir / relative
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".next")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


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
    # Stable entrypoints select a release; running executors use the recorded path.
    for name in [*names, "executor-files.json"]:
        destination = platform_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".next")
        temporary.unlink(missing_ok=True)
        if sys.platform == "win32":
            # A symlink needs Developer Mode or an administrator there.
            shutil.copyfile(release / name, temporary)
        else:
            temporary.symlink_to(release / name)
        temporary.replace(destination)


def binary(owner, api, verified=None):
    windows = sys.platform == "win32"
    executable = ".exe" if windows else ""
    destination = owner / ".cheese/claude/versions" / (VERSION + executable)
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
            stat = candidate.stat()
            identity = (
                VERSION,
                stat.st_dev,
                stat.st_ino,
                stat.st_mode,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
            if verified is not None and verified.get(str(candidate)) == identity:
                return str(candidate)
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
                    return str(destination)
                if verified is not None:
                    verified[str(candidate)] = identity
                return str(candidate)
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


def bubblewrap():
    """Where `bwrap` is, installed first on a machine enrolled before the
    platform needed it. Without it the room does not start: a room meant to be
    boxed never runs over the whole machine instead."""
    found = shutil.which("bwrap")
    if found:
        return found
    install = ["sudo", "-n", "apt-get", "install", "-y", "-q", "bubblewrap"]
    failure = ""
    for commands in ([install], [["sudo", "-n", "apt-get", "update", "-q"], install]):
        for command in commands:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
            )
            failure = result.stderr.strip()[-500:]
            if result.returncode:
                break
        found = shutil.which("bwrap")
        if found:
            return found
    raise RuntimeError(
        "This room runs in a sandbox and bubblewrap is not installed here; "
        "installing it failed: " + failure
    )


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


def sandbox_argv(argv, *, owner, home, claude, sockets):
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
    connector can still reach it. The network is the machine's.

    Its own pid namespace too: no other room's processes to signal, trace, or
    reach a filesystem through via /proc. That makes the pid an executor
    reports meaningless outside it, which is why it is stopped by request.
    """
    if not sys.platform.startswith("linux"):
        raise RuntimeError("This room runs in a sandbox, which needs Linux")
    bwrap = bubblewrap()
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
        "--unshare-cgroup-try",
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
        "--tmpfs",
        str(owner),
    ]
    runtime_dir = Path(f"/run/user/{os.getuid()}")
    if runtime_dir.is_dir():
        command += ["--tmpfs", str(runtime_dir)]
    # The docker group's socket is root on the machine, and a user namespace
    # keeps the owner's groups: a session would reach it through a read-only
    # mount as well, since connecting to a socket is not a write to the mount.
    if Path("/run/docker.sock").exists():
        command += ["--ro-bind", "/dev/null", "/run/docker.sock"]
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


@contextlib.contextmanager
def prepared(
    payload, owner, verified=None, *, refresh_runtime=False, fetch_toolchain=True
):
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
    config_dir = home / ".claude"
    platform_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    work.mkdir(parents=True, exist_ok=True)
    with platform_file(platform_dir, "executor-bootstrap.lock") as bootstrap_lock:
        lock(bootstrap_lock)
        # Under the lock like everything else this writes: the temporary file
        # each copy goes through is one name, and two prepares of one room
        # would otherwise be renaming the same `.next` file.
        platform = payload.get("platform_skills") or []
        project = payload.get("project_skills") or []
        prune_platform_skills(
            config_dir, platform, project, payload.get("skills_before_list") or []
        )
        prune_project_skills(config_dir, project, platform)
        plant_native_skills(config_dir, payload.get("skills") or {})
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
            "claude": binary(owner, env["CHEESE_API"], verified),
            "env": scoped_env,
            "mcp_servers": {},
            "release": str(release),
            "sandbox": bool(payload.get("sandbox")),
        }
        mcp = work / ".mcp.json"
        if mcp.exists():
            config["mcp_servers"] = json.loads(mcp.read_text()).get("mcpServers", {})
        state = platform_dir / "executor"
        state.mkdir(exist_ok=True, mode=0o700)
        (platform_dir / "cheese-preview.token").write_text(env["CHEESE_TOKEN"])
        (platform_dir / "cheese-preview.token").chmod(0o600)
        if (state / "config.json").exists():
            previous = json.loads((state / "config.json").read_text())
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
                environment_file = state / "environment.json"
                if environment_file.exists() and json.loads(
                    environment_file.read_text()
                ) != payload.get("environment"):
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
                            state=str(state),
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
            directory = home / ".cheese-environment"
            directory.mkdir(exist_ok=True, mode=0o700)
            runner = runpy.run_path(str(release / "cheese-environment.py"))
            runner["write_json"](directory / "config.json", payload["environment"])
        yield home, config, state, env


def configure(payload):
    try:
        configure_idle(payload)
    except UpgradeDeferred as deferred:
        print(json.dumps(deferred.info))


def configure_idle(payload):
    with prepared(payload, Path.home(), refresh_runtime=True) as (
        home,
        config,
        state,
        env,
    ):
        platform_dir = home / PLATFORM_DIR
        work = Path(config["workspace"])
        scoped_env = config["env"]
        release = Path(config["release"])
        runtime = runpy.run_path(str(release / "remote-execution/runtime.py"))

        if (state / "config.json").exists():
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
                            "state": str(state),
                        }
                    )
                )
                return
        runtime["write_json"](state / "config.json", config)
        runtime["write_json"](state / "environment.json", payload.get("environment"))
        runtime["write_json"](
            platform_dir / "execution-owner.json", {"resource": home.name}
        )
        with platform_file(platform_dir, "executor-bootstrap.log") as output:
            argv = [
                sys.executable,
                str(release / "remote-execution/bootstrap.py"),
                str(state),
            ]
            if config["sandbox"]:
                record_sandbox(Path.home(), home, release)
                directory = Path(runtime["socket_directory"](state))
                argv = sandbox_argv(
                    argv,
                    owner=Path.home(),
                    home=home,
                    claude=Path(config["claude"]),
                    sockets=directory,
                )
                # The service makes this link in the /tmp it is shown, which is
                # its own; clients outside dial the machine's.
                public = Path(runtime["socket_path"](state))
                public.unlink(missing_ok=True)
                public.symlink_to(directory / "executor.sock")
            options = {
                "cwd": work,
                "env": env,
                "stdin": subprocess.DEVNULL,
                "stdout": output,
                "stderr": output,
            }
            process = (
                portable(release)["popen_daemon"](argv, **options)
                if sys.platform == "win32"
                else subprocess.Popen(argv, start_new_session=True, **options)
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
                    "state": str(state),
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
