"""A call that may start work on a session's machine tells the machine whose
it is.

The rules, as a person would state them:

- the machine is told which session a tool call, a project server's call or a
  command of the session's is from, the conversation it works and the seat it
  acts under, as the session's credential says;
- what the request itself claims about who it is from is not what the machine
  is told;
- a call that starts nothing (a ping) carries no such name.
"""

import pytest

from tests.integration.test_hands_checked_moments_ago import _ask, _machine
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


async def _working(client, monkeypatch):
    """A room whose session holds its machine, and the machine's record of
    what each call it was sent said about its caller."""
    room = await _room(client)
    remote = _machine(monkeypatch)
    handed = _ask(client, room)
    assert handed.status_code == 200, handed.text
    told = []

    async def answer(target, method, params, *args, **kwargs):
        told.append((method, params.get("caller")))
        return {}

    remote.side_effect = answer
    token = handed.json()["data"]["token"]

    def call(method, params):
        return client.post(
            f"/topics/{room.topic_id}/execution/{room.lease['room_resource_id']}",
            headers={"Authorization": f"Bearer {token}"},
            json={"method": method, "params": params},
        )

    return room, call, told


async def test_a_call_that_starts_work_names_the_session_it_is_from(
    client, monkeypatch
):
    room, call, told = await _working(client, monkeypatch)
    for method, params in (
        ("invoke", {"id": "call-1", "tool": "Bash", "args": {"command": "true"}}),
        ("mcp", {"server": "tracker", "method": "tools/list"}),
        ("control", {"subtype": "shell", "operation": "start", "kind": "bash"}),
    ):
        assert call(method, params).status_code == 200, method
    expected = {
        "attachment": str(room.session_id),
        "conversation": str(room.conversation_id),
        "author": room.agent_handle,
    }
    assert told == [
        ("invoke", expected),
        ("mcp", expected),
        ("control", expected),
    ]


async def test_what_a_request_claims_about_its_caller_does_not_reach_the_machine(
    client, monkeypatch
):
    room, call, told = await _working(client, monkeypatch)
    forged = {"attachment": "someone-else", "author": "someone-else"}
    answer = call("invoke", {"id": "call-1", "tool": "Read", "caller": forged})
    assert answer.status_code == 200, answer.text
    assert told[-1][1]["attachment"] == str(room.session_id)
    assert told[-1][1]["author"] == room.agent_handle


async def test_a_ping_names_nobody(client, monkeypatch):
    _, call, told = await _working(client, monkeypatch)
    assert call("ping", {}).status_code == 200
    assert told == [("ping", None)]
