"""The session tool creates one complete question group over the platform HTTP API.

The backend is a loopback HTTP fixture; the actual shipped CLI, request encoder,
credential headers and response reader run unchanged. Group transactions and
executor recovery belong to the backend's integration tests.
"""

import copy
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

GROUP = "c319a264-4948-4255-87b7-542999e073d8"


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
        "allow_other": True,
        "reject_option": False,
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
            members = [
                f"question-{i}" for i in range(len(payload.get("questions", [])))
            ]
            body = json.dumps(
                {
                    "data": {
                        "group": {
                            "topic_id": "room",
                            "asked_by": "original-agent",
                            "id": payload.get("ask_group", GROUP),
                            "members": members,
                            "total": len(members),
                        },
                        "blocks": [{"id": member} for member in members],
                        "settlement": None,
                        "receipt": None,
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


@pytest.mark.parametrize("total", [1, 8])
def test_complete_group_is_one_platform_request_with_object_options(platform, total):
    cli, host, calls, _ = platform
    questions = [_question(i) for i in range(total)]
    original = copy.deepcopy(questions)
    result = cli.run_platform_tool(
        "cheese_ask",
        {"questions": questions, "ask_group": GROUP, "author": "spoof"},
        host,
    )

    assert len(calls) == 1
    path, body, headers = calls[0]
    assert path == "/topics/room/asks"
    assert body == {"questions": questions, "ask_group": GROUP}
    assert questions == original
    assert headers["X-Cheese-Token"] == "fixture-agent-credential"
    assert headers["X-Cheese-Turn"] == "original-turn"
    created = json.loads(result)
    assert created["group"]["id"] == GROUP
    assert created["group"]["members"] == [f"question-{i}" for i in range(total)]
    assert [block["id"] for block in created["blocks"]] == created["group"]["members"]
    assert created["settlement"] is None
    assert created["receipt"] is None


def test_optional_group_id_and_answer_permissions_are_left_to_the_backend(platform):
    cli, host, calls, _ = platform
    question = _question()
    del question["allow_other"]
    del question["reject_option"]
    result = cli.run_platform_tool("cheese_ask", {"questions": [question]}, host)
    assert calls[0][1] == {"questions": [question]}
    assert json.loads(result)["group"]["id"] == GROUP


@pytest.mark.parametrize(
    "arguments",
    [
        {"question": "旧入口", "option": ["A", "B"]},
        {"questions": []},
        {"questions": [_question(i) for i in range(9)]},
        {"questions": [{**_question(), "question": " "}]},
        {"questions": [{**_question(), "options": ["A", "B"]}]},
        {"questions": [{**_question(), "options": [{"text": "A"}]}]},
        {
            "questions": [
                {**_question(), "options": [{"text": str(i)} for i in range(4)]}
            ]
        },
        {"questions": [{**_question(), "options": [{"text": " "}, {"text": "B"}]}]},
        {"questions": [{**_question(), "allow_other": "false"}]},
        {"questions": [{**_question(), "reject_option": "false"}]},
        {"questions": [_question()], "ask_group": " "},
        {"questions": [_question(), {**_question(), "options": ["A", "B"]}]},
    ],
)
def test_invalid_group_never_publishes_even_its_valid_first_question(
    platform, arguments
):
    cli, host, calls, _ = platform
    with pytest.raises(cli.PlatformToolError):
        cli.run_platform_tool("cheese_ask", arguments, host)
    assert calls == []


def test_explicit_retry_preserves_group_id_and_payload_without_automatic_replay(
    platform,
):
    cli, host, calls, failures = platform
    arguments = {"questions": [_question()], "ask_group": GROUP}
    failures.append(503)
    with pytest.raises(cli.PlatformHTTPError) as failed:
        cli.run_platform_tool("cheese_ask", arguments, host)
    assert failed.value.status == 503
    assert len(calls) == 1
    result = cli.run_platform_tool("cheese_ask", arguments, host)
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1] == arguments
    assert json.loads(result)["group"]["id"] == GROUP


def test_missing_creation_response_is_not_reported_as_a_created_question():
    cli = _load()

    class EmptyResponse:
        environ = {"CHEESE_TOPIC": "room"}

        def request(self, _):
            return {"data": None}

    with pytest.raises(cli.PlatformToolError):
        cli.run_platform_tool(
            "cheese_ask", {"questions": [_question()]}, EmptyResponse()
        )
