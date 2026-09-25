"""A room's Bash behaves as Claude Code running on the executor would.

A room's Claude Code runs on the session host, and every Bash command it runs
goes through `CLAUDE_CODE_SHELL_PREFIX` to the room's executor
(`remote_execution/client.py` `shell`, `runtime.py`). The promise is that
neither the model nor the platform can tell: the session does what the same
build would do running on the executor itself. This script checks it.

It runs one scenario matrix twice against the same deterministic model
(`model_fixture.py`, which answers each turn from a `DO:` directive):

  reference  the given build, headless with the room's stream-json flags, run
             directly in the machine's project, with no prefix and no plugin.
             It stands in for Claude Code running on the executor.
  remote     the build launched the way a room's central session is
             (`client.prepare`: its own namespace, plugin, shell prefix,
             guard, the forwarded project view mounted over FUSE), with
             `runtime.py` serving the machine's project, reached over the
             device route (`kind: device`) through a relay standing in for
             the platform's.

The two runs happen one after the other and use the same paths: the machine
(its HOME, its project, its programs) and the session's own directories
(config, temp) are rebuilt identically before each. The session host's view
of the project lives somewhere else entirely, and its HOME carries a shell
startup file of its own, which nothing may see. So the two records must be
equal with no path rewritten: the only replacements are the values in
NORMALIZATIONS, each drawn at random or naming the session host's own
process. Any other difference is a bug in the remote path, not something to
add to that list.

`--shell zsh` gives the machine zsh as its user shell, with its own aliases,
functions and options; the session host's shell stays bash.

Needs Linux: the session's namespace, and FUSE for the view.

Usage:
    python3 equivalence.py --claude <binary> [--shell bash|zsh]
        [--output <receipts dir>] [--only <step> ...]
"""

import argparse
import contextlib
import hashlib
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
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
# these; the receipt lists them. No path is among them.
NORMALIZATIONS = {
    "<SESSION>": "The session id, which the build draws at random.",
    "<TASK n>": "Background task ids, which the build draws at random; "
    "numbered in order of appearance, so their order is still compared.",
    "<FILE>": "Names of persisted tool results, which the build draws at random.",
    "dropped fields": "uuid, prompt_id, timestamps, durations, cost and usage: "
    "drawn at random or read off a clock. cache_control: the build's prompt "
    "cache markers, placed by position among the blocks it sends.",
    "session context": "The <system-reminder> blocks the build attaches to a "
    "user message (environment, git status, skill list, date) describe the "
    "session it runs in, which stays on the session host by design; they are "
    "not produced by any command, so this check leaves them out.",
    "environment: session-host process": "CLAUDE_PID, "
    "CLAUDE_CODE_MESSAGING_SOCKET and CLAUDE_CODE_MESSAGING_TOKEN name the "
    "session host's Claude Code process: a pid, and a Unix socket with its "
    "token. They are not forwarded — the token is a central credential, and "
    "neither the pid nor the socket exists on the executor — so they are "
    "removed from the reference before comparing.",
}
HOST_PROCESS_ENV = {
    "CLAUDE_PID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
}
# Lines of an environment listing the normalizations above remove.
UNLISTED = re.compile(
    r"^(" + "|".join(sorted(HOST_PROCESS_ENV)) + r")=.*(\n|$)", re.MULTILINE
)
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
# The machine user's own shell setup. Each run reads it from the machine's
# HOME; the session host's HOME has a different one.
STARTUP = {
    "bash": {
        ".bashrc": """\
alias exec_alias='echo EXEC_ALIAS'
exec_fn() { echo EXEC_FN; }
export PATH="$HOME/exec-bin:$PATH"
""",
        ".bash_profile": "[ -f ~/.bashrc ] && . ~/.bashrc\n",
    },
    "zsh": {
        ".zshrc": """\
alias exec_alias='echo EXEC_ALIAS'
exec_fn() { echo EXEC_FN; }
setopt SH_WORD_SPLIT
export PATH="$HOME/exec-bin:$PATH"
""",
    },
}
CENTRAL_BASHRC = """\
alias central_alias='echo CENTRAL_ALIAS'
central_fn() { echo CENTRAL_FN; }
"""
PROJECT_HOOK = (
    'cat >> "$HOOK_LOG/project-{event}.jsonl"; '
    'echo >> "$HOOK_LOG/project-{event}.jsonl"'
)
DEVICE_ID = "equivalence-device"


