"""教师 = 题目板的管理员 —— 一句话在五扇门上的后果。

产品把定义拍死了：题目板 = 一门课 / 一个活动，教师 = 这个题目板的管理员（库里
``space_admin_relation`` 里的一行，建版的人是 OWNER）。于是有三件事必须是真的，
本文件逐条按浏览器会收到的状态码来断：

1. **教师读得到学生项目里的对话**（``app.auth.project_access`` 的第五条主张）；
2. **教师打得了分**（评审四个接口）；
3. **学生发不了题**（发布收权）；
4. **学生导不出参与者花名册**（明文个人信息，403 而不是空 CSV）；
5. **撤掉管理员，下一次请求就必须读不到** —— 判据每次现查，没有缓存。

判据全部收敛在 ``app.auth.space_access``；这里测的是它的可观测后果，不看实现。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    post_project,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    """建版的人 + 一块已通过审核的题目板。建版的人就是它的 OWNER（教师）。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Board Teacher ({suffix})",
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


def _make_admin(
    api_client: TestClient, board: dict, user_id: int, *, by_token: str | None = None
):
    return api_client.post(
        f"/spaces/{board['space_id']}/managers",
        json={"userId": user_id, "role": "ADMIN"},
        headers=_auth(by_token or board["creator_token"]),
    )


def _make_member(
    api_client: TestClient, board: dict, user_id: int, *, by_token: str | None = None
):
    return api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user_id},
        headers=_auth(by_token or board["creator_token"]),
    )


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


# --- 1. 教师读得到学生项目里的对话 -------------------------------------------


def test_a_board_admin_reads_the_conversations_of_its_students_projects(
    api_client: TestClient, user_client: UserCreator
):
    """项目归学生所有、学生开的；教师不在名册上、不在小队里。他进来的凭据是
    「这门课的管理员」——``Project.external_task_id`` → ``Task.space_id`` →
    ``SpaceAdminRelation``。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="这门课的题")

    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    project = post_project(
        api_client,
        json={
            "name": "学生的项目",
            "owner_handle": student.username,
            "external_task_id": task_id,
        },
        headers=_auth(student_token),
    )
    assert project.status_code == 200, project.text
    pid = project.json()["data"]["id"]

    assert (
        api_client.get(f"/projects/{pid}", headers=_auth(teacher_token)).status_code
        == 200
    )
    assert (
        api_client.get(
            "/topics", params={"project_id": pid}, headers=_auth(teacher_token)
        ).status_code
        == 200
    )


def test_a_plain_member_of_the_board_is_not_a_teacher(
    api_client: TestClient, user_client: UserCreator
):
    """教师 ≠ 这个板里的每一个人。填码进来的是学生，读不到别人组的对话 ——
    这正是 ``app.auth.project_access`` 顶部记的那个「把全班每个组的对话交给全班」
    的越权，换一层说法也一样。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="这门课的题")

    # 板里有一位教师（管理员），但下面被测的不是他。
    teacher = user_client.create_user()
    _login(user_client, api_client, teacher)
    _make_admin(api_client, board, teacher.user_id)

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    project = post_project(
        api_client,
        json={
            "name": "学生的项目",
            "owner_handle": student.username,
            "external_task_id": task_id,
        },
        headers=_auth(student_token),
    )
    pid = project.json()["data"]["id"]

    # 另一个学生：是这块板里的学生（成员），但没有任何管理权。
    peer = user_client.create_user()
    peer_token = _login(user_client, api_client, peer)
    assert _make_member(api_client, board, peer.user_id).status_code == 201

    assert (
        api_client.get(f"/projects/{pid}", headers=_auth(peer_token)).status_code == 403
    )


