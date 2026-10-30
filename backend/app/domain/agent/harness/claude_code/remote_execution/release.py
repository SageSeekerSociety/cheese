"""Install released helpers without replacing the native conversation process."""

from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import types
from importlib.metadata import distribution
from pathlib import Path

# What a mountpoint is doing — told apart in the one way `os.path.ismount` cannot.
#
# When a forwarded_fs server process dies, its mount does NOT go away: the
# directory stays occupied and every stat on it fails. `os.path.ismount` swallows
# that OSError and returns False, which every caller reads as "nothing is mounted
# here" — so a mount is attempted onto it and fails, and the cleanup that would
# have removed it skips it. Nothing ever clears it again.
#
# The error is ENOTCONN where the kernel has a connection to report, and EIO
# where it does not: a mount whose server was killed answers `Input/output
# error` (measured on 6.12), so that one is in the list beside the other three.
#
# It is not only the room's problem. Anything that walks the directory blocks
# there, `actions/checkout` included: one left in a CI runner's workspace on
# 2026-09-15 wedged every job that machine picked up afterwards, each in a
# 15-minute timeout with an empty workspace and no git process, until the mount
# was released by hand.
MOUNT_LIVE = "live"
MOUNT_DEAD = "dead"
MOUNT_NONE = "none"


def mount_state(path):
    """``live``, ``dead`` or ``none`` for one mountpoint."""
    try:
        os.lstat(path)
    except OSError as error:
        if error.errno in (errno.ENOTCONN, errno.ETIMEDOUT, errno.EHOSTDOWN, errno.EIO):
            return MOUNT_DEAD
        return MOUNT_NONE
    return MOUNT_LIVE if os.path.ismount(path) else MOUNT_NONE


def release_mount(path):
    """Unmount ``path`` whatever state it is in. True when nothing is left there.

    Takes a dead mount too, which is the case the callers actually need: a live
    one they could have found themselves.
    """
    if mount_state(path) == MOUNT_NONE:
        return True
    unmount = shutil.which("fusermount3") or shutil.which("fusermount")
    if unmount is None:
        return False
    # Plain unmount first; lazy only if the directory is still occupied, since a
    # lazy unmount detaches a mount that may still have a live reader.
    for flag in ("-u", "-uz"):
        subprocess.run([unmount, flag, str(path)], capture_output=True, timeout=10)
        if mount_state(path) == MOUNT_NONE:
            return True
    return False


# Where a session's execution target is written. The launcher names it, so the
# runner that holds the session and `bootstrap` preparing it agree on the file
# without either spelling the other's layout. Every process of a session carries
# it — the launch environment `prepare` hands the build sets it too — which is
# what tells a session that is still running from one that has ended.
EXECUTION_CONFIG = "CHEESE_EXECUTION_CONFIG"
# The forwarded view inside a session's directory, and the program serving it.
FORWARDED_VIEW = "forwarded-project"
VIEW_SERVER = "forwarded_fs.py"


# How long a view stays kept after a session last took it, whether or not a
# process of that session is running yet. `bootstrap` is the session from the
# moment it takes the view, but a caller that prepares in one process and starts
# the build in another (`client.py prepare`, the equivalence check) has a gap in
# between where nothing carries the session's variable.
TAKEN_GRACE_S = 120


def _taken_path(directory):
    return Path(directory) / f"{FORWARDED_VIEW}.taken"


@contextlib.contextmanager
def view_lock(directory, *, taking=False):
    """Held while the view in ``directory`` is mounted, taken over by a session,
    or released for one that ended — so a session that starts while its seat's
    view is being released mounts it again, rather than going on with one that
    is unmounted from under it. ``taking``: a session is taking the view, which
    keeps it for `TAKEN_GRACE_S` whatever is running."""
    # A view is only mounted where fusermount is; Windows still loads this file.
    # deferred-import: platform-only module; fcntl is absent on Windows
    import fcntl

    with (Path(directory) / f"{FORWARDED_VIEW}.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if taking:
            _taken_path(directory).touch()
        yield


def take_view(directory, target_path, view):
    """Have the forwarded view mounted at ``view`` for the session taking it.

    Under the view's lock, which its server takes to release the view once the
    session that uses it has ended (`release_abandoned`): the server either sees
    this session taking it or has already let it go and it is mounted again
    here — never released just after this found it live. Returns None once
    the view is live, or the end of the server's log when it did not come up.
    """
    mount_log = Path(directory) / f"{FORWARDED_VIEW}.log"
    with view_lock(directory, taking=True):
        if mount_state(view) == MOUNT_LIVE:
            return None
        # A previous mount whose server died still occupies this directory and
        # cannot be mounted over, so without this the spawn below fails and the
        # room is stuck reporting a mount failure on every turn from then on.
        release_mount(view)
        with mount_log.open("a") as output:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name(VIEW_SERVER)),
                    str(target_path),
                    str(view),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        deadline = time.monotonic() + 10
        while mount_state(view) != MOUNT_LIVE and time.monotonic() < deadline:
            time.sleep(0.05)
        if mount_state(view) != MOUNT_LIVE:
            return mount_log.read_text()[-1000:] if mount_log.exists() else ""
    return None


