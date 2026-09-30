"""「已提交」在三个分析端点上必须是同一个数。

概览把「已提交」算成「这条领取记录有一条提交行」，题目表与发布者表一度按
``completion_status`` 是否落在 ``SUBMITTED_STATUSES`` 里算。后端没有任何请求路径会
随提交推进 ``completion_status``（唯一写它的是截止时间清扫任务，它只写 FAILED），
于是这两套判据今天就能在同一份数据上互相打架：交过作业的人不算已提交，从没交过、
只是被清扫任务判成 FAILED 的人反而算。

这里从接口这一层验后果：同一份数据下，概览 / 题目表 / 发布者表三处的
``submittedParticipantCount`` 必须相等，并且等于「有提交行的人数」；转化率的分子
也跟着同一个数。断言的是接口返回的字段值，不看实现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import TaskMembership
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _space(api_client: TestClient, creator_token: str) -> dict:
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Analytics Submitted Count ({suffix})",
            "intro": "一门课",
            "description": "一块题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["space"]


def _publish_approved_task(
    api_client: TestClient,
    creator_token: str,
    *,
    space_id: int,
    category_id: int,
) -> int:
    """一道已通过、未结项的题。"""
    deadline = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"Analytics Task ({unique_int(1000, 9999)})",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": deadline,
            "space": space_id,
            "categoryId": category_id,
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(creator_token),
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _join_and_approve(
    api_client: TestClient,
    participant_token: str,
    creator_token: str,
    *,
    task_id: int,
) -> int:
    joined = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={},
        headers=_auth(participant_token),
    )
    assert joined.status_code == 200, joined.text
    membership_id = joined.json()["data"]["participant"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(creator_token),
    )
    assert approved.status_code == 200, approved.text
    return membership_id


def _submit(
    api_client: TestClient, participant_token: str, *, task_id: int, membership_id: int
) -> None:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        json=[{"text": "我的作业"}],
        headers=_auth(participant_token),
    )
    assert resp.status_code == 200, resp.text


async def _expire_then_sweep(session: AsyncSession, membership_id: int) -> str:
    """把这个领取记录的截止时间放到过去，再跑平台自己那条清扫任务。

    ``FAILED`` 只有 ``check_and_fail_expired_deadlines`` 会写，所以「有状态、没有提交
    行」这个状态只能这样造 —— 它正是线上数据里那 81 条的形状。
    """
    membership = await session.get(TaskMembership, membership_id)
    assert membership is not None
    now = datetime.now(UTC)
    membership.deadline = now - timedelta(minutes=5)
    membership.updated_at = now
    await session.flush()
    await check_and_fail_expired_deadlines(session)
    await session.refresh(membership)
    return membership.completion_status


def test_the_three_analytics_tables_count_the_same_submissions(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """两个人交了作业，一个人从没交、只是被清扫任务判成 FAILED。

    「已提交」= 3 是错的（会把没交的人算进来），= 2 才是「有提交行的人数」。三个端点
    必须都给出 2；转化率的分子也必须是 2（分母是 3 个已批准的领取记录）。
    """
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    space = _space(api_client, creator_token)
    space_id, category_id = space["id"], space["defaultCategoryId"]
    task_id = _publish_approved_task(
        api_client, creator_token, space_id=space_id, category_id=category_id
    )

    for _ in range(2):
        submitter = user_client.create_user()
        submitter_token = _login(user_client, api_client, submitter)
        membership_id = _join_and_approve(
            api_client, submitter_token, creator_token, task_id=task_id
        )
        _submit(
            api_client,
            submitter_token,
            task_id=task_id,
            membership_id=membership_id,
        )

    expired = user_client.create_user()
    expired_token = _login(user_client, api_client, expired)
    expired_membership_id = _join_and_approve(
        api_client, expired_token, creator_token, task_id=task_id
    )
    status = _portal.call(_expire_then_sweep, db_session, expired_membership_id)
    assert status == "FAILED", (
        "前提没成立：这条领取记录应当被清扫任务判成 FAILED 且没有提交行"
    )

    headers = _auth(creator_token)

    overview = api_client.get(f"/spaces/{space_id}/analytics/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    entity_metrics = overview.json()["data"]["entityMetrics"]
    assert entity_metrics["approvedParticipantCount"] == 3, entity_metrics

    task_rows = api_client.get(
        f"/spaces/{space_id}/analytics/tasks",
        params={"sortBy": "createdAt"},
        headers=headers,
    )
    assert task_rows.status_code == 200, task_rows.text
    task_row = next(
        row for row in task_rows.json()["data"]["tasks"] if row["taskId"] == task_id
    )

    publisher_rows = api_client.get(
        f"/spaces/{space_id}/analytics/publishers", headers=headers
    )
    assert publisher_rows.status_code == 200, publisher_rows.text
    publisher_row = next(
        row
        for row in publisher_rows.json()["data"]["publishers"]
        if row["publisherId"] == creator.user_id
    )

    # 三处同一个判据、同一个数：有提交行的是 2 个人。
    assert entity_metrics["submittedParticipantCount"] == 2, entity_metrics
    assert task_row["submittedParticipantCount"] == 2, task_row
    assert publisher_row["submittedParticipantCount"] == 2, publisher_row

    # 转化率的分子跟着同一个数走，分母（已批准）不动。
    assert entity_metrics["submissionConversionRate"] == pytest.approx(
        2 / 3, abs=1e-4
    ), entity_metrics
    assert task_row["submissionConversionRate"] == pytest.approx(2 / 3, abs=1e-4), (
        task_row
    )
    assert publisher_row["submissionConversionRate"] == pytest.approx(
        2 / 3, abs=1e-4
    ), publisher_row
