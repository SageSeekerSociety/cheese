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

RUNTIME = (
    Path(__file__).resolve().parents[2]
    / "app/domain/agent/harness/claude_code/remote_execution/runtime.py"
)
spec = importlib.util.spec_from_file_location("execution_runtime", RUNTIME)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def test_background_command_keeps_task_id_when_it_finishes_before_response(
    tmp_path, monkeypatch
):
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.json").write_text(
        json.dumps({"workspace": str(tmp_path), "env": {}})
    )
    executor = runtime.Executor(state)
    original_start = threading.Thread.start

    def finish_before_return(thread):
        original_start(thread)
        thread.join(timeout=5)
        assert not thread.is_alive()

    monkeypatch.setattr(threading.Thread, "start", finish_before_return)
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
            output = executor.output(task_id)
            assert output["stdout"] == "completed"
            assert output["exit_code"] == code
            assert output["status"] == ("completed" if code == 0 else "failed")
    finally:
        executor.close()


def test_executor_bootstrap_starts_in_room_without_a_git_checkout(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-executor")
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".claude/executor"
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
            [str(home / ".claude/cheese"), "--help"],
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
        (home / ".claude/cheese").write_text(
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


def test_running_executor_prepares_updated_room_without_restart(
    tmp_path, monkeypatch, capsys
):
    from app.domain.agent.harness.claude_code.remote_execution import bootstrap
    from app.domain.agent.harness.claude_code.remote_execution.launch import script

    monkeypatch.setenv("HOME", str(tmp_path))
    binary = tmp_path / ".cheese/claude/versions" / bootstrap.VERSION
    binary.parent.mkdir(parents=True)
    version_calls = tmp_path / "version-calls"
    binary.write_text(
        f"#!/bin/sh\necho checked >> '{version_calls}'\necho '{bootstrap.VERSION}'\n"
    )
    binary.chmod(0o700)
    project, resource = uuid.uuid4(), uuid.uuid4()
    home = tmp_path / ".cheese/home" / str(project) / str(resource)
    state = home / ".claude/executor"
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
        (home / ".claude/cheese-hook").write_text("locally edited helper")
        changed = runtime.request(state, "ping")
        repair = payload_for(project, resource, payload["env"], changed["files"])
        assert set(repair["files"]) == {"cheese", "cheese-hook"}
        payload["env"]["CHEESE_TOKEN"] = "refreshed"
        payload["files"]["cheese-hook"] = base64.b64encode(b"updated hook").decode()
        ready = runtime.request(state, "prepare", payload)
        assert ready["pid"] == original["pid"]
        assert ready["workspace"] == str(home / "room")
        assert (home / ".claude/cheese-preview.token").read_text() == "refreshed"
        assert (home / ".claude/cheese-hook").read_text() == "updated hook"
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
            assert (home / ".claude/preload-calls").read_text() == "loaded\n"
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
        assert (home / ".claude/preload-calls").read_text() == "loaded\nloaded\n"
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
        self.assertEqual(
            result["stdout"], str(self.workspace / "sub dir") + "\nremote-environment\n"
        )
        self.assertEqual(result["stderr"], "failure")
        self.assertEqual(result["returnCodeInterpretation"], "Exit code 7")

    def test_request_replay_does_not_repeat_write(self):
        args = {"command": "printf x >> count.txt"}
        original = self.invoke("Bash", args, key="same-request")
        self.assertEqual(self.invoke("Bash", args, key="same-request"), original)
        self.assertEqual((self.workspace / "count.txt").read_text(), "x")
        with self.assertRaisesRegex(RuntimeError, "different input"):
            self.invoke(
                "Bash", {"command": "printf y >> count.txt"}, key="same-request"
            )

    def test_reconnect_retains_background_task(self):
        task = self.invoke(
            "Bash",
            {"command": "sleep 0.2; printf background-done", "run_in_background": True},
        )
        previous_pid = self.pid
        self.start()
        self.assertEqual(self.pid, previous_pid)
        result = self.invoke(
            "TaskOutput",
            {"task_id": task["backgroundTaskId"], "block": True, "timeout": 5000},
        )
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

    def test_context_sync_preserves_unchanged_files_and_repairs_local_changes(self):
        from app.domain.agent.harness.claude_code.remote_execution import client

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
        responses = []

        def context_request(_client, method, params=None):
            result = runtime.request(self.state, method, params or {})
            responses.append(result)
            return result

        with patch.object(client.RemoteClient, "call", context_request):
            client.sync_context(target)
            copied = central / "CLAUDE.md"
            before = copied.stat()
            client.sync_context(target)
            self.assertEqual(responses[-1]["files"], {})
            self.assertEqual(copied.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(copied.stat().st_ino, before.st_ino)
            copied.write_text("local corruption")
            client.sync_context(target)
            self.assertEqual(copied.read_text(), "project instructions")
            (self.workspace / "CLAUDE.md").unlink()
            client.sync_context(target)
            self.assertFalse(copied.exists())
            self.assertEqual((config / "CLAUDE.md").read_text(), "")

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
            task = self.invoke(
                "TaskOutput",
                {"task_id": result["backgroundTaskId"], "block": True, "timeout": 5000},
            )
            self.assertEqual(task["task"]["output"], "done")

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
