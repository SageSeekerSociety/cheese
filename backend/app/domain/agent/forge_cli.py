#!/usr/bin/env python3
"""Run the native forge CLI with credentials minted for this invocation only.

Shipped as both gh and fj; standard library only, like the executor bootstrap.
"""

import contextlib
import hashlib
import http.client
import json
import os
import platform
import runpy
import shutil
import socket
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if sys.platform != "win32":
    import fcntl


def lock(file):
    if sys.platform == "win32":
        # Shipped as remote-execution/bin/gh; the Windows primitives are one up.
        portable = Path(__file__).resolve().parents[1] / "portable.py"
        runpy.run_path(str(portable))["lock"](file)
    else:
        fcntl.flock(file, fcntl.LOCK_EX)


def native_binary(name):
    wrapper = Path(__file__).resolve()
    with wrapper.open("rb") as stream:
        signature = stream.read(128)
    # The file the distribution unpacks to, and the name a PATH search finds.
    executable = name + (".exe" if sys.platform == "win32" else "")
    # fj must use the patched distribution, including when an old fj is on PATH.
    for directory in os.get_exec_path() if name == "gh" else ():
        candidate = Path(directory) / executable
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open("rb") as stream:
                if stream.read(128) != signature:
                    return str(candidate)
    if sys.platform not in ("linux", "darwin", "win32"):
        raise RuntimeError(f"Native {name} is not installed on this machine")
    machine = platform.machine()
    arch = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "x64",
        "AMD64": "x64",
    }.get(machine)
    if arch is None:
        raise RuntimeError(f"No {name} distribution for {machine}")
    version = {"fj": "0.6.0-cheese.2", "gh": "2.62.0"}[name]
    directory = Path.home() / f".cheese/native/{name}-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / executable
    with (directory / "install.lock").open("a") as install_lock:
        lock(install_lock)
        if target.is_file():
            return str(target)
        system = "windows" if sys.platform == "win32" else sys.platform
        url = (
            os.environ["CHEESE_API"].rstrip("/")
            + f"/connector/toolchain/{name}/{system}-{arch}/artifact"
        )
        with tempfile.TemporaryFile() as archive:
            with urllib.request.urlopen(url, timeout=120) as response:
                digest = hashlib.sha256()
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    archive.write(chunk)
                if digest.hexdigest() != response.headers.get("X-Checksum-SHA256"):
                    raise RuntimeError(f"{name} download checksum mismatch")
            archive.seek(0)
            partial = directory / f"{name}.part"
            with contextlib.ExitStack() as unpack:
                if zipfile.is_zipfile(archive):
                    package = unpack.enter_context(zipfile.ZipFile(archive))
                    members = [
                        item
                        for item in package.infolist()
                        if not item.is_dir() and Path(item.filename).name == executable
                    ]
                    if len(members) != 1:
                        raise RuntimeError(f"{name} archive must contain one binary")
                    source = unpack.enter_context(package.open(members[0]))
                else:
                    archive.seek(0)
                    package = unpack.enter_context(tarfile.open(fileobj=archive))
                    members = [
                        item
                        for item in package.getmembers()
                        if item.isfile() and Path(item.name).name == executable
                    ]
                    if len(members) != 1:
                        raise RuntimeError(f"{name} archive must contain one binary")
                    extracted = package.extractfile(members[0])
                    assert extracted is not None  # Members were restricted to files.
                    source = unpack.enter_context(extracted)
                with partial.open("wb") as output:
                    shutil.copyfileobj(source, output)
            partial.chmod(0o700)
            partial.replace(target)
    return str(target)


# How long one probe of the forge's API decides how the next invocations reach
# it. A wrong "not directly" only sends them through the platform's tunnel,
# which works, so that answer is kept longer; a wrong "directly" fails them, so
# that one is asked again sooner.
REACH_KEPT_S = {True: 120, False: 600}


def _kept(name):
    """A file under this machine's forge state, private to its user."""
    root = Path.home() / ".cheese" / "forge-auth"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / name