class Layout:
    """The paths both runs use. `rebuild` lays them out afresh and identically:
    the machine's HOME, project, programs and hook log, and the session's
    config, temp and platform-hook log."""

    def __init__(self, root, shell):
        self.root = root
        self.shell = shell
        self.machine = root / "machine"
        self.home = self.machine / "home"
        self.project = self.machine / "project"
        self.programs = self.machine / "programs"
        self.hook_log = self.machine / "hook-log"
        self.session = root / "session"
        self.config = self.session / "config"
        self.temporary = self.session / "tmp"
        self.platform_log = self.session / "platform-log"
        self.harness = root / "harness"
        self.template = root / "template"
        project(self.template)

    def rebuild(self):
        for path in (self.machine, self.session):
            shutil.rmtree(path, ignore_errors=True)
        (self.home / ".claude").mkdir(parents=True)
        for name, text in STARTUP[self.shell].items():
            (self.home / name).write_text(text)
        tool = self.home / "exec-bin" / "exec-tool"
        tool.parent.mkdir()
        tool.write_text("#!/bin/sh\necho EXEC_TOOL\n")
        tool.chmod(0o755)
        # A copy, so both runs have the same commit ids.
        shutil.copytree(self.template, self.project, symlinks=True)
        bin_dir = self.programs / "remote-execution" / "bin"
        bin_dir.mkdir(parents=True)
        # What a device's session Stop checkpoint runs (`launch.py`).
        sync = bin_dir / "cheese-sync"
        sync.write_text("#!/bin/sh\nexit 0\n")
        sync.chmod(0o755)
        for path in (self.hook_log, self.config, self.temporary, self.platform_log):
            path.mkdir(parents=True)

    def env(self, port, platform_cli=True):
        """The environment the machine's own processes start with — the
        reference Claude Code, or the executor's service — before the build
        adds anything. The same for both, so a command's whole environment
        can be compared: the executor's service puts the platform CLI first
        on PATH itself, and the reference is given it there."""
        return {
            **contract.fixture_env(self.home, self.session, port),
            "CLAUDE_CONFIG_DIR": str(self.config),
            "CLAUDE_CODE_TMPDIR": str(self.temporary),
            "SHELL": shutil.which(self.shell),
            "PATH": (
                str(self.programs / "remote-execution/bin") + os.pathsep
                if platform_cli
                else ""
            )
            + os.environ["PATH"],
            "HOOK_LOG": str(self.hook_log),
            "EQ_MACHINE": "1",
        }


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
    git = [
        "git",
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=f@example.invalid",
    ]
    env = dict(
        os.environ,
        GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
    )
    subprocess.run([*git, "init", "-q"], cwd=path, check=True, env=env)
    subprocess.run([*git, "add", "."], cwd=path, check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=path, check=True, env=env)


def platform_hooks(log):
    return {
        event: [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"cat >> {log / event}.jsonl; "
                        f"echo >> {log / event}.jsonl",
                    }
                ],
            }
        ]
        for event in ("PreToolUse", "PostToolUse")
    }


