"""An app's HTML documents get the runtime bridge on their way through the relay.

The right-hand panel's little browser reads the page's address, steps back and
forward, and keeps a static snapshot through that bridge; a room file already had
it, an app did not. The real hub and relay run here against an in-memory tunnel.
"""

import asyncio
import uuid
from types import SimpleNamespace

from starlette.requests import Request

from app.api import preview_relay
from app.api.preview_runtime import RUNTIME_TAG
from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub

PAGE = b"<!doctype html><html><head><title>t</title></head><body>hi</body></html>"


class _App:
    """A tunnel whose app answers every request with one fixed response."""

    def __init__(self, status: int, headers: list[list[str]], chunks: list[bytes]):
        self.machine = None
        self.status, self.headers, self.chunks = status, headers, chunks

    async def send_bytes(self, data):
        op, sid, _ = wire.decode(data)
        if op == wire.OP_REQ:
            meta = {"status": self.status, "headers": self.headers}
            self.machine.on_frame(
                wire.encode(wire.OP_RESP, sid, wire.encode_meta(meta, b""))
            )
            for chunk in self.chunks:
                self.machine.on_frame(wire.encode(wire.OP_DATA, sid, chunk))
            self.machine.on_frame(wire.encode(wire.OP_END, sid))


async def _relay(app: _App, method: str = "GET") -> tuple[int, dict, bytes]:
    hub = PreviewHub()
    topic = uuid.uuid4()
    app.machine = hub.attach(topic, "seat", app)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": method,
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"sec-fetch-mode", b"navigate"), (b"accept", b"text/html")],
        "scheme": "https",
        "server": ("test", 443),
        "client": ("browser", 1234),
        "app": SimpleNamespace(state=SimpleNamespace(preview_hub=hub)),
    }
    done = asyncio.Event()
    sent_body = False

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    response = await preview_relay.relay_http(topic, "seat", Request(scope, receive))
    messages = []

    async def send(message):
        messages.append(message)

    await asyncio.wait_for(response(scope, receive, send), 2)
    done.set()
    start = messages[0]
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in messages[1:])
    return start["status"], headers, body


async def test_an_app_page_carries_the_runtime_and_drops_its_stale_length():
    app = _App(
        200,
        [
            ["content-type", "text/html; charset=utf-8"],
            ["content-length", str(len(PAGE))],
        ],
        [PAGE[:20], PAGE[20:]],
    )
    status, headers, body = await _relay(app)
    assert status == 200
    assert body == PAGE.replace(b"<head>", b"<head>" + RUNTIME_TAG)
    # 插了一行，原来的长度不再对；留着它浏览器会截掉页尾。
    assert headers.get("content-length") in (None, str(len(body)))


async def test_non_html_compressed_and_failed_answers_pass_through_untouched():
    for status, headers in (
        (200, [["content-type", "application/javascript"]]),
        (200, [["content-type", "text/html"], ["content-encoding", "gzip"]]),
        (404, [["content-type", "text/html"]]),
    ):
        app = _App(status, headers, [PAGE])
        _, _, body = await _relay(app)
        assert body == PAGE, (status, headers)


def test_only_a_plain_200_html_get_is_injected():
    html = [("content-type", "text/html")]
    assert preview_relay._injects("GET", 200, html)
    assert preview_relay._injects(
        "GET", 200, [("Content-Type", "application/xhtml+xml")]
    )
    assert preview_relay._injects("GET", 200, [*html, ("content-encoding", "identity")])
    assert not preview_relay._injects("HEAD", 200, html)
    assert not preview_relay._injects("POST", 200, html)
    assert not preview_relay._injects("GET", 304, html)
    assert not preview_relay._injects("GET", 200, [*html, ("content-encoding", "br")])
    assert not preview_relay._injects("GET", 200, [("content-type", "text/plain")])


async def test_a_long_page_streams_past_the_prefix_unchanged():
    tail = b"x" * (preview_relay._INJECT_PREFIX_BYTES * 2)

    async def chunks():
        yield b"<html><head>"
        for i in range(0, len(tail), 4096):
            yield tail[i : i + 4096]

    out = [part async for part in preview_relay._with_runtime(chunks())]
    joined = b"".join(out)
    assert joined == b"<html><head>" + RUNTIME_TAG + tail
    # 前缀之后的块原样逐块往下发，不等整页收完。
    assert len(out) > 2
