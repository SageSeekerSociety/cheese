"""The command registry: one command, started, read and stopped by its id.

A command the central session's shell prefix starts and one this executor's own
Bash tool starts are the same object here: a process group of its own, output
into files under `commands/`, so a reader that lost its answer picks up at the
byte it had reached — from any connection, for as long as the record is kept.
`Executor` in `runtime.py` forwards to these bodies.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

if __package__:
    from . import runtime_core as core
else:
    # A script run has this file's directory first on `sys.path`; one loaded by
    # path — `bootstrap` installs one, `predecessor` loads the release's — has
    # not, and the module it reads is the one beside it.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_core as core

# The `control` operations that reach a command rather than make one. `dispatch`
# in `runtime.py` reads this: an executor that is draining still runs these.
SHELL_INSPECT = {"read", "signal", "forget"}

# What the bodies below read of the runtime's primitives, named here the way
# they were named when they were `Executor`'s methods.
BASH_INLINE_CHARS = core.BASH_INLINE_CHARS
COMMAND_ID = core.COMMAND_ID
COMMAND_READ_BYTES = core.COMMAND_READ_BYTES
COMMAND_READ_WAIT_S = core.COMMAND_READ_WAIT_S
STOP_SIGNALS = core.STOP_SIGNALS
beside = core.beside
claude_shell = core.claude_shell
native_path = core.native_path
resolve_program = core.resolve_program
write_json = core.write_json


def shell_argv(self, script):
    """How the build starts its shell for one command: after its snapshot
    when it has one, and as a login shell when it does not."""
    snapshot = self.shell_snapshot()
    return [
        claude_shell(self.env),
        "-c",
        *([] if snapshot else ["-l"]),
        (
            f"source {shlex.quote(str(snapshot))} 2>/dev/null || true && "
            if snapshot
            else ""
        )
        + script,
    ]


def command_env(self):
    """The environment every command starts from: the room's, with the
    platform CLI first on PATH, and the build that runs here named where a
    native session names it for its commands (the snapshot's `rg` reads it)."""
    if self.cli_worker is not None:
        # Commands reach the CLI through it, so it has to be up first.
        self._ready_cli_worker()
    env = dict(
        self.env,
        PATH=str(self.programs / "remote-execution/bin")
        + os.pathsep
        + self.env.get("PATH", os.defpath),
    )
    if self.config.get("claude"):
        env["CLAUDE_CODE_EXECPATH"] = str(self.config["claude"])
    return env


def shell_snapshot(self):
    """The snapshot of this machine's shell that the pinned build writes.

    A native Claude Code session sources a snapshot of its own machine's
    shell — functions, aliases, options, PATH — before every command. The
    central session's snapshot is of the wrong machine, so every command
    here sources this one instead: `claude mcp serve` writes it into its
    config directory the first time its Bash tool runs, from the
    environment a command here gets, so asking that tool for `true` once
    is how this executor holds exactly the file a native session on this
    machine would. None when the build could not write one; a command then
    runs without, as a native one does.
    """
    with self.snapshot_lock:
        if self.snapshot is not None and self.snapshot.exists():
            return self.snapshot
        try:
            self.client("native").call(
                "tools/call", {"name": "Bash", "arguments": {"command": "true"}}
            )
        except Exception as exc:  # a command still runs
            self.log("shell-snapshot", "failed", error=str(exc))
            return None
        shell = claude_shell(self.env)
        kind = "zsh" if "zsh" in shell else "bash" if "bash" in shell else "sh"
        found = sorted(
            (self.state / "native-config/shell-snapshots").glob(
                f"snapshot-{kind}-*.sh"
            ),
            key=lambda path: path.stat().st_mtime,
        )
        self.snapshot = found[-1] if found else None
        return self.snapshot


def _record(self, command_id):
    if not isinstance(command_id, str) or not COMMAND_ID.fullmatch(command_id):
        raise ValueError("Invalid command id")
    return self.commands_dir / command_id


def start_command(
    self, command_id, argv, cwd, env, *, merge, stdin=None, watched, meta
):
    """Run one command as a process group of its own, output to its record.

    Its output goes to files and not through this process, so what it
    prints does not depend on anyone reading it at the time: a reader that
    loses its connection picks up at the byte it had reached.
    """
    record = self._record(command_id)
    record.mkdir()
    write_json(record / "command.json", {**meta, "watched": watched})
    out = (record / "out").open("wb")
    err = out if merge else (record / "err").open("wb")
    source = subprocess.DEVNULL
    if stdin is not None:
        (record / "in").write_bytes(stdin)
        source = (record / "in").open("rb")
    try:
        options = {
            "cwd": str(cwd),
            "env": env,
            "stdin": source,
            "stdout": out,
            "stderr": err,
        }
        if sys.platform == "win32":
            process = subprocess.Popen(
                resolve_program(argv),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                **options,
            )
        else:
            process = subprocess.Popen(argv, start_new_session=True, **options)
    finally:
        for stream in {out, err, source} - {subprocess.DEVNULL}:
            stream.close()
    with self.command_lock:
        self.running[command_id] = {
            "process": process,
            "read_at": time.monotonic(),
            "watched": watched,
        }
        if sys.platform != "win32":
            # Its own group, numbered by its pid (`start_new_session`): what an
            # idle executor ends before it stops (`end_left_behind`).
            self.groups.add(process.pid)
    self.log(command_id, "started", pid=process.pid, command=meta.get("command"))
    with contextlib.suppress(OSError):
        # The command's own run, apart from everything the platform spends
        # around it: the caller cannot see either end of this, and a tool
        # call's wall clock mixes the two (``read_command`` reports it).
        write_json(record / "ran", {"started": time.monotonic()})
    threading.Thread(
        target=self._wait_command, args=(command_id, process), daemon=True
    ).start()


def _wait_command(self, command_id, process):
    code = process.wait()
    # Publish only a complete code; ENOSPC can leave an empty or partial file.
    temporary = self._record(command_id) / ("exit." + uuid.uuid4().hex)
    written = False
    with contextlib.suppress(OSError):
        written = temporary.write_text(str(code)) == len(str(code))
    with self.command_lock:
        with contextlib.suppress(OSError):
            if written:
                temporary.replace(temporary.with_name("exit"))
        self.running.pop(command_id, None)
        self.collected[command_id] = time.time()
        # A command that ran until now is work until now (`idle_stop`).
        self.active_at = time.monotonic()
    with contextlib.suppress(OSError):
        self.log(command_id, "exited", exit_code=code)
    with contextlib.suppress(OSError, ValueError):
        record = self._record(command_id)
        began = json.loads((record / "ran").read_text())["started"]
        # Written beside the command's own record, so a reader that comes
        # later still sees it and one already reading waits for the exit file
        # only.
        write_json(record / "ran_ms", round((time.monotonic() - began) * 1000))


def _tree(self, pid):
    """The process and its descendants, parents first. A descendant that
    left the group (`setsid`) is still one: the build's own stop kills the
    tree, not the group."""
    rows = []
    if Path("/proc").is_dir():
        # /proc rather than `ps`: the private execution image has no `ps`.
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            # The command name sits in parentheses and may hold spaces;
            # the parent pid is the second field after it.
            rows.append((int(entry.name), int(stat[stat.rindex(")") + 2 :].split()[1])))
    else:
        # macOS, where not even `ps` runs in a room's sandbox.
        rows = beside("darwin")["processes"]()
    children = {}
    for child, parent in rows:
        children.setdefault(parent, []).append(child)
    found, pending = [], [pid]
    while pending:
        current = pending.pop(0)
        if current not in found:
            found.append(current)
            pending.extend(children.get(current, []))
    return found


def signal_command(self, command_id, number):
    """Send a signal to a running command's group and every descendant.

    A stop for a command not started yet is kept: the prefix's start and
    its stop travel separately, and a stop that overtook its start must
    still hold, so that start is refused (`shell`)."""
    record = self._record(command_id)
    with self.command_lock:
        entry = self.running.get(command_id)
        if entry is None and not record.exists():
            record.mkdir()
            write_json(
                record / "command.json",
                {"watched": True, "stopped_before_start": True, "command": ""},
            )
            (record / "exit").write_text(str(-number))
            self.log(command_id, "stopped before start", signal=number)
            return {"running": False}
    if entry is None:
        return {"running": False}
    pid = entry["process"].pid
    if sys.platform == "win32":
        beside("portable")["terminate_tree"](pid)
        return {"running": True}
    pids = self._tree(pid)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, number)
    for member in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(member, number)
    return {"running": True}


