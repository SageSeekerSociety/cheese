"""A room's Bash behaves as Claude Code running on the executor would.

A room's Claude Code runs on the session host, and every Bash command it runs
goes through `CLAUDE_CODE_SHELL_PREFIX` to the room's executor
(`remote_execution/client.py` `shell`, `runtime.py`). The promise is that
neither the model nor the platform can tell: the session does what the same
build would do running on the executor itself. This script checks it.

It runs one scenario matrix twice against the same deterministic model
(`model_fixture.py`, which answers each turn from a `DO:` directive):

  reference  the given build, headless with the room's stream-json flags, run
             directly in a workspace of its own with no prefix and no plugin,
             under a HOME of its own. It stands in for Claude Code running on
             the executor.
  remote     the build launched the way a room's central session is
             (`client.prepare`: plugin, shell prefix, guard, the forwarded
             project view mounted over FUSE), with `runtime.py` serving an
             identical copy of the project under an executor HOME of its own.

The reference HOME and the executor HOME carry the same ~/.bashrc; the central
session's HOME carries a different one, which nothing may see. Each step
records what the model is sent, what stream-json reports, and what can be
observed on the machine, and the two records must be equal after the
replacements in NORMALIZATIONS: the complete list of values that differ only
because these are two environments. Any other difference is a bug in the
remote path, not something to add to that list.

Usage:
    python3 equivalence.py --claude <binary> [--output <receipts dir>]
        [--only <step> ...] [--no-mount]

`--no-mount` skips the FUSE view for hosts without FUSE; the steps that `cd`
into a directory only the executor has cannot pass without it.
"""

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import headless_contract as contract  # noqa: E402
from headless_contract import DRIVER, Session, blocks, do, is_  # noqa: E402

SOURCE = (
    HERE.parents[1] / "backend/app/domain/agent/harness/claude_code/remote_execution"
)
sys.path.insert(0, str(SOURCE))
import release as execution_release  # noqa: E402
import runtime as execution_runtime  # noqa: E402

_spec = importlib.util.spec_from_file_location("execution_client", SOURCE / "client.py")
client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client)

