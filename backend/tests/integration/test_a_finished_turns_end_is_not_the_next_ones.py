"""A page that opens the room just after a turn ended reads its own turn's end.

The room replays an open turn's frames to whoever subscribes, so a page that
reconnects mid-turn sees what it missed. A turn that has already said ``done``
must not be open by then: a page that connects for the next message would be
handed the last turn's ``done`` and stop listening before its own turn ran.
"""

import asyncio

from app.domain.agent import session_state
from tests.integration.conftest import post_message, post_project, room_socket


def _private_room(client) -> str:
    project = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    return client.get(
        f"/projects/{project['id']}/private-chat", params={"user_handle": "user-1"}
    ).json()["data"]["id"]


def _until_done(ws) -> list[dict]:
    frames: list[dict] = []
    while frames[-1:] == [] or frames[-1]["type"] not in ("done", "error"):
        frames.append(ws.receive_json())
    return frames


def test_the_next_message_is_not_ended_by_the_last_turns_done(
    client, stub_hooks, monkeypatch
):
    # The database answers slowly, as it does under load: the room's books on
    # the closing result take a moment after the result itself was published.
    recorded = session_state.landed

    async def slow_landed(*args, **kwargs):
        await asyncio.sleep(1)
        return await recorded(*args, **kwargs)

    monkeypatch.setattr(session_state, "landed", slow_landed)
    room = _private_room(client)

    with room_socket(client, room, "user-1") as ws:
        post_message(client, room, "user-1", {"content": "第一句"})
        assert _until_done(ws)[-1]["type"] == "done"

    with room_socket(client, room, "user-1") as ws:
        post_message(client, room, "user-1", {"content": "第二句"})
        frames = _until_done(ws)

    assert frames[-1]["type"] == "done", frames[-1]
    said = [
        frame["block"]["content"] for frame in frames if frame["type"] == "user_block"
    ]
    assert "第二句" in said, [frame["type"] for frame in frames]
    assert "第二句" in (stub_hooks.last_prompt or "")
