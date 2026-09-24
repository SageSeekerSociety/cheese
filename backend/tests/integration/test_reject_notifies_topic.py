"""Rejection commits its task event and preserves the reason for the native parent."""

import asyncio
import uuid

from tests.conftest import wait_work_idle
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import post_project, session_auth_headers


def _project(client) -> str:
    return post_project(client, json={"name": "P"}).json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    return client.post(
        "/topics", json={"project_id": project_id, "title": "做一个东西"}
    ).json()["data"]["id"]


def _card(client, topic_id: str, reviewer: str = "alice") -> str:
    return client.post(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    ).json()["data"]["id"]


def _reject(client, card_id: str, reviewer: str = "alice", note: str = ""):
    return client.post(
        f"/accept-cards/{card_id}/reject",
        json={"decided_by": reviewer, "note": note},
        headers=session_auth_headers(reviewer),
    )


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}"
    ).json()["data"]["blocks"]


def _instruction(client, card_id):
    from sqlalchemy import select

    from app.domain.delivery.models import Delivery
    from app.domain.review.models import AcceptCard

    async def read():
        async with client.test_factory() as session:
            card = await session.get(AcceptCard, uuid.UUID(card_id))
            row = await session.scalar(
                select(Delivery).where(Delivery.task_id == card.task_id)
            )
            assert row.state == "pending" and row.agent_instance_id is None
            return row.payload["content"]

    return asyncio.run(read())


def test_reject_retains_the_reason_until_its_native_parent_is_known(client, stub_hooks):
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    r = _reject(client, cid, note="迁移没加索引，列表页会全表扫")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "rejected"
    wait_work_idle()

    instruction = _instruction(client, cid)
    assert stub_hooks.last_prompt is None
    assert "迁移没加索引，列表页会全表扫" in instruction
    assert "alice" in instruction
    assert "重新递卡" in instruction


def test_reject_leaves_a_task_visible_line_with_the_reason_in_meta(client):
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    _reject(client, cid, note="口径和上一版对不上")
    wait_work_idle()

    events = [b for b in _blocks(client, tid) if b["kind"] == "event"]
    rejected = [
        e for e in events if (e.get("meta") or {}).get("event_type") == "card_rejected"
    ]
    assert len(rejected) == 1
    assert "驳回" in rejected[0]["content"]
    assert "alice" in rejected[0]["content"]
    meta = rejected[0]["meta"]
    assert meta["detail"] == "口径和上一版对不上"
    assert meta["detail_label"] == "驳回理由"
    assert meta["severity"] == "warn"


def test_reject_without_a_reason_records_that_no_reason_was_given(client, stub_hooks):
    """An empty reason must not be replaced with an invented one."""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    _reject(client, cid, note="")
    wait_work_idle()

    assert "没写理由" in _instruction(client, cid)


def test_rejected_topic_stays_active(client):
    """Unchanged behaviour, asserted because the wake-up would be pointless
    otherwise: an archived topic runs no turns."""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)

    _reject(client, cid, note="改一下")
    wait_work_idle()

    assert client.get(f"/topics/{tid}").json()["data"]["status"] == "active"


def test_reject_of_closed_task_reports_reason_without_waking_worker(client, stub_hooks):
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid)
    task_id = delivery_task_id(client, tid)
    closed = client.post(
        f"/topics/{tid}/tasks/{task_id}/close",
        json={"conclusion": "Stopped"},
        headers=delivery_headers(client, tid),
    )
    assert closed.status_code == 200, closed.text
    before = stub_hooks.last_prompt
    response = _reject(client, cid, note="Needs a replacement task")
    assert response.status_code == 200, response.text
    wait_work_idle()
    assert stub_hooks.last_prompt == before
    notices = [
        b
        for b in _blocks(client, tid)
        if (b.get("meta") or {}).get("event_type") == "card_rejected"
    ]
    assert notices[-1]["meta"]["detail"] == "Needs a replacement task"
    assert "原任务已结束" in notices[-1]["content"]
