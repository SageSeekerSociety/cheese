"""The build's own Bash, run on the executor through the shell prefix.

Everything here goes through the interfaces the product uses: a real
runtime.py executor process, its `control` subtype `shell`, the `client.py
shell` prefix the build execs, and the `shell-prefix` script `client.prepare`
writes.
"""

import base64
import json
import os
import shlex
import signal
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pinned_claude import claude_binary

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution"
)
RUNTIME = SOURCE / "runtime.py"
CLIENT = SOURCE / "client.py"


def _load_runtime():
    import importlib.util

    spec = importlib.util.spec_from_file_location("native_shell_runtime", RUNTIME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = _load_runtime()

# The executor gets an environment of its own, as a machine does: nothing of
# this test process's environment is in it.
EXECUTOR_ENV = {
    "PATH": os.environ["PATH"],
    "HOME": "",
    "LANG": "C.UTF-8",
    "SHELL": "/bin/bash",
}


class Machine:
    """A workspace on the "executor" and the runtime serving it."""

    def __init__(self, root: Path):
        self.root = root
        self.workspace = root / "executor project"
        self.workspace.mkdir(parents=True)
        self.state = root / "state"
        home = root / "executor-home"
        home.mkdir()
        self.env = {**EXECUTOR_ENV, "HOME": str(home)}
        process = subprocess.run(
            [sys.executable, str(RUNTIME), "start", "--state", str(self.state)],
            input=json.dumps(
                {
                    "workspace": str(self.workspace),
                    "claude": claude_binary(),
                    "env": {"EXECUTOR_MARKER": "on-the-executor"},
                }
            ),
            capture_output=True,
            text=True,
            timeout=30,
            env=self.env,
        )
        assert process.returncode == 0, process.stderr

    def control(self, **params):
        return runtime.request(self.state, "control", {"subtype": "shell", **params})

    def start(self, command_id, body, *, kind="sh", merge=True, cwd=None, stdin=None):
        return self.control(
            operation="start",
            command_id=command_id,
            kind=kind,
            body=body,
            cwd=str(cwd or self.workspace),
            env={},
            merge=merge,
            stdin=stdin,
        )

    def collect(self, command_id, timeout=30):
        out, err, deadline = b"", b"", time.monotonic() + timeout
        while True:
            answer = self.control(
                operation="read",
                command_id=command_id,
                out=len(out),
                err=len(err),
                wait=1,
            )
            out += base64.b64decode(answer["out"])
            err += base64.b64decode(answer["err"])
            if "exit" in answer:
                return out, err, answer
            assert time.monotonic() < deadline, (out, err)

    def stop(self):
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(self.state)],
            capture_output=True,
            timeout=30,
        )


@pytest.fixture
def machine(tmp_path):
    started = Machine(tmp_path / "machine")
    yield started
    started.stop()


def _alive(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def _wait_for(check, timeout=10):
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.05)


# --- the executor's side ------------------------------------------------------


def test_a_command_can_be_read_again_from_any_offset(machine):
    machine.start("slow", "for i in 1 2 3; do echo line$i; sleep 0.2; done; exit 4")
    first = machine.control(operation="read", command_id="slow", out=0, err=0, wait=5)
    head = base64.b64decode(first["out"])
    assert head.startswith(b"line1")
    out, _, answer = machine.collect("slow")
    assert out == b"line1\nline2\nline3\n"
    assert answer["exit"] == 4
    again = machine.control(operation="read", command_id="slow", out=2, err=0, wait=0)
    assert base64.b64decode(again["out"]) == out[2:]
    assert again["exit"] == 4


def test_the_same_start_is_one_command_and_other_input_is_refused(machine):
    body = "printf x >> count.txt"
    machine.start("once", body)
    machine.start("once", body)
    machine.collect("once")
    assert (machine.workspace / "count.txt").read_text() == "x"
    with pytest.raises(RuntimeError, match="different input"):
        machine.start("once", "printf y >> count.txt")


