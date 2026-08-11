"""The tmux control client, exercised against a REAL tmux server.

A protocol parser tested only against a fake proves the parser, not tmux. The
one property the whole design rests on — that tmux reports a send into a dead
pane as SUCCESS — is a fact about tmux, so it has to be measured against tmux.
"""

import asyncio
import pathlib
import shutil
import subprocess
import uuid

import pytest

from app.domain.agent.tmux_control import TmuxControlClient, TmuxControlError

pytestmark = pytest.mark.skipif(
    shutil.which("tmux") is None, reason="tmux not installed"
)


@pytest.fixture
def server():
    """A scratch tmux server with one session running `cat` (a process that sits
    there until killed, so the pane has a real, killable occupant).

    NOT under ``tmp_path``: a unix socket path is capped at 104 bytes on macOS
    and pytest's per-test directories run past it, so tmux silently fails to
    bind and every case dies at connect.
    """
    sock = f"/tmp/cx-{uuid.uuid4().hex[:10]}.sock"
    name = f"s{uuid.uuid4().hex[:8]}"

    def tmux(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", "-S", sock, *args], capture_output=True, text=True
        )

    tmux("new-session", "-d", "-s", name, "-x", "80", "-y", "24", "cat")
    tmux("set-option", "-t", name, "remain-on-exit", "on")
    try:
        yield sock, name, tmux
    finally:
        tmux("kill-server")
        pathlib.Path(sock).unlink(missing_ok=True)


async def _client(sock: str, session: str) -> TmuxControlClient:
    client = TmuxControlClient(sock, session)
    await client.start()
    return client


def test_a_successful_command_reports_ok(server):
    sock, name, _ = server

    async def go() -> None:
        client = await _client(sock, name)
        try:
            result = await client.send("send-keys", "-t", name, "-l", "HELLO")
            assert result.ok, result.error
        finally:
            await client.close()

    asyncio.run(go())


def test_a_failing_command_carries_tmux_reason(server):
    sock, name, _ = server

    async def go() -> None:
        client = await _client(sock, name)
        try:
            result = await client.send("send-keys", "-t", "%999", "-l", "nope")
            assert not result.ok
            assert "can't find pane" in result.error
        finally:
            await client.close()

    asyncio.run(go())


def test_tmux_reports_success_when_the_pane_is_dead(server):
    """The property that forces a liveness check: tmux guarantees the bytes
    reached the pane, never that a program read them."""
    sock, name, tmux = server

    async def go() -> None:
        client = await _client(sock, name)
        try:
            pid = tmux("list-panes", "-t", name, "-F", "#{pane_pid}").stdout.strip()
            subprocess.run(["kill", "-9", pid], capture_output=True)
            await asyncio.sleep(0.5)

            assert await client.pane_dead(), "tmux should mark the pane dead"
            result = await client.send("send-keys", "-t", name, "-l", "INTO-THE-VOID")
            assert result.ok, "tmux still accepts the send — hence the precheck"
        finally:
            await client.close()

    asyncio.run(go())


def test_pane_dead_is_false_while_the_process_lives(server):
    sock, name, _ = server

    async def go() -> None:
        client = await _client(sock, name)
        try:
            assert not await client.pane_dead()
        finally:
            await client.close()

    asyncio.run(go())


def test_pane_output_reaches_a_subscriber(server):
    """`%output` is how we see our own injected text come back, which is what
    replaces screen-scraping for readiness."""
    sock, name, _ = server
    seen: list[str] = []

    async def go() -> None:
        client = await _client(sock, name)
        client.on_output(seen.append)
        try:
            await client.send("send-keys", "-t", name, "-l", "ECHO-ME")
            for _ in range(30):
                await asyncio.sleep(0.1)
                if any("ECHO-ME" in chunk for chunk in seen):
                    return
            pytest.fail(f"no %output carried the text; saw {seen!r}")
        finally:
            await client.close()

    asyncio.run(go())


def test_a_dead_server_surfaces_as_a_typed_error():
    async def go() -> None:
        client = TmuxControlClient(f"/tmp/cx-none-{uuid.uuid4().hex[:8]}.sock", "nope")
        with pytest.raises(TmuxControlError):
            await client.start()

    asyncio.run(go())
