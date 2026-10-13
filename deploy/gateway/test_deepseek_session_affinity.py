"""A conversation on deepseek-flash stays on one RUC key, so on one RUC node and its prompt cache.

Builds the router from config.yaml's deepseek-flash rows and router_settings, pointed at
a local upstream whose RUC keys can be made busy (429) or failing (500), and calls it the
way the proxy does, with the Claude Code session in the request metadata.
"""

import asyncio
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import litellm
import yaml

RUC_BASE = "os.environ/RUC_DEEPSEEK_API_BASE"
STATE = {"busy": set(), "failing": False}
SEEN: list[str] = []


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        who = self.path.split("/")[1]
        SEEN.append(who)
        if who != "official" and who in STATE["busy"]:
            return self.reply(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "busy"}})
        if who != "official" and STATE["failing"]:
            return self.reply(500, {"type": "error", "error": {"type": "api_error", "message": "down"}})
        if self.path.endswith("/v1/messages"):
            return self.reply(200, {"id": "m", "type": "message", "role": "assistant", "model": "m", "content": [{"type": "text", "text": who}], "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}})
        return self.reply(200, {"id": "c", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": who}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def router(base):
    """deepseek-flash as config.yaml declares it, each RUC key and DeepSeek's API
    answering at its own path of the local upstream."""
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    rows = []
    for entry in copy.deepcopy(config["model_list"]):
        if entry["model_name"] != "deepseek-flash":
            continue
        params = entry["litellm_params"]
        if params.get("api_base") == RUC_BASE:
            name = "k" + params["api_key"].rsplit("_", 1)[1]
        else:
            name = "official"
        params.update(api_base=f"{base}/{name}", api_key=name)
        rows.append(entry)
    return litellm.Router(model_list=rows, **(config.get("router_settings") or {}))


async def ask(r, session):
    response = await r.acompletion(
        model="deepseek-flash",
        messages=[{"role": "user", "content": "hi"}],
        metadata={"session_id": session},
    )
    return response.choices[0].message.content


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    r = router(f"http://127.0.0.1:{server.server_address[1]}")

    # One conversation, one key.
    answers = {await ask(r, "conversation-1") for _ in range(12)}
    assert len(answers) == 1 and next(iter(answers)).startswith("k"), answers
    pinned = next(iter(answers))

    # Conversations are spread over the keys rather than all pinned to one.
    keys = {await ask(r, f"conversation-{n}") for n in range(2, 30)}
    assert len(keys) > 3 and "official" not in keys, keys

    # Its key past its limit: the conversation stays on RUC.
    STATE["busy"] = {pinned}
    moved = await ask(r, "conversation-1")
    assert moved.startswith("k") and moved != pinned, moved
    STATE["busy"] = set()

    # A conversation that began while RUC was down is not kept on DeepSeek's API.
    STATE["failing"] = True
    assert await ask(r, "conversation-down") == "official"
    STATE["failing"] = False
    back = [await ask(r, "conversation-down") for _ in range(3)]
    assert all(a.startswith("k") for a in back), back

    server.shutdown()
    print("PASS: a deepseek-flash conversation keeps its RUC key; busy or down RUC still handled")


if __name__ == "__main__":
    asyncio.run(main())
