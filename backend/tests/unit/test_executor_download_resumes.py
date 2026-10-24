"""Installing the executor on a person's own machine over a link that drops.

The executor is a quarter of a gigabyte and one install has two minutes. What
must hold: an attempt that is cut off leaves its progress for the next one,
which fetches only the rest; bytes that are not the binary the platform serves
are never installed; and a failed attempt's last line — all the room is told —
says what it was fetching and how far it got.

Served by the platform's real route, over a real socket, to the real bootstrap.
"""

from __future__ import annotations

import os
import random
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request

from app.api.routes import installer
from app.domain.agent.harness.claude_code.remote_execution import bootstrap
from app.domain.machine import claude_dist, install_progress
from tests.support import executor_release

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake executor is a shell script"
)

# A script that answers `--version` as the pinned build does, long enough to
# take several reads, and only partly compressible.
EXECUTOR = (
    f'#!/bin/sh\necho "{bootstrap.VERSION} (Claude Code)"\nexit 0\n'.encode()
    + b"".join(
        b"# %s\n" % random.Random(n).randbytes(32).hex().encode() for n in range(30000)
    )
)


def _serve(app: FastAPI) -> tuple[str, uvicorn.Server, threading.Thread]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off"))
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [listener]}, daemon=True
    )
    thread.start()
    while not server.started:
        time.sleep(0.01)
    return f"http://127.0.0.1:{listener.getsockname()[1]}", server, thread


class _Cut:
    """A link in front of the platform that drops the next response after
    ``after`` bytes, as a mobile connection or a TLS middlebox does."""

    def __init__(self, upstream: str) -> None:
        self.port = int(upstream.rsplit(":", 1)[1])
        self.after: int | None = None
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.listener.getsockname()[1]}"

    def _accept(self) -> None:
        while True:
            client, _ = self.listener.accept()
            threading.Thread(target=self._relay, args=(client,), daemon=True).start()

    def _relay(self, client: socket.socket) -> None:
        after, self.after = self.after, None
        upstream = socket.create_connection(("127.0.0.1", self.port))
        request = client.recv(65536)
        upstream.sendall(request)
        sent = 0
        while chunk := upstream.recv(65536):
            if after is not None and sent + len(chunk) >= after:
                client.sendall(chunk[: after - sent])
                break
            client.sendall(chunk)
            sent += len(chunk)
        upstream.close()
        client.shutdown(socket.SHUT_RDWR)
        client.close()


@pytest.fixture
def platform(tmp_path, monkeypatch):
    """A machine with no executor of its own, the release it was handed, the
    platform's download route over a link the test can cut, and the ranges it
    was asked for."""
    cached = tmp_path / "cache" / "claude"
    cached.parent.mkdir()
    cached.write_bytes(EXECUTOR)

    async def ensure_cached(root, version, platform_):
        return cached

    monkeypatch.setattr(claude_dist, "ensure_cached", ensure_cached)
    asked: list[str | None] = []
    app = FastAPI()

    @app.middleware("http")
    async def record(request: Request, call_next):
        asked.append(request.headers.get("range"))
        return await call_next(request)

    app.include_router(installer.router)
    origin, server, thread = _serve(app)
    cut = _Cut(origin)
    bare = tmp_path / "bin"
    bare.mkdir()
    monkeypatch.setenv("PATH", str(bare))
    owner = tmp_path / "owner"
    owner.mkdir()
    # The release the machine was handed, which carries the downloader.
    release = executor_release.install(tmp_path / "platform").parent.parent
    yield owner, release, cut, asked
    server.should_exit = True
    thread.join(5)


def _installed(owner: Path) -> Path:
    return owner / ".cheese/claude/versions" / bootstrap.VERSION


def test_a_cut_download_is_continued_and_installs_the_executor(platform):
    owner, release, cut, asked = platform

    cut.after = 600_000
    with pytest.raises(RuntimeError):
        bootstrap.binary(owner, cut.url, release)
    assert not _installed(owner).exists()

    path = bootstrap.binary(owner, cut.url, release)

    assert path == str(_installed(owner))
    assert _installed(owner).read_bytes() == EXECUTOR
    assert os.access(path, os.X_OK)
    # The second attempt asked only for what the first did not get.
    assert asked[0] is None
    assert asked[1] is not None and asked[1].startswith("bytes=")
    assert int(asked[1].removeprefix("bytes=").rstrip("-")) > 0