def _own_processes():
    """``(pid, argv, environ, same_pid_namespace)`` for each of this user's processes.

    What the kernel will not show us — another user's, a zombie's — is skipped:
    a session runs as the user whose views it holds, so none of those is one.
    """
    namespace = os.readlink("/proc/self/ns/pid")
    me = os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != me:
                continue
            argv = [
                os.fsdecode(arg)
                for arg in (entry / "cmdline").read_bytes().split(b"\0")
                if arg
            ]
            environ = [
                os.fsdecode(item)
                for item in (entry / "environ").read_bytes().split(b"\0")
                if item
            ]
            same = os.readlink(entry / "ns/pid") == namespace
        except OSError:
            continue
        yield int(entry.name), argv, environ, same


def _serves_a_view(argv):
    return any(Path(arg).name == VIEW_SERVER for arg in argv)


def session_running(directory):
    """Whether a process of the session whose files are in ``directory`` is alive.

    Any process counts — the build, its runner, a command it left behind —
    except the view's own server, which inherited the variable from the session
    that started it and outlives it.
    """
    directory = os.path.realpath(directory)
    prefix = EXECUTION_CONFIG + "="
    for _pid, argv, environ, _same in _own_processes():
        if _serves_a_view(argv):
            continue
        for item in environ:
            if item.startswith(prefix) and (
                os.path.realpath(os.path.dirname(item[len(prefix) :])) == directory
            ):
                return True
    return False


def release_abandoned(directory):
    """Unmount the forwarded view in ``directory`` once its session has ended.

    True when its session has ended and the view is no longer mounted here,
    whether released now or before. That does not end its server: every
    session's own mount namespace (`client.enter`) holds a copy of each view
    mounted when it started, and the server goes on serving those copies for
    as long as any such session lives. So whoever gets True ends the server too
    (`end_server`). The next session on the seat mounts the view again
    (`client.prepare`), under the same lock.
    """
    view = Path(directory) / FORWARDED_VIEW
    with view_lock(directory):
        try:
            taken = _taken_path(directory).stat().st_mtime
        except FileNotFoundError:
            taken = 0
        if time.time() - taken < TAKEN_GRACE_S or session_running(directory):
            return False
        return release_mount(view)


def end_server(pid, view):
    """End the server of an abandoned ``view``, if ``pid`` is still it.

    SIGKILL, not SIGTERM: libfuse acts on SIGTERM only when one of its workers
    next wakes, and a copy nothing reads never wakes one. Nothing is lost — the
    view holds no state — and a copy left in another session's namespace, at
    another seat's path it never reads, answers ENOTCONN from then on.
    """
    try:
        argv = [
            os.fsdecode(arg)
            for arg in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            if arg
        ]
    except OSError:
        return
    if _serves_a_view(argv) and argv[-1] == str(view):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def release_abandoned_views():
    """End every other forwarded view on this host whose session has ended,
    server included.

    Found through the servers holding them rather than by walking seat
    directories: a walk would descend into the views themselves. Only servers
    in this PID namespace — a session it cannot see is one it cannot tell is
    still running. Returns how many servers it ended.
    """
    ended = 0
    candidates = []
    running = set()
    prefix = EXECUTION_CONFIG + "="
    for pid, argv, environ, same in _own_processes():
        if not _serves_a_view(argv):
            running.update(
                os.path.realpath(os.path.dirname(item[len(prefix) :]))
                for item in environ
                if item.startswith(prefix)
            )
        elif same and pid != os.getpid() and Path(argv[-1]).name == FORWARDED_VIEW:
            candidates.append((pid, Path(argv[-1])))
    for pid, view in candidates:
        # A session seen running a moment ago keeps its view without another
        # look; one that was not is looked for again under the lock, since it
        # may have started since.
        if os.path.realpath(view.parent) in running:
            continue
        if release_abandoned(view.parent):
            end_server(pid, view)
            ended += 1
    return ended


