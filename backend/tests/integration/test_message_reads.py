"""已读：谁真的读到了一段对话里的哪几条消息。

A person has read what their page showed them (the page reports it); an AI
teammate has read what one of its turns was given. Each reader's reads of a
conversation stay a few stretches of block numbers, however many messages they
read, and only someone who can read the conversation learns who read what.
"""

import uuid

import pytest

from app.api import deps as session_turn_deps
from app.core.config import settings
from app.domain.agent.chat import ChatService
from tests.conftest import finish_turn, stub_compute
from tests.integration.conftest import (
    join_project_team,
    post_project,
    project_service,
    registered,
    room_socket,
    session_auth_headers,
)


def _channel(client, *, members_only: bool = False) -> str:
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    join_project_team(client, p["id"], "bob")
    join_project_team(client, p["id"], "carol")
    body = {"project_id": p["id"], "title": "频道"}
    if members_only:
        body["members_only"] = True
    return client.post(
        "/topics", json=body, headers=session_auth_headers("alice")
    ).json()["data"]["id"]


def _say(client, topic_id: str, author: str, *, kind: str = "message") -> int:
    """Store one block as ``author`` and return its number (`Block.seq`)."""
    from app.domain.block.models import BlockKind
    from app.domain.block.repositories import BlockRepository
    from app.domain.event_author import AuthorType
    from app.domain.topic.models import Topic

    async def store() -> int:
        async with client.test_factory() as session:
            room = await session.get(Topic, uuid.UUID(topic_id))
            assert room is not None
            block = await BlockRepository(session).add(
                project_id=room.project_id,
                conversation_id=room.id,
                author=author,
                author_type=AuthorType.participant
                if kind != "event"
                else AuthorType.platform,
                kind=BlockKind(kind),
                content=f"{author} 说的话",
            )
            await session.commit()
            return int(block.seq)

    return client.portal.call(store)


def _report(client, topic_id: str, reader: str, *seqs: int):
    return client.post(
        f"/topics/{topic_id}/reads",
        json={"ranges": [[s, s] for s in seqs]},
        headers=session_auth_headers(reader),
    )


def _reads(client, topic_id: str, asker: str, first: int = 1, last: int = 10_000):
    r = client.get(
        f"/topics/{topic_id}/reads",
        params={"first": first, "last": last},
        headers=session_auth_headers(asker),
    )
    assert r.status_code == 200, r.text
    return {row["reader"]: row["ranges"] for row in r.json()["data"]["readers"]}


def _covers(ranges: list[list[int]], seq: int) -> bool:
    return any(first <= seq <= last for first, last in ranges)


def test_reading_on_through_a_conversation_keeps_one_stretch(client):
    """Bob sees Alice's messages one screen at a time, with an AI step and his
    own reply between them: his reads stay one stretch, not one per message."""
    topic_id = _channel(client)
    first = _say(client, topic_id, "alice")
    _say(client, topic_id, "system", kind="event")
    _say(client, topic_id, "bob")
    second = _say(client, topic_id, "alice")
    third = _say(client, topic_id, "alice")

    assert _report(client, topic_id, "bob", first).status_code == 200
    assert _report(client, topic_id, "bob", second, third).status_code == 200

    assert _reads(client, topic_id, "carol")["bob"] == [[first, third]]


def test_a_message_the_page_never_showed_stays_unread(client):
    topic_id = _channel(client)
    seen_first = _say(client, topic_id, "alice")
    skipped = _say(client, topic_id, "carol")
    seen_last = _say(client, topic_id, "alice")

    _report(client, topic_id, "bob", seen_first, seen_last)

    bob = _reads(client, topic_id, "alice")["bob"]
    assert _covers(bob, seen_first) and _covers(bob, seen_last)
    assert not _covers(bob, skipped)


def test_reporting_what_was_already_read_changes_nothing(client):
    topic_id = _channel(client)
    seq = _say(client, topic_id, "alice")
    assert _report(client, topic_id, "bob", seq).json()["data"]["ranges"] == [
        [seq, seq]
    ]
    again = _report(client, topic_id, "bob", seq)
    assert again.status_code == 200
    assert again.json()["data"]["ranges"] == []
    assert _reads(client, topic_id, "alice")["bob"] == [[seq, seq]]


