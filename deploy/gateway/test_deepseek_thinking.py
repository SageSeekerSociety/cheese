"""A DeepSeek thinking conversation keeps working after a thinking block is lost.

The Messages case goes through the proxy's own `/v1/messages` route to a local
DeepSeek-shaped upstream, under a model group that does not carry `deepseek` in
its name: the repair must follow the provider, not the group. A LiteLLM upgrade
that stops routing here, stops running the transformation, or changes its
signature fails here.

The rest call the repair directly: a request that already carries its thinking
must come through untouched, and one that asked for thinking off must not gain
a block.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import litellm
from cheese_deepseek_thinking import PLACEHOLDER, install, repair_request
from litellm.proxy import proxy_server
from litellm.router import Router

install()

SEEN: list[tuple[str, dict]] = []

REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "deepseek-flash",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        SEEN.append(
            (
                self.path,
                json.loads(
                    self.rfile.read(int(self.headers.get("content-length") or 0))
                ),
            )
        )
        body = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def conversation() -> list[dict]:
    """A turn whose first tool call lost the thinking block Claude Code got
    back with it, and a second call that still has one."""
    return [
        {"role": "user", "content": [{"type": "text", "text": "list the files"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "a.txt"}
            ],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "read it", "signature": "sig"},
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Read",
                    "input": {"file_path": "a.txt"},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "hi"}],
        },
    ]


async def through_proxy(base: str) -> None:
    router = Router(
        model_list=[
            {
                "model_name": "cc-alias",
                "litellm_params": {
                    "model": "deepseek/deepseek-flash",
                    "api_base": f"{base}/anthropic",
                    "api_key": "k",
                },
            }
        ]
    )
    proxy_server.llm_router = router
    proxy_server.master_key = None
    litellm.callbacks = []
    transport = httpx.ASGITransport(app=proxy_server.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        response = await client.post(
            "/v1/messages",
            json={
                "model": "cc-alias",
                "max_tokens": 16,
                "messages": conversation(),
                "thinking": {"type": "adaptive"},
            },
        )
        assert response.status_code == 200, (response.status_code, response.text)

    path, body = SEEN[-1]
    assert path == "/anthropic/v1/messages", path
    first = body["messages"][1]["content"]
    assert first[0] == {
        "type": "thinking",
        "thinking": PLACEHOLDER,
        "signature": "",
    }, first[0]
    assert first[1]["type"] == "tool_use", first[1]
    # The call that still had its thinking is passed as it came.
    assert body["messages"][3]["content"][0] == {
        "type": "thinking",
        "thinking": "read it",
        "signature": "sig",
    }, body["messages"][3]["content"][0]


def other_cases() -> None:
    # An empty thinking block is filled in place, keeping its signature.
    empty = {
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": "s"},
                    {"type": "tool_use", "id": "t", "name": "Bash", "input": {}},
                ],
            }
        ],
    }
    out = repair_request(empty, "deepseek-flash")
    thinking = out["messages"][0]["content"][0]
    assert thinking["thinking"] == PLACEHOLDER and thinking["signature"] == "s", (
        thinking
    )

    # Thinking turned off: nothing is added, or DeepSeek would refuse the block.
    disabled = {
        "thinking": {"type": "disabled"},
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}
                ],
            }
        ],
    }
    out = repair_request(disabled, "deepseek-flash")
    assert out["messages"] == disabled["messages"], out["messages"]

    # Every assistant message is checked, not only one that called a tool:
    # DeepSeek's check is on the conversation, and a turn that never thought
    # is missing the same block.
    plain = {
        "messages": [
            {"role": "assistant", "content": [{"type": "text", "text": "done"}]}
        ],
    }
    out = repair_request(plain, "deepseek-flash")
    assert out["messages"][0]["content"][0] == {
        "type": "thinking",
        "thinking": PLACEHOLDER,
        "signature": "",
    }, out["messages"]
    assert out["messages"][0]["content"][1]["text"] == "done", out["messages"]

    # A user message is not touched.
    user = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    out = repair_request(user, "deepseek-flash")
    assert out["messages"] == user["messages"], out["messages"]


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        await through_proxy(base)
        other_cases()
    finally:
        server.shutdown()
    print(
        "PASS: a DeepSeek turn whose thinking block was lost gets a placeholder, "
        "under an alias model group, and a healthy turn or thinking-off request "
        "is left alone"
    )


asyncio.run(main())
