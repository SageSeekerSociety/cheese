"""JSON-RPC over an app-server stream owned by the session runner."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any


class AppServerError(RuntimeError):
    def __init__(self, error: dict):
        super().__init__(error.get("message", "Codex request failed"))
        self.code = error.get("code")


class AppServer:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        on_event: Callable[[dict], Awaitable[None]],
        on_request: Callable[[str, dict], Awaitable[dict]],
    ):
        self.reader = reader
        self.writer = writer
        self.on_event = on_event
        self.on_request = on_request
        self.pending: dict[int, asyncio.Future[Any]] = {}
        self.sequence = 0
        self.write_lock = asyncio.Lock()
        self.handlers: set[asyncio.Task] = set()
        self.closed = False

    async def write(self, value: dict) -> None:
        async with self.write_lock:
            self.writer.write(json.dumps(value, ensure_ascii=False).encode() + b"\n")
            await self.writer.drain()

    async def request(self, method: str, params: dict) -> Any:
        if self.closed:
            raise ConnectionError("Codex app-server connection is closed")
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self.pending.pop(request_id, None)

    async def initialize(self) -> dict:
        result = await self.request(
            "initialize",
            {
                "clientInfo": {"name": "cheese", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        await self.write({"method": "initialized"})
        return result

    async def _answer(self, message: dict) -> None:
        try:
            result = await self.on_request(message["method"], message.get("params", {}))
            response = {"id": message["id"], "result": result}
        except Exception as error:
            response = {
                "id": message["id"],
                "error": {"code": -32603, "message": str(error)},
            }
        await self.write(response)

    async def listen(self) -> None:
        failure: Exception = ConnectionError("Codex app-server disconnected")
        try:
            while line := await self.reader.readline():
                message = json.loads(line)
                if "method" in message:
                    if "id" in message:
                        # Tool execution must not block reading cancellation or replies.
                        task = asyncio.create_task(self._answer(message))
                        self.handlers.add(task)
                        task.add_done_callback(self.handlers.discard)
                    else:
                        await self.on_event(message)
                    continue
                future = self.pending.get(message.get("id"))
                if future is None or future.done():
                    continue
                if "error" in message:
                    future.set_exception(AppServerError(message["error"]))
                else:
                    future.set_result(message.get("result"))
        except Exception as error:
            failure = error
            raise
        finally:
            self.closed = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(failure)
            for task in self.handlers:
                task.cancel()
            await asyncio.gather(*self.handlers, return_exceptions=True)