# Every value the two runs may differ in, and why. `normalize` applies exactly
# these; the receipt lists them.
NORMALIZATIONS = {
    "<WS>": "The project directory: the reference's workspace and the "
    "executor's copy are two directories with the same contents.",
    "<HOME>": "The machine's HOME: the reference's and the executor's are two "
    "directories carrying the same ~/.bashrc.",
    "<CONFIG>": "The session's config directory (transcripts, persisted "
    "results). It is on the session host in both runs, as two directories.",
    "<TMP>": "The session's temp directory (background task output). It is on "
    "the session host in both runs, as two directories.",
    "<SLUG>": "The project key the build derives from its own working "
    "directory's path, which differs with that path.",
    "<SESSION>": "The session id, which the build draws at random.",
    "<TASK n>": "Background task ids, which the build draws at random; "
    "numbered in order of appearance, so their order is still compared.",
    "<FILE>": "Names of persisted tool results, which the build draws at random.",
    "<PORT>": "The port of each run's model fixture.",
    "<PROGRAMS>": "The directory each machine keeps the platform's CLI in, "
    "which its commands find first on PATH.",
    "<HOOKLOG>": "The directory each machine's project hooks log to (HOOK_LOG): "
    "the test's own instrument, one per machine.",
    "realpath spellings": "Each directory above is also matched as its real "
    "path, which the build and `pwd -P` report (macOS keeps /var under "
    "/private/var), and as `printf %q` spells it.",
    "dropped fields": "uuid, prompt_id, timestamps, durations, cost and usage: "
    "drawn at random or read off a clock. cache_control: the build's prompt "
    "cache markers, placed by position among the blocks it sends.",
    "session context": "The <system-reminder> blocks the build attaches to a "
    "user message (environment, git status, skill list, date) describe the "
    "session it runs in, which stays on the session host by design; they are "
    "not produced by any command, so this check leaves them out.",
    "platform hook input": "A platform hook runs on the session host by design, "
    "so its input names the central workspace and transcript; the central "
    "workspace is read as <WS> there and nowhere else.",
    "environment: macOS": "__CF_USER_TEXT_ENCODING is added by macOS to the "
    "executor's Python service; it does not exist on Linux, and the reference "
    "Claude Code is started without it.",
    "environment: session-host process": "CLAUDE_PID, "
    "CLAUDE_CODE_MESSAGING_SOCKET and CLAUDE_CODE_MESSAGING_TOKEN name the "
    "session host's Claude Code process: a pid, and a Unix socket with its "
    "token. They are not forwarded — the token is a central credential, and "
    "neither the pid nor the socket exists on the executor — so they are "
    "removed from the reference before comparing.",
}
# Lines of an environment listing the normalizations above remove.
UNLISTED = re.compile(
    r"^(CLAUDE_PID|CLAUDE_CODE_MESSAGING_SOCKET|CLAUDE_CODE_MESSAGING_TOKEN"
    r"|__CF_USER_TEXT_ENCODING)=.*(\n|$)",
    re.MULTILINE,
)
HOST_PROCESS_ENV = {
    "CLAUDE_PID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
}
DROPPED = {
    "uuid",
    "prompt_id",
    "timestamp",
    "duration_ms",
    "duration_api_ms",
    "total_cost_usd",
    "usage",
    "modelUsage",
    "request_id",
    "fast_mode_state",
    "last_assistant_message_id",
    "cache_control",
    "end_time",
}
BASHRC = """\
alias exec_alias='echo EXEC_ALIAS'
exec_fn() { echo EXEC_FN; }
export PATH="$HOME/exec-bin:$PATH"
"""
CENTRAL_BASHRC = """\
alias central_alias='echo CENTRAL_ALIAS'
central_fn() { echo CENTRAL_FN; }
"""
PROJECT_HOOK = (
    'cat >> "$HOOK_LOG/project-{event}.jsonl"; '
    'echo >> "$HOOK_LOG/project-{event}.jsonl"'
)


def machine_home(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / ".bashrc").write_text(BASHRC)
    (home / ".bash_profile").write_text("[ -f ~/.bashrc ] && . ~/.bashrc\n")
    tool = home / "exec-bin" / "exec-tool"
    tool.parent.mkdir(exist_ok=True)
    tool.write_text("#!/bin/sh\necho EXEC_TOOL\n")
    tool.chmod(0o755)


def project(path):
    """The project both machines hold: a git repository with project hooks."""
    path.mkdir(parents=True)
    (path / "target.txt").write_text("TARGET\n")
    (path / ".claude").mkdir()
    (path / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    event: [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": PROJECT_HOOK.format(event=event),
                                }
                            ],
                        }
                    ]
                    for event in ("PreToolUse", "PostToolUse")
                }
            }
        )
    )
    git = ["git", "-c", "user.name=fixture", "-c", "user.email=f@example.invalid"]
    subprocess.run([*git, "init", "-q"], cwd=path, check=True)
    subprocess.run([*git, "add", "."], cwd=path, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=path, check=True)


def platform_hooks(log):
    return {
        event: [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"cat >> {shlex.quote(str(log / event))}.jsonl; "
                        f"echo >> {shlex.quote(str(log / event))}.jsonl",
                    }
                ],
            }
        ]
        for event in ("PreToolUse", "PostToolUse")
    }


def machine_env(home, temporary, extra, programs=None):
    """The environment a machine's own processes start with — the reference
    Claude Code, or the executor's service — before the build adds anything.
    Both machines get the same one, so a command's whole environment can be
    compared."""
    return {
        **contract.fixture_env(home, temporary.parent, 9),
        "CLAUDE_CODE_TMPDIR": str(temporary),
        # The executor's service puts its platform CLI first on PATH itself.
        "PATH": (
            str(programs / "remote-execution/bin") + os.pathsep if programs else ""
        )
        + os.environ["PATH"],
        **extra,
    }