def test_forget_takes_a_finished_command_and_leaves_a_running_one(machine):
    machine.start("running", "sleep 30")
    with pytest.raises(RuntimeError, match="still running"):
        machine.control(operation="forget", command_id="running")
    machine.control(operation="signal", command_id="running", signal=signal.SIGKILL)
    machine.collect("running")
    machine.control(operation="forget", command_id="running")
    with pytest.raises(RuntimeError, match="Unknown command"):
        machine.control(operation="read", command_id="running", out=0, err=0)


def test_merged_output_keeps_its_order_and_separate_streams_stay_apart(machine):
    body = "echo o1; echo e1 >&2; echo o2; echo e2 >&2"
    machine.start("merged", body, merge=True)
    assert machine.collect("merged")[:2] == (b"o1\ne1\no2\ne2\n", b"")
    machine.start("apart", body, merge=False)
    assert machine.collect("apart")[:2] == (b"o1\no2\n", b"e1\ne2\n")


def test_a_bash_command_reports_the_directory_its_shell_ended_in(machine):
    (machine.workspace / "sub").mkdir()
    machine.start("cd", "eval 'cd sub' < /dev/null", kind="bash")
    _, _, answer = machine.collect("cd")
    assert Path(answer["cwd"]) == (machine.workspace / "sub").resolve()


def test_directory_lookups_see_only_the_projects_directories(machine):
    (machine.workspace / "made/here").mkdir(parents=True)
    (machine.workspace / "file.txt").write_text("x")
    (machine.workspace / "escape").symlink_to(machine.root)

    def lookup(operation, path):
        return runtime.request(
            machine.state, "context_fs", {"operation": operation, "path": path}
        )

    assert "mode" in lookup("directory", "made/here")
    assert lookup("list", "made") == {"directories": ["here"]}
    for path in ("file.txt", "missing", "../machine", str(machine.root), "escape"):
        assert lookup("directory", path) == {"missing": True}, path
    assert "escape" not in lookup("list", "")["directories"]


def test_the_projects_tool_hooks_travel_with_the_context_tree(machine):
    settings = machine.workspace / ".claude"
    settings.mkdir()
    (settings / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "echo pre"},
                                {"type": "command", "command": "x", "args": ["y"]},
                                {"type": "prompt", "prompt": "judge"},
                            ],
                        }
                    ],
                    "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}],
                }
            }
        )
    )
    (settings / "settings.local.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": "echo post"}]}
                    ]
                }
            }
        )
    )
    tree = runtime.request(machine.state, "context_fs", {"operation": "tree"})
    assert tree["hooks"] == {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}
        ],
        "PostToolUse": [{"hooks": [{"type": "command", "command": "echo post"}]}],
    }


