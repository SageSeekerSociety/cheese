#!/usr/bin/env python3
"""Run the native forge CLI with credentials minted for this invocation only.

Shipped as both gh and fj; standard library only, like the executor bootstrap.
"""

import contextlib
import fcntl
import hashlib
import http.client
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def native_binary(name):
    wrapper = Path(__file__).resolve()
    with wrapper.open("rb") as stream:
        signature = stream.read(128)
    for directory in os.get_exec_path():
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open("rb") as stream:
                if stream.read(128) != signature:
                    return str(candidate)
    if sys.platform not in ("linux", "darwin"):
        raise RuntimeError(f"Native {name} is not installed on this machine")
    machine = platform.machine()
    arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64"}.get(machine)
    if arch is None:
        raise RuntimeError(f"No {name} distribution for {machine}")
    version = {"fj": "0.6.0", "gh": "2.62.0"}[name]
    directory = Path.home() / f".cheese/native/{name}-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    with (directory / "install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_file():
            return str(target)
        url = (
            os.environ["CHEESE_API"].rstrip("/")
            + f"/connector/toolchain/{name}/{sys.platform}-{arch}/artifact"
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
                        if not item.is_dir() and Path(item.filename).name == name
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
                        if item.isfile() and Path(item.name).name == name
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


def credentials():
    api = os.environ["CHEESE_API"].rstrip("/")
    request = urllib.request.Request(
        f"{api}/sandbox/forge-token",
        headers={"X-Cheese-Token": os.environ["CHEESE_TOKEN"]},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["data"]


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
            lock = cleanup.enter_context((root / "macos.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX)
            if fixed.is_symlink() and fixed.readlink().is_relative_to(root):
                fixed.unlink()  # An interrupted launcher left its own link.
            fixed.symlink_to(path)
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
