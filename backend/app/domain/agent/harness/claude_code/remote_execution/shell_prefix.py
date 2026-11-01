"""The shell prefix: one command the build hands it, run where it belongs.

The build wraps each Bash command and hands the wrapper to this program's
`shell` mode. A platform command this host trusts, matched whole, runs here;
anything else runs on the executor, and a process of this host stands in for
it towards the build: it prints what the command prints as it prints it, ends
when it ends and the way it ended, passes on a signal the build sends it, and
for a Bash command writes into the build's own cwd file the directory the
shell ended in (`_report_directory`). It also names the skill files a session
refers to where the executor holds them (`skill_paths`), and the files the
build sends a command's output to (`task_outputs`). Shipped beside
`client.py` (`release.sources`).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

if __package__:
    from app.domain.agent.executor_transport import (
        DEFERRED_WORKSPACE,
        MACHINE_OUT_OF_REACH,
        WORKING_DIRECTORY,
        MachineOutOfReach,
        RemoteClient,
        session_path,
        to_machine,
    )
    from app.domain.agent.harness.claude_code.remote_execution.shell_stop import (
        CAUGHT,
        KILL,
        current_target,
        start_watcher,
    )
    from app.domain.agent.pr_creation import refuse_in_shell
else:
    # Source scripts find the shared module in agent/; deployed bundles ship
    # the same module beside this script, which remains first on sys.path.
    sys.path.append(str(Path(__file__).resolve().parents[3]))
    from executor_transport import (
        DEFERRED_WORKSPACE,
        MACHINE_OUT_OF_REACH,
        WORKING_DIRECTORY,
        MachineOutOfReach,
        RemoteClient,
        session_path,
        to_machine,
    )
    from pr_creation import refuse_in_shell

    # Its siblings sit beside it, which a script run has first on sys.path and
    # a file loaded by path has not.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from shell_stop import CAUGHT, KILL, current_target, start_watcher

# What the build adds to its shell children's environment, forwarded to a
# command on the executor because a native session there would set the same.
# Nothing else of this host's environment leaves it — its credentials are in
# there — and the build's messaging socket and its token name a process on
# this host, so they stay too.
FORWARDED_ENV = (
    "AI_AGENT",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "COREPACK_ENABLE_AUTO_PIN",
    "GIT_EDITOR",
    "NoDefaultCurrentDirectoryInExePath",
)


def bind_working_directory(body, value, *, spelled_later=False):
    """``body`` with the session's short name bound in the shell that runs it.

    Not in the command's environment: a build running natively on the executor
    has no variable there, and a room's command must see exactly the environment
    that one sees (the equivalence check holds the two equal). Bound as a shell
    variable it is still there for what the line is told to write, `cd "$T"`.
    The assignment stays on the same line so Bash reports the command's original line numbers.
    ``value`` is the directory as the machine holds it (`outward`), so the line
    bound here is the one the command's other paths are spelled with.

    ``spelled_later`` is the placeholder a session names before its machine is
    rented: the transport substitutes the machine's path into ``body`` after
    this point, textually. Single quotes keep whatever it substitutes one word —
    a machine path can have a space — and are not escaped, since an escaped
    value would no longer match the placeholder it replaces. A placeholder that
    itself has a quote cannot be spelled that way and is left unbound (the value
    stays a directory this host does not hold, so binding it would be no better).
    """
    if value is None:
        return body
    if spelled_later:
        if "'" in value:
            return body
        return f"{WORKING_DIRECTORY}='{value}'; {body}"
    return f"{WORKING_DIRECTORY}={shlex.quote(value)}; {body}"


# The build wraps each Bash command as
# `source <snapshot> 2>/dev/null || true && <options> && eval '<command>'
# < /dev/null && pwd -P >| <tmp>/claude-<hex>-cwd`. The snapshot is of this
# host's shell and the cwd file is where the build learns the new directory,
# so the executor swaps in its own snapshot and file, and the prefix copies
# the directory back.
_SNAPSHOT = re.compile(r"source ('(?:[^']|'\\'')*'|\S+) 2>/dev/null \|\| true && ")
_CWD_FILE = re.compile(r" && pwd -P >\| ('(?:[^']|'\\'')*'|\S+)\Z")
# The stdin a hook command may carry to the executor, and the output a command
# may write onto this host (the build persists what it is handed).
SHELL_INPUT_BYTES = 16 * 1024 * 1024
SHELL_OUTPUT_BYTES = 256 * 1024 * 1024
# How long a command that never started keeps being retried across a dropped
# link before the machine is reported out of reach; how long a reader keeps
# retrying an executor that answers, but with an error, before giving it up.
# Retries are hand-written, not tenacity: this ships stdlib-only (`release.sources`).
SHELL_START_RETRY_S = 60.0
SHELL_ERROR_RETRY_S = 60.0
SHELL_READ_RETRY_S = (0.5, 5.0)  # first and longest wait between read retries


def _same_file(first, second):
    try:
        a, b = os.fstat(first), os.fstat(second)
    except OSError:
        return False
    return stat.S_ISREG(a.st_mode) and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def run_in_place(argv, env=None):
    """Run argv as this process: exec it, where there is exec.

    Windows has none. Its os.exec* starts a new process and ends this one, and
    whoever waits on this one — the runner on its agent, Claude Code on a
    command — takes the program for ended while it runs on, outside the tree
    that is ended with the session. There it runs as a child, on the handles
    this process holds, and this process ends the way it ended."""
    if sys.platform != "win32":
        if env is None:
            os.execvp(argv[0], argv)
        os.execvpe(argv[0], argv, env)
    path = (env if env is not None else os.environ).get("PATH")
    program = shutil.which(argv[0], path=path) or argv[0]
    try:
        code = subprocess.call([program, *argv[1:]], env=env)
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


def _under(path, root):
    return bool(root) and (path == root or path.startswith(root + "/"))


def shell(target_path, command):
    """One command the build hands its shell prefix: run where it belongs.

    A platform command this host trusts, matched whole, runs here. Anything
    else runs on the executor, and this process stands in for it towards the
    build: it prints what the command prints as it prints it, ends when it
    ends and the way it ended, passes on a signal the build sends it, and for
    a Bash command tells the build which directory the shell ended in. The
    command itself is the executor's, not this process's: a dropped link only
    pauses the reading, and the reader picks up at the byte it had reached.
    """
    target = json.loads(Path(target_path).read_text())
    words = shlex.split(command)
    # The build spells this program's path as `client.py`'s, which ships
    # beside this file; the match below is on that path, not on this one's.
    helper = str(Path(__file__).with_name("client.py").resolve())
    # Only the platform's own transport and guard run on the central host.
    if (
        len(words) >= 4
        and words[:2] == [sys.executable, helper]
        and words[2] in ("guard", "context", "checkpoint", "transport", "reply")
        and words[3] == str(target_path)
    ):
        run_in_place(words)
    commands = {
        hook["command"]
        for groups in target.get("central_hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
        if hook.get("type") == "command"
    }
    if command in commands:
        run_in_place(["sh", "-c", command])
    # The build hands this process's stdout and stderr to the command: they
    # are the command's output, and nothing of the platform's may be in them.
    # What the transport logs on its way through a retry (a 409 while the
    # device reconnects, say) goes to this session's own log instead. Only a
    # command that could not run at all says so there, in the platform's words.
    logging.basicConfig(
        filename=str(Path(target_path).parent / "shell-prefix.log"),
        format="%(asctime)s %(process)d %(levelname)s %(message)s",
        level=logging.INFO,
    )
    return run_on_the_machine(target, command)


def skill_places(target):
    """Where the executor holds each skill the session names by an entry that
    is not the project's root skill of that name: a subdirectory's, and one
    written without its `paths` (`release.SKILL_PLACES`)."""
    path = Path(target.get("target_file", "")).parent / "skill-places.json"
    try:
        places = json.loads(path.read_text())["places"]
    except (OSError, ValueError, KeyError):
        return {}
    return {name: place["place"] for name, place in places.items()}


def skill_paths(target, text):
    """`text` with each skill file named in the session's config dir named
    where the executor holds it: a project skill in the project's
    `.claude/skills`, one the platform shipped in the executor's config dir.
    `proxy.js` `skillPath` is the same mapping, for the file tools."""
    skills = target["central_config"] + "/skills/"
    project = session_path(target["workspace"]) + "/.claude/skills/"
    shipped = set(target.get("shipped_skills") or [])
    executor = target.get("executor_config")
    places = skill_places(target)

    def place(match):
        name = match.group(1)
        if name in places:
            return places[name]
        if name in shipped:
            return f"{executor}/skills/{name}" if executor else match.group(0)
        return project + name

    return re.sub(re.escape(skills) + r"([^/\s'\"`]+)", place, text)


# The build's file for one command's output, under its temporary directory:
# `claude-<uid>/<project>/<session>/tasks/<id>.output`.
_TASK_OUTPUT = r"(?:[^/\s'\"`]+/)+tasks/[A-Za-z0-9_-]+\.output"
# A path that reads the same unquoted, so it can stand where one was written.
_PLAIN = re.compile(r"[A-Za-z0-9_./:@+-]+")


# The build sends every Bash command's output to a file of its own here, and
# keeps the file of one it ran in the background: its TaskOutput, its
# notification and Monitor read it. A command that reads it later — `tail`,
# `cat`, `grep` — runs on the executor, where the file is not. The executor's
# record of the command holds the same bytes (`runtime_commands` `keep`), so
# the file is named by the record's path there. Which record holds which file
# is kept in `task-outputs/` beside the target, one entry per file.


def _output_places(target):
    return Path(target["target_file"]).parent / "task-outputs"


def _output_entry(target, name):
    return _output_places(target) / hashlib.sha256(name.encode()).hexdigest()[:32]


def task_output(target):
    """This command's output file, relative to the build's temporary
    directory, when the build sent its output to one; otherwise None."""
    if not target.get("central_tmp") or not target.get("target_file"):
        return None
    try:
        if sys.platform == "linux":
            path = os.readlink("/proc/self/fd/1")
        elif sys.platform == "darwin":
            # deferred-import: fcntl is absent on Windows, where this never runs
            import fcntl

            path = fcntl.fcntl(1, fcntl.F_GETPATH, bytes(1024)).split(b"\0")[0]
            path = path.decode()
        else:
            return None
    except (OSError, UnicodeDecodeError):
        return None
    root = os.path.realpath(target["central_tmp"]) + "/"
    path = os.path.realpath(path)
    if not path.startswith(root) or not re.fullmatch(_TASK_OUTPUT, path[len(root) :]):
        return None
    return path[len(root) :]


def keep_output(target, name, command_id, answer):
    """Record where the executor holds the output file `name`; whether it
    does. An executor that does not keep it, or holds it at a path a command
    could not name unquoted, leaves the file named as it was."""
    held = (answer or {}).get("held")
    if not held or not _PLAIN.fullmatch(session_path(held)):
        return False
    entry = _output_entry(target, name)
    entry.parent.mkdir(exist_ok=True)
    temporary = entry.with_name(entry.name + "." + uuid.uuid4().hex)
    temporary.write_text(
        json.dumps({"name": name, "command_id": command_id, "held": session_path(held)})
    )
    temporary.replace(entry)
    return True


def dropped_outputs(target):
    """The commands whose output file the build has since deleted — one it
    waited for, whose output it took — which the executor forgets. Each is
    told once."""
    found = []
    if not target.get("central_tmp") or not target.get("target_file"):
        return found
    places = _output_places(target)
    for entry in places.iterdir() if places.is_dir() else ():
        try:
            kept = json.loads(entry.read_text())
        except (OSError, ValueError):
            continue
        if not (Path(target["central_tmp"]) / kept["name"]).exists():
            entry.unlink(missing_ok=True)
            found.append(kept["command_id"])
    return found


def task_outputs(target, text):
    """`text` with each output file the executor holds named where it does."""
    root = target.get("central_tmp")
    if not root or root not in text or not target.get("target_file"):
        return text

    def place(match):
        try:
            kept = json.loads(_output_entry(target, match.group(1)).read_text())
        except (OSError, ValueError):
            return match.group(0)
        return kept["held"]

    return re.sub(re.escape(root) + "/(" + _TASK_OUTPUT + ")", place, text)


def run_on_the_machine(target, command):
    # The session sees the project at the executor's own path, so a command and
    # its directory need no respelling. Its skills are the one thing the build
    # reads from this host's config directory (`link_forwarded_user_context`),
    # where the executor has none: a command naming a skill's file names it in
    # the project. So are the files it sends a command's output to, which a
    # command names where the executor holds them (`task_outputs`). A session
    # in a directory of its own names the executor's.
    def outward(text):
        seen = target.get("virtual_workspace")
        text = to_machine(text, seen, target["workspace"]) if seen else text
        return task_outputs(target, skill_paths(target, text))

    cwd = os.getcwd()
    head, tail = _SNAPSHOT.match(command), _CWD_FILE.search(command)
    bash = tail is not None
    if bash:
        # A Bash tool command: the executor wraps its body with its own
        # snapshot and cwd file. Its stdin is `/dev/null` by construction.
        body = command[head.end() if head else 0 : tail.start()]
        stdin = None
        output = task_output(target)
        # The platform opens the task's PR (`pr_creation`).
        if (refused := refuse_in_shell(body)) is not None:
            return refused
    else:
        # A hook: its input arrives on stdin, spelled for this host.
        body, output = command, None
        data = b""
        with os.fdopen(os.dup(0), "rb") as source:
            data = source.read(SHELL_INPUT_BYTES + 1)
        if len(data) > SHELL_INPUT_BYTES:
            raise SystemExit("Hook input is over the executor's limit")
        stdin = base64.b64encode(
            outward(data.decode("utf-8", "surrogateescape")).encode(
                "utf-8", "surrogateescape"
            )
        ).decode()
    env = {name: os.environ[name] for name in FORWARDED_ENV if name in os.environ}
    if "CLAUDE_PROJECT_DIR" in os.environ:
        env["CLAUDE_PROJECT_DIR"] = outward(os.environ["CLAUDE_PROJECT_DIR"])
    # The short name is bound in the command's shell, not exported
    # (`bind_working_directory`): this session's spelling of its directory goes
    # through the same `outward` as every other path a command names, so the
    # shell gets it whole. While the target still names the placeholder the
    # machine has not been rented, and only the transport can spell the project
    # — after this point, inside `body`; the binding quotes for that
    # substitution rather than escaping a value that is about to be replaced.
    short_name = os.environ.get(WORKING_DIRECTORY)
    spelled_later = target.get("workspace") == DEFERRED_WORKSPACE
    client = RemoteClient(target)
    resolved = target.get("working_directory")
    # A target with a lease can be for a machine the answer was not resolved
    # on, and `acquire` drops the answer then: only a target with no lease to
    # take is fixed enough to bind it before the command runs.
    trusted = resolved if not target.get("lease_path") else None
    if short_name is not None and trusted:
        # The target file keeps the answer (`working_directory`): the launch's
        # name was the placeholder, or the launch could not ask and a later
        # command did. It is the session's directory from now on.
        short_name = trusted
        spelled_later = False
    elif short_name is not None and not spelled_later:
        placeholder = target.get("virtual_workspace")
        if short_name == DEFERRED_WORKSPACE or (
            placeholder and short_name == placeholder != target.get("workspace")
        ):
            if target.get("lease_path"):
                # The session started before its machine was rented, so its
                # name is the placeholder. It stays one here: the transport
                # spells it after it takes the hands, where the checkout is
                # known — a machine the session was moved to holds another one
                # than the target file says.
                spelled_later = True
            else:
                # No lease to take: where the machine holds the project is what
                # this target says, so the checkout can be asked for here.
                short_name = client.working_directory(notice=False)
        elif target.get("checkout_pending"):
            # The launch could not ask for the checkout; the machine is here.
            short_name = client.working_directory(notice=False)
        else:
            short_name = outward(short_name)
    if trusted and os.path.normpath(cwd) == os.path.normpath(
        target.get("workspace") or ""
    ):
        # The build still stands at the project view while the session's own
        # directory is somewhere else (a launch whose lookup failed, or a
        # placeholder session just resolved): the command starts where the
        # session is, as it would once it had cd'ed there itself.
        cwd = resolved
    merge = _same_file(1, 2)
    command_id = "shell-" + uuid.uuid4().hex

    def call(operation, **params):
        return client.call(
            "control",
            {
                "subtype": "shell",
                "operation": operation,
                "command_id": command_id,
                **params,
            },
            preparing=sys.stderr if bash else None,  # a hook's stderr is its answer
        )

    # A stop has to reach the command even when this process does not live to
    # send it: the build follows its TERM with a KILL about a second later, and
    # a command whose start was still on its way would otherwise run on with
    # nobody left to stop it. So a watcher that outlives this process delivers
    # it (`shell_stop.deliver_stop`), told through a pipe: `stop <n>` when the
    # build asks, `done` when the command has ended, and nothing — the pipe
    # closing — when this process was killed first.
    watcher = start_watcher(target, command_id)

    def on_signal(number, _frame):
        with contextlib.suppress(OSError):
            os.write(watcher, f"stop {number}\n".encode())

    for number in CAUGHT:
        signal.signal(number, on_signal)

    # Started once, whatever it takes: the id makes a retried start the same start.
    deadline = time.monotonic() + SHELL_START_RETRY_S
    drop = dropped_outputs(target)
    while True:
        try:
            started = call(
                "start",
                kind="bash" if bash else "sh",
                body=bind_working_directory(
                    outward(body), short_name, spelled_later=spelled_later
                ),
                cwd=cwd,
                env=env,
                merge=merge,
                stdin=stdin,
                keep=output is not None,
                drop=drop,
            )
            break
        except MachineOutOfReach:
            if target.get("kind") == "unavailable" or time.monotonic() > deadline:
                sys.stderr.write(MACHINE_OUT_OF_REACH + "\n")
                return 1
        except (OSError, TimeoutError):
            if time.monotonic() > deadline:
                sys.stderr.write(MACHINE_OUT_OF_REACH + "\n")
                return 1
        except RuntimeError as exc:
            if "upgrading" not in str(exc) or time.monotonic() > deadline:
                sys.stderr.write(f"{exc}\n")
                return 1
        time.sleep(1)
    # Its record stays while the build keeps the output file it mirrors.
    kept = bool(output) and keep_output(target, output, command_id, started)
    offsets = {"out": 0, "err": 0}
    written = 0
    failing_since, delay = None, SHELL_READ_RETRY_S[0]
    answer = {}
    while True:
        try:
            answer = call("read", out=offsets["out"], err=offsets["err"], wait=20)
            failing_since, delay = None, SHELL_READ_RETRY_S[0]
        except Exception as exc:  # the command outlives the link
            link = isinstance(exc, MachineOutOfReach | OSError | TimeoutError)
            failing_since = failing_since or time.monotonic()
            if not link and time.monotonic() - failing_since > SHELL_ERROR_RETRY_S:
                sys.stderr.write(f"{exc}\n")
                return 1
            client = RemoteClient(current_target(target))
            time.sleep(delay)
            delay = min(delay * 2, SHELL_READ_RETRY_S[1])
            continue
        if answer.get("lost"):
            sys.stderr.write(
                "The command's executor restarted; its outcome is unknown\n"
            )
            return 1
        for stream, fd in (("out", 1), ("err", 2)):
            data = base64.b64decode(answer.get(stream) or "")
            offsets[stream] += len(data)
            written += len(data)
            if written > SHELL_OUTPUT_BYTES:
                with contextlib.suppress(Exception):
                    call("signal", signal=KILL)
                sys.stderr.write(
                    f"\nOutput passed {SHELL_OUTPUT_BYTES // (1024 * 1024)} MiB; "
                    "the command was stopped.\n"
                )
                return 1
            _write_all(fd, data)
        if "exit" in answer:
            break
    for number in CAUGHT:
        signal.signal(number, signal.SIG_IGN)
    with contextlib.suppress(OSError):
        os.write(watcher, b"done\n")
    if bash and tail is not None:
        leave_timing(target, _cwd_call(tail.group(1)), answer)
        if "cwd" in answer:
            _report_directory(target, client, answer["cwd"], tail.group(1))
    if not kept:
        with contextlib.suppress(Exception):
            call("forget")
    code = answer["exit"]
    if answer.get("disk"):
        # The executor found its disk full as the command failed: most likely
        # why, and nothing the command's own output says in so many words.
        sys.stderr.write(answer["disk"] + "\n")
    if code < 0 and sys.platform == "win32":
        # No signal to end with here: the shell's way of saying it, 128 + n.
        return 128 - code
    if code < 0:
        # Killed by a signal there: end the same way here, as the shell the
        # build started would have.
        # SIGKILL's disposition cannot be set, and needs no resetting.
        with contextlib.suppress(OSError, ValueError):
            signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


def _cwd_call(spelled):
    """The tool call a `pwd -P` file belongs to: the build names the file
    `claude-<call id>-cwd` in its temporary directory."""
    name = Path(shlex.split(spelled)[0]).name
    match = re.fullmatch(r"claude-(.+)-cwd", name)
    return match.group(1) if match else ""


def leave_timing(target, call, answer):
    """Leave how long the command itself ran, keyed by the tool call the build
    named for it, where the runner reads it (`Runner.command_ran`).

    A call's wall clock is the command's own run plus whatever the platform
    spent carrying it to the executor, and only the executor sees both ends of
    the former. Named for the call and not the executor's command id: the id
    the build knows is the one its record will carry.
    """
    ran = (answer or {}).get("ran_ms")
    where = target.get("target_file")
    if not isinstance(ran, int) or not where or not _PLAIN.fullmatch(str(call)):
        return
    directory = Path(where).parent / "command-timings"
    with contextlib.suppress(OSError):
        directory.mkdir(exist_ok=True)
        temporary = directory / (str(call) + "." + uuid.uuid4().hex)
        temporary.write_text(json.dumps({"ran_ms": ran}))
        temporary.replace(directory / f"{call}.json")


def _report_directory(target, client, final, spelled):
    """Tell the build which directory the command's shell ended in.

    The build reads it from the cwd file it named and keeps it only if the
    directory exists here. A directory in the workspace does, at the same path,
    in the session's view of the project. One outside the workspace is
    reported as `/`, which the build answers exactly as a native session does:
    it resets to the workspace root and says so.
    """
    path = Path(shlex.split(spelled)[0])
    temporary = os.environ.get("CLAUDE_CODE_TMPDIR")
    # Only the cwd file the build itself chose: a hook shaped like a Bash
    # command must not steer this write anywhere else on this host.
    if (
        not temporary
        or path.parent != Path(temporary)
        or not re.fullmatch(r"claude-[0-9a-f]+-cwd", path.name)
    ):
        return
    # A session started before its machine was rented sees the project at a
    # placeholder until it is relaunched on the machine (`_take_leased_machine`);
    # the lease (`RemoteClient.call`) points its commands at the machine's
    # workspace meanwhile.
    machine = session_path(client.config["workspace"])
    seen = session_path(client.config.get("virtual_workspace", machine))
    final = final[:4096]
    if _under(final, machine):
        spelled_here = seen + final[len(machine) :]
        if target.get("kind") == "private" or (target.get("local") and seen != machine):
            # A plain directory here: the private scratch view, an own session's.
            Path(spelled_here).mkdir(parents=True, exist_ok=True)
    else:
        spelled_here = "/"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(spelled_here + "\n")
