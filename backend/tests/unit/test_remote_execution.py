"""Exercise the executor through its public process/socket protocol."""

import ast
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

if __package__:
    from tests.pinned_claude import claude_binary
else:
    # The acceptance suite runs this file as a script, from outside the package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pinned_claude import claude_binary

RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution/runtime.py"
)
spec = importlib.util.spec_from_file_location("execution_runtime", RUNTIME)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_a_background_command_that_finishes_at_once_still_has_an_id_and_an_exit(
    tmp_path,
):
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text(
        json.dumps({"workspace": str(tmp_path), "claude": claude_binary(), "env": {}})
    )
    executor = runtime.Executor(state)
    try:
        for code in (0, 7):
            result = executor.invoke(
                {
                    "id": str(uuid.uuid4()),
                    "tool": "Bash",
                    "args": {
                        "command": f"printf completed; exit {code}",
                        "run_in_background": True,
                    },
                }
            )
            task_id = result["value"]["backgroundTaskId"]
            report = executor.task_output({"task_id": task_id, "timeout": 10000})
            assert report["task"]["output"] == "completed"
            assert report["task"]["exitCode"] == code
            assert report["task"]["status"] == ("completed" if code == 0 else "failed")
    finally:
        executor.close()


def test_executor_bootstrap_starts_in_room_without_a_git_checkout(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-executor")
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    # Use the installation payload shipped to devices, including its CLI.
    program = script(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    call = ast.parse(program).body[-1].value
    payload = json.loads(ast.literal_eval(call.args[0].args[0]))
    try:
        bootstrap.configure(payload)
        assert json.loads(capsys.readouterr().out)["workspace"] == str(home / "room")
        assert not (home / "room/.git").exists()
        config = json.loads((state / "config.json").read_text())
        assert "ANTHROPIC_API_KEY" not in config["env"]
        installed = subprocess.run(
            [str(home / ".cheese/cheese"), "--help"],
            env={**os.environ, **config["env"]},
            capture_output=True,
            text=True,
            check=True,
        )
        assert "worktree" in installed.stdout
        deadline = time.monotonic() + 10
        while not Path(runtime.socket_path(state)).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert runtime.request(state, "ping")["workspace"] == str(home / "room")
        publication_help = runtime.request(
            state,
            "invoke",
            {
                "id": "cli-worker-help",
                "tool": "Bash",
                "args": {
                    "command": 'test -S "$CHEESE_CLI_SOCKET" && cheese chat send --help'
                },
            },
        )
        assert "--request-id" in publication_help["value"]["stdout"]
        (home / ".cheese/cheese").write_text(
            "import sys\n"
            "if __name__ == 'preload':\n"
            "    sys.cheese_cli_preloaded = True\n"
            "if __name__ == '__main__':\n"
            "    print(getattr(sys, 'cheese_cli_preloaded', False))\n"
        )
        preloaded = runtime.request(
            state,
            "invoke",
            {
                "id": "cli-preloaded-dispatch",
                "tool": "Bash",
                "args": {"command": "cheese"},
            },
        )
        assert preloaded["value"]["stdout"].strip() == "True"
        from app.domain.agent import environment_runner

        environment = home / ".cheese-environment"
        environment.mkdir()
        payload["environment"] = {"revision": "existing"}
        for status in ("ready", "failed", "pending"):
            environment_runner.write_json(
                environment / "status.json",
                {
                    "state": status,
                    "pid": os.getpid(),
                    "process_identity": environment_runner.process_identity(
                        os.getpid()
                    ),
                },
            )
            bootstrap.configure(payload)
            reused = json.loads(capsys.readouterr().out)
            assert reused["pid"] == runtime.request(state, "ping")["pid"]
            assert reused["environment_status"] == status
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def _room_prepared_under_the_previous_root(tmp_path, monkeypatch):
    """A room as it exists today: its executor installed in `.claude`.

    Returns the payload that would prepare it again under the root in force now,
    and the previous root's directory.
    """
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    previous = home / ".claude"
    (previous / "remote-execution").mkdir(parents=True)
    # Its OWN runtime, the one that started it: the protocol a running executor
    # answers is the one it was installed with, not the one being installed now.
    shutil.copyfile(RUNTIME, previous / "remote-execution/runtime.py")
    work = home / "room"
    work.mkdir(parents=True)
    program = script(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    call = ast.parse(program).body[-1].value
    payload = json.loads(ast.literal_eval(call.args[0].args[0]))
    return payload, home, previous, resource


def _await_socket(state, timeout=10):
    deadline = time.monotonic() + timeout
    while not Path(runtime.socket_path(state)).exists():
        assert time.monotonic() < deadline, f"executor never answered at {state}"
        time.sleep(0.01)


def _start_executor(state, workspace):
    subprocess.run(
        [sys.executable, str(RUNTIME), "start", "--state", str(state)],
        input=json.dumps({"workspace": str(workspace), "env": {}}),
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )


def test_a_room_left_under_the_previous_root_loses_its_old_executor(
    tmp_path, monkeypatch, capsys
):
    """Moving the platform's own directory has to come back for what the old one
    started.

    The executor is a detached daemon — closing a screen does not close it — and
    the branch that stops a previous executor looks for its state under the root
    in force today. A room prepared under an earlier root would therefore keep
    its daemon running and get the new one beside it: two processes, one HOME,
    one `.cheese-environment/status.json` between them.
    """
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    payload, home, previous, resource = _room_prepared_under_the_previous_root(
        tmp_path, monkeypatch
    )
    old_state = previous / "executor"
    _start_executor(old_state, home / "room")
    old_pid = runtime.request(old_state, "ping")["pid"]
    state = home / ".cheese/executor"
    try:
        bootstrap.configure(payload)

        # The one that was running is not running any more, and what is left
        # under the old root says nothing about a room that is.
        deadline = time.monotonic() + 10
        while True:
            try:
                os.kill(old_pid, 0)
            except OSError:
                break
            assert time.monotonic() < deadline, "the old executor is still running"
            time.sleep(0.01)
        assert not old_state.exists()
        assert not (previous / "execution-owner.json").exists()
        # The room came up under the root in force, and only there.
        _await_socket(state)
        assert runtime.request(state, "ping")["workspace"] == str(home / "room")
        assert json.loads((home / ".cheese/execution-owner.json").read_text()) == {
            "resource": str(resource)
        }
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_a_previous_root_with_nothing_behind_it_does_not_hold_the_room_back(
    tmp_path, monkeypatch, capsys
):
    """A machine that rebooted leaves the old executor's state on disk with no
    process behind it. That is the ordinary case, not a reason to refuse the
    room — the stop has to ask whether anything is there before insisting."""
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    payload, home, previous, _resource = _room_prepared_under_the_previous_root(
        tmp_path, monkeypatch
    )
    old_state = previous / "executor"
    old_state.mkdir()
    (old_state / "config.json").write_text(
        json.dumps({"workspace": str(home / "room"), "env": {}})
    )
    (previous / "execution-owner.json").write_text('{"resource": "whatever"}')
    state = home / ".cheese/executor"
    try:
        bootstrap.configure(payload)

        _await_socket(state)
        assert runtime.request(state, "ping")["workspace"] == str(home / "room")
        assert not old_state.exists()
        assert not (previous / "execution-owner.json").exists()
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_executor_release_waits_for_commands_and_preserves_results(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    payload = payload_for(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    source = home / ".cheese/remote-execution/runtime.py"

    def ready():
        deadline = time.monotonic() + 10
        while True:
            try:
                return runtime.request(state, "ping")
            except (ConnectionError, FileNotFoundError):
                assert time.monotonic() < deadline
                time.sleep(0.01)

    try:
        bootstrap.configure(payload)
        capsys.readouterr()
        original = ready()
        task = runtime.request(
            state,
            "invoke",
            {
                "id": "retained-output",
                "tool": "Bash",
                "args": {
                    "command": (
                        "while [ ! -f release ]; do sleep 0.05; done; printf kept"
                    ),
                    "run_in_background": True,
                },
            },
        )["value"]["backgroundTaskId"]
        changed = (
            base64.b64decode(payload["files"]["remote-execution/runtime.py"])
            + b"\n# release fixture\n"
        )
        payload["files"]["remote-execution/runtime.py"] = base64.b64encode(
            changed
        ).decode()
        before = source.read_bytes()
        with unittest.TestCase().assertRaisesRegex(RuntimeError, "running commands"):
            bootstrap.configure(payload)
        assert source.read_bytes() == before
        assert ready()["pid"] == original["pid"]
        (home / "room/release").touch()
        deadline = time.monotonic() + 10
        while True:
            result = runtime.request(
                state, "control", {"subtype": "task_output", "task_id": task}
            )
            if result["status"] != "running":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert result["stdout"] == "kept"
        bootstrap.configure(payload)
        capsys.readouterr()
        updated = ready()
        assert updated["pid"] != original["pid"]
        assert updated["runtime_sha256"] == hashlib.sha256(changed).hexdigest()
        assert (
            runtime.request(
                state, "control", {"subtype": "task_output", "task_id": task}
            )["stdout"]
            == "kept"
        )
        bootstrap.configure(payload)
        capsys.readouterr()
        assert ready()["pid"] == updated["pid"]
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_a_platform_tool_answers_while_a_shell_command_still_holds_the_room(
    tmp_path, monkeypatch, capsys
):
    """The listing a new session needs cannot be made to wait for the shell.

    Claude Code allows the native server 30s to answer `tools/list` and drops it
    for the whole session when the answer is late — the room then denies every
    file, shell and chat tool. On 2026-09-17 one listing queued behind a 60s
    command and a room stayed dead for three hours, so the listing has to answer
    while a command is still running, not after it.
    """
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import payload_for

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "binary", lambda *_: claude_binary())
    project, resource = uuid.uuid4(), uuid.uuid4()
    payload = payload_for(
        project, resource, {"CHEESE_API": "http://unused", "CHEESE_TOKEN": "test"}
    )
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    release = home / "room/release"
    try:
        bootstrap.configure(payload)
        capsys.readouterr()
        _await_socket(state)
        held = threading.Thread(
            target=runtime.request,
            args=(
                state,
                "invoke",
                {
                    "id": "holds-the-shell",
                    "tool": "Bash",
                    "args": {
                        "command": (
                            "touch running; while [ ! -f release ]; do sleep 0.05; done"
                        ),
                        "timeout": 5000,
                    },
                },
            ),
            daemon=True,
        )
        held.start()
        running = home / "room/running"
        deadline = time.monotonic() + 10
        while not running.exists():
            assert time.monotonic() < deadline, "the command never started"
            time.sleep(0.01)

        started = time.monotonic()
        listing = runtime.request(state, "cli", {"method": "tools/list"})
        waited = time.monotonic() - started

        assert waited < 2, f"the listing waited {waited:.1f}s for the shell"
        assert "cheese_status" in {tool["name"] for tool in listing["tools"]}
        assert not release.exists(), "the command had already finished"
    finally:
        release.touch()
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_running_executor_prepares_updated_room_without_restart(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    binary = tmp_path / ".cheese/claude/versions" / bootstrap.VERSION
    binary.parent.mkdir(parents=True)
    version_calls = tmp_path / "version-calls"
    # The stub counts version checks; the executor's commands need the real
    # build behind it, because every command runs through its `mcp serve`.
    binary.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"  echo checked >> '{version_calls}'\n"
        f"  echo '{bootstrap.VERSION}'\n"
        "  exit 0\n"
        "fi\n"
        f"exec '{claude_binary()}' \"$@\"\n"
    )
    binary.chmod(0o700)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".cheese/executor"
    call = (
        ast.parse(
            script(
                project,
                resource,
                {
                    "CHEESE_API": "http://unused",
                    "CHEESE_TOKEN": "first",
                },
            )
        )
        .body[-1]
        .value
    )
    payload = json.loads(ast.literal_eval(call.args[0].args[0]))
    import base64

    cli = (
        "from pathlib import Path\n"
        "if __name__ == 'preload':\n"
        "    with Path(__file__).with_name('preload-calls').open('a') as output:\n"
        "        output.write('loaded\\n')\n"
        "if __name__ == '__main__':\n"
        "    print('first CLI')\n"
    )
    payload["files"]["cheese"] = base64.b64encode(cli.encode()).decode()
    try:
        bootstrap.configure(payload)
        capsys.readouterr()
        deadline = time.monotonic() + 10
        while not Path(runtime.socket_path(state)).exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        original = runtime.request(state, "ping")
        assert "prepare" in original["capabilities"]
        from app.domain.agent.harness.claude_code.remote_execution.launch import (
            payload_for,
        )

        delta = payload_for(project, resource, payload["env"], original["files"])
        # The fixture replaces the CLI; all other installed helpers are unchanged.
        assert set(delta["files"]) == {"cheese"}
        (home / ".cheese/cheese-hook").write_text("locally edited helper")
        changed = runtime.request(state, "ping")
        repair = payload_for(project, resource, payload["env"], changed["files"])
        assert set(repair["files"]) == {"cheese", "cheese-hook"}
        payload["env"]["CHEESE_TOKEN"] = "refreshed"
        payload["files"]["cheese-hook"] = base64.b64encode(b"updated hook").decode()
        ready = runtime.request(state, "prepare", payload)
        assert ready["pid"] == original["pid"]
        assert ready["workspace"] == str(home / "room")
        assert (home / ".cheese/cheese-preview.token").read_text() == "refreshed"
        assert (home / ".cheese/cheese-hook").read_text() == "updated hook"
        assert (
            json.loads((state / "config.json").read_text())["env"]["CHEESE_TOKEN"]
            == "refreshed"
        )
        for iteration in range(2):
            result = runtime.request(
                state,
                "invoke",
                {
                    "id": f"preload-{iteration}",
                    "tool": "Bash",
                    "args": {"command": "cheese --version"},
                },
            )
            assert result["value"]["stdout"].strip() == "first CLI"
            assert (home / ".cheese/preload-calls").read_text() == "loaded\n"
            runtime.request(state, "prepare", payload)
        payload["files"]["cheese"] = base64.b64encode(
            cli.replace("first CLI", "updated CLI").encode()
        ).decode()
        runtime.request(state, "prepare", payload)
        updated = runtime.request(
            state,
            "invoke",
            {
                "id": "updated-cli",
                "tool": "Bash",
                "args": {"command": "cheese --version"},
            },
        )
        assert updated["value"]["stdout"].strip() == "updated CLI"
        assert (home / ".cheese/preload-calls").read_text() == "loaded\nloaded\n"
        checked = version_calls.read_text()
        runtime.request(state, "prepare", payload)
        assert version_calls.read_text() == checked
        before = binary.stat()
        binary.write_text(binary.read_text() + "# changed in place\n")
        os.utime(binary, ns=(before.st_atime_ns, before.st_mtime_ns))
        runtime.request(state, "prepare", payload)
        assert version_calls.read_text() == checked + "checked\n"
        checked = version_calls.read_text()
        replacement = binary.with_suffix(".replacement")
        replacement.write_text(binary.read_text())
        replacement.chmod(0o700)
        replacement.replace(binary)
        runtime.request(state, "prepare", payload)
        assert version_calls.read_text() == checked + "checked\n"
        from app.domain.agent import environment_runner

        environment = home / ".cheese-environment"
        environment.mkdir()
        payload["environment"] = {"revision": "existing"}
        for status in ("ready", "failed", "pending"):
            environment_runner.write_json(
                environment / "status.json",
                {
                    "state": status,
                    "pid": os.getpid(),
                    "process_identity": environment_runner.process_identity(
                        os.getpid()
                    ),
                },
            )
            prepared = runtime.request(state, "prepare", payload)
            assert prepared["pid"] == original["pid"]
            assert prepared["environment_status"] == status
        payload["resource"] = str(uuid.uuid4())
        import pytest

        with pytest.raises(RuntimeError, match="another room"):
            runtime.request(state, "prepare", payload)
        assert not (home.parent / payload["resource"]).exists()
    finally:
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(state)],
            capture_output=True,
            timeout=15,
        )


def test_modified_verified_binary_with_wrong_version_is_not_reused(tmp_path):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap

    binary = tmp_path / ".cheese/claude/versions" / bootstrap.VERSION
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\necho '{bootstrap.VERSION}'\n")
    binary.chmod(0o700)
    fallback = tmp_path / ".local/bin/claude"
    fallback.parent.mkdir(parents=True)
    fallback.write_text(binary.read_text())
    fallback.chmod(0o700)
    verified = {}
    assert bootstrap.binary(tmp_path, "http://unused", verified) == str(binary)
    binary.write_text("#!/bin/sh\necho '0.0.0'\n")
    assert bootstrap.binary(tmp_path, "http://unused", verified) == str(fallback)


class RemoteExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cheese-execution-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "project with spaces"
        self.workspace.mkdir()
        self.state = self.root / "state"
        claude = os.environ.get("CHEESE_TEST_CLAUDE") or shutil.which("claude")
        self.assertIsNotNone(
            claude, "Install the pinned Claude Code build before running acceptance"
        )
        self.config = {
            "workspace": str(self.workspace),
            "claude": claude,
            "env": {"EXECUTOR_MARKER": "remote-environment"},
        }
        self.start()
        self.addCleanup(self.stop)

    def start(self):
        process = subprocess.run(
            [sys.executable, str(RUNTIME), "start", "--state", str(self.state)],
            input=json.dumps(self.config),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.pid = json.loads(process.stdout)["pid"]

    def stop(self):
        subprocess.run(
            [sys.executable, str(RUNTIME), "stop", "--state", str(self.state)],
            capture_output=True,
            timeout=15,
        )

    def invoke(self, tool, args, key=None):
        response = runtime.request(
            self.state,
            "invoke",
            {"id": key or str(uuid.uuid4()), "tool": tool, "args": args},
        )
        self.assertNotIn("error", response, response)
        return response["value"]

    def test_native_files_and_rc_agree(self):
        target = self.workspace / "sample.txt"
        target.write_text("before\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "add", "."], cwd=self.workspace, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "seed",
            ],
            cwd=self.workspace,
            check=True,
        )
        read = self.invoke("Read", {"file_path": str(target)})
        self.assertEqual(read["file"]["content"], "before\n")
        self.invoke(
            "Edit",
            {"file_path": str(target), "old_string": "before", "new_string": "after"},
        )
        self.invoke(
            "Write", {"file_path": str(self.workspace / "new.txt"), "content": "你好\n"}
        )
        preview = runtime.request(
            self.state, "control", {"subtype": "read_file", "path": "sample.txt"}
        )
        self.assertEqual(preview["contents"], "after\n")
        diff = runtime.request(self.state, "control", {"subtype": "get_workspace_diff"})
        self.assertIn("+after", diff["diff"])
        self.assertEqual((self.workspace / "new.txt").read_text(), "你好\n")

    def test_shell_cwd_environment_and_exit_status(self):
        (self.workspace / "sub dir").mkdir()
        self.invoke("Bash", {"command": "cd 'sub dir'"})
        result = self.invoke(
            "Bash",
            {
                "command": 'printf "%s\\n" "$PWD" "$EXECUTOR_MARKER"; '
                "printf failure >&2; exit 7"
            },
        )
        # A failure comes back the way the build gives it to its own agent:
        # the exit code first, then everything the command printed, together.
        self.assertTrue(result["stdout"].startswith("Exit code 7"), result)
        self.assertIn(
            str(self.workspace / "sub dir") + "\nremote-environment", result["stdout"]
        )
        self.assertIn("failure", result["stdout"])
        self.assertEqual(result["stderr"], "")

    def test_a_refreshed_environment_reaches_the_next_command(self):
        # A token arrives refreshed through `configure` while the serve process
        # keeps the environment it started with; the command must see the new
        # value, or every `cheese` call from the shell dies with the old token.
        self.assertEqual(
            self.invoke("Bash", {"command": 'printf "$EXECUTOR_MARKER"'})["stdout"],
            "remote-environment",
        )
        runtime.request(
            self.state, "configure", {"env": {"EXECUTOR_MARKER": "refreshed"}}
        )
        self.assertEqual(
            self.invoke("Bash", {"command": 'printf "$EXECUTOR_MARKER"'})["stdout"],
            "refreshed",
        )

    def test_request_replay_does_not_repeat_write(self):
        args = {"command": "printf x >> count.txt"}
        original = self.invoke("Bash", args, key="same-request")
        self.assertEqual(self.invoke("Bash", args, key="same-request"), original)
        self.assertEqual((self.workspace / "count.txt").read_text(), "x")
        with self.assertRaisesRegex(RuntimeError, "different input"):
            self.invoke(
                "Bash", {"command": "printf y >> count.txt"}, key="same-request"
            )

    def finished_task(self, task_id, timeout=30000):
        """A task's output once it has finished, or a failure that says why.

        `'' != 'done'` has been failing this file on CI since at least
        2026-09-19 and says nothing about the cause. `TaskOutput` reports
        `retrieval_status: "timeout"` when its wait runs out, and `status:
        "unknown"` for a task this executor no longer holds — both of which
        come back with the output so far. Checking them here turns the next
        failure into its own diagnosis instead of a bare string mismatch.
        """
        result = self.invoke(
            "TaskOutput", {"task_id": task_id, "block": True, "timeout": timeout}
        )
        self.assertEqual(
            result["retrieval_status"],
            "success",
            f"task did not finish within {timeout} ms: {result}",
        )
        self.assertEqual(
            result["task"]["status"],
            "completed",
            f"task did not complete: {result}",
        )
        return result

    def test_reconnect_retains_background_task(self):
        task = self.invoke(
            "Bash",
            {"command": "sleep 0.2; printf background-done", "run_in_background": True},
        )
        previous_pid = self.pid
        self.start()
        self.assertEqual(self.pid, previous_pid)
        result = self.finished_task(task["backgroundTaskId"])
        self.assertEqual(result["task"]["output"], "background-done")
        self.assertEqual(result["task"]["status"], "completed")

    def test_stop_kills_descendant_ignoring_term(self):
        task = self.invoke(
            "Bash",
            {
                "command": 'bash -c \'trap "" TERM; touch started; sleep 2; '
                "printf survived > forbidden.txt' & wait",
                "run_in_background": True,
            },
        )
        deadline = time.monotonic() + 3
        while not (self.workspace / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.workspace / "started").exists())
        stopped = self.invoke("TaskStop", {"task_id": task["backgroundTaskId"]})
        self.assertEqual(stopped["task_id"], task["backgroundTaskId"])
        time.sleep(2.2)
        self.assertFalse((self.workspace / "forbidden.txt").exists())
        output = self.invoke("TaskOutput", {"task_id": task["backgroundTaskId"]})
        self.assertEqual(output["task"]["status"], "stopped")

    def test_restart_preserves_completed_request_receipt(self):
        self.invoke("Bash", {"command": "printf x >> count.txt"}, key="persisted")
        self.stop()
        self.start()
        self.invoke("Bash", {"command": "printf x >> count.txt"}, key="persisted")
        self.assertEqual((self.workspace / "count.txt").read_text(), "x")

    def test_context_reads_project_instructions_and_skill_assets(self):
        (self.workspace / "CLAUDE.md").write_text("REMOTE_INSTRUCTIONS")
        skill = self.workspace / ".claude/skills/example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("REMOTE_SKILL")
        (skill / "asset.bin").write_bytes(b"\x00\xff")
        result = runtime.request(self.state, "context")
        self.assertEqual(
            set(result["files"]),
            {
                "CLAUDE.md",
                ".claude/skills/example/SKILL.md",
                ".claude/skills/example/asset.bin",
            },
        )

    def test_context_sends_only_changed_working_tree_files(self):
        instructions = self.workspace / "CLAUDE.md"
        instructions.write_text("first")
        first = runtime.request(self.state, "context")
        known = {
            name: hashlib.sha256(base64.b64decode(value)).hexdigest()
            for name, value in first["files"].items()
        }
        unchanged = runtime.request(self.state, "context", {"known_files": known})
        self.assertEqual(unchanged["files"], {})
        self.assertIn("CLAUDE.md", unchanged["file_names"])
        self.assertEqual(unchanged["instructions"], first["instructions"])

        instructions.write_text("uncommitted change")
        changed = runtime.request(self.state, "context", {"known_files": known})
        self.assertEqual(
            base64.b64decode(changed["files"]["CLAUDE.md"]), b"uncommitted change"
        )
        self.assertIn("uncommitted change", changed["instructions"])
        instructions.unlink()
        removed = runtime.request(self.state, "context", {"known_files": known})
        self.assertNotIn("CLAUDE.md", removed["file_names"])
        self.assertEqual(removed["instructions"], "")

    def test_context_fs_lists_metadata_and_reads_bytes_on_demand(self):
        (self.workspace / "CLAUDE.md").write_text("root @docs/more.md @.claude")
        (self.workspace / "docs").mkdir()
        (self.workspace / "docs/more.md").write_text("imported @nested.md")
        (self.workspace / "docs/nested.md").write_text("nested import")
        (self.workspace / "backend").mkdir()
        (self.workspace / "backend/CLAUDE.md").write_text("nested instructions")
        skill = self.workspace / ".claude/skills/example"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("skill body")
        (skill / "support.bin").write_bytes(b"012345")
        (skill / "support-link").symlink_to("support.bin")
        (self.workspace / ".claude/settings.json").write_text("do not expose")
        (self.workspace / ".claude/settings.local.json").write_text("do not expose")

        tree = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertEqual(tree["unsupported_imports"], [])
        self.assertEqual(tree["unsupported_paths"], [])
        self.assertIn("CLAUDE.md", tree["entries"])
        self.assertIn("docs/more.md", tree["entries"])
        self.assertIn("docs/nested.md", tree["entries"])
        self.assertIn("backend/CLAUDE.md", tree["entries"])
        self.assertIn(".claude/skills/example/support.bin", tree["entries"])
        self.assertEqual(
            tree["entries"][".claude/skills/example/support-link"]["kind"],
            "symlink",
        )
        self.assertEqual(
            tree["entries"][".claude/skills/example/support-link"]["target"],
            "support.bin",
        )
        self.assertNotIn(".claude/settings.json", tree["entries"])
        self.assertNotIn(".claude/settings.local.json", tree["entries"])
        chunk = runtime.request(
            self.state,
            "context_fs",
            {
                "operation": "read",
                "path": ".claude/skills/example/support.bin",
                "offset": 2,
                "size": 3,
            },
        )
        self.assertEqual(base64.b64decode(chunk["data"]), b"234")

        (skill / "SKILL.md").write_text("changed skill body")
        changed = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertNotEqual(changed["generation"], tree["generation"])

    def test_context_fs_reports_imports_outside_project_boundary(self):
        absolute_project_import = str(self.workspace / "inside.md")
        (self.workspace / "inside.md").write_text("inside")
        (self.workspace / "CLAUDE.md").write_text(
            f"@/etc/hosts @../../outside.md @~/.claude/machine.md "
            f"@{absolute_project_import}"
        )
        tree = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertEqual(
            tree["unsupported_imports"],
            sorted(
                [
                    "../../outside.md",
                    "/etc/hosts",
                    absolute_project_import.split()[0],
                    str(Path.home() / ".claude/machine.md"),
                ]
            ),
        )

        outside = self.root / "outside-skill"
        outside.mkdir()
        (outside / "SKILL.md").write_text("outside")
        skills = self.workspace / ".claude/skills"
        skills.mkdir(parents=True)
        (skills / "outside").symlink_to(outside, target_is_directory=True)
        tree = runtime.request(self.state, "context_fs", {"operation": "tree"})
        self.assertEqual(tree["unsupported_paths"], [".claude/skills/outside"])
        self.assertNotIn(".claude/skills/outside/SKILL.md", tree["entries"])

    def test_context_sync_records_generation_without_copying_file_bodies(self):
        spec = importlib.util.spec_from_file_location(
            "central_client", RUNTIME.with_name("client.py")
        )
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)

        (self.workspace / "CLAUDE.md").write_text("project instructions")
        central = self.root / "central"
        config = self.root / "config"
        central.mkdir()
        config.mkdir()
        target = self.root / "target.json"
        target.write_text(
            json.dumps(
                {"central_workspace": str(central), "central_config": str(config)}
            )
        )

        def context_request(_client, method, params=None):
            return runtime.request(self.state, method, params or {})

        with patch.object(client.RemoteClient, "call", context_request):
            first = client.sync_context(target)
            self.assertTrue(first["changed"])
            self.assertEqual(list(central.iterdir()), [])
            tree_path = target.with_name("context-tree.json")
            first_tree = tree_path.read_bytes()
            first_tree_mtime = tree_path.stat().st_mtime_ns
            unchanged = client.sync_context(target)
            self.assertFalse(unchanged["changed"])
            self.assertEqual(tree_path.read_bytes(), first_tree)
            self.assertEqual(tree_path.stat().st_mtime_ns, first_tree_mtime)
            (self.workspace / "CLAUDE.md").write_text("updated instructions")
            changed = client.sync_context(target)
            self.assertTrue(changed["changed"])
            self.assertNotEqual(changed["generation"], first["generation"])

    def test_disconnected_executor_is_an_error(self):
        self.stop()
        with self.assertRaises(OSError):
            runtime.request(
                self.state,
                "invoke",
                {
                    "id": "after-disconnect",
                    "tool": "Write",
                    "args": {
                        "file_path": str(self.workspace / "forbidden.txt"),
                        "content": "wrong",
                    },
                },
            )
        self.assertFalse((self.workspace / "forbidden.txt").exists())

    def test_rc_can_background_a_running_foreground_command(self):
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(
                self.invoke,
                "Bash",
                {"command": "touch started; sleep 3; printf done"},
                "foreground",
            )
            deadline = time.monotonic() + 3
            while (
                not (self.workspace / "started").exists()
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            self.assertTrue((self.workspace / "started").exists())
            runtime.request(
                self.state,
                "control",
                {"subtype": "background_tasks", "tool_use_id": "foreground"},
            )
            result = pending.result(timeout=1)
            task = self.finished_task(result["backgroundTaskId"])
            self.assertEqual(task["task"]["output"], "done")

    def test_a_foreground_command_past_its_timeout_becomes_a_task_the_room_can_see(
        self,
    ):
        # The agent's timeout is enforced here, not by the serve process: at
        # the deadline the command is left running and handed back as a task.
        result = self.invoke(
            "Bash",
            {"command": "touch started; sleep 2; printf done", "timeout": 500},
            "foreground",
        )
        self.assertIn("backgroundTaskId", result)
        listed = runtime.request(
            self.state, "control", {"subtype": "background_tasks"}
        )["tasks"]
        self.assertIn(
            result["backgroundTaskId"],
            [task["task_id"] for task in listed if task["status"] == "running"],
        )
        task = self.invoke(
            "TaskOutput",
            {"task_id": result["backgroundTaskId"], "block": True, "timeout": 5000},
        )
        self.assertEqual(task["task"]["output"], "done")
        self.assertEqual(task["task"]["status"], "completed")

    def test_remote_command_hook_can_prevent_a_write(self):
        config = self.workspace / ".claude"
        config.mkdir()
        (config / "settings.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "printf denied >&2; exit 2",
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        response = runtime.request(
            self.state,
            "invoke",
            {
                "id": "denied-write",
                "tool": "Write",
                "args": {
                    "file_path": str(self.workspace / "forbidden.txt"),
                    "content": "wrong",
                },
            },
        )
        self.assertIn("denied", response["error"])
        self.assertFalse((self.workspace / "forbidden.txt").exists())

    def test_context_expands_remote_imports(self):
        (self.workspace / "CLAUDE.md").write_text("@instructions.md\n")
        (self.workspace / "instructions.md").write_text("IMPORTED_REMOTE_INSTRUCTIONS")
        self.assertIn(
            "IMPORTED_REMOTE_INSTRUCTIONS",
            runtime.request(self.state, "context")["instructions"],
        )

    def test_remote_shell_search(self):
        (self.workspace / "find-me.txt").write_text("REMOTE_SEARCH_MARKER\n")
        glob = self.invoke("Bash", {"command": "rg --files -g '*.txt'"})
        self.assertIn("find-me.txt", json.dumps(glob))
        grep = self.invoke(
            "Bash",
            {"command": "rg REMOTE_SEARCH_MARKER ."},
        )
        self.assertIn("REMOTE_SEARCH_MARKER", json.dumps(grep))

    def test_interrupting_transport_stops_remote_foreground_command(self):
        target = self.root / "client.json"
        target.write_text(
            json.dumps(
                {
                    "command": [sys.executable, str(RUNTIME)],
                    "state": str(self.state),
                    "workspace": str(self.workspace),
                }
            )
        )
        process = subprocess.Popen(
            [
                sys.executable,
                str(RUNTIME.with_name("client.py")),
                "transport",
                str(target),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

        self.addCleanup(cleanup)
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "invoke",
                        "arguments": {
                            "id": "interrupted",
                            "session_id": "test",
                            "tool": "Bash",
                            "args": {
                                "command": "touch started; sleep 2; touch forbidden.txt"
                            },
                        },
                    },
                }
            )
            + "\n"
        )
        process.stdin.flush()
        deadline = time.monotonic() + 3
        while not (self.workspace / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((self.workspace / "started").exists())
        process.terminate()
        self.assertEqual(process.wait(timeout=5), 143)
        time.sleep(2.1)
        self.assertFalse((self.workspace / "forbidden.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