def test_removing_a_board_admin_shuts_the_door_immediately(
    api_client: TestClient, user_client: UserCreator
):
    """撤权立刻生效：同一个 URL、同样的参数，中间只多了一次管理员关系的软删。
    读路径上任何形式的缓存都会在这里留下撤权前的那一次 200。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="这门课的题")

    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    project = post_project(
        api_client,
        json={
            "name": "学生的项目",
            "owner_handle": student.username,
            "external_task_id": task_id,
        },
        headers=_auth(student_token),
    )
    pid = project.json()["data"]["id"]

    assert (
        api_client.get(f"/projects/{pid}", headers=_auth(teacher_token)).status_code
        == 200
    )

    removed = api_client.delete(
        f"/spaces/{board['space_id']}/managers/{teacher.user_id}",
        headers=_auth(board["creator_token"]),
    )
    assert removed.status_code == 204, removed.text

    assert (
        api_client.get(f"/projects/{pid}", headers=_auth(teacher_token)).status_code
        == 403
    )


# --- 2. 教师打得了分 ---------------------------------------------------------


def _submission_ready(api_client: TestClient, user_client: UserCreator) -> dict:
    """一块板、一道题、一个报名并提交了的学生，外加一位教师（管理员）。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="要打分的题")

    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201

    participant = user_client.create_user()
    participant_token = _login(user_client, api_client, participant)

    approve_task = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approve_task.status_code == 200, approve_task.text

    join = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": participant.user_id},
        json={},
        headers=_auth(participant_token),
    )
    assert join.status_code == 200, join.text
    membership_id = join.json()["data"]["participant"]["id"]

    api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    submit = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        json=[{"text": "这是提交。"}],
        headers=_auth(participant_token),
    )
    assert submit.status_code == 200, submit.text
    submission_id = submit.json()["data"]["submission"]["id"]

    return {
        **board,
        "task_id": task_id,
        "teacher": teacher,
        "teacher_token": teacher_token,
        "participant": participant,
        "participant_token": participant_token,
        "membership_id": membership_id,
        "submission_id": submission_id,
    }


def test_a_board_admin_can_grade(api_client: TestClient, user_client: UserCreator):
    """出题者**或**该题所在题目板的教师。这里出题者是创建者，打分的人是后来被设
    成管理员的教师 —— 他既不是出题者，也不在名册上。"""
    s = _submission_ready(api_client, user_client)
    resp = api_client.post(
        f"/tasks/{s['task_id']}/participants/{s['membership_id']}"
        f"/submissions/{s['submission_id']}/review",
        json={"accepted": True, "score": 5, "comment": "教师打分"},
        headers=_auth(s["teacher_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["review"]["reviewed"] is True


def test_a_student_cannot_grade(api_client: TestClient, user_client: UserCreator):
    """学生/参与者不是教师：打分这条门对他关着。"""
    s = _submission_ready(api_client, user_client)
    resp = api_client.post(
        f"/tasks/{s['task_id']}/participants/{s['membership_id']}"
        f"/submissions/{s['submission_id']}/review",
        json={"accepted": True, "score": 5, "comment": "自评"},
        headers=_auth(s["participant_token"]),
    )
    assert resp.status_code == 403, resp.text


# --- 3. 学生发不了题 ---------------------------------------------------------


def test_a_student_cannot_publish_a_task(
    api_client: TestClient, user_client: UserCreator
):
    """发布收权：填码进来的学生拿着 space id 也发不了题。"""
    board = _new_board(user_client, api_client)

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    assert _make_member(api_client, board, student.user_id).status_code == 201

    resp = api_client.post(
        "/tasks",
        json={
            "name": "学生想发的题",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
        },
        headers=_auth(student_token),
    )
    assert resp.status_code == 403, resp.text


def test_the_board_creator_can_still_publish_a_task(
    api_client: TestClient, user_client: UserCreator
):
    """收权不能连创建者一起收掉 —— 否则正路也走不通了。"""
    board = _new_board(user_client, api_client)
    assert _create_task(api_client, board, name="教师发的题") > 0


# --- 4. 学生导不出参与者花名册 -----------------------------------------------


def test_a_student_cannot_export_the_participant_roster(
    api_client: TestClient, user_client: UserCreator
):
    """这份 CSV 逐行写着学生的真实姓名、学号、年级、专业、班级、电话、邮箱。学生
    是这块板里的成员、看得见课，但导出是教师的事：403，明确报错，不是一张空 CSV。"""
    board = _new_board(user_client, api_client)

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    assert _make_member(api_client, board, student.user_id).status_code == 201

    denied = api_client.get(
        f"/spaces/{board['space_id']}/analytics/participants/export",
        headers=_auth(student_token),
    )
    assert denied.status_code == 403, denied.text
    assert "ForbiddenError" in denied.text

    # 紧邻那一格（参与者的分组统计）读的是同一批解密出来的身份，同一个门。
    denied_summary = api_client.get(
        f"/spaces/{board['space_id']}/analytics/participants",
        headers=_auth(student_token),
    )
    assert denied_summary.status_code == 403, denied_summary.text

    # 教师（创建者）自己拿得到。
    allowed = api_client.get(
        f"/spaces/{board['space_id']}/analytics/participants/export",
        headers=_auth(board["creator_token"]),
    )
    assert allowed.status_code == 200, allowed.text


def test_an_outsider_still_gets_404_not_403(
    api_client: TestClient, user_client: UserCreator
):
    """门收窄之后，一个不在板里的人不该被 403 确认这块板存在 —— 先答 404。"""
    board = _new_board(user_client, api_client)
    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)
    resp = api_client.get(
        f"/spaces/{board['space_id']}/analytics/participants/export",
        headers=_auth(outsider_token),
    )
    assert resp.status_code == 404, resp.text


# --- 5. 教师对一道题的其余能力 ------------------------------------------------
#
# 发布与评审之外，「老师对这门课里的这道题能做的事」还有一整套：管报名、看名单、
# 看提交、看未过审的草稿、重提审核、编辑/删除题目。它们从前各写了一遍「出题者」，
# 教师那一半时有时无；现在全部问 ``may_teach_task``，与打分、发题同一个判据。


def test_a_board_admin_manages_participants_and_reads_submissions(
    api_client: TestClient, user_client: UserCreator
):
    """管报名、看名单、看提交：教师做得到，普通学生做不到。"""
    s = _submission_ready(api_client, user_client)
    task_id = s["task_id"]
    membership_id = s["membership_id"]

    # 名单与提交列表 —— 教师视角。
    assert (
        api_client.get(
            f"/tasks/{task_id}/participants", headers=_auth(s["teacher_token"])
        ).status_code
        == 200
    )
    assert (
        api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers=_auth(s["teacher_token"]),
        ).status_code
        == 200
    )
    # 管报名：批准这个学生。
    approved = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(s["teacher_token"]),
    )
    assert approved.status_code == 200, approved.text

    # 另一个只是成员的学生：同两条路都关着。
    peer = user_client.create_user()
    peer_token = _login(user_client, api_client, peer)
    assert _make_member(api_client, s, peer.user_id).status_code == 201
    assert (
        api_client.get(
            f"/tasks/{task_id}/participants", headers=_auth(peer_token)
        ).status_code
        == 403
    )
    assert (
        api_client.get(
            f"/tasks/{task_id}/participants/{membership_id}/submissions",
            headers=_auth(peer_token),
        ).status_code
        == 403
    )