class Run:
    """One of the two sessions, and everything needed to read its record."""

    def __init__(
        self, name, session, *, workspace, home, config, temporary, env, also=()
    ):
        self.also = list(also)
        self.name = name
        self.session = session
        self.workspace = workspace
        self.home = home
        self.config = config
        self.temporary = temporary
        self.env = env
        self.central = None
        self.hook_log = None
        self.platform_log = None
        self.drop = None
        self.close = session.stop

    def replacements(self):
        pairs = []
        for directory in sorted(self.config.glob("projects/*")):
            pairs.append((directory.name, "<SLUG>"))
        for directory in sorted(self.temporary.glob("claude-*/*")):
            if directory.is_dir():
                pairs.append((directory.name, "<SLUG>"))
        for path, token in [
            (self.config, "<CONFIG>"),
            (self.temporary, "<TMP>"),
            (self.workspace, "<WS>"),
            (self.home, "<HOME>"),
            *self.also,
        ]:
            for spelled in (str(path), os.path.realpath(path)):
                pairs.append((spelled, token))
                pairs.append((spelled.replace(" ", "\\ "), token))
        _, init = self.session.wait(is_("system", "init"), 1)
        if init:
            pairs.append((init["session_id"], "<SESSION>"))
        tasks = []
        for event in self.session.events:
            if is_("system", "task_started")(event) and event["task_id"] not in tasks:
                tasks.append(event["task_id"])
        pairs += [(task, f"<TASK {n}>") for n, task in enumerate(tasks, 1)]
        # Longest first, so a path is replaced before a path it contains.
        return sorted(pairs, key=lambda pair: -len(pair[0]))

    def normalize(self, value, extra=()):
        pairs = [*extra, *self.replacements()]

        def text(string):
            for old, new in pairs:
                if old:
                    string = string.replace(old, new)
            string = UNLISTED.sub("", string)
            string = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<PORT>", string)
            return re.sub(
                r"tool-results/[A-Za-z0-9_-]+\.txt", "tool-results/<FILE>", string
            )

        def walk(item):
            if isinstance(item, str):
                return text(item)
            if isinstance(item, list):
                return [walk(element) for element in item]
            if isinstance(item, dict):
                return {
                    text(key): walk(element)
                    for key, element in item.items()
                    if key not in DROPPED
                }
            return item

        return walk(value)


def reference(binary, root):
    folder = root / "reference"
    home = folder / "reference" / "home"
    machine_home(home)
    workspace = folder / "machine" / "the project"
    project(workspace)
    log = folder / "hook-log"
    log.mkdir(parents=True)
    platform = folder / "platform-log"
    platform.mkdir()
    extra = {"HOOK_LOG": str(log), "EQ_MACHINE": "1"}
    programs = folder / "programs"
    temporary = folder / "reference" / "tmp"
    env = machine_env(home, temporary, extra, programs)
    launch = {
        "command": [
            binary,
            "--setting-sources",
            "user,project,local",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps({"mcpServers": {}}),
            "--disallowedTools",
            "EnterWorktree,ExitWorktree",
            "--no-chrome",
        ],
        # The model's address is the fixture's, which the session sets.
        "env": {k: v for k, v in env.items() if k != "ANTHROPIC_BASE_URL"},
        "cwd": str(workspace),
    }
    session = Session(
        binary,
        folder,
        "reference",
        DRIVER,
        settings={"hooks": platform_hooks(platform)},
        launch=launch,
        home=home,
    )
    env = {
        **env,
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{session.server.server_port}",
    }
    run = Run(
        "reference",
        session,
        workspace=workspace,
        home=home,
        config=home / ".claude",
        temporary=temporary,
        env=env,
        also=[(programs, "<PROGRAMS>"), (log, "<HOOKLOG>")],
    )
    run.hook_log, run.platform_log = log, platform
    return run


