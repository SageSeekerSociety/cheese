"""「谁是管理员」= 这块板的管理员名单 —— 一句话在五扇门上的后果。

产品把定义拍死了：题目板 = 一门课 / 一个活动，「谁是管理员」= 这块板的管理员名单（库里
``space_admin_relation`` 里的一行，建版的人是 OWNER）。于是有三件事必须是真的，
本文件逐条按浏览器会收到的状态码来断：

1. **管理员不凭这个身份读成员的项目**：被题目点名为导师、学生领题时同意了的，才
   作为导师进到领题开出的项目里，读得到、改不了（#3830）；
2. **管理员打得了分**（评审四个接口）；
3. **板里的人都发得了题，但上板要所有者或管理员点头**（谁都能出题、管理员审核）；
4. **成员导不出参与者花名册**（明文个人信息，403 而不是空 CSV）；
5. **把导师移出项目，下一次请求就必须读不到** —— 判据每次现查，没有缓存。

判据全部收敛在 ``app.auth.space_access``；这里测的是它的可观测后果，不看实现。
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
    """建版的人 + 一块已通过审核的题目板。建版的人就是它的 OWNER（管理员）。"""
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


def _publish(
    api_client: TestClient,
    board: dict,
    *,
    name: str,
    token: str | None = None,
    mentors: list[str] | None = None,
):
    """往这块板里发一道题，把响应如实交回调用方 —— 谁发得成由各自的用例断言。"""
    return api_client.post(
        "/tasks",
        json={
            "mentors": mentors or [],
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
        headers=_auth(token or board["creator_token"]),
    )


def _create_task(
    api_client: TestClient,
    board: dict,
    *,
    name: str,
    mentors: list[str] | None = None,
) -> int:
    resp = _publish(api_client, board, name=name, mentors=mentors)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _claim(api_client: TestClient, board: dict, task_id: int, user, token: str) -> str:
    """``user`` 领了这道题：题目过审、人进了这门课，再申请（同意题目点名的导师），
    出题人通过。返回申请通过时开出的那个项目。"""
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text
    joined = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": user.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert joined.status_code in (201, 409), joined.text
    claimed = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={"agreeMentors": True},
        headers=_auth(token),
    )
    assert claimed.status_code in (200, 201), claimed.text
    approved = api_client.patch(
        f"/tasks/{task_id}/participants/{claimed.json()['data']['participant']['id']}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text
    again = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={"agreeMentors": True},
        headers=_auth(token),
    )
    assert again.status_code == 200, again.text
    return again.json()["data"]["project"]["id"]


# --- 1. 导师读得到成员项目里的对话，单是管理员读不到 ---------------------------


def _mentored_project(api_client: TestClient, user_client: UserCreator) -> dict:
    """一块板、一位管理员被这道题点名为导师、另一位管理员没被点名，一个学生领了题。"""
    board = _new_board(user_client, api_client)
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201
    bystander = user_client.create_user()
    bystander_token = _login(user_client, api_client, bystander)
    assert _make_admin(api_client, board, bystander.user_id).status_code == 201
    task_id = _create_task(
        api_client, board, name="这门课的题", mentors=[teacher.username]
    )
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    pid = _claim(api_client, board, task_id, student, student_token)
    return {
        "board": board,
        "pid": pid,
        "teacher": teacher,
        "teacher_token": teacher_token,
        "bystander_token": bystander_token,
        "student_token": student_token,
    }


def test_a_mentor_reads_the_conversations_of_its_students_projects(
    api_client: TestClient, user_client: UserCreator
):
    """项目归学生所有；导师是题目点名、学生领题时同意了的那位，他读得到项目和
    里面的对话。"""
    m = _mentored_project(api_client, user_client)
    pid, token = m["pid"], m["teacher_token"]

    assert api_client.get(f"/projects/{pid}", headers=_auth(token)).status_code == 200
    assert (
        api_client.get(
            "/topics", params={"project_id": pid}, headers=_auth(token)
        ).status_code
        == 200
    )


def test_a_board_admin_who_is_not_a_mentor_does_not_read_the_project(
    api_client: TestClient, user_client: UserCreator
):
    """管理这门课不等于进得了每个学生的项目：没被点名为导师的管理员读不到。"""
    m = _mentored_project(api_client, user_client)
    pid, token = m["pid"], m["bystander_token"]

    assert api_client.get(f"/projects/{pid}", headers=_auth(token)).status_code == 403
    assert (
        api_client.get(
            "/topics", params={"project_id": pid}, headers=_auth(token)
        ).status_code
        == 403
    )


def test_a_mentor_reads_a_students_project_but_does_not_change_it(
    api_client: TestClient, user_client: UserCreator
):
    """导师看得见学生的项目，但项目里的文档、记忆是学生自己的：他不新建、不改。"""
    m = _mentored_project(api_client, user_client)
    pid, teacher_token = m["pid"], m["teacher_token"]

    listed = api_client.get(f"/projects/{pid}/documents", headers=_auth(teacher_token))
    made = api_client.post(
        f"/projects/{pid}/documents",
        json={"title": "老师写的"},
        headers=_auth(teacher_token),
    )
    remembered = api_client.put(
        "/memory/files",
        json={
            "project_id": pid,
            "scope": "team",
            "path": "teacher-note.md",
            "content": "---\nname: teacher-note\ndescription: 老师的话\n"
            "type: feedback\n---\n\n照老师说的做。\n",
        },
        headers=_auth(teacher_token),
    )

    assert listed.status_code == 200, listed.text
    assert made.status_code == 403
    assert remembered.status_code == 403


def test_a_plain_member_of_the_board_is_not_a_teacher(
    api_client: TestClient, user_client: UserCreator
):
    """管理员 ≠ 这个板里的每一个人。填码进来的是成员，读不到别人组的对话 ——
    这正是 ``app.auth.project_access`` 顶部记的那个「把全班每个组的对话交给全班」
    的越权，换一层说法也一样。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="这门课的题")

    # 板里有一位管理员，但下面被测的不是他。
    teacher = user_client.create_user()
    _login(user_client, api_client, teacher)
    _make_admin(api_client, board, teacher.user_id)

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    pid = _claim(api_client, board, task_id, student, student_token)

    # 另一个成员：是这块板里的成员（成员），但没有任何管理权。
    peer = user_client.create_user()
    peer_token = _login(user_client, api_client, peer)
    assert _make_member(api_client, board, peer.user_id).status_code == 201

    assert (
        api_client.get(f"/projects/{pid}", headers=_auth(peer_token)).status_code == 403
    )


