"""A deployment that declares upstream headers is sent them, and only it.

One model group the way a subscription is put in front of a pay-per-use API:
a row on the native Messages path that declares a User-Agent and a session
header (`order: 1`), the pay-per-use row behind it (`order: 2`), and a row on
the Messages-to-chat path that declares the same. The rows that declare
headers are added at runtime, as the proxy adds the rows stored in its
database. Calls go through the router the way the proxy makes them, with the
caller's headers in `proxy_server_request` and its key in the metadata.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
from litellm.router import Router
from litellm.types.router import Deployment

from cheese_upstream_headers import handler

SESSION = "0f1e2d3c-1111-2222-3333-444455556666"
DECLARED = {
    "cheese_upstream_headers": {"user-agent": "cheese-gateway/1.0"},
    "cheese_session_header": "x-opencode-session",
}
REFUSING: set[str] = set()
SEEN: list[tuple[str, dict]] = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        who = self.path.split("/")[1]
        SEEN.append((who, {k.lower(): v for k, v in self.headers.items()}))
        if who in REFUSING:
            return self.reply(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "quota"}})
        if self.path.endswith("/messages"):
            return self.reply(200, {"id": "m", "type": "message", "role": "assistant", "model": "m", "content": [{"type": "text", "text": who}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})
        return self.reply(200, {"id": "c", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": who}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


async def call(router, model, headers, first="hi", key="key-a"):
    SEEN.clear()
    response = await router.aanthropic_messages(
        model=model,
        max_tokens=8,
        messages=[{"role": "user", "content": first}],
        proxy_server_request={"headers": headers},
        litellm_metadata={"user_api_key_hash": key},
    )
    return response["content"][0]["text"], list(SEEN)


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    router = Router(
        model_list=[
            {"model_name": "messages", "litellm_params": {"model": "anthropic/m", "api_base": f"{base}/official", "api_key": "k", "order": 2}},
            {"model_name": "chat", "litellm_params": {"model": "anthropic/m", "api_base": f"{base}/official", "api_key": "k", "order": 2}},
        ]
    )
    for name, params in (
        ("messages", {"model": "anthropic/m", "api_base": f"{base}/sub"}),
        ("chat", {"model": "hosted_vllm/m", "api_base": f"{base}/sub/v1"}),
    ):
        router.upsert_deployment(
            Deployment(
                model_name=name,
                litellm_params={**params, "api_key": "k", "order": 1},
                model_info={"id": f"{name}-sub", **DECLARED},
            )
        )
    litellm.callbacks = [handler]

    for group, path in (("messages", "/sub/v1/messages"), ("chat", "/sub/v1/chat/completions")):
        REFUSING.clear()
        who, seen = await call(router, group, {"X-Claude-Code-Session-Id": SESSION})
        assert who == "sub" and len(seen) == 1, (group, who, seen)
        headers = seen[0][1]
        assert headers.get("user-agent") == "cheese-gateway/1.0", (group, headers)
        assert headers.get("x-opencode-session") == SESSION, (group, headers)

        # Without a valid session of its own the caller still sends one: the
        # same for every request of a conversation, different for another
        # conversation or another key, and never the caller's own text.
        sent = {}
        for case, caller, first, key in (
            ("none", {}, "fix the bug", "key-a"),
            ("again", {}, "fix the bug", "key-a"),
            ("invalid", {"X-Claude-Code-Session-Id": "bad value\r\nx: y"}, "fix the bug", "key-a"),
            ("other conversation", {}, "write the docs", "key-a"),
            ("other key", {}, "fix the bug", "key-b"),
        ):
            _, seen = await call(router, group, caller, first=first, key=key)
            sent[case] = seen[0][1].get("x-opencode-session")
            assert sent[case] and "bad" not in sent[case] and "fix" not in sent[case], (group, case, sent)
        assert sent["none"] == sent["again"] == sent["invalid"], (group, sent)
        assert len({sent["none"], sent["other conversation"], sent["other key"]}) == 3, (group, sent)

        # Its quota spent, the subscription row hands the request to the
        # pay-per-use row within the same call, and that row is sent neither.
        REFUSING.add("sub")
        who, seen = await call(router, group, {"X-Claude-Code-Session-Id": SESSION})
        assert who == "official", (group, who, seen)
        for upstream, headers in seen:
            if upstream == "official":
                assert "x-opencode-session" not in headers, (group, headers)
                assert "cheese-gateway" not in headers.get("user-agent", ""), (group, headers)

    server.shutdown()
    print(
        "PASS: declared headers reach a runtime-added row on Messages and chat paths, "
        "with the caller's session or one derived per conversation; a 429 falls to the "
        "order-2 row in the same request, which is sent neither"
    )


asyncio.run(main())
