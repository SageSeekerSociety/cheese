"""A session's MCP client, as far as a test needs one: tool lists and tool
calls at a gateway (`driven/mcp_gateway.py`), the way Claude Code, Codex and
pi reach it."""

import json
import urllib.request

from app.domain.agent.harness.driven.mcp_gateway import SECRET_HEADER, Gateway


def rpc(gateway: Gateway, server: str, method: str, params: dict | None = None):
    request = urllib.request.Request(
        gateway.url(server),
        data=json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        ).encode(),
        headers={"Content-Type": "application/json", SECRET_HEADER: gateway.secret},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        answer = json.loads(response.read())
    if "error" in answer:
        raise RuntimeError(answer["error"]["message"])
    return answer["result"]


def tool_names(gateway: Gateway, server: str) -> list[str]:
    return [tool["name"] for tool in rpc(gateway, server, "tools/list")["tools"]]


def call_tool(gateway: Gateway, server: str, tool: str, arguments: dict) -> dict:
    return rpc(gateway, server, "tools/call", {"name": tool, "arguments": arguments})


def text_of(result: dict) -> str:
    return "\n".join(
        part.get("text", "") for part in result["content"] if part["type"] == "text"
    )
