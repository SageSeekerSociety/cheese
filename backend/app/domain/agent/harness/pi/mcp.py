"""项目的 MCP 服务器在 pi 这边：开场时列出它们的工具，调用时送回去。

pi has no MCP client of its own, so the runner is one, and each kind of server
is reached where the other harnesses reach it:

- **stdio servers** in the checkout's ``.mcp.json`` run as processes here. pi
  runs on the machine that holds the workspace, which is the machine Claude
  Code's and Codex's executors start the same processes on, with the same
  command, arguments, environment and working directory.
- **remote servers** (an entry with a ``url``) are the platform's to call. The
  backend names the ones this session can use (``remote_mcp``, from the default
  branch's ``.mcp.json``), and every request goes to ``/topics/{id}/mcp/{name}``
  through ``RemoteClient``, the client Codex's tools use. The backend attaches
  the server's credential, so nothing on this machine ever holds it. A stdio
  entry in the checkout under a remote server's name does not replace it: which
  host a name reaches is decided by committed configuration, not the checkout.

A tool is registered with pi as ``mcp__<server>__<tool>``, the name Claude Code
and Codex give it, and the tool list is read once, when the session starts, as
the other harnesses read it.
"""

import asyncio
import contextlib
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

from app.domain.agent.executor_transport import RemoteClient
from app.domain.agent.harness.pi import hooks
from app.domain.agent.harness.pi.rpc import LINE_LIMIT

PROTOCOL_VERSION = "2024-11-05"
# Listing runs before the runner binds its socket, and the room's first turn
# waits on that socket for two minutes (`channel.STARTUP_WAIT_S`). Servers are
# listed together, so one that hangs costs this much and not the room.
LIST_TIMEOUT_S = 30.0
# The executor's bound for one call to a stdio server (`MCPProcess.finish`).
CALL_TIMEOUT_S = 300.0


def tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def stdio_servers(workspace: str) -> dict[str, dict]:
    """The checkout's stdio servers: every entry without a ``url``."""
    try:
        text = (Path(workspace) / ".mcp.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    declared = json.loads(text).get("mcpServers") or {}
    return {
        name: spec
        for name, spec in declared.items()
        if isinstance(spec, dict) and not isinstance(spec.get("url"), str)
    }


class StdioServer:
    """One stdio MCP server, as a child of this runner."""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self.pending: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.reader = asyncio.create_task(self._read())

    @classmethod
    async def start(
        cls, spec: dict, *, workspace: str, env: dict[str, str], log: Path
    ) -> "StdioServer":
        with log.open("ab") as errors:
            process = await asyncio.create_subprocess_exec(
                spec["command"],
                *spec.get("args", []),
                cwd=spec.get("cwd", workspace),
                env={**env, **spec.get("env", {})},
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=errors,
                # Its own group, so closing it takes whatever it started too.
                start_new_session=True,
                limit=LINE_LIMIT,
            )
        server = cls(process)
        try:
            await server.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "cheese-pi", "version": "0.1.0"},
                },
                timeout=LIST_TIMEOUT_S,
            )
            await server._send(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        except BaseException:
            await server.close()
            raise
        return server

    async def _send(self, message: dict) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await self.process.stdin.drain()

    async def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "method" in message:
                    # A request from the server (sampling, elicitation): there
                    # is nobody here to answer it, and saying so beats a server
                    # waiting forever.
                    if "id" in message:
                        await self._send(
                            {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "This client has no sampling "
                                    "or elicitation handler",
                                },
                            }
                        )
                    continue
                future = self.pending.pop(message.get("id"), None)
                if future is not None and not future.done():
                    future.set_result(message)
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("MCP process disconnected"))
            self.pending.clear()

    async def request(self, method: str, params: dict, *, timeout: float) -> dict:
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": key, "method": method, "params": params}
            )
            answer = await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(key, None)
        if "error" in answer:
            raise RuntimeError(answer["error"].get("message") or str(answer["error"]))
        return answer["result"]

    async def close(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self.process.wait(), 2)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGKILL)
            await self.process.wait()
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)


def _pi_result(result: dict) -> dict:
    """An MCP ``tools/call`` result as pi's tool content: text and images as
    they are, anything pi has no content type for as its JSON."""
    content = []
    for item in result.get("content", []):
        if item.get("type") == "text":
            content.append({"type": "text", "text": item["text"]})
        elif item.get("type") == "image":
            content.append(
                {"type": "image", "data": item["data"], "mimeType": item["mimeType"]}
            )
        else:
            content.append(
                {"type": "text", "text": json.dumps(item, ensure_ascii=False)}
            )
    if result.get("structuredContent") is not None:
        content.append(
            {
                "type": "text",
                "text": json.dumps(result["structuredContent"], ensure_ascii=False),
            }
        )
    return {"content": content, "isError": bool(result.get("isError"))}


