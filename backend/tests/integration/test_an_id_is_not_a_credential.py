"""知道一个 id 不等于持有凭据：这三条路由过去只要 id 就办事。

一次在飞分支之外的审计扫出这一族。调用者是 ``off_the_street`` 造出来的 ——
同一套 integration fixture 的 ``client``，把 ``X-Cheese-Token`` 摘掉，于是真的是
「什么凭据都没有」。改动前，这些请求各自拿到了 200：

    POST  /blocks/{id}/reactions   以任意 handle 给任意一条消息加/删表情
    POST  /blocks/{id}/upgrade     在别人的房间里落一张卡，created_by 自己填谁是谁
    GET   /spaces/{id}/dashboard   这个 Space 下每个队伍的项目卡（space id 是小整数）

同一口气里的对照是 ``GET /projects/{id}/usage``，它一直要求项目成员：同样摘了头，
它回 401 —— 所以这些 200 不是「摘法把整条请求弄坏了」，是门真的不在。

口径（与 `test_project_reads_need_membership.py` 同一条）：要补的是「没有凭据也能
进来」。沙箱 token 是这个部署里可信的开发凭据，``ActorResolver`` 放行它
（`app.api.auth`），所以下面有两半：摘掉头就进不来，而 `test_project_tree.py` /
`test_dashboard.py` 那些带着它的用例照旧是绿的。

「谁能冒名」是后来补上的另一半：表情和升级过去把请求体里的 ``author`` /
``created_by`` 当成是谁在做这件事，于是一个登录的成员能以别人的名义点表情、能把
升级出来的卡记到别人名下。现在这两栏不在请求体里了，人由凭据说 —— 文件末尾的几条
钉住这一半。

``upgrade`` 比另外两条多一层：它在**别人的房间**里造东西。所以它除了凭据，还要在
``block.topic_id`` 那个房间里站得住 —— 这一层由最后的
``test_a_block_id_alone_does_not_dispatch_a_card`` 之外的正反两半一起钉住。
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.conftest import (
    seed_claim,
    seed_space,
    seed_task_with_protocol,
    seed_user,
    wait_work_idle,
)
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)
from tests.integration.test_project_reads_need_membership import off_the_street


def _project(client, owner: str = "alice") -> dict:
    return post_project(client, json={"name": "P"}, owner=owner).json()["data"]


def _insert_block(client, project_id: str, topic_id: str) -> str:
    """A plain message in ``topic_id``, as the room's own people write them.

    Straight to the row (the seeding style `test_project_tree.py` uses) because
    what is under test is the HTTP door on the block id, not how the block got
    there.
    """
    holder: dict[str, str] = {}

    async def _seed() -> None:
        async with client.test_factory() as session:
            block = Block(
                project_id=uuid.UUID(project_id),
                conversation_id=uuid.UUID(topic_id),
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="alice",
                content="我们要不要单独做一个数据清洗的模块",
                refs=[],
            )
            session.add(block)
            await session.flush()
            holder["id"] = str(block.id)
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def _board(client, owner: str = "alice") -> tuple[int, dict]:
    """A Space with one 赛题 and one project opened from it — the board's one row."""
    space_id = seed_space(client, "明理书院")
    task_id = seed_task_with_protocol(client, space_id=space_id)
    seed_claim(client, task_id, handle=owner)
    project = post_project(
        client,
        json={"name": "队伍A", "external_task_id": task_id},
        owner=owner,
    ).json()["data"]
    return space_id, project


def test_a_block_id_alone_does_not_add_a_reaction(client):
    p = _project(client)
    block = _insert_block(client, p["id"], p["root_topic_id"])

    with off_the_street(client) as anon:
        r = anon.post(
            f"/blocks/{block}/reactions", json={"emoji": "👍", "author": "someone-else"}
        )
        assert r.status_code == 401, r.text

    blocks = client.get(f"/topics/{p['root_topic_id']}/blocks").json()["data"]["data"]
    assert next(b for b in blocks if b["id"] == block)["reactions"] == []