def test_an_abandoned_watched_command_is_stopped_and_an_own_task_is_not(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "work"
    workspace.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text(
        json.dumps({"workspace": str(workspace), "claude": claude_binary(), "env": {}})
    )
    executor = runtime.Executor(state)
    monkeypatch.setattr(executor, "shell_snapshot", lambda: None)
    try:
        executor.shell(
            {
                "operation": "start",
                "command_id": "watched",
                "kind": "sh",
                "body": "sleep 51",
                "cwd": str(workspace),
                "env": {},
                "merge": True,
                "stdin": None,
            }
        )
        own = executor.bash({"command": "sleep 52", "run_in_background": True})
        _wait_for(lambda: _alive("^sleep 51$") and _alive("^sleep 52$"))
        monkeypatch.setattr(runtime, "COMMAND_ABANDONED_S", 0.5)
        time.sleep(1)
        rounds = iter([None])
        clock = SimpleNamespace(
            sleep=lambda _s: next(rounds),
            monotonic=time.monotonic,
            time=time.time,
        )
        monkeypatch.setattr(runtime, "time", clock)
        with pytest.raises(StopIteration):
            executor._reap()
        monkeypatch.setattr(runtime, "time", time)
        _wait_for(lambda: not _alive("^sleep 51$"))
        assert _alive("^sleep 52$")
        executor.stop_command(own["backgroundTaskId"])
    finally:
        executor.close()


# --- the prefix ---------------------------------------------------------------


class Session:
    """What the build gives its shell prefix: the target, a cwd file, its
    temp directory and a merged output file."""

    def __init__(self, root: Path, machine: Machine, kind=None):
        self.root = root
        self.central = root / "central" / "forwarded-project"
        self.central.mkdir(parents=True)
        self.config = root / "central" / "config"
        self.config.mkdir()
        self.tmp = root / "central" / "tmp"
        self.tmp.mkdir()
        self.target = root / "central" / "execution.json"
        self.target.write_text(
            json.dumps(
                {
                    "command": [sys.executable, str(RUNTIME)],
                    "state": str(machine.state),
                    "workspace": str(machine.workspace),
                    "central_workspace": str(self.central),
                    "central_config": str(self.config),
                    "central_tmp": str(self.tmp),
                    "central_hooks": {},
                    **({"kind": kind} if kind else {}),
                }
            )
        )
        self.output = root / "output"
        self.cwd_file = self.tmp / "claude-ab12-cwd"

    def wrapped(self, command):
        """A Bash command the way the build hands it to the prefix."""
        return (
            f"source {self.config}/shell-snapshots/snapshot-bash-1-x.sh "
            "2>/dev/null || true && shopt -u extglob 2>/dev/null || true && "
            f"eval {shlex.quote(command)} < /dev/null && pwd -P >| {self.cwd_file}"
        )

    def env(self, **extra):
        return {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root),
            "CLAUDE_CODE_TMPDIR": str(self.tmp),
            **extra,
        }

    def popen(self, argv, *, stdin=subprocess.DEVNULL, env=None, cwd=None):
        output = self.output.open("wb")
        try:
            return subprocess.Popen(
                argv,
                cwd=cwd or self.central,
                env=env or self.env(),
                stdin=stdin,
                stdout=output,
                stderr=output,
            )
        finally:
            output.close()

    def prefix(self, command, **options):
        return self.popen(
            [sys.executable, str(CLIENT), "shell", str(self.target), command],
            **options,
        )

    def run(self, command, timeout=60, **options):
        process = self.prefix(command, **options)
        code = process.wait(timeout=timeout)
        return code, self.output.read_bytes()


@pytest.fixture
def session(tmp_path, machine):
    return Session(tmp_path / "session", machine)


def test_a_bash_command_runs_there_and_the_build_learns_its_directory(session, machine):
    (machine.workspace / "only there").mkdir()
    code, output = session.run(
        session.wrapped(
            'printf "%s|%s\\n" "$PWD" "$EXECUTOR_MARKER"; echo err >&2; '
            "cd 'only there'; exit 3"
        )
    )
    assert code == 3
    assert output == f"{machine.workspace}|on-the-executor\nerr\n".encode()
    # exit 3 skips `pwd -P`: the directory is unchanged, as it is natively.
    assert not session.cwd_file.exists()
    code, _ = session.run(session.wrapped("cd 'only there'"))
    assert code == 0
    assert session.cwd_file.read_text() == f"{session.central}/only there\n"
    code, _ = session.run(session.wrapped("cd /"))
    assert session.cwd_file.read_text() == "/\n"


def test_the_prefix_starts_in_the_executors_spelling_of_its_directory(session, machine):
    (machine.workspace / "sub").mkdir()
    (session.central / "sub").mkdir()
    code, output = session.run(session.wrapped("pwd"), cwd=session.central / "sub")
    assert (code, output) == (0, f"{(machine.workspace / 'sub').resolve()}\n".encode())


DRIVER = textwrap.dedent(
    """
    import json, os, sys
    sys.path.insert(0, {source!r})
    import client
    for name, value in json.loads(os.environ["DRIVER_CONSTANTS"]).items():
        setattr(client, name, value)
    failures = int(os.environ.get("DRIVER_FAILURES", "0"))
    kind = os.environ.get("DRIVER_FAILURE", "out-of-reach")
    real = client.RemoteClient
    state = {{"reads": 0, "failed": 0}}

    class Flaky(real):
        def call(self, method, params=None):
            if (params or {{}}).get("operation") == "read":
                state["reads"] += 1
                if state["reads"] > 1 and state["failed"] < failures:
                    state["failed"] += 1
                    with open(os.environ["DRIVER_LOG"], "a") as log:
                        log.write("dropped\\n")
                    import time
                    time.sleep(0.3)
                    if kind == "os":
                        raise ConnectionResetError("link dropped")
                    raise client.MachineOutOfReach()
            return super().call(method, params)

    client.RemoteClient = Flaky
    raise SystemExit(client.shell(sys.argv[1], sys.argv[2]))
    """
)


