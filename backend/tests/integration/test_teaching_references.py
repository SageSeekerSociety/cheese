"""`teaching.documentIds` / `materialIds` 指向谁的 —— 写入侧那一半。

读取侧 (`teaching.for_project`) 不做成员校验：一门课点了名的团队文档，课下项目的
AI 队友就读得到（#3774）。所以**写入侧**必须保证老师只能点自己读得到的：一个题目板
的所有者不能把 `teaching` 指向**别的团队**的文档，否则那道题里的 agent 就读到了别人
团队的东西。

这里按浏览器会收到的状态码逐条断：引用别人团队的文档 → 4xx 且没落库；引用自己团队
的文档 → 200 且 `for_project()` 读得到标题；对照 —— 同一份文档直接读仍是 403。另外
两条把「指向不存在的 id 要明说是哪个字段」和「课件没有归属、不需要成员校验」钉住。

登录接口按用户名限速，所以每个用户都用 `user_client.create_user()` 现造，不碰真
种子账号。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.project.repositories import ProjectRepository
from app.domain.task import teaching as teaching_context
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)

#: A 空间 that says nothing, as the API reports it. Read with the board OWNER's
#: token, so the 私有指导 (`privatePrompt`) key is present and null.
EMPTY_TEACHING = {
    "mode": None,
    "systemPrompt": None,
    "privatePrompt": None,
    "learningGoals": [],
    "materialIds": [],
    "documentIds": [],
    "skills": [],
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    """一块已过审的题目板；建版的人就是它的 OWNER（题目板的所有者）。"""
    creator = user_client.create_user()
    token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Teaching Refs ({suffix})",
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
        "creator": creator,
        "token": token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _team_with_document(
    api_client: TestClient, *, owner, token: str, name: str
) -> dict:
    """一个团队 + 里面一份团队文档，团队的所有者由调用方指定。

    `teamId` 必须落在一个调用方（也就是写入 `teaching` 的那个人）确实是成员的
    团队上，否则连写入侧都不该放行 —— 这正是被测的那条判据。
    """
    suffix = unique_int(10000000, 99999999)
    team = api_client.post(
        "/teams",
        json={
            "name": f"Teaching Refs Team ({suffix})",
            "intro": "一个团队",
            "description": "A lengthy text. " * 100,
            "avatarId": 1,
        },
        headers=_auth(token),
    )
    assert team.status_code == 201, team.text
    team_id = team.json()["data"]["team"]["id"]

    doc = api_client.post(
        f"/teams/{team_id}/documents", json={"title": name}, headers=_auth(token)
    )
    assert doc.status_code == 200, doc.text
    return {
        "owner": owner,
        "token": token,
        "team_id": team_id,
        "document_id": doc.json()["data"]["id"],
        "document_title": name,
    }


def _patch_teaching(
    api_client: TestClient,
    board: dict,
    token: str,
    *,
    document_ids: list[str] | None = None,
    material_ids: list[int] | None = None,
):
    return api_client.patch(
        f"/spaces/{board['space_id']}",
        json={
            "teaching": {
                "materialIds": material_ids or [],
                "documentIds": document_ids or [],
            }
        },
        headers=_auth(token),
    )


def _teaching_of(api_client: TestClient, board: dict, token: str) -> dict:
    resp = api_client.get(f"/spaces/{board['space_id']}", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["space"]["teaching"]


def _publish_task(api_client: TestClient, board: dict, *, name: str) -> int:
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
            "deadline": int(datetime.now(UTC).timestamp() * 1000) + 7 * 86400 * 1000,
        },
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _project_under(
    api_client: TestClient, user_client: UserCreator, board: dict, *, task_id: int
) -> str:
    """一个学生用这道题建的项目：题目过审、人进了这门课，再开始做这道题。"""
    student = user_client.create_user()
    student_token = _login(user_client, api_client, student)
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
    # 开始做这道题 (a task starts directly by default): the claim opens its project.
    started = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(student_token)
    )
    assert started.status_code in (200, 201), started.text
    return started.json()["data"]["project"]["id"]


def _for_project(portal, session, project_id: str):
    """`for_project` for one project, driven on the app's own loop."""
    holder: dict = {}

    async def _run() -> None:
        project = await ProjectRepository(session).get(uuid.UUID(str(project_id)))
        holder["ctx"] = await teaching_context.for_project(
            session=session, project=project
        )

    portal.call(_run)
    return holder["ctx"]


def _seed_material(portal, session, *, uploader_id: int, name: str) -> int:
    """A 课件 somebody else uploaded — `Material` has no owning team."""
    holder: dict = {}

    async def _run() -> None:
        from app.domain.materials.models import Material

        row = Material(
            type="file",
            url="https://example.invalid/other.pdf",
            name=name,
            uploader_id=uploader_id,
            created_at=datetime.now(UTC),
            meta={},
        )
        session.add(row)
        await session.flush()
        holder["id"] = row.id

    portal.call(_run)
    return holder["id"]


