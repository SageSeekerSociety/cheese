"""撤权立刻生效，出题者有自己的门 —— 同一个判断，三处可观测的后果。

``app.auth.project_access.may_read_project`` 是「谁能读这个项目的对话与摘要」的
唯一答案；``test_project_reads_need_membership.py`` 测的是它的门（非成员进不来）。
这里测另外三件它必须做到的事，每一件都是某个具体的人在某一天会碰上的：

1. **撤权立刻生效。** 成员被移出后，同一个 URL、同样的参数，下一次请求就必须被
   拒 —— 项目、房间列表（工作台页面加载的两支请求）、AI 聚合（``/contributions``
   这类摘要）与名册一起。这条同时是「摘要没有缓存」的实测：如果读路径上留着撤权
   前算好的那一份，这里拿到的就是一次旧的 200。
2. **出题者不是越权。** 赛题报名项目归报名者所有（``for_participation`` 把报名者
   写成 ``owner_handle``），出题者不在任何名册上。他能读这道赛题的项目，理由是
   ``Project.external_task_id`` → ``Task.creator_id``：「这道题是我出的」——不是
   「我是老师」。所以同题的其他参与者读不到（那是把别人的项目给竞争对手看）。
3. **同一判断覆盖到别的门。** 项目文件与实时终端过去各有一份自己的「成员或所有者」
   拷贝，其中两份漏了「项目所属小队」。撤权后它们必须和别的门同时关，队友也必须
   和名册上的人一样进得来。

每条断言都是浏览器会收到的东西（状态码），不看实现。
"""

import asyncio

from tests.conftest import seed_space, seed_user
from tests.integration.conftest import add_external_member, post_project
from tests.integration.test_project_reads_need_membership import off_the_street
from tests.integration.test_team_member_enters_team_project import (
    _bearer,
    _team,
    _team_project,
)

# 撤权后必须一起关的门。名字说的是「泄露了会让人失去什么」。
DOORS = {
    "项目本身": "/projects/{pid}",
    "房间列表（页面）": "/topics?project_id={pid}",
    "任务列表": "/projects/{pid}/tasks",
    "成员名册": "/projects/{pid}/members",
    "决策记录": "/projects/{pid}/decisions",
    "AI 摘要（贡献聚合）": "/projects/{pid}/contributions",
}


def _project(client, owner: str = "alice") -> tuple[str, str]:
    """``(project_id, root_topic_id)``，所有者为 ``owner``。"""
    r = post_project(client, json={"name": "P", "owner_handle": owner})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    return data["id"], data["root_topic_id"]


def _add(client, pid: str, handle: str, *, by: str = "alice") -> None:
    """``handle`` comes in from outside the team: invited by ``by``, accepted."""
    add_external_member(client, pid, handle, by=by)


def _remove(client, pid: str, handle: str, *, by: str = "alice"):
    return client.delete(
        f"/projects/{pid}/members/{handle}", headers=_bearer(seed_user(client, by))
    )


def _read_every_door(client, pid: str, who: str) -> dict[str, int]:
    """``who`` 逐个门收到的状态码。同一支请求、同样的参数，逐次真发。"""
    headers = _bearer(seed_user(client, who))
    return {
        what: client.get(path.format(pid=pid), headers=headers).status_code
        for what, path in DOORS.items()
    }