def remote(binary, root, mount):
    folder = root / "remote"
    executor_home = folder / "executor-home"
    machine_home(executor_home)
    central_home = folder / "central-home"
    central_home.mkdir(parents=True)
    (central_home / ".bashrc").write_text(CENTRAL_BASHRC)
    (central_home / ".bash_profile").write_text("[ -f ~/.bashrc ] && . ~/.bashrc\n")
    (central_home / ".claude").mkdir()
    workspace = folder / "machine" / "the project"
    project(workspace)
    log = folder / "hook-log"
    log.mkdir(parents=True)
    platform = folder / "platform-log"
    platform.mkdir()
    programs = folder / "execution"
    (programs / "remote-execution").mkdir(parents=True)
    shutil.copyfile(SOURCE / "runtime.py", programs / "runtime.py")
    shutil.copyfile(SOURCE / "portable.py", programs / "portable.py")
    extra = {"HOOK_LOG": str(log), "EQ_MACHINE": "1"}
    config = {
        "workspace": str(workspace),
        "claude": binary,
        "env": extra,
        "mcp_servers": {},
    }
    target = {
        "command": [sys.executable, str(programs / "runtime.py")],
        "state": str(programs / "state"),
        "mcp_servers": [],
    }
    executor = client.RemoteClient(target)
    # The executor's own environment: its machine's, not this process's.
    executor_temporary = folder / "executor-tmp"
    executor_temporary.mkdir()
    executor_env = machine_env(executor_home, executor_temporary, {})
    subprocess.run(
        executor.command("start"),
        input=json.dumps(config),
        text=True,
        check=True,
        capture_output=True,
        env=executor_env,
        cwd=str(programs),
    )
    if not mount:
        execution_release.mount_state = lambda _path: execution_release.MOUNT_LIVE
    client.PINNED_VERSION = subprocess.run(
        [binary, "--version"], capture_output=True, text=True
    ).stdout.split()[0]
    base = {
        "hooks": {
            **platform_hooks(platform),
            "SessionStart": [
                {
                    "hooks": [
                        {"type": "command", "command": "cheese sync-agents || true"}
                    ]
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {"type": "command", "command": "cheese sync-agents || true"}
                    ]
                }
            ],
        }
    }
    launch = client.prepare(
        central_home / "session",
        target,
        claude=binary,
        base_settings=base,
        home_override=central_home,
        config_override=central_home / ".claude",
    )
    session = Session(
        binary,
        folder,
        "central",
        DRIVER,
        launch=launch,
        home=central_home,
        # A credential only the session host holds: the executor must never
        # see it.
        env={"SHELL": "/bin/bash", "CENTRAL_SECRET": "central-only-secret"},
    )
    run = Run(
        "remote",
        session,
        workspace=workspace,
        home=executor_home,
        config=central_home / ".claude",
        temporary=Path(launch["env"]["CLAUDE_CODE_TMPDIR"]),
        env={**executor_env, **extra},
        also=[
            (executor_home / ".claude", "<CONFIG>"),
            (executor_temporary, "<TMP>"),
            (programs, "<PROGRAMS>"),
            (log, "<HOOKLOG>"),
        ],
    )
    run.central = Path(launch["cwd"])
    run.hook_log, run.platform_log = log, platform
    socket = Path(execution_runtime.socket_path(Path(target["state"])))

    def drop(seconds):
        """The link to the executor goes away, and comes back."""
        hidden = socket.with_name(socket.name + ".dropped")
        socket.rename(hidden)
        time.sleep(seconds)
        hidden.rename(socket)

    def close():
        session.stop()
        subprocess.run(executor.command("stop"), capture_output=True, timeout=30)
        if mount:
            execution_release.release_mount(run.central)

    run.drop = drop
    run.close = close
    return run


# --- steps -----------------------------------------------------------------
#
# Each step drives one run and returns what it saw beyond the generic record
# (`record`). They are the same code for both runs; `run.name` is never read.


def turn(run, directive, timeout=120):
    mark = run.session.user(directive)
    run.session.wait(is_("result"), timeout, mark)
    return mark


def alive(pattern):
    """Whether a process whose whole argv is `pattern` exists on this host,
    which holds both the reference and the executor."""
    return (
        subprocess.run(["pgrep", "-f", "-x", pattern], capture_output=True).returncode
        == 0
    )