def sources():
    # deferred-import: this file also runs on the machine, with no `app` package
    from app.domain.agent import executor_transport, pr_creation, task_checkout

    # deferred-import: this file also runs on the machine, with no `app` package
    from app.domain.agent.harness.driven import mcp_gateway

    directory = Path(__file__).parent
    result = {
        name: (directory / name).read_text()
        for name in (
            "client.py",
            "send_user_file.py",
            "shell_stop.py",
            # The parts of `client.py` it imports from beside itself: the
            # command it runs for the build's shell prefix, the namespace it
            # enters a session into, and the files a turn hands a person.
            "shell_prefix.py",
            "namespace.py",
            "send_user_file.py",
            # Windows' locks, process trees and detached starts, which
            # `shell_stop.py` and `runtime.py` load from beside them there.
            "portable.py",
            "proxy.js",
            "private.py",
            "runtime.py",
            # The parts of `runtime.py` it imports from beside itself: the
            # primitives every piece reads, the command registry, the project's
            # context and the tool calls. It is shipped whole and loaded by
            # path, so it can reach no module of the app.
            "runtime_core.py",
            "runtime_commands.py",
            "runtime_context.py",
            "runtime_calls.py",
            "context_service.py",
            "forwarded_fs.py",
            # The other direction: this machine's view of the session host's
            # transcript, and the helper that binds it where the build names
            # it (`bootstrap` starts that one).
            "forwarded_transcript.py",
            "transcript_view.py",
            "release.py",
        )
    }
    fuse_distribution = distribution("fusepy")
    fuse_source = fuse_distribution.locate_file("fuse.py").read_text()
    if "Permission to use, copy, modify, and distribute" not in fuse_source:
        raise RuntimeError("fusepy source does not carry its ISC license")
    result["fuse.py"] = fuse_source
    result.update(
        {
            "cheese.py": (
                Path(__file__).resolve().parents[6] / "sandbox/cheese"
            ).read_text(),
            "executor_transport.py": Path(executor_transport.__file__).read_text(),
            # The transport's own reads it from beside itself.
            "task_checkout.py": Path(task_checkout.__file__).read_text(),
            "pr_creation.py": Path(pr_creation.__file__).read_text(),
            # The door its Claude Code reaches the project's MCP servers by.
            "mcp_gateway.py": Path(mcp_gateway.__file__).read_text(),
        }
    )
    return result


# The helpers a release really does bring up to date in a running session. The
# plugin module is loaded again by `/reload-plugins`; the other two are read
# only by processes that start after the release — a prompt hook, and the native
# MCP transport the release reconnects.
#
# Every other helper stays as the launch left it, so a change to one is a
# change to the launch (`launch_only`). `client.py` is replaced on disk here,
# but `prepare` wrote the shell prefix, the launch environment, the argv and
# the MCP config out of it once, the forwarded view's server and the MCP
# gateway keep the modules they started with, and nothing writes those again.
# A helper added later is launch-only until it is shown to be one of these.
RESIDENT = frozenset({"proxy.js", "context_service.py", "cheese.py"})


def platform_tool_names(cheese_source: str) -> list[str]:
    """The platform tools the native MCP server serves: the rows of
    `PLATFORM_TOOLS` in this release's `cheese.py`, the one table every harness
    reads. A module of its own, so the CLI's `__main__` block does not run."""
    module = types.ModuleType("cheese_platform_tools")
    exec(compile(cheese_source, "cheese", "exec"), module.__dict__)
    return list(module.PLATFORM_TOOLS.names())


def hook_module(proxy_source: str, target: dict, platform_tools: list[str]) -> str:
    """`proxy.js` as the plugin loads it, at launch and at every release."""
    return proxy_source.replace("__EXECUTION_CONFIG__", json.dumps(target)).replace(
        "__PLATFORM_TOOLS__", json.dumps(platform_tools)
    )


def allow_native_tools(settings: dict, platform_tools: list[str]) -> None:
    """Allow the native server's tools by name, the table's rows included."""
    allowed = settings.setdefault("permissions", {}).setdefault("allow", [])
    for name in ("invoke", *platform_tools):
        tool = "mcp__native__" + name
        if tool not in allowed:
            allowed.append(tool)


# Remote flags the build would otherwise never see. It offers some tools only
# when a GrowthBook flag says so, and it fetches those flags only for some
# logins: a session started with an OAuth token in the environment, as every
# session here is, fetches none, so Monitor was simply absent and agents waited
# with `sleep N; tail` instead (2.1.282, measured on the central host: 0 flags
# fetched; seeding this one entry offers Monitor and the build keeps it across
# restarts). Monitor's command goes through the shell prefix like Bash, so it
# runs on the executor. The same flag also makes Bash refuse a foreground
# command that starts with `sleep` of 25 s or more and point at Monitor
# (2.1.299), which is the waiting it replaces. The flag name and both effects
# belong to the pinned build: when the pin moves, `headless_contract.py`'s
# `remotemonitor` says whether they still hold.
SEEDED_FLAGS = {"tengu_amber_sentinel": True}


def write_claude_state(config: Path, seen: str) -> None:
    """The build's own state file, `.claude.json`, as a session needs it: no
    first-run gate to answer, and the flags it would not fetch."""
    gates = {
        "hasCompletedOnboarding": True,
        "autoUpdates": False,
        "bypassPermissionsModeAccepted": True,
        "projects": {
            seen: {
                "hasTrustDialogAccepted": True,
                "hasCompletedProjectOnboarding": True,
            }
        },
    }
    gate_file = config / ".claude.json"
    previous = json.loads(gate_file.read_text()) if gate_file.exists() else {}
    # Into the build's own cache, beside whatever flags it did fetch.
    gates["cachedGrowthBookFeatures"] = {
        **previous.get("cachedGrowthBookFeatures", {}),
        **SEEDED_FLAGS,
    }
    gate_file.write_text(json.dumps({**previous, **gates}))


def launch_only(sources):
    """The helper sources a running session keeps as they were at launch."""
    return {name: text for name, text in sources.items() if name not in RESIDENT}


def script(function, *args):
    return (
        Path(__file__).read_text()
        + "\nprint(json.dumps("
        + function
        + "(*json.loads("
        + repr(json.dumps(args))
        + "))))\n"
    )


def digest(sources):
    return hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()


