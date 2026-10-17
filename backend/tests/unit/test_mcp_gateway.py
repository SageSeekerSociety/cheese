"""The door a session's own MCP client reaches the project's servers through.

The rules, stated before they were written:

- only a request carrying the session's secret is answered: the session
  machine is shared, and its loopback is reachable from every session on it;
- a server the session does not have is not there;
- a tool call that did not happen (a hook said no, the machine is out of reach)
  reaches the model as the tool's answer, so it can decide what to do next;
- the hooks are the harness's: the call goes on marked as already hooked;
- a client is answered in its own protocol revision when the door speaks it,
  and in the one all three clients share when it does not; one that asks in
  the later stateless revision first is told to fall back;
- nothing but tools is asked of a server.
"""

import json
import urllib.error
import urllib.request

import pytest

from app.domain.agent.harness.driven.mcp_gateway import (
    SECRET_HEADER,
    SHARED_REVISION,
    Gateway,
)

TOOLS = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}


@pytest.fixture
def door():
    calls = []
    answers = {"invoke": {"value": {"content": [{"type": "text", "text": "ok"}]}}}

    def call(method, params):
        calls.append((method, params))
        answer = answers.get(method, TOOLS)
        if isinstance(answer, Exception):
            raise answer
        return answer

    gateway = Gateway(call, lambda: ["docs"])
    gateway.start()
    try:
        yield gateway, calls, answers
    finally:
        gateway.close()


def _post(gateway, body, *, server="docs", secret=None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{gateway.port}/mcp/{server}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            SECRET_HEADER: gateway.secret if secret is None else secret,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            text = response.read()
            return response.status, json.loads(text) if text else None
    except urllib.error.HTTPError as error:
        return error.code, None


def _rpc(method, params=None, request_id=1):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


def test_a_request_without_the_sessions_secret_is_refused(door):
    gateway, calls, _ = door

    assert _post(gateway, _rpc("tools/list"), secret="")[0] == 401
    assert _post(gateway, _rpc("tools/list"), secret="another-session")[0] == 401
    assert calls == []


def test_a_server_the_session_does_not_have_is_not_there(door):
    gateway, calls, _ = door

    assert _post(gateway, _rpc("tools/list"), server="elsewhere")[0] == 404
    assert calls == []


def test_a_tool_call_goes_on_marked_as_already_hooked(door):
    gateway, calls, _ = door

    status, answer = _post(
        gateway, _rpc("tools/call", {"name": "echo", "arguments": {"text": "hi"}})
    )

    assert status == 200
    assert answer["result"]["content"] == [{"type": "text", "text": "ok"}]
    [(method, params)] = calls
    assert method == "invoke"
    assert params["server"] == "docs"
    assert params["tool"] == "echo"
    assert params["args"] == {"text": "hi"}
    assert params["platform"] is True


def test_a_call_that_did_not_happen_is_the_tools_answer(door):
    gateway, _, answers = door
    answers["invoke"] = {"error": "PreToolUse hook: not on main"}

    status, answer = _post(gateway, _rpc("tools/call", {"name": "echo"}))

    assert status == 200
    assert answer["result"]["isError"] is True
    assert "not on main" in answer["result"]["content"][0]["text"]


def test_a_server_that_cannot_list_its_tools_says_why(door):
    gateway, _, answers = door
    answers["mcp"] = RuntimeError("npx: command not found")

    _, answer = _post(gateway, _rpc("tools/list"))

    assert "npx: command not found" in answer["error"]["message"]


@pytest.mark.parametrize(
    ("asked", "answered"),
    [
        ("2025-11-25", "2025-11-25"),
        ("2025-06-18", "2025-06-18"),
        ("2024-11-05", "2024-11-05"),
        ("2099-01-01", SHARED_REVISION),
    ],
)
def test_a_client_is_answered_in_a_revision_it_speaks(door, asked, answered):
    gateway, calls, _ = door

    _, answer = _post(gateway, _rpc("initialize", {"protocolVersion": asked}))

    assert answer["result"]["protocolVersion"] == answered
    assert calls == [], "the handshake is answered here, not upstream"


def test_a_client_asking_in_the_stateless_revision_is_told_to_fall_back(door):
    gateway, _, _ = door

    _, answer = _post(gateway, _rpc("server/discover"))

    assert answer["error"]["code"] == -32601


def test_nothing_but_tools_is_asked_of_a_server(door):
    gateway, calls, _ = door

    for method in ("resources/list", "resources/templates/list", "prompts/list"):
        _, answer = _post(gateway, _rpc(method))
        assert answer["error"]["code"] == -32601, method
    assert calls == []


def test_a_notification_is_accepted_without_an_answer(door):
    gateway, calls, _ = door

    status, answer = _post(
        gateway, {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )

    assert (status, answer) == (202, None)
    assert calls == []
