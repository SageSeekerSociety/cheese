"""Acceptance closes the delivered task while its room stays active.

A later delivery uses a new task; revoking approval does not undo a Git merge.
"""

import uuid

import pytest

from tests.delivery import delivery_headers, delivery_task, delivery_task_id
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    r = client.post("/projects", json={"name": "P", "owner_handle": "alice"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _topic(client, project_id: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": "做一个东西", "created_by": "alice"},
    )
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _card(
    client,
    topic_id: str,
    reviewer: str = "alice",
    subject: str = "chore(test): file an accept card",
):
    return client.post(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}/accept-card",
        headers=delivery_headers(client, topic_id),
        json={
            "change_subject": subject,
            "reviewer_handle": reviewer,
            "routing_reason": "最懂",
        },
    )


def _commit_next_task(client, project_id: str, topic_id: str, text: str) -> None:
    from tests.machine_work import machine_commits

    task = delivery_task(client, topic_id, new=True, commit=False)
    machine_commits(uuid.UUID(project_id), task.id, {"next-task.txt": text})


def _task_state(client, topic_id: str) -> dict:
    response = client.get(
        f"/topics/{topic_id}/tasks/{delivery_task_id(client, topic_id)}",
        headers=delivery_headers(client, topic_id),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _accept(client, card_id: str, by: str = "alice"):
    return client.post(
        f"/accept-cards/{card_id}/accept",
        json={"decided_by": by},
        headers=session_auth_headers(by),
    )


def _state(client, topic_id: str) -> dict:
    r = client.get(f"/topics/{topic_id}")
    assert r.status_code == 200
    return r.json()["data"]


def _delivered_topic(client) -> tuple[str, str]:
    """一个已经交付过一次的话题 (project_id, topic_id)。"""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid).json()["data"]["id"]
    assert _accept(client, cid).status_code == 200
    return pid, tid


def test_merge_leaves_the_topic_active_with_a_delivery_marker(client):
    _pid, tid = _delivered_topic(client)

    topic = _state(client, tid)
    assert topic["status"] == "active"  # 不是 archived
    assert topic["accepted_by"] is None
    task = _task_state(client, tid)
    assert task["accepted_by"] == "alice"
    assert task["accepted_at"] is not None
    assert task["status"] == "closed"
    assert task["delivered_head"]


def test_a_delivered_topic_with_nothing_new_cannot_file_a_second_card(client):
    """The delivered task is closed even though its room is still active."""
    _pid, tid = _delivered_topic(client)

    r = _card(client, tid, reviewer="bob")
    assert r.status_code == 422
    # 拒绝话术要说清出路（读它的是一轮之后就要再试一次的芝士）。
    message = r.json()["message"]
    assert "任务已结束" in message
    # 而且这个拒绝不靠归档 —— 话题还活着。
    assert _state(client, tid)["status"] == "active"


def test_a_delivered_room_can_deliver_again_once_there_are_new_commits(client):
    """The next task owns a new branch in the same open room."""
    pid, tid = _delivered_topic(client)

    # 干下一件活：新任务、新分支。
    _commit_next_task(client, pid, tid, "next task")

    r = _card(client, tid, reviewer="bob", subject="feat(x): the next task")
    assert r.status_code == 200, r.text


def test_revoking_approval_keeps_merged_task_closed(client):
    """Approval can be revoked; the already merged branch cannot be reused."""
    _pid, tid = _delivered_topic(client)
    cid = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]["id"]

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    task = _task_state(client, tid)
    assert task["accepted_at"] is None
    assert task["accepted_by"] is None
    assert task["status"] == "closed"
    assert task["delivered_head"]
    assert _card(client, tid, reviewer="bob").status_code == 422
    delivery_task(client, tid, new=True)
    assert _card(client, tid, reviewer="bob").status_code == 200


def test_revoking_does_not_undo_a_persons_archive(client):
    """两个动作互不覆盖：取消归档不改写采纳记录（TopicService.unarchive），
    反过来撤回采纳也不改写归档状态 —— 那是人的决定。"""
    _pid, tid = _delivered_topic(client)
    cid = client.get(f"/topics/{tid}/accept-card").json()["data"]["data"][0]["id"]

    assert (
        client.post(
            f"/topics/{tid}/archive",
            json={"by": "alice"},
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    assert _state(client, tid)["status"] == "archived"

    r = client.post(
        f"/accept-cards/{cid}/revoke",
        json={"decided_by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    # 交付标记清了，但话题还是人放下的那个状态。
    topic = _state(client, tid)
    assert topic["accepted_by"] is None
    assert topic["status"] == "archived"


def test_merge_does_not_stop_the_container(client, monkeypatch):
    """采纳不再删容器：话题接着干活要用它，而闲置回收器管它的死活。"""

    stopped: list[uuid.UUID] = []

    _pid, tid = _delivered_topic(client)

    assert stopped == []
    assert _state(client, tid)["status"] == "active"


def test_manual_archive_still_archives_and_still_freezes_new_cards(client):
    """归档这条路一点没变，它只是不再由合并触发。"""
    pid = _project(client)
    tid = _topic(client, pid)

    r = client.post(
        f"/topics/{tid}/archive",
        json={"by": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200
    topic = _state(client, tid)
    assert topic["status"] == "archived"
    assert topic["archived_at"] is not None
    # 归档过的话题没交付过 —— 两个标记互相独立。
    assert topic["accepted_at"] is None

    r = _card(client, tid)
    assert r.status_code == 422
    assert "已归档" in r.json()["message"]

    # 取消归档回到可递卡。
    assert (
        client.post(
            f"/topics/{tid}/unarchive",
            json={"by": "alice"},
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    assert _card(client, tid).status_code == 200


@pytest.mark.parametrize("blocked_status", ["pending", "accepted"])
def test_one_card_at_a_time_covers_both_live_and_delivered(client, blocked_status):
    """递卡互斥的两半：一张还活着的卡挡新卡（老行为），一张已交付的卡也挡（新行为）。
    两条走的是同一个闸门，但拒绝理由必须不一样——出路完全不同。"""
    pid = _project(client)
    tid = _topic(client, pid)
    cid = _card(client, tid).json()["data"]["id"]
    if blocked_status == "accepted":
        assert _accept(client, cid).status_code == 200

    r = _card(client, tid, reviewer="bob")
    assert r.status_code == 422
    message = r.json()["message"]
    if blocked_status == "pending":
        assert "改验收人" in message
    else:
        assert "任务已结束" in message
