"""An image no model can open reaches no model; every other image passes untouched.

The Messages case goes through the proxy's own `/v1/messages` route to a local
upstream, so a LiteLLM upgrade that stops running pre-call hooks on that route,
or rebuilds `messages` after them, fails here. The other two request shapes
call the hook directly.
"""

import asyncio
import base64
import copy
import io
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import litellm
import litellm.proxy.proxy_server as proxy_server
from litellm.router import Router
from PIL import Image

from cheese_image_guard import handler

SEEN: list[dict] = []

REPLY = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "m",
    "content": [{"type": "text", "text": "ok"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        SEEN.append(
            json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        )
        body = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def png(width=40, height=20) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


def chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data))
    )


def broken_png() -> bytes:
    """A PNG as a hand-written encoder can get wrong: a valid signature and
    header, every chunk's CRC right, and image data that does not inflate to
    the picture the header describes."""
    header = struct.pack(">IIBBBBB", 2000, 640, 8, 6, 0, 0, 0)
    pixels = zlib.compress(b"\x00" + b"\xff" * 400)  # far too few rows
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


GOOD = b64(png())
BROKEN = b64(broken_png())
NOT_AN_IMAGE = b64(b"%PDF-1.7 not a picture")
HUGE = b64(png(8001, 1))


def image(data: str, **extra) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": data},
        **extra,
    }


def messages() -> list[dict]:
    return [
        {"role": "user", "content": [{"type": "text", "text": "look"}, image(GOOD)]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "r1",
                    "name": "Read",
                    "input": {"file_path": "a.png"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "r1",
                    "content": [image(BROKEN, cache_control={"type": "ephemeral"})],
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "seen"}]},
        {
            "role": "user",
            "content": [
                image(NOT_AN_IMAGE),
                image(HUGE),
                {"type": "text", "text": "and these?"},
            ],
        },
    ]


async def through_proxy(base: str) -> None:
    router = Router(
        model_list=[
            {
                "model_name": "m",
                "litellm_params": {
                    "model": "anthropic/m",
                    "api_base": base,
                    "api_key": "k",
                },
            }
        ]
    )
    proxy_server.llm_router = router
    proxy_server.master_key = None
    litellm.callbacks = [handler]
    transport = httpx.ASGITransport(app=proxy_server.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        for _ in range(2):
            response = await client.post(
                "/v1/messages",
                json={"model": "m", "max_tokens": 8, "messages": messages()},
            )
            assert response.status_code == 200, (response.status_code, response.text)


def blocks(request: dict) -> list[dict]:
    m = request["messages"]
    return [
        m[0]["content"][1],
        m[2]["content"][0]["content"][0],
        m[4]["content"][0],
        m[4]["content"][1],
    ]


async def other_shapes() -> None:
    chat = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{GOOD}"},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{BROKEN}"},
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/a.png"},
                    },
                ],
            },
        ],
    }
    sent = copy.deepcopy(chat)
    out = await handler.async_pre_call_hook(None, None, chat, "acompletion")
    parts = out["messages"][0]["content"]
    assert parts[0] == sent["messages"][0]["content"][0], parts[0]
    assert parts[1]["type"] == "text" and "could not be decoded" in parts[1]["text"], (
        parts[1]
    )
    assert parts[2] == sent["messages"][0]["content"][2], parts[2]

    responses = {
        "model": "m",
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{BROKEN}",
                    }
                ],
            },
            {
                "type": "function_call_output",
                "call_id": "c",
                "output": [
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{GOOD}",
                    }
                ],
            },
        ],
    }
    sent = copy.deepcopy(responses)
    out = await handler.async_pre_call_hook(None, None, responses, "aresponses")
    first = out["input"][0]["content"][0]
    assert first["type"] == "input_text" and "could not be decoded" in first["text"], (
        first
    )
    assert out["input"][1] == sent["input"][1], out["input"][1]


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    await through_proxy(f"http://127.0.0.1:{server.server_address[1]}")
    server.shutdown()

    assert len(SEEN) == 2, len(SEEN)
    kept, broken, not_image, huge = blocks(SEEN[0])
    assert kept == image(GOOD), "a valid image must pass byte for byte"
    assert broken["type"] == "text" and "could not be decoded" in broken["text"], broken
    assert broken.get("cache_control") == {"type": "ephemeral"}, broken
    assert "not PNG, JPEG, GIF or WebP" in not_image["text"], not_image
    assert "8000 px" in huge["text"], huge
    assert blocks(SEEN[0]) == blocks(SEEN[1]), (
        "the replacement must not change between requests"
    )

    await other_shapes()
    print(
        "PASS: undecodable images replaced on /v1/messages and both OpenAI shapes; valid images untouched"
    )


asyncio.run(main())