def _keep(path, value):
    """Best effort: a write that loses to another invocation (on Windows, a
    file another process has open cannot be replaced) only means the next
    invocation asks again, never that this one fails."""
    partial = path.with_name(f"{path.name}.{os.getpid()}.part")
    try:
        with open(
            partial, "w", opener=lambda p, flags: os.open(p, flags, 0o600)
        ) as output:
            json.dump(value, output)
        partial.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            partial.unlink()


def _recall(path):
    """What `_keep` left at ``path`` while it is still good, else None."""
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        kept = json.loads(path.read_text())
        if kept["until"] > time.time():
            return kept["value"]
    return None


#: What the HTTP client raises besides OSError when an answer breaks off: a
#: platform socket that stopped answering, as much as one that never did.
_CLIENT_ERRORS = (
    http.client.IncompleteRead,
    http.client.BadStatusLine,
    http.client.LineTooLong,
    http.client.ImproperConnectionState,
)


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("platform.cheese", timeout=timeout)
        self._socket_path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


def _platform_socket():
    path = os.environ.get("CHEESE_PLATFORM_SOCKET", "")
    return path if path and os.path.exists(path) else ""


def _ask_socket(path, target, timeout=30):
    """``GET target`` on the sandbox's platform socket, to its JSON: the
    platform knows whose the request is without a token."""
    connection = _UnixConnection(path, timeout)
    try:
        connection.request("GET", target)
        response = connection.getresponse()
        body = response.read()
    except _CLIENT_ERRORS as exc:
        raise OSError(
            f"the platform socket broke off: {exc}"  # i18n-exempt: machine log
        ) from exc
    finally:
        connection.close()
    if response.status != 200:
        raise OSError(
            f"the platform socket answered HTTP {response.status}"  # i18n-exempt: machine log
        )
    return json.loads(body)


