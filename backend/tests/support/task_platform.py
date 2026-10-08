"""A task's platform and forge, for the `cheese` CLI on a machine under test.

The forge is a bare repository on disk. The platform is an HTTP server that
answers the routes the CLI calls for a task — the task itself, its snapshots,
the room's messages — over a real socket, so the CLI is exercised as it runs on
a machine and not through stand-ins for its own functions.
"""

import hashlib
import importlib.util
import json
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

CLI = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def load_cli(platform: "TaskPlatform"):
    """The CLI as a module, talking to `platform` with a session's credential."""
    loader = SourceFileLoader(f"cheese_cli_{uuid.uuid4().hex}", str(CLI))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    cli.API, cli.PROJECT, cli.TOPIC, cli.TOKEN = (
        platform.url,
        platform.project,
        platform.room,
        "session-token",
    )
    return cli


class TaskPlatform:
    def __init__(self, root: Path):
        self.project = str(uuid.uuid4())
        self.room = str(uuid.uuid4())
        self.task_id = str(uuid.uuid4())
        self.branch = "task/" + uuid.UUID(self.task_id).hex[:8]
        self.forge = root / "forge.git"
        seed = root / "seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        git(seed, "config", "user.name", "Someone")
        git(seed, "config", "user.email", "someone@users.invalid")
        (seed / "report.txt").write_text("base\n")
        (seed / "notes.txt").write_text("notes\n")
        git(seed, "add", ".")
        git(seed, "commit", "-q", "-m", "Base")
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(seed), str(self.forge)], check=True
        )
        self.snapshots: list[dict] = []
        self.room_messages: list[str] = []
        #: The next this many requests for a snapshot's contents fail.
        self.downloads_failing = 0
        platform = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def answer(self, status: int, body: bytes, kind="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def data(self, value: dict, status: int = 200):
                self.answer(status, json.dumps({"data": value}).encode())

            def read_body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_POST(self):
                body = self.read_body()
                if self.path == f"/topics/{platform.room}/messages":
                    platform.room_messages.append(json.loads(body)["content"])
                    return self.data({})
                return self.do_GET()

            def do_GET(self):
                task = f"/projects/{platform.project}/git/tasks/{platform.task_id}"
                if self.path == task:
                    return self.data(platform.task())
                if self.path == f"{task}/snapshots/latest":
                    if not platform.snapshots:
                        return self.answer(404, b'{"error": {"message": "none"}}')
                    row = platform.snapshots[-1]
                    return self.data({k: v for k, v in row.items() if k != "bundle"})
                if self.path.startswith(f"{task}/snapshots/"):
                    if platform.downloads_failing:
                        platform.downloads_failing -= 1
                        return self.answer(503, b'{"error": {"message": "busy"}}')
                    wanted = self.path.rsplit("/", 1)[1]
                    row = next(r for r in platform.snapshots if r["id"] == wanted)
                    return self.answer(200, row["bundle"], "application/x-git-bundle")
                return self.answer(404, b"{}")

            def do_PUT(self):
                task = f"/projects/{platform.project}/git/tasks/{platform.task_id}"
                assert self.path.startswith(f"{task}/snapshots/")
                bundle = self.read_body()
                digest = hashlib.sha256(bundle).hexdigest()
                assert digest == self.headers["X-Content-SHA256"]
                row = {
                    "id": str(uuid.uuid4()),
                    "snapshot_sha": self.path.rsplit("/", 1)[1],
                    "head_sha": self.headers["X-Cheese-Head"],
                    "digest": digest,
                    "created_at": datetime.now(UTC).isoformat(),
                    "bundle": bundle,
                }
                platform.snapshots.append(row)
                return self.data({k: v for k, v in row.items() if k != "bundle"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def task(self) -> dict:
        return {
            "task_id": self.task_id,
            "room_id": self.room,
            "branch": self.branch,
            "base": "main",
            "closed": False,
            "remote": str(self.forge),
            "landed": None,
            "coauthors": [],
        }

    def branch_head(self) -> str | None:
        found = git(self.forge, "for-each-ref", "--format=%(objectname)", self.ref)
        return found or None

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.branch}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
