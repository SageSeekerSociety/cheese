"""The course link, end to end: what a student gets when he opens it.

A 题目版 is a course now, and the link a teacher drops in the group chat is how
a student arrives. What the link must do, and must not do:

- redeem the code it carries — the same un-named way in the 邀请码 page hands
  out, so a new student joins and lands in the course;
- leave him with his project in that course, created through the ordinary
  participation path (so it inherits the protocol, gets its brief, and keeps
  the 一学期一个项目 key);
- **not** mint a second project when he opens the same link again, and not
  mint one when the teacher later publishes a new 题 either — reuse is asked
  of the course, not of whichever 题 happens to be the anchor today;
- refuse a code that belongs to a different board, without joining anybody
  anywhere;
- and change nothing about who may see the board: an outsider still gets 404,
  and only the board's teachers may hand out the link.
"""

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


def _make_course(api_client: TestClient, user_client: UserCreator, token: str) -> dict:
    """An approved 题目版 with an approved 题 to anchor projects on."""
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Course Link ({suffix})",
            "intro": "A course.",
            "description": "A lengthy description. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    task_id = _publish_task(api_client, token, space, name=f"课程项目 ({suffix})")
    return {
        "space_id": space["id"],
        "category_id": space["defaultCategoryId"],
        "task_id": task_id,
    }


def _publish_task(api_client: TestClient, token: str, space: dict, *, name: str) -> int:
    resp = api_client.post(
        "/tasks",
        headers=_auth(token),
        json={
            "name": name,
            "intro": "本周任务",
            "description": "记录观察结果并注明资料来源",
            "space": space["id"],
            "categoryId": space["defaultCategoryId"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 86_400_000,
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}", headers=_auth(token), json={"approved": "APPROVED"}
    )
    assert approved.status_code == 200, approved.text
    return task_id


def test_a_student_opens_the_link_and_leaves_with_his_course_project(
    api_client: TestClient, user_client: UserCreator
) -> None:
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    course = _make_course(api_client, user_client, teacher_token)

    link = api_client.get(
        f"/spaces/{course['space_id']}/course-link", headers=_auth(teacher_token)
    )
    assert link.status_code == 200, link.text
    body = link.json()["data"]
    assert body["path"] == f"/spaces/join/{body['code']}"

    # Before the link he cannot see the board at all — same 404 as any stranger.
    assert (
        api_client.get(
            f"/spaces/{course['space_id']}", headers=_auth(student_token)
        ).status_code
        == 404
    )

    enrolled = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": body["code"]},
    )
    assert enrolled.status_code == 200, enrolled.text
    data = enrolled.json()["data"]
    assert data["space"]["id"] == course["space_id"]
    assert data["project"] is not None, "the link must leave him with a project"
    assert data["project"]["id"]

    # He is a member now, and the project is really his.
    assert (
        api_client.get(
            f"/spaces/{course['space_id']}", headers=_auth(student_token)
        ).status_code
        == 200
    )


def test_opening_the_same_link_again_makes_no_second_project(
    api_client: TestClient, user_client: UserCreator
) -> None:
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    course = _make_course(api_client, user_client, teacher_token)
    code = api_client.get(
        f"/spaces/{course['space_id']}/course-link", headers=_auth(teacher_token)
    ).json()["data"]["code"]

    first = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    second = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert (
        first.json()["data"]["project"]["id"] == second.json()["data"]["project"]["id"]
    )


def test_a_new_assignment_does_not_hand_him_a_second_project(
    api_client: TestClient, user_client: UserCreator
) -> None:
    """The anchor 题 may change; the student's course project must not.

    The course keeps 一学期一个项目 by hanging projects on ONE 课程题, but the
    teacher publishing a new one must not read as 「you have no project here」.
    """
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    course = _make_course(api_client, user_client, teacher_token)
    code = api_client.get(
        f"/spaces/{course['space_id']}/course-link", headers=_auth(teacher_token)
    ).json()["data"]["code"]

    first = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    assert first.status_code == 200, first.text

    # The teacher publishes a second 题 — the anchor rule must not move the
    # student onto it, because that would create a second project.
    _publish_task(
        api_client,
        teacher_token,
        {
            "id": course["space_id"],
            "defaultCategoryId": course["category_id"],
        },
        name="第二周的作业",
    )

    again = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    assert again.status_code == 200, again.text
    assert (
        again.json()["data"]["project"]["id"] == first.json()["data"]["project"]["id"]
    )


