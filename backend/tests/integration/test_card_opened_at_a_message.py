"""A card opened at one of its messages (a search hit, a link someone sent).

The card normally shows only its newest blocks; `through` must stretch that
window back far enough that the named message is in it, and must not reach
into another card's conversation to do so.
"""

import uuid

from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from tests.integration.conftest import open_task, post_project, session_auth_headers


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return pid, rid


def _card(client, room: str, title: str) -> str:
    return open_task(client, room, title, start=False)["id"]


def _say(client, pid: str, room: str, card: str, text: str) -> str:
    async def go() -> str:
        async with client.test_request_factory() as session:
            block = await BlockRepository(session).add(
                project_id=uuid.UUID(pid),
                conversation_id=uuid.UUID(card),
                author="alice",
                author_type=AuthorType.participant,
                content=text,
                kind=BlockKind.message,
            )
            await session.commit()
            return str(block.id)

    return client.portal.call(go)


def _open(client, card: str, **params):
    return client.get(f"/topics/{card}/blocks", params=params)


def test_a_card_opened_at_an_old_message_reaches_back_to_it(client):
    pid, room = _room(client)
    card = _card(client, room, "一件活")
    said = [_say(client, pid, room, card, f"第 {i} 条") for i in range(30)]

    newest = _open(client, card, limit=5).json()["data"]["data"]
    assert said[3] not in [b["id"] for b in newest]

    r = _open(client, card, limit=5, around=said[3])
    assert r.status_code == 200, r.text
    ids = [b["id"] for b in r.json()["data"]["data"]]
    assert said[3] in ids


def test_a_message_of_another_card_is_not_found(client):
    pid, room = _room(client)
    mine = _card(client, room, "我的活")
    theirs = _card(client, room, "别的活")
    _say(client, pid, room, mine, "我的消息")
    elsewhere = _say(client, pid, room, theirs, "别处的消息")

    r = _open(client, mine, limit=5, around=elsewhere)
    assert r.status_code == 404