def stop_command(self, command_id):
    """Stop a command for good: TERM, then KILL for whatever ignored it."""
    record = self._record(command_id)
    if not (record / "command.json").exists():
        raise ValueError("Unknown remote task")
    meta = json.loads((record / "command.json").read_text())
    with self.command_lock:
        entry = self.running.get(command_id)
    if entry is not None:
        # Taken once, before TERM: a descendant that ignores TERM outlives
        # the shell that parented it and is re-parented, so a second look
        # would not find it.
        pids = [] if sys.platform == "win32" else self._tree(entry["process"].pid)
        self.signal_command(command_id, signal.SIGTERM)
        time.sleep(0.3)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if sys.platform != "win32":
                os.killpg(entry["process"].pid, signal.SIGKILL)
        self.log(command_id, "stopped")
    return meta


def _group_alive(group):
    try:
        os.killpg(group, 0)
    except (ProcessLookupError, PermissionError):
        # Gone, or a group of somebody else's that took the number: not ours.
        return False
    return True


def prune_groups(self):
    """Forget the groups that have no process left. A group's number is not
    handed out again while any process is in it, so a group still here is
    still the one a command of ours started (`end_left_behind`)."""
    with self.command_lock:
        running = {entry["process"].pid for entry in self.running.values()}
        self.groups = {g for g in self.groups if g in running or _group_alive(g)}


