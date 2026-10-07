"""A run of edits to the living document is one line in the conversation.

The document is stored a few seconds after the typing stops, so a person
working on it for ten minutes stores it many times. The task reads one
"编辑了文档" for that run: the last such line is extended while nothing else
has been said since and it was touched within the last ten minutes. It then
names everyone in the run, and "查看本次修改" covers the whole run. Anything
said in between, or a longer pause, starts a new line. An AI teammate is named
by its own handle, as a person is, so the client calls it by its name.
"""

import asyncio
import uuid
from datetime import timedelta

from sqlalchemy import update

from app.domain.block.models import Block
from tests.integration.conftest import (
    chat_ws_url,
    open_task,
    room_agent_seat,
    session_auth_headers,
)
from tests.integration.test_message_edit import _room, _say_in_task


def _task(client) -> str:
    """A task of alice's, in a room bob is also in."""
    room, _ = _room(client)
    return open_task(client, room, start=False)["id"]


def _store(client, task: str, content: str, *actors: str) -> None:
    client.portal.call(client.collab.type_in, uuid.UUID(task), content, *actors)


def _edit_lines(client, task: str) -> list[dict]:
    blocks = client.get(
        f"/topics/{task}/blocks", headers=session_auth_headers("alice")
    ).json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]


def test_edits_in_a_row_are_one_line_naming_everyone_with_one_diff(client):
    task = _task(client)
    _store(client, task, "第一段", "alice")
    _store(client, task, "第一段\n\n第二段", "bob")
    _store(client, task, "第一段\n\n第二段\n\n第三段", "alice")

    lines = _edit_lines(client, task)
    assert len(lines) == 1
    line = lines[0]
    assert "<@alice>" in line["content"] and "<@bob>" in line["content"]
    assert line["meta"]["doc_version"] == 3
    # "查看本次修改" is the whole run: from before the first edit to now.
    diff = line["meta"]["detail"]
    assert "+第一段" in diff and "+第二段" in diff and "+第三段" in diff


def test_an_ai_teammate_is_named_by_its_handle_like_a_person(client):
    """The line carries the teammate's own <@handle>, the token the client draws
    with the name the roster gives it; never one fixed name for every teammate."""
    room, _ = _room(client)
    task = open_task(client, room, start=False)["id"]
    seat = room_agent_seat(client, room)
    _store(client, task, "第一段", seat)
    _store(client, task, "第一段\n\n第二段", "alice")

    line = _edit_lines(client, task)[0]
    assert f"<@{seat}>" in line["content"] and "<@alice>" in line["content"]
    assert "芝士" not in line["content"]


def test_something_said_in_between_starts_a_new_line(client):
    task = _task(client)
    _store(client, task, "第一段", "alice")
    _say_in_task(client, "", task, "alice", "我先看看")
    _store(client, task, "第一段\n\n第二段", "alice")

    lines = _edit_lines(client, task)
    assert len(lines) == 2
    assert "+第二段" in lines[-1]["meta"]["detail"]
    assert "+第一段" not in lines[-1]["meta"]["detail"]


def test_a_pause_longer_than_ten_minutes_starts_a_new_line(client):
    task = _task(client)
    _store(client, task, "第一段", "alice")

    async def eleven_minutes_ago():
        async with client.test_factory() as session:
            await session.execute(
                update(Block)
                .where(Block.conversation_id == uuid.UUID(task))
                .values(created_at=Block.created_at - timedelta(minutes=11))
            )
            await session.commit()

    asyncio.run(eleven_minutes_ago())
    _store(client, task, "第一段\n\n第二段", "alice")

    assert len(_edit_lines(client, task)) == 2


def test_an_open_page_sees_the_line_change_in_place(client):
    task = _task(client)
    _store(client, task, "第一段", "alice")
    first = _edit_lines(client, task)[0]
    with client.websocket_connect(chat_ws_url(task, "bob")) as bob:
        _store(client, task, "第一段\n\n第二段", "bob")
        # Something said after the store: its frame comes after the store's,
        # so the page stops waiting even if the store pushed nothing.
        said = _say_in_task(client, "", task, "alice", "改好了")
        while True:
            frame = bob.receive_json()
            assert frame["type"] != "event_block", "a second line was pushed"
            if frame["type"] == "block_updated":
                break
            assert (frame.get("block") or {}).get("id") != said, (
                "the line was not updated on the open page"
            )
    assert frame["block"]["id"] == first["id"]
    assert "<@bob>" in frame["block"]["content"]
