"""「给 AI 队友的指导」的读写口 (#944)，按浏览器会走的接口逐条断。

两级各有读与写，都搭在**已有的实体接口**上，不另开路由：空间默认随
`PATCH /spaces/{id}` 写、随 `GET /spaces/{id}` 读；题目覆盖随 `POST /tasks` /
`PATCH /tasks/{id}` 写、随同一份 task 响应读。判据也沿用已有的：空间是「管得了这
块板的管理员」，题目是「管得了这道题的人」（出题人或空间管理员）。

这里钉四件事：写进去读得回来；同一个键的「整份替换 / 省略即不动」；引用校验与空
间设置里的管理员门；以及题目那一层确实盖过空间默认 —— 后者走真读路
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
    post_project,
    unique_int,
)

SPACE_WEEK = {
    "systemPrompt": "空间默认：第 {current_week} 周",
    "currentWeek": 1,
    "allowedTopics": ["空间话题"],
}

#: All six fields set, in the request's spelling — what the settings form sends.
#: No 课件 / 知识 ids: naming one needs a real row, and the reference check has
#: its own tests (`test_teaching_references.py`).
FULL_TEACHING = {
    "systemPrompt": "第 {current_week} 周，只做 {allowed_topics}。",
    "currentWeek": 3,
    "allowedTopics": ["循环", "数组"],
    "avoidInCode": ["递归"],
    "materialIds": [],
    "knowledgeIds": [],
}

#: What a level that says nothing reads back as.
EMPTY_TEACHING = {
    "systemPrompt": None,
    "currentWeek": None,
    "allowedTopics": [],
    "avoidInCode": [],
    "materialIds": [],
    "knowledgeIds": [],
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
    """A project built on this task: approve it, the student joins the 题目板,
    claims the task, then `POST /projects` with the task linked."""
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
            "external_task_id": task_id,
        },
        headers=_auth(student_token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


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
        json={"teaching": SPACE_WEEK},
        headers=_auth(board["token"]),
    )

    renamed = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"intro": "改了介绍"},
        headers=_auth(board["token"]),
    )
    assert renamed.status_code == 200, renamed.text
    assert _space_teaching_of(api_client, board)["currentWeek"] == 1


def test_an_empty_object_clears_the_space_default(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_WEEK},
        headers=_auth(board["token"]),
    )

    cleared = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {}},
        headers=_auth(board["token"]),
    )
    assert cleared.status_code == 200, cleared.text
    # 「清空」在读取侧的意思 = 六格全空，于是下一级（或没有下一级时）说了算。
    assert _space_teaching_of(api_client, board) == EMPTY_TEACHING


def test_a_non_admin_cannot_write_the_space_default(
    api_client: TestClient, user_client: UserCreator, db_session
):
    board = _new_board(user_client, api_client)
    outsider = user_client.create_user()
    outsider_token = user_client.login(api_client, outsider.username, outsider.password)

    resp = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_WEEK},
        headers=_auth(outsider_token),
    )
    assert resp.status_code == 403, resp.text
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
        json={"teaching": {"currentWeek": 9}},
        headers=_auth(outsider_token),
    )
    assert resp.status_code == 403, resp.text


def test_the_task_override_beats_the_space_default_on_the_real_read_path(
    api_client: TestClient,
    user_client: UserCreator,
    db_session,
    _portal,
):
    """两级都写了，芝士开新会话时读到题目的那份 —— 读的是 `for_project`，不是把
    两个响应摆在一起比。"""
    board = _new_board(user_client, api_client)
    api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": SPACE_WEEK},
        headers=_auth(board["token"]),
    )
    task = _publish_task(
        api_client,
        board,
        name="覆盖空间默认的题",
        teaching={"systemPrompt": "题目的", "currentWeek": 3},
    )
    project_id = _project_under(api_client, user_client, board, task_id=task["id"])

    context = _for_project(_portal, db_session, project_id)

    assert context is not None
    assert context.teaching.current_week == 3
    assert context.teaching.system_prompt == "题目的"


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
        json={"teaching": SPACE_WEEK},
        headers=_auth(board["token"]),
    )
    task = _publish_task(api_client, board, name="改天换周次的题")
    project_id = _project_under(api_client, user_client, board, task_id=task["id"])

    before = _for_project(_portal, db_session, project_id)
    assert before is not None
    assert before.teaching.current_week == 1

    changed = api_client.patch(
        f"/spaces/{board['space_id']}",
        json={"teaching": {**SPACE_WEEK, "currentWeek": 4}},
        headers=_auth(board["token"]),
    )
    assert changed.status_code == 200, changed.text

    after = _for_project(_portal, db_session, project_id)
    assert after is not None
    assert after.teaching.current_week == 4