def replace(path, content):
    path = Path(path)
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.write_text(content)
    temporary.chmod(path.stat().st_mode if path.exists() else 0o600)
    temporary.replace(path)


def expanded_path(path):
    return os.path.expandvars(
        path.replace(
            "${CHEESE_PROFILE_ROOT:-.cheese}",
            os.environ.get("CHEESE_PROFILE_ROOT", ".cheese"),
        )
    )


def stage(home, sources, seat="", session_id="", background=False):
    """Install a release into the session that asked for it.

    ``seat`` is that session's own directory inside the room's home
    (``place.seat_dir``), where its execution target and everything the client
    prepared beside it live; empty is a caller with no seat to name, which
    resolves to the room-level directory files sat in before seats existed.
    The transcript config dir is the room's. A named seat owns its helpers and
    hook settings, so releasing it cannot replace another seat's tool code or
    redirect its hooks. The busy check still reads room transcripts.
    ``background``: the session has a command running in the background. The
    release leaves the process and its commands running, so that only matters
    where the release would unmount the forwarded view the command may be
    reading.
    """
    config = Path(expanded_path(home)) / ".claude"
    platform_dir = Path(expanded_path(home)) / ".cheese"
    helpers = (
        Path(expanded_path(seat)) / "remote-execution"
        if seat
        else platform_dir / "remote-execution"
    )
    directory = (
        Path(expanded_path(seat)) / "remote-session"
        if seat
        else platform_dir / "remote-session"
    )
    target = json.loads((directory / "execution.json").read_text())
    settings_path = directory / "settings.json"
    settings = json.loads(settings_path.read_text())
    version = digest(sources)
    ready = directory / "release-ready"
    if ready.exists() and ready.read_text() == version:
        return {"changed": False, "version": version}
    transcripts = list(
        (config / "projects").glob(
            f"*/{session_id}.jsonl" if session_id else "*/*.jsonl"
        )
    )
    if transcripts:
        busy = False
        latest = max(transcripts, key=lambda path: path.stat().st_mtime_ns)
        for line in latest.read_text().splitlines():
            event = json.loads(line)
            if event.get("isMeta") or event.get("isSidechain"):
                continue
            message = event.get("message", {})
            if event.get("type") == "user":
                busy = True
            elif event.get("type") == "assistant" and message.get("stop_reason"):
                busy = message["stop_reason"] == "tool_use"
            elif (
                event.get("type") == "system"
                and event.get("subtype") == "local_command"
            ):
                busy = False
        if busy:
            # Replacing helpers under a running turn swaps the code its tool
            # calls are in. Said rather than raised: it is a reason to wait,
            # which the caller decides, not a failed release.
            return {"changed": False, "busy": True, "version": version}
    forwarded = directory / "forwarded-project"
    unmounts = Path(target.get("central_workspace", "")) != forwarded
    if background and unmounts and mount_state(forwarded) != MOUNT_NONE:
        return {"changed": False, "busy": True, "version": version}
    if unmounts:
        # `release_mount`, not a bare fusermount: it also takes down a mount whose
        # server has died, which is the one that would otherwise stay here forever.
        # Still loud on failure — replacing the helpers under a view that is still
        # mounted is what this unmount exists to prevent.
        if not release_mount(forwarded):
            raise RuntimeError(f"Could not release the forwarded view at {forwarded}")
    backup = helpers / "release-backups" / version
    paths = {name: helpers / name for name in sources}
    paths.update(settings=settings_path, proxy=directory / "plugin/hooks/proxy.js")
    for name, path in paths.items():
        destination = backup / name
        if path.exists() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            destination.chmod(0o600)
    # Install companions before client.py, which imports them on reconnect.
    for name in sorted(sources, key=lambda name: name == "client.py"):
        if Path(name).name != name:
            raise ValueError("A helper source must be a filename")
        replace(helpers / name, sources[name])
    platform_tools = platform_tool_names(sources["cheese.py"])
    replace(
        directory / "plugin/hooks/proxy.js",
        hook_module(sources["proxy.js"], target, platform_tools),
    )
    allow_native_tools(settings, platform_tools)
    if target.get("kind") != "private" and target.get("helper"):
        managed_context_hook = {
            "type": "command",
            "command": target["helper"][0],
            "args": [
                str(helpers / "context_service.py"),
                str(directory / "execution.json"),
            ],
        }
        for event in ("SessionStart", "UserPromptSubmit"):
            groups = []
            for group in settings.get("hooks", {}).get(event, []):
                hooks = [
                    hook
                    for hook in group.get("hooks", [])
                    if not all(
                        hook.get(key) == value
                        for key, value in managed_context_hook.items()
                    )
                ]
                if hooks:
                    groups.append({**group, "hooks": hooks})
            settings.get("hooks", {})[event] = groups
    replace(settings_path, json.dumps(settings))
    return {"changed": True, "version": version}


def acknowledge(home, version, seat=""):
    directory = (
        Path(expanded_path(seat)) / "remote-session"
        if seat
        else Path(expanded_path(home)) / ".cheese/remote-session"
    )
    replace(
        directory / "release-ready",
        version,
    )


