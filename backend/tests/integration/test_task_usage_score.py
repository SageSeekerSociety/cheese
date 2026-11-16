"""额度用量评价：名单和提交里读到每份领题用掉的额度，评分要点里的额度那一行
按阈值或排名给分，评审把手动分和额度分合成总分，排名题截止后定稿。"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.domain.project.models import Project
from app.domain.task import usage_score
from app.domain.task.models import Task, TaskMembership
from app.domain.team.models import Team
from app.domain.usage.models import ResourceUsage
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    join_task_space,
    unique_int,
)

MANUAL = [{"item": "正确性", "points": 80, "criteria": "全对"}]
THRESHOLD = {
    "item": "额度用量",
    "points": 20,
    "criteria": "",
    "kind": "usage",
    "config": {"mode": "threshold", "full": 50, "zero": 200},
}
RANK = {**THRESHOLD, "config": {"mode": "rank"}}


def _login(user_client: UserCreator, api_client: TestClient):
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _h(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


def _run(board, fn):
    """在这条用例的事务里跑一段库操作（和接口共用同一个会话）。"""

    async def _go():
        out = await fn(board["db"])
        await board["db"].flush()
        return out

    return board["portal"].call(_go)


@pytest.fixture
def board(
    user_client: UserCreator, api_client: TestClient, db_session, _portal
) -> dict:
    creator = _login(user_client, api_client)
    space = create_approved_space(
        api_client,
        json={
            "name": f"Usage Space ({unique_int(10000000, 99999999)})",
            "intro": "usage",
            "description": "A lengthy description. " * 20,
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_h(creator),
    ).json()["data"]["space"]
    return {
        "creator": creator,
        "space": space,
        "users": user_client,
        "db": db_session,
        "portal": _portal,
    }


def _task(api_client: TestClient, board: dict, rubric: list, **extra):
    return api_client.post(
        "/tasks",
        json={
            "name": f"CacheLab ({unique_int(1000, 9999)})",
            "submitterType": "USER",
            "deadline": int((datetime.now(UTC) + timedelta(days=7)).timestamp() * 1000),
            "resubmittable": True,
            "editable": True,
            "intro": "Cache simulator " * 5,
            "description": "Write csim " * 10,
            "submissionSchema": [{"prompt": "csim", "type": "TEXT"}],
            "space": board["space"]["id"],
            "categoryId": board["space"]["defaultCategoryId"],
            "rubric": rubric,
            **extra,
        },
        headers=_h(board["creator"]),
    )


def _published(api_client, board, rubric, **extra) -> int:
    resp = _task(api_client, board, rubric, **extra)
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}", json={"approved": "APPROVED"}, headers=_h(board["creator"])
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _student(api_client, board, task_id: int, credits: list[tuple], submit=True):
    """一个领了题、开了项目、在项目里花了 ``credits``（[(额度, 模型, 记在谁名下)]）
    的学生；``submit`` 时交一版。"""
    student = _login(board["users"], api_client)
    join_task_space(
        api_client, task_id, student.user_id, admin_headers=_h(board["creator"])
    )
    joined = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": student.user_id},
        json={},
        headers=_h(student),
    )
    assert joined.status_code == 200, joined.text
    mid = joined.json()["data"]["participant"]["id"]
    ok = api_client.patch(
        f"/tasks/{task_id}/participants/{mid}",
        json={"approved": "APPROVED"},
        headers=_h(board["creator"]),
    )
    assert ok.status_code == 200, ok.text

    async def _seed(session):
        team_id = await session.scalar(
            select(Team.id).where(Team.personal_owner_user_id == student.user_id)
        )
        assert team_id is not None
        project = Project(
            team_id=team_id,
            name=f"P-{unique_int(1000, 9999)}",
            owner_handle=student.username,
            external_task_id=task_id,
        )
        session.add(project)
        await session.flush()
        await session.execute(
            update(TaskMembership)
            .where(TaskMembership.id == mid)
            .values(agreed_mentors=["mentor-x"])
        )
        for amount, model, owner in credits:
            session.add(
                ResourceUsage(
                    project_id=project.id,
                    model=model,
                    credits=amount,
                    credit_owner_handle=owner,
                    kind="chat",
                    route="gateway",
                )
            )

    _run(board, _seed)
    sub_id = None
    if submit:
        handed = api_client.post(
            f"/tasks/{task_id}/participants/{mid}/submissions",
            json=[{"text": "csim"}],
            headers=_h(student),
        )
        assert handed.status_code == 200, handed.text
        sub_id = handed.json()["data"]["submission"]["id"]
    return {"user": student, "mid": mid, "sub": sub_id}


def _roster(api_client, board, task_id) -> dict:
    resp = api_client.get(
        f"/tasks/{task_id}/participants", headers=_h(board["creator"])
    )
    assert resp.status_code == 200, resp.text
    return {p["id"]: p["usage"] for p in resp.json()["data"]["participants"]}


def _review(api_client, board, task_id, who, **body):
    return api_client.post(
        f"/tasks/{task_id}/participants/{who['mid']}/submissions/{who['sub']}/review",
        json={"accepted": True, "score": 0, "comment": "", **body},
        headers=_h(board["creator"]),
    )


def test_an_automatic_rubric_must_total_100_and_has_one_usage_line(
    api_client: TestClient, board: dict
):
    short = _task(api_client, board, [{"item": "正确性", "points": 70}, THRESHOLD])
    assert short.status_code == 400
    twice = _task(
        api_client, board, [{"item": "正确性", "points": 60}, THRESHOLD, THRESHOLD]
    )
    assert twice.status_code == 400
    good = _task(api_client, board, [*MANUAL, THRESHOLD])
    assert good.status_code == 200, good.text
    task_id = good.json()["data"]["task"]["id"]
    rubric = api_client.get(f"/tasks/{task_id}", headers=_h(board["creator"])).json()[
        "data"
    ]["task"]["rubric"]
    assert rubric[0] == {**MANUAL[0], "stage": None}
    assert rubric[1]["kind"] == "usage"
    assert rubric[1]["config"] == {"mode": "threshold", "full": 50.0, "zero": 200.0}


def test_threshold_scores_each_claim_and_leaves_mentor_spending_out(
    api_client: TestClient, board: dict
):
    task_id = _published(api_client, board, [*MANUAL, THRESHOLD])
    lean = _student(api_client, board, task_id, [(38.6, "glm", None)])
    heavy = _student(
        api_client,
        board,
        task_id,
        [(96.0, "kimi", "someone"), (36.4, "glm", None), (500.0, "glm", "mentor-x")],
    )
    idle = _student(api_client, board, task_id, [(12.3, "glm", None)], submit=False)

    usage = _roster(api_client, board, task_id)
    assert usage[lean["mid"]]["credits"] == 38.6
    assert usage[lean["mid"]]["score"] == 20
    assert usage[heavy["mid"]]["credits"] == 132.4  # 导师那 500 不算
    assert usage[heavy["mid"]]["score"] == 9
    assert usage[heavy["mid"]]["final"] is True
    assert usage[idle["mid"]]["credits"] == 12.3
    assert usage[idle["mid"]]["score"] is None  # 没交作业不给分

    mine = api_client.get(
        f"/tasks/{task_id}/participants/{heavy['mid']}/submissions",
        headers=_h(heavy["user"]),
    ).json()["data"]["usage"]
    assert mine["score"] == 9
    assert mine["models"] == [
        {"model": "kimi", "credits": 96.0},
        {"model": "glm", "credits": 36.4},
    ]


def test_a_review_adds_the_usage_score_to_the_manual_points(
    api_client: TestClient, board: dict
):
    task_id = _published(api_client, board, [*MANUAL, THRESHOLD])
    heavy = _student(api_client, board, task_id, [(132.4, "glm", None)])

    too_much = _review(api_client, board, task_id, heavy, score=81)
    assert too_much.status_code == 400

    made = _review(api_client, board, task_id, heavy, score=62)
    assert made.status_code == 200, made.text
    detail = made.json()["data"]["review"]["detail"]
    assert detail["score"] == 71
    assert detail["autoScores"]["manual"] == 62
    assert detail["autoScores"]["lines"][0]["score"] == 9

    # 只改通过与否：驳回不记额度分，改回通过时沿用原来的手动分。
    path = f"/tasks/{task_id}/participants/{heavy['mid']}/submissions/{heavy['sub']}/review"
    rejected = api_client.patch(
        path, json={"accepted": False}, headers=_h(board["creator"])
    )
    assert rejected.status_code == 200, rejected.text
    assert "autoScores" not in rejected.json()["data"]["review"]["detail"]


def test_a_task_without_a_usage_line_shows_credits_and_scores_as_before(
    api_client: TestClient, board: dict
):
    task_id = _published(api_client, board, [{"item": "正确性", "points": 7}])
    one = _student(api_client, board, task_id, [(40.0, "glm", None)])
    assert _roster(api_client, board, task_id)[one["mid"]] == {"credits": 40.0}
    made = _review(api_client, board, task_id, one, score=85)
    assert made.status_code == 200, made.text
    detail = made.json()["data"]["review"]["detail"]
    assert detail["score"] == 85
    assert "autoScores" not in detail


def test_a_private_rubric_hides_the_usage_score_from_the_student(
    api_client: TestClient, board: dict
):
    task_id = _published(api_client, board, [*MANUAL, THRESHOLD], rubricPublic=False)
    one = _student(api_client, board, task_id, [(40.0, "glm", None)])
    mine = api_client.get(
        f"/tasks/{task_id}/participants/{one['mid']}/submissions",
        headers=_h(one["user"]),
    ).json()["data"]["usage"]
    assert mine["credits"] == 40.0
    assert "score" not in mine and "line" not in mine
    graded = api_client.get(
        f"/tasks/{task_id}/participants/{one['mid']}/submissions",
        headers=_h(board["creator"]),
    ).json()["data"]["usage"]
    assert graded["score"] == 20


def test_rank_is_provisional_until_the_deadline_then_finalized(
    api_client: TestClient, board: dict
):
    task_id = _published(api_client, board, [*MANUAL, RANK])
    a = _student(api_client, board, task_id, [(10.0, "glm", None)])
    b = _student(api_client, board, task_id, [(30.0, "glm", None)])

    usage = _roster(api_client, board, task_id)
    assert (usage[a["mid"]]["score"], usage[b["mid"]]["score"]) == (20, 0)
    assert usage[a["mid"]]["final"] is False

    made = _review(api_client, board, task_id, b, score=70)
    assert made.json()["data"]["review"]["detail"]["score"] == 70
    assert made.json()["data"]["review"]["detail"]["autoScores"]["final"] is False

    # 截止前又有人交了、用得更多：b 不再垫底。
    _student(api_client, board, task_id, [(90.0, "glm", None)])

    async def _close(session):
        await session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(deadline=datetime.now(UTC) - timedelta(minutes=1))
        )

    _run(board, _close)
    assert _run(board, usage_score.finalize_ranked_reviews) == 1
    assert _run(board, usage_score.finalize_ranked_reviews) == 0

    review = api_client.get(
        f"/tasks/{task_id}/participants/{b['mid']}/submissions/{b['sub']}/review",
        headers=_h(board["creator"]),
    ).json()["data"]["review"]["detail"]
    # 三人里 b 只比 1 人用得少：20 × 1 ÷ 2 = 10。
    assert review["score"] == 80
    assert review["autoScores"]["final"] is True
    assert _roster(api_client, board, task_id)[b["mid"]]["final"] is True