def test_a_message_not_yet_said_cannot_be_read_ahead(client):
    topic_id = _channel(client)
    seq = _say(client, topic_id, "alice")
    _report(client, topic_id, "bob", seq + 5)
    later = _say(client, topic_id, "alice")
    assert not _covers(_reads(client, topic_id, "alice").get("bob", []), later)


def test_the_reads_asked_for_are_those_within_the_span(client):
    topic_id = _channel(client)
    seqs = [_say(client, topic_id, "alice") for _ in range(4)]
    _report(client, topic_id, "bob", *seqs)
    assert _reads(client, topic_id, "alice", seqs[1], seqs[2])["bob"] == [
        [seqs[1], seqs[2]]
    ]
    assert "bob" not in _reads(client, topic_id, "alice", seqs[3] + 1, seqs[3] + 9)


def test_someone_outside_a_private_channel_learns_nothing_about_its_reads(client):
    topic_id = _channel(client, members_only=True)
    seq = _say(client, topic_id, "alice")
    _report(client, topic_id, "alice", seq)

    outsider = client.get(
        f"/topics/{topic_id}/reads",
        params={"first": 1, "last": 100},
        headers=session_auth_headers("carol"),
    )
    assert outsider.status_code in (403, 404)
    assert "alice" not in outsider.text
    assert _report(client, topic_id, "carol", seq).status_code in (403, 404)


def test_whoever_has_the_conversation_open_sees_a_read_land(client):
    topic_id = _channel(client)
    seq = _say(client, topic_id, "alice")
    with room_socket(client, topic_id, "alice") as ws:
        _report(client, topic_id, "bob", seq)
        frame = ws.receive_json()
        while frame["type"] != "reads":
            frame = ws.receive_json()
    assert frame["reader"] == "bob"
    assert _covers(frame["ranges"], seq)


@pytest.mark.anyio
async def test_an_ai_teammate_has_read_what_its_turn_was_given(
    business_db_factory, tmp_path, monkeypatch
):
    """The messages a turn carries are the AI teammate's reads; the older ones
    its window left out are not, though its turn moved past them."""
    monkeypatch.setattr(settings, "agent_input_window_messages", 2)
    factory = business_db_factory  # type: ignore[attr-defined]
    svc = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=stub_compute(),
    )
    from sqlalchemy import select

    from app.domain.block import message_reads
    from app.domain.block.models import Block
    from app.domain.block.repositories import BlockRepository
    from app.domain.event_author import AuthorType
    from app.domain.topic.models import TopicMembership
    from app.domain.topic.services import TopicService

    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        topic_id = topic.id
        for text in ("很早的一句", "也很早"):
            await BlockRepository(session).add(
                project_id=project.id,
                conversation_id=topic_id,
                author="u",
                author_type=AuthorType.participant,
                content=text,
            )
        await session.commit()

    _ = [
        f
        async for f in svc.converse(
            topic_id=topic_id, author="u", content="帮我看看", summon=True
        )
    ]
    await finish_turn(svc, topic_id)

    async with factory() as session:
        said = list(
            await session.scalars(
                select(Block)
                .where(Block.conversation_id == topic_id, Block.author == "u")
                .order_by(Block.seq)
            )
        )
        reads = {
            row["reader"]: row["ranges"]
            for row in await message_reads.reads_between(session, topic_id, 1, 10_000)
        }
        # The page names a reader by the room's roster: the AI teammate's
        # reads are under the handle it sits there with.
        seated = set(
            await session.scalars(
                select(TopicMembership.member_handle).where(
                    TopicMembership.topic_id == topic_id
                )
            )
        )
    early, earlier_window, summons = said[0], said[1], said[-1]
    ai = [reader for reader in reads if reader != "u"]
    assert len(ai) == 1, reads
    assert ai[0] in seated, (ai, seated)
    assert _covers(reads[ai[0]], summons.seq)
    assert _covers(reads[ai[0]], earlier_window.seq)
    assert not _covers(reads[ai[0]], early.seq)


def test_an_ai_teammate_cannot_report_reads_for_itself(client):
    """What an AI teammate has read is what its turns were given, recorded by
    the platform: its own credential reports nothing."""
    from tests.integration.conftest import room_agent_headers

    topic_id = _channel(client)
    seq = _say(client, topic_id, "alice")
    r = client.post(
        f"/topics/{topic_id}/reads",
        json={"ranges": [[seq, seq]]},
        headers=room_agent_headers(client, topic_id),
    )
    assert r.status_code in (401, 403)
    assert _reads(client, topic_id, "alice") == {}
