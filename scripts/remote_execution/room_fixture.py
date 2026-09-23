"""Ordinary project executor behind the production HTTP and connector transport."""

import asyncio
import hashlib
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

from app.domain.agent import execution, place  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution import runtime  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution.launch import script  # noqa: E402


class RoomExecutor:
    def __init__(self, folder, claude):
        project, resource = uuid.uuid4(), uuid.uuid4()
        self.project = str(project)
        self.task = str(uuid.uuid4())
        self.owner = folder / "execution-host"
        self.snapshots = {}
        self.home = self.owner / ".cheese/home" / str(project) / str(resource)
        self.work = self.home / place.CHECKOUT_DIR
        self.state = self.home / ".cheese/executor"
        repository = folder / "task-origin"
        self.remote = str(repository)
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
        # A real room's checkout is a git checkout — `resource_cleanup` reads
        # its working-tree status before retiring it, and the whole of 结论 49
        # is a claim about what that status says. A plain directory here makes
        # the claim unaskable, so the fixture commits the project's own files
        # and leaves the tree clean: anything a turn adds after this is either
        # the agent's work or something the platform had no business writing.
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
                "Project files the room starts from",
            ],
        ):
            subprocess.run(["git", "-C", str(original), *arguments], check=True)
        pin = self.owner / ".cheese/claude/versions/2.1.277"
        pin.parent.mkdir(parents=True)
        pin.symlink_to(Path(claude).resolve())
        env = {
            "CHEESE_API": "http://127.0.0.1:1",
            "CHEESE_TOKEN": "room-fixture-token",
            "CHEESE_PROJECT": str(project),
            "CHEESE_TOPIC": str(resource),
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
            def do_PUT(self):
                prefix = (
                    f"/projects/{executor.project}/git/tasks/{executor.task}/snapshots/"
                )
                if not self.path.startswith(prefix):
                    return super().do_PUT()
                assert self.headers.get("X-Cheese-Token") == "room-fixture-token"
                assert self.headers.get("Content-Type") == "application/x-git-bundle"
                content = self.rfile.read(int(self.headers["Content-Length"]))
                digest = hashlib.sha256(content).hexdigest()
                assert digest == self.headers["X-Content-SHA256"]
                assert content.startswith((b"# v2 git bundle\n", b"# v3 git bundle\n"))
                snapshot_sha = self.path.removeprefix(prefix)
                assert len(snapshot_sha) == 40 and all(
                    c in "0123456789abcdef" for c in snapshot_sha
                )
                head_sha = self.headers["X-Cheese-Head"]
                assert len(head_sha) == 40 and all(
                    c in "0123456789abcdef" for c in head_sha
                )
                if digest not in executor.snapshots:
                    directory = executor.owner / "snapshots"
                    directory.mkdir(exist_ok=True)
                    bundle = directory / f"{digest}.bundle"
                    bundle.write_bytes(content)
                    executor.snapshots[digest] = {
                        "id": str(uuid.uuid4()),
                        "snapshot_sha": snapshot_sha,
                        "head_sha": head_sha,
                        "digest": digest,
                        "bundle": str(bundle),
                    }
                row = executor.snapshots[digest]
                return self.reply(
                    {
                        "data": {
                            key: row[key] for key in ("id", "snapshot_sha", "digest")
                        }
                    }
                )

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
                                "remote": executor.remote,
                                "coauthors": [],
                            }
                        }
                    )
                return super().do_GET()

            def do_POST(self):
                if (
                    self.path
                    == f"/projects/{executor.project}/git/tasks/{executor.task}"
                ):
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    return self.do_GET()
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
                        timeout=payload.get("timeout", 660),
                    )
                )
                return self.reply(result)

        return Handler

    async def call_executor(
        self, device, state, method, params, *, trace_id=None, timeout=660
    ):
        assert device == "executor"
        assert Path(state) == self.state
        async with asyncio.timeout(timeout):
            return await asyncio.to_thread(runtime.request, self.state, method, params)

    def close(self):
        subprocess.run(
            [sys.executable, runtime.__file__, "stop", "--state", str(self.state)],
            capture_output=True,
            check=True,
            timeout=30,
        )
