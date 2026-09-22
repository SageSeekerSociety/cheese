"""一门课的作业与验收：教师一屏看到整门课，学生看不到。

产品的「作业与验收」是**教师版面**：这门课有几道作业、交了多少、还有多少人
没交、多少份等着看。这条链路上有四处必须是真的，本文件按浏览器会收到的状态
码与数字逐条断：

1. **一屏看整门课**：一次请求拿到这门课里每个人的最新一版提交，每行知道它是
   哪道题、哪条报名记录 —— 不再是「逐道题 × 逐个学生」地各问一次；
2. **`reviewed=false` 就是验收队列**：打完分那一份立刻不在队列里；
3. **数字对得上**：没交的人数 = 报名人数 − 交过东西的人数；
4. **只有这门课的老师看得到**：在板里的学生答 403，不在板里的答 404（不确认
   这个板存在），而且**别的板子的提交不会串进来**。
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
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Submission Queue ({suffix})",
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


def _approve_task(api_client: TestClient, board: dict, task_id: int) -> None:
    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text


def _enroll(
    api_client: TestClient, board: dict, task_id: int, user, user_token: str
) -> int:
    """一个学生报名一门课的某道作业，返回报名记录 id。"""
    join = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": user.user_id},
        json={},
        headers=_auth(user_token),
    )
    assert join.status_code == 200, join.text
    membership_id = join.json()["data"]["participant"]["id"]
    api_client.patch(
        f"/tasks/{task_id}/participants/{membership_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    return membership_id


def _submit(
    api_client: TestClient, task_id: int, membership_id: int, token: str, text: str
):
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{membership_id}/submissions",
        json=[{"text": text}],
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission"]["id"]


def _queue(api_client: TestClient, board: dict, token: str, **params):
    return api_client.get(
        f"/spaces/{board['space_id']}/submissions",
        params=params,
        headers=_auth(token),
    )


def _course_with_two_assignments(api_client: TestClient, user_client: UserCreator):
    """一门课 + 两道作业 + 两个学生，各交了一份。"""
    board = _new_board(user_client, api_client)
    task_a = _create_task(api_client, board, name="第 3 周作业")
    task_b = _create_task(api_client, board, name="第 4 周作业")
    _approve_task(api_client, board, task_a)
    _approve_task(api_client, board, task_b)

    alice = user_client.create_user()
    bob = user_client.create_user()
    alice_token = _login(user_client, api_client, alice)
    bob_token = _login(user_client, api_client, bob)
    alice_m = _enroll(api_client, board, task_a, alice, alice_token)
    bob_m = _enroll(api_client, board, task_a, bob, bob_token)
    _submit(api_client, task_a, alice_m, alice_token, "alice 的第 3 周作业")
    _submit(api_client, task_a, bob_m, bob_token, "bob 的第 3 周作业")

    return {
        **board,
        "task_a": task_a,
        "task_b": task_b,
        "alice": alice,
        "bob": bob,
        "alice_token": alice_token,
        "alice_m": alice_m,
        "bob_m": bob_m,
    }


def test_a_teacher_sees_the_whole_course_in_one_request(
    api_client: TestClient, user_client: UserCreator
):
    """一次请求拿到整门课的提交，每行知道自己是哪道题的哪条报名。"""
    s = _course_with_two_assignments(api_client, user_client)

    resp = _queue(api_client, s, s["creator_token"])
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    submissions = data["submissions"]

    assert len(submissions) == 2
    assert {row["taskId"] for row in submissions} == {s["task_a"]}
    assert {row["taskTitle"] for row in submissions} == {"第 3 周作业"}
    assert {row["participantId"] for row in submissions} == {s["alice_m"], s["bob_m"]}
    # 「谁的哪份作业」：每行都带得出提交内容本身。
    assert {row["content"][0]["contentText"] for row in submissions} == {
        "alice 的第 3 周作业",
        "bob 的第 3 周作业",
    }


def test_the_numbers_add_up(api_client: TestClient, user_client: UserCreator):
    """报名人数、交过的人数、还等多少人、没交的人数 —— 四个数字互相对得上。"""
    s = _course_with_two_assignments(api_client, user_client)

    # 第三个学生报了第 4 周作业但没交 —— 「没交」的那个人。
    carol = user_client.create_user()
    carol_token = _login(user_client, api_client, carol)
    carol_m = _enroll(api_client, s, s["task_b"], carol, carol_token)
    assert carol_m > 0

    summary = _queue(api_client, s, s["creator_token"]).json()["data"]["summary"]
    # 三道报名（alice / bob 在第 3 周，carol 在第 4 周），交了两份。
    assert summary["participants"] == 3
    assert summary["submissions"] == 2
    assert summary["pendingReview"] == 2
    assert summary["missing"] == 1


def test_the_queue_is_what_has_no_review_yet(
    api_client: TestClient, user_client: UserCreator
):
    """`reviewed=false` 是验收队列；打完分那一份立刻从队列里消失，并出现在
    `reviewed=true` 里 —— 队列不是另一份数据，是同一个列表的过滤。"""
    s = _course_with_two_assignments(api_client, user_client)

    pending = _queue(api_client, s, s["creator_token"], reviewed="false").json()["data"]
    assert pending["page"]["total"] == 2

    first = pending["submissions"][0]
    review = api_client.post(
        f"/tasks/{first['taskId']}/participants/{first['participantId']}"
        f"/submissions/{first['id']}/review",
        json={"accepted": True, "score": 90, "comment": "很好"},
        headers=_auth(s["creator_token"]),
    )
    assert review.status_code == 200, review.text

    left = _queue(api_client, s, s["creator_token"], reviewed="false").json()["data"]
    assert left["page"]["total"] == 1
    assert first["id"] not in {row["id"] for row in left["submissions"]}

    reviewed = _queue(
        api_client, s, s["creator_token"], reviewed="true"
    ).json()["data"]
    assert reviewed["page"]["total"] == 1
    assert reviewed["submissions"][0]["review"]["detail"]["score"] == 90


def test_a_student_in_the_board_gets_403(
    api_client: TestClient, user_client: UserCreator
):
    """填码进这门课的是学生 —— 整门课的提交不是他能看的，但板子本身他看得见，
    所以是 403 而不是 404。"""
    s = _course_with_two_assignments(api_client, user_client)
    added = api_client.post(
        f"/spaces/{s['space_id']}/members",
        json={"userId": s["alice"].user_id},
        headers=_auth(s["creator_token"]),
    )
    assert added.status_code == 201, added.text

    resp = _queue(api_client, s, s["alice_token"])
    assert resp.status_code == 403, resp.text


def test_someone_outside_the_board_gets_404(
    api_client: TestClient, user_client: UserCreator
):
    """不在这个板里的人拿到 404 —— 一个你不在的题目板不该被确认存在（同一道
    可见性闸，与列表查询和直链是同一个答案）。"""
    s = _course_with_two_assignments(api_client, user_client)
    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)

    resp = _queue(api_client, s, outsider_token)
    assert resp.status_code == 404, resp.text


def test_another_board_submissions_do_not_leak_in(
    api_client: TestClient, user_client: UserCreator
):
    """隔壁那门课的提交不该出现在这门课的队列里 —— 判据是题目归属的那块板。"""
    s = _course_with_two_assignments(api_client, user_client)

    other = _new_board(user_client, api_client)
    other_task = _create_task(api_client, other, name="别的课的作业")
    _approve_task(api_client, other, other_task)
    other_student = user_client.create_user()
    other_token = _login(user_client, api_client, other_student)
    other_m = _enroll(api_client, other, other_task, other_student, other_token)
    _submit(api_client, other_task, other_m, other_token, "别的课的提交")

    mined = _queue(api_client, s, s["creator_token"]).json()["data"]
    assert {row["taskId"] for row in mined["submissions"]} == {s["task_a"]}
    assert mined["summary"]["submissions"] == 2

    theirs = _queue(api_client, other, other["creator_token"]).json()["data"]
    assert {row["taskId"] for row in theirs["submissions"]} == {other_task}


def test_a_board_admin_who_did_not_publish_is_still_a_teacher(
    api_client: TestClient, user_client: UserCreator
):
    """助教：不是出题者，但是这门课的管理员 —— 教师版面照样是他的。"""
    s = _course_with_two_assignments(api_client, user_client)
    assistant = user_client.create_user()
    assistant_token = _login(user_client, api_client, assistant)
    made = api_client.post(
        f"/spaces/{s['space_id']}/managers",
        json={"userId": assistant.user_id, "role": "ADMIN"},
        headers=_auth(s["creator_token"]),
    )
    assert made.status_code == 201, made.text

    resp = _queue(api_client, s, assistant_token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["page"]["total"] == 2