def _task_by(client, handle: str) -> int:
    """一道赛题，出题者是 ``handle`` —— ``Task.creator_id`` 是 int，所以他得是
    一个真的 User，而不是一个 handle。"""
    from datetime import UTC, datetime

    from app.domain.space.models import SpaceCategory
    from app.domain.task.models import Task
    from app.domain.user.repositories import UserRepository

    seed_user(client, handle)
    space_id = seed_space(client, name=f"信院-{handle}")
    holder: dict[str, int] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            author = await UserRepository(session).get_by_username(handle)
            assert author is not None
            category = SpaceCategory(
                space_id=space_id,
                name="创研课",
                description="",
                display_order=0,
                resource_pack={},
                conditions=[],
                default_role=None,
                created_at=now,
                updated_at=now,
            )
            session.add(category)
            await session.flush()
            task = Task(
                name="赛题",
                intro="",
                description="",
                creator_id=author.id,
                space_id=space_id,
                category_id=category.id,
                submitter_type=0,
                approved=1,
                default_deadline=0,
                # 没有访问控制的赛题对谁都可见（``can_view_task`` 的第一条分支），
                # 那正是这里要关掉的东西：这些断言说的是「谁看得见这道题」。
                access_control_enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(task)
            await session.flush()
            holder["id"] = task.id
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def _project_from_task(client, task_id: int, *, student: str) -> str:
    """报名者开的项目：他拥有它，出题者不在名册上。"""
    r = post_project(
        client,
        json={"name": "赛题项目", "owner_handle": student, "external_task_id": task_id},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["external_task_id"] == task_id
    return r.json()["data"]["id"]


# --- 1. 撤权立刻生效 ---------------------------------------------------------


def test_removing_a_member_shuts_every_door_at_once(client):
    pid, _ = _project(client)
    _add(client, pid, "bob")
    assert set(_read_every_door(client, pid, "bob").values()) == {200}, (
        "先证明他在门里 —— 否则下面的 403 什么都证明不了"
    )

    assert _remove(client, pid, "bob").status_code == 200
    assert set(_read_every_door(client, pid, "bob").values()) == {403}


def test_the_ai_summary_is_refused_the_moment_the_member_is_removed(client):
    """摘要不是另一条路，是同一扇门。

    同一个 URL、同样的参数，中间只多了一次名册写入。摘要读路径上任何形式的缓存
    （进程内 memo、``lru_cache``、算好的一份聚合）都会在这里留下撤权前的那一次
    200 —— 这个测试就是为它写的。
    """
    pid, _ = _project(client)
    _add(client, pid, "bob")
    who = _bearer(seed_user(client, "bob"))
    summary = f"/projects/{pid}/contributions"

    assert client.get(summary, headers=who).status_code == 200
    assert client.get(summary, headers=who).status_code == 200  # 读过两遍，算过了

    _remove(client, pid, "bob")
    refused = client.get(summary, headers=who)
    assert refused.status_code == 403
    assert "bob" not in refused.text, "拒绝的响应里不该夹带摘要内容"


def test_the_same_door_reopens_when_the_claim_comes_back(client):
    """撤权 → 加回 → 再读，三段都必须是当场判断，而不是一次被缓存的答案。"""
    pid, _ = _project(client)
    _add(client, pid, "bob")
    assert _read_every_door(client, pid, "bob")["项目本身"] == 200

    _remove(client, pid, "bob")
    assert _read_every_door(client, pid, "bob")["项目本身"] == 403

    _add(client, pid, "bob")
    assert set(_read_every_door(client, pid, "bob").values()) == {200}


def _set_team_membership(client, team_id: int, handle: str, *, joined: bool) -> None:
    """加入 / 退出小队 —— 成员关系按 int user id 存，所以 ``handle`` 得先落成一个
    真的 User。

    直接写库而不是走接口：小队成员那套路由属于「空间成员与邀请码」那条任务，
    这里要的只是「小队的门槛是活的」这一件事。
    """
    from datetime import UTC, datetime

    from sqlalchemy import delete

    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    seed_user(client, handle)

    async def _run() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            await session.execute(
                delete(TeamUserRelation).where(
                    TeamUserRelation.team_id == team_id,
                    TeamUserRelation.user_id == user.id,
                )
            )
            if joined:
                session.add(
                    TeamUserRelation(
                        team_id=team_id,
                        user_id=user.id,
                        role=TeamMemberRole.MEMBER,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await session.commit()

    asyncio.run(_run())


def test_a_removed_teammate_loses_the_team_claim_too(client):
    """小队成员靠 ``project.team_id`` 进来；把他移出小队，这道门也要关。

    名册不是唯一的入口 —— 「我在这个队里」是另一条，撤权必须两条都撤。项目建好
    之后再入队，是为了让他**只**有这一条凭据：建队时就带来的成员会被写进项目
    名册（``test_new_project_roster.py`` 里那些带 ``source`` 的行），那样测的是
    名册那条路，不是小队这条。
    """
    team_id = _team(client, owner="alice", members=())
    pid, _ = _team_project(client, owner="alice", team_id=team_id)
    who = _bearer(seed_user(client, "bob"))
    assert client.get(f"/projects/{pid}", headers=who).status_code == 403

    _set_team_membership(client, team_id, "bob", joined=True)
    assert client.get(f"/projects/{pid}", headers=who).status_code == 200

    _set_team_membership(client, team_id, "bob", joined=False)
    assert client.get(f"/projects/{pid}", headers=who).status_code == 403


# --- 2. 出题者有自己的门，且只有那道门 ---------------------------------------


def test_the_asker_reads_the_project_their_task_produced(client):
    """#945 要的就是这条：出题者读得到本题关联的项目。

    项目是学生开的、学生拥有的 —— 出题者不在名册上，也不在小队里。他进来的凭据
    是「这道题是我出的」。
    """
    task_id = _task_by(client, "teacher")
    pid = _project_from_task(client, task_id, student="student")
    teacher = _bearer(seed_user(client, "teacher"))

    assert client.get(f"/projects/{pid}", headers=teacher).status_code == 200
    assert (
        client.get("/topics", params={"project_id": pid}, headers=teacher).status_code
        == 200
    )
    assert (
        client.get(f"/projects/{pid}/contributions", headers=teacher).status_code == 200
    )


def test_an_asker_is_not_a_key_to_every_project(client):
    """出题者 ≠ 全站教师，也 ≠ 出过题的任何人。

    同一道题的项目他读得到；别人出的题的项目，他和陌生人一样被拒。
    """
    mine = _task_by(client, "teacher")
    theirs = _task_by(client, "other-teacher")
    mine_pid = _project_from_task(client, mine, student="student")
    theirs_pid = _project_from_task(client, theirs, student="student2")
    teacher = _bearer(seed_user(client, "teacher"))

    assert client.get(f"/projects/{mine_pid}", headers=teacher).status_code == 200
    assert client.get(f"/projects/{theirs_pid}", headers=teacher).status_code == 403


def test_being_a_participant_of_the_task_is_not_a_claim_on_the_project(client):
    """报名这道题的人，不是这道题**其他**项目的读者。

    把 ``can_view_task`` 整个搬过来当项目判断就会犯这个错：同题的两个队伍互相看得
    见对方的房间。所以项目级的凭据只认出题者一个人。
    """
    from datetime import UTC, datetime

    from app.domain.task.models import TaskMembership
    from app.domain.user.repositories import UserRepository

    task_id = _task_by(client, "teacher")
    pid = _project_from_task(client, task_id, student="student")
    seed_user(client, "rival")

    async def _join() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            rival = await UserRepository(session).get_by_username("rival")
            assert rival is not None
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=rival.id,
                    is_team=False,
                    approved=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_join())
    # 他确实看得见那道题……
    assert (
        client.get(
            f"/projects/by-task/{task_id}", headers=_bearer(seed_user(client, "rival"))
        ).status_code
        == 200
    )
    # ……但看不见别人队伍的项目。
    assert (
        client.get(
            f"/projects/{pid}", headers=_bearer(seed_user(client, "rival"))
        ).status_code
        == 403
    )


def test_the_task_page_directory_is_not_open_to_strangers(client):
    """``GET /projects/by-task/{id}`` 用一个小整数换回这道题每个项目的 id、名字
    与所有者 —— 而 id 就是其余所有门的钥匙。所以它问的是「你看得见这道题吗」。"""
    task_id = _task_by(client, "teacher")
    pid = _project_from_task(client, task_id, student="student")

    seen = client.get(
        f"/projects/by-task/{task_id}", headers=_bearer(seed_user(client, "teacher"))
    )
    assert seen.status_code == 200
    assert pid in [p["id"] for p in seen.json()["data"]["data"]]

    outsider = client.get(
        f"/projects/by-task/{task_id}", headers=_bearer(seed_user(client, "mallory"))
    )
    assert outsider.status_code == 403

    with off_the_street(client) as anon:
        assert anon.get(f"/projects/by-task/{task_id}").status_code == 401


def test_a_team_project_is_not_a_directory_by_path(client):
    """同一条查询 ``/projects?team_id=`` 一直被小队门守着，``/projects/by-team/``
    却谁都能问 —— 同一个项目、同一个可猜的 id，两条路两个答案。"""
    team_id = _team(client, owner="alice", members=("bob",))
    pid, _ = _team_project(client, owner="alice", team_id=team_id)

    seen = client.get(
        f"/projects/by-team/{team_id}", headers=_bearer(seed_user(client, "bob"))
    )
    assert seen.status_code == 200
    assert seen.json()["data"]["id"] == pid

    assert (
        client.get(
            f"/projects/by-team/{team_id}",
            headers=_bearer(seed_user(client, "mallory")),
        ).status_code
        == 403
    )
    with off_the_street(client) as anon:
        assert anon.get(f"/projects/by-team/{team_id}").status_code == 401


# --- 3. 撤权后别的门一起关 / 小队成员别的门一起开 ----------------------------


def test_the_project_files_close_with_the_same_key(client):
    """项目文件那条路曾经有自己的一份「成员或所有者」判断。撤权后它也必须关。"""
    pid, _ = _project(client)
    _add(client, pid, "bob")
    who = _bearer(seed_user(client, "bob"))
    assert client.get(f"/projects/{pid}/files", headers=who).status_code == 200

    _remove(client, pid, "bob")
    # 答 404 而不是 403：拒绝本身也不能确认这个项目 id 指向什么。
    assert client.get(f"/projects/{pid}/files", headers=who).status_code == 404


def test_a_teammate_reads_the_files_like_any_other_door(client):
    """2026-09-04 那个 bug 的另一半：队友在侧栏看得见项目，点进去文件却 403。

    文件那份拷贝漏了「项目所属小队」，合并成一个判断之后它和
    ``/projects/{id}`` 给同一个答案。
    """
    team_id = _team(client, owner="alice", members=("bob",))
    pid, root = _team_project(client, owner="alice", team_id=team_id)
    who = _bearer(seed_user(client, "bob"))

    assert client.get(f"/projects/{pid}", headers=who).status_code == 200
    assert client.get(f"/projects/{pid}/files", headers=who).status_code == 200


# --- 4. AI 摘要：同一个判断，没有旁路 ----------------------------------------


def _conversation_of(client, *, task_id: int, owner: str) -> str:
    """给 ``task_id`` 真建一条 AI 对话，返回它的 id。

    单条对话那条路要读到东西才谈得上「谁读得到」：从前它只按 id 取，连它属于哪
    道题都不看，所以 id 本身就是通行证。
    """
    from datetime import UTC, datetime

    from app.domain.llm.models import AIConversation
    from app.domain.user.repositories import UserRepository

    seed_user(client, owner)
    holder: dict[str, str] = {}
    cid = f"conv-for-{task_id}"

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            author = await UserRepository(session).get_by_username(owner)
            assert author is not None
            session.add(
                AIConversation(
                    owner_id=author.id,
                    context_id=task_id,
                    conversation_id=cid,
                    title="AI 摘要",
                    model_type="standard",
                    module_type="task_ai_advice",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        holder["cid"] = cid

    asyncio.run(_seed())
    return holder["cid"]


def _ai_advice_paths(task_id: int, conversation_id: str) -> dict[str, str]:
    return {
        "摘要列表": f"/tasks/{task_id}/ai-advice",
        "生成状态": f"/tasks/{task_id}/ai-advice/status",
        "对话分组": f"/tasks/{task_id}/ai-advice/conversations/grouped",
        "单条对话": f"/tasks/{task_id}/ai-advice/conversations/{conversation_id}",
    }


def test_the_ai_summary_of_a_task_is_readable_only_by_those_who_see_the_task(client):
    """摘要的读路径不能比任务本身更宽。

    从前这些路只问「登录了吗」：任何一个注册用户拿着任何一道题的 id，就能读它的
    建议记录与 AI 对话。现在它们问的是 ``TaskVisibilityService`` —— 和任务页同一
    个判断。答 404 而不是 403，是为了不确认这个 id 指向什么。
    """
    task_id = _task_by(client, "teacher")
    teacher = _bearer(seed_user(client, "teacher"))
    stranger = _bearer(seed_user(client, "mallory"))
    cid = _conversation_of(client, task_id=task_id, owner="teacher")

    for what, path in _ai_advice_paths(task_id, cid).items():
        assert client.get(path, headers=teacher).status_code == 200, what
        assert client.get(path, headers=stranger).status_code == 404, what


def test_a_conversation_id_is_not_a_pass_by_itself(client):
    """知道 id 不等于有权限：别的题的对话 id 放在这道题的地址里也读不到。

    读出路径过去只按 id 找（``get_by_conversation_id``），连它属于哪道题都不看。
    id 是随机的、不好猜，但「猜不到」不是一道授权判断 —— 问的那条路
    （``ask``/``stream``）一直要求 ``convo.context_id == task_id``，读的那条现在
    也一样。
    """
    mine = _task_by(client, "teacher")
    theirs = _task_by(client, "someone-else")
    teacher = _bearer(seed_user(client, "teacher"))
    other_cid = _conversation_of(client, task_id=theirs, owner="someone-else")

    # 出题者自己那条路开着，别人那道题的 id 放进来必须关。
    assert (
        client.get(
            f"/tasks/{mine}/ai-advice/conversations/{other_cid}", headers=teacher
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/tasks/{mine}/ai-advice/conversations/{other_cid}", headers=teacher
        ).status_code
        == 404
    )


def test_the_ai_summary_write_paths_take_the_same_door(client):
    """问一句、删一条，也都是对某道题的摘要在动手 —— 同样没有旁路。"""
    task_id = _task_by(client, "teacher")
    stranger = _bearer(seed_user(client, "mallory"))

    asked = client.post(
        f"/tasks/{task_id}/ai-advice/conversations",
        json={"question": "这道题怎么做？"},
        headers=stranger,
    )
    assert asked.status_code == 404
    deleted = client.delete(
        f"/tasks/{task_id}/ai-advice/conversations/whatever", headers=stranger
    )
    assert deleted.status_code == 404
