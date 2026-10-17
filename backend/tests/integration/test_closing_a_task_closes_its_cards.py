"""A task put down takes its undecided accept cards with it.

A card outliving the task it is on is a card nobody will ever decide: it keeps
saying 「等你审阅」in an inbox, the poller keeps reading its PR, and the sidebar
counts it as work still moving. 测试频道 had two tasks closed on 10-12 whose
cards sat pending for five days, and the channel row kept breathing green.

The fix is the one archiving a room already had: the same card states are
revoked, the same inbox lines are settled, and the reason is left on the card.
A card riding an unmerged PR is let go — the PR's own fate belongs to the task
close (`review.task_pr`), not to this one.
"""

import uuid

from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.review.notes import NoteCode
from tests.integration.conftest import open_task, post_project, session_auth_headers


def _room(client) -> str:
    r = post_project(client, {"name": "关掉的活"}, owner="alice")
    assert r.status_code == 200, r.text
    return r.json()["data"]["root_topic_id"]


def _plant_card(client, room: str, task_id: str, **fields) -> str:
    async def run() -> str:
        async with client.test_factory() as session:
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=uuid.UUID(task_id),
                reviewer_handle="bob",
                **fields,
            )
            session.add(card)
            await session.commit()
            return str(card.id)

    return client.portal.call(run)


def _close(client, task_id: str) -> None:
    r = client.post(
        f"/topics/{task_id}/close", json={}, headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text


def _card(client, card_id: str) -> AcceptCard:
    async def read():
        async with client.test_factory() as session:
            return await session.get(AcceptCard, uuid.UUID(card_id))

    return client.portal.call(read)


def test_closing_a_task_revokes_its_undecided_cards(client):
    room = _room(client)
    task = open_task(client, room, "做一件事", owner="alice")
    card_id = _plant_card(
        client,
        room,
        task["id"],
        status=AcceptStatus.pending,
        pr_number=3528,
        pr_url="https://github.com/acme/widgets/pull/3528",
    )

    _close(client, task["id"])

    card = _card(client, card_id)
    assert card.status is AcceptStatus.revoked
    assert card.note_code is NoteCode.task_closed
    assert "任务已关闭" in card.note
    # 骑着未合并的 PR：留痕说清平台不再跟进，并留着链接。
    assert "#3528" in card.note
    # 关任务的人是这个决定的人。
    assert card.decided_by == "alice"
    assert card.decided_at is not None


def test_an_already_settled_card_is_not_touched(client):
    """终态的卡说的是一次已经做完的决议 —— 关活不会再改写它。"""
    room = _room(client)
    task = open_task(client, room, "做一件事", owner="alice")
    card_id = _plant_card(
        client,
        room,
        task["id"],
        status=AcceptStatus.accepted,
        note="已经合了",
    )

    _close(client, task["id"])

    card = _card(client, card_id)
    assert card.status is AcceptStatus.accepted
    assert card.note == "已经合了"
    assert card.decided_by is None


def test_the_room_level_card_is_not_the_one_a_task_closes(client):
    """房间自己的卡（`task_id` 空）挂的不是活，关活不该碰它。"""
    room = _room(client)
    task = open_task(client, room, "做一件事", owner="alice")

    async def plant() -> str:
        async with client.test_factory() as session:
            card = AcceptCard(
                topic_id=uuid.UUID(room),
                task_id=None,
                reviewer_handle="bob",
                status=AcceptStatus.pending,
            )
            session.add(card)
            await session.commit()
            return str(card.id)

    card_id = client.portal.call(plant)

    _close(client, task["id"])

    card = _card(client, card_id)
    assert card.status is AcceptStatus.pending
    assert card.note == ""
