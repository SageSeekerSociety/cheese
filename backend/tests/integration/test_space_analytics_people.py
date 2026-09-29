"""``GET /spaces/{spaceId}/analytics/people``：逐人一行，外加「领了没动」的名单。

这一格回答的是原型里那两处「逐人的那一层」——谁领了几道、走到哪一步，以及**谁**
在哪道题上领了两周没动。断言全部落在接口返回的字段上：

1. 一条领取的状态由**提交与评审**算出来，不看 ``completion_status``（那一列没有
   任何请求路径会推进它）。所以「在做 → 已交 → 通过」这一条链要在接口上真的走得通。
2. 逐人那一格只数**这块板**上**未删除**的题与未删除的领取 —— 别的板、已删的题、
   已撤的领取都不该混进来。
3. 领了没动是**逐条领取**：领取超过 14 天**而且**一版提交都没有。交过一版的、
   或者才领了三天的，都不算。

鉴权那一条与同一组分析接口同一个门，红了说明这一条路另开了一扇窗。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

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


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    """建版的人 + 一块已通过审核的题目板。建版的人就是它的 OWNER（管理员）。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Analytics People ({suffix})",
            "intro": "一门课",
            "description": "一块题目板",
            "avatarId": 1,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "creator": creator,
        "creator_token": creator_token,
        "space_id": space["id"],
        "category_id": space["defaultCategoryId"],
    }


