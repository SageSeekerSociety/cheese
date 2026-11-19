"""题目板上的名字按读的人自己的叫法，私人备注只进他自己的那一份。

一道题的响应里有三处写人名：出题人（``creator``）、空间管理员（``space.admins``）、
加入项目的导师（``mentors``）；报名名册上还有一份。每一处都跟着 ``remark`` ——
**这个查看者**给他设的个人备注；没设的人没有这个键的值，界面照旧用公开昵称。

别人的那一份必须还是公开名：题目板是共享的，谁都能读同一道题。
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    join_task_space,
    unique_int,
)

REMARK = "我们叫他老王"


@pytest.fixture
def published_task(user_client: UserCreator, api_client: TestClient) -> dict:
    """一道过审的题，出版人的昵称和账号不同（才分得清「换成了」和「本来就一样」）。"""
    creator = user_client.create_user(nickname="出题人的公开昵称")
    creator.token = user_client.login(api_client, creator.username, creator.password)
    suffix = unique_int()

    space_resp = create_approved_space(
        api_client,
        json={
            "name": f"Remark Task Space ({suffix})",
            "intro": "Test",
            "description": "Desc",
            "avatarId": 1,
        },
        headers={"Authorization": f"Bearer {creator.token}"},
    )
    assert space_resp.status_code == 201, space_resp.text
    space = space_resp.json()["data"]["space"]

    task_resp = api_client.post(
        "/tasks",
        json={
            "name": f"Remark Task ({suffix})",
            "intro": "test",
            "description": '{"type":"doc","content":[]}',
            "space": space["id"],
            "categoryId": space["defaultCategoryId"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int((datetime.now(UTC).timestamp() + 7 * 86400) * 1000),
        },
        headers={"Authorization": f"Bearer {creator.token}"},
    )
    assert task_resp.status_code == 200, task_resp.text
    task_id = task_resp.json()["data"]["task"]["id"]

    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers={"Authorization": f"Bearer {creator.token}"},
    )
    assert approved.status_code == 200, approved.text

    return {"creator": creator, "task_id": task_id, "space_id": space["id"]}


def _set_remark(api_client: TestClient, token: str, handle: str, remark: str) -> None:
    saved = api_client.put(
        f"/users/{handle}/remark",
        json={"remark": remark},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert saved.status_code == 200, saved.text


def test_the_task_page_names_its_creator_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, published_task: dict
):
    reader = user_client.create_user(nickname="读者")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    join_task_space(
        api_client,
        published_task["task_id"],
        reader.user_id,
        admin_headers={"Authorization": f"Bearer {published_task['creator'].token}"},
    )
    _set_remark(api_client, reader.token, published_task["creator"].username, REMARK)

    resp = api_client.get(
        f"/tasks/{published_task['task_id']}",
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert resp.status_code == 200, resp.text
    task = resp.json()["data"]["task"]
    assert task["creator"]["remark"] == REMARK, task["creator"]
    # 公开昵称没有被改写：它仍是这个人对外的名字。
    assert task["creator"]["nickname"] == "出题人的公开昵称", task["creator"]


def test_the_task_board_lists_its_creator_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, published_task: dict
):
    reader = user_client.create_user(nickname="板读者")
    reader.token = user_client.login(api_client, reader.username, reader.password)
    join_task_space(
        api_client,
        published_task["task_id"],
        reader.user_id,
        admin_headers={"Authorization": f"Bearer {published_task['creator'].token}"},
    )
    _set_remark(api_client, reader.token, published_task["creator"].username, REMARK)

    resp = api_client.get(
        "/tasks",
        params={"space": published_task["space_id"], "pageSize": 50},
        headers={"Authorization": f"Bearer {reader.token}"},
    )
    assert resp.status_code == 200, resp.text
    mine = next(
        t for t in resp.json()["data"]["tasks"] if t["id"] == published_task["task_id"]
    )
    assert mine["creator"]["remark"] == REMARK, mine["creator"]


def test_another_member_reads_the_public_name_and_no_remark(
    user_client: UserCreator, api_client: TestClient, published_task: dict
):
    """私密称呼不进别人的那一份：他读到的是公开昵称，而且拿不到这条备注。"""
    other = user_client.create_user(nickname="旁观的读者")
    other.token = user_client.login(api_client, other.username, other.password)
    join_task_space(
        api_client,
        published_task["task_id"],
        other.user_id,
        admin_headers={"Authorization": f"Bearer {published_task['creator'].token}"},
    )

    resp = api_client.get(
        f"/tasks/{published_task['task_id']}",
        headers={"Authorization": f"Bearer {other.token}"},
    )
    assert resp.status_code == 200, resp.text
    creator = resp.json()["data"]["task"]["creator"]
    assert creator["nickname"] == "出题人的公开昵称", creator
    assert not creator.get("remark"), creator


def test_the_roster_names_a_participant_by_the_readers_own_remark(
    user_client: UserCreator, api_client: TestClient, published_task: dict
):
    participant = user_client.create_user(nickname="报名人的公开昵称")
    participant.token = user_client.login(
        api_client, participant.username, participant.password
    )
    join_task_space(
        api_client,
        published_task["task_id"],
        participant.user_id,
        admin_headers={"Authorization": f"Bearer {published_task['creator'].token}"},
    )
    joined = api_client.post(
        f"/tasks/{published_task['task_id']}/participants",
        json={"role": "PARTICIPANT"},
        headers={"Authorization": f"Bearer {participant.token}"},
    )
    assert joined.status_code in (200, 201), joined.text

    creator = published_task["creator"]
    _set_remark(api_client, creator.token, participant.username, REMARK)

    resp = api_client.get(
        f"/tasks/{published_task['task_id']}/participants",
        headers={"Authorization": f"Bearer {creator.token}"},
    )
    assert resp.status_code == 200, resp.text
    row = next(
        p
        for p in resp.json()["data"]["participants"]
        if p["memberId"] == participant.user_id
    )
    assert row["member"]["remark"] == REMARK, row["member"]
    assert row["member"]["name"] == "报名人的公开昵称", row["member"]


def test_another_teacher_saving_the_task_reads_it_in_their_own_names(
    user_client: UserCreator, api_client: TestClient, published_task: dict
):
    """A space manager may edit a task they did not set (``task_roles.teaches``),
    and the answer they get back replaces the page they were on — so it has to
    name people the way that manager calls them, not publicly."""
    creator = published_task["creator"]
    manager = user_client.create_user(nickname="改题的管理员")
    manager.token = user_client.login(api_client, manager.username, manager.password)
    added = api_client.post(
        f"/spaces/{published_task['space_id']}/managers",
        json={"userId": manager.user_id, "role": "ADMIN"},
        headers={"Authorization": f"Bearer {creator.token}"},
    )
    assert added.status_code == 201, added.text

    # 管理员给出题人起了个自己的叫法。
    _set_remark(api_client, manager.token, creator.username, REMARK)

    resp = api_client.patch(
        f"/tasks/{published_task['task_id']}",
        json={"intro": "管理员改了一句"},
        headers={"Authorization": f"Bearer {manager.token}"},
    )
    assert resp.status_code == 200, resp.text
    task = resp.json()["data"]["task"]
    assert task["creator"]["remark"] == REMARK, task["creator"]
