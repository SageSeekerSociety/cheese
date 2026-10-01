"""项目的 MCP 服务器在 pi 这边：开场时列出它们的工具，调用时送回去。

pi has no MCP client of its own, so the runner is one, and each kind of server
is reached where the other harnesses reach it, through the room's machine
client (`machine.py`):

- **the checkout's stdio servers** (``.mcp.json``) and **the teammate's type's**
  (``agent_mcp``) run on the room's machine, started by its executor with the
  command, arguments, environment and working directory a native session there
  would use. The executor runs the project's hooks around each call itself.
- **remote servers** (an entry with a ``url``) are the platform's to call: every
  request goes to ``/topics/{id}/mcp/{name}``, which attaches the server's
  credential, so nothing pi can read ever holds it. Their hooks are the
  machine's, run around the call once the session has a machine.

Which servers a session lists is the client's to say (`session_servers`): a
session started before it had a machine lists only the remote ones, and is
started again on the machine once it has one (the channel's launch contract).

A tool is registered with pi as ``mcp__<server>__<tool>``, the name Claude Code
and Codex give it, and the tool list is read once, when the session starts, as
the other harnesses read it.
"""

import asyncio
import json
import sys
import uuid

#: Listing runs before the runner binds its socket, and the room's first turn
#: waits on that socket; servers are listed together, so one that hangs costs
#: this much and not the room.
LIST_TIMEOUT_S = 60.0


def tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


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

    def __init__(self, machine, session: str = ""):
        self.machine = machine
        # The executor keeps every call it was asked under its id, for good, and
        # answers an id it has seen with what it answered then. pi numbers its
        # calls per session, so the id the executor sees names the session too.
        self.session = session or uuid.uuid4().hex
        #: pi's tool name -> (server, the server's own tool name)
        self.routes: dict[str, tuple[str, str]] = {}

    async def _list(self, server: str) -> list[dict]:
        client = self.machine.client
        tools, cursor = [], None
        while True:
            page = await asyncio.to_thread(
                client.call,
                "mcp",
                {
                    "server": server,
                    "method": "tools/list",
                    "params": {"cursor": cursor} if cursor else {},
                },
            )
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
            servers = await asyncio.to_thread(self.machine.servers)
        except Exception as error:  # noqa: BLE001 — the room still opens
            print(f"[cheese] MCP servers unavailable: {error}", file=sys.stderr)
            return []
        listings = await asyncio.gather(
            *(asyncio.wait_for(self._list(name), LIST_TIMEOUT_S) for name in servers),
            return_exceptions=True,
        )
        tools = []
        for server, listing in zip(servers, listings, strict=True):
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

    def _call(self, server: str, tool: str, arguments: dict, call_id: str) -> dict:
        call_id = f"pi-{self.session}-{call_id}"
        client = self.machine.client
        if server not in client.remote_servers():
            # The machine's own server: its executor runs the hooks around it.
            return client.call(
                "invoke",
                {
                    "id": call_id,
                    "server": server,
                    "tool": tool,
                    "args": arguments,
                },
            )
        if not self.machine.taken:
            # The project's hooks are the machine's, and a remote server needs
            # none: a session that has not needed its machine yet has no hooks
            # to run, and is not made to take one for this call.
            return client.remote_mcp(
                "invoke", {"server": server, "tool": tool, "args": arguments}
            )
        return client.remote_call_with_hooks(call_id, server, tool, arguments)

    async def call(self, tool: str, arguments: dict, *, call_id: str) -> dict:
        """One tool call, with the project's hooks around it: a `PreToolUse`
        block means the call is not made, and a `PostToolUse` block is added to
        the result as feedback, since the call has happened."""
        if tool not in self.routes:
            raise ValueError(f"No MCP tool named {tool}")
        server, original = self.routes[tool]
        receipt = await asyncio.to_thread(
            self._call, server, original, arguments, call_id
        )
        if "error" in receipt:
            raise RuntimeError(receipt["error"])
        return _pi_result(receipt["value"])
