"""Expose the room executor's tool schemas and receipts to app-server."""

import asyncio
import json

from app.domain.agent.executor_transport import RemoteClient

# The executor implements these tools; other native MCP tools belong to its CLI.
NATIVE_TOOLS = {
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Glob",
    "Grep",
    "NotebookEdit",
    "TaskOutput",
    "TaskStop",
}


class RemoteTools:
    def __init__(self, target: dict):
        self.client = RemoteClient(target)
        self.routes: dict[str, tuple[str, str]] = {}

    async def discover(self, servers: list[str]) -> list[dict]:
        tools = []
        routes = {}
        for server in ["native", *servers]:
            cursor = None
            while True:
                result = await asyncio.to_thread(
                    self.client.call,
                    "mcp",
                    {
                        "server": server,
                        "method": "tools/list",
                        "params": {"cursor": cursor} if cursor else {},
                    },
                )
                for tool in result["tools"]:
                    original = tool["name"]
                    if server == "native" and original not in NATIVE_TOOLS:
                        continue
                    name = (
                        original if server == "native" else f"mcp__{server}__{original}"
                    )
                    if name in routes:
                        raise ValueError(f"Duplicate executor tool: {name}")
                    routes[name] = (server, original)
                    tools.append(
                        {
                            "type": "function",
                            "name": name,
                            "description": tool.get("description", ""),
                            "inputSchema": tool["inputSchema"],
                        }
                    )
                cursor = result.get("nextCursor")
                if not cursor:
                    break
        self.routes = routes
        return tools

    async def __call__(self, method: str, params: dict) -> dict:
        if method != "item/tool/call":
            raise ValueError(f"Unsupported Codex server request: {method}")
        server, tool = self.routes[params["tool"]]
        receipt = await asyncio.to_thread(
            self.client.call,
            "invoke",
            {
                "id": params["callId"],
                "server": server,
                "tool": tool,
                "args": params["arguments"],
            },
        )
        if "error" in receipt:
            return {
                "success": False,
                "contentItems": [{"type": "inputText", "text": receipt["error"]}],
            }
        value = receipt["value"]
        if server == "native":
            if isinstance(value, dict) and value.get("type") == "image":
                file = value["file"]
                return {
                    "success": True,
                    "contentItems": [
                        {
                            "type": "inputImage",
                            "imageUrl": f"data:{file['type']};base64,{file['base64']}",
                        }
                    ],
                }
            return {
                "success": True,
                "contentItems": [
                    {"type": "inputText", "text": json.dumps(value, ensure_ascii=False)}
                ],
            }
        content = []
        for item in value.get("content", []):
            if item["type"] == "text":
                content.append({"type": "inputText", "text": item["text"]})
            elif item["type"] in ("image", "audio"):
                image = item["type"] == "image"
                url = f"data:{item['mimeType']};base64,{item['data']}"
                content.append(
                    {
                        "type": "inputImage" if image else "inputAudio",
                        "imageUrl" if image else "audioUrl": url,
                    }
                )
            else:
                # Preserve structured resources that have no app-server content type.
                content.append({"type": "inputText", "text": json.dumps(item)})
        if value.get("structuredContent") is not None:
            content.append(
                {"type": "inputText", "text": json.dumps(value["structuredContent"])}
            )
        return {"success": not value.get("isError", False), "contentItems": content}