def test_a_board_admin_sees_an_unapproved_task_and_can_resubmit(
    api_client: TestClient, user_client: UserCreator
):
    """未过审的题在老师手上是草稿（看得见、能重提），在学生手上该不存在。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="还没过审的题")  # approved = NONE

    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201

    assert (
        api_client.get(f"/tasks/{task_id}", headers=_auth(teacher_token)).status_code
        == 200
    )

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    assert _make_member(api_client, board, student.user_id).status_code == 201
    assert (
        api_client.get(f"/tasks/{task_id}", headers=_auth(student_token)).status_code
        == 403
    )

    # 创建者（也是管理员）驳回它，教师重提。
    rejected = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "DISAPPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert rejected.status_code == 200, rejected.text
    assert (
        api_client.post(
            f"/tasks/{task_id}/resubmit", headers=_auth(teacher_token)
        ).status_code
        == 200
    )

    # 学生重提另一道被驳回的题 —— 403。
    other = _create_task(api_client, board, name="另一道被驳回的题")
    api_client.patch(
        f"/tasks/{other}",
        json={"approved": "DISAPPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert (
        api_client.post(
            f"/tasks/{other}/resubmit", headers=_auth(student_token)
        ).status_code
        == 403
    )


# --- 6. 教师版面（数据分析）--------------------------------------------------
#
# 前端把整个「数据分析」入口挂在 isCurrentUserAtLeastAdmin 下，所以 API 也收成
# 管理员/创建者。留住学习看板：它读学生项目里的对话，由项目级判据逐个项目过。


def test_the_analytics_surface_is_for_the_teacher(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    assert _make_member(api_client, board, student.user_id).status_code == 201

    for suffix in (
        "/analytics/overview",
        "/analytics/alerts",
        "/analytics/publishers",
        "/analytics/participants",
        "/analytics/tasks",
        "/analytics/tasks/export",
        "/analytics/publishers/export",
        "/analytics/participants/export",
    ):
        resp = api_client.get(
            f"/spaces/{board['space_id']}{suffix}", headers=_auth(student_token)
        )
        assert resp.status_code == 403, (suffix, resp.status_code, resp.text)

    # 创建者（管理员）自己进得去。/analytics/tasks 的默认 sortBy 是 publishedAt、
    # 不在它自己的白名单里（与本次无关的既有限制），所以显式给一个合法排序。
    for suffix in ("/analytics/overview", "/analytics/tasks?sortBy=createdAt"):
        resp = api_client.get(
            f"/spaces/{board['space_id']}{suffix}",
            headers=_auth(board["creator_token"]),
        )
        assert resp.status_code == 200, (suffix, resp.status_code, resp.text)
