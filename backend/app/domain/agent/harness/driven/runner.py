"""The part of a session-machine runner that does not depend on what it drives.

A runner owns one agent process and outlives every backend that reads from it.
What it does the same way for any harness: hold the state directory's lock,
answer one JSON line per connection on the socket the connector relays to, and
accept each input at most once however many times a reconnecting backend asks.
"""

import asyncio
import contextlib
import fcntl
import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Generic, TypeVar

from app.domain.agent.harness.driven.journal import Journal

# Not PEP 695 syntax: this module runs on the session machine's interpreter.
J = TypeVar("J", bound=Journal)


def socket_path(state: Path) -> str:
    """Where the connector will look, given this state directory.

    The name is not ours to choose: ``cli/internal/host/executor.go`` derives it
    from the state directory the backend recorded and relays one JSON line each
    way. Matching it is what lets ``hub.call_executor`` reach this runner with no
    connector change at all.
    """
    digest = hashlib.sha256(str(state.resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


class Runner(Generic[J]):  # noqa: UP046
    def __init__(self, state: Path, journal: type[J], name: str):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = state
        self.journal = journal(state / name)
        self.process: asyncio.subprocess.Process | None = None
        self.listener: asyncio.Task | None = None
        self.server: asyncio.Server | None = None
        self.inputs: dict[str, asyncio.Task] = {}
        self.errors = None
        self.lock = None

    def claim(self) -> None:
        """Take the state directory, or fail if another runner holds it."""
        self.lock = (self.state / "runner.lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Only the lock owner may remove a socket a crashed runner left behind.
        Path(socket_path(self.state)).unlink(missing_ok=True)

    async def listen(self, limit: int) -> None:
        """Open the socket; last, so a backend that reaches it finds a session."""
        self.server = await asyncio.start_unix_server(
            self.handle, path=socket_path(self.state), limit=limit
        )
        os.chmod(socket_path(self.state), 0o600)

    async def accept(
        self,
        identifier: str,
        content: dict,
        submit: Callable[[], Awaitable[dict]],
    ) -> dict:
        """Put one input in, at most once, however many times we are asked.

        The identifier is the platform's, and it is what makes a reconnection
        safe: a backend that never saw our answer resends the same id and gets
        the same outcome rather than a second turn. The same id with different
        content is refused, since answering it with the first input's outcome
        would report something that was never sent.
        """
        payload = json.dumps(content, sort_keys=True)
        previous = self.journal.input(identifier)
        if previous is not None:
            if previous[0] != payload:
                raise ValueError("An input ID cannot be reused for different text")
            if previous[1] == "accepted":
                return previous[2] or {}
            if previous[1] == "failed":
                raise RuntimeError((previous[2] or {})["error"])
            if identifier not in self.inputs:
                raise RuntimeError(
                    "Previous input outcome is unresolved; it was not resubmitted"
                )
        else:
            self.journal.begin_input(identifier, payload)
            task = asyncio.create_task(self._settle(identifier, submit))
            self.inputs[identifier] = task

            def finished(task):
                self.inputs.pop(identifier, None)
                if not task.cancelled():
                    task.exception()  # Failure is retained in the input journal.

            task.add_done_callback(finished)
        return await asyncio.shield(self.inputs[identifier])

    async def _settle(
        self, identifier: str, submit: Callable[[], Awaitable[dict]]
    ) -> dict:
        try:
            result = await submit()
            self.journal.finish_input(identifier, "accepted", result)
            return result
        except Exception as error:
            self.journal.finish_input(identifier, "failed", {"error": str(error)})
            raise

    async def dispatch(self, method: str, params: dict) -> dict:
        raise NotImplementedError

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = json.loads(await reader.readline())
            response = {
                "result": await self.dispatch(
                    request["method"], request.get("params", {})
                )
            }
        except Exception as error:
            response = {"error": str(error)}
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, BrokenPipeError):
            # A disconnected backend does not cancel the accepted input.
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, BrokenPipeError):
                await writer.wait_closed()

    async def stopped(self) -> None:
        """Wait out the protocol reader once its process is gone."""
        if self.listener is not None:
            await self.listener

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        try:
            await self.stopped()
        finally:
            # Protocol failure must release the lock and database too, so a
            # supervised replacement can resume the recorded session.
            await asyncio.gather(*self.inputs.values(), return_exceptions=True)
            if self.errors is not None:
                self.errors.close()
            self.journal.close()
            if self.server is not None:
                Path(socket_path(self.state)).unlink(missing_ok=True)
            if self.lock is not None:
                self.lock.close()