class Relay:
    """The platform's device route, standing in: each executor call over HTTP,
    passed to the executor's socket, answered as the route answers. A machine
    whose socket is gone is offline (409 with `X-Device-Id`), and `fail` makes
    the next starts and reads of a Bash command's shell answer so too, as a
    device that drops and comes back does. Only a Bash command's: the project
    hooks around it travel the same way, and would take the failures first."""

    def __init__(self, state):
        self.state = state
        self.lock = threading.Lock()
        self.failing = {}
        self.bash_commands = set()
        relay = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def answer(self, status, body, offline=False):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if offline:
                    self.send_header("X-Device-Id", DEVICE_ID)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                request = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                method, params = request["method"], request.get("params") or {}
                operation = (
                    params.get("operation")
                    if method == "control" and params.get("subtype") == "shell"
                    else None
                )
                offline = {"detail": f"DeviceOffline: 设备 {DEVICE_ID} 离线"}
                with relay.lock:
                    if operation == "start" and params.get("kind") == "bash":
                        relay.bash_commands.add(params.get("command_id"))
                    if (
                        params.get("command_id") in relay.bash_commands
                        and relay.failing.get(operation, 0) > 0
                    ):
                        relay.failing[operation] -= 1
                        return self.answer(409, offline, offline=True)
                try:
                    result = execution_runtime.request(relay.state, method, params)
                except (FileNotFoundError, ConnectionError):
                    return self.answer(409, offline, offline=True)
                except RuntimeError as exc:
                    return self.answer(500, {"detail": str(exc)})
                return self.answer(200, result)

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                # A prefix the build stopped hangs up mid-answer, as it may
                # on the platform's route; nothing to report.
                if not isinstance(sys.exc_info()[1], ConnectionError):
                    super().handle_error(request, client_address)

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/executor"

    def fail(self, **counts):
        with self.lock:
            self.failing.update(counts)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Run:
    """One of the two sessions, and everything needed to read its record."""

    def __init__(self, name, session, layout, env):
        self.name = name
        self.session = session
        self.layout = layout
        self.env = env
        self.drop = None
        self.fail = None
        self.close = session.stop

    def replacements(self):
        pairs = []
        _, init = self.session.wait(is_("system", "init"), 1)
        if init:
            pairs.append((init["session_id"], "<SESSION>"))
        tasks = []
        for event in self.session.events:
            if is_("system", "task_started")(event) and event["task_id"] not in tasks:
                tasks.append(event["task_id"])
        pairs += [(task, f"<TASK {n}>") for n, task in enumerate(tasks, 1)]
        return pairs

    def normalize(self, value):
        pairs = self.replacements()

        def text(string):
            for old, new in pairs:
                string = string.replace(old, new)
            string = UNLISTED.sub("", string)
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


def reference(binary, layout, port):
    layout.rebuild()
    env = layout.env(port)
    (layout.config / "settings.json").write_text(
        json.dumps({"hooks": platform_hooks(layout.platform_log)})
    )
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
        "cwd": str(layout.project),
    }
    session = Session(
        binary,
        layout.harness,
        "reference",
        DRIVER,
        launch=launch,
        home=layout.home,
        port=port,
    )
    return Run("reference", session, layout, env)


