"""A command cut off as pi exits fails once, and leaves nothing unread behind.

pi exiting closes both of its pipes: the command being written fails at the
write, and the reader, at end of stream, fails every reply still pending. When
the reader gets there first, the command's reply has failed with nobody left
to await it, and asyncio reports it as an exception never retrieved, at
garbage collection, wherever the loop happens to be. Under anyio's test runner
that report fails whichever test is running then.
"""

import asyncio
import gc

import pytest

from app.domain.agent.harness.pi.rpc import Connection


class ExitingPi:
    """pi's stdin as pi exits: the write is taken, and before the drain
    reports the pipe gone, pi's stdout reaches its end."""

    def __init__(self, stdout: asyncio.StreamReader) -> None:
        self.stdout = stdout

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        self.stdout.feed_eof()
        for _ in range(3):
            await asyncio.sleep(0)
        raise ConnectionResetError("Connection lost")


async def test_a_command_cut_off_as_pi_exits_leaves_no_error_unread():
    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    try:
        stdout = asyncio.StreamReader()

        async def on_event(_event: dict) -> None:
            pass

        pi = Connection(stdout, ExitingPi(stdout), on_event=on_event)
        listening = asyncio.create_task(pi.listen())
        with pytest.raises(ConnectionResetError):
            await pi.request("get_entries")
        await listening
        gc.collect()
        await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous)
    assert reported == []