def credentials():
    socket_path = _platform_socket()
    if socket_path:
        return _ask_socket(socket_path, "/api/sandbox/forge-token")["data"]
    api = os.environ["CHEESE_API"].rstrip("/")
    request = urllib.request.Request(
        f"{api}/sandbox/forge-token",
        headers={"X-Cheese-Token": os.environ["CHEESE_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["data"]


def _tunnel_secret():
    """What a forge tunnel connection is opened with: a credential asked of
    the platform socket for that connection, lasting a minute, or without a
    socket the session's own token."""
    socket_path = _platform_socket()
    if socket_path:
        return _ask_socket(socket_path, "/credential")["token"]
    return os.environ["CHEESE_TOKEN"]


def reaches(url):
    """Whether this machine reaches ``url`` without the platform, as one probe
    answered within ``REACH_KEPT_S``. An HTTP error over TLS is a route:
    GitHub's API answers an anonymous caller over its rate limit 403, and
    that is the forge talking, not a network that cannot reach it. Over plain
    HTTP the answer may be a proxy's in between, so there it is not."""
    path = _kept("reach-" + hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")
    kept = _recall(path)
    if isinstance(kept, bool):
        return kept
    try:
        with urllib.request.urlopen(url, timeout=5):
            pass
        reached = True
    except urllib.error.HTTPError:
        reached = urllib.parse.urlsplit(url).scheme == "https"
    except (OSError, urllib.error.URLError):
        reached = False
    _keep(path, {"until": time.time() + REACH_KEPT_S[reached], "value": reached})
    return reached


@contextlib.contextmanager
def github_transport(data, env):
    """Choose a route before invoking the CLI, never replay a native mutation."""
    if not reaches(data["api_url"]):
        helper = runpy.run_path(str(Path.home() / ".cheese/cheese-tunnel.py"))
        api = urllib.parse.urlsplit(os.environ["CHEESE_API"].rstrip("/"))
        tunnel = urllib.parse.urlunsplit(
            (
                "wss" if api.scheme == "https" else "ws",
                api.netloc,
                api.path + "/sandbox/forge-tunnel/" + data["project_id"],
                "",
                "",
            )
        )

        class Connection(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    secret = _tunnel_secret()
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    print(
                        f"[cheese] 平台通道暂时没给出凭据，这次连接没能打开：{exc}",
                        file=sys.stderr,
                    )
                    self.request.close()
                    return
                helper["handle_connection"](self.request, tunnel, secret)

        class Proxy(socketserver.ThreadingTCPServer):
            daemon_threads = True

        with Proxy(("127.0.0.1", 0), Connection) as proxy:
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            destination = f"http://127.0.0.1:{proxy.server_address[1]}"
            env.update(
                HTTPS_PROXY=destination,
                https_proxy=destination,
                NO_PROXY="",
                no_proxy="",
            )
            print("[cheese] 仓库无法直连，本次通过平台访问", file=sys.stderr)
            try:
                yield
            finally:
                proxy.shutdown()
                thread.join()
    else:
        yield


def fj_destination(data, arguments):
    """Select transport before invoking a command, never retry a mutation."""
    public = data["url"].rsplit("/", 2)[0]
    args = list(arguments)
    host_index = None
    inline = ""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("--host", "-H") and index + 1 < len(args):
            host_index = index + 1
            break
        if arg.startswith("--host="):
            host_index, inline = index, "--host="
            break
        if arg.startswith("-H") and len(arg) > 2:
            host_index, inline = index, "-H"
            break
        if arg == "--":
            break
        if arg in ("--cwd", "-C", "--style"):
            index += 2
        elif arg.startswith("-"):
            index += 1
        else:
            index += 1
    if host_index is not None:
        explicit = args[host_index].removeprefix(inline).rstrip("/")
        parsed = urllib.parse.urlsplit(public)
        if explicit not in (public, parsed.netloc + parsed.path):
            # Preserve explicit destinations; the temporary credential remains
            # scoped to this project's host and cannot authenticate another one.
            return public, args, None
    destination = public
    try:
        with urllib.request.urlopen(public + "/api/v1/version", timeout=5):
            pass
    except (OSError, urllib.error.URLError):
        api = os.environ["CHEESE_API"].rstrip("/")
        destination = f"{api}/sandbox/forge/{data['project_id']}"
        print("[cheese] 仓库无法直连，本次通过平台访问", file=sys.stderr)
    if host_index is None:
        args = ["--host", destination + "/", *args]
        host_index = 1
    else:
        args[host_index] = inline + destination + "/"
    return destination, args, host_index


@contextlib.contextmanager
def fj_path_transport(destination):
    """fj 0.6 joins absolute API paths, so subpath hosts need a loopback relay."""
    parsed = urllib.parse.urlsplit(destination)
    if not parsed.path.strip("/"):
        yield destination
        return

    class Forward(BaseHTTPRequestHandler):
        def forward(self):
            connection_type = (
                http.client.HTTPSConnection
                if parsed.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = connection_type(parsed.hostname, parsed.port, timeout=300)
            try:
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower()
                    in (
                        "authorization",
                        "content-type",
                        "content-length",
                        "accept",
                        "user-agent",
                        "content-encoding",
                    )
                }

                def chunks():
                    if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                        while True:
                            size = int(self.rfile.readline(128).split(b";", 1)[0], 16)
                            if not size:
                                while self.rfile.readline(8192).strip():
                                    pass
                                return
                            remaining = size
                            while remaining:
                                chunk = self.rfile.read(min(remaining, 65536))
                                if not chunk:
                                    raise ConnectionError("Incomplete request")
                                remaining -= len(chunk)
                                yield chunk
                            if self.rfile.read(2) != b"\r\n":
                                raise ConnectionError("Invalid chunk")
                    else:
                        remaining = int(self.headers.get("Content-Length", 0))
                        while remaining:
                            chunk = self.rfile.read(min(remaining, 65536))
                            if not chunk:
                                raise ConnectionError("Incomplete request")
                            remaining -= len(chunk)
                            yield chunk

                connection.request(
                    self.command,
                    parsed.path.rstrip("/") + self.path,
                    body=chunks(),
                    headers=headers,
                    encode_chunked=True,
                )
                response = connection.getresponse()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() in (
                        "content-type",
                        "content-length",
                        "content-encoding",
                        # fj requires this header even for a one-page list.
                        "x-total-count",
                    ):
                        self.send_header(key, value)
                self.end_headers()
                while chunk := response.read(65536):
                    self.wfile.write(chunk)
            finally:
                connection.close()
                self.close_connection = True

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = forward

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Forward)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run(name, arguments):
    data = credentials()
    expected = "github_app" if name == "gh" else "forgejo"
    if data["kind"] != expected:
        other = "fj" if name == "gh" else "gh"
        raise RuntimeError(
            f"This project's repository uses {other}; run {other} instead"
        )
    if any(
        arguments[index : index + 2] == ["pr", "merge"]
        for index in range(len(arguments) - 1)
    ) and not any(arg in ("--help", "-h") for arg in arguments):
        print(
            "[cheese] Merging accepts this task. Use cheese_card(action=request) "
            "to submit it for project review.",
            file=sys.stderr,
            flush=True,
        )
    binary = native_binary(name)
    env = dict(os.environ)
    for key in (
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    ):
        env.pop(key, None)
    if name == "gh":
        host = urllib.parse.urlsplit(data["url"]).netloc
        env.update(GH_HOST=host, GH_REPO=f"{host}/{data['repo']}")
        env["GH_TOKEN" if host == "github.com" else "GH_ENTERPRISE_TOKEN"] = data[
            "token"
        ]
        with github_transport(data, env):
            return subprocess.call([binary, *arguments], env=env)

    destination, arguments, host_index = fj_destination(data, arguments)
    # fj 0.6 reads keys.json, not a token environment variable.
    root = Path.home() / ".cheese" / "forge-auth"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with contextlib.ExitStack() as cleanup:
        native_destination = cleanup.enter_context(fj_path_transport(destination))
        if host_index is not None:
            prefix = (
                "--host="
                if arguments[host_index].startswith("--host=")
                else "-H"
                if arguments[host_index].startswith("-H")
                else ""
            )
            arguments[host_index] = prefix + native_destination + "/"
        temporary = cleanup.enter_context(
            tempfile.TemporaryDirectory(prefix="fj-", dir=root)
        )
        env["XDG_DATA_HOME"] = temporary
        key_dir = Path(temporary) / "forgejo-cli"
        key_dir.mkdir(parents=True, mode=0o700)
        # The API URL can be deployment-internal; native CLI calls use the
        # same public host/subpath as the checkout's remote.
        url = urllib.parse.urlsplit(native_destination)
        host = url.netloc + url.path
        keys = {"hosts": {host: {"type": "Application", "token": data["token"]}}}
        path = key_dir / "keys.json"
        with open(
            path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)
        ) as output:
            json.dump(keys, output)
        if sys.platform == "darwin":
            # directories-rs ignores XDG on macOS. Serialize access to fj's
            # fixed config path within the room; never replace a user's file.
            fixed = (
                Path.home()
                / "Library/Application Support/forgejo-cli.forgejo-cli/keys.json"
            )
            fixed.parent.mkdir(parents=True, exist_ok=True)
            lock(cleanup.enter_context((root / "macos.lock").open("a")))
            if fixed.is_symlink() and fixed.readlink().is_relative_to(root):
                fixed.unlink()  # An interrupted launcher left its own link.
            fixed.symlink_to(path)
            cleanup.callback(fixed.unlink, missing_ok=True)
        elif sys.platform == "win32":
            # directories-rs reads the roaming AppData folder on Windows, and a
            # symlink there needs Developer Mode, so the file is written in
            # place. The marker is what tells a copy an interrupted launcher
            # left from a user's own file, which is never replaced.
            fixed = Path(
                os.environ["APPDATA"], "forgejo-cli/forgejo-cli/data/keys.json"
            )
            fixed.parent.mkdir(parents=True, exist_ok=True)
            lock(cleanup.enter_context((root / "windows.lock").open("a")))
            marker = root / "windows-keys-written"
            if marker.exists():
                fixed.unlink(missing_ok=True)
            with open(fixed, "x") as output:
                json.dump(keys, output)
            marker.touch()
            cleanup.callback(marker.unlink, missing_ok=True)
            cleanup.callback(fixed.unlink, missing_ok=True)
        return subprocess.call([binary, *arguments], env=env)


def main():
    try:
        name = Path(sys.argv[0]).name
        if name not in ("gh", "fj"):
            raise RuntimeError("Invoke this launcher as gh or fj")
        return run(name, sys.argv[1:])
    except (OSError, RuntimeError, KeyError, ValueError) as error:
        print(f"[cheese] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