# A skill's name is its entry in the config dir, where "/" cannot appear. A
# name Claude Code qualifies with a directory (`apps/web:deploy`) is spelled
# with this in its place; `proxy.js` takes the plain spelling in a Skill call.
NAME_SLASH = "∕"
# The state of the skills a session is offered only once a file tool reaches
# their files: the subdirectories whose `.claude/skills` it has reached, and
# the path-scoped skills a file it reached matched (`touch_skills`).
LAZY_STATE = "lazy-skills.json"
# For every entry in the config dir that is not the project's root skill of
# the same name: where the executor holds its files, and what Claude Code adds
# to its description (`proxy.js` reads both).
SKILL_PLACES = "skill-places.json"
# Left by `touch_skills` for the runner's next catch-up, which reloads skills.
SKILLS_CHANGED = "skills-changed"


class _Locked:
    """One writer at a time to a session's config dir: the runner's catch-up
    and the session's own file tools both relink it."""

    def __init__(self, directory):
        self.path = Path(directory) / "skills.lock"

    def __enter__(self):
        self.stream = self.path.open("a")
        if sys.platform == "win32":
            # deferred-import: only imported under a sys.platform branch
            import runpy

            # The Windows lock shipped beside this file, as runtime.py loads it.
            portable = runpy.run_path(str(Path(__file__).with_name("portable.py")))
            portable["lock"](self.stream)
        else:
            # deferred-import: POSIX-only module; absent on Windows
            import fcntl

            fcntl.flock(self.stream, fcntl.LOCK_EX)

    def __exit__(self, *_):
        self.stream.close()


def _lazy_state(directory):
    path = Path(directory) / LAZY_STATE
    state = json.loads(path.read_text()) if path.exists() else {}
    return {
        "directories": state.get("directories", []),
        "skills": state.get("skills", []),
        "generated": state.get("generated", []),
    }


def _split_outside_braces(value):
    pieces, current, depth = [], "", 0
    for character in value:
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif character == "," and depth == 0:
            pieces.append(current)
            current = ""
            continue
        current += character
    return [*pieces, current]


def _expand_braces(value):

    done, pending = [], [value]
    while pending:
        item = pending.pop()
        match = re.match(r"^([^{]*)\{([^}]+)\}(.*)$", item)
        if not match:
            done.append(item)
            continue
        head, options, tail = match.groups()
        pending.extend(head + option.strip() + tail for option in options.split(","))
    return done


def path_patterns(values):
    """The patterns a skill's `paths` scopes it to, read as Claude Code reads
    them: split at top-level commas, braces expanded, a trailing `/**` dropped.
    None when they scope it to nothing narrower than everything."""
    patterns = []
    for value in values or []:
        for piece in _split_outside_braces(value):
            piece = piece.strip()
            if len(piece) >= 2 and piece[0] == piece[-1] and piece[0] in "'\"":
                piece = piece[1:-1]
            for pattern in _expand_braces(piece) if piece else []:
                if pattern.endswith("/**"):
                    pattern = pattern[:-3]
                if pattern:
                    patterns.append(pattern)
    if not patterns or all(pattern == "**" for pattern in patterns):
        return None
    return patterns


def _pattern(pattern):

    negate = pattern.startswith("!")
    if negate:
        pattern = pattern[1:]
    directory_only = pattern.endswith("/")
    pattern = pattern.rstrip("/")
    anchored = "/" in pattern
    pattern = pattern.lstrip("/")
    out, index = "", 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            out, index = out + "(?:.*/)?", index + 3
        elif pattern.startswith("**", index):
            out, index = out + ".*", index + 2
        elif pattern[index] == "*":
            out, index = out + "[^/]*", index + 1
        elif pattern[index] == "?":
            out, index = out + "[^/]", index + 1
        elif pattern[index] == "[" and "]" in pattern[index + 2 :]:
            end = pattern.index("]", index + 2)
            members = pattern[index + 1 : end]
            if members.startswith("!"):
                members = "^" + members[1:]
            out, index = out + "[" + members + "]", end + 1
        elif pattern[index] == "\\" and index + 1 < len(pattern):
            out, index = out + re.escape(pattern[index + 1]), index + 2
        else:
            out, index = out + re.escape(pattern[index]), index + 1
    return negate, directory_only, re.compile(("" if anchored else "(?:.*/)?") + out)


def path_matches(patterns, relative):
    """Whether a file at `relative` (to the project) is one `patterns` name,
    by gitignore rules, as Claude Code decides whether to offer a path-scoped
    skill: the file or any directory above it matching is enough."""
    if not relative or relative.startswith("../") or relative.startswith("/"):
        return False
    compiled = [_pattern(pattern) for pattern in patterns]
    parts = relative.split("/")
    for depth in range(1, len(parts) + 1):
        candidate = "/".join(parts[:depth])
        is_directory = depth < len(parts)
        matched = False
        for negate, directory_only, expression in compiled:
            if directory_only and not is_directory:
                continue
            if expression.fullmatch(candidate):
                matched = not negate
        if matched:
            return True
    return False


