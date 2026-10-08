"""Comments on a task's changes: written while it awaits review, sent with a 退回.

The rules a reviewer can state without reading the code:

- A comment is written only while the task awaits review, and only by the
  people the task belongs to (owner, contributors, reviewer). 芝士 does not
  write them.
- Until it is sent, a comment is its author's alone: nobody else sees it, and it
  is still there after a reload.
- 退回 sends the drafts named with it: 芝士 is told each one, with an id; drafts
  not named stay drafts. Nobody can send another person's draft.
- When 芝士 hands the task over again it says per comment what it did, and each
  comment is shown where its lines are in the new version, or as gone.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.delivery.models import Delivery
from app.domain.review.models import AcceptCard
from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.machine_work import machine_commits


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    join_project_team(client, pid, "alice")
    join_project_team(client, pid, "bob")
    room = client.post(
        "/topics", json={"project_id": pid, "title": "做一个东西"}
    ).json()["data"]["id"]
    return pid, room


def _file(client, room: str) -> str:
    return f"deliveries/{delivery_task_id(client, room)}.txt"


def _hand_over(client, room: str, **extra) -> dict:
    r = client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "focus": "最懂",
            **extra,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _write(client, room: str, who: str = "alice", **fields):
    body = {
        "path": _file(client, room),
        "line_start": 1,
        "line_end": 1,
        "line_text": "Test delivery",
        "body": "这里要说清楚",
        **fields,
    }
    return client.post(
        f"/topics/{delivery_task_id(client, room)}/review-comments",
        json=body,
        headers=session_auth_headers(who),
    )


def _comments(client, room: str, who: str = "alice") -> list[dict]:
    r = client.get(
        f"/topics/{delivery_task_id(client, room)}/review-comments",
        headers=session_auth_headers(who),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["comments"]


def _reject(client, card_id: str, comment_ids: list[str], who: str = "alice"):
    return client.post(
        f"/accept-cards/{card_id}/reject",
        json={"decided_by": who, "note": "", "comment_ids": comment_ids},
        headers=session_auth_headers(who),
    )


def _instruction(client, card_id: str) -> str:
    async def read():
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(card_id))
            rows = await session.scalars(
                select(Delivery).where(Delivery.conversation_id == card.task_id)
            )
            return "\n".join(row.payload["content"] for row in rows)

    return asyncio.run(read())


def test_a_draft_is_its_authors_alone_and_survives_a_reload(client, stub_hooks):
    _pid, room = _room(client)
    _hand_over(client, room)

    r = _write(client, room)
    assert r.status_code == 200, r.text
    mine = [c for c in _comments(client, room) if c["state"] == "draft"]
    assert [c["body"] for c in mine] == ["这里要说清楚"]
    # Someone else reading the same task sees nothing of it.
    assert _comments(client, room, who="bob") == []


def test_comments_are_written_only_while_the_task_awaits_review(client):
    _pid, room = _room(client)
    delivery_task(client, room)

    r = _write(client, room)
    assert r.status_code == 422, r.text
    assert _comments(client, room) == []


def test_only_the_people_the_task_belongs_to_write_comments(client, stub_hooks):
    _pid, room = _room(client)
    _hand_over(client, room)

    assert _write(client, room, who="bob").status_code == 403
    # 芝士 answers comments; it does not write them.
    agent = client.post(
        f"/topics/{delivery_task_id(client, room)}/review-comments",
        json={
            "path": _file(client, room),
            "line_start": 1,
            "line_end": 1,
            "body": "x",
        },
        headers=delivery_headers(client, room),
    )
    assert agent.status_code == 403


def test_send_back_takes_the_named_drafts_to_the_agent(client, stub_hooks):
    _pid, room = _room(client)
    card = _hand_over(client, room)
    sent = _write(client, room, body="归档的也要排除").json()["data"]
    kept = _write(client, room, body="这条下次再说").json()["data"]
    suggestion = _write(
        client, room, body="", suggestion="Delivery for review\n"
    ).json()["data"]

    r = _reject(client, card["id"], [sent["id"], suggestion["id"]])
    assert r.status_code == 200, r.text

    told = _instruction(client, card["id"])
    assert sent["id"] in told and "归档的也要排除" in told
    assert suggestion["id"] in told and "Delivery for review" in told
    assert "这条下次再说" not in told
    states = {c["id"]: c["state"] for c in _comments(client, room)}
    assert states == {sent["id"]: "sent", kept["id"]: "draft", suggestion["id"]: "sent"}
    # Sent, the comment is part of the record: bob reads it too.
    assert {c["id"] for c in _comments(client, room, who="bob")} == {
        sent["id"],
        suggestion["id"],
    }


def test_nobody_sends_another_persons_draft(client, stub_hooks):
    pid, room = _room(client)
    task = delivery_task(client, room)

    async def add_contributor():
        from app.domain.room_task.models import Task

        async with client.test_factory() as session:
            row = await session.get(Task, task.id)
            row.contributor_handles = ["bob"]
            await session.commit()

    asyncio.run(add_contributor())
    card = _hand_over(client, room)
    theirs = _write(client, room, who="bob", body="bob 的意见").json()["data"]

    r = _reject(client, card["id"], [theirs["id"]])
    assert r.status_code == 422, r.text
    assert [c["state"] for c in _comments(client, room, who="bob")] == ["draft"]
    # The refused 退回 left the card waiting for review.
    cards = client.get(
        f"/topics/{delivery_task_id(client, room)}/accept-cards",
        headers=session_auth_headers("alice"),
    )
    if cards.status_code == 200:
        assert all(c["status"] != "rejected" for c in cards.json()["data"])


def test_the_agent_answers_each_comment_and_it_follows_its_lines(client, stub_hooks):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    card = _hand_over(client, room)
    moved = _write(client, room, body="这一行要改").json()["data"]
    _reject(client, card["id"], [moved["id"]])

    # 芝士 adds two lines above the commented one and hands over again.
    machine_commits(
        task.project_id,
        task.id,
        {_file(client, room): "Header\nSecond\nTest delivery\n"},
        "chore: move the line",
    )
    _hand_over(
        client,
        room,
        comment_outcomes=[{"id": moved["id"], "handled": True, "note": "改了措辞"}],
    )

    (row,) = [c for c in _comments(client, room) if c["id"] == moved["id"]]
    assert row["outcome"] == "handled"
    assert row["outcome_note"] == "改了措辞"
    assert row["current_line"] == 3


def test_a_comment_whose_lines_are_gone_has_no_line(client, stub_hooks):
    _pid, room = _room(client)
    task = delivery_task(client, room)
    card = _hand_over(client, room)
    gone = _write(client, room).json()["data"]
    _reject(client, card["id"], [gone["id"]])

    machine_commits(
        task.project_id,
        task.id,
        {_file(client, room): "Something else entirely\n"},
        "chore: rewrite",
    )
    _hand_over(client, room)

    (row,) = [c for c in _comments(client, room) if c["id"] == gone["id"]]
    assert row["current_line"] is None
    # 芝士 said nothing about it, so it is unanswered.
    assert row["outcome"] is None


def test_an_answer_for_a_comment_not_in_the_last_round_is_refused(client, stub_hooks):
    _pid, room = _room(client)
    card = _hand_over(client, room)
    comment = _write(client, room).json()["data"]
    _reject(client, card["id"], [comment["id"]])
    task = delivery_task(client, room)
    machine_commits(
        task.project_id, task.id, {"more.txt": "more\n"}, "chore: more work"
    )

    r = client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "comment_outcomes": [
                {"id": str(uuid.uuid4()), "handled": True, "note": "x"}
            ],
        },
    )
    assert r.status_code == 422, r.text
