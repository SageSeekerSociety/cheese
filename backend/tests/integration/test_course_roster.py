"""课程里的人：教师看到的学生、他们的项目、他们的组。

课程模板「学生与分组」那一屏的数据（``GET /spaces/{spaceId}/course/roster``）。
三件必须是真的：

1. **名单上是学生** —— 成员表里的每个人，减去本版管理员（一位老师也可以是成员，
   但他出现在学生名单里只会让人数说谎）；
2. **项目与人对得上** —— 项目记的是 ``owner_handle``，名单给人看的昵称，两者要在
   服务端合成一条，不能把这个 join 丢给前端；
3. **门只对教师开** —— 学生读到 403，门外人仍是 404（不做存在性确认）。

机制全部复用别处：成员表（``space_member``）、项目（锚在这版的赛题上）、小队。
本文件测的是它们被摆成「一门课」之后的可观测结果。
"""

from __future__ import annotations

import time

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
    """建版的人（= 本版 OWNER / 教师）+ 一块已过审的题目板。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Course Roster ({suffix})",
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


def _create_task(api_client: TestClient, board: dict, *, name: str) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": name,
            "intro": "题",
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


def _create_team(api_client: TestClient, token: str, *, name: str) -> int:
    resp = api_client.post(
        "/teams",
        json={"name": name, "intro": "", "description": "", "avatarId": 1},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["team"]["id"]


def _create_project(
    api_client: TestClient, token: str, *, owner: str, task_id: int, team_id: int | None
) -> str:
    body: dict = {
        "name": "学生的项目",
        "owner_handle": owner,
        "external_task_id": task_id,
    }
    if team_id is not None:
        body["team_id"] = team_id
    resp = api_client.post("/projects", json=body, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


# --- 1. 教师拿到的是学生、他们的项目、他们的组 ---------------------------------


def test_a_teacher_reads_the_students_their_projects_and_their_teams(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="这门课的题")

    alice = user_client.create_user()
    alice_token = _login(user_client, api_client, alice)
    bob = user_client.create_user()

    team_id = _create_team(api_client, alice_token, name="第一组")
    assert (
        api_client.post(
            f"/teams/{team_id}/members",
            json={"userId": bob.user_id},
            headers=_auth(alice_token),
        ).status_code
        == 201
    )
    project_id = _create_project(
        api_client, alice_token, owner=alice.username, task_id=task_id, team_id=team_id
    )

    for user in (alice, bob):
        assert (
            api_client.post(
                f"/spaces/{board['space_id']}/members",
                json={"userId": user.user_id},
                headers=_auth(board["creator_token"]),
            ).status_code
            == 201
        )

    resp = api_client.get(
        f"/spaces/{board['space_id']}/course/roster",
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    students = {row["user"]["id"]: row for row in data["students"]}
    assert set(students) == {alice.user_id, bob.user_id}
    # 建版的人（教师）不在学生名单里 —— 他不在 member 表里，本来也不该在。
    assert board["creator"].user_id not in students

    # 项目挂在它主人的那一行上，队也对上了。
    assert [p["id"] for p in students[alice.user_id]["projects"]] == [project_id]
    assert students[alice.user_id]["teamIds"] == [team_id]
    assert students[alice.user_id]["user"]["nickname"]
    # 组了队、还没有项目的人：一行在，项目是空的。
    assert students[bob.user_id]["projects"] == []
    assert students[bob.user_id]["teamIds"] == []

    teams = {team["id"]: team for team in data["teams"]}
    assert set(teams) == {team_id}
    assert teams[team_id]["name"] == "第一组"
    assert {row["id"] for row in teams[team_id]["members"]} == {
        alice.user_id,
        bob.user_id,
    }


# --- 2. 门只对教师开 -----------------------------------------------------------


def test_a_student_member_cannot_read_the_roster(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    assert (
        api_client.post(
            f"/spaces/{board['space_id']}/members",
            json={"userId": student.user_id},
            headers=_auth(board["creator_token"]),
        ).status_code
        == 201
    )

    resp = api_client.get(
        f"/spaces/{board['space_id']}/course/roster", headers=_auth(student_token)
    )
    assert resp.status_code == 403, resp.text


def test_an_outsider_does_not_learn_the_board_exists(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)

    resp = api_client.get(
        f"/spaces/{board['space_id']}/course/roster", headers=_auth(outsider_token)
    )
    # 404 而不是 403：403 等于确认这块板存在。
    assert resp.status_code == 404, resp.text


# --- 3. 教师做管理员之后也在名单外 ---------------------------------------------


def test_someone_made_a_teacher_leaves_the_student_list(
    api_client: TestClient, user_client: UserCreator
):
    """一位「既是成员、又被设成管理员」的人：他算教师，不再算学生。

    成员表与管理员表是两件事（``space_member`` 管谁能看见，``space_admin_relation``
    管谁能管理），同一个人可以两行都有。名单按后者把他摘出去。
    """
    board = _new_board(user_client, api_client)
    helper = user_client.create_user()
    helper_token = _login(user_client, api_client, helper)
    assert (
        api_client.post(
            f"/spaces/{board['space_id']}/members",
            json={"userId": helper.user_id},
            headers=_auth(board["creator_token"]),
        ).status_code
        == 201
    )

    before = api_client.get(
        f"/spaces/{board['space_id']}/course/roster",
        headers=_auth(board["creator_token"]),
    ).json()["data"]
    assert helper.user_id in {row["user"]["id"] for row in before["students"]}

    assert (
        api_client.post(
            f"/spaces/{board['space_id']}/managers",
            json={"userId": helper.user_id, "role": "ADMIN"},
            headers=_auth(board["creator_token"]),
        ).status_code
        == 201
    )

    after = api_client.get(
        f"/spaces/{board['space_id']}/course/roster", headers=_auth(helper_token)
    ).json()["data"]
    assert helper.user_id not in {row["user"]["id"] for row in after["students"]}