def _skill_scope(entries, skill):
    """The `paths` patterns of the skill at `skill` (a directory in the
    project, or a link to one), or None when it applies everywhere."""
    entry = entries.get(skill, {})
    if entry.get("kind") == "symlink":
        skill = os.path.normpath(
            os.path.join(os.path.dirname(skill), entry.get("target", ""))
        ).replace(os.sep, "/")
    return path_patterns(entries.get(skill + "/SKILL.md", {}).get("paths"))


def _skills_in(entries, parent):
    prefix = parent + "/"
    return sorted(
        name[len(prefix) :]
        for name, entry in entries.items()
        if name.startswith(prefix)
        and "/" not in name[len(prefix) :]
        and entry["kind"] in ("directory", "symlink")
    )


def _without_paths(text):
    """SKILL.md's text with its frontmatter's `paths` taken out."""

    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    out, dropping = [lines[0]], False
    for index in range(1, len(lines)):
        line = lines[index]
        if line.strip() == "---":
            out.extend(lines[index:])
            break
        if dropping and (line[:1].isspace() or line.startswith("-")):
            continue
        dropping = bool(re.match(r"paths\s*:", line))
        if not dropping:
            out.append(line)
    return "\n".join(out)


#: Bytes asked of the executor per read of one context file.
CONTEXT_READ_BYTES = 1024 * 1024