def remote(binary, layout, port):
    layout.rebuild()
    programs = layout.programs
    shutil.copyfile(SOURCE / "runtime.py", programs / "runtime.py")
    shutil.copyfile(SOURCE / "portable.py", programs / "portable.py")
    state = programs / "state"
    executor_env = layout.env(port, platform_cli=False)
    subprocess.run(
        [sys.executable, str(programs / "runtime.py"), "start", "--state", str(state)],
        input=json.dumps(
            {
                "workspace": str(layout.project),
                "claude": binary,
                "env": {},
                "mcp_servers": {},
            }
        ),
        text=True,
        check=True,
        capture_output=True,
        env=executor_env,
        cwd=str(programs),
    )
    relay = Relay(state)
    client.PINNED_VERSION = subprocess.run(
        [binary, "--version"], capture_output=True, text=True
    ).stdout.split()[0]
    central_home = layout.session / "home"
    central_home.mkdir()
    (central_home / ".bashrc").write_text(CENTRAL_BASHRC)
    (central_home / ".bash_profile").write_text("[ -f ~/.bashrc ] && . ~/.bashrc\n")
    base = {
        "hooks": {
            **platform_hooks(layout.platform_log),
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
    os.environ["CHEESE_TOKEN"] = "equivalence-execution-token"
    launch = client.prepare(
        layout.session,
        {
            "kind": "device",
            "device_id": DEVICE_ID,
            "url": relay.url,
            "mcp_servers": [],
        },
        claude=binary,
        base_settings=base,
        home_override=central_home,
        config_override=layout.config,
    )
    assert launch["workspace"] == str(layout.project), launch
    assert Path(launch["cwd"]) != layout.project, launch
    session = Session(
        binary,
        layout.harness,
        "central",
        DRIVER,
        launch=launch,
        home=central_home,
        port=port,
        # The session host's own shell, and a credential only it holds: the
        # executor must see neither.
        env={"SHELL": "/bin/bash", "CENTRAL_SECRET": "central-only-secret"},
    )
    run = Run("remote", session, layout, layout.env(port))
    listening = Path(execution_runtime.socket_path(state))

    def drop(seconds):
        """The link to the executor goes away, and comes back."""
        hidden = listening.with_name(listening.name + ".dropped")
        listening.rename(hidden)
        time.sleep(seconds)
        hidden.rename(listening)

    def close():
        session.stop()
        subprocess.run(
            [
                sys.executable,
                str(programs / "runtime.py"),
                "stop",
                "--state",
                str(state),
            ],
            capture_output=True,
            timeout=30,
        )
        relay.close()
        execution_release.release_mount(launch["cwd"])

    run.drop = drop
    run.fail = relay.fail
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
    # The build refuses this itself, naming its working directory. Removes
    # only the directory the shell is in; a run whose `cd` did not hold finds
    # nothing to remove instead of removing the workspace.
    turn(run, bash("rmdir ../vanishing && echo removed"))
    turn(run, bash("pwd"))
    return {}


# Every exported variable as `NAME=<json value>`, one per line, whatever the
# shell: the same listing from bash and zsh.
LIST_ENV = (
    "python3 -c 'import json, os; "
    '[print(k + "=" + json.dumps(v, ensure_ascii=False)) '
    "for k, v in sorted(os.environ.items())]'"
)


def step_environment(run):
    turn(run, bash('export EQ_EXPORTED=exported; echo "[$EQ_EXPORTED]"'))
    turn(run, bash('echo "[${EQ_EXPORTED:-unset}]"'))
    mark = turn(run, bash(LIST_ENV))
    listed = {}
    for text in tool_texts(run, mark):
        for line in text.splitlines():
            name, _, value = line.partition("=")
            with contextlib.suppress(ValueError):
                listed[name] = json.loads(value)
    added = {
        name: value
        for name, value in listed.items()
        if name != "PATH"
        and name not in HOST_PROCESS_ENV
        and run.env.get(name) != value
    }
    return {
        "variables the build set": added,
        "session-host credential visible": "CENTRAL_SECRET" in listed
        or any("central-only-secret" in value for value in listed.values()),
        "machine variable visible": listed.get("EQ_MACHINE") == "1",
    }


def step_shell(run):
    login = "[[ -o login ]]" if run.layout.shell == "zsh" else "shopt -q login_shell"
    turn(
        run,
        bash(
            "type exec_alias; exec_fn; command -v exec-tool; exec-tool; "
            "type central_alias 2>&1 | head -1; type central_fn 2>&1 | head -1; "
            'echo "$SHELL"; echo "${ZSH_VERSION:+zsh}${BASH_VERSION:+bash}"; '
            f"{login} && echo login || echo not-login; "
            # Word splitting of an unquoted variable: off in zsh, unless the
            # machine's own options turn it on.
            'words="one two"; for word in $words; do echo "[$word]"; done'
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


def step_transient(run):
    """The device drops for a moment as a command starts, and again while it
    is read, in the foreground and in the background. Nothing of that may
    show in what the command printed."""
    if run.fail:
        run.fail(start=1, read=2)
    turn(run, bash("echo before; sleep 1; echo done-fg"))
    if run.fail:
        run.fail(start=1, read=2)
    mark = run.session.user(
        do(
            "Bash",
            command="sleep 1; echo done-bg",
            run_in_background=True,
            description="bg",
        )
    )
    _, note = run.session.wait(is_("system", "task_notification"), 60, mark)
    if note:
        index = run.session.events.index(note)
        run.session.wait(is_("result"), 60, index + 1)
        turn(run, do("Read", file_path=note["output_file"]))
    return {"notified": note is not None}


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
    # Well past the point where the build reports a foreground command as a
    # task: a timeout close to it made that report a race between the runs.
    turn(run, bash("sleep 304", timeout=8000))
    gone = settle(lambda: not alive("sleep 304"))
    turn(run, bash("trap '' TERM; sleep 305", timeout=8000))
    stubborn_gone = settle(lambda: not alive("sleep 305"), timeout=15)
    turn(run, bash("(trap '' TERM; sleep 306) & sleep 307", timeout=8000))
    time.sleep(12)
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
    "transient": step_transient,
    "move": step_move,
    "stop_task": step_stop_task,
    "task_stop_tool": step_task_stop_tool,
    "interrupt": step_interrupt,
    "timeout": step_timeout,
    "link_drop": step_link_drop,
    # Last: it ends the session.
    "stdin_close": step_stdin_close,
}
LEFTOVERS = (
    "sleep 301",
    "sleep 302",
    "sleep 303",
    "sleep 304",
    "sleep 305",
    "sleep 306",
    "sleep 307",
    "sleep 309",
    "sleep 4",
)

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


def hook_logs(layout):
    logs = {}
    for name, directory in (
        ("project", layout.hook_log),
        ("platform", layout.platform_log),
    ):
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


def free_port():
    with contextlib.closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True, help="the claude binary to check")
    parser.add_argument(
        "--shell", choices=sorted(STARTUP), default="bash", help="the machine's shell"
    )
    parser.add_argument("--output", type=Path, help="where to write the receipt")
    parser.add_argument("--only", nargs="*", choices=list(STEPS), help="run only these")
    arguments = parser.parse_args()
    binary = str(Path(arguments.claude).resolve())
    if not shutil.which(arguments.shell):
        raise SystemExit(f"{arguments.shell} is not installed")
    version = subprocess.run(
        [binary, "--version"], capture_output=True, text=True
    ).stdout.strip()
    names = [name for name in STEPS if not arguments.only or name in arguments.only]
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="equivalence-")))
    layout = Layout(root, arguments.shell)
    port = free_port()
    records = {}
    try:
        for build in (reference, remote):
            run = build(binary, layout, port)
            try:
                run.session.control({"subtype": "initialize"})
                raw = play(run, names)
            finally:
                run.close()
            records[run.name] = {
                **{name: run.normalize(value) for name, value in raw.items()},
                "hooks": {
                    name: [run.normalize(entry) for entry in entries]
                    for name, entries in hook_logs(layout).items()
                },
            }
            # Nothing either run left behind may be taken for the other's.
            for pattern in LEFTOVERS:
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
            "machine shell": arguments.shell,
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
            for run_dir in layout.harness.iterdir():
                for name in ("transcript.jsonl", "stderr.log"):
                    found_file = run_dir / name
                    if found_file.exists():
                        target = arguments.output / run_dir.name
                        target.mkdir(parents=True, exist_ok=True)
                        shutil.copy(found_file, target / name)
            prefix_log = layout.session / "shell-prefix.log"
            if prefix_log.exists():
                shutil.copy(prefix_log, arguments.output / "shell-prefix.log")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(
        f"\n{version} ({arguments.shell}): "
        f"{sum(r['equal'] for r in results)}/{len(results)} steps equal"
    )
    return 0 if receipt["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
