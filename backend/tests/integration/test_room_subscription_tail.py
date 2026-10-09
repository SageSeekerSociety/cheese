"""A message stored between reading a room and subscribing to it is not lost.

A page reads a room's history over HTTP and hears what lands after that on the
rooms socket. Those are two paths, and a message stored after the read but
before the subscription is registered travels by neither: the read was too
early and the socket too late. In a quiet room nothing comes after it to give
it away, so it stays missing until the page is reloaded.

So the acknowledgement says how far the room's stored messages reach as of the
moment the subscription took hold: the number of the last one (`seq`). A page
whose messages stop short of it asks for those stored after the last it holds;
anything stored later is published to it. By number and not by date, because a
message can be dated before messages stored ahead of it.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.integration.conftest import (
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)


def _room(client) -> str:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    return client.post(
        "/topics",
        json={"project_id": pid, "title": "T"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]


def _read(client, room: str, **params) -> dict:
    r = client.get(
        f"/topics/{room}/blocks",
        params={"limit": 50, "shown": "true", **params},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _held(page: dict) -> int:
    return max((b["seq"] for b in page["data"]), default=0)


def test_a_message_stored_after_the_read_is_named_when_the_room_is_subscribed(
    client,
):
    room = _room(client)
    held = _held(_read(client, room))
    between = post_message(client, room, "alice", {"content": "读完历史之后才说的"})

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["newest"] > held
    missed = _read(client, room, stored_after=held)["data"]
    assert [b["id"] for b in missed] == [between["id"]]


def test_the_newest_number_matches_what_a_fresh_read_ends_with(client):
    room = _room(client)
    post_message(client, room, "alice", {"content": "第一句"})

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["newest"] == _held(_read(client, room))


def test_a_message_dated_before_what_the_page_holds_is_still_caught_up(client):
    """芝士's message is dated when it began and stored once the step after it
    arrives: behind messages the page already holds. Asking by date for what
    came after the newest held would never find it; asking by number does."""
    room = _room(client)
    first = post_message(client, room, "alice", {"content": "先说的一句"})
    page = _read(client, room)
    pid = client.get(f"/topics/{room}").json()["data"]["project_id"]

    async def late() -> str:
        async with client.test_factory() as session:
            block = Block(
                project_id=uuid.UUID(pid),
                conversation_id=uuid.UUID(room),
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="cheese",
                content="早就开始写、这会儿才写完的回答",
                created_at=datetime.fromisoformat(first["created_at"])
                - timedelta(seconds=5),
            )
            session.add(block)
            await session.commit()
            return str(block.id)

    late_id = asyncio.run(late())

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["newest"] > _held(page)
    missed = _read(client, room, stored_after=_held(page))["data"]
    assert [b["id"] for b in missed] == [late_id]


def test_a_step_kept_out_of_the_room_is_not_what_it_names(client):
    """The page reads only what the room shows, so the newest it is told about is
    the newest of those: a step stored after it would send it reading again for
    a row it never draws."""
    room = _room(client)
    said = post_message(client, room, "alice", {"content": "房间里说的最后一句"})
    pid = client.get(f"/topics/{room}").json()["data"]["project_id"]

    async def step() -> None:
        async with client.test_factory() as session:
            session.add(
                Block(
                    project_id=uuid.UUID(pid),
                    conversation_id=uuid.UUID(room),
                    kind=BlockKind.event,
                    author_type=AuthorType.participant,
                    author="cheese",
                    content="ran tests",
                    meta={"in_room": False, "tool": "Bash"},
                )
            )
            await session.commit()

    asyncio.run(step())

    with room_socket(client, room, "alice") as ws:
        assert ws.subscribed["newest"] == said["seq"]