def copy_context(target, tree, client):
    """The project's context — the tree a forwarded view would show — as plain
    files in the session's own directory (`central_workspace`), for a session
    whose host has no view of a project held on another machine (a member's
    own Claude Code, `apart`). Claude Code reads the project's instructions,
    rules, skills and agents from there as it would from the view; what is
    gone from the tree is taken away, and a file whose size and time are
    unchanged is not read again."""
    root = Path(target["central_workspace"])
    record = Path(target["target_file"]).with_name("context-copy.json")
    copied = json.loads(record.read_text()) if record.exists() else {}
    entries = tree["entries"]
    remote_root = target["workspace"]
    for name in sorted(set(copied) - set(entries), reverse=True):
        path = root / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    for name in sorted(entries):
        entry = entries[name]
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            # i18n-exempt: session-host helper, stdlib only, no catalog
            raise ValueError("Invalid remote context path")
        path = root / relative
        shape = [entry["kind"], entry.get("size"), entry.get("mtime_ns")]
        shape.append(entry.get("target"))
        if copied.get(name) == shape and (path.exists() or path.is_symlink()):
            continue
        if path.is_symlink() or (path.exists() and entry["kind"] != "directory"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        path.parent.mkdir(parents=True, exist_ok=True)
        if entry["kind"] == "directory":
            path.mkdir(exist_ok=True)
        elif entry["kind"] == "symlink":
            # A link into the project points into this copy of it, as the
            # view shows it (`forwarded_fs.readlink`).
            link = entry["target"]
            if link == remote_root or link.startswith(remote_root + "/"):
                link = str(root) + link[len(remote_root) :]
            path.symlink_to(link)
        else:
            data = b""
            while True:
                piece = base64.b64decode(
                    client.call(
                        "context_fs",
                        {
                            "operation": "read",
                            "path": name,
                            "offset": len(data),
                            "size": CONTEXT_READ_BYTES,
                        },
                    )["data"]
                )
                data += piece
                if len(piece) < CONTEXT_READ_BYTES:
                    break
            temporary = path.with_name(path.name + ".next")
            temporary.write_bytes(data)
            temporary.replace(path)
        copied[name] = shape
    for name in set(copied) - set(entries):
        del copied[name]
    record.write_text(json.dumps(copied))


def link_forwarded_user_context(directory, config, forwarded, tree, helpers, view=None):
    """Expose forwarded project context through Claude's managed user source.

    `forwarded` is where the session sees the project, and what the links
    name; `view` is where this process can read it, when that is elsewhere
    (the runner reads it outside the session's namespace)."""
    with _Locked(directory):
        _link(
            Path(directory),
            Path(config),
            Path(forwarded),
            tree,
            helpers,
            Path(view) if view else Path(forwarded),
        )


def _link(directory, config, forwarded, tree, helpers, view):
    entries = tree["entries"]
    backup = (
        Path(helpers)
        / "release-backups/forwarded-context/user-source"
        / hashlib.sha256(str(config).encode()).hexdigest()[:16]
    )
    for category in ("skills", "commands", "agents", "rules"):
        parent = config / category
        if parent.is_symlink():
            backup.mkdir(parents=True, exist_ok=True)
            link_backup = backup / f"{category}.symlink"
            if not link_backup.exists():
                link_backup.write_text(os.readlink(parent))
            parent.unlink()
            parent.mkdir()
    links = {}
    for name in entries:
        relative = Path(name)
        if relative.parts[:2] in {
            (".claude", "skills"),
            (".claude", "commands"),
            (".claude", "agents"),
            (".claude", "rules"),
        }:
            if len(relative.parts) == 3:
                links[config / relative.parts[1] / relative.parts[2]] = (
                    forwarded / relative
                )
            continue
    instructions = config / "CLAUDE.md"
    imports = [
        forwarded / name for name in ("CLAUDE.md", "CLAUDE.local.md") if name in entries
    ]
    # The project is at the executor's own path, which can hold a space; an
    # import ends at the first unescaped one, and Claude Code reads `\ ` as a
    # space inside it.
    wrapper = "".join("@" + str(path).replace(" ", "\\ ") + "\n" for path in imports)
    if instructions.is_symlink():
        instructions.unlink()
    elif instructions.exists() and instructions.read_text() != wrapper:
        instructions_backup = backup / "CLAUDE.md"
        if not instructions_backup.exists():
            instructions_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(instructions, instructions_backup)
    replace(instructions, wrapper)

    # Skills Claude Code offers only once a file tool reaches their files
    # (`touch_skills` records which it has reached):
    #
    #   a subdirectory's `.claude/skills`, from the first time a file under
    #   that subdirectory is read or written, under its own name, or under
    #   `dir:name` when a root skill or another subdirectory's has the name;
    #
    #   a skill whose `paths` scopes it to some files, from the first time one
    #   of them is. Linked as it is, the session holds it back itself and a
    #   native Read (`proxy.js`) offers it; one a Write or Edit reached is
    #   written here without its `paths`, so the session offers it outright.
    lazy = _lazy_state(directory)
    touched = set(lazy["skills"])
    ours = set(lazy["generated"])
    generated = {}
    places = {}
    for destination, source in list(links.items()):
        skill = str(source.relative_to(forwarded))
        if destination.parent.name == "skills" and skill in touched:
            if _skill_scope(entries, skill):
                generated[destination] = skill
                places[destination.name] = {"place": str(source)}
                del links[destination]
    regular = {
        path.name if path.parent.name == "skills" else path.stem
        for path in links
        if path.parent.name in ("skills", "commands")
    } | {path.name for path in generated}
    if (config / "skills").is_dir():
        regular |= {
            entry.name
            for entry in (config / "skills").iterdir()
            if entry.is_dir() and not entry.is_symlink() and entry.name not in ours
        }
    nested = [
        (parent, name)
        for parent in sorted(set(lazy["directories"]))
        for name in _skills_in(entries, parent + "/.claude/skills")
    ]
    counts = {}
    for _, name in nested:
        counts[name] = counts.get(name, 0) + 1
    for parent, name in nested:
        skill = f"{parent}/.claude/skills/{name}"
        if name in regular or counts[name] > 1:
            # As Claude Code names and describes it — a skill in a subdirectory
            # is listed with a path prefix, `apps/web:deploy`. Read off a
            # 2.1.299 turn (its own Skill tool description).
            entry = parent.replace("/", NAME_SLASH) + ":" + name
            note = (
                f' (scoped to {parent}/ — use this instead of the unscoped "{name}" '
                f"skill when the files being changed are under {parent}/)"
                if name in regular
                else f" (from {parent}/.claude/skills — applies when working on "
                f"files under {parent}/)"
            )
        else:
            entry = name
            note = (
                f" (from {parent}/.claude/skills — applies when working on "
                f"files under {parent}/)"
            )
        destination = config / "skills" / entry
        places[entry] = {"place": str(forwarded / skill), "note": note}
        if skill in touched and _skill_scope(entries, skill):
            generated[destination] = skill
        else:
            links[destination] = forwarded / skill

    state_path = directory / "forwarded-user-links.json"
    previous = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    wanted = {str(path.relative_to(config)) for path in (*links, *generated)}
    for name in sorted(
        previous - wanted, key=lambda value: value.count("/"), reverse=True
    ):
        path = config / name
        if path.is_symlink():
            path.unlink()
        elif path.is_dir() and path.name in ours:
            shutil.rmtree(path)
    for destination, source in links.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            if destination.resolve() == source.resolve():
                continue
            destination.unlink()
        elif destination.is_dir() and destination.name in ours:
            shutil.rmtree(destination)
        elif destination.exists():
            # The platform ships something by this name (its own skill, or a
            # way of working the project saved). Plain Claude Code gives a
            # personal skill precedence over a project skill of the same name
            # and carries on, and so does the room: the repository's stays in
            # the project, unlinked, and the session starts.
            wanted.discard(str(destination.relative_to(config)))
            continue
        destination.symlink_to(
            source,
            target_is_directory=entries[str(source.relative_to(forwarded))]["kind"]
            == "directory",
        )
    written = []
    for destination, skill in generated.items():
        if destination.is_symlink():
            destination.unlink()
        elif destination.exists() and destination.name not in ours:
            wanted.discard(str(destination.relative_to(config)))
            continue
        written.append(destination.name)
        destination.mkdir(parents=True, exist_ok=True)
        # Only SKILL.md is read here; a file beside it is read where the
        # executor holds it (`skill-places.json`).
        replace(
            destination / "SKILL.md",
            _without_paths((view / skill / "SKILL.md").read_text()),
        )
    lazy["generated"] = sorted(written)
    replace(directory / LAZY_STATE, json.dumps(lazy))
    # Whether any skill the session holds is scoped by `paths`: only then does
    # a Read need the session's own look at the file (`proxy.js`).
    scoped = any(
        _skill_scope(entries, str(source.relative_to(forwarded)))
        for destination, source in links.items()
        if destination.parent.name == "skills"
    )
    replace(directory / SKILL_PLACES, json.dumps({"places": places, "scoped": scoped}))
    replace(state_path, json.dumps(sorted(wanted)))


def touch_skills(directory, config, forwarded, helpers, path, view=None):
    """A file tool reached `path`: offer what Claude Code offers once one has.

    Records the subdirectories between the file and the project whose
    `.claude/skills` the session now offers, and the path-scoped skills the
    file matches, relinks, and leaves word for the runner's catch-up. True
    when anything is offered that was not."""
    directory = Path(directory)
    try:
        relative = Path(path).relative_to(Path(forwarded)).as_posix()
    except ValueError:
        return False
    tree_path = directory / "context-tree.json"
    if relative == "." or not tree_path.exists():
        return False
    tree = json.loads(tree_path.read_text())
    entries = tree["entries"]
    with _Locked(directory):
        lazy = _lazy_state(directory)
        directories, skills = set(lazy["directories"]), set(lazy["skills"])
        parts = relative.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent + "/.claude/skills" in entries:
                directories.add(parent)
        for parent in ("", *sorted(directories)):
            skills_dir = (parent + "/" if parent else "") + ".claude/skills"
            for name in _skills_in(entries, skills_dir):
                skill = f"{skills_dir}/{name}"
                patterns = _skill_scope(entries, skill)
                if patterns and path_matches(patterns, relative):
                    skills.add(skill)
        if directories == set(lazy["directories"]) and skills == set(lazy["skills"]):
            return False
        lazy.update(directories=sorted(directories), skills=sorted(skills))
        replace(directory / LAZY_STATE, json.dumps(lazy))
    link_forwarded_user_context(directory, config, forwarded, tree, helpers, view)
    (directory / SKILLS_CHANGED).touch()
    return True


def forget_touched_skills(directory, config):
    """A new session process starts with none of what the last one reached."""
    directory, config = Path(directory), Path(config)
    with _Locked(directory):
        lazy = _lazy_state(directory)
        for name in lazy["generated"]:
            path = config / "skills" / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        (directory / LAZY_STATE).unlink(missing_ok=True)


def project_dir(config, path):
    """Where the pinned build keeps the transcripts of sessions started at
    `path`: under its config dir, named for the path with every UTF-16 unit
    other than an ASCII letter or digit spelled `-`, and a name longer than
    200 cut there and followed by a hash of the path. `headless_contract.py`
    holds this to the build."""
    units = memoryview(path.encode("utf-16-le")).cast("H")
    name = "".join(
        chr(unit) if chr(unit).isascii() and chr(unit).isalnum() else "-"
        for unit in units
    )
    if len(name) > 200:
        digest = 0
        for unit in units:
            digest = (digest * 31 + unit) & 0xFFFFFFFF
        digest = abs(digest - (1 << 32) if digest >= 1 << 31 else digest)
        spelled = ""
        while True:
            digest, digit = divmod(digest, 36)
            spelled = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + spelled
            if not digest:
                break
        name = f"{name[:200]}-{spelled}"
    return Path(config) / "projects" / name


def carry_conversation(config, launch_args, cwd):
    """The build resumes a conversation only from the directory it keeps for
    the session's cwd, but one config dir holds every conversation the session
    runs, each started wherever its own place was then: the placeholder, one
    machine path, another. So the conversation this launch resumes (the
    runner appends `--resume <id>`, `claude_code.runner.Runner.start`) is
    gathered there from wherever its pieces are, and nothing else is moved:
    another conversation may still be writing where it is.

    A conversation resumed at another path goes on writing its transcript where
    it found it, but starts a new file there once that one has been moved away,
    so one transcript can be in several pieces. They are joined in the order
    they were last written."""
    args = list(launch_args)
    if "--resume" not in args[:-1]:
        return
    session_id = args[args.index("--resume") + 1]
    destination = project_dir(config, cwd)
    projects = Path(config) / "projects"
    for path in list(projects.glob(f"*/{session_id}")):
        if path.is_dir() and path.parent != destination:
            move_into(path, destination / session_id)
    pieces = sorted(
        (path for path in projects.glob(f"*/{session_id}.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    target = destination / f"{session_id}.jsonl"
    if not pieces or pieces == [target]:
        return
    destination.mkdir(parents=True, exist_ok=True)
    joined = destination / f".{session_id}.jsonl.joining"
    with joined.open("wb") as out:
        for piece in pieces:
            data = piece.read_bytes()
            out.write(data)
            if data and not data.endswith(b"\n"):
                out.write(b"\n")
    joined.replace(target)
    for piece in pieces:
        if piece != target:
            piece.unlink()


def move_into(source, destination):
    """`source`'s entries into `destination`, merging the directories both
    have. A conversation that already lived at `after` and was resumed at
    `before` keeps its transcript where it found it, but writes its subagents'
    transcripts and saved tool results under `before`'s directory for that
    conversation, which `after` has too."""
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_dir() and not entry.is_symlink() and target.is_dir():
            move_into(entry, target)
        else:
            entry.replace(target)
    source.rmdir()
