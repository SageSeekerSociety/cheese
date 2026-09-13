"""Deterministic Messages API for acceptance of the real Claude Code terminal."""

import datetime as dt
import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def log(path, value):
    with path.open("a") as stream:
        stream.write(
            json.dumps({"time": dt.datetime.now(dt.UTC).isoformat(), **value}) + "\n"
        )
        stream.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.reply({})

    def reply(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        body = json.loads(raw or b"{}")
        if self.path == "/topics/fixture/messages":
            assert self.headers.get("X-Cheese-Token") == "fixture-place-token"
            self.server.state.setdefault("publications", []).append(body)
            dump(
                self.server.state["dir"] / "publications.json",
                self.server.state["publications"],
            )
            return self.reply({"data": body})
        if self.path.startswith("/hook"):
            log(self.server.state["dir"] / "hooks.jsonl", {"payload": body})
            return self.reply({"code": 200})
        if "/count_tokens" in self.path:
            return self.reply({"input_tokens": 100})
        if "/messages" not in self.path:
            return self.reply({})
        title = "Write the title in the predominant language" in json.dumps(
            body.get("messages", [])
        )
        quota = body.get("messages") == [{"role": "user", "content": "quota"}]
        if title or quota:
            return self.reply(
                {
                    "id": "msg_background",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [{"type": "text", "text": "Execution test"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            )
        state = self.server.state
        index = len(state["requests"])
        action = state["actions"][index] if index < len(state["actions"]) else None
        if callable(action):
            try:
                action = action(body)
            except Exception as exc:
                state["error"] = str(exc)
                dump(state["dir"] / "failed-request.json", body)
                return self.reply(
                    {"error": {"type": "invalid_request_error", "message": str(exc)}}
                )
        state["requests"].append(body)
        dump(state["dir"] / f"request-{index + 1}.json", body)
        log(
            state["dir"] / "progress.jsonl",
            {"item": index + 1, "status": "received", "bytes": len(raw)},
        )
        dump(state["dir"] / f"action-{index + 1}.json", action)
        block = (
            {"type": "tool_use", "id": f"toolu_acceptance_{index}", **action}
            if action
            else {"type": "text", "text": "ACCEPTANCE_DONE"}
        )
        message = {
            "id": f"msg_{index}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
        events = [
            ("message_start", {"type": "message_start", "message": message}),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {**block, "input": {}}
                    if action
                    else {"type": "text", "text": ""},
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(action["input"]),
                    }
                    if action
                    else {"type": "text_delta", "text": "ACCEPTANCE_DONE"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": "tool_use" if action else "end_turn",
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": 20},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        encoded = "".join(
            f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)
