"""The platform's side of a remote MCP server's transport.

Sessions call a remote server through the backend, which holds the credential,
so this is an MCP client: Streamable HTTP (spec 2025-11-25), and the 2024-11-05
HTTP+SSE transport for servers that still speak only that. Each (room, server)
pair keeps its own MCP session in this process; another backend process opens
its own, which the protocol allows.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.sentences import say
from app.domain.remote_mcp import http

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-11-25"
_CLIENT_INFO = {"name": "cheese", "version": "1"}
_TOOL_TIMEOUT_S = 600
_MAX_SESSIONS = 256


class Unauthorized(Exception):
    """The server refused the credential (401)."""


class UpstreamError(Exception):
    """The server answered with an error, or not in the protocol."""


_ids = itertools.count(1)


async def _events(response: httpx.Response) -> AsyncIterator[tuple[str, str]]:
    """(event, data) pairs of a `text/event-stream` body."""
    event, data = "message", []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)
    if data:
        yield event, "\n".join(data)


def _result(message: dict) -> dict:
    if "error" in message:
        error = message["error"] or {}
        raise UpstreamError(str(error.get("message") or error))
    return message.get("result") or {}


@dataclass
class _Streamable:
    url: str
    session_id: str | None = None
    protocol: str = PROTOCOL_VERSION
    ready: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def _headers(self, auth: dict[str, str]) -> dict[str, str]:
        headers = {
            **auth,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.ready:
            headers["MCP-Protocol-Version"] = self.protocol
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post(self, auth: dict[str, str], message: dict) -> dict | None:
        async with (
            http.client(timeout=_TOOL_TIMEOUT_S) as client,
            client.stream(
                "POST", self.url, json=message, headers=self._headers(auth)
            ) as response,
        ):
            if response.status_code == 401:
                raise Unauthorized
            if response.status_code == 404 and self.session_id:
                raise _SessionExpired
            if "id" not in message:
                if response.status_code >= 400:
                    raise UpstreamError(
                        say("mcpServerReturned", status=response.status_code)
                    )
                return None
            if response.status_code != 200:
                await response.aread()
                raise UpstreamError(
                    say("mcpServerReturned", status=response.status_code)
                )
            if message.get("method") == "initialize":
                self.session_id = response.headers.get("mcp-session-id")
            kind = response.headers.get("content-type", "")
            if kind.startswith("text/event-stream"):
                async for _event, data in _events(response):
                    try:
                        reply = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(reply, dict) and reply.get("id") == message["id"]:
                        return reply
                raise UpstreamError(say("mcpServerNoResult"))
            return json.loads(await response.aread())

    async def _initialize(self, auth: dict[str, str]) -> None:
        self.session_id, self.ready = None, False
        reply = await self._post(
            auth,
            {
                "jsonrpc": "2.0",
                "id": next(_ids),
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": _CLIENT_INFO,
                },
            },
        )
        result = _result(reply or {})
        self.protocol = str(result.get("protocolVersion") or PROTOCOL_VERSION)
        self.ready = True
        await self._post(
            auth, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

    async def request(self, auth: dict[str, str], method: str, params: dict) -> dict:
        async with self.lock:
            if not self.ready:
                await self._initialize(auth)
        message = {"jsonrpc": "2.0", "id": next(_ids), "method": method}
        if params:
            message["params"] = params
        try:
            reply = await self._post(auth, message)
        except _SessionExpired:
            async with self.lock:
                await self._initialize(auth)
            reply = await self._post(auth, message)
        return _result(reply or {})

    async def close(self) -> None:
        return None


class _SessionExpired(Exception):
    pass


@dataclass
class _Legacy:
    """The 2024-11-05 HTTP+SSE transport: one GET stream carries every reply,
    and requests are POSTed to the endpoint the stream names first."""

    url: str
    endpoint: str | None = None
    ready: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[int, asyncio.Future] = field(default_factory=dict)
    reader: asyncio.Task | None = None

    async def _open(self, auth: dict[str, str]) -> None:
        await self.close()
        opened: asyncio.Future = asyncio.get_running_loop().create_future()
        self.reader = asyncio.create_task(self._read(auth, opened))
        self.endpoint = await asyncio.wait_for(opened, 30)
        reply = await self._send(
            auth,
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": _CLIENT_INFO,
            },
        )
        _result(reply)
        await self._post(
            auth, {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.ready = True

    async def _read(self, auth: dict[str, str], opened: asyncio.Future) -> None:
        try:
            async with (
                http.client(timeout=None) as client,
                client.stream(
                    "GET", self.url, headers={**auth, "Accept": "text/event-stream"}
                ) as response,
            ):
                if response.status_code == 401:
                    raise Unauthorized
                if response.status_code != 200:
                    raise UpstreamError(
                        say("mcpServerReturned", status=response.status_code)
                    )
                async for event, data in _events(response):
                    if event == "endpoint":
                        endpoint = urljoin(self.url, data.strip())
                        if urlsplit(endpoint).netloc != urlsplit(self.url).netloc:
                            raise UpstreamError(say("mcpServerRedirectedHost"))
                        if not opened.done():
                            opened.set_result(endpoint)
                        continue
                    try:
                        message = json.loads(data)
                    except ValueError:
                        continue
                    waiter = self.pending.pop(message.get("id"), None)
                    if waiter is not None and not waiter.done():
                        waiter.set_result(message)
        except Exception as exc:  # noqa: BLE001 — handed to whoever waits
            if not opened.done():
                opened.set_exception(exc)
            for waiter in self.pending.values():
                if not waiter.done():
                    waiter.set_exception(exc)
        finally:
            self.ready = False
            error = UpstreamError(say("mcpServerDisconnected"))
            if not opened.done():
                opened.set_exception(error)
            for waiter in self.pending.values():
                if not waiter.done():
                    waiter.set_exception(error)
            self.pending.clear()

    async def _post(self, auth: dict[str, str], message: dict) -> None:
        assert self.endpoint is not None
        async with http.client() as client:
            response = await client.post(
                self.endpoint,
                json=message,
                headers={**auth, "Content-Type": "application/json"},
            )
        if response.status_code == 401:
            raise Unauthorized
        if response.status_code >= 400:
            raise UpstreamError(say("mcpServerReturned", status=response.status_code))

    async def _send(self, auth: dict[str, str], method: str, params: dict) -> dict:
        identity = next(_ids)
        waiter: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[identity] = waiter
        message = {"jsonrpc": "2.0", "id": identity, "method": method}
        if params:
            message["params"] = params
        try:
            await self._post(auth, message)
            return await asyncio.wait_for(waiter, _TOOL_TIMEOUT_S)
        finally:
            self.pending.pop(identity, None)

    async def request(self, auth: dict[str, str], method: str, params: dict) -> dict:
        async with self.lock:
            if not self.ready:
                await self._open(auth)
        return _result(await self._send(auth, method, params))

    async def close(self) -> None:
        self.ready = False
        if self.reader is not None:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.reader
            self.reader = None


_sessions: OrderedDict[tuple, _Streamable | _Legacy] = OrderedDict()


async def request(
    key: tuple,
    *,
    transport: str,
    url: str,
    auth: dict[str, str],
    method: str,
    params: dict,
) -> dict:
    """One JSON-RPC request to the server, over the session this key holds."""
    full = (*key, transport, url)
    session = _sessions.get(full)
    if session is None:
        session = _Legacy(url) if transport == "sse" else _Streamable(url)
        _sessions[full] = session
        while len(_sessions) > _MAX_SESSIONS:
            _, evicted = _sessions.popitem(last=False)
            await evicted.close()
    _sessions.move_to_end(full)
    return await session.request(auth, method, params)


async def forget(key_prefix: tuple) -> None:
    """Drop every session whose key starts with this prefix (a disconnect)."""
    for full in [k for k in _sessions if k[: len(key_prefix)] == key_prefix]:
        await _sessions.pop(full).close()