def _preview_declared(self):
    """Whether the room declared a dev server with `cheese serve` and its
    preview helper is still up: the port file `cheese-preview-up` writes and
    the pid it recorded (`machine_launcher.CHEESE_PREVIEW_UP`). A person may be
    looking at it, so it is work, and neither it nor the server goes."""
    platform = Path(self.env.get("HOME", "")) / ".cheese"
    try:
        port = (platform / "cheese-preview.port").read_text().strip()
        pid = int((platform / "cheese-preview.pid").read_text().strip())
    except (OSError, ValueError):
        return False
    if not port:
        return False
    if sys.platform == "win32":
        # The pid is Git Bash's own there; a recorded helper is taken as up.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def end_left_behind(self):
    """End every process still in a group a command of ours started: what
    the agent detached and left running (`nohup … &`, a dev server), which
    keeps nothing up (`machine.lifecycle`) and outlives the command that
    started it. TERM, five seconds, then KILL.

    What this cannot reach: a process that left its group — `setsid`, a
    `setpgid`, a daemon that double-forks into a session of its own, a
    background job of a shell running with job control (`set -m`). Nor, on
    Windows, anything: a command there is not a POSIX group, and only the
    executor itself stops."""
    self.prune_groups()
    with self.command_lock:
        groups = sorted(self.groups)
    if not groups:
        return []
    for number in (signal.SIGTERM, signal.SIGKILL):
        for group in groups:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(group, number)
        deadline = time.monotonic() + 5
        left = groups
        while left and time.monotonic() < deadline:
            time.sleep(0.1)
            left = [g for g in left if _group_alive(g)]
        if not left:
            break
    return groups


def idle_stop(self, idle_s):
    """Whether this executor has sat idle for `idle_s` and is to stop now.

    Idle is nothing of its own going on: no request in flight and none for
    `idle_s` (a `ping` is a question about it, not work for it), no command
    still running — a Bash sent to the background included — and no preview
    the room declared (`_preview_declared`). Then it stops taking requests
    and answers True; `serve` shuts down and, once its socket is gone, ends
    what the commands left behind (`end_left_behind`)."""
    if _preview_declared(self):
        self.active_at = time.monotonic()
        return False
    with self.admission_lock, self.command_lock:
        if (
            self.active_calls
            or self.running
            or time.monotonic() - self.active_at < idle_s
        ):
            return False
        # From here on nothing is admitted (`dispatch`), so no command can
        # start between this decision and the shutdown.
        self.stopping = True
    return True


