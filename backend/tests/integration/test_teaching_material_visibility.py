"""「仅管理员」档的课件不进学生的开场上下文 (#944) —— 一条安全缺陷的回归。

素材的两档可见性（``members`` / ``admins``）原本只在 ``GET /materials/{id}`` 那道
通用读门上判。同一条 ``material_ids`` 却经
``app.domain.task.teaching.for_project`` 无检查地进入每个学生的 agent system
prompt —— 它带的 ``url`` 是一条公开可猜的 ``/uploads/...`` 路径（见
``routes/uploads.py`` 顶部）。而且 ``PATCH /spaces/{id}/materials/{mid}`` 可以在
一份配置点名它**之后**再把它翻进「仅管理员」档，所以读侧必须自己再判一次。

这里钉四件事：

1. **读侧过滤**：只在「仅管理员」档里的课件，被 ``for_project`` 从
   ``TeachingContext.materials`` 里丢掉 —— 同一份上下文读两次，翻档前在、翻档后
   不在（读的是真读路，不是把两个接口响应摆在一起比）。
2. **判据本身**：``member_readable_material_ids`` 的几个格子 —— 没有活着的关联行
   = 可读（普通素材）；有至少一条「所有成员」= 可读（最宽的那道说了算）；只有
   「仅管理员」= 不可读；**同一份素材在 A 板只进「仅管理员」、在 B 板进「所有成员」
   时可读**。
3. **写侧拒绝**：表单里点名一份只进「仅管理员」档的课件，当场 400、字段名是
   ``materialIds``（空间默认与题目覆盖两条写口各一次），而不是先收下、等
   ``for_project`` 把它悄悄丢掉。
4. **两条读路一个结论**：``for_project`` 与 ``GET /tasks/{taskId}/inheritance``
   对**同一份素材**给出同一个答案，且 ``inheritance`` 那份响应里
   ``teaching.materialIds`` 与 ``materials`` 不能一个说有、一个说没有。

上传走 ``POST /spaces/{spaceId}/materials``（multipart ``file`` + ``visibility``），
不挂板子的散件走 ``POST /materials``（只有一个 ``material`` 行、没有关联行）；
翻档走 ``PATCH /spaces/{spaceId}/materials/{materialId}`` 的 ``{"visibility": ...}``。
登录接口按用户名限速，所以每个用户都现造。
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.project.repositories import ProjectRepository
from app.domain.space.material_service import (
    SpaceMaterialRepository,
    member_readable_material_ids,
)
from app.domain.task import teaching as teaching_context
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    post_project,
    unique_int,
)

PDF_BYTES = b"%PDF-1.4 course handout"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块过审的板：一个所有者（管理员）。"""
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Teaching Materials ({suffix})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "owner": creator,
        "token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _upload_into(
    api_client: TestClient, board: dict, *, visibility: str | None = None
) -> int:
    """把一份文件放进这块板，返回 ``material`` id。"""
    data: dict = {"type": "file"}
    if visibility is not None:
        data["visibility"] = visibility
    resp = api_client.post(
        f"/spaces/{board['space_id']}/materials",
        files={"file": ("handout.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
        data=data,
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["material"]["id"]


def _upload_loose(api_client: TestClient, board: dict) -> int:
    """上传一份**不挂任何板子**的散件（``POST /materials``）。"""
    resp = api_client.post(
        "/materials",
        files={"file": ("loose.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
        data={"type": "file"},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _set_visibility(
    api_client: TestClient, board: dict, material_id: int, visibility: str
):
    return api_client.patch(
        f"/spaces/{board['space_id']}/materials/{material_id}",
        json={"visibility": visibility},
        headers=_auth(board["token"]),
    )


def _publish_task(
    api_client: TestClient, board: dict, *, name: str, teaching=None
) -> dict:
    body: dict = {
        "name": name,
        "intro": "题",
        "description": '{"type":"doc","content":[]}',
        "space": board["space_id"],
        "categoryId": board["category_id"],
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": int(datetime.now(UTC).timestamp() * 1000) + 7 * 86400 * 1000,
    }
    if teaching is not None:
        body["teaching"] = teaching
    resp = api_client.post("/tasks", json=body, headers=_auth(board["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]


def _project_under(
    api_client: TestClient, user_client: UserCreator, board: dict, *, task_id: int
) -> str:
    """在这道题上建一个学生的项目（过审 → 入板 → 认领 → 开项目）。"""
    student = user_client.create_user()
    student_token = user_client.login(api_client, student.username, student.password)
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["token"]),
    )
    assert approved.status_code == 200, approved.text
    joined = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": student.user_id},
        headers=_auth(board["token"]),
    )
    assert joined.status_code in (201, 409), joined.text
    claimed = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(student_token)
    )
    assert claimed.status_code in (200, 201), claimed.text
    resp = post_project(
        api_client,
        json={
            "name": "学生的项目",
            "owner_handle": student.username,
            "external_task_id": task_id,
        },
        headers=_auth(student_token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _material_ids_in(context) -> list[int]:
    return [item["id"] for item in context.materials]


def _for_project(portal, session, project_id: str):
    holder: dict = {}

    async def _run() -> None:
        project = await ProjectRepository(session).get(uuid.UUID(str(project_id)))
        holder["ctx"] = await teaching_context.for_project(
            session=session, project=project
        )

    portal.call(_run)
    return holder["ctx"]


def _readable_ids(portal, session, material_ids: list[int]) -> set[int]:
    holder: dict = {}

    async def _run() -> None:
        holder["ids"] = await member_readable_material_ids(
            session, material_ids=material_ids
        )

    portal.call(_run)
    return holder["ids"]


def _link(portal, session, *, space_id: int, material_id: int, visibility: str) -> None:
    """直接挂一条板内关联行 —— 接口只会在上传时挂，挂不到第二块板。"""

    async def _run() -> None:
        await SpaceMaterialRepository(session=session).create(
            space_id=space_id, material_id=material_id, visibility=visibility
        )

    portal.call(_run)


# ── 1. 读侧过滤：翻进「仅管理员」档，学生的上下文里就没有它 ──────────────────


def test_a_material_locked_to_admins_is_dropped_from_for_project(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
):
    """空间默认点名一份课件，学生先看到它；把它翻进「仅管理员」档之后，
    ``for_project`` 就不该再把它交给这个项目。"""
    board = _board(user_client, api_client)
    material_id = _upload_into(api_client, board)

    named = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {"materialIds": [material_id]}},
        headers=_auth(board["token"]),
    )
    assert named.status_code == 200, named.text

    task = _publish_task(api_client, board, name="带课件的题")
    project_id = _project_under(api_client, user_client, board, task_id=task["id"])

    before = _for_project(_portal, db_session, project_id)
    assert material_id in _material_ids_in(before)

    flipped = _set_visibility(api_client, board, material_id, "admins")
    assert flipped.status_code == 200, flipped.text

    after = _for_project(_portal, db_session, project_id)
    assert material_id not in _material_ids_in(after)


# ── 2. 判据本身：member_readable_material_ids 的几个格子 ─────────────────────


def test_member_readable_material_ids_keeps_only_what_a_member_could_read(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board_a = _board(user_client, api_client)
    board_b = _board(user_client, api_client)

    loose = _upload_loose(api_client, board_a)  # 没有关联行 = 普通素材
    members = _upload_into(api_client, board_a)  # 一条「所有成员」
    admins = _upload_into(api_client, board_a)  # 只有「仅管理员」
    assert _set_visibility(api_client, board_a, admins, "admins").status_code == 200

    # 同一份素材：A 板只进「仅管理员」，B 板进「所有成员」 —— 最宽的那道说了算。
    both = _upload_loose(api_client, board_a)
    _link(
        _portal,
        db_session,
        space_id=board_a["space_id"],
        material_id=both,
        visibility="admins",
    )
    _link(
        _portal,
        db_session,
        space_id=board_b["space_id"],
        material_id=both,
        visibility="members",
    )

    readable = _readable_ids(_portal, db_session, [loose, members, admins, both])

    assert admins not in readable
    assert readable == {loose, members, both}


# ── 3. 写侧拒绝：点名一份「仅管理员」档课件当场报错 ──────────────────────────


def test_the_space_form_refuses_a_material_locked_to_admins(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _board(user_client, api_client)
    material_id = _upload_into(api_client, board)
    assert _set_visibility(api_client, board, material_id, "admins").status_code == 200

    resp = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {"materialIds": [material_id]}},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["data"]["field"] == "materialIds"


def test_the_task_form_refuses_a_material_locked_to_admins(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _board(user_client, api_client)
    material_id = _upload_into(api_client, board)
    assert _set_visibility(api_client, board, material_id, "admins").status_code == 200

    resp = api_client.post(
        "/tasks",
        json={
            "name": "点名了锁住的课件",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": board["space_id"],
            "categoryId": board["category_id"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(datetime.now(UTC).timestamp() * 1000) + 7 * 86400 * 1000,
            "teaching": {"materialIds": [material_id]},
        },
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["data"]["field"] == "materialIds"


# ── 4. 两条读路一个结论 ─────────────────────────────────────────────────────


def test_both_read_paths_agree_on_the_same_locked_material(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
):
    """一份课件在题目层被点名、事后翻进「仅管理员」档：``for_project`` 不带它，
    ``GET /tasks/{id}/inheritance`` 的 ``teaching.materialIds`` 与 ``materials``
    也都不带它 —— 同一份响应不能自己打自己。"""
    board = _board(user_client, api_client)
    material_id = _upload_into(api_client, board)

    task = _publish_task(
        api_client,
        board,
        name="点名课件后被翻档",
        teaching={"materialIds": [material_id]},
    )
    assert _set_visibility(api_client, board, material_id, "admins").status_code == 200

    project_id = _project_under(api_client, user_client, board, task_id=task["id"])

    inheritance = api_client.get(
        f"/tasks/{task['id']}/inheritance", headers=_auth(board["token"])
    )
    assert inheritance.status_code == 200, inheritance.text
    body = inheritance.json()
    assert material_id not in body["teaching"]["materialIds"]
    assert material_id not in [item["id"] for item in body["materials"]]

    context = _for_project(_portal, db_session, project_id)
    assert material_id not in _material_ids_in(context)
