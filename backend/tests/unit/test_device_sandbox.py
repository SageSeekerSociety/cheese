"""A session's isolated environment on a machine a person enrolled (#2320 step
2), and the code outside it that reads what the session can write.

The tests that start a sandbox need Linux with bubblewrap and unprivileged
user namespaces, as CI's runner has; elsewhere they are skipped. The macOS
ones are in `test_device_sandbox_macos.py`. The rest holds anywhere.
"""

import base64
import contextlib
import datetime
import errno
import json
import os
import shutil
import signal
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent import environment_runner
from app.domain.agent import resource_cleanup as cleanup
from app.domain.agent.harness.claude_code.remote_execution import (
    bootstrap,
    confinement,
    runtime,
)
from app.domain.agent.harness.claude_code.remote_execution.launch import (
    payload_for,
    refused,
)
from app.domain.machine.caller import valid_proxy_proof
from tests.support import bubblewrap
from tests.tls_gateway import write_pki

RUNNER = Path(environment_runner.__file__)


def _bubblewrap_runs() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("bwrap") is None:
        return False
    probe = subprocess.run(
        ["bwrap", "--unshare-user", "--unshare-pid", "--ro-bind", "/", "/"]
        + ["--dev", "/dev", "--proc", "/proc", "true"],
        capture_output=True,
    )
    return probe.returncode == 0


sandboxed = pytest.mark.skipif(
    not _bubblewrap_runs(), reason="needs bubblewrap with user namespaces (Linux)"
)
linux = pytest.mark.skipif(sys.platform != "linux", reason="Linux only")


def _wait(condition, what, timeout=15):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, what
        time.sleep(0.05)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _payload(project, resource, *, sandbox, environment=None, site_forward=None):
    env = {"CHEESE_API": "http://127.0.0.1:1", "CHEESE_TOKEN": "test"}
    if environment is not None:
        env["CHEESE_ENVIRONMENT"] = json.dumps(environment)
    if site_forward is not None:
        # A private-control cloud machine (`machine_address.site_forward`).
        env["CHEESE_SITE_FORWARD"] = site_forward
    # A machine a person enrolled: the platform installs nothing there.
    return payload_for(project, resource, env, sandbox=sandbox, platform_machine=False)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """A person's home on their own machine, the connector running as them."""
    home = tmp_path / "owner"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    bubblewrap.place(home)
    monkeypatch.setattr(bootstrap, "binary", lambda *_: sys.executable)
    return home


def _refusal(payload, capsys) -> dict:
    with pytest.raises(SystemExit) as exited:
        bootstrap.configure(payload)
    assert exited.value.code == bootstrap.SANDBOX_UNAVAILABLE_EXIT
    return json.loads(capsys.readouterr().err)


def test_a_refusal_after_a_download_is_still_read_as_the_refusal():
    """An install that fetched the executor first has said how far the
    download got before it refuses; the owner is still told the refusal."""
    said = {"key": "sandboxNotPlaced", "params": {}}
    progress = json.dumps({"executor_download": {"have": 1, "total": 1}})

    refusal = refused(
        {
            "exit": bootstrap.SANDBOX_UNAVAILABLE_EXIT,
            "stderr": f"{progress}\n{progress}\n{json.dumps(said)}\n",
        }
    )

    assert refusal is not None and refusal.said == said