def _driver(session, command, *, constants=None, failures=0, failure="out-of-reach"):
    script = session.root / "driver.py"
    script.write_text(DRIVER.format(source=str(SOURCE)))
    log = session.root / "driver.log"
    env = session.env(
        DRIVER_CONSTANTS=json.dumps(constants or {}),
        DRIVER_FAILURES=str(failures),
        DRIVER_FAILURE=failure,
        DRIVER_LOG=str(log),
    )
    return (
        session.popen(
            [sys.executable, str(script), str(session.target), command], env=env
        ),
        log,
    )


@pytest.mark.parametrize("failure", ["out-of-reach", "os"])
def test_a_dropped_link_loses_and_repeats_nothing(session, machine, failure):
    command = (
        "for i in 1 2 3 4 5 6; do echo line$i; date +%s%N >> ran.txt; "
        "sleep 0.3; done; exit 4"
    )
    process, log = _driver(
        session, session.wrapped(command), failures=4, failure=failure
    )
    assert process.wait(timeout=60) == 4
    assert log.read_text().count("dropped") == 4
    assert session.output.read_bytes() == b"".join(
        f"line{i}\n".encode() for i in range(1, 7)
    )
    # It ran through the drop at its own pace, not paused with the reader.
    stamps = [int(line) for line in (machine.workspace / "ran.txt").read_text().split()]
    assert len(stamps) == 6
    assert (stamps[-1] - stamps[0]) / 1e9 < 5 * 0.3 + 1.5


def test_a_stop_reaches_the_command_and_the_prefix_ends_by_it(session, machine):
    process = session.prefix(session.wrapped("touch started; sleep 61"))
    _wait_for(lambda: (machine.workspace / "started").exists())
    assert _alive("^sleep 61$")
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=30) == -signal.SIGTERM
    _wait_for(lambda: not _alive("^sleep 61$"))


def test_a_command_that_ignores_the_stop_is_killed_after_the_grace(session, machine):
    process, _ = _driver(
        session,
        session.wrapped("trap '' TERM; touch started; sleep 62; echo survived"),
        constants={"SHELL_STOP_GRACE_S": 1.0},
    )
    _wait_for(lambda: (machine.workspace / "started").exists())
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=30) < 0
    _wait_for(lambda: not _alive("^sleep 62$"))
    assert b"survived" not in session.output.read_bytes()


def test_output_past_the_cap_stops_the_command(session, machine):
    process, _ = _driver(
        session,
        session.wrapped(
            "head -c 20000 /dev/zero | tr '\\0' a; touch halfway; sleep 63; "
            "head -c 20000 /dev/zero | tr '\\0' b"
        ),
        constants={"SHELL_OUTPUT_BYTES": 10000},
    )
    assert process.wait(timeout=30) == 1
    output = session.output.read_bytes()
    assert b"b" not in output.replace(b"stopped", b"").replace(b"MiB", b"")
    assert output.count(b"a") <= 10000 + 10
    _wait_for(lambda: not _alive("^sleep 63$"))


def test_hook_input_over_the_limit_is_refused_before_it_travels(session, machine):
    script = session.root / "driver.py"
    script.write_text(DRIVER.format(source=str(SOURCE)))
    process = session.popen(
        [
            sys.executable,
            str(script),
            str(session.target),
            "cat > got-input.txt",
        ],
        stdin=subprocess.PIPE,
        env=session.env(
            DRIVER_CONSTANTS=json.dumps({"SHELL_INPUT_BYTES": 100}),
            DRIVER_LOG=str(session.root / "log"),
        ),
    )
    process.communicate(b"x" * 1000, timeout=30)
    assert process.returncode != 0
    assert not (machine.workspace / "got-input.txt").exists()


