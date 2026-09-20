"""JSONL over pi's stdio, and the two framing rules it is strict about.

Not JSON-RPC, though it rhymes: a command is ``{"id", "type", ...}``, its reply
is ``{"id", "type": "response", "command", "success", "data"|"error"}``, and an
event is a bare ``{"type", ...}`` with no id at all. So a reader cannot pair
everything up by id — the unmatched objects ARE the stream, and dropping them is
dropping the session's output.

Two rules the transport gets wrong by default, both of which corrupt a Chinese
session specifically:

**Split on LF and nothing else.** pi's own documentation says so, because a
generic line reader that also breaks on U+2028/U+2029 will cut a message in
half the moment the agent writes one — and those characters appear in ordinary
prose. ``StreamReader.readline`` is correct here; ``for line in text_stream`` in
Python is not.

**Raise the buffer limit.** ``agent_end`` carries the whole conversation, and
``asyncio``'s default 64 KiB line limit is not a truncation but a
``LimitOverrunError`` that kills the reader. One turn of Chinese with a few tool
results clears 64 KiB easily, so the limit belongs with the transport rather
than being discovered in production.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

# One line may carry the entire conversation (``agent_end``). Sized to be a
# ceiling nothing legitimate reaches rather than a budget: the alternative to a
# large buffer is a dead reader, not a smaller message.
LINE_LIMIT = 64 * 1024 * 1024


class PiError(RuntimeError):
    """A command pi refused. ``command`` is which one, so a caller that sends
    several does not have to guess from the message."""

    def __init__(self, command: str, message: str):
        super().__init__(message or f"pi refused {command}")
        self.command = command


class Connection:
    """Send commands, read replies, hand events to whoever is listening.

    ``on_event`` receives every object that is not a reply, in arrival order.
    Reading happens in ``listen``; a caller awaiting ``request`` is woken by it,
    which is why nothing here works until ``listen`` is running.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        on_event: Callable[[dict], Awaitable[None]],
    ):
        self.reader = reader
        self.writer = writer
        self.on_event = on_event
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.write_lock = asyncio.Lock()
        self.closed = False

    async def send(self, value: dict) -> None:
        async with self.write_lock:
            self.writer.write(json.dumps(value, ensure_ascii=False).encode() + b"\n")
            await self.writer.drain()

    async def request(self, command: str, **fields: Any) -> Any:
        if self.closed:
            raise ConnectionError("pi rpc connection is closed")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "type": command, **fields})
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def listen(self) -> None:
        failure: Exception = ConnectionError("pi disconnected")
        try:
            while line := await self.reader.readline():
                # Strict JSONL: the only delimiter is LF, and a trailing CR is
                # framing rather than content.
                text = line.decode().rstrip("\r\n")
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    # A line we cannot parse is one event lost; the session is
                    # still producing, so reading must not stop for it.
                    continue
                if message.get("type") != "response":
                    await self.on_event(message)
                    continue
                future = self.pending.get(message.get("id"))
                if future is None or future.done():
                    # A reply to a request whose caller already gave up. Events
                    # carry the work, so there is nothing here to recover.
                    continue
                if message.get("success"):
                    future.set_result(message.get("data"))
                else:
                    future.set_exception(
                        PiError(message.get("command", ""), message.get("error", ""))
                    )
        except Exception as error:
            failure = error
            raise
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