@linux
def test_an_isolated_room_this_machine_cannot_isolate_is_refused_and_nothing_runs(
    owner, tmp_path, monkeypatch, capsys
):
    """No bwrap where cheesehost places it, even with one on the system: the
    room is refused with what to do about it, nothing is installed with the
    owner's sudo, and nothing runs over the whole machine instead."""
    bubblewrap.placed(owner).unlink(missing_ok=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    asked = tmp_path / "sudo-was-run"
    (tools / "sudo").write_text(f"#!/bin/sh\ntouch {asked}\nexit 1\n")
    (tools / "sudo").chmod(0o755)
    system_ran = tmp_path / "system-bwrap-ran"
    (tools / "bwrap").write_text(f"#!/bin/sh\ntouch {system_ran}\n")
    (tools / "bwrap").chmod(0o755)
    monkeypatch.setenv("PATH", str(tools))
    project, resource = uuid.uuid4(), uuid.uuid4()

    said = _refusal(_payload(project, resource, sandbox=True), capsys)

    assert said["key"] == "sandboxNotPlaced"
    assert not system_ran.exists()
    assert not asked.exists()
    state = owner / ".cheese/home" / str(project) / str(resource) / ".cheese/executor"
    assert not Path(runtime.socket_path(state)).exists()
    assert not (owner / ".cheese/sandboxes").exists()


@linux
def test_a_machine_that_allows_no_user_namespaces_is_told_what_to_change(
    owner, tmp_path, monkeypatch, capsys
):
    """Bubblewrap makes the sandbox in a user namespace of its own; where the
    machine forbids that (Ubuntu's AppArmor restriction among others) the
    room is refused with bubblewrap's own words and what to change."""
    refusing = bubblewrap.placed(owner)
    refusing.parent.mkdir(parents=True, exist_ok=True)
    refusing.unlink(missing_ok=True)
    refusing.write_text(
        "#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n"
    )
    refusing.chmod(0o755)

    said = _refusal(_payload(uuid.uuid4(), uuid.uuid4(), sandbox=True), capsys)

    assert "setting up uid map: Permission denied" in said["params"]["detail"]
    assert said["key"] in {
        "sandboxAppArmor",
        "sandboxUsernsCloneOff",
        "sandboxUserNamespacesOff",
        "sandboxNoUserNamespaces",
    }
    assert not (owner / ".cheese/sandboxes").exists()


class Room:
    """One session's executor on this machine, started by the install the
    backend sends."""

    def __init__(
        self, owner: Path, capsys, *, sandbox=True, environment=None, site_forward=None
    ):
        self.project, self.resource = uuid.uuid4(), uuid.uuid4()
        self.home = owner / ".cheese/home" / str(self.project) / str(self.resource)
        self.state = self.home / ".cheese/executor"
        try:
            bootstrap.configure(
                _payload(
                    self.project,
                    self.resource,
                    sandbox=sandbox,
                    environment=environment,
                    site_forward=site_forward,
                )
            )
        except RuntimeError:
            log = self.home / ".cheese/executor-bootstrap.log"
            pytest.fail(log.read_text() if log.exists() else "no executor log")
        self.started = json.loads(capsys.readouterr().out)
        self.release = Path(self.started["release"])

    def bash(self, command: str) -> str:
        answer = runtime.request(
            self.state,
            "invoke",
            {"id": uuid.uuid4().hex, "tool": "Bash", "args": {"command": command}},
        )
        assert "error" not in answer, answer
        return answer["value"]["stdout"].strip()

    def stop(self):
        cleanup.stop_executor(self.home, str(self.resource))


@pytest.fixture
def rooms(owner, capsys):
    started: list[Room] = []

    def start(**options) -> Room:
        room = Room(owner, capsys, **options)
        started.append(room)
        return room

    yield start
    for room in started:
        room.stop()


@sandboxed
def test_a_session_on_a_persons_machine_sees_the_system_and_none_of_their_home(
    owner, rooms
):
    """The machine's own tools work; what the person keeps in their home —
    their keys, their tools under ~/.local/bin, ~/.cargo, ~/.nvm — is not
    there, and neither is anything mounted under /mnt or /media."""
    (owner / ".ssh").mkdir()
    (owner / ".ssh/id_ed25519").write_text("the owner's key")
    for tool in (".local/bin/mytool", ".cargo/bin/mytool", ".nvm/bin/mytool"):
        path = owner / tool
        path.parent.mkdir(parents=True)
        path.write_text("#!/bin/sh\necho ran\n")
        path.chmod(0o755)

    room = rooms()

    assert room.bash("git --version").startswith("git version")
    assert room.bash("python3 -c 'print(6 * 7)'") == "42"
    for hidden in (".ssh/id_ed25519", ".local/bin/mytool", ".cargo", ".nvm"):
        assert room.bash(f'ls "{owner / hidden}" >/dev/null 2>&1 || echo hidden') == (
            "hidden"
        ), hidden
    path = f"{owner}/.local/bin:{owner}/.cargo/bin:$PATH"
    assert room.bash(f'PATH="{path}"; command -v mytool || echo missing') == "missing"
    for mount in confinement.HIDDEN_MOUNTS:
        if Path(mount).is_dir():
            assert room.bash(f'ls -A "{mount}" | wc -l') == "0", mount
    # Its own home is there, and writable.
    assert room.bash('touch "$HOME/mine" && echo ok') == "ok"
    assert (room.home / "mine").exists()


@sandboxed
def test_a_room_runs_the_git_cheesehost_placed_and_nothing_else_it_placed(owner, rooms):
    """On a machine whose own git was missing or too old, cheesehost placed one
    under the owner's home (`cli/internal/devenv`): a room's turn needs it. The
    rest of what it placed there is the connector's, not the room's."""
    runtime = owner / ".cheese/runtime"
    for tool in ("git", "tmux"):
        path = runtime / tool / "bin" / tool
        path.parent.mkdir(parents=True)
        path.write_text(f"#!/bin/sh\necho placed {tool}\n")
        path.chmod(0o755)

    room = rooms()

    path = f"{runtime}/git/bin:{runtime}/tmux/bin:$PATH"
    assert room.bash(f'PATH="{path}"; git') == "placed git"
    assert room.bash(f'ls "{runtime}/tmux" >/dev/null 2>&1 || echo hidden') == "hidden"


@sandboxed
def test_the_teardown_ends_everything_the_sandbox_started(rooms):
    """A session's processes are its sandbox's descendants, whatever pid files
    it wrote: the teardown ends them all with the sandbox, reading only what
    the install recorded."""
    room = rooms()
    marker = f"sleep {31000 + os.getpid() % 1000}"
    room.bash(f"setsid nohup {marker} >/dev/null 2>&1 & echo started")

    def running() -> bool:
        listing = subprocess.run(["ps", "-eo", "args="], capture_output=True, text=True)
        return any(line.strip() == marker for line in listing.stdout.splitlines())

    _wait(running, "the session's process never started")
    sandbox = environment_runner.sandbox_process(room.home)
    assert sandbox is not None

    room.stop()

    _wait(lambda: not running(), "the session's process outlived its sandbox")
    assert environment_runner.sandbox_process(room.home) is None
    with pytest.raises(OSError):
        runtime.request(room.state, "ping")


RECORD_STATUS = """
import json, os, subprocess, sys, runpy
runner = runpy.run_path(sys.argv[1])
child = subprocess.Popen(["sleep", "300"], start_new_session=True)
runner["write_json"](
    __import__("pathlib").Path.home() / ".cheese-environment/status.json",
    {
        "state": "preparing",
        "pid": child.pid,
        "pid_namespace": runner["pid_namespace"](),
        "process_identity": runner["process_identity"](child.pid),
    },
)
print(child.pid)
"""


@sandboxed
def test_a_cancel_reaches_the_sessions_own_process_by_its_sandbox(rooms):
    """The cancel runs outside the sandbox and signals what the session's
    status names only when it is one of the sandbox's processes: here it is,
    so it ends."""
    room = rooms()
    room.bash('mkdir -p "$HOME/.cheese-environment"')
    program = room.home / "record.py"
    program.write_text(RECORD_STATUS)
    inside = room.bash(
        f'python3 "{program}" "{room.release / "cheese-environment.py"}"'
    )
    assert room.bash(f"kill -0 {inside} && echo alive") == "alive"

    cancelled = _run_runner(room.home, "cancel", room.release / "cheese-environment.py")

    assert cancelled.returncode == 0, cancelled.stderr
    _wait(
        lambda: (
            room.bash(f"kill -0 {inside} 2>/dev/null && echo alive || echo gone")
            == "gone"
        ),
        "the session's own process was not cancelled",
    )


def _sandboxed_home(owner: Path, release: Path) -> Path:
    """A room's home on a machine that recorded it as sandboxed, started from
    `release`."""
    project, resource = str(uuid.uuid4()), str(uuid.uuid4())
    home = owner / ".cheese/home" / project / resource
    (home / ".cheese").mkdir(parents=True)
    marker = owner / ".cheese/sandboxes" / project / resource
    marker.parent.mkdir(parents=True)
    marker.write_text(str(release))
    return home


def _run_runner(
    home: Path, action: str, runner: Path = RUNNER
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(runner), action],
        env={**os.environ, "HOME": str(home), "CHEESE_SANDBOXED": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("action", ["reset", "cancel"])
def test_a_status_naming_a_process_the_sandbox_did_not_start_signals_nothing(
    tmp_path, action
):
    """A sandboxed room writes its own environment status. One naming a
    process of the machine's that its sandbox did not start gets that process
    no signal from the reset or the cancel run outside the sandbox."""
    home = _sandboxed_home(tmp_path, tmp_path / "release")
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        directory = home / ".cheese-environment"
        directory.mkdir()
        environment_runner.write_json(
            directory / "status.json",
            {
                "state": "ready" if action == "reset" else "preparing",
                "pid": bystander.pid,
                "pid_namespace": environment_runner.pid_namespace(),
                "process_identity": environment_runner.process_identity(bystander.pid),
            },
        )
        # And a record naming it the sandbox, which the room cannot write but
        # which must not be enough on its own either: a sandbox's own first
        # process is not one of the room's.
        record = tmp_path / ".cheese/sandboxes" / home.parent.name / home.name
        Path(str(record) + ".process").write_text(
            json.dumps(
                {
                    "pid": bystander.pid,
                    "identity": environment_runner.process_identity(bystander.pid),
                }
            )
        )

        _run_runner(home, action)

        assert bystander.poll() is None
    finally:
        bystander.send_signal(signal.SIGKILL)
        bystander.wait()


def test_pruning_a_skill_removes_the_links_in_it_and_nothing_they_name(tmp_path):
    """A skill the room's record says was planted and is no longer shipped is
    removed with everything in it. What the room put there as links goes as
    links: the directories and files they name are the owner's and stay."""
    home = tmp_path / "home"
    skills = home / ".claude/skills"
    stale = skills / "stale"
    (stale / "nested").mkdir(parents=True)
    (stale / "nested/file").write_text("planted")
    owners = tmp_path / "owners"
    (owners / "documents").mkdir(parents=True)
    (owners / "documents/report").write_text("the owner's")
    (stale / "linked-directory").symlink_to(owners / "documents")
    (stale / "nested/linked-file").symlink_to(owners / "documents/report")
    (skills / "kept").mkdir()
    (skills / "linked-skill").symlink_to(owners)
    (skills / ".cheese-project-skills.json").write_text(
        json.dumps(["stale", "kept", "linked-skill"])
    )

    bootstrap.prune_project_skills(home, ["kept"], [])

    assert not stale.exists() and not stale.is_symlink()
    assert not (skills / "linked-skill").is_symlink()
    assert (skills / "kept").is_dir()
    assert (owners / "documents/report").read_text() == "the owner's"
    assert json.loads((skills / ".cheese-project-skills.json").read_text()) == ["kept"]


@sandboxed
def test_a_session_moved_from_a_sandbox_to_its_owners_machine_runs_outside_it(
    owner, rooms, capsys
):
    """The machine's owner moves a session from a sandbox to run directly on
    the machine: its next install replaces the sandboxed executor with one over
    the machine, and nothing out here goes on treating it as sandboxed."""
    room = rooms()
    assert environment_runner.sandbox_process(room.home) is not None
    assert room.bash(f'ls "{owner}/.cheese/sandboxes" 2>/dev/null || echo hidden') == (
        "hidden"
    )

    bootstrap.configure(_payload(room.project, room.resource, sandbox=False))
    capsys.readouterr()

    assert (
        room.bash(f'ls -d "{owner}/.cheese/sandboxes"') == f"{owner}/.cheese/sandboxes"
    )
    assert cleanup.sandbox_release(room.home) is None
    assert environment_runner.sandbox_process(room.home) is None


@sandboxed
def test_a_room_dials_its_platform_socket_and_cannot_put_its_own_in_its_place(
    rooms,
):
    """The connector serves a room's platform socket from outside its sandbox
    (`cli/internal/host/machine.go`); the room is shown that directory
    read-only. It reaches the socket, cannot unlink it or bind one of its own
    there, and does not see another room's."""
    room, other = rooms(), rooms()
    socket_path = Path(runtime.platform_socket_directory(room.state)) / "platform.sock"
    assert room.bash('echo "$CHEESE_PLATFORM_SOCKET"') == str(socket_path)

    server = socket.socket(socket.AF_UNIX)
    server.bind(str(socket_path))
    server.listen()

    def answer():
        conn, _ = server.accept()
        with conn:
            conn.sendall(b"outside heard " + conn.recv(64))

    threading.Thread(target=answer, daemon=True).start()
    try:
        dial = (
            "import os, socket; s = socket.socket(socket.AF_UNIX); "
            's.connect(os.environ["CHEESE_PLATFORM_SOCKET"]); '
            's.sendall(b"ping"); print(s.recv(64).decode())'
        )
        assert room.bash(f"python3 -c '{dial}'") == "outside heard ping"
        replace = (
            "import os, socket; p = os.environ['CHEESE_PLATFORM_SOCKET']\n"
            "try:\n    os.unlink(p)\nexcept OSError as e:\n    print(e.errno)\n"
            "try:\n    socket.socket(socket.AF_UNIX).bind(p + '.mine')\n"
            "except OSError as e:\n    print(e.errno)\n"
        )
        assert room.bash(f'python3 -c "{replace}"').split() == [
            str(errno.EROFS),
            str(errno.EROFS),
        ]
        assert socket_path.exists()
        assert (
            other.bash(f'ls "{socket_path}" >/dev/null 2>&1 || echo hidden') == "hidden"
        )
    finally:
        server.close()


def test_a_platform_directory_someone_else_put_there_is_refused(tmp_path):
    """The platform socket's directory has a fixed name in a shared /tmp; a
    link planted there would put the socket where its maker can replace it."""
    elsewhere = tmp_path / "theirs"
    elsewhere.mkdir()
    planted = tmp_path / "cheese-platform"
    planted.symlink_to(elsewhere)
    with pytest.raises(RuntimeError, match="not this user's own directory"):
        confinement.own_directory(planted)
    mine = confinement.own_directory(tmp_path / "mine")
    assert mine.is_dir() and (mine.stat().st_mode & 0o777) == 0o700


@linux
def test_a_room_on_a_whole_machine_is_given_its_platform_socket_too(rooms):
    """A room without a sandbox (a VM that is its own, or a machine its owner
    gave over) makes its model calls on the platform socket as well: the
    install makes the directory and names the socket."""
    room = rooms(sandbox=False)
    expected = Path(runtime.platform_socket_directory(room.state)) / "platform.sock"
    assert room.bash('echo "$CHEESE_PLATFORM_SOCKET"') == str(expected)
    made = expected.parent.lstat()
    assert expected.parent.is_dir() and made.st_uid == os.getuid()
    assert (made.st_mode & 0o777) == 0o700


def _holds_no_credential(room):
    assert room.bash('printf %s "${CHEESE_TOKEN-unset}"') == ""
    assert room.bash('printf %s "${CHEESE_PROXY_PROOF-unset}"') == "unset"
    assert room.bash('echo "$CHEESE_PLATFORM_SOCKET"')
    assert room.bash('echo "$HTTPS_PROXY"').startswith("http://127.0.0.1:")
    assert room.bash('echo "$SSL_CERT_FILE"').endswith("/ca-bundle.pem")
    assert room.bash('echo "$NODE_EXTRA_CA_CERTS"').endswith("/ca.pem")
    token_file = room.home / ".cheese" / "cheese-preview.token"
    assert not token_file.exists() and not token_file.is_symlink()


@sandboxed
def test_an_isolated_room_holds_no_credential(rooms):
    """Given its platform socket, a sandboxed room holds no credential of its
    own: not in its environment, not in the file its preview helper could
    read."""
    _holds_no_credential(rooms(sandbox=True))


def test_a_room_on_its_whole_machine_holds_no_credential(rooms):
    """The same on a whole machine, which has the platform socket too."""
    _holds_no_credential(rooms(sandbox=False))


SITE = "site.invalid"
REAL_TOKEN = "the-projects-own-forge-token"
_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _pkt(line: str) -> bytes:
    raw = line.encode()
    return f"{len(raw) + 4:04x}".encode() + raw


@pytest.fixture
def site_forge(tmp_path, monkeypatch):
    """The deployment's site on a machine that forwards it: a Forgejo under
    `/forge` answering TLS for the site's name on the forward's loopback port,
    with a certificate from a CA the machine trusts. It records the
    credential of every request and answers only the project's own."""
    ca, cert, key = write_pki(
        tmp_path, expires_in=datetime.timedelta(days=30), name=SITE
    )
    # What the machine trusts, so the proxy outside the room verifies the
    # site's certificate as it would the public one.
    monkeypatch.setenv("SSL_CERT_FILE", ca)
    seen: list[tuple[str, str]] = []
    expected = {
        "Basic " + base64.b64encode(f"o:{REAL_TOKEN}".encode()).decode(),
        f"token {REAL_TOKEN}",
    }

    class Forge(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path == "/api/live":
                if self.headers.get("Upgrade", "").lower() != "websocket":
                    self._send(400, b"missing upgrade")
                    return
                self.send_response(101)
                self.send_header("Connection", "Upgrade")
                self.send_header("Upgrade", "websocket")
                self.end_headers()
                self.wfile.flush()
                self.wfile.write(self.rfile.read(4))
                self.wfile.flush()
                self.close_connection = True
                return
            auth = self.headers.get("Authorization") or ""
            seen.append((self.path, auth))
            if auth not in expected:
                self._send(401, b"", WWW_Authenticate='Basic realm="forge"')
            elif self.path == "/forge/o/r.git/info/refs?service=git-upload-pack":
                body = (
                    _pkt("# service=git-upload-pack\n")
                    + b"0000"
                    + _pkt(f"{_SHA} refs/heads/main\0agent=fake\n")
                    + b"0000"
                )
                self._send(
                    200,
                    body,
                    Content_Type="application/x-git-upload-pack-advertisement",
                )
            elif self.path == "/forge/api/v1/repos/o/r":
                self._send(
                    200, b'{"full_name": "o/r"}', Content_Type="application/json"
                )
            else:
                self._send(404, b"")

        def _send(self, status, body, **headers):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Forge)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"{SITE}:{server.server_port}", seen
    server.shutdown()
    server.server_close()


@contextlib.contextmanager
def _platform_socket_of(room):
    """What the platform answers on a room's platform socket for a Forgejo
    project on the site: the room itself gets the placeholder, the proxy
    outside it, with its proof, the project's own token (#4896)."""

    class Platform(BaseHTTPRequestHandler):
        def do_GET(self):
            proof = self.headers.get("X-Cheese-Proxy-Proof") or ""
            data = {
                "kind": "forgejo",
                "project_id": str(room.project),
                "token": REAL_TOKEN
                if valid_proxy_proof(str(room.project), proof)
                else "placeholder",
                "username": "o",
                "repo": "o/r",
                "url": f"https://{SITE}/forge/o/r.git",
                "api_url": f"https://{SITE}/forge/api/v1",
            }
            raw = json.dumps({"data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            request, _ = super().get_request()
            return request, ("socket", 0)

    path = Path(runtime.platform_socket_directory(room.state)) / "platform.sock"
    server = Server(str(path), Platform)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        path.unlink(missing_ok=True)


def test_git_in_a_room_reaches_a_forge_on_the_forwarded_site_with_its_credential(
    site_forge, rooms
):
    """On a machine that forwards the site, git in a room reaches the
    project's forge on the site's name with the project's credential, not the
    placeholder the room holds."""
    forward, seen = site_forge
    room = rooms(sandbox=False, site_forward=forward)
    # Git's credential helper hands over what the room was given.
    helper = "!f() { echo username=o; echo password=placeholder; }; f"
    with _platform_socket_of(room):
        said = room.bash(
            f"GIT_TERMINAL_PROMPT=0 git -c credential.helper= "
            f"-c 'credential.helper={helper}' "
            f"ls-remote https://{SITE}/forge/o/r.git 2>&1; echo exit=$?"
        )
    assert said.endswith("exit=0"), said
    assert f"{_SHA}\trefs/heads/main" in said
    assert not any("placeholder" in auth for _, auth in seen)


def test_fj_in_a_room_reaches_a_forge_on_the_forwarded_site_with_its_credential(
    site_forge, rooms
):
    """The same for fj: what it sends the project's forge on the site carries
    the project's credential, not the placeholder."""
    forward, seen = site_forge
    room = rooms(sandbox=False, site_forward=forward)
    # The native fj the launcher runs, already on the machine: it calls the
    # host it is given with the token its configuration holds, as fj does.
    native = room.home / ".cheese/native/fj-0.6.0-cheese.2/fj"
    native.parent.mkdir(parents=True)
    native.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, urllib.request\n"
        "from pathlib import Path\n"
        "host = sys.argv[sys.argv.index('--host') + 1]\n"
        "keys = Path(os.environ['XDG_DATA_HOME']) / 'forgejo-cli/keys.json'\n"
        "[entry] = json.loads(keys.read_text())['hosts'].values()\n"
        "request = urllib.request.Request(host + 'api/v1/repos/o/r', "
        "headers={'Authorization': 'token ' + entry['token']})\n"
        "with urllib.request.urlopen(request, timeout=30) as response:\n"
        "    print(json.load(response)['full_name'])\n"
    )
    native.chmod(0o755)
    with _platform_socket_of(room):
        said = room.bash("fj repo view 2>&1; echo exit=$?")
    assert said.endswith("o/r\nexit=0"), said
    assert not any("placeholder" in auth for _, auth in seen)


def test_a_room_keeps_a_live_connection_to_the_site_beside_its_forge(site_forge, rooms):
    """An upgraded site connection still exchanges bytes when the site and
    forge share a hostname and use the credential proxy."""
    forward, _ = site_forge
    room = rooms(sandbox=False, site_forward=forward)
    check = room.home / "site-live.py"
    check.write_text(
        "import os, socket, ssl, urllib.parse\n"
        "proxy = urllib.parse.urlsplit(os.environ['HTTPS_PROXY'])\n"
        "sock = socket.create_connection((proxy.hostname, proxy.port), timeout=5)\n"
        f"sock.sendall(b'CONNECT {SITE}:443 HTTP/1.1\\r\\nHost: {SITE}\\r\\n\\r\\n')\n"
        "def headers(sock):\n"
        "    data = b''\n"
        "    while not data.endswith(b'\\r\\n\\r\\n'):\n"
        "        part = sock.recv(1)\n"
        "        assert part, data\n"
        "        data += part\n"
        "    return data\n"
        "assert b' 200 ' in headers(sock)\n"
        f"sock = ssl.create_default_context().wrap_socket(sock, server_hostname='{SITE}')\n"
        f"sock.sendall(b'GET /api/live HTTP/1.1\\r\\nHost: {SITE}\\r\\nConnection: Upgrade\\r\\nUpgrade: websocket\\r\\n\\r\\n')\n"
        "reply = headers(sock)\n"
        "assert b' 101 ' in reply, reply\n"
        "sock.sendall(b'ping')\n"
        "assert sock.recv(4) == b'ping'\n"
        "sock.close()\n"
        "print('live connection exchanged ping')\n"
    )
    with _platform_socket_of(room):
        said = room.bash(f"{sys.executable} {check} 2>&1; echo exit=$?")
    assert said.endswith("live connection exchanged ping\nexit=0"), said