def test_a_cut_download_says_what_it_was_fetching_and_how_far_it_got(platform):
    owner, release, cut, _ = platform

    cut.after = 600_000
    with pytest.raises(RuntimeError) as stopped:
        bootstrap.binary(owner, cut.url, release)

    line = str(stopped.value).splitlines()[-1]
    assert f"{cut.url}/connector/claude/{bootstrap.VERSION}/" in line
    assert re.search(r"after \d+\.\d MB of \d+\.\d MB", line), line


def test_a_download_says_how_far_it_has_got_in_lines_the_platform_reads(
    platform, capsys
):
    """What the room is shown while the executor downloads
    (`install_progress`) is read off these lines: from where it starts to all
    of it, never going back."""
    owner, release, cut, _ = platform

    bootstrap.binary(owner, cut.url, release)

    said = [
        progress
        for line in capsys.readouterr().err.splitlines()
        if (progress := install_progress.downloaded(line)) is not None
    ]
    assert said, "the download said nothing the platform reads"
    total = said[0][1]
    assert said[0][0] == 0
    assert said[-1] == (total, total)
    assert [have for have, _ in said] == sorted(have for have, _ in said)


def test_bytes_that_are_not_the_served_executor_are_never_installed(platform):
    owner, release, cut, _ = platform
    versions = _installed(owner).parent
    versions.mkdir(parents=True)
    # What an earlier attempt left, but not the start of what is served.
    (versions / f"{bootstrap.VERSION}.partial").write_bytes(b"\x1f\x8b" + b"x" * 4096)

    with pytest.raises(RuntimeError) as wrong:
        bootstrap.binary(owner, cut.url, release)

    assert cut.url in str(wrong.value).splitlines()[-1]
    assert not _installed(owner).exists()
    # Thrown away, so the attempt after it starts over and succeeds.
    assert bootstrap.binary(owner, cut.url, release) == str(_installed(owner))
    assert _installed(owner).read_bytes() == EXECUTOR


def test_a_download_that_arrived_whole_is_installed_without_fetching_it_again(
    platform, tmp_path
):
    """The attempt that received the last byte can still be killed at the
    deadline before unpacking it; the next one installs what is held."""
    owner, release, cut, asked = platform
    whole = claude_dist.packed(tmp_path / "cache" / "claude").read_bytes()
    versions = _installed(owner).parent
    versions.mkdir(parents=True)
    (versions / f"{bootstrap.VERSION}.partial").write_bytes(whole)

    assert bootstrap.binary(owner, cut.url, release) == str(_installed(owner))

    assert _installed(owner).read_bytes() == EXECUTOR
    # Nothing past what was held was fetched: one byte at most, to learn the
    # digest the platform names.
    starts = [int(a.removeprefix("bytes=").rstrip("-")) for a in asked if a]
    assert len(starts) == len(asked) and min(starts) >= len(whole) - 1, asked


def test_an_executor_that_is_not_the_digest_the_platform_names_is_not_installed(
    platform, monkeypatch
):
    owner, release, cut, _ = platform
    monkeypatch.setattr(claude_dist, "served_digest", lambda binary: "0" * 64)

    with pytest.raises(RuntimeError) as wrong:
        bootstrap.binary(owner, cut.url, release)

    assert "sha256" in str(wrong.value).splitlines()[-1]
    assert not _installed(owner).exists()
    # Nothing it fetched is left to be picked up as a part of the next one.
    assert not list(_installed(owner).parent.glob(f"{bootstrap.VERSION}.*partial*"))


def test_a_bootstrap_waits_for_an_install_under_way_and_uses_it(platform, capfd):
    """The desktop app fetches the executor in the background once a computer
    is connected (`cheesehost claude install`), and a session may start before
    it finishes. The bootstrap then waits for that install and runs what it
    placed, rather than fetching a second copy alongside it."""
    owner, release, cut, asked = platform
    destination = _installed(owner)
    destination.parent.mkdir(parents=True)
    answer: list[object] = []
    with destination.with_name(destination.name + ".lock").open("a") as held:
        import fcntl  # deferred-import: POSIX-only; this module is skipped on Windows

        fcntl.flock(held, fcntl.LOCK_EX)
        waiting = threading.Thread(
            target=lambda: answer.append(bootstrap.binary(owner, cut.url, release))
        )
        waiting.start()
        time.sleep(0.5)
        assert not answer, "installed while another installer held the lock"
        # What the other installer places, as it does: whole, then renamed in.
        placed = destination.with_name("placed")
        placed.write_bytes(EXECUTOR)
        placed.chmod(0o700)
        placed.replace(destination)
    waiting.join(30)

    assert answer == [str(destination)]
    assert asked == []
    # Said while it waited, in case the wait outlasts the install's deadline.
    assert "waiting for another install" in capfd.readouterr().err