def test_a_block_id_alone_does_not_dispatch_a_card(client):
    p = _project(client)
    block = _insert_block(client, p["id"], p["root_topic_id"])

    with off_the_street(client) as anon:
        # 请求体把负责人填成谁都不重要：凭据都没有，谁也不该给出这张卡。
        r = anon.post(
            f"/blocks/{block}/upgrade",
            json={"created_by": "someone-else", "reviewer_handle": "someone-else"},
        )
        assert r.status_code == 401, r.text

    wait_work_idle()
    room = f"/topics/{p['root_topic_id']}"
    assert client.get(f"{room}/tasks").json()["data"]["total"] == 0
    # 房间时间线上也没有落下「一条消息已转为任务」那条事件。
    said = [
        b.get("content") or ""
        for b in client.get(f"{room}/blocks").json()["data"]["data"]
    ]
    assert not [line for line in said if "转为任务" in line], said


def test_a_space_id_alone_does_not_list_the_board(client):
    space_id, project = _board(client)

    with off_the_street(client) as anon:
        # 对照组：隔壁那条项目读接口在同一张请求下回 401，证明摘法造出的是真
        # 匿名调用者，而不是「摘错了头所以整条请求坏了」。
        assert anon.get(f"/projects/{project['id']}/usage").status_code == 401
        assert anon.get(f"/spaces/{space_id}/dashboard").status_code == 401


def test_the_board_opens_for_the_projects_it_lists_and_nobody_else(client):
    """加门不等于关掉这项能力：列在这块板上的项目里的人照旧读得到，
    只是「知道 space id」不再是那张票。"""
    space_id, _ = _board(client)

    r = client.get(
        f"/spaces/{space_id}/dashboard", headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    assert [t["name"] for t in r.json()["data"]["teams"]] == ["队伍A"]

    seed_user(client, "mallory")
    r = client.get(
        f"/spaces/{space_id}/dashboard", headers=session_auth_headers("mallory")
    )
    assert r.status_code == 403, r.text


# --- 请求体里写的名字不是凭据 ------------------------------------------------------
#
# 下面的调用者都是真登录的成员（会话 token），请求体里却写着另一个成员的名字。
# 改动前请求体赢：表情记到 bob 头上，升级出来的卡归 bob。


def _team(client) -> dict:
    """``owner`` 的项目，alice 和 bob 都在它的团队里 —— 两个都够得着根房间。"""
    p = _project(client, owner="owner")
    for member in ("alice", "bob"):
        join_project_team(client, p["id"], member)
    return p


def test_a_member_who_names_someone_else_upgrades_a_card_as_themselves(client):
    p = _team(client)
    block = _insert_block(client, p["id"], p["root_topic_id"])

    r = client.post(
        f"/blocks/{block}/upgrade",
        json={"created_by": "bob", "reviewer_handle": "owner"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    room = f"/topics/{p['root_topic_id']}"
    cards = client.get(f"{room}/tasks").json()["data"]["data"]
    assert [(c["id"], c["owner_handle"]) for c in cards] == [
        (r.json()["data"]["id"], "alice")
    ]


def test_a_member_who_names_someone_else_upgrades_a_room_as_themselves(client):
    """私聊里的一条升级出来的是一个房间：房间的主人同样是升级的那个人。"""
    p = _team(client)
    dm = client.get(
        f"/projects/{p['id']}/private-chat",
        params={"user_handle": "alice"},
        headers=session_auth_headers("alice"),
    )
    assert dm.status_code == 200, dm.text
    block = _insert_block(client, p["id"], dm.json()["data"]["id"])

    r = client.post(
        f"/blocks/{block}/upgrade",
        json={"created_by": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    wait_work_idle()

    roster = client.get(f"/topics/{r.json()['data']['id']}/members").json()["data"]
    owners = [m["member_handle"] for m in roster["data"] if m["role"] == "owner"]
    assert owners == ["alice"]


def test_a_member_who_names_someone_else_reacts_as_themselves(client):
    p = _team(client)
    block = _insert_block(client, p["id"], p["root_topic_id"])

    r = client.post(
        f"/blocks/{block}/reactions",
        json={"emoji": "👍", "author": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["reactions"] == [
        {"emoji": "👍", "count": 1, "authors": ["alice"]}
    ]


def test_the_dev_credential_alone_names_nobody_to_react_as(client):
    """沙箱 token 开得了门，但它不是任何人：没有人可以把表情记在他名下。"""
    p = _team(client)
    block = _insert_block(client, p["id"], p["root_topic_id"])

    r = client.post(f"/blocks/{block}/reactions", json={"emoji": "👍"})
    assert r.status_code == 401, r.text
    blocks = client.get(f"/topics/{p['root_topic_id']}/blocks").json()["data"]["data"]
    assert next(b for b in blocks if b["id"] == block)["reactions"] == []