def test_a_hook_gets_its_input_spelled_for_the_executor(session, machine):
    process = session.prefix(
        'cat > hook-input.json; printf "%s" "$CLAUDE_PROJECT_DIR" > project-dir.txt',
        stdin=subprocess.PIPE,
        env=session.env(CLAUDE_PROJECT_DIR=str(session.central)),
    )
    process.communicate(
        json.dumps({"cwd": str(session.central), "tool_name": "Bash"}).encode(),
        timeout=60,
    )
    assert process.returncode == 0
    assert json.loads((machine.workspace / "hook-input.json").read_text()) == {
        "cwd": str(machine.workspace),
        "tool_name": "Bash",
    }
    assert (machine.workspace / "project-dir.txt").read_text() == str(machine.workspace)


def test_this_hosts_environment_stays_here(session, machine):
    code, _ = session.run(
        session.wrapped("env > env.txt"),
        env=session.env(
            CENTRAL_SECRET="s3cret-central",
            ANTHROPIC_API_KEY="sk-central",
            CHEESE_TOKEN="central-token",
            CLAUDE_CODE_MESSAGING_TOKEN="messaging-token",
            CLAUDE_CODE_MESSAGING_SOCKET="/central/socket",
            CLAUDE_PID="4242",
            CLAUDECODE="1",
            GIT_EDITOR="true",
            CLAUDE_CODE_ENTRYPOINT="sdk-cli",
        ),
    )
    assert code == 0
    remote = dict(
        line.split("=", 1)
        for line in (machine.workspace / "env.txt").read_text().splitlines()
        if "=" in line
    )
    for secret in (
        "CENTRAL_SECRET",
        "ANTHROPIC_API_KEY",
        "CHEESE_TOKEN",
        "CLAUDE_CODE_MESSAGING_TOKEN",
        "CLAUDE_CODE_MESSAGING_SOCKET",
        "CLAUDE_PID",
        "CLAUDE_CODE_TMPDIR",
    ):
        assert secret not in remote, secret
    assert "s3cret-central" not in (machine.workspace / "env.txt").read_text()
    assert remote["CLAUDECODE"] == "1"
    assert remote["GIT_EDITOR"] == "true"
    assert remote["CLAUDE_CODE_ENTRYPOINT"] == "sdk-cli"
    assert remote["EXECUTOR_MARKER"] == "on-the-executor"


@pytest.mark.parametrize(
    "target",
    [
        "{root}/central/elsewhere-cwd",
        "{tmp}/settings.json",
        "{tmp}/claude-ab12-cwd.extra",
        "{tmp}/sub/claude-ab12-cwd",
    ],
)
def test_only_the_builds_own_cwd_file_is_written(session, machine, target):
    path = Path(target.format(root=session.root, tmp=session.tmp))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("untouched")
    command = f"true && pwd -P >| {path}"
    code, _ = session.run(command)
    assert code == 0
    assert path.read_text() == "untouched"


def test_a_machine_out_of_reach_is_said_at_once(tmp_path, machine):
    session = Session(tmp_path / "gone", machine, kind="unavailable")
    started = time.monotonic()
    code, output = session.run(session.wrapped("echo never"))
    assert code == 1
    assert time.monotonic() - started < 20
    assert b"never" not in output


# --- the launch -----------------------------------------------------------------


def _prepared(tmp_path, machine, monkeypatch):
    from app.domain.agent.harness.claude_code.remote_execution import client, release

    monkeypatch.setattr(release, "mount_state", lambda _path: release.MOUNT_LIVE)
    settings = machine.workspace / ".claude"
    settings.mkdir(exist_ok=True)
    (settings / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "touch project-hook"}
                            ],
                        }
                    ]
                }
            }
        )
    )
    home = tmp_path / "central-home"
    launch = client.prepare(
        tmp_path / "central-session",
        {
            "command": [sys.executable, str(RUNTIME)],
            "state": str(machine.state),
        },
        claude=claude_binary(),
        base_settings={
            "hooks": {
                "Stop": [
                    {"hooks": [{"type": "command", "command": "touch platform-hook"}]}
                ]
            }
        },
        home_override=home,
        config_override=home / ".claude",
    )
    return launch, home