class ProjectServers:
    """The session's MCP servers: listed once at start, called by tool name."""

    def __init__(
        self,
        state: Path,
        *,
        workspace: str,
        env: dict[str, str],
        remote: dict | None = None,
    ):
        self.state = state
        self.workspace = workspace
        self.env = env
        self.remote_names = list((remote or {}).get("servers", []))
        self.remote = (
            RemoteClient({"remote_mcp": remote}) if self.remote_names else None
        )
        self.stdio: dict[str, StdioServer] = {}
        #: pi's tool name -> (server, the server's own tool name)
        self.routes: dict[str, tuple[str, str]] = {}

    async def _list_stdio(self, name: str, spec: dict) -> list[dict]:
        digest = hashlib.sha256(name.encode()).hexdigest()[:12]
        server = await StdioServer.start(
            spec,
            workspace=self.workspace,
            env=self.env,
            log=self.state / f"mcp-{digest}.log",
        )
        self.stdio[name] = server
        return await self._pages(
            lambda params: server.request("tools/list", params, timeout=LIST_TIMEOUT_S)
        )

    async def _list_remote(self, name: str) -> list[dict]:
        assert self.remote is not None
        remote = self.remote
        return await self._pages(
            lambda params: asyncio.to_thread(
                remote.call,
                "mcp",
                {"server": name, "method": "tools/list", "params": params},
            )
        )

    @staticmethod
    async def _pages(ask) -> list[dict]:
        tools, cursor = [], None
        while True:
            page = await ask({"cursor": cursor} if cursor else {})
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor:
                return tools

    async def discover(self) -> list[dict]:
        """Every tool of every server that answered, as pi registers it.

        A server that fails to start or to list is left out and named on this
        runner's stderr (``runner.log``). The room still opens, and the server's
        tools are absent rather than broken.
        """
        try:
            declared = stdio_servers(self.workspace)
        except ValueError as error:
            print(f"[cheese] .mcp.json is unreadable: {error}", file=sys.stderr)
            declared = {}
        stdio = {
            name: spec
            for name, spec in declared.items()
            if name not in self.remote_names
        }
        names = [*stdio, *self.remote_names]
        listings = await asyncio.gather(
            *(
                asyncio.wait_for(self._list_stdio(name, spec), LIST_TIMEOUT_S * 2)
                for name, spec in stdio.items()
            ),
            *(
                asyncio.wait_for(self._list_remote(name), LIST_TIMEOUT_S * 2)
                for name in self.remote_names
            ),
            return_exceptions=True,
        )
        tools = []
        for server, listing in zip(names, listings, strict=True):
            if isinstance(listing, BaseException):
                print(
                    f"[cheese] MCP server {server} is unavailable: "
                    f"{type(listing).__name__}: {listing}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            for tool in listing:
                name = tool_name(server, tool["name"])
                self.routes[name] = (server, tool["name"])
                tools.append(
                    {
                        "name": name,
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("inputSchema")
                        or {"type": "object", "properties": {}},
                    }
                )
        return tools

    async def call(
        self, tool: str, arguments: dict, *, call_id: str, cwd: str | None = None
    ) -> dict:
        """One tool call, with the project's hooks around it (`hooks.py`): a
        `PreToolUse` block means the call is not made, and a `PostToolUse`
        block is added to the result as feedback, since the call has happened."""
        if tool not in self.routes:
            raise ValueError(f"No MCP tool named {tool}")
        server, original = self.routes[tool]
        around = {
            "call_id": call_id,
            "root": self.workspace,
            "cwd": cwd,
            "env": self.env,
            "session_id": self.state.name,
        }
        try:
            arguments = await hooks.run("PreToolUse", tool, arguments, **around)
        except hooks.Denied as denied:
            raise RuntimeError(str(denied)) from None
        if server in self.stdio:
            result = await self.stdio[server].request(
                "tools/call",
                {"name": original, "arguments": arguments},
                timeout=CALL_TIMEOUT_S,
            )
        else:
            assert self.remote is not None
            receipt = await asyncio.to_thread(
                self.remote.call,
                "invoke",
                {"server": server, "tool": original, "args": arguments},
            )
            if "error" in receipt:
                raise RuntimeError(receipt["error"])
            result = receipt["value"]
        answer = _pi_result(result)
        try:
            await hooks.run("PostToolUse", tool, arguments, result=result, **around)
        except hooks.Denied as denied:
            answer["content"].append(
                {"type": "text", "text": f"PostToolUse hook: {denied}"}
            )
        return answer

    async def close(self) -> None:
        servers, self.stdio = list(self.stdio.values()), {}
        await asyncio.gather(
            *(server.close() for server in servers), return_exceptions=True
        )
