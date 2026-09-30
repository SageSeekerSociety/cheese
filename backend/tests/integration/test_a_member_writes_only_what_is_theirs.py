"""普通成员能写什么：请求里点名的东西，得真是他的。

第 2 批把「谁能发题」从管理员放宽成任意成员之后，同一族缺陷的下一层是**请求里的
引用没有被核对**。两处都在 ``app.api.routes.tasks`` 的写路由上，各自把「调用者手上的
一个数字」当成了「他有权处置的那个对象」：

1. ``POST /tasks/{taskId}/participants``：``member`` 不传时它默认成调用者**自己的
   user id**，TEAM 题上却把这个 user id 当成**队伍 id** 写进报名 —— 全程不问
   「这支队存不存在、你在不在里面」。同一条能力在 ``/participations/team`` 上问的
   正是这句话。
2. ``POST /tasks`` 与 ``PATCH /tasks/{taskId}`` 的 ``accessDomainGroupIds``：只按
   ``group_id`` 取域，不问这个组属于哪块板 —— 于是别的板管理员圈定的域名名单能被
   原样搬到自己这道题上，而那份名单正是**读权限**的判据（``TaskVisibilityService``
   把 ``TaskAccessDomain.domain`` 并进可见性的 or 列表）。同一个请求体里的
   ``categoryId`` 一直是按板收口的，这里补上同一条。

两条都按浏览器会收到的状态码来断，不读实现。发现 1 需要那个「两支队伍的 id 与
user id 撞上」的数值巧合 —— 平台没有约束阻止它发生，本文件把巧合造出来，好让
「把 user id 当 team id 用」这个缺陷现形。
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal
    from sqlalchemy.ext.asyncio import AsyncSession


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _new_board(user_client: UserCreator, api_client: TestClient) -> dict:
    """建版的人（= 这块板的 OWNER）与一块已过审的题目板。"""
    creator = user_client.create_user()
    creator_token = _login(user_client, api_client, creator)
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Member Writes ({suffix})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(creator_token),
    )
    assert resp.status_code == 201, resp.text
    space = resp.json()["data"]["space"]
    return {
        "creator": creator,
        "creator_token": creator_token,
        "space_id": space["id"],
        "category_id": space.get("defaultCategoryId"),
    }


def _a_member_of(
    user_client: UserCreator, api_client: TestClient, board: dict
) -> tuple[object, str]:
    """板里的一位普通成员：既不是出题人，也不是管理员。"""
    member = user_client.create_user()
    token = _login(user_client, api_client, member)
    added = api_client.post(
        f"/spaces/{board['space_id']}/members",
        json={"userId": member.user_id},
        headers=_auth(board["creator_token"]),
    )
    assert added.status_code == 201, added.text
    return member, token


def _publish(
    api_client: TestClient,
    board: dict,
    *,
    token: str,
    name: str,
    space_id: int | None = None,
    category_id: int | None = None,
    submitter_type: str = "USER",
    access_domain_group_ids: list[int] | None = None,
):
    body: dict = {
        "name": name,
        "intro": "题",
        "description": '{"type":"doc","content":[]}',
        "space": space_id if space_id is not None else board["space_id"],
        "categoryId": category_id if category_id is not None else board["category_id"],
        "submitterType": submitter_type,
        "resubmittable": True,
        "editable": True,
        "defaultDeadline": 30,
        "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
    }
    if access_domain_group_ids is not None:
        body["accessControlEnabled"] = True
        body["accessDomainGroupIds"] = access_domain_group_ids
    return api_client.post("/tasks", json=body, headers=_auth(token))


def _a_team_task(api_client: TestClient, board: dict, *, name: str) -> int:
    """一块板上已过审的 TEAM 题。"""
    published = _publish(
        api_client,
        board,
        token=board["creator_token"],
        name=name,
        submitter_type="TEAM",
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _plant_a_team_whose_id_is(
    db_session: AsyncSession,
    portal: BlockingPortal,
    *,
    team_id: int,
    owner_user_id: int,
) -> bool:
    """在库里放一支 id 恰好等于 ``team_id`` 的队，主人是别人（``owner_user_id``）。

    这就是审计里那个**数值巧合**：user id 与 team id 各自自增，平台没有约束阻止
    它们撞上。造出它，「把调用者的 user id 当队伍 id 用」这个缺陷才现形。

    返回 False 表示这个 id 已经被别的队占了（换个新注册的人再来一次）。
    """
    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation

    async def _insert() -> bool:
        if await db_session.get(Team, team_id) is not None:
            return False
        now = datetime.now(UTC)
        db_session.add(
            Team(
                id=team_id,
                name=f"Phantom Team {team_id}",
                handle=f"ph-{uuid.uuid4().hex[:12]}",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()
        db_session.add(
            TeamUserRelation(
                team_id=team_id,
                user_id=owner_user_id,
                role=TeamMemberRole.OWNER,
                created_at=now,
                updated_at=now,
            )
        )
        await db_session.flush()
        return True

    return portal.call(_insert)


# --- 1. 报名这条路由把 user id 当成了 team id --------------------------------


def test_a_team_id_that_is_just_someones_user_id_is_not_a_registration(
    api_client: TestClient,
    user_client: UserCreator,
    db_session: AsyncSession,
    _portal: BlockingPortal,
):
    """TEAM 题上没带 ``member`` 的报名，拿的是调用者自己的 user id —— 那不是一个
    队伍，他也没资格把它报上去。

    前提（审计里那个巧合）：库里有一支 id 恰好等于他 user id 的队，且他不在里面。
    修复前在这条路由上这是 200，还会建出一个 ``team_id == 他的 user id`` 的项目；
    同一个人走 /participations/team 上的是同一句话的那扇门，那里一直是 403。
    """
    board = _new_board(user_client, api_client)
    task_id = _a_team_task(api_client, board, name="队伍的题")
    real_owner = user_client.create_user()

    for _ in range(20):
        joiner, joiner_token = _a_member_of(user_client, api_client, board)
        planted = _plant_a_team_whose_id_is(
            db_session,
            _portal,
            team_id=joiner.user_id,
            owner_user_id=real_owner.user_id,
        )
        if planted:
            break
    else:
        pytest.fail("20 个新 user id 都被已有的队占着，造不出这个巧合")

    resp = api_client.post(
        f"/tasks/{task_id}/participants",
        json={},
        headers=_auth(joiner_token),
    )
    assert resp.status_code == 403, resp.text


def test_a_team_the_caller_is_in_still_registers(
    api_client: TestClient, user_client: UserCreator
):
    """守这条路的守卫不是「一律 403」：出题人报上一支他自己在的队，照旧 200。

    （这条同时钉住「报名的是队伍」这件事本身：项目挂在队伍名下。）"""
    board = _new_board(user_client, api_client)
    task_id = _a_team_task(api_client, board, name="队伍的题")

    team_resp = api_client.post(
        "/teams",
        json={
            "name": f"Real Team {unique_int(10000000, 99999999)}",
            "intro": "队",
            "description": "一支真队",
            "avatarId": 1,
        },
        headers=_auth(board["creator_token"]),
    )
    assert team_resp.status_code in (200, 201), team_resp.text
    team_id = team_resp.json()["data"]["team"]["id"]

    resp = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": team_id},
        json={},
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["project"]["team_id"] == team_id


# --- 2. 域组得是这块板的 -------------------------------------------------------


def _a_domain_group(api_client: TestClient, board: dict, *, domains: list[str]) -> int:
    """板管理员圈一个域组（名单里的人因此读得到挂了这个组的题）。"""
    resp = api_client.post(
        f"/spaces/{board['space_id']}/domain-groups",
        json={
            "name": f"Domain Group ({unique_int(10000000, 99999999)})",
            "description": "一组域",
            "domains": domains,
        },
        headers=_auth(board["creator_token"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["group"]["id"]


def test_a_task_cannot_be_published_with_another_boards_domain_group(
    api_client: TestClient, user_client: UserCreator
):
    """板 B 的成员发题时带上板 A 的域组 id：那组不是这块板的，跟外板的
    ``categoryId`` 一样答 404（同一个请求体里相邻那一格一直就是这么收的）。"""
    board_a = _new_board(user_client, api_client)
    foreign_group = _a_domain_group(
        api_client, board_a, domains=[f"leak-{unique_int()}.example.edu"]
    )
    board_b = _new_board(user_client, api_client)
    _, member_token = _a_member_of(user_client, api_client, board_b)

    # 对照：外板的 categoryId 一直是 404 —— 同一句话。
    foreign_category = _publish(
        api_client,
        board_b,
        token=member_token,
        name="带外板分类的题",
        category_id=board_a["category_id"],
    )
    assert foreign_category.status_code == 404, foreign_category.text

    resp = _publish(
        api_client,
        board_b,
        token=member_token,
        name="带外板域组的题",
        access_domain_group_ids=[foreign_group],
    )
    assert resp.status_code == 404, resp.text


def test_a_task_cannot_be_repointed_at_another_boards_domain_group(
    api_client: TestClient, user_client: UserCreator
):
    """改题那条路同一个形状：成员自己发的题，PATCH 上板 A 的域组 id 也是 404。"""
    board_a = _new_board(user_client, api_client)
    foreign_group = _a_domain_group(
        api_client, board_a, domains=[f"leak-{unique_int()}.example.edu"]
    )
    board_b = _new_board(user_client, api_client)
    _, member_token = _a_member_of(user_client, api_client, board_b)

    published = _publish(api_client, board_b, token=member_token, name="成员发的题")
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]

    resp = api_client.patch(
        f"/tasks/{task_id}",
        json={
            "accessControlEnabled": True,
            "accessDomainGroupIds": [foreign_group],
        },
        headers=_auth(member_token),
    )
    assert resp.status_code == 404, resp.text


def test_a_task_may_use_its_own_boards_domain_group(
    api_client: TestClient, user_client: UserCreator
):
    """本板的域组照旧可用，而且它真的是读权限：名单里的人读得到这道题。

    这一条钉住「收口不是全部拒绝」，也钉住这个字段的产品含义 —— 否则上一条断言
    404 只能证明有人把门焊死了。
    """
    board = _new_board(user_client, api_client)
    domain = f"own-{unique_int()}.example.edu"
    group_id = _a_domain_group(api_client, board, domains=[domain])

    suffix = unique_int(10000000, 99999999)
    published = _publish(
        api_client,
        board,
        token=board["creator_token"],
        name=f"本板域组的题 {suffix}",
        access_domain_group_ids=[group_id],
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]
    assert published.json()["data"]["task"]["accessDomainGroupIds"] == [group_id]

    approved = api_client.patch(
        f"/tasks/{task_id}",
        json={"approved": "APPROVED"},
        headers=_auth(board["creator_token"]),
    )
    assert approved.status_code == 200, approved.text

    # 名单里的人：不是这块板的成员，只是邮箱域名在那份名单上。
    reader = user_client.create_user(email=f"reader-{suffix}@{domain}")
    reader_token = _login(user_client, api_client, reader)
    assert (
        api_client.get(f"/tasks/{task_id}", headers=_auth(reader_token)).status_code
        == 200
    )

    # 名单外的人读不到 —— 否则上面那个 200 什么都证明不了。
    outsider = user_client.create_user()
    outsider_token = _login(user_client, api_client, outsider)
    assert (
        api_client.get(f"/tasks/{task_id}", headers=_auth(outsider_token)).status_code
        == 404
    )
