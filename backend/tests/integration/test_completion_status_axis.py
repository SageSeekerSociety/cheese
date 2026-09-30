"""完成状态这条轴由提交与评审推进 —— 按浏览器会走的路一步步断言。

``task_membership.completion_status`` 以前只有两处写入（建领取写 ``NOT_SUBMITTED``、
截止清扫写 ``FAILED``），于是交了作业、判了通过都还读作「未提交」，新板「我的」、
老页面的完成率全跟着错。这里真发请求走一遍：领取 → 交 → 判 → 驳回 → 重交 → 撤销
评审，每一步读回出题人看到的那条领取，断言它带的是那一步该有的值；再断言截止清扫
与存量回填和这条轴说的是同一句话。

口径的唯一出处是 ``app.domain.task.submission_state``。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.task.deadline_scheduler import check_and_fail_expired_deadlines
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from scripts.backfill_completion_status import backfill
from tests.integration.conftest import (
    CreatedUser,
    UserCreator,
    create_approved_space,
    unique_int,
)

NOT_SUBMITTED = "NOT_SUBMITTED"
PENDING_REVIEW = "PENDING_REVIEW"
REJECTED_RESUBMITTABLE = "REJECTED_RESUBMITTABLE"
FAILED = "FAILED"
SUCCESS = "SUCCESS"

_PASS = {"accepted": True, "score": 90, "comment": "通过"}
_FAIL = {"accepted": False, "score": 10, "comment": "再改改"}


def _auth(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _human(user_client: UserCreator, api_client: TestClient) -> CreatedUser:
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


@dataclass
class _Claim:
    teacher: CreatedUser
    student: CreatedUser
    space_id: int
    task_id: int
    membership_id: int


def _claim(user_client: UserCreator, api_client: TestClient, *, own: bool) -> _Claim:
    """一块板、一道可重交的题、一条已批准的领取。

    ``own=True`` 时领题的就是建板的人自己 —— 「我的参与概览」要求调用者看得见这块
    板，建板的人天然看得见，省掉一轮邀请码。
    """
    teacher = _human(user_client, api_client)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Completion Axis ({suffix})",
            "intro": "一门课",
            "description": "一块题目板。" * 20,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(teacher.token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]

    deadline = int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000)
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"Completion Axis Task ({suffix})",
            "submitterType": "USER",
            "deadline": deadline,
            "resubmittable": True,
            "editable": True,
            "intro": "题目简介 " * 10,
            "description": "题目描述 " * 10,
            "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
            "space": space["id"],
            "categoryId": space["defaultCategoryId"],
        },
        headers=_auth(teacher.token),
    )
    assert resp.status_code == 200, resp.text
    task_id = int(resp.json()["data"]["task"]["id"])
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(teacher.token),
    )
    assert approved.status_code == 200, approved.text

    student = teacher if own else _human(user_client, api_client)
    joined = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": student.user_id},
        json={},
        headers=_auth(student.token),
    )
    assert joined.status_code == 200, joined.text
    membership_id = int(joined.json()["data"]["participant"]["id"])
    ok = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(teacher.token),
    )
    assert ok.status_code == 200, ok.text
    return _Claim(teacher, student, int(space["id"]), task_id, membership_id)


def _status(api_client: TestClient, c: _Claim) -> str:
    """出题人读回这条领取 —— 看板与名单读的就是这一列。"""
    resp = api_client.get(
        f"/tasks/{c.task_id}/participants/{c.membership_id}",
        headers=_auth(c.teacher.token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participant"]["completionStatus"]


def _submit(api_client: TestClient, c: _Claim, text: str = "我的作业") -> int:
    resp = api_client.post(
        f"/tasks/{c.task_id}/participants/{c.membership_id}/submissions",
        json=[{"text": text}],
        headers=_auth(c.student.token),
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["data"]["submission"]["id"])


def _review_url(c: _Claim, submission_id: int) -> str:
    return (
        f"/tasks/{c.task_id}/participants/{c.membership_id}"
        f"/submissions/{submission_id}/review"
    )


def _review(api_client: TestClient, c: _Claim, submission_id: int, body: dict):
    resp = api_client.post(
        _review_url(c, submission_id), json=dict(body), headers=_auth(c.teacher.token)
    )
    assert resp.status_code == 200, resp.text


class TestSubmissionsAndReviewsDriveTheAxis:
    def test_claim_hand_in_pass(self, user_client, api_client):
        c = _claim(user_client, api_client, own=False)
        assert _status(api_client, c) == NOT_SUBMITTED

        submission = _submit(api_client, c)
        assert _status(api_client, c) == PENDING_REVIEW

        _review(api_client, c, submission, _PASS)
        assert _status(api_client, c) == SUCCESS

    def test_sent_back_then_handed_in_again(self, user_client, api_client):
        c = _claim(user_client, api_client, own=False)
        first = _submit(api_client, c)
        _review(api_client, c, first, _FAIL)
        assert _status(api_client, c) == REJECTED_RESUBMITTABLE

        _submit(api_client, c, "改过的作业")
        assert _status(api_client, c) == PENDING_REVIEW

    def test_one_passed_version_among_several_is_success(self, user_client, api_client):
        c = _claim(user_client, api_client, own=False)
        first = _submit(api_client, c)
        _review(api_client, c, first, _FAIL)
        second = _submit(api_client, c, "第二版")
        _review(api_client, c, second, _PASS)
        assert _status(api_client, c) == SUCCESS

    def test_withdrawing_a_pass_puts_it_back_in_the_queue(
        self, user_client, api_client
    ):
        """撤销评审是把话收回去：这一版回到队列，状态不能留在 SUCCESS 上。"""
        c = _claim(user_client, api_client, own=False)
        submission = _submit(api_client, c)
        _review(api_client, c, submission, _PASS)
        assert _status(api_client, c) == SUCCESS

        resp = api_client.delete(
            _review_url(c, submission), headers=_auth(c.teacher.token)
        )
        assert resp.status_code in (200, 204), resp.text
        assert _status(api_client, c) == PENDING_REVIEW

    def test_changing_a_pass_to_a_fail_sends_it_back(self, user_client, api_client):
        c = _claim(user_client, api_client, own=False)
        submission = _submit(api_client, c)
        _review(api_client, c, submission, _PASS)

        resp = api_client.patch(
            _review_url(c, submission),
            json=dict(_FAIL),
            headers=_auth(c.teacher.token),
        )
        assert resp.status_code == 200, resp.text
        assert _status(api_client, c) == REJECTED_RESUBMITTABLE


# ---- 截止清扫与存量回填：直接对库，照 test_deadline_sweep_* 的写法 ----------

PAST = datetime.now(UTC) - timedelta(days=1)


async def _raw_claim(
    factory, *, status: str, verdicts: list[bool | None], deadline=PAST
) -> int:
    """一条领取，名下每一版提交的评审按 ``verdicts`` 给（``None`` = 还没评）。"""
    async with factory() as session:
        now = datetime.now(UTC)
        task = Task(
            name=f"axis-{uuid.uuid4().hex[:8]}",
            intro="",
            description="",
            creator_id=1,
            space_id=1,
            category_id=1,
            submitter_type=0,
            approved=1,
            default_deadline=0,
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.flush()
        membership = TaskMembership(
            task_id=task.id,
            member_id=1,
            is_team=False,
            approved=1,
            completion_status=status,
            deadline=deadline,
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        await session.flush()
        for version, verdict in enumerate(verdicts, start=1):
            submission = TaskSubmission(
                membership_id=membership.id,
                version=version,
                submitter_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(submission)
            await session.flush()
            if verdict is not None:
                session.add(
                    TaskSubmissionReview(
                        submission_id=submission.id,
                        accepted=verdict,
                        score=0,
                        comment="",
                        created_at=now,
                        updated_at=now,
                    )
                )
        await session.commit()
        return int(membership.id)


async def _read(factory, membership_id: int) -> str:
    async with factory() as session:
        value = await session.scalar(
            select(TaskMembership.completion_status).where(
                TaskMembership.id == membership_id
            )
        )
        assert value is not None
        return value


@pytest.mark.anyio
async def test_backfill_is_dry_by_default_and_idempotent(db_factory):
    """存量：判通过了却还写着 NOT_SUBMITTED 的领取，回填把它纠正成 SUCCESS。"""
    future = datetime.now(UTC) + timedelta(days=7)
    passed = await _raw_claim(
        db_factory, status=NOT_SUBMITTED, verdicts=[True], deadline=future
    )
    queued = await _raw_claim(
        db_factory, status=NOT_SUBMITTED, verdicts=[None], deadline=future
    )

    async with db_factory() as session:
        await backfill(session, apply=False)
    assert await _read(db_factory, passed) == NOT_SUBMITTED  # dry run 一行不写

    async with db_factory() as session:
        await backfill(session, apply=True)
    assert await _read(db_factory, passed) == SUCCESS
    assert await _read(db_factory, queued) == PENDING_REVIEW

    async with db_factory() as session:
        again = await backfill(session, apply=True)
    assert again.changed == 0, again.describe()  # 第二遍什么都没变
    assert await _read(db_factory, passed) == SUCCESS
    assert await _read(db_factory, queued) == PENDING_REVIEW


@pytest.mark.anyio
async def test_sweep_and_derivation_agree_on_a_sent_back_claim_past_deadline(
    db_factory,
):
    """被驳回、过了截止还没重交：清扫写 FAILED，重推必须仍是 FAILED，不能翻回去。"""
    membership_id = await _raw_claim(
        db_factory, status=REJECTED_RESUBMITTABLE, verdicts=[False]
    )

    async with db_factory() as session:
        await check_and_fail_expired_deadlines(session)
    assert await _read(db_factory, membership_id) == FAILED

    async with db_factory() as session:
        await backfill(session, apply=True)
    assert await _read(db_factory, membership_id) == FAILED


@pytest.mark.anyio
async def test_sweep_leaves_passed_and_queued_alone(db_factory):
    passed = await _raw_claim(db_factory, status=SUCCESS, verdicts=[True])
    queued = await _raw_claim(db_factory, status=PENDING_REVIEW, verdicts=[None])
    nothing = await _raw_claim(db_factory, status=NOT_SUBMITTED, verdicts=[])

    async with db_factory() as session:
        await check_and_fail_expired_deadlines(session)

    assert await _read(db_factory, passed) == SUCCESS
    assert await _read(db_factory, queued) == PENDING_REVIEW
    assert await _read(db_factory, nothing) == FAILED