def test_referencing_another_teams_document_is_refused(
    api_client: TestClient, user_client: UserCreator
):
    """(a)+(c)：板主引用别的团队的文档 → 4xx 且 `teaching` 没变；同一份文档他
    直接读也还是 403。"""
    board = _new_board(user_client, api_client)  # 乙：板的所有者，不是 T 的成员
    stranger = user_client.create_user()
    stranger_token = _login(user_client, api_client, stranger)
    foreign = _team_with_document(
        api_client,
        owner=stranger,
        token=stranger_token,
        name=f"SECRET-{unique_int(10000000, 99999999)}",
    )

    # 对照 (c)：他对这份文档本来就读不到。
    peek = api_client.get(
        f"/documents/{foreign['document_id']}", headers=_auth(board["token"])
    )
    assert peek.status_code == 403, peek.text

    denied = _patch_teaching(
        api_client, board, board["token"], document_ids=[foreign["document_id"]]
    )
    assert 400 <= denied.status_code < 500, denied.text
    # 被拒之后，存着的配置一个字都没动。
    assert _teaching_of(api_client, board, board["token"]) == EMPTY_TEACHING


def test_referencing_my_own_teams_document_sticks_and_reaches_the_course(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """(b)：引用自己团队的文档 → 200，`document_ids` 落库，`for_project()`
    读得到标题 —— 正路走得通。"""
    board = _new_board(user_client, api_client)
    # 文档的团队就是板主自己的团队 —— 他确实是成员。
    mine = _team_with_document(
        api_client,
        owner=board["creator"],
        token=board["token"],
        name=f"SEASON-{unique_int(10000000, 99999999)}",
    )

    ok = _patch_teaching(
        api_client, board, board["token"], document_ids=[mine["document_id"]]
    )
    assert ok.status_code == 200, ok.text
    assert _teaching_of(api_client, board, board["token"])["documentIds"] == [
        mine["document_id"]
    ]

    task_id = _publish_task(api_client, board, name="这门课的题")
    project_id = _project_under(api_client, user_client, board, task_id=task_id)

    context = _for_project(_portal, db_session, project_id)
    assert context is not None
    assert [d["id"] for d in context.documents] == [mine["document_id"]]
    assert [d["title"] for d in context.documents] == [mine["document_title"]]


def test_a_missing_document_id_is_named_not_dropped(
    api_client: TestClient, user_client: UserCreator
):
    """指向不存在的文档 → 4xx，且指出是 `documentIds` 这个字段，而不是静默丢。

    写入侧的取向与 `Teaching.from_json` 相反：那边丢坏字段是「不能因为一个字段
    拼错就让二十道题全挂」；这里有人正看着表单，可以被告知哪个字段错了。
    """
    board = _new_board(user_client, api_client)

    denied = _patch_teaching(
        api_client, board, board["token"], document_ids=[str(uuid.uuid4())]
    )
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["data"]["field"] == "documentIds"
    assert _teaching_of(api_client, board, board["token"]) == EMPTY_TEACHING


def test_a_missing_material_id_is_named_not_dropped(
    api_client: TestClient, user_client: UserCreator
):
    """课件同理：不存在的 id → 400，指出 `materialIds`。"""
    board = _new_board(user_client, api_client)

    denied = _patch_teaching(
        api_client, board, board["token"], material_ids=[999_999_999]
    )
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["data"]["field"] == "materialIds"
    assert _teaching_of(api_client, board, board["token"]) == EMPTY_TEACHING


def test_someone_elses_material_is_allowed_because_material_has_no_owner(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """`Material` 没有归属：别的上传者的课件照样引用得动 —— 记下「不需要核」的结论。

    `Material` 只有 `uploader_id`，没有团队；`GET /materials/{id}` 对任何登录用户
    都开着。所以这里没有「别人的课件」这种可泄露的东西，也就不加成员校验。
    """
    board = _new_board(user_client, api_client)
    stranger = user_client.create_user()
    material_id = _seed_material(
        _portal, db_session, uploader_id=stranger.user_id, name="别人的课件"
    )

    ok = _patch_teaching(api_client, board, board["token"], material_ids=[material_id])
    assert ok.status_code == 200, ok.text
    assert _teaching_of(api_client, board, board["token"])["materialIds"] == [
        material_id
    ]


def test_the_other_teaching_fields_are_untouched(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    """回归：引用校验只碰 `materialIds` / `documentIds`，`mode` / `systemPrompt`
    照旧落库、照旧进这一轮的开场上下文。"""
    board = _new_board(user_client, api_client)
    teaching = {
        "mode": "guide",
        "systemPrompt": "只讲循环，不碰递归。",
        "materialIds": [],
        "documentIds": [],
    }
    resp = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": teaching},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text

    # 回复带上空的几格：`learningGoals`、`skills` 与（板主看得见的）`privatePrompt`。
    assert _teaching_of(api_client, board, board["token"]) == {
        **teaching,
        "learningGoals": [],
        "skills": [],
        "privatePrompt": None,
    }

    task_id = _publish_task(api_client, board, name="这门课的题（其它字段）")
    project_id = _project_under(api_client, user_client, board, task_id=task_id)
    context = _for_project(_portal, db_session, project_id)
    assert context is not None
    assert context.teaching.mode == "guide"
    assert context.teaching.system_prompt == "只讲循环，不碰递归。"
