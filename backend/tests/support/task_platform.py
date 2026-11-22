"""A task's platform and forge, for the `cheese` CLI on a machine under test.

The forge is a bare repository on disk. The platform is an HTTP server that
answers the routes the CLI calls for a task — the task itself, the room's
messages, and the snapshot of its uncommitted work the platform keeps for a
repository that is not its own — over a real socket, so the CLI is exercised
as it runs on a machine and not through stand-ins for its own functions. The
snapshot is read the way the backend reads it (`kept_work`). Any other write
the CLI still sends is recorded and refused: it keeps its work by pushing
commits, so a test that finds one here is seeing a regression.
"""

import importlib.util
import json
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path
from urllib.parse import parse_qs

from app.domain.repository import kept_work

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
    def __init__(self, root: Path, *, forge_kind: str | None = None):
        #: The kind of forge the task's project is bound to, as the platform
        #: says it (`forge_kind`); the platform's own Forgejo is `forgejo`.
        self.forge_kind = forge_kind
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
        #: Every write the CLI sent, which is a regression if it is not empty.
        self.writes: list[dict] = []
        self.room_messages: list[str] = []
        #: The task's snapshot the platform keeps (`kept_work.KeptBundle`):
        #: the bundle, the commit it carries and the one it was cut from.
        self.kept: dict | None = None
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
                if self.path == "/":
                    # GitHub's site, as the CLI probes it to tell it is
                    # reachable (`_configure_git_transport`).
                    return self.answer(200, b"", "text/html")
                if self.path == task:
                    return self.data(platform.task())
                if self.path == task + "/kept":
                    if platform.kept is None:
                        self.send_response(204)
                        self.end_headers()
                        return None
                    return self.answer(
                        200, platform.kept["bundle"], "application/octet-stream"
                    )
                return self.answer(404, b"{}")

            def do_PUT(self):
                body = self.read_body()
                path, _, query = self.path.partition("?")
                if path == platform.kept_path:
                    try:
                        commit = kept_work.snapshot_in(
                            body, uuid.UUID(platform.task_id)
                        )
                    except kept_work.NotABundle:
                        return self.answer(400, b"{}")
                    platform.kept = {
                        "bundle": body,
                        "commit": commit,
                        "cut_from": parse_qs(query)["cut_from"][0],
                    }
                    return self.data({})
                platform.writes.append({"path": self.path, "body": body})
                return self.answer(404, b"{}")

            def do_DELETE(self):
                if self.path == platform.kept_path:
                    platform.kept = None
                    return self.data({})
                platform.writes.append({"path": self.path, "body": b""})
                return self.answer(404, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def kept_path(self) -> str:
        return f"/projects/{self.project}/git/tasks/{self.task_id}/kept"

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
            **(
                {
                    "forge_kind": self.forge_kind,
                    "forge_repo": "owner/repo",
                    "remote": self.github_remote
                    if self.forge_kind == "github_app"
                    else self.site_remote().as_uri(),
                }
                if self.forge_kind
                else {}
            ),
        }

    def site_remote(self) -> Path:
        """The forge as a bound forge's site has it, `<site>/<owner>/<repo>.git`,
        with the version page the CLI probes to tell it is reachable
        (`_configure_git_transport`)."""
        site = self.forge.parent / "site"
        remote = site / "owner" / "repo.git"
        if not remote.exists():
            (site / "api" / "v1").mkdir(parents=True)
            (site / "api" / "v1" / "version").write_text("{}")
            remote.parent.mkdir()
            remote.symlink_to(self.forge)
        return remote

    @property
    def github_remote(self) -> str:
        """The repository as GitHub names it, `<site>/<owner>/<repo>.git`, on
        this platform's own address; git reaches the bare repository under
        that name through `wire_home`."""
        return f"{self.url}/owner/repo.git"

    def wire_home(self, home: Path) -> None:
        """Point the git of a machine whose home is `home` from the GitHub
        name of the repository to the bare one."""
        if self.forge_kind == "github_app":
            git(
                home,
                "config",
                "--file",
                str(home / ".gitconfig"),
                f"url.{self.forge}.insteadOf",
                self.github_remote,
            )

    def branch_head(self) -> str | None:
        found = git(self.forge, "for-each-ref", "--format=%(objectname)", self.ref)
        return found or None

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.branch}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
