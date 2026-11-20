"""The sender's copy of a message is what was stored, as the room is told it.

`POST /topics/{id}/messages` wrote the message, then read it back by id to
build the sender's copy, and read the parent again for a reply's preview. The
write already produced that block as the room is told it (`dump_blocks_json`,
the preview included); the copy is that, with no read back.
"""

import uuid

from sqlalchemy import select

from app.domain.block.models import Block
from app.domain.block.schemas import BlockOut
from tests.integration.conftest import join_project_team, session_auth_headers
from tests.integration.test_the_composer_shows_who_a_message_is_for import _channel
from tests.support.counting_session import counting_sql


def _send(client, room, **body):
    return client.post(
        f"/topics/{room}/messages",
        json={"request_id": str(uuid.uuid4()), **body},
        headers=session_auth_headers("alice"),
    )


def test_the_senders_copy_is_the_stored_block_without_reading_it_back(client):
    project, _root, room = _channel(client)
    # No AI turn for these unaddressed messages in a two-person project.
    join_project_team(client, project, "bob")
    first = _send(client, room, content="第一句")
    assert first.status_code == 200, first.text
    with counting_sql(client) as seen:
        reply = _send(
            client, room, content="回第一句", reply_to=first.json()["data"]["id"]
        )

    assert reply.status_code == 200, reply.text
    # The write ends by recording its result for retries; the route reads
    # nothing of the blocks after it.
    written = max(
        i for i, s in enumerate(seen) if s.startswith("UPDATE idempotency_keys")
    )
    after = [s for s in seen[written + 1 :] if s.startswith("SELECT blocks.")]
    assert after == [], after


def test_the_senders_copy_matches_the_block_as_stored(client):
    import asyncio

    _, _root, room = _channel(client)
    first = _send(client, room, content="第一句").json()["data"]
    sent = _send(client, room, content="回第一句", reply_to=first["id"]).json()["data"]

    async def stored() -> dict:
        async with client.test_factory() as db:
            block = await db.scalar(
                select(Block).where(Block.id == uuid.UUID(sent["id"]))
            )
            return BlockOut.model_validate(block).model_dump(mode="json")

    expected = asyncio.run(stored())
    assert {k: v for k, v in sent.items() if k in expected} == expected
    assert sent["reply_preview"]
