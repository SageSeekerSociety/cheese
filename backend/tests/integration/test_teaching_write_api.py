"""「给 AI 队友的指导」的读写口 (#944)，按浏览器会走的接口逐条断。

两级各有读与写，都搭在**已有的实体接口**上，不另开路由：空间默认随
`PATCH /spaces/{id}` 写、随 `GET /spaces/{id}` 读；题目覆盖随 `POST /tasks` /
`PATCH /tasks/{id}` 写、随同一份 task 响应读。判据也沿用已有的：空间是「管得了这
块板的管理员」，题目是「管得了这道题的人」（出题人或空间管理员）。

这里钉四件事：写进去读得回来；同一个键的「整份替换 / 省略即不动」；引用校验与空
间设置里的管理员门；以及题目那一层和空间那一层合起来生效 —— 后者走真读路
（`for_project`）验一次，免得两级各自都对、合起来却不生效。

登录接口按用户名限速，所以每个用户都用 `user_client.create_user()` 现造。
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

SPACE_GUIDE = {"mode": "guide", "systemPrompt": "空间的要求"}

#: All seven fields set, in the request's spelling — what the settings form sends.
#: `privatePrompt` 是出题人可见的那一格（`teaching_to_api(include_private=True)`），
#: 这几条用的都是出题人/管理员的身份，所以它也在读回来的那一份里。
#: No 课件 / 知识 ids: naming one needs a real row, and the reference check has
#: its own tests (`test_teaching_references.py`).
FULL_TEACHING = {
    "mode": "spec",
    "systemPrompt": "先写需求说明，再动手。",
    "privatePrompt": None,
    "learningGoals": ["会读栈追踪"],
    "materialIds": [],
    "documentIds": [],
    "skills": ["wolfram"],
}

#: What a level that says nothing reads back as.
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


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Teaching Write ({suffix})",
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


def _space_teaching_of(api_client: TestClient, board: dict) -> dict:
    resp = api_client.get(f"/spaces/{board['space_id']}", headers=_auth(board["token"]))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["space"]["teaching"]


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
    """A project built on this task: approve it, the student joins the 题目板
    and starts the task, which makes the project."""
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


# ── 空间级：读 + 写 ─────────────────────────────────────────────────────────


def test_the_space_default_reads_back_exactly_as_the_form_wrote_it(
    api_client: TestClient, user_client: UserCreator, db_session
):
    """设置页把读回来的这一份原样填回表单、再原样存回去，所以读和写必须是同一套
    键。读回来换了拼写，表单就是一片空白，再点一次保存就把存着的那份抹掉。"""
    board = _new_board(user_client, api_client)

    resp = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": FULL_TEACHING},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text

    assert resp.json()["data"]["space"]["teaching"] == FULL_TEACHING
    assert _space_teaching_of(api_client, board) == FULL_TEACHING


def test_a_space_patch_that_omits_teaching_leaves_it_alone(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_GUIDE},
        headers=_auth(board["token"]),
    )

    renamed = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"intro": "改了介绍"},
        headers=_auth(board["token"]),
    )
    assert renamed.status_code == 200, renamed.text
    assert _space_teaching_of(api_client, board)["mode"] == "guide"


def test_an_empty_object_clears_the_space_default(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_GUIDE},
        headers=_auth(board["token"]),
    )

    cleared = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {}},
        headers=_auth(board["token"]),
    )
    assert cleared.status_code == 200, cleared.text
    # 「清空」在读取侧的意思 = 每一格都空，这一层不再说话。
    assert _space_teaching_of(api_client, board) == EMPTY_TEACHING


def test_a_non_admin_cannot_write_the_space_default(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    outsider = user_client.create_user()
    outsider_token = user_client.login(api_client, outsider.username, outsider.password)

    resp = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_GUIDE},
        headers=_auth(outsider_token),
    )
    assert resp.status_code == 404, resp.text
    assert _space_teaching_of(api_client, board) == EMPTY_TEACHING


def test_a_space_default_naming_a_missing_material_is_refused(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)

    resp = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {"materialIds": [999_999_999]}},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["data"]["field"] == "materialIds"


# ── 题目级：读 + 写 ─────────────────────────────────────────────────────────


def test_a_task_override_is_written_on_create_and_read_back(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)

    task = _publish_task(
        api_client,
        board,
        name="带指导的题",
        teaching=FULL_TEACHING,
    )

    assert task["teaching"] == FULL_TEACHING


def test_patching_a_task_writes_its_override(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    task = _publish_task(api_client, board, name="先没指导的题")
    assert task["teaching"] == EMPTY_TEACHING

    resp = api_client.patch(
        f"/tasks/{task['id']}",
        json={"teaching": FULL_TEACHING},
        headers=_auth(board["token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["task"]["teaching"] == FULL_TEACHING


def test_a_stranger_cannot_write_a_task_override(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    task = _publish_task(api_client, board, name="别人的题")

    outsider = user_client.create_user()
    outsider_token = user_client.login(api_client, outsider.username, outsider.password)
    resp = api_client.patch(
        f"/tasks/{task['id']}",
        json={"teaching": {"mode": "free"}},
        headers=_auth(outsider_token),
    )
    assert resp.status_code == 403, resp.text


def test_the_task_and_the_space_are_both_read_on_the_real_read_path(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
):
    """两级都写了，芝士开新会话时两份都读到，题目选的方式生效 —— 读的是
    `for_project`，不是把两个响应摆在一起比。"""
    board = _new_board(user_client, api_client)
    api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_GUIDE},
        headers=_auth(board["token"]),
    )
    task = _publish_task(
        api_client,
        board,
        name="加了自己要求的题",
        teaching={"mode": "example", "systemPrompt": "题目的"},
    )
    project_id = _project_under(api_client, user_client, board, task_id=task["id"])

    context = _for_project(_portal, db_session, project_id)

    assert context is not None
    assert context.teaching.mode == "example"
    assert context.teaching.system_prompt == "题目的"
    assert context.teaching.space_prompt == "空间的要求"


def test_a_later_edit_reaches_an_existing_project_on_its_next_read(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
):
    """改一次配置，**已经建好的**那个项目下一次读就是新值。

    这是生效语义的第 2 条（`harness.prompt` 顶上那段）：已有项目的新会话读到此刻
    的配置。钉的是「`for_project` 每次现取，不往项目那一行记一份」—— 谁要是哪天
    改成建项目时存个快照，这条会红。同一个 `project_id` 全程不动。

    第 3 条（运行中的会话保持启动时那一份）这里碰不到：那是 Claude Code 启动时读
    一次 `--append-system-prompt-file` 的事实，没有第二条路可绕（`prompt_text` 不
    携带教学上下文）。
    """
    board = _new_board(user_client, api_client)
    api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_GUIDE},
        headers=_auth(board["token"]),
    )
    task = _publish_task(api_client, board, name="改天换方式的题")
    project_id = _project_under(api_client, user_client, board, task_id=task["id"])

    before = _for_project(_portal, db_session, project_id)
    assert before is not None
    assert before.teaching.mode == "guide"

    changed = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {**SPACE_GUIDE, "mode": "spec"}},
        headers=_auth(board["token"]),
    )
    assert changed.status_code == 200, changed.text

    after = _for_project(_portal, db_session, project_id)
    assert after is not None
    assert after.teaching.mode == "spec"
