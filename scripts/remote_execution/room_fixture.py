"""Ordinary project executor behind the production HTTP and connector transport."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.agent import execution  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution import runtime  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution.launch import script  # noqa: E402


class RoomExecutor:
    def __init__(self, folder, claude):
        project, resource = uuid.uuid4(), uuid.uuid4()
        self.project = str(project)
        self.task = str(uuid.uuid4())
        self.owner = folder / "execution-host"
        self.home = self.owner / ".cheese/home" / str(project) / str(resource)
        self.work = self.home / "room"
        self.state = self.home / ".cheese/executor"
        repository = folder / "task-origin"
        repository.mkdir()
        (repository / "backend").mkdir()
        (repository / "backend/dependency-version").write_text("1")
        for arguments in (
            ["init", "-q", "-b", "main"],
            ["add", "."],
            [
                "-c",
                "user.name=fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "-qm",
                "Task dependency fixture",
            ],
            ["branch", f"task/{self.task}"],
        ):
            subprocess.run(["git", "-C", str(repository), *arguments], check=True)
        original = self.work
        original.mkdir(parents=True)
        (original / "CLAUDE.md").write_text(
            "Project commands execute beside project.txt.\n"
        )
        (original / "project.txt").write_text("retained project\n")
        shutil.copyfile(
            Path(__file__).with_name("custom_mcp.py"), original / "custom_mcp.py"
        )
        (original / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "custom": {
                            "command": sys.executable,
                            "args": ["custom_mcp.py"],
                            "env": {"MCP_TEST_MARKER": "room-executor"},
                        }
                    }
                }
            )
        )
        pin = self.owner / ".cheese/claude/versions/2.1.277"
        pin.parent.mkdir(parents=True)
        pin.symlink_to(Path(claude).resolve())
        env = {
            "CHEESE_API": "http://127.0.0.1:1",
            "CHEESE_TOKEN": "room-fixture-token",
            "CHEESE_PROJECT": str(project),
            "CHEESE_TOPIC": str(resource),
            "CHEESE_GIT_REMOTE": str(repository),
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.test",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.test",
            "CHEESE_ENVIRONMENT": json.dumps(
                {
                    "revision": "room-fixture",
                    "variables": {"PROJECT_VALUE": "executor-env"},
                    "setup_script": 'printf "$PROJECT_VALUE" > setup-result',
                    "startup_script": 'cd backend\nprintf "$PROJECT_VALUE" > startup-result',
                }
            ),
        }
        result = subprocess.run(
            [sys.executable, "-"],
            input=script(project, resource, env),
            env={**os.environ, "HOME": str(self.owner)},
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        info = json.loads(result.stdout)
        self.target = {
            "kind": "device",
            "device_id": "executor",
            "resource_id": str(resource),
            "home": str(self.home),
            "workspace": str(self.work),
            "mcp_servers": info["mcp_servers"],
            # Recorded, not derived — a room's target carries where its executor
            # actually is, the same way a real placement does.
            "state": info["state"],
        }
        deadline = time.monotonic() + 30
        while True:
            try:
                runtime.request(self.state, "ping")
                break
            except (ConnectionError, FileNotFoundError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        (self.home / ".cheese/executor-bootstrap.log").read_text()
                    )
                time.sleep(0.1)

    def set_api(self, url):
        config = json.loads((self.state / "config.json").read_text())
        runtime.request(
            self.state, "configure", {"env": {**config["env"], "CHEESE_API": url}}
        )

    def handler(self, base):
        executor = self

        class Handler(base):
            def do_GET(self):
                if (
                    self.path
                    == f"/projects/{executor.project}/git/tasks/{executor.task}"
                ):
                    assert self.headers.get("X-Cheese-Token") == "room-fixture-token"
                    return self.reply(
                        {
                            "data": {
                                "task_id": executor.task,
                                "room_id": executor.target["resource_id"],
                                "branch": f"task/{executor.task}",
                                "base": "main",
                                "closed": False,
                            }
                        }
                    )
                return super().do_GET()

            def do_POST(self):
                if self.path != "/execution":
                    return super().do_POST()
                assert self.headers.get("X-Cheese-Token") == "room-fixture-token"
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                result = asyncio.run(
                    execution.call(
                        executor.target,
                        payload["method"],
                        payload.get("params", {}),
                        hub=executor,
                    )
                )
                return self.reply(result)

        return Handler

    async def call_executor(self, device, state, method, params, *, trace_id=None):
        assert device == "executor"
        assert Path(state) == self.state
        return await asyncio.to_thread(runtime.request, self.state, method, params)

    def close(self):
        subprocess.run(
            [sys.executable, runtime.__file__, "stop", "--state", str(self.state)],
            capture_output=True,
            check=True,
            timeout=30,
        )