def settle(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def bash(command, **arguments):
    return do("Bash", command=command, description="step", **arguments)


def step_output(run):
    turn(
        run,
        bash(
            "printf 'out one\\n'; printf 'err one\\n' >&2; printf 'out two\\n'; "
            "printf 'err two' >&2; exit 7"
        ),
    )
    turn(run, bash("printf 'success, no newline'"))
    return {}


def step_unicode(run):
    turn(run, bash("printf '中文 ✓ 🧀 é\\n'; printf 'e\\xcc\\x81 combining\\n'"))
    return {}


def step_binary(run):
    turn(run, bash("printf 'A\\000B\\377\\376C\\n'; printf 'tail\\n'"))
    return {}


def step_large(run):
    mark = turn(
        run, bash("head -c 1000000 /dev/zero | tr '\\0' a; echo; echo LARGE_TAIL")
    )
    results = [
        event["tool_use_result"]
        for event in run.session.events[mark:]
        if event.get("type") == "user"
        and isinstance(event.get("tool_use_result"), dict)
    ]
    persisted = results[-1].get("persistedOutputPath") if results else None
    content = Path(persisted).read_bytes() if persisted else b""
    return {
        "persisted file sha256": hashlib.sha256(content).hexdigest(),
        "persisted file bytes": len(content),
    }


def step_cwd(run):
    turn(run, bash('mkdir -p "only here/deeper" && cd "only here/deeper" && pwd'))
    turn(run, bash("pwd"))
    turn(run, bash("cd .. && pwd"))
    turn(run, bash("pwd"))
    turn(run, bash("cd /tmp && pwd"))
    turn(run, bash("pwd"))
    turn(run, bash("mkdir -p vanishing && cd vanishing && pwd"))
    # Removes only the directory the shell is in; a run whose `cd` did not
    # hold finds nothing to remove instead of removing the workspace.
    turn(run, bash("rmdir ../vanishing && echo removed"))
    turn(run, bash("pwd"))
    return {}


def step_environment(run):
    turn(run, bash('export EQ_EXPORTED=exported; echo "[$EQ_EXPORTED]"'))
    turn(run, bash('echo "[${EQ_EXPORTED:-unset}]"'))
    mark = turn(
        run,
        bash(
            "for name in $(compgen -e | LC_ALL=C sort); do "
            'printf \'%s=%q\\n\' "$name" "${!name}"; done'
        ),
    )
    listed = {}
    for text in tool_texts(run, mark):
        for line in text.splitlines():
            name, _, value = line.partition("=")
            listed[name] = value
    machine = {
        name: shlex.quote(value) if value else "''" for name, value in run.env.items()
    }

    def plain(value):
        # %q and shlex quote differently; compare what they quote.
        with contextlib.suppress(ValueError):
            words = shlex.split(value)
            return words[0] if words else ""
        return value

    added = {
        name: value
        for name, value in listed.items()
        if name != "PATH"
        and name not in HOST_PROCESS_ENV
        and name != "__CF_USER_TEXT_ENCODING"
        and plain(machine.get(name, "\0")) != plain(value)
    }
    return {
        "variables the build set": added,
        "session-host credential visible": "CENTRAL_SECRET" in listed
        or any("central-only-secret" in value for value in listed.values()),
        "machine variable visible": listed.get("EQ_MACHINE") == "1",
    }


def step_shell(run):
    turn(
        run,
        bash(
            "type exec_alias; exec_fn; command -v exec-tool; exec-tool; "
            "type central_alias 2>&1 | head -1; type central_fn 2>&1 | head -1; "
            'echo "$SHELL"; shopt -q login_shell && echo login || echo not-login'
        ),
    )
    return {}


def step_stdin(run):
    turn(run, bash('cat; echo "cat=$?"; read -t 1 line; echo "read=$?"'))
    return {}


def step_composition(run):
    turn(
        run,
        bash(
            '(echo a; echo b) | sort -r; echo "sub=$(echo inner)"; '
            "{ sleep 0.3; echo late; } & echo early; wait; echo waited"
        ),
    )
    turn(run, bash("( sleep 2; echo orphan ) & echo quick"))
    time.sleep(3)
    return {}


def step_background(run):
    mark = run.session.user(
        do(
            "Bash",
            command="sleep 1; echo BG_OUT; echo BG_ERR >&2; exit 3",
            run_in_background=True,
            description="bg",
        )
    )
    _, note = run.session.wait(is_("system", "task_notification"), 60, mark)
    follow, _ = (None, None)
    if note:
        index = run.session.events.index(note)
        follow, _ = run.session.wait(is_("result"), 60, index + 1)
        mark = turn(run, do("Read", file_path=note["output_file"]))
    return {"notified": note is not None, "follow-up turn": follow is not None}


def step_move(run):
    mark = run.session.user(bash("sleep 4; echo MOVED_DONE", timeout=120000))
    _, started = run.session.wait(is_("system", "task_started"), 60, mark)
    if not started:
        return {"started": False}
    time.sleep(1.5)
    answer = run.session.control(
        {"subtype": "background_tasks", "tool_use_id": started["tool_use_id"]}
    )
    running_after_move = alive("sleep 4")
    _, note = run.session.wait(
        is_("system", "task_notification", task_id=started["task_id"]), 60, mark
    )
    if note:
        run.session.wait(is_("result"), 60, run.session.events.index(note) + 1)
    return {
        "answer": (answer.get("response") or {}),
        "still running after the move": running_after_move,
    }


def step_stop_task(run):
    mark = run.session.user(
        do("Bash", command="sleep 301", run_in_background=True, description="s")
    )
    _, started = run.session.wait(is_("system", "task_started"), 60, mark)
    run.session.wait(is_("result"), 60, mark)
    before = settle(lambda: alive("sleep 301"))
    answer = run.session.control(
        {"subtype": "stop_task", "task_id": started["task_id"]}
    )
    run.session.wait(
        is_("system", "task_notification", task_id=started["task_id"]), 30, mark
    )
    gone = settle(lambda: not alive("sleep 301"))
    return {
        "running before": before,
        "gone after": gone,
        "answer": answer.get("subtype"),
    }


def step_task_stop_tool(run):
    mark = run.session.user(
        do("Bash", command="sleep 302", run_in_background=True, description="s")
    )
    _, started = run.session.wait(is_("system", "task_started"), 60, mark)
    run.session.wait(is_("result"), 60, mark)
    before = settle(lambda: alive("sleep 302"))
    turn(run, do("TaskStop", task_id=started["task_id"]))
    gone = settle(lambda: not alive("sleep 302"))
    return {"running before": before, "gone after": gone}


def step_interrupt(run):
    mark = run.session.user(bash("sleep 303"))
    run.session.wait(is_("system", "task_started"), 60, mark)
    before = settle(lambda: alive("sleep 303"))
    run.session.control({"subtype": "interrupt"})
    run.session.wait(is_("result"), 60, mark)
    gone = settle(lambda: not alive("sleep 303"))
    return {"running before": before, "gone after": gone}


def step_timeout(run):
    turn(run, bash("sleep 304", timeout=3000))
    gone = settle(lambda: not alive("sleep 304"))
    turn(run, bash("trap '' TERM; sleep 305", timeout=3000))
    stubborn_gone = settle(lambda: not alive("sleep 305"), timeout=15)
    turn(run, bash("(trap '' TERM; sleep 306) & sleep 307", timeout=3000))
    time.sleep(8)
    return {
        "gone after": gone,
        "TERM-ignoring command gone after": stubborn_gone,
        "TERM-ignoring descendant still running": alive("sleep 306"),
    }


def step_link_drop(run):
    """The remote run loses its link to the executor for three seconds in the
    middle of the command. The reference does not; the records must agree."""
    if run.drop:
        threading.Timer(2.5, run.drop, args=(3.0,)).start()
    turn(
        run,
        bash(
            "for i in 1 2 3 4 5 6 7; do echo line$i; echo err$i >&2; sleep 1; done; "
            "exit 4",
            timeout=120000,
        ),
    )
    return {}


def step_stdin_close(run):
    mark = run.session.user(
        do("Bash", command="sleep 309", run_in_background=True, description="s")
    )
    run.session.wait(is_("system", "task_started"), 60, mark)
    run.session.wait(is_("result"), 60, mark)
    before = settle(lambda: alive("sleep 309"))
    run.session.close_stdin()
    _, eof = run.session.wait(is_("_eof"), 60, mark)
    gone = settle(lambda: not alive("sleep 309"))
    return {
        "running before": before,
        "gone after": gone,
        "exit status": eof and eof.get("returncode"),
    }


STEPS = {
    "output": step_output,
    "unicode": step_unicode,
    "binary": step_binary,
    "large": step_large,
    "cwd": step_cwd,
    "environment": step_environment,
    "shell": step_shell,
    "stdin": step_stdin,
    "composition": step_composition,
    "background": step_background,
    "move": step_move,
    "stop_task": step_stop_task,
    "task_stop_tool": step_task_stop_tool,
    "interrupt": step_interrupt,
    "timeout": step_timeout,
    "link_drop": step_link_drop,
    # Last: it ends the session.
    "stdin_close": step_stdin_close,
}

# --- the generic record ------------------------------------------------------

SYSTEM_KEPT = {
    "task_started",
    "task_updated",
    "task_notification",
    "background_tasks_changed",
}


def tool_texts(run, mark):
    return [
        contract.text_of(block)
        for event in run.session.events[mark:]
        if event.get("type") == "user"
        for block in blocks(event)
        if block.get("type") == "tool_result"
    ]


def record(run, mark, requests_mark):
    """What the model was sent, and what stream-json said, since `mark`."""
    stream = []
    for event in run.session.events[mark:]:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") in SYSTEM_KEPT:
            stream.append(event)
        elif kind == "user" and not event.get("isReplay"):
            stream.append(
                {
                    "type": "user",
                    "content": [
                        block
                        for block in blocks(event)
                        if block.get("type") == "tool_result"
                    ],
                    "tool_use_result": event.get("tool_use_result"),
                }
            )
        elif kind == "assistant":
            stream.append(
                {
                    "type": "assistant",
                    "content": [
                        {"name": block.get("name"), "input": block.get("input")}
                        for block in blocks(event)
                        if block.get("type") == "tool_use"
                    ],
                }
            )
        elif kind == "result":
            stream.append(
                {
                    key: event.get(key)
                    for key in ("type", "subtype", "is_error", "result", "num_turns")
                }
            )
    model = []
    for request in run.session.requests()[requests_mark:]:
        message = request["messages"][-1]
        content = message.get("content")
        if isinstance(content, list):
            content = [
                block
                for block in content
                if not (
                    block.get("type") == "text"
                    and block.get("text", "").startswith("<system-reminder>")
                )
            ]
        model.append({**message, "content": content})
    return {"stream": stream, "model": model}


def hook_logs(run):
    logs = {}
    for name, directory in (("project", run.hook_log), ("platform", run.platform_log)):
        for path in sorted(directory.glob("*.jsonl")):
            logs[f"{name} {path.stem}"] = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
    return logs


def play(run, names):
    """Drive every step on one run; what it recorded, by step."""
    record_by_step = {}
    for name in names:
        mark = len(run.session.events)
        requests_mark = len(run.session.requests())
        try:
            seen = STEPS[name](run)
            error = None
        except Exception:  # noqa: BLE001 — a failing step is a result
            seen, error = {}, traceback.format_exc()
        # Let trailing events of this step land before the next one starts.
        time.sleep(1)
        record_by_step[name] = {
            "record": record(run, mark, requests_mark),
            "observed": seen,
            "error": error,
        }
    return record_by_step


def normalized(run, raw, logs):
    central = [(str(run.central), "<WS>")] if run.central else []
    out = {name: run.normalize(value) for name, value in raw.items()}
    # Platform hooks alone may name the central workspace (NORMALIZATIONS).
    out["hooks"] = {
        name: [
            run.normalize(entry, central if name.startswith("platform") else ())
            for entry in entries
        ]
        for name, entries in logs.items()
    }
    return out


def differences(left, right, path=""):
    if type(left) is not type(right):
        return [f"{path}: {json.dumps(left)[:400]} != {json.dumps(right)[:400]}"]
    if isinstance(left, dict):
        found = []
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                side = "remote" if key in right else "reference"
                value = json.dumps(left.get(key, right.get(key)))[:400]
                found.append(f"{path}/{key}: only in {side}: {value}")
            else:
                found += differences(left[key], right[key], f"{path}/{key}")
        return found
    if isinstance(left, list):
        found = []
        if len(left) != len(right):
            found.append(f"{path}: {len(left)} items != {len(right)} items")
        for index, (a, b) in enumerate(zip(left, right, strict=False)):
            found += differences(a, b, f"{path}[{index}]")
        return found
    return (
        []
        if left == right
        else [f"{path}: {json.dumps(left)[:400]} != {json.dumps(right)[:400]}"]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True, help="the claude binary to check")
    parser.add_argument("--output", type=Path, help="where to write the receipt")
    parser.add_argument("--only", nargs="*", choices=list(STEPS), help="run only these")
    parser.add_argument(
        "--no-mount", action="store_true", help="no FUSE view (see the docstring)"
    )
    arguments = parser.parse_args()
    binary = str(Path(arguments.claude).resolve())
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True
    ).stdout.strip()
    names = [name for name in STEPS if not arguments.only or name in arguments.only]
    root = Path(tempfile.mkdtemp(prefix="equivalence-"))
    records = {}
    try:
        for build in (reference, lambda b, r: remote(b, r, not arguments.no_mount)):
            run = build(binary, root)
            try:
                run.session.control({"subtype": "initialize"})
                raw = play(run, names)
            finally:
                run.close()
            records[run.name] = normalized(run, raw, hook_logs(run))
            # Nothing either run left behind may be taken for the other's.
            for pattern in (
                "sleep 301",
                "sleep 302",
                "sleep 303",
                "sleep 304",
                "sleep 305",
                "sleep 306",
                "sleep 307",
                "sleep 309",
                "sleep 4",
            ):
                subprocess.run(["pkill", "-f", "-x", pattern], capture_output=True)
        results = []
        for name in [*names, "hooks"]:
            left = records["reference"].get(name)
            right = records["remote"].get(name)
            found = differences(left, right)
            for side in ("reference", "remote"):
                error = (
                    (records[side].get(name) or {}).get("error")
                    if name != "hooks"
                    else None
                )
                if error:
                    found.append(f"{side} step failed:\n{error}")
            results.append({"step": name, "equal": not found, "differences": found})
            print(f"{'PASS' if not found else 'FAIL'}  {name}", flush=True)
            for line in found[:20]:
                print(f"      {line}", flush=True)
        receipt = {
            "claude": binary,
            "version": version,
            "mounted": not arguments.no_mount,
            "normalizations": NORMALIZATIONS,
            "steps": results,
            "equal": all(result["equal"] for result in results),
        }
        if arguments.output:
            arguments.output.mkdir(parents=True, exist_ok=True)
            (arguments.output / "equivalence.json").write_text(
                json.dumps(receipt, indent=2, ensure_ascii=False)
            )
            (arguments.output / "records.json").write_text(
                json.dumps(records, indent=1, ensure_ascii=False)
            )
            for run_dir in root.iterdir():
                for name in ("transcript.jsonl", "stderr.log"):
                    for found_file in run_dir.rglob(name):
                        target = (
                            arguments.output / run_dir.name / found_file.parent.name
                        )
                        target.mkdir(parents=True, exist_ok=True)
                        shutil.copy(found_file, target / name)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{version}: {sum(r['equal'] for r in results)}/{len(results)} steps equal")
    return 0 if receipt["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
