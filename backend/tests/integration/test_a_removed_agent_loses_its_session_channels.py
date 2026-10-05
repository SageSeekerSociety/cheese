"""An agent taken off a room loses the channels its session credential opens.

A session credential lasts weeks and only proves which agent it was minted for.
Once the agent is off the room, the calls that credential makes to the model,
to the room's machine, to the project's remote servers, files and inbox, and
to the platform's renderer are refused; the turn it had running is stopped
first. An agent still on the roster is served as before.
"""

import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.api.routes import llm_proxy
from app.domain.agent.harness.channel import mint_session_token
from tests.integration.conftest import (
    new_project,
    post_message,
    room_agent_seat,
    session_auth_headers,
)

REFUSED = (401, 403)


class _Upstream:
    """The model pool: answers every request it is sent."""

    def __init__(self, *a, **kw):
        pass

    def build_request(self, method, url, **kw):
        return url

    async def send(self, request, stream: bool = False):
        return _Answer()

    async def aclose(self) -> None:
        return None


class _Answer:
    status_code = 200
    headers = {"content-type": "application/json"}

    async def aiter_raw(self):
        yield b'{"ok":true}'

    async def aclose(self) -> None:
        return None


@pytest.fixture
def model_pool(monkeypatch):
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")
    # Only the model route's client: the rest of the app keeps the real one.
    monkeypatch.setattr(
        llm_proxy,
        "httpx",
        SimpleNamespace(AsyncClient=_Upstream, HTTPError=httpx.HTTPError),
    )

    async def project_key(self, project_id):
        return "sk-project"

    monkeypatch.setattr(
        "app.domain.agent.chat.ChatService.project_gateway_key", project_key
    )


@pytest.fixture
def seated(client):
    project = new_project(client, owner="alice")
    pid, room = project["id"], project["root_topic_id"]
    seat = room_agent_seat(client, room)
    return pid, room, seat, mint_session_token(pid, room, seat)


def _take_off(client, room, seat):
    r = client.delete(
        f"/topics/{room}/members/{seat}", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text


def _admission(client, token) -> dict:
    r = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _model_call(client, token) -> int:
    return client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        content=b'{"model":"m","messages":[]}',
    ).status_code


def _channels(client, pid, room, token) -> dict[str, int]:
    """Every other route a session reaches with its credential, by status."""
    headers = {"X-Cheese-Token": token}
    return {
        "executor": client.post(
            f"/topics/{room}/execution/{room}",
            json={"method": "ping"},
            headers=headers,
        ).status_code,
        "work lease": client.post(
            f"/topics/{room}/sessions/{uuid.uuid4()}/work-lease",
            json={},
            headers=headers,
        ).status_code,
        "remote mcp": client.post(
            f"/topics/{room}/mcp/tracker",
            json={"method": "tools/list"},
            headers=headers,
        ).status_code,
        "backend errors": client.post(
            "/backend-errors",
            json={"errors": [{"message": "boom"}]},
            headers=headers,
        ).status_code,
        "project files": client.get(
            f"/projects/{pid}/files", headers=headers
        ).status_code,
        "environment overview": client.get(
            f"/projects/{pid}/environment/recovery/rooms/{room}", headers=headers
        ).status_code,
        "project inbox": client.post(
            f"/projects/{pid}/alerts",
            json={"level": "silent", "kind": "change_alert", "title": "t"},
            headers=headers,
        ).status_code,
        "page check": client.post(
            "/page-check",
            json={"html": "<p>x</p>", "project": pid},
            headers=headers,
        ).status_code,
        "project mcp servers": client.get(
            f"/projects/{pid}/mcp/servers", headers=headers
        ).status_code,
    }


def test_a_seated_agent_reaches_the_model(client, model_pool, seated):
    _, _, _, token = seated
    assert _admission(client, token)["allow"] is True
    assert _model_call(client, token) == 200


def test_a_removed_agent_is_refused_the_model(client, model_pool, seated):
    _, room, seat, token = seated
    _take_off(client, room, seat)

    answer = _admission(client, token)
    assert answer["allow"] is False
    # Not a budget refusal: the proxy must not tell the agent to wait for credits.
    assert answer["reason_kind"] == "binding"
    assert _model_call(client, token) == 403


def test_a_seated_agent_is_not_refused_its_other_channels(client, seated):
    pid, room, _, token = seated
    refused = {
        name: status
        for name, status in _channels(client, pid, room, token).items()
        if status in REFUSED
    }
    assert refused == {}


def test_a_removed_agent_is_refused_its_other_channels(client, seated):
    pid, room, seat, token = seated
    _take_off(client, room, seat)
    assert set(_channels(client, pid, room, token).values()) == {403}


def test_a_turn_running_when_its_agent_is_taken_off_is_stopped_and_its_calls_refused(
    client, stub_hooks, model_pool
):
    project = new_project(client, owner="alice")
    pid, room = project["id"], project["root_topic_id"]

    def turn(topic, prompt, reply, agent=None):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="sleep 600")

    stub_hooks.emit_turn = turn
    seat = room_agent_seat(client, room)
    token = mint_session_token(pid, room, seat)
    post_message(client, room, "alice", {"content": "@芝士 跑一下测试"})
    assert _until(lambda: _working(client, room) == [seat])
    assert _admission(client, token)["allow"] is True

    _take_off(client, room, seat)

    assert _until(lambda: _working(client, room) == [])
    assert _admission(client, token)["allow"] is False
    assert _model_call(client, token) == 403
    assert _channels(client, pid, room, token)["executor"] == 403


def _working(client, room: str) -> list[str]:
    got = client.get(f"/topics/{room}", headers=session_auth_headers("alice"))
    return [
        entry["member"]
        for entry in got.json()["data"]["activity"]
        if entry["kind"] == "working"
    ]


def _until(check, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.05)
    return False
