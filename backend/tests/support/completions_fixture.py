"""A deterministic OpenAI Chat Completions endpoint, for driving a real pi.

pi reaches its model through `openai-completions` (`pi/device_launch.provider`),
so a scripted turn is a script of completions: each request the model endpoint
receives is answered with the next step, in order. A step is a tool call
(``{"tool": name, "arguments": {...}}``) or plain text (``{"text": ...}``); a
callable step is asked with the request body and answers with one of those,
which is how a script looks at what the model was just told. Past the end of the
script every request is answered with text, so a turn always ends.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Completions:
    def __init__(self, steps: list):
        self.steps = steps
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with fixture.lock:
                    index = len(fixture.requests)
                    fixture.requests.append(body)
                step = (
                    fixture.steps[index]
                    if index < len(fixture.steps)
                    else {"text": "DONE"}
                )
                if callable(step):
                    step = step(body)
                data = (
                    "".join(
                        f"data: {json.dumps(chunk)}\n\n"
                        for chunk in _chunks(index, step)
                    ).encode()
                    + b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def _chunks(index: int, step: dict) -> list[dict]:
    def chunk(delta: dict, finish: str | None = None, **extra) -> dict:
        return {
            "id": f"chatcmpl-{index}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "fixture",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            **extra,
        }

    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if "tool" in step:
        call = {
            "index": 0,
            "id": f"call_{index}",
            "type": "function",
            "function": {
                "name": step["tool"],
                "arguments": json.dumps(step.get("arguments", {})),
            },
        }
        return [
            chunk({"role": "assistant", "content": None, "tool_calls": [call]}),
            chunk({}, "tool_calls", usage=usage),
        ]
    return [
        chunk({"role": "assistant", "content": step.get("text", "DONE")}),
        chunk({}, "stop", usage=usage),
    ]