def disk_full(path):
    """The sentence for a command that failed where the disk is full, or
    None while it has room. A `statvfs` of the workspace: on a platform host
    that is the room's own quota (`sandbox_host.py`). Full is under 1% of it
    left, or under 64 MB, in bytes or in files; a large disk with no quota of
    the room's is full only under 1 GiB or 10,000 files."""
    if not hasattr(os, "statvfs"):
        return None
    try:
        found = os.statvfs(path)
    except OSError:
        return None
    total, free = found.f_blocks * found.f_frsize, found.f_bavail * found.f_frsize
    files = found.f_favail
    if free >= min(max(total // 100, 64 << 20), 1 << 30) and (
        not found.f_files or files >= min(found.f_files // 100, 10_000)
    ):
        return None
    return f"disk full: {free // 10**6} MB free, {files} files left ({path})"


def read_command(self, command_id, out_offset=0, err_offset=0, wait=0.0):
    """What a command printed from the given offsets on, waiting briefly for
    more; its exit status and final directory once everything is read.

    Offsets are the reader's, so a reader that lost an answer asks again
    from where it was and gets each byte exactly once.
    """
    record = self._record(command_id)
    if not (record / "command.json").exists():
        raise ValueError("Unknown command")
    deadline = time.monotonic() + min(max(float(wait), 0.0), COMMAND_READ_WAIT_S)
    out_path, err_path = record / "out", record / "err"
    while True:
        with self.command_lock:
            entry = self.running.get(command_id)
            if entry is not None:
                entry["read_at"] = time.monotonic()
            self.collected[command_id] = time.time()
        # The exit file first: once it exists, the process wrote all it
        # will, so the sizes read after it cover everything it printed.
        exited = (record / "exit").exists()
        out_size = out_path.stat().st_size if out_path.exists() else 0
        err_size = err_path.stat().st_size if err_path.exists() else 0
        if entry is None and not exited:
            # Not ours to wait for, and never finished: the service that
            # started it is gone.
            return {"out": "", "err": "", "lost": True}
        if (
            out_size > out_offset
            or err_size > err_offset
            or exited
            or time.monotonic() >= deadline
        ):
            break
        time.sleep(0.05)

    def chunk(path, offset, size):
        if size <= offset:
            return b""
        with path.open("rb") as stream:
            stream.seek(offset)
            return stream.read(min(size - offset, COMMAND_READ_BYTES))

    out = chunk(out_path, out_offset, out_size)
    err = chunk(err_path, err_offset, err_size)
    answer: dict = {
        "out": base64.b64encode(out).decode(),
        "err": base64.b64encode(err).decode(),
    }
    if (
        exited
        and out_offset + len(out) >= out_size
        and err_offset + len(err) >= err_size
    ):
        answer["exit"] = int((record / "exit").read_text())
        with contextlib.suppress(OSError, ValueError):
            answer["ran_ms"] = int((record / "ran_ms").read_text())
        if answer["exit"] and (full := disk_full(self.root)):
            answer["disk"] = full
        # As the shell spelled it, which is the session's spelling too
        # (`native_path`).
        path = record / "cwd"
        if path.exists():
            text = path.read_text(errors="replace")[:4096].strip()
            if text:
                answer["cwd"] = text
    return answer


def _final_directory(self, record):
    """The directory a command's shell ended in, as its `pwd -P` wrote it."""
    path = record / "cwd"
    if not path.exists():
        return None
    text = path.read_text(errors="replace")[:4096].strip()
    return Path(native_path(text)) if text else None


def shell(self, params):
    """The central session's shell prefix: one command, started, read and
    signalled by the id its prefix chose. Starting it again with the same
    input is answered as started, so a prefix whose answer was lost asks
    again without running it twice.

    A `control` rather than a method of its own: the route that carries
    executor calls from a session is served by the device connection's
    owner, which an app release leaves on its previous image, and that
    route admits a fixed set of methods."""
    operation = params.get("operation")
    command_id = params.get("command_id")
    record = self._record(command_id)
    if operation == "start":
        inputs = {
            key: params.get(key)
            for key in ("kind", "body", "cwd", "env", "merge", "stdin", "keep")
        }
        # A kept record holds the command's output for a later command to read
        # (`shell_prefix.task_outputs`); `drop` names those the session let go.
        held = {"held": str(record / "out")} if params.get("keep") else {}
        for dropped in params.get("drop") or []:
            with contextlib.suppress(ValueError):
                shell(self, {"operation": "forget", "command_id": dropped})
        digest = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        with self.command_lock:
            if record.exists():
                meta = json.loads((record / "command.json").read_text())
                if meta.get("stopped_before_start"):
                    # Its stop came first: it never runs, and reads as
                    # ended by that signal.
                    return {"started": False}
                if meta.get("digest") != digest:
                    raise ValueError("Command ID already belongs to different input")
                return {"started": True, **held}
            cwd = Path(native_path(params.get("cwd") or str(self.root)))
            # 看不见的 cwd 会抛 EPERM：Path.is_dir 吞不住，os.path.isdir 当它不在
            if not os.path.isdir(cwd):
                cwd = self.root
            env = self.command_env()
            env.update(
                (name, str(value))
                for name, value in (params.get("env") or {}).items()
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            )
            if params.get("kind") == "bash":
                argv = self.shell_argv(
                    params["body"] + " && pwd -P >| " + shlex.quote(str(record / "cwd"))
                )
            elif params.get("kind") == "sh":
                argv = (
                    [claude_shell(self.env), "-c", params["body"]]
                    if sys.platform == "win32"
                    else ["/bin/sh", "-c", params["body"]]
                )
            else:
                raise ValueError("Unknown command kind")
            stdin = params.get("stdin")
            self.start_command(
                command_id,
                argv,
                cwd,
                env,
                merge=bool(params.get("merge")),
                stdin=None if stdin is None else base64.b64decode(stdin),
                watched=True,
                meta={
                    "digest": digest,
                    "command": params["body"][-2000:],
                    "kept": bool(held),
                },
            )
        return {"started": True, **held}
    if operation == "read":
        return self.read_command(
            command_id,
            int(params.get("out", 0)),
            int(params.get("err", 0)),
            params.get("wait", 0),
        )
    if operation == "signal":
        number = int(params["signal"])
        if number not in STOP_SIGNALS:
            raise ValueError("Unsupported signal")
        return self.signal_command(command_id, number)
    if operation == "forget":
        with self.command_lock:
            if command_id in self.running:
                raise ValueError("Command is still running")
            shutil.rmtree(record, ignore_errors=True)
            self.collected.pop(command_id, None)
        return {}
    raise ValueError("Unknown shell operation")


def bash(self, args, key=None):
    """This executor's own Bash tool, for the callers that have no shell of
    their own here: the Codex harness and the platform's own commands.

    It keeps the build's Bash contract those callers were written against:
    a `cd` holds for the next call and leaving the workspace returns to its
    root, a failure comes back as `Exit code N` followed by the output, a
    foreground command still running at its timeout goes on as a background
    task, and `TaskStop` stops one. So does one the caller moves to the
    background while it runs (`background`), as Ctrl+B does to the build's.
    """
    command_id = "task-" + uuid.uuid4().hex[:16]
    record = self._record(command_id)
    cwd = self.bash_cwd if self.bash_cwd.is_dir() else self.root
    env = self.command_env()
    # A platform call may name variables for this one command (`checkpoint`'s
    # turn). Its own key, so a Bash argument from anywhere else cannot set one.
    if isinstance(args.get("platform_env"), dict):
        env.update({str(k): str(v) for k, v in args["platform_env"].items()})
    self.start_command(
        command_id,
        self.shell_argv(
            f"eval {shlex.quote(args['command'])} < /dev/null && pwd -P >| "
            + shlex.quote(str(record / "cwd"))
        ),
        cwd,
        env,
        merge=True,
        watched=False,
        meta={"command": args["command"]},
    )
    running = {
        "stdout": "",
        "stderr": "",
        "interrupted": False,
        "noOutputExpected": False,
        "backgroundTaskId": command_id,
        # Where it goes on printing: a file on this machine, so the caller
        # can read it with the same tools it reads the project with.
        "outputFile": str(record / "out"),
    }
    if args.get("run_in_background"):
        return running
    deadline = (
        time.monotonic() + min(max(args.get("timeout", 120000), 1), 600000) / 1000
    )
    with self.command_lock:
        self.foreground[key] = command_id
    try:
        while not (record / "exit").exists():
            with self.command_lock:
                yielded = key in self.backgrounded
                self.backgrounded.discard(key)
            if yielded or time.monotonic() >= deadline:
                return running
            time.sleep(0.05)
    finally:
        with self.command_lock:
            self.foreground.pop(key, None)
            self.backgrounded.discard(key)
    code = int((record / "exit").read_text())
    if code < 0:
        # Killed by signal n: the number a shell reports.
        code = 128 - code
    final = self._final_directory(record)
    self.bash_cwd = (
        final
        if final is not None and (final == self.root or self.root in final.parents)
        else self.root
    )
    with (record / "out").open("rb") as stream:
        text = stream.read(BASH_INLINE_CHARS * 4).decode("utf-8", "replace")
    text = text[:BASH_INLINE_CHARS].rstrip("\n")
    if code and (full := disk_full(self.root)):
        text += "\n" + full
    return {
        "stdout": text if code == 0 else f"Exit code {code}\n{text}",
        "stderr": "",
        "interrupted": False,
        "noOutputExpected": False,
    }