def test_removing_a_mentor_shuts_the_door_immediately(
    api_client: TestClient, user_client: UserCreator
):
    """撤权立刻生效：同一个 URL、同样的参数，中间只多了一次学生把导师移出项目。
    读路径上任何形式的缓存都会在这里留下撤权前的那一次 200。"""
    m = _mentored_project(api_client, user_client)
    pid, token = m["pid"], m["teacher_token"]

    assert api_client.get(f"/projects/{pid}", headers=_auth(token)).status_code == 200

    removed = api_client.delete(
        f"/projects/{pid}/members/{m['teacher'].username}",
        headers=_auth(m["student_token"]),
    )
    assert removed.status_code == 200, removed.text

    assert api_client.get(f"/projects/{pid}", headers=_auth(token)).status_code == 403


# --- 2. 管理员打得了分 ---------------------------------------------------------


def _submission_ready(api_client: TestClient, user_client: UserCreator) -> dict:
    """一块板、一道题、一个报名并提交了的成员，外加一位管理员。"""
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="要打分的题")

    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201

    participant = user_client.create_user()
    participant_token = _login(user_client, api_client, participant)
    assert _make_member(api_client, board, participant.user_id).status_code == 201

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
    """出题者**或**该题所在题目板的管理员。这里出题者是创建者，打分的人是后来被设
    成管理员的管理员 —— 他既不是出题者，也不在名册上。"""
    s = _submission_ready(api_client, user_client)
    resp = api_client.post(
        f"/tasks/{s['task_id']}/participants/{s['membership_id']}"
        f"/submissions/{s['submission_id']}/review",
        json={"accepted": True, "score": 5, "comment": "管理员打分"},
        headers=_auth(s["teacher_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["review"]["reviewed"] is True


def test_a_student_cannot_grade(api_client: TestClient, user_client: UserCreator):
    """成员/参与者不是管理员：打分这条门对他关着。"""
    s = _submission_ready(api_client, user_client)
    resp = api_client.post(
        f"/tasks/{s['task_id']}/participants/{s['membership_id']}"
        f"/submissions/{s['submission_id']}/review",
        json={"accepted": True, "score": 5, "comment": "自评"},
        headers=_auth(s["participant_token"]),
    )
    assert resp.status_code == 403, resp.text


# --- 3. 谁发得了题、谁批得动 -------------------------------------------------


def test_a_member_publishes_into_the_audit_queue(
    api_client: TestClient, user_client: UserCreator
):
    """题目板是一块任何人都能出题的板：填码进来的成员发得成题，但发出来的题一律
    进待审队列（approved=NONE），要所有者或管理员点头才上板。"""
    board = _new_board(user_client, api_client)

    member = user_client.create_user()
    member_token = _login(user_client, api_client, member)
    assert _make_member(api_client, board, member.user_id).status_code == 201

    resp = _publish(api_client, board, name="成员发的题", token=member_token)
    assert resp.status_code == 200, resp.text
    task = resp.json()["data"]["task"]
    assert task["approved"] == "NONE", task
    assert task["creator"]["username"] == member.username


def test_someone_outside_the_board_cannot_publish_a_task(
    api_client: TestClient, user_client: UserCreator
):
    """没填码、没被加进来的人拿着 space id 也发不了题 —— 这道门问的是「你在不在
    这块板里」，不是「你是不是管理员」。"""
    board = _new_board(user_client, api_client)

    stranger = user_client.create_user()
    stranger_token = _login(user_client, api_client, stranger)

    resp = _publish(api_client, board, name="外人想发的题", token=stranger_token)
    assert resp.status_code == 403, resp.text


def test_the_member_who_published_it_still_cannot_approve_it(
    api_client: TestClient, user_client: UserCreator
):
    """发得成不等于批得动：那道题要所有者或管理员 PATCH 才上板，发题的人自己按不动
    这个开关。"""
    board = _new_board(user_client, api_client)

    member = user_client.create_user()
    member_token = _login(user_client, api_client, member)
    assert _make_member(api_client, board, member.user_id).status_code == 201

    published = _publish(api_client, board, name="成员发的题", token=member_token)
    task_id = published.json()["data"]["task"]["id"]

    denied = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(member_token),
    )
    assert denied.status_code == 403, denied.text

    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text


def test_the_board_creator_can_still_publish_a_task(
    api_client: TestClient, user_client: UserCreator
):
    """放开「谁能发」不能连创建者一起收掉 —— 否则正路也走不通了。"""
    board = _new_board(user_client, api_client)
    assert _create_task(api_client, board, name="创建者发的题") > 0


# --- 4. 成员导不出参与者花名册 -----------------------------------------------


def test_a_student_cannot_export_the_participant_roster(
    api_client: TestClient, user_client: UserCreator
):
    """这份 CSV 逐行写着成员的真实姓名、学号、年级、专业、班级、电话、邮箱。成员
    是这块板里的成员、看得见课，但导出是管理员的事：403，明确报错，不是一张空 CSV。"""
    board = _new_board(user_client, api_client)

    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
    assert _make_member(api_client, board, student.user_id).status_code == 201

    denied = api_client.get(
        f"/spaces/{board['space_id']}/analytics/participants/export",
        headers=_auth(student_token),
    )
    assert denied.status_code == 403, denied.text

    # 紧邻那一格（参与者的分组统计）读的是同一批解密出来的身份，同一个门。
    denied_summary = api_client.get(
        f"/spaces/{board['space_id']}/analytics/participants",
        headers=_auth(student_token),
    )
    assert denied_summary.status_code == 403, denied_summary.text

    # 管理员（创建者）自己拿得到。
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


# --- 5. 管理员对一道题的其余能力 ------------------------------------------------
#
# 发布与评审之外，「管理员对这门课里的这道题能做的事」还有一整套：管报名、看名单、
# 看提交、看未过审的草稿、重提审核、编辑/删除题目。它们从前各写了一遍「出题者」，
# 管理员那一半时有时无；现在全部问 ``may_teach_task``，与打分、发题同一个判据。


def test_a_board_admin_manages_participants_and_reads_submissions(
    api_client: TestClient, user_client: UserCreator
):
    """管报名、看名单、看提交：管理员做得到，普通成员做不到。"""
    s = _submission_ready(api_client, user_client)
    task_id = s["task_id"]
    membership_id = s["membership_id"]

    # 名单与提交列表 —— 管理员视角。
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
    # 管报名：批准这个成员。
    approved = api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(s["teacher_token"]),
    )
    assert approved.status_code == 200, approved.text

    # 另一个只是成员的成员：同两条路都关着。
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
    """未过审的题在管理员手上是草稿（看得见、能重提），在成员手上该不存在。"""
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

    # 创建者（也是管理员）驳回它，管理员重提。
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

    # 成员重提另一道被驳回的题 —— 403。
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


# --- 6. 管理员版面（数据分析）--------------------------------------------------
#
# 前端把整个「数据分析」入口挂在 isCurrentUserAtLeastAdmin 下，所以 API 也收成
# 管理员/创建者。留住学习看板：它读成员项目里的对话，由项目级判据逐个项目过。


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
        "/analytics/people",
        "/analytics/tasks",
        "/analytics/tasks/export",
        "/analytics/publishers/export",
        "/analytics/participants/export",
    ):
        resp = api_client.get(
            f"/spaces/{board['space_id']}{suffix}", headers=_auth(student_token)
        )
        assert resp.status_code == 403, (suffix, resp.status_code, resp.text)

    # 创建者（管理员）自己进得去，且两处都按默认参数调用 —— 不带查询参数
    # 也必须 200。（`/analytics/tasks` 曾经默认 sortBy=publishedAt，落在自己
    # 白名单之外，于是这里只能显式补一个 `?sortBy=createdAt` 绕过去；默认值
    # 改成 `createdAt` 之后那个绕行和那段说明都不需要了。）
    for suffix in ("/analytics/overview", "/analytics/tasks"):
        resp = api_client.get(
            f"/spaces/{board['space_id']}{suffix}",
            headers=_auth(board["creator_token"]),
        )
        assert resp.status_code == 200, (suffix, resp.status_code, resp.text)


