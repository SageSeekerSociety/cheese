"""A deterministic OpenAI Responses endpoint, for driving a real Codex.

The Codex counterpart of `completions_fixture`: each request is answered with
the next step of a script, a tool call (``{"tool": name, "arguments": {...}}``)
or text (``{"text": ...}``), and a callable step is asked with the request body.
Past the end of the script every request is answered with text, so a turn
always ends. Codex reaches the fixture as a provider in its ``config.toml``
(`config`), with a model whose tools are plain function calls.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "gpt-5.3-codex"


class Responses:
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
                data = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in _events(index, step)
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def config(self) -> str:
        """``config.toml`` for a Codex that answers to this fixture."""
        return (
            f'model = "{MODEL}"\nmodel_provider = "fixture"\n'
            '[model_providers.fixture]\nname = "fixture"\n'
            f'base_url = "http://127.0.0.1:{self.server.server_port}/v1"\n'
            'wire_api = "responses"\nrequires_openai_auth = false\n'
            "[analytics]\nenabled = false\n"
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def _events(index: int, step: dict) -> list[dict]:
    if "tool" in step:
        item = {
            "id": f"fc_{index}",
            "type": "function_call",
            "call_id": f"call_{index}",
            "name": step["tool"],
            "arguments": json.dumps(step.get("arguments", {})),
            "status": "completed",
        }
    else:
        item = {
            "id": f"msg_{index}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {
                    "type": "output_text",
                    "text": step.get("text", "DONE"),
                    "annotations": [],
                }
            ],
        }
    response = {
        "id": f"resp_{index}",
        "object": "response",
        "status": "completed",
        "output": [item],
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }
    return [
        {
            "type": "response.created",
            "response": {**response, "status": "in_progress", "output": []},
        },
        {"type": "response.output_item.added", "output_index": 0, "item": item},
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
