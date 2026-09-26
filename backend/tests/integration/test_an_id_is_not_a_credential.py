"""知道一个 id 不等于持有凭据：这四条路由过去只要 id 就办事。

一次在飞分支之外的审计扫出这一族。调用者是 ``off_the_street`` 造出来的 ——
同一套 integration fixture 的 ``client``，把 ``X-Cheese-Token`` 摘掉，于是真的是
「什么凭据都没有」。改动前，这些请求各自拿到了 200：

    DELETE /milestones/{id}        硬删任意项目的里程碑（模型没有软删字段，
                                   repository 的 delete 就是 session.delete）
    POST  /blocks/{id}/reactions   以任意 handle 给任意一条消息加/删表情
    POST  /blocks/{id}/upgrade     在别人的房间里落一张卡，created_by 自己填谁是谁
    GET   /spaces/{id}/dashboard   这个 Space 下每个队伍的项目卡（space id 是小整数）

同一口气里的对照是 ``GET /projects/{id}/usage``，它一直要求项目成员：同样摘了头，
它回 401 —— 所以这些 200 不是「摘法把整条请求弄坏了」，是门真的不在。

口径（与 `test_project_reads_need_membership.py` 同一条）：要补的是「没有凭据也能
进来」，不是「谁能冒名」。沙箱 token 是这个部署里可信的开发凭据，``ActorResolver``
放行它（`app.api.auth`），所以下面有两半：摘掉头就进不来，而 `test_reactions.py` /
`test_milestones.py` / `test_project_tree.py` / `test_dashboard.py` 那些带着它的用例
照旧是绿的。``body.author`` / ``body.created_by`` 仍然是请求体说了算 —— 那件事是
另一个产品口径，这次不动。

``upgrade`` 比另外三条多一层：它在**别人的房间**里造东西。所以它除了凭据，还要在
``block.topic_id`` 那个房间里站得住 —— 这一层由最后的
``test_a_block_id_alone_does_not_dispatch_a_card`` 之外的正反两半一起钉住。
"""

import asyncio
import uuid

from app.domain.block.models import AuthorType, Block, BlockKind
from tests.conftest import (
    seed_space,
    seed_task_with_protocol,
    seed_user,
    wait_work_idle,
)
from tests.integration.conftest import post_project, session_auth_headers
from tests.integration.test_project_reads_need_membership import off_the_street


def _project(client, owner: str = "alice") -> dict:
    return post_project(
        client, json={"name": "P", "owner_handle": owner}
    ).json()["data"]


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
                topic_id=uuid.UUID(topic_id),
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
    project = post_project(
        client,
        json={"name": "队伍A", "external_task_id": task_id, "owner_handle": owner},
    ).json()["data"]
    return space_id, project


def test_a_milestone_id_alone_does_not_delete_it(client):
    p = _project(client)
    milestone = client.post(
        f"/projects/{p['id']}/milestones", json={"title": "中期检查"}
    ).json()["data"]

    with off_the_street(client) as anon:
        assert anon.delete(f"/milestones/{milestone['id']}").status_code == 401

    # 门关上了，里程碑还在 —— 这条才是这半句的重点。
    left = client.get(f"/projects/{p['id']}/milestones").json()["data"]
    assert [m["id"] for m in left["data"]] == [milestone["id"]], left


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
    assert client.get(f"/topics/{p['root_topic_id']}/tasks").json()["data"][
        "total"
    ] == 0
    # 房间时间线上也没有落下「一条消息已转为任务」那条事件。
    room_msgs = client.get(f"/topics/{p['root_topic_id']}/blocks").json()["data"]["data"]
    assert not [
        b for b in room_msgs if (b.get("meta") or {}).get("detail", "").startswith("活 ")
    ], room_msgs


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
