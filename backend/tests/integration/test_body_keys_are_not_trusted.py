"""请求体里的外键：能填，不等于能指。

三条 create 路由各有一支**请求体**给出的外键，落库前谁都没问过一句：

* ``POST /projects`` 的 ``team_id`` —— 项目会出现在那个团队的 ``GET
  /projects?team_id=`` 列表里，挂着那个团队的名字，而调用方可能连凭据都没有。
* ``POST /projects`` 的 ``external_task_id`` —— 那份 赛题 的 资源包 也照发，哪怕
  调用方根本没报名（判据写成了「有报名但没过审」才拦）。
* ``POST /topics`` 的 ``parent_id`` —— 在别人的项目里建房间时把父指到别人的根
  房间，那片树从此多出一个他管不着的房间。

每条修复都配一条正向用例，钉住合法路径还在：团队成员在自己的团队里建项目、过审的
报名者拿到资源包、房间照常挂在自己项目的根房间下。
"""

import asyncio
import uuid
from datetime import UTC, datetime

from tests.conftest import seed_task_with_protocol, seed_user
from tests.integration.conftest import post_project, session_auth_headers

OWNER = "alice"
OUTSIDER = "mallory"


def _now() -> datetime:
    return datetime.now(UTC)


def _team(client, owner: str) -> int:
    """A shared team owned by ``owner`` (registered first), as the DB holds it.

    The team is seeded directly because ``POST /teams`` also needs a token and a
    filled form, and none of that is what these tests are about.
    """
    from tests.integration.conftest import a_team

    async def _seed() -> int:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            team_id = await a_team(session, owner)
            await session.commit()
            return team_id

    return asyncio.run(_seed())


