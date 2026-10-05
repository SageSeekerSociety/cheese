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
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(card),
                author="alice",
                author_type=AuthorType.participant,
                content=text,
                kind=BlockKind.message,
            )
            await session.commit()
            return str(block.id)

    return client.portal.call(go)


def _open(client, room: str, card: str, **params):
    return client.get(f"/topics/{card}/task", params=params)


def test_a_card_opened_at_an_old_message_reaches_back_to_it(client):
    pid, room = _room(client)
    card = _card(client, room, "一件活")
    said = [_say(client, pid, room, card, f"第 {i} 条") for i in range(30)]

    newest = _open(client, room, card, limit=5).json()["data"]["blocks"]
    assert said[3] not in [b["id"] for b in newest]

    r = _open(client, room, card, limit=5, through=said[3])
    assert r.status_code == 200, r.text
    ids = [b["id"] for b in r.json()["data"]["blocks"]]
    assert said[3] in ids
    # Still the whole stretch down to the newest: the card has no gaps.
    assert ids[ids.index(said[3]) :] == said[3:]


def test_a_message_already_in_the_newest_window_changes_nothing(client):
    pid, room = _room(client)
    card = _card(client, room, "一件活")
    said = [_say(client, pid, room, card, f"第 {i} 条") for i in range(30)]

    plain = _open(client, room, card, limit=5).json()["data"]["blocks"]
    focused = _open(client, room, card, limit=5, through=said[-2]).json()["data"][
        "blocks"
    ]
    assert [b["id"] for b in focused] == [b["id"] for b in plain]


def test_a_message_of_another_card_is_not_found(client):
    pid, room = _room(client)
    mine = _card(client, room, "我的活")
    theirs = _card(client, room, "别的活")
    _say(client, pid, room, mine, "我的消息")
    elsewhere = _say(client, pid, room, theirs, "别处的消息")

    r = _open(client, room, mine, limit=5, through=elsewhere)
    assert r.status_code == 404
