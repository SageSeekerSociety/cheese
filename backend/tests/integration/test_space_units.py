"""教学单元 —— 一门课的时间线，以及「发布了才有」。

产品把课程定义成一串教学单元（周次 / 知识点 / 课件 / 作业 / 发布与截止）。本文件按
浏览器会收到的状态码断言两件事：

1. **成员只看得到发布过的**（``published_at`` 为 NULL 的单元对成员不存在）—— 这是
   「随课程推进才能得到更多知识」的默认实现，不靠管理员每周记得改配置；
2. **写的一次只有管理员能过**，门外人连这个题目板都看不到（404，不是 403）。

判据不在这里：管理员是谁问 ``app.auth.space_access.is_space_admin``，可见性问
``_ensure_space_visible``。这里测的是它的可观测后果。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

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
    """建版的人（管理员 / OWNER）+ 一块已过审的题目板。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Course Units ({suffix})",
            "intro": "一门课",
            "description": "一个题目板",
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
        "category_id": space.get("defaultCategoryId"),
    }


def _join(api_client: TestClient, board: dict, user_id: int) -> dict[str, str]:
    """把人加进这个题目板，返回他的登录头。成员就是这么进来的。"""
    resp = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user_id},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code in (200, 201), resp.text
    return resp


def _create_task(api_client: TestClient, board: dict, *, name: str) -> int:
    import time

    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
            "intro": "本周作业",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _create_unit(
    api_client: TestClient,
    board: dict,
    *,
    week: int,
    title: str,
    published: bool = False,
    assignment_task_id: int | None = None,
    token: str | None = None,
):
    return api_client.post(
        f"/spaces/{board['space_id']}/units",
        json={
            "week": week,
            "title": title,
            "summary": f"第 {week} 周",
            "knowledgePointIds": [],
            "materialIds": [],
            "assignmentTaskId": assignment_task_id,
            "published": published,
        },
        headers=_auth(token or board["creator_token"]),
    )


def _list_units(api_client: TestClient, board: dict, token: str) -> dict:
    return api_client.get(f"/spaces/{board['space_id']}/units", headers=_auth(token))


def _weeks(payload: dict) -> list[int]:
    return [unit["week"] for unit in payload["data"]["units"]]


# --- 1. 发布了才有 -----------------------------------------------------------


def test_students_see_only_the_weeks_that_were_published(
    api_client: TestClient, user_client: UserCreator
):
    """管理员建第 3 周与第 8 周，只发布第 3 周。

    成员拿到的列表里没有第 8 周 —— 不是前端藏了，是接口就没给。管理员自己两条都看得到，
    否则他没法继续编排还没到的那几周。
    """
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    assert (
        _create_unit(
            api_client, board, week=3, title="循环与数组", published=True
        ).status_code
        == 201
    )
    assert (
        _create_unit(
            api_client, board, week=8, title="递归与分治", published=False
        ).status_code
        == 201
    )

    student_view = _list_units(api_client, board, student_token)
    assert student_view.status_code == 200, student_view.text
    assert _weeks(student_view.json()) == [3]
    assert student_view.json()["data"]["canTeach"] is False

    teacher_view = _list_units(api_client, board, board["creator_token"])
    assert teacher_view.status_code == 200, teacher_view.text
    assert _weeks(teacher_view.json()) == [3, 8]
    assert teacher_view.json()["data"]["canTeach"] is True


def test_publishing_a_later_week_reaches_the_student_and_taking_one_back_hides_it(
    api_client: TestClient, user_client: UserCreator
):
    """发布是一步动作，不是建单元时的选项：管理员先建好第 4 周，等他真讲到再放出去。

    同一条 PATCH 也是「撤回发布」：published=false 之后成员立刻看不到它。
    """
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    created = _create_unit(api_client, board, week=4, title="指针", published=False)
    assert created.status_code == 201, created.text
    unit_id = created.json()["data"]["unit"]["id"]
    assert _weeks(_list_units(api_client, board, student_token).json()) == []

    published = api_client.patch(
        f"/spaces/{board['space_id']}/units/{unit_id}",
        json={"published": True},
        headers=_auth(board["creator_token"]),
    )
    assert published.status_code == 200, published.text
    assert _weeks(_list_units(api_client, board, student_token).json()) == [4]

    withdrawn = api_client.patch(
        f"/spaces/{board['space_id']}/units/{unit_id}",
        json={"published": False},
        headers=_auth(board["creator_token"]),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert _weeks(_list_units(api_client, board, student_token).json()) == []


def test_a_unit_carries_the_weeks_assignment(
    api_client: TestClient, user_client: UserCreator
):
    """作业就用一道正式的题承载 —— 单元挂的是它的 id，提交与评审仍是那一套。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="第 5 周作业")

    created = _create_unit(
        api_client,
        board,
        week=5,
        title="数组",
        published=True,
        assignment_task_id=task_id,
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["unit"]["assignmentTaskId"] == task_id


def test_a_week_cannot_hand_out_another_boards_problem(
    api_client: TestClient, user_client: UserCreator
):
    """挂别人的题会让成员的提交落进他没有名册的项目 —— 这是要当场拒掉的。"""
    board = _new_board(user_client, api_client)
    other_board = _new_board(user_client, api_client)
    foreign_task = _create_task(api_client, other_board, name="别的版的题")

    refused = _create_unit(
        api_client,
        board,
        week=1,
        title="第一周",
        assignment_task_id=foreign_task,
    )
    assert refused.status_code == 400, refused.text


def test_a_week_belongs_to_a_numbered_week(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    assert _create_unit(api_client, board, week=0, title="第零周").status_code == 400


# --- 2. 谁写得了 -------------------------------------------------------------


def test_a_student_cannot_build_or_change_the_timeline(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    _join(api_client, board, student.user_id)

    created = _create_unit(api_client, board, week=2, title="循环", published=True)
    unit_id = created.json()["data"]["unit"]["id"]

    assert (
        _create_unit(
            api_client, board, week=6, title="我自己加的", token=student_token
        ).status_code
        == 403
    )
    assert (
        api_client.patch(
            f"/spaces/{board['space_id']}/units/{unit_id}",
            json={"title": "改个名字"},
            headers=_auth(student_token),
        ).status_code
        == 403
    )
    assert (
        api_client.delete(
            f"/spaces/{board['space_id']}/units/{unit_id}",
            headers=_auth(student_token),
        ).status_code
        == 403
    )


def test_an_outsider_cannot_see_that_the_timeline_exists(
    api_client: TestClient, user_client: UserCreator
):
    """不在这个题目板里的人先答 404 —— 一个你没有的版不该被确认存在。"""
    board = _new_board(user_client, api_client)
    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)

    assert _list_units(api_client, board, outsider_token).status_code == 404
    assert (
        _create_unit(
            api_client, board, week=1, title="不是我的版", token=outsider_token
        ).status_code
        == 404
    )


def test_deleting_a_unit_takes_it_out_of_the_timeline(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    created = _create_unit(api_client, board, week=7, title="要删的", published=True)
    unit_id = created.json()["data"]["unit"]["id"]

    deleted = api_client.delete(
        f"/spaces/{board['space_id']}/units/{unit_id}",
        headers=_auth(board["creator_token"]),
    )
    assert deleted.status_code == 204, deleted.text
    assert _weeks(_list_units(api_client, board, board["creator_token"]).json()) == []