def _publish_approved_task(api_client: TestClient, board: dict, *, name: str) -> int:
    deadline = int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000)
    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": deadline,
            "space": board["space_id"],
            "categoryId": board["category_id"],
        },
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _join_and_approve(
    api_client: TestClient, board: dict, participant_token: str, *, task_id: int
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
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text
    return membership_id


def _submit(
    api_client: TestClient, participant_token: str, *, task_id: int, membership_id: int
) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        json=[{"text": "我的作业"}],
        headers=_auth(participant_token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission"]["id"]


def _review(
    api_client: TestClient,
    board: dict,
    *,
    task_id: int,
    membership_id: int,
    submission_id: int,
    accepted: bool,
) -> None:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions/{submission_id}/review",  # noqa: E501
        json={"accepted": accepted, "score": 5, "comment": "判了"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _people(api_client: TestClient, token: str, space_id: int) -> dict:
    resp = api_client.get(f"/spaces/{space_id}/analytics/people", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _row_for(people: dict, user_id: int) -> dict:
    rows = [row for row in people["people"] if row["userId"] == user_id]
    assert len(rows) == 1, (user_id, people)
    return rows[0]


async def _backdate_claim(session: AsyncSession, membership_id: int, days: int) -> None:
    """把一条领取的领取时间往前挪 —— 「领了没动」那条线没有别的造法。"""
    membership = await session.get(TaskMembership, membership_id)
    assert membership is not None
    claimed_at = datetime.now(UTC) - timedelta(days=days)
    membership.created_at = claimed_at
    membership.updated_at = claimed_at
    await session.flush()


def test_the_owner_reads_one_row_per_claimant_with_its_three_counts(
    api_client: TestClient, user_client: UserCreator
):
    """两个人、两道题：领取 / 通过 / 已交未通过 / 在做四个数逐人对得上。

    这些数只能从提交与评审来 —— 造数据时故意不碰 ``completion_status``（它仍是
    ``NOT_SUBMITTED``），接口若读了那一列，这里每个数都会是 0。
    """
    board = _new_board(user_client, api_client)
    space_id = board["space_id"]
    first_task = _publish_approved_task(api_client, board, name="第一道题")
    second_task = _publish_approved_task(api_client, board, name="第二道题")

    alice = user_client.create_user()
    alice_token = _login(user_client, api_client, alice)
    bob = user_client.create_user()
    bob_token = _login(user_client, api_client, bob)

    # 甲：第一道题交了、还没判；第二道题交了、判过通过。
    alice_first = _join_and_approve(api_client, board, alice_token, task_id=first_task)
    _submit(api_client, alice_token, task_id=first_task, membership_id=alice_first)
    alice_second = _join_and_approve(
        api_client, board, alice_token, task_id=second_task
    )
    submission = _submit(
        api_client, alice_token, task_id=second_task, membership_id=alice_second
    )
    _review(
        api_client,
        board,
        task_id=second_task,
        membership_id=alice_second,
        submission_id=submission,
        accepted=True,
    )

    # 乙：领了第一道题，一版都没交。
    bob_first = _join_and_approve(api_client, board, bob_token, task_id=first_task)
    assert bob_first > 0

    people = _people(api_client, board["creator_token"], space_id)

    assert _row_for(people, alice.user_id) == {
        "userId": alice.user_id,
        "name": alice.nickname,
        "isTeam": False,
        "claims": 2,
        "passed": 1,
        "submitted": 1,
        "inProgress": 0,
    }
    assert _row_for(people, bob.user_id) == {
        "userId": bob.user_id,
        "name": bob.nickname,
        "isTeam": False,
        "claims": 1,
        "passed": 0,
        "submitted": 0,
        "inProgress": 1,
    }


def test_a_plain_member_and_an_outsider_are_answered_like_the_other_analytics_routes(
    api_client: TestClient, user_client: UserCreator
):
    """普通成员 403、外人 404 —— 与同一组分析接口同一个门、同一个口径。

    逐人那一格是成员参与的明细（谁领了几道、卡在哪一步），和参与者导出同一类
    东西，所以它和 ``/analytics/participants`` 一起挂在管理员版面上。
    """
    board = _new_board(user_client, api_client)

    member = user_client.create_user()
    member_token = _login(user_client, api_client, member)
    added = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": member.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert added.status_code == 201, added.text

    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)

    member_read = api_client.get(
        f"/spaces/{board['space_id']}/analytics/people",
        headers=_auth(member_token),
    )
    assert member_read.status_code == 403, member_read.text

    outsider_read = api_client.get(
        f"/spaces/{board['space_id']}/analytics/people",
        headers=_auth(outsider_token),
    )
    assert outsider_read.status_code == 404, outsider_read.text

    owner_read = api_client.get(
        f"/spaces/{board['space_id']}/analytics/people",
        headers=_auth(board["creator_token"]),
    )
    assert owner_read.status_code == 200, owner_read.text


def test_a_claim_on_another_board_does_not_show_up(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal,
):
    """同一个人在两块板上各领一道题：每块板只看得见自己那一道。

    逐人那一格**和**「领了没动」那一格都是按板捞的，漏了这层 where，别块板的
    领取会悄悄加进这个人的数里、也会替别块板报出一条不归它管的陈账。所以这里
    把本板上那条领取**真的放旧**：只有「在这里、且旧」时才该出现。
    """
    here = _new_board(user_client, api_client)
    elsewhere = _new_board(user_client, api_client)

    someone = user_client.create_user()
    token = _login(user_client, api_client, someone)

    home_task = _publish_approved_task(api_client, here, name="这块板的题")
    away_task = _publish_approved_task(api_client, elsewhere, name="别块板的题")
    home_claim = _join_and_approve(api_client, here, token, task_id=home_task)
    _join_and_approve(api_client, elsewhere, token, task_id=away_task)
    _portal.call(_backdate_claim, db_session, home_claim, 20)

    people = _people(api_client, here["creator_token"], here["space_id"])
    assert _row_for(people, someone.user_id)["claims"] == 1
    assert [row["taskId"] for row in people["stalled"]] == [home_task]

    # 那条旧领取是这块板的事：别块板这一格空着，不替它报账。
    other = _people(api_client, elsewhere["creator_token"], elsewhere["space_id"])
    assert [row["taskId"] for row in other["stalled"]] == []


def test_deleted_tasks_and_deleted_claims_are_not_counted(
    api_client: TestClient, user_client: UserCreator
):
    """撤掉的领取、删掉的题都不算数 —— 连那个人本身也该从表里消失。

    一个人只剩「已撤的领取」和「已删的题上的领取」时，这一格没有他一件事可说；
    留下一个 0 行的名字会让看板读成「他领过、什么都没做」。
    """
    board = _new_board(user_client, api_client)
    space_id = board["space_id"]
    kept_task = _publish_approved_task(api_client, board, name="留下的题")
    deleted_task = _publish_approved_task(api_client, board, name="删掉的题")

    alice = user_client.create_user()
    alice_token = _login(user_client, api_client, alice)
    dropper = user_client.create_user()
    dropper_token = _login(user_client, api_client, dropper)

    alice_on_kept = _join_and_approve(api_client, board, alice_token, task_id=kept_task)
    _join_and_approve(api_client, board, alice_token, task_id=deleted_task)
    removed = _join_and_approve(api_client, board, dropper_token, task_id=kept_task)

    assert (
        api_client.delete(
            f"/tasks/{deleted_task}", headers=_auth(board["creator_token"])
        ).status_code
        == 204
    )
    assert (
        api_client.delete(
            f"/tasks/{kept_task}/participants/{removed}",
            headers=_auth(board["creator_token"]),
        ).status_code
        == 204
    )

    people = _people(api_client, board["creator_token"], space_id)

    assert alice_on_kept > 0
    # 甲在删掉的题上的那条领取不算，只剩留下的那道题上的那一条。
    assert _row_for(people, alice.user_id)["claims"] == 1
    # 撤回领取的人在这张表上不该留下一个 0 行的自己。
    assert [row["userId"] for row in people["people"]] == [alice.user_id]
    assert people["stalled"] == []


def test_a_review_that_passes_moves_a_claim_from_in_progress_to_passed(
    api_client: TestClient, user_client: UserCreator
):
    """同一条领取走三步：在做 → 已交 → 通过，四个数每一步都跟着走。

    只测终态看不出「已交」是不是真按评审算的；这一步一步来，接口若把「交过但没判」
    算成通过（或算成在做），中间那一步必红。
    """
    board = _new_board(user_client, api_client)
    space_id = board["space_id"]
    task_id = _publish_approved_task(api_client, board, name="一步步来的题")

    student = user_client.create_user()
    token = _login(user_client, api_client, student)
    membership_id = _join_and_approve(api_client, board, token, task_id=task_id)

    def counts() -> dict:
        row = _row_for(
            _people(api_client, board["creator_token"], space_id), student.user_id
        )
        return {k: row[k] for k in ("claims", "passed", "submitted", "inProgress")}

    # 领了、没交 → 在做。
    assert counts() == {"claims": 1, "passed": 0, "submitted": 0, "inProgress": 1}

    submission_id = _submit(
        api_client, token, task_id=task_id, membership_id=membership_id
    )
    # 交了、还在队列里 → 已交（不是在做，也还不是通过）。
    assert counts() == {"claims": 1, "passed": 0, "submitted": 1, "inProgress": 0}

    _review(
        api_client,
        board,
        task_id=task_id,
        membership_id=membership_id,
        submission_id=submission_id,
        accepted=True,
    )
    # 通过 → 从「已交」挪到「通过」。
    assert counts() == {"claims": 1, "passed": 1, "submitted": 0, "inProgress": 0}


def test_stalled_lists_only_claims_older_than_fourteen_days_without_a_submission(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal,
):
    """四条领取：只列那条「超过 14 天、且一版都没交」的。

    - 15 天前领的、没交 → 在名单里（题号、题名、人、领取时间都要对得上）；
    - 15 天前领的、交过一版 → 不在（交过就是动过，判没判另说）；
    - 3 天前领的、没交 → 不在（还没到 14 天）；
    - 昨天领的、没交 → 不在。
    """
    board = _new_board(user_client, api_client)
    space_id = board["space_id"]
    stale_task = _publish_approved_task(api_client, board, name="两周没人动的题")
    moved_task = _publish_approved_task(api_client, board, name="交过一版的题")
    fresh_task = _publish_approved_task(api_client, board, name="刚领的题")

    idle = user_client.create_user()
    idle_token = _login(user_client, api_client, idle)
    mover = user_client.create_user()
    mover_token = _login(user_client, api_client, mover)
    newcomer = user_client.create_user()
    newcomer_token = _login(user_client, api_client, newcomer)

    stale = _join_and_approve(api_client, board, idle_token, task_id=stale_task)
    moved = _join_and_approve(api_client, board, mover_token, task_id=moved_task)
    fresh = _join_and_approve(api_client, board, newcomer_token, task_id=fresh_task)
    _submit(api_client, mover_token, task_id=moved_task, membership_id=moved)

    for membership_id, days in ((stale, 15), (moved, 15), (fresh, 3)):
        _portal.call(_backdate_claim, db_session, membership_id, days)

    people = _people(api_client, board["creator_token"], space_id)

    assert [(row["taskId"], row["name"]) for row in people["stalled"]] == [
        (stale_task, idle.nickname)
    ]
    entry = people["stalled"][0]
    assert entry["taskTitle"] == "两周没人动的题"
    assert entry["userId"] == idle.user_id
    assert entry["isTeam"] is False
    # 领取时间是接口回的毫秒时间戳，且确实是 15 天前那一下。
    claim_ms = entry["claimedAt"]
    assert (
        pytest.approx(claim_ms, abs=120_000)
        == (datetime.now(UTC) - timedelta(days=15)).timestamp() * 1000
    )