def _project(client, name: str, owner: str, **extra) -> dict:
    r = post_project(
        client,
        json={"name": name, "owner_handle": owner, **extra},
        headers=session_auth_headers(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _team_listing(client, team_id: int, viewer: str) -> list[str]:
    r = client.get(f"/projects?team_id={team_id}", headers=session_auth_headers(viewer))
    assert r.status_code == 200, r.text
    return [p["name"] for p in r.json()["data"]["data"]]


def _grants(client, project_id: str) -> list[tuple[int | None, float]]:
    """``(source_task_id, credits_total)`` of every grant on this project."""
    from app.domain.usage.repositories import ComputeGrantRepository

    async def _read() -> list[tuple[int | None, float]]:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            rows = await ComputeGrantRepository(session).list_for_project(
                uuid.UUID(project_id)
            )
            return [(g.source_task_id, g.credits_total) for g in rows]

    return asyncio.run(_read())


def _membership(client, *, task_id: int, handle: str, approved: int) -> None:
    """A 报名 for ``handle`` on this 赛题, in the state ``approved`` names
    (``ApproveType.APPROVED == 0``, pending is 2)."""
    from app.domain.task.models import TaskMembership
    from app.domain.user.repositories import UserRepository

    async def _seed() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=user.id,
                    is_team=False,
                    approved=approved,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            await session.commit()

    asyncio.run(_seed())


# --- F2: `POST /projects` 的 team_id ----------------------------------------


def test_an_outsider_cannot_plant_a_project_in_someone_elses_team(client):
    team_id = _team(client, OWNER)
    seed_user(client, OUTSIDER)

    planted = client.post(
        "/projects",
        json={"name": "mallory-was-here", "owner_handle": OUTSIDER, "team_id": team_id},
        headers=session_auth_headers(OUTSIDER),
    )
    assert planted.status_code == 403, planted.text

    # 一个凭据都没有的调用方更不在任何团队里。
    anonymous = client.post(
        "/projects",
        json={"name": "anon-was-here", "owner_handle": OUTSIDER, "team_id": team_id},
    )
    assert anonymous.status_code == 401, anonymous.text

    assert _team_listing(client, team_id, OWNER) == []


def test_a_member_can_still_create_a_project_in_their_team(client):
    team_id = _team(client, OWNER)

    made = _project(client, "alice-project", OWNER, team_id=team_id)

    assert made["team_id"] == team_id
    assert _team_listing(client, team_id, OWNER) == ["alice-project"]


def test_a_personal_project_still_needs_no_team_named(client):
    """个人项目走的是 ``team_id=None`` → 所有者自己的团队，这条路没被上面那道门碰到。"""
    seed_user(client, OWNER)

    made = _project(client, "alice-personal", OWNER)

    assert made["team_id"] is not None
    assert _team_listing(client, made["team_id"], OWNER) == ["alice-personal"]


# --- F3: `POST /projects` 的 external_task_id -------------------------------


def test_a_stranger_gets_no_resource_pack_from_a_task(client):
    """没报名的人建项目：项目是他的，赛题的资源包不是。"""
    task_id = seed_task_with_protocol(client, resource_pack={"compute_credits": 5000})
    seed_user(client, OUTSIDER)

    for name in ("freeloader-1", "freeloader-2"):
        made = _project(client, name, OUTSIDER, external_task_id=task_id)
        assert _grants(client, made["id"]) == []


def test_a_pending_application_gets_no_pack_either(client):
    """报名了但没过审：工作区可以先建起来，资源包要等审批。"""
    task_id = seed_task_with_protocol(client, resource_pack={"compute_credits": 5000})
    seed_user(client, OUTSIDER)
    _membership(client, task_id=task_id, handle=OUTSIDER, approved=2)  # NONE

    made = _project(client, "pending", OUTSIDER, external_task_id=task_id)

    assert _grants(client, made["id"]) == []


def test_an_approved_applicant_gets_the_pack(client):
    """过审的报名者照领——这正是修复要保住的那条正路。"""
    task_id = seed_task_with_protocol(client, resource_pack={"compute_credits": 5000})
    seed_user(client, OUTSIDER)
    _membership(client, task_id=task_id, handle=OUTSIDER, approved=0)  # APPROVED

    made = _project(client, "approved", OUTSIDER, external_task_id=task_id)

    assert _grants(client, made["id"]) == [(task_id, 5000.0)]


def test_a_team_gets_the_pack_when_its_own_registration_is_approved(client):
    """团队报名（``submitter_type == 1``）问的是团队那行 membership。"""
    from app.domain.task.models import Task, TaskMembership

    team_id = _team(client, OUTSIDER)
    task_id = seed_task_with_protocol(client, resource_pack={"compute_credits": 5000})

    async def _approve_team() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            task = await session.get(Task, task_id)
            assert task is not None
            # 团队报名的赛题：``member_id`` 于是是 team id，不是人的 id。
            task.submitter_type = 1
            session.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=team_id,
                    is_team=True,
                    approved=0,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
            await session.commit()

    asyncio.run(_approve_team())

    made = _project(
        client, "team-project", OUTSIDER, team_id=team_id, external_task_id=task_id
    )

    assert _grants(client, made["id"]) == [(task_id, 5000.0)]


# --- F4: `POST /topics` 的 parent_id ----------------------------------------


def test_a_room_cannot_be_parented_into_another_project(client):
    alice = _project(client, "alice-project", OWNER)
    mallory = _project(client, "mallory-project", OUTSIDER)

    r = client.post(
        "/topics",
        json={
            "project_id": mallory["id"],
            "title": "mallory 挂到 alice 项目树下的房间",
            "parent_id": alice["root_topic_id"],
        },
        headers=session_auth_headers(OUTSIDER),
    )
    assert r.status_code == 404, r.text

    children = client.get(
        f"/topics/{alice['root_topic_id']}/children",
        headers=session_auth_headers(OWNER),
    ).json()["data"]["data"]
    assert children == []


def test_a_room_still_hangs_under_its_own_projects_root(client):
    alice = _project(client, "alice-project", OWNER)

    r = client.post(
        "/topics",
        json={
            "project_id": alice["id"],
            "title": "提问",
            "parent_id": alice["root_topic_id"],
        },
        headers=session_auth_headers(OWNER),
    )
    assert r.status_code == 200, r.text
    made = r.json()["data"]["id"]

    children = client.get(
        f"/topics/{alice['root_topic_id']}/children",
        headers=session_auth_headers(OWNER),
    ).json()["data"]["data"]
    assert [t["id"] for t in children] == [made]
