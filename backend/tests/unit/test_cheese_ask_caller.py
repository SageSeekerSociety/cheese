"""The session tool posts its questions in one platform request and says to stop.

The backend is a loopback HTTP fixture; the actual shipped CLI, request encoder,
credential headers and response reader run unchanged. What the backend does
with the questions belongs to `tests/integration/test_ask_as_messages.py`.
"""

import importlib.util
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


def _load():
    source = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
    loader = SourceFileLoader("cheese_ask_caller", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _question(index=0):
    return {
        "question": f"第 {index + 1} 项采用哪个方案？",
        "options": [
            {"text": "方案 A", "explain": "统一处理"},
            {"text": "方案 B", "explain": "分别处理"},
        ],
    }


@pytest.fixture
def platform():
    calls = []
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, payload, dict(self.headers)))
            status = failures.pop(0) if failures else 200
            body = json.dumps(
                {
                    "data": {
                        "blocks": [
                            {"id": f"question-{i}"}
                            for i in range(len(payload.get("questions", [])))
                        ],
                        "request_id": payload.get("request_id"),
                    }
                }
                if status == 200
                else {"error": {"message": "temporarily unavailable"}}
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}
    )
    thread.start()
    cli = _load()
    host = cli.LocalHost(
        {
            "CHEESE_API": f"http://127.0.0.1:{server.server_port}",
            "CHEESE_TOPIC": "room",
            "CHEESE_TOKEN": "fixture-agent-credential",
            "CHEESE_TURN": "original-turn",
        }
    )
    try:
        yield cli, host, calls, failures
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.parametrize("total", [1, 3])
def test_the_questions_go_out_in_one_request_and_the_agent_is_told_to_stop(
    platform, total
):
    cli, host, calls, _ = platform
    questions = [_question(i) for i in range(total)]
    result = cli.run_platform_tool(
        "cheese_ask", {"questions": questions, "author": "spoof"}, host
    )

    assert len(calls) == 1
    path, body, headers = calls[0]
    assert path == "/topics/room/asks"
    assert body["questions"] == questions
    assert "author" not in body
    uuid.UUID(body["request_id"])
    assert headers["X-Cheese-Token"] == "fixture-agent-credential"
    assert headers["X-Cheese-Turn"] == "original-turn"
    # The agent learns where its questions are, how to retry, and that it does
    # not wait: the answer starts its next turn.
    for i in range(total):
        assert f"question-{i}" in result
    assert body["request_id"] in result
    assert "结束这一轮" in result


@pytest.mark.parametrize(
    "arguments",
    [
        {"question": "旧入口", "option": ["A", "B"]},
        {"questions": []},
        {"questions": [_question(i) for i in range(4)]},
        {"questions": [{**_question(), "question": " "}]},
        {"questions": [{**_question(), "options": ["A", "B"]}]},
        {"questions": [{**_question(), "options": [{"text": "A"}]}]},
        {
            "questions": [
                {**_question(), "options": [{"text": str(i)} for i in range(4)]}
            ]
        },
        {"questions": [{**_question(), "options": [{"text": " "}, {"text": "B"}]}]},
        {"questions": [_question(), {**_question(), "options": ["A", "B"]}]},
        {"questions": [_question()], "request_id": "not-a-uuid"},
    ],
)
def test_invalid_questions_never_publish_even_a_valid_first_one(platform, arguments):
    cli, host, calls, _ = platform
    with pytest.raises((cli.PlatformToolError, ValueError)):
        cli.run_platform_tool("cheese_ask", arguments, host)
    assert calls == []


def test_a_retry_with_the_returned_request_id_sends_the_same_request(platform):
    cli, host, calls, failures = platform
    request_id = str(uuid.uuid4())
    arguments = {"questions": [_question()], "request_id": request_id}
    failures.append(503)
    with pytest.raises(cli.PlatformHTTPError) as failed:
        cli.run_platform_tool("cheese_ask", arguments, host)
    assert failed.value.status == 503
    cli.run_platform_tool("cheese_ask", arguments, host)
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert calls[1][1]["request_id"] == request_id


def test_missing_creation_response_is_not_reported_as_a_question_asked():
    cli = _load()

    class EmptyResponse:
        environ = {"CHEESE_TOPIC": "room"}

        def request(self, _):
            return {"data": None}

    with pytest.raises(cli.PlatformToolError):
        cli.run_platform_tool(
            "cheese_ask", {"questions": [_question()]}, EmptyResponse()
        )
