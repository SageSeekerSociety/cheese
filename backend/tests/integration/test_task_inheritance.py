"""从题目建项目之前要给人看的那张清单 (#944) —— 界面数据就是合成结果。

本文件盯住的两件事，恰好是这条功能的验证底线：

**界面拿到的指导 = 合成的结果。** `GET /tasks/{id}/inheritance` 报出来的
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

SPACE_WEEK = {"mode": "guide", "system_prompt": "板上的要求。"}
CATEGORY_WEEK = {"mode": "free", "system_prompt": "项目集上留下的旧配置。"}
TASK_WEEK = {"mode": "spec", "system_prompt": "这道题的要求。"}

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
    # 信封响应：前端 axios 栈（``ResponseDataType``）按 ``.data`` 取数，
    # 这里照同一个契约拆，才不会出现「后端自测绿、前端拿不到」。
    return resp.json()["data"]


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

    `teaching` 是空间和题目合起来的那一份，`source` 是同一趟读法带出来的。断言两边
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
    _join_board(
        client, space_id=_space_id_of(client, task_id), handle="inheritance-viewer"
    )

    data = _get(client, task_id=task_id, token=token)
    expected = _resolve_from_db(client, task_id)

    assert data["resourcePack"] == {"compute_credits": 500}
    assert data["teaching"]["mode"] == expected.teaching.mode
    assert data["teaching"]["systemPrompt"] == expected.teaching.system_prompt
    assert data["teaching"]["spacePrompt"] == expected.teaching.space_prompt
    # 两层都说了：题目的方式生效，两份要求都在，项目集上的旧配置不读。
    assert data["teaching"]["mode"] == "spec"
    assert data["teaching"]["spacePrompt"] == "板上的要求。"
    assert data["teaching"]["systemPrompt"] == "这道题的要求。"
    assert data["teaching"]["source"] == "both"


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
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-mutation-task",
    )

    before = _get(client, task_id=task_id, token=token)
    assert before["teaching"]["mode"] == "spec"
    assert before["teaching"]["source"] == "both"

    _clear_task_override(client, task_id)

    after = _get(client, task_id=task_id, token=token)
    assert after["teaching"]["mode"] == "guide"
    assert after["teaching"]["source"] == "space"
    assert after["teaching"]["systemPrompt"] == SPACE_WEEK["system_prompt"]
    assert after["teaching"]["spacePrompt"] is None


def test_dropping_the_space_layer_changes_what_the_screen_shows(client: TestClient):
    """变异验证：把空间那层去掉，屏幕上那项必须跟着变（值 + 来源层）。

    少了这条，一个根本没读 `Space.teaching` 的实现
    在只有空间配置时也「看起来」能用，直到有人发现清单永远是空的。
    """
    task_id = seed_task_with_protocol(client, space_teaching=SPACE_WEEK)
    token = seed_user(client, "inheritance-mutation-space")
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-mutation-space",
    )
    space_id = _space_id_of(client, task_id)

    before = _get(client, task_id=task_id, token=token)
    assert before["teaching"]["mode"] == "guide"
    assert before["teaching"]["source"] == "space"

    _clear_space_teaching(client, space_id)

    after = _get(client, task_id=task_id, token=token)
    assert after["teaching"]["mode"] is None
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


def test_a_viewer_outside_the_board_reads_nothing(client: TestClient):
    """题目只给它那块板里的人：板外的人读这道题的继承清单，和读题目详情一样 404
    —— 指导、资料一样都不交出去。"""
    task_id = seed_task_with_protocol(client, space_teaching=SPACE_WEEK)
    space_id = _space_id_of(client, task_id)
    _add_material(client, space_id=space_id, name="公开讲义.pdf", visibility="members")
    token = seed_user(client, "inheritance-outsider")

    resp = client.get(f"/tasks/{task_id}/inheritance", headers=_headers(token))

    assert resp.status_code == 404, resp.text
    assert "公开讲义.pdf" not in resp.text


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


def test_the_screen_shows_the_default_teammate_and_the_skills_it_brings(
    client: TestClient,
):
    """题目指定的默认队友 (#944)：屏幕报出类型名、预设标题、以及它会带的技能。

    这一格就是「含技能」在按下「领取」之前唯一看得见的地方 —— 建出来的项目里
    那个芝士会带着哪几颗技能，靠的就是这里。
    """
    task_id = seed_task_with_protocol(client, default_role="academic-research")
    token = seed_user(client, "inheritance-agent-viewer")
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-agent-viewer",
    )

    data = _get(client, task_id=task_id, token=token)

    assert data["agent"]["typeName"] == "academic-research"
    assert data["agent"]["displayName"] == "学术研究"
    assert data["agent"]["skills"] == ["documents", "wolfram"]


def test_a_role_that_names_no_shipped_preset_is_not_dressed_up_as_one(
    client: TestClient,
):
    """`default_role` 对不上任何发货预设（老数据、拼错）时不假装有一个队友。

    编一个空技能的名字出来，比空着更糟 —— 它会让人以为这个项目生来带一个芝士。
    """
    task_id = seed_task_with_protocol(client, default_role="no-such-role")
    token = seed_user(client, "inheritance-agent-unknown")
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-agent-unknown",
    )

    data = _get(client, task_id=task_id, token=token)

    assert data["agent"] is None


def test_a_tasks_default_teammate_lands_in_the_project_it_builds(client: TestClient):
    """题目指定的默认队友（含技能）→ 建出的项目里，芝士带着那个预设的技能 (#944)。

    机制早就通了（`_accept_task_protocol` → `set_type` → `initial_configuration`），
    这一条把它钉住：技能真的落进那一行 `configuration.skills`，而不是只出现在建项目
    之前的预览里 —— 「看得见」和「真的给了」是两件事，各自要有自己的证据。
    """
    from app.domain.agent_instance.adapters import default_teammate
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.agent_type.library import preset_types
    from app.domain.project.services import ProjectService
    from app.domain.task.adapters import TaskTerms
    from app.domain.task.models import TaskMembership
    from app.domain.user.repositories import UserRepository

    handle = "edu-project-owner"
    task_id = seed_task_with_protocol(client, default_role="academic-research")
    # A real user id: the membership keys off it (submitter_type 0).
    seed_user(client, handle)
    expected = list(preset_types()["academic-research"].skills)
    holder: dict = {}

    async def _build() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None
            now = datetime.now(UTC)
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=user.id,
                    # ApproveType.APPROVED — the one state that releases the pack.
                    approved=0,
                    is_team=False,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await session.flush()
            project = await ProjectService(
                session,
                default_teammate=default_teammate,
                task_terms=TaskTerms(session),
            ).create(
                owner_handle=handle,
                name="教育项目",
                forge_kind="github_app",
                external_task_id=task_id,
            )
            await session.flush()
            instance = await session.get(
                AgentInstance, project.default_agent_instance_id
            )
            assert instance is not None
            holder["type_name"] = instance.type_name
            holder["skills"] = list(instance.configuration.get("skills") or [])
            await session.commit()

    asyncio.run(_build())

    assert holder["type_name"] == "academic-research"
    assert holder["skills"] == expected


def test_the_screen_shows_the_skills_every_teammate_will_carry(client: TestClient):
    """题目给项目里每个 AI 队友的技能（`teaching.skills`），领题之前就看得到。"""
    task_id = seed_task_with_protocol(
        client,
        override={"teaching": {"skills": ["documents", "figma"]}},
    )
    token = seed_user(client, "inheritance-skills-viewer")
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-skills-viewer",
    )

    data = _get(client, task_id=task_id, token=token)

    assert data["teaching"]["skills"] == ["documents", "figma"]
    assert data["teaching"]["source"] == "task"


def test_the_screen_shows_the_pack_prompt_the_default_teammate_gets(
    client: TestClient,
):
    """A 资源包's prompt is added to the default teammate of every project built
    from the 题目, so the screen says it is in force before the 题目 is taken."""
    task_id = seed_task_with_protocol(
        client, resource_pack={"prompt": "先写测试再写实现"}
    )
    token = seed_user(client, "inheritance-pack-prompt-viewer")
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-pack-prompt-viewer",
    )

    data = _get(client, task_id=task_id, token=token)

    assert data["packPrompt"] == "先写测试再写实现"


def test_a_plain_viewer_never_reads_the_private_guidance(client: TestClient):
    """私有指导（#944 第二种可见性）只给出题人/板管理员：一个普通登录用户读这道题
    的继承清单时，`privatePrompt` 那个键根本不在（不是空串，是没有）。"""
    task_id = seed_task_with_protocol(
        client,
        override={"teaching": {"private_prompt": "参考答案是 42"}},
    )
    token = seed_user(client, "inheritance-plain-viewer")
    _join_board(
        client,
        space_id=_space_id_of(client, task_id),
        handle="inheritance-plain-viewer",
    )

    data = _get(client, task_id=task_id, token=token)

    assert "privatePrompt" not in data["teaching"]


def _default_teammate_body(client: TestClient, task_id: int, handle: str) -> str:
    """Build a project from ``task_id`` for an approved participant and read
    its default teammate's persona."""
    from app.domain.agent_instance.adapters import default_teammate
    from app.domain.agent_instance.models import AgentInstance
    from app.domain.project.services import ProjectService
    from app.domain.task.adapters import TaskTerms
    from app.domain.task.models import TaskMembership
    from app.domain.user.repositories import UserRepository

    seed_user(client, handle)
    holder: dict = {}

    async def _build() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None
            now = datetime.now(UTC)
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=user.id,
                    approved=0,
                    is_team=False,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
            await session.flush()
            project = await ProjectService(
                session,
                default_teammate=default_teammate,
                task_terms=TaskTerms(session),
            ).create(
                owner_handle=handle,
                name="题目包项目",
                forge_kind="github_app",
                external_task_id=task_id,
            )
            await session.flush()
            instance = await session.get(
                AgentInstance, project.default_agent_instance_id
            )
            assert instance is not None
            holder["body"] = instance.configuration.get("body") or ""
            await session.commit()

    asyncio.run(_build())
    return holder["body"]


def test_a_project_built_from_a_task_whose_pack_has_a_prompt_gets_it(
    client: TestClient,
):
    """Until the contract migration moves a 资源包's prompt into the 题目's
    guidance, a project built from the 题目 gets it at the end of its default
    teammate's persona, with a preset and without one."""
    with_preset = seed_task_with_protocol(
        client,
        resource_pack={"prompt": "先写测试再写实现"},
        default_role="academic-research",
    )
    bare = seed_task_with_protocol(client, resource_pack={"prompt": "先写测试再写实现"})

    assert _default_teammate_body(client, with_preset, "pack-prompt-preset").endswith(
        "\n\n先写测试再写实现"
    )
    assert (
        _default_teammate_body(client, bare, "pack-prompt-bare") == "先写测试再写实现"
    )
