"""从题目建项目之前要给人看的那张清单 (#944) —— 界面数据就是合成结果。

本文件盯住的两件事，恰好是这条功能的验证底线：

**界面拿到的指导 = 四层合成的结果。** `GET /tasks/{id}/inheritance` 报出来的
`teaching` 与拿同一组行跑一遍 ``protocol.resolve()`` 得到的逐字段相等
(`test_the_screen_shows_the_composed_guidance_the_turn_will_read`)。这一条不能
省：接口自己再拼一遍指导也「看起来对」，直到某一个字段的别名写错。等值断言把它
钉在同一个函数上。

**去掉一层，界面那项要跟着变。** 一条断言等值的测试可能是空转的 —— 一个把上层的
值抄下来的实现照样等值。所以下面两条各删掉一层（题目、空间），重跑同一个接口，断
言**值和来源层都变了**（`test_dropping_the_task_layer_changes_what_the_screen_shows`、
`test_dropping_the_space_layer_changes_what_the_screen_shows`）。这是变异验证，
留下的是命令与观察到的输出，不是「我相信它走了那条路」。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.domain.space.models import SpaceCategory, SpaceMaterial, SpaceMember
from app.domain.task.models import Task
from app.domain.task.protocol import resolve
from tests.conftest import seed_task_with_protocol, seed_user

SPACE_WEEK = {"system_prompt": "板上默认：第 {current_week} 周。", "current_week": 1}
CATEGORY_WEEK = {
    "system_prompt": "项目集：第 {current_week} 周，只做 {allowed_topics}。",
    "current_week": 3,
    "allowed_topics": ["循环"],
}
TASK_WEEK = {"system_prompt": "这道题改成第 {current_week} 周。", "current_week": 5}

PLAIN_MEMBER = "plain-member"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _join_board(client: TestClient, *, space_id: int, handle: str) -> int:
    """Put ``handle`` in the board's 成员名册 (the row ``is_member`` reads)."""
    user_id = _user_id(client, handle)

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            session.add(
                SpaceMember(
                    space_id=space_id,
                    user_id=user_id,
                    invite_code_id=None,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await session.commit()

    asyncio.run(_seed())
    return user_id


def _user_id(client: TestClient, handle: str) -> int:
    holder: dict[str, int] = {}

    async def _read() -> None:
        from app.domain.user.repositories import UserRepository

        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None
            holder["id"] = user.id

    asyncio.run(_read())
    return holder["id"]


def _add_material(
    client: TestClient, *, space_id: int, name: str, visibility: str
) -> int:
    """One 资料库 row: a ``material`` and the board's link to it, at one 档."""
    from app.domain.materials.models import Material

    holder: dict[str, int] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            material = Material(
                type="file",
                url=f"https://example.invalid/{name}",
                name=name,
                uploader_id=1,
                created_at=now,
                meta={},
            )
            session.add(material)
            await session.flush()
            session.add(
                SpaceMaterial(
                    space_id=space_id,
                    material_id=material.id,
                    visibility=visibility,
                    download_count=0,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await session.commit()
            holder["id"] = material.id

    asyncio.run(_seed())
    return holder["id"]


def _space_id_of(client: TestClient, task_id: int) -> int:
    holder: dict[str, int] = {}

    async def _read() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            task = await session.get(Task, task_id)
            assert task is not None
            holder["id"] = task.space_id

    asyncio.run(_read())
    return holder["id"]


def _resolve_from_db(client: TestClient, task_id: int):
    """拿同一组行跑一遍合成 —— 这条测试里的「正确答案」不是接口给的。"""
    holder: dict = {}

    async def _read() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            task = await session.get(Task, task_id)
            assert task is not None
            category = await session.get(SpaceCategory, task.category_id)
            from app.domain.space.models import Space

            space = await session.get(Space, task.space_id)
            holder["protocol"] = resolve(space=space, category=category, task=task)

    asyncio.run(_read())
    return holder["protocol"]


def _get(client: TestClient, *, task_id: int, token: str) -> dict:
    resp = client.get(f"/tasks/{task_id}/inheritance", headers=_headers(token))
    assert resp.status_code == 200, resp.text
    # 裸响应体：``/tasks`` 这一族（详情、附件清单、参与）都不套
    # ``{"code","message","data"}``。
    return resp.json()


def _clear_space_teaching(client: TestClient, space_id: int) -> None:
    async def _run() -> None:
        from app.domain.space.models import Space

        async with client.test_factory() as session:  # type: ignore[attr-defined]
            space = await session.get(Space, space_id)
            assert space is not None
            space.teaching = {}
            await session.commit()

    asyncio.run(_run())


def _clear_task_override(client: TestClient, task_id: int) -> None:
    async def _run() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            task = await session.get(Task, task_id)
            assert task is not None
            task.protocol_override = None
            await session.commit()

    asyncio.run(_run())


def _mark_unapproved(client: TestClient, task_id: int) -> None:
    """把这道题挪回「还没过审」—— ``approved == 2`` 就是 NONE，见
    ``_ensure_task_readable`` 的第一道闸。"""

    async def _run() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            task = await session.get(Task, task_id)
            assert task is not None
            task.approved = 2
            task.ended_at = None
            await session.commit()

    asyncio.run(_run())


def test_the_screen_shows_the_composed_guidance_the_turn_will_read(client: TestClient):
    """界面数据里的那一段指导，逐个字段等于 `protocol.resolve()` 的结果。

    `teaching` 是四层里最具体的赢，`source` 是同一趟循环带出来的层名。断言两边
    相等而不是各写一份期望值 —— 各写一份的话，两边一起错的时候测试还是绿的。
    """
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE_WEEK,
        teaching=CATEGORY_WEEK,
        override={"teaching": TASK_WEEK},
        resource_pack={"compute_credits": 500},
    )
    token = seed_user(client, "inheritance-viewer")

    data = _get(client, task_id=task_id, token=token)
    expected = _resolve_from_db(client, task_id)

    assert data["resourcePack"] == {"compute_credits": 500}
    assert data["teaching"]["systemPrompt"] == expected.teaching.system_prompt
    assert data["teaching"]["currentWeek"] == expected.teaching.current_week
    assert data["teaching"]["allowedTopics"] == expected.teaching.allowed_topics
    assert data["teaching"]["avoidInCode"] == expected.teaching.avoid_in_code
    # 最具体的那层是题目，所以屏幕上的值和来源层都指向它。
    assert data["teaching"]["currentWeek"] == 5
    assert data["teaching"]["source"] == "task"


def test_dropping_the_task_layer_changes_what_the_screen_shows(client: TestClient):
    """变异验证：把题目那层去掉，屏幕上那项必须跟着变（值 + 来源层）。

    否则一个「只读最上层」的实现也能通过上面那条等值断言。
    """
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE_WEEK,
        teaching=CATEGORY_WEEK,
        override={"teaching": TASK_WEEK},
    )
    token = seed_user(client, "inheritance-mutation-task")

    before = _get(client, task_id=task_id, token=token)
    assert before["teaching"]["currentWeek"] == 5
    assert before["teaching"]["source"] == "task"

    _clear_task_override(client, task_id)

    after = _get(client, task_id=task_id, token=token)
    assert after["teaching"]["currentWeek"] == 3
    assert after["teaching"]["source"] == "category"
    assert after["teaching"]["systemPrompt"] == CATEGORY_WEEK["system_prompt"]


def test_dropping_the_space_layer_changes_what_the_screen_shows(client: TestClient):
    """变异验证：把空间那层去掉，屏幕上那项必须跟着变（值 + 来源层）。

    这一层是 #944 新加的第四层 —— 少了这条，一个根本没读 `Space.teaching` 的实现
    在只有空间配置时也「看起来」能用，直到有人发现清单永远是空的。
    """
    task_id = seed_task_with_protocol(client, space_teaching=SPACE_WEEK)
    token = seed_user(client, "inheritance-mutation-space")
    space_id = _space_id_of(client, task_id)

    before = _get(client, task_id=task_id, token=token)
    assert before["teaching"]["currentWeek"] == 1
    assert before["teaching"]["source"] == "space"

    _clear_space_teaching(client, space_id)

    after = _get(client, task_id=task_id, token=token)
    assert after["teaching"]["currentWeek"] is None
    assert after["teaching"]["systemPrompt"] is None
    assert after["teaching"]["source"] is None


def test_the_screen_lists_the_members_tier_materials_and_hides_the_admins_tier(
    client: TestClient,
):
    """「会被带上的资料」= 板子资料库里「所有成员」那一档；「仅管理员」的不列。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE_WEEK)
    space_id = _space_id_of(client, task_id)
    _add_material(client, space_id=space_id, name="公开讲义.pdf", visibility="members")
    _add_material(
        client, space_id=space_id, name="仅管理员档资料.pdf", visibility="admins"
    )
    seed_user(client, PLAIN_MEMBER)
    _join_board(client, space_id=space_id, handle=PLAIN_MEMBER)
    token = seed_user(client, PLAIN_MEMBER)

    data = _get(client, task_id=task_id, token=token)

    assert [m["name"] for m in data["materials"]] == ["公开讲义.pdf"]
    assert all(m["visibility"] == "members" for m in data["materials"])
    # 字节的地址从不进这个响应 —— 「仅管理员」那一档靠这个才成立。
    assert all("url" not in m for m in data["materials"])


def test_a_viewer_outside_the_board_gets_no_materials_not_an_error(client: TestClient):
    """一道没开可见范围的题谁都看得见；看不见那块板的人只是拿到空清单。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE_WEEK)
    space_id = _space_id_of(client, task_id)
    _add_material(client, space_id=space_id, name="公开讲义.pdf", visibility="members")
    token = seed_user(client, "inheritance-outsider")

    data = _get(client, task_id=task_id, token=token)

    assert data["materials"] == []
    # 指导那一半照样在：资料拿不到不是「这道题对你不可见」。
    assert data["teaching"]["source"] == "space"


def test_an_unapproved_task_is_forbidden_and_hands_over_nothing(client: TestClient):
    """未过审的题：和 ``GET /tasks/{id}`` 一样 403，而且一个字都不交出去。

    这一条是复核抓出来的真缺陷留下的钉子。这条接口原先只走 ``can_view_task``，
    而它在题目没开可见范围时对任何登录用户都放行 —— 于是同一道未过审的题，详情
    是 403、清单却是 200，还把 ``resourcePack``（算力额度就在里面）与板上默认的
    指导交了出去；调用者若是板成员，members 档的资料名也一并到手。任何登录用户
    顺序枚举 id 就能拿。
    """
    task_id = seed_task_with_protocol(
        client,
        space_teaching=SPACE_WEEK,
        resource_pack={"compute_credits": 999},
    )
    space_id = _space_id_of(client, task_id)
    _add_material(client, space_id=space_id, name="公开讲义.pdf", visibility="members")
    _mark_unapproved(client, task_id)

    token = seed_user(client, "inheritance-unapproved")

    # 详情那一侧是怎么答的，这里就得怎么答 —— 这条断言就是「同一条判据」。
    detail = client.get(f"/tasks/{task_id}", headers=_headers(token))
    assert detail.status_code == 403, detail.text

    resp = client.get(f"/tasks/{task_id}/inheritance", headers=_headers(token))
    assert resp.status_code == 403, resp.text
    # 403 的正文里不许漏出这道题会交出什么。
    body = resp.text
    assert "999" not in body
    assert "compute_credits" not in body
    assert "systemPrompt" not in body
    assert "公开讲义.pdf" not in body


def test_an_unknown_task_is_a_404(client: TestClient):
    token = seed_user(client, "inheritance-404")
    resp = client.get("/tasks/99999999/inheritance", headers=_headers(token))
    assert resp.status_code == 404, resp.text
