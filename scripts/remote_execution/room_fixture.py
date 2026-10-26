"""Ordinary project executor behind the production HTTP and connector transport."""

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.domain.agent import execution, place  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution import runtime  # noqa: E402
from app.domain.agent.harness.claude_code.remote_execution.launch import script  # noqa: E402

#: What the fixture's platform takes as the room's own: handed at
#: ``/credential`` on its platform socket for what a socket cannot carry, and
#: sent by the fixture's own callers the direct way (``private_terminal``).
ROOM_TOKEN = "room-fixture-token"
#: How a request the room made on its platform socket is marked when it reaches
#: the fixture's platform (`PlatformSocket`): the room's own, and carrying no
#: credential of its own.
SOCKET_HEADER = "X-Fixture-Platform-Socket"


class PlatformSocket:
    """The socket a room on a machine reaches its platform on, served here.

    A room installed on a machine is given ``CHEESE_PLATFORM_SOCKET`` and holds
    no credential (#4839, #4877): its ``cheese`` sends every platform call down
    this socket, and asks it at ``/credential`` for a one-minute credential for
    what a socket cannot carry. In production the connector serves it
    (``cli/internal/host/machine.go``). The connector this acceptance runs
    comes from the released owner image, whose revision predates the socket,
    and the platform it would relay to is the released owner's rather than this
    fixture's stand-in. So the fixture serves the socket itself, on the path the
    install named to the room, and answers as the platform does: the API under
    ``/api``, passed on to the fixture's own server and marked as the room's
    own, and the credential.

    Only what this room asks for is served: the API under ``/api`` and the
    credential. Nothing competes for the path — the connector of the owner
    image pinned in ``owner_fixture`` predates serving a platform socket
    (#4826, ten days after that revision), so the room would otherwise find
    this directory empty and fall back to the tokenless direct HTTP that the
    handlers refuse.
    """

    def __init__(self, path, token):
        self.path = Path(path)
        self.token = token
        #: The fixture server's own address, set once it answers
        #: (`RoomExecutor.set_api`), before any command runs in the room.
        self.platform = ""
        self.closed = threading.Event()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.path))
        # As the connector's own socket is (`cli/internal/host/machine.go`):
        # this one speaks as the room without asking for a credential.
        self.path.chmod(0o600)
        self.listener.listen(64)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        # A timeout rather than a blocking accept, so `close` ends this thread
        # instead of leaving it in a socket nobody reads again.
        self.listener.settimeout(1)
        while not self.closed.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:  # closed under the loop
                return
            threading.Thread(target=self.answer, args=(client,), daemon=True).start()

    def answer(self, client):
        with client:
            client.settimeout(60)
            try:
                request = self.read(client)
                if request is None:
                    return
                method, version, target, head, body = request
                route = target.partition("?")[0]
                if route == "/credential":
                    return self.reply(client, {"token": self.token})
                if not route.startswith("/api/") and route != "/whoami":
                    return self.reply(
                        client, {"error": {"message": "not the platform's"}}, 404
                    )
                self.pass_on(client, method, version, target, head, body)
            except OSError:
                # The room's caller went away mid-answer, as a sandbox's does
                # (`machine.go`: a stream that stops reading is its own).
                return

    @staticmethod
    def read(client):
        """The request one connection sent: its first line and head, and the
        body its ``Content-Length`` names — none of it left for the platform
        to wait on, since the platform closes the connection after answering."""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = client.recv(65536)
            if not chunk:
                return None
            data += chunk
        head, _, body = data.partition(b"\r\n\r\n")
        first = head.split(b"\r\n", 1)[0]
        method, target, version = (first.split(b" ") + [b"", b""])[:3]
        wanted = 0
        for line in head.split(b"\r\n")[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                wanted = int(value.strip() or 0)
        while len(body) < wanted:
            chunk = client.recv(65536)
            if not chunk:
                break
            body += chunk
        return (
            method.decode("latin-1"),
            version.decode("latin-1"),
            target.decode("latin-1"),
            head,
            body,
        )

    def pass_on(self, client, method, version, target, head, body):
        """Pass the room's request to the fixture's platform as the room's own.

        Nothing goes down the socket with a credential (the CLI strips one), so
        the request carries the mark of the socket it came in on instead, which
        is how the fixture's handlers read it as the room's. ``/api`` is the
        platform's own prefix on this socket; the fixture's server serves the
        same routes without it."""
        platform = urllib.parse.urlsplit(self.platform)
        if platform.hostname is None:
            return self.reply(
                client, {"error": {"message": "the platform is not here"}}, 503
            )
        try:
            upstream = socket.create_connection(
                (platform.hostname, platform.port), timeout=60
            )
        except OSError:
            return self.reply(
                client, {"error": {"message": "the platform is not here"}}, 502
            )
        lines = [f"{method} {target.removeprefix('/api')} {version}".encode()]
        lines += head.split(b"\r\n")[1:]
        lines.append(f"{SOCKET_HEADER}: 1".encode())
        with upstream:
            upstream.sendall(b"\r\n".join(lines) + b"\r\n\r\n" + body)
            upstream.shutdown(socket.SHUT_WR)
            while chunk := upstream.recv(65536):
                client.sendall(chunk)

    @staticmethod
    def reply(client, value, status=200):
        reason = {
            200: "OK",
            404: "Not Found",
            502: "Bad Gateway",
            503: "Service Unavailable",
        }[status]
        data = json.dumps(value).encode()
        client.sendall(
            (
                f"HTTP/1.1 {status} {reason}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + data
        )

    def close(self):
        self.closed.set()
        self.listener.close()
        self.path.unlink(missing_ok=True)


class RoomExecutor:
    def __init__(self, folder, claude):
        from owner_fixture import WireOwner

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
        pin = self.owner / ".cheese/claude/versions/2.1.299"
        pin.parent.mkdir(parents=True)
        pin.symlink_to(Path(claude).resolve())
        env = {
            "CHEESE_API": "http://127.0.0.1:1",
            "CHEESE_TOKEN": ROOM_TOKEN,
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
            input=script(project, resource, env, sandbox=False, platform_machine=False),
            env={**os.environ, "HOME": str(self.owner)},
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        info = json.loads(result.stdout)
        self.target = {
            "kind": "device",
            "device_id": "acceptance-machine",
            "resource_id": str(resource),
            "home": str(self.home),
            "workspace": str(self.work),
            "mcp_servers": info["mcp_servers"],
            # Recorded, not derived — a room's target carries where its executor
            # actually is, the same way a real placement does.
            "state": info["state"],
        }
        # Served from here on: the room's first `cheese` call in this
        # acceptance is a command the executor runs, and it reads the socket
        # once, when its own module loads.
        self.platform_socket = None
        if sys.platform != "win32":  # no socket, and a token, on Windows
            self.platform_socket = PlatformSocket(
                Path(runtime.platform_socket_directory(info["state"]))
                / "platform.sock",
                ROOM_TOKEN,
            )
        self.wire = None
        try:
            self.wire = WireOwner(claude)
            deadline = time.monotonic() + 30
            while True:
                try:
                    asyncio.run(execution.call(self.target, "ping", {}, hub=self.wire))
                    break
                except (ConnectionError, FileNotFoundError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            (self.home / ".cheese/executor-bootstrap.log").read_text()
                        )
                    time.sleep(0.1)
        except BaseException:
            self.close()
            raise

    def set_api(self, url):
        config = json.loads((self.state / "config.json").read_text())
        if self.platform_socket is not None:
            # Where the room's platform calls go: the fixture's own server, the
            # one `private_terminal` starts after this room is installed.
            self.platform_socket.platform = url
        asyncio.run(
            execution.call(
                self.target,
                "configure",
                {"env": {**config["env"], "CHEESE_API": url}},
                hub=self.wire,
            )
        )

    def handler(self, base):
        executor = self

        class Handler(base):
            def caller_is_the_room(self):
                """This request is the room's own — asserted.

                A room with a platform socket holds no credential (#4877), and
                a request on that socket carries none: what it carries instead
                is the mark `PlatformSocket` puts on it, and the socket it came
                in on is what says whose it is. Anything else is a caller of the
                fixture's own, and those carry the token."""
                if self.headers.get(SOCKET_HEADER) == "1":
                    assert not self.headers.get("X-Cheese-Token")
                    return
                assert self.headers.get("X-Cheese-Token") == ROOM_TOKEN

            def do_PUT(self):
                prefix = (
                    f"/projects/{executor.project}/git/tasks/{executor.task}/snapshots/"
                )
                if not self.path.startswith(prefix):
                    return super().do_PUT()
                # A bundle cannot go down the socket: it is sent straight to the
                # platform on the credential the socket handed for it, so it is
                # the one thing that must still arrive with a token.
                assert self.headers.get(SOCKET_HEADER) is None
                assert self.headers.get("X-Cheese-Token") == ROOM_TOKEN
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
                    self.caller_is_the_room()
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
                latest = (
                    f"/projects/{executor.project}/git/tasks/{executor.task}"
                    "/snapshots/latest"
                )
                if self.path == latest:
                    self.caller_is_the_room()
                    if not executor.snapshots:
                        # The platform's answer for a task never backed up.
                        body = b'{"error": {"message": "no backup"}}'
                        self.send_response(404)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return None
                    row = list(executor.snapshots.values())[-1]
                    return self.reply(
                        {
                            "data": {
                                key: row[key]
                                for key in ("id", "snapshot_sha", "head_sha", "digest")
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
                assert self.headers.get("X-Cheese-Token") == ROOM_TOKEN
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                result = asyncio.run(
                    execution.call(
                        executor.target,
                        payload["method"],
                        payload.get("params", {}),
                        hub=executor.wire,
                        timeout=payload.get("timeout", 660),
                    )
                )
                return self.reply(result)

        return Handler

    def close(self):
        try:
            subprocess.run(
                [sys.executable, runtime.__file__, "stop", "--state", str(self.state)],
                capture_output=True,
                check=True,
                timeout=30,
            )
        finally:
            # Not every path here got as far as making one: a room that failed
            # to install, or one on Windows.
            socket = getattr(self, "platform_socket", None)
            if socket is not None:
                socket.close()
            if self.wire is not None:
                self.wire.close()