def test_a_course_with_nothing_to_anchor_still_lets_him_in(
    api_client: TestClient, user_client: UserCreator
) -> None:
    """No 题 yet is an honest empty answer, not a stray hidden 题.

    A brand-new course may have published nothing. He joins, he lands, and the
    project arrives with the first 题 the course hangs its work on — the second
    call to enroll is what creates it.
    """
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Empty Course ({suffix})",
            "intro": "Nothing published yet.",
            "description": "A lengthy description. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(teacher_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    code = api_client.get(
        f"/spaces/{space['id']}/course-link", headers=_auth(teacher_token)
    ).json()["data"]["code"]

    enrolled = api_client.post(
        f"/spaces/{space['id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    assert enrolled.status_code == 200, enrolled.text
    assert enrolled.json()["data"]["project"] is None
    assert (
        api_client.get(
            f"/spaces/{space['id']}", headers=_auth(student_token)
        ).status_code
        == 200
    )

    # Then the course publishes something, and the very same link now produces
    # the project — one, not two.
    _publish_task(
        api_client,
        teacher_token,
        {"id": space["id"], "defaultCategoryId": space["defaultCategoryId"]},
        name="第一周的作业",
    )
    later = api_client.post(
        f"/spaces/{space['id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    assert later.status_code == 200, later.text
    assert later.json()["data"]["project"] is not None


def test_a_code_from_another_board_is_refused_and_joins_nothing(
    api_client: TestClient, user_client: UserCreator
) -> None:
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    first_course = _make_course(api_client, user_client, teacher_token)
    second_course = _make_course(api_client, user_client, teacher_token)
    foreign_code = api_client.get(
        f"/spaces/{second_course['space_id']}/course-link", headers=_auth(teacher_token)
    ).json()["data"]["code"]

    refused = api_client.post(
        f"/spaces/{first_course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": foreign_code},
    )
    assert refused.status_code == 400, refused.text

    # Neither board let him in: the refusal happens before the code is spent.
    assert (
        api_client.get(
            f"/spaces/{first_course['space_id']}", headers=_auth(student_token)
        ).status_code
        == 404
    )
    assert (
        api_client.get(
            f"/spaces/{second_course['space_id']}", headers=_auth(student_token)
        ).status_code
        == 404
    )


def test_only_the_boards_teachers_hand_out_the_link(
    api_client: TestClient, user_client: UserCreator
) -> None:
    """A student in the course still cannot hand its link around.

    He is a member, so the board is not hidden from him — the answer is an
    explicit 403, not a 404 (that one is for people the board is not theirs to
    see; see ``test_an_outsider_still_reads_the_board_as_absent``).
    """
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)

    course = _make_course(api_client, user_client, teacher_token)
    code = api_client.get(
        f"/spaces/{course['space_id']}/course-link", headers=_auth(teacher_token)
    ).json()["data"]["code"]
    joined = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(student_token),
        json={"code": code},
    )
    assert joined.status_code == 200, joined.text

    assert (
        api_client.get(
            f"/spaces/{course['space_id']}/course-link", headers=_auth(student_token)
        ).status_code
        == 403
    )


def test_an_outsider_still_reads_the_board_as_absent(
    api_client: TestClient, user_client: UserCreator
) -> None:
    """The link does not widen who may see the board — 404, not 403."""
    teacher = user_client.create_user()
    teacher_token = _login(user_client, api_client, teacher)
    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)

    course = _make_course(api_client, user_client, teacher_token)
    probing = api_client.post(
        f"/spaces/{course['space_id']}/enroll",
        headers=_auth(outsider_token),
        json={},
    )
    assert probing.status_code == 404, probing.text
