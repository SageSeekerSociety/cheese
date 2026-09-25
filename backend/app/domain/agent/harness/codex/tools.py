"""Expose the room executor's tool schemas and receipts to app-server."""

import asyncio
import importlib.resources
import json
import types
from pathlib import Path

from app.domain.agent.executor_transport import (
    MACHINE_OUT_OF_REACH,
    MachineOutOfReach,
    PlatformHost,
    RemoteClient,
)

# The executor implements these tools. The platform's own tools are not the
# executor's to list: they are the constant table (`platform_tools`).
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


#: The route a platform tool takes: not a server on the executor, the backend.
PLATFORM = "platform"


def platform_tools():
    """The platform's tool table and its runner (结论 63): the one file every
    harness serves them from. Shipped beside this module in the runner archive
    (``bundle.py``); read from the source tree when running from a checkout."""
    shipped = importlib.resources.files(__package__).joinpath("cheese.py")
    source = (
        shipped.read_text()
        if shipped.is_file()
        else (Path(__file__).resolve().parents[5] / "sandbox" / "cheese").read_text()
    )
    module = types.ModuleType("cheese_platform_tools")
    exec(compile(source, "cheese", "exec"), module.__dict__)  # noqa: S102
    return module


class RemoteTools:
    def __init__(self, target: dict):
        self.client = RemoteClient(target)
        self.routes: dict[str, tuple[str, str]] = {}
        self.platform = platform_tools()
        self.doc_versions: dict[str, int] = {}

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
        for tool in self.platform.PLATFORM_TOOLS.schemas():
            if tool["name"] in routes:
                raise ValueError(f"Duplicate executor tool: {tool['name']}")
            routes[tool["name"]] = (PLATFORM, tool["name"])
            tools.append({"type": "function", **tool})
        self.routes = routes
        return tools

    def _platform_call(self, tool: str, call_id: str, arguments: dict) -> dict:
        def invoke(payload, args):
            return self.client.call(
                "invoke", {"id": payload["id"], "tool": payload["tool"], "args": args}
            )

        host = PlatformHost(self.client, invoke, call_id, self.doc_versions)
        try:
            text = self.platform.run_platform_tool(tool, arguments, host)
        except MachineOutOfReach:
            text, success = MACHINE_OUT_OF_REACH, False
        except Exception as exc:  # noqa: BLE001 — the agent reads the reason
            text, success = str(exc), False
        else:
            success = True
        return {
            "success": success,
            "contentItems": [{"type": "inputText", "text": text}],
        }

    async def __call__(self, method: str, params: dict) -> dict:
        if method != "item/tool/call":
            raise ValueError(f"Unsupported Codex server request: {method}")
        server, tool = self.routes[params["tool"]]
        if server == PLATFORM:
            return await asyncio.to_thread(
                self._platform_call, tool, params["callId"], params["arguments"]
            )
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
