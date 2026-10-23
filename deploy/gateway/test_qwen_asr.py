"""A chat request carrying audio reaches DashScope as a Qwen-Audio ASR call and
comes back as a chat reply holding the transcript: the system message rides
along as context, the hotword table as ``vocabulary``, the audio length is
billed as 25 prompt tokens a second at the row's price, and an upstream
refusal reaches the caller as an error rather than an empty transcript.

The configured row, called through the router against a local upstream.
"""

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import litellm
import yaml
from litellm.router import Router

from cheese_qwen_asr import handler

SEEN: list[dict] = []
REPLY = {"status": 200}


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)))
        SEEN.append({"path": self.path, "auth": self.headers.get("authorization"), "body": body})
        if REPLY["status"] != 200:
            payload = {"code": "InvalidParameter", "message": "bad audio"}
        else:
            payload = {
                "output": {"text": "@芝士ni 帮我看一下"},
                "usage": {"duration": 3.2},
                "request_id": "r1",
            }
        data = json.dumps(payload).encode()
        self.send_response(REPLY["status"])
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def configured_row() -> dict:
    with open("/opt/cheese-gateway/config.yaml") as f:
        config = yaml.safe_load(f)
    providers = config["litellm_settings"]["custom_provider_map"]
    assert {"provider": "qwen-asr", "custom_handler": "cheese_qwen_asr.handler"} in providers
    with open("/opt/cheese-gateway/test_rows.yaml") as f:
        rows = yaml.safe_load(f)["model_list"]
    (row,) = [r for r in rows if r["litellm_params"]["model"].startswith("qwen-asr/")]
    return row


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    row = configured_row()
    params = {**row["litellm_params"], "api_base": f"http://127.0.0.1:{server.server_port}", "api_key": "sk-test"}
    litellm.custom_provider_map = [{"provider": "qwen-asr", "custom_handler": handler}]
    litellm.drop_params = True
    router = Router(model_list=[{"model_name": row["model_name"], "litellm_params": params}])

    audio = "data:audio/wav;base64," + base64.b64encode(b"RIFF....WAVE").decode()
    messages = [
        {"role": "system", "content": "房间成员：芝士ni、彭文博"},
        {"role": "user", "content": [{"type": "input_audio", "input_audio": {"data": audio, "format": "wav"}}]},
    ]
    reply = await router.acompletion(
        model=row["model_name"], messages=messages, vocabulary={"芝士ni": 5}
    )

    (call,) = SEEN
    assert call["path"].endswith("/api/v1/services/aigc/multimodal-generation/generation"), call["path"]
    assert call["auth"] == "Bearer sk-test"
    body = call["body"]
    assert body["model"] == params["model"].split("/", 1)[1]
    turns = body["input"]["messages"]
    assert turns[0]["content"] == [{"type": "input_text", "text": "房间成员：芝士ni、彭文博"}], turns
    assert turns[-1]["content"] == [{"type": "input_audio", "input_audio": {"data": audio}}], turns
    assert body["parameters"]["format"] == "wav"
    assert body["parameters"]["vocabulary"] == {"芝士ni": 5}, body["parameters"]

    assert reply.choices[0].message.content == "@芝士ni 帮我看一下"
    # 3.2 s is billed as 4 whole seconds, 25 tokens each.
    assert reply.usage.prompt_tokens == 100, reply.usage
    cost = reply._hidden_params.get("response_cost")
    assert cost and abs(cost - 100 * params["input_cost_per_token"]) < 1e-12, cost

    REPLY["status"] = 400
    try:
        await router.acompletion(model=row["model_name"], messages=messages, num_retries=0)
    except Exception as exc:  # noqa: BLE001 - any raised error is the point
        assert "bad audio" in str(exc), exc
    else:
        raise AssertionError("an upstream refusal came back as a transcript")

    no_audio = [{"role": "user", "content": "hello"}]
    try:
        await router.acompletion(model=row["model_name"], messages=no_audio, num_retries=0)
    except Exception as exc:  # noqa: BLE001
        assert "input_audio" in str(exc), exc
    else:
        raise AssertionError("a request without audio reached the upstream")
    assert len(SEEN) == 2, "the request without audio must not be sent upstream"
    server.shutdown()
    print("qwen_asr ok")


asyncio.run(main())