def test_only_whole_trusted_commands_run_on_this_host(tmp_path, machine, monkeypatch):
    launch, home = _prepared(tmp_path, machine, monkeypatch)
    prefix = launch["env"]["CLAUDE_CODE_SHELL_PREFIX"]
    central = Path(launch["cwd"])
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "CLAUDE_CODE_TMPDIR": launch["env"]["CLAUDE_CODE_TMPDIR"],
    }

    def run(command):
        return subprocess.run(
            [prefix, command],
            cwd=central,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=60,
        )

    for command, made, where in (
        ("touch platform-hook", "platform-hook", central),
        ("touch arbitrary", "arbitrary", machine.workspace),
        ("touch platform-hook; touch appended", "appended", machine.workspace),
        ("touch project-hook", "project-hook", machine.workspace),
        (
            f"touch '{central}/absolute-central-path'",
            "absolute-central-path",
            machine.workspace,
        ),
    ):
        assert run(command).returncode == 0, command
        assert (where / made).exists(), command
    assert (central / "platform-hook").exists()
    for name in ("arbitrary", "appended", "project-hook"):
        assert (machine.workspace / name).exists(), name
        assert not (central / name).exists(), name
    assert not (central / "absolute-central-path").exists()
    assert (machine.workspace / "absolute-central-path").exists()


def test_the_projects_hooks_are_registered_centrally_and_run_there(
    tmp_path, machine, monkeypatch
):
    launch, home = _prepared(tmp_path, machine, monkeypatch)
    written = json.loads((home / ".claude/settings.json").read_text())
    registered = [
        hook["command"]
        for group in written["hooks"]["PreToolUse"]
        for hook in group["hooks"]
    ]
    assert "touch project-hook" in registered
    # Bash itself is not behind the guard: the build runs it.
    guard = written["hooks"]["PreToolUse"][0]["matcher"].split("|")
    assert "Bash" not in guard and "Read" in guard
    assert launch["env"]["CLAUDE_CODE_TMPDIR"]


# --- the guard ------------------------------------------------------------------


def _guard(target, call):
    done = subprocess.run(
        [sys.executable, str(CLIENT), "guard", str(target)],
        input=json.dumps(call),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return "deny" if done.stdout.strip() else "allow"


def test_the_guard_admits_only_reads_of_the_builds_own_output(tmp_path, machine):
    session = Session(tmp_path / "guarded", machine)
    output = session.tmp / "claude-501/-w/s/tasks/b1.output"
    output.parent.mkdir(parents=True)
    output.write_text("x")
    result = session.config / "projects/-w/s/tool-results/r.txt"
    result.parent.mkdir(parents=True)
    result.write_text("x")
    (session.tmp / "link").symlink_to(tmp_path)
    transcript = session.config / "projects/-w/s.jsonl"
    transcript.write_text("{}")
    for path, verdict in (
        (output, "allow"),
        (result, "allow"),
        (f"{session.tmp}/../execution.json", "deny"),
        (session.tmp / "link/elsewhere", "deny"),
        (transcript, "deny"),
        (session.central / "a.txt", "deny"),
    ):
        call = {"tool_name": "Read", "tool_input": {"file_path": str(path)}}
        assert _guard(session.target, call) == verdict, path
    for tool in ("Write", "Edit", "NotebookEdit", "Glob", "Grep"):
        call = {"tool_name": tool, "tool_input": {"file_path": str(output)}}
        assert _guard(session.target, call) == "deny", tool


def test_a_new_command_id_is_new(machine):
    ids = {f"shell-{uuid.uuid4().hex}" for _ in range(3)}
    for command_id in ids:
        machine.start(command_id, "true")
    assert all(machine.collect(command_id)[2]["exit"] == 0 for command_id in ids)