# --- 导师要领题的人同意，也只能是这门课的人 -------------------------------------


def test_a_task_with_mentors_is_not_claimed_without_agreeing_to_them(
    api_client: TestClient, user_client: UserCreator
):
    """领题的人不同意导师加入项目，这道题就领不了；同意了才领得到。"""
    board = _new_board(user_client, api_client)
    teacher = user_client.create_user()
    assert _make_admin(api_client, board, teacher.user_id).status_code == 201
    task_id = _create_task(
        api_client, board, name="有导师的题", mentors=[teacher.username]
    )
    student = user_client.create_user()
    token = _login(user_client, api_client, student)
    assert (
        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers=_auth(board["creator_token"]),
        ).status_code
        == 200
    )
    api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": student.user_id},
        headers=_auth(board["creator_token"]),
    )

    refused = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert refused.status_code == 400, refused.text

    claimed = api_client.post(
        f"/tasks/{task_id}/participations/user",
        json={"agreeMentors": True},
        headers=_auth(token),
    )
    assert claimed.status_code in (200, 201), claimed.text


def test_only_the_publisher_or_a_space_admin_is_a_mentor(
    api_client: TestClient, user_client: UserCreator
):
    """导师会读学生的项目，所以只能是出题人自己或这门课的管理员。"""
    board = _new_board(user_client, api_client)
    stranger = user_client.create_user()

    refused = _publish(api_client, board, name="题", mentors=[stranger.username])

    assert refused.status_code == 400, refused.text
