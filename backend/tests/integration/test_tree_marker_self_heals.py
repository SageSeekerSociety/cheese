"""磁盘上那条「这个房间写哪棵树」的记号，不许指着一条交付完的分支。

The workspace layer is sync and DB-free, so which tree a room writes to reaches
it as a file (`ws.bind_tree` / `ws.tree_for_place`) — a cache of a database fact,
written by whoever last opened a batch. Every time the two have drifted, the
symptom has been silent and expensive:

- the room's marker still named the batch that had just MERGED, so every commit
  after the delivery landed on a branch already squashed into main — reachable
  from nothing, and no step of the delivery path notices;
- the marker is written outside any transaction, so a rollback after the row it
  names was created leaves it naming a row that does not exist at all.

Both have the same shape, and the database knows the answer, so reading a place
repairs it. These go through a real route, because that is what every path that
goes on to touch files does first.
"""

import uuid

from app.domain.room_task.services import WorkTreeService
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    client.headers.update(session_auth_headers("alice"))
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _room_with_a_batch(client) -> tuple[str, str]:
    pid = _project(client)
    r = client.post(
        "/topics", json={"project_id": pid, "title": "房间", "created_by": "alice"}
    )
    room = r.json()["data"]["id"]
    # 派活是房间开出一批活的那道门。
    assert client.post(f"/topics/{room}/split", json={"title": "活"}).status_code == 200
    return pid, room


def _marker_branch(room: str) -> str:
    from app.domain.workspace import service as ws

    return ws.branch_for_tree(ws.tree_for_place(uuid.UUID(room)))


def _open_branch(client, room: str) -> str:
    from app.domain.workspace import service as ws

    async def _do() -> str:
        async with client.test_factory() as s:
            tree = await WorkTreeService(s).current(uuid.UUID(room))
            assert tree is not None
            return ws.branch_for_tree(tree.id)

    return client.portal.call(_do)


def _read_the_place(client, room: str) -> None:
    """任何一条按地点寻址的真实入口 —— 它们都先把这个 id 解析成一个地点。"""
    r = client.post(f"/topics/{room}/ready")
    assert r.status_code == 200, r.text


def test_a_marker_left_on_a_delivered_batch_is_dragged_back_onto_the_open_one(
    client,
):
    pid, room = _room_with_a_batch(client)
    from app.domain.workspace import service as ws

    delivered = _marker_branch(room)

    async def _land_and_start_the_next() -> None:
        async with client.test_factory() as s:
            trees = WorkTreeService(s)
            landed = await trees.current(uuid.UUID(room))
            assert landed is not None
            await trees.mark_merged(landed)
            await trees.ensure_open(project_id=uuid.UUID(pid), room_id=uuid.UUID(room))
            await s.commit()
            # 偏斜：把记号按回那棵已经合掉的树上（回滚、并发写、旧代码留下的都
            # 是这个形状）。
            ws.bind_tree(uuid.UUID(room), landed.id)

    client.portal.call(_land_and_start_the_next)
    assert _marker_branch(room) == delivered  # 偏斜确实造出来了

    _read_the_place(client, room)

    assert _marker_branch(room) != delivered
    assert _marker_branch(room) == _open_branch(client, room)


def test_a_marker_naming_a_tree_that_does_not_exist_is_repaired(client):
    """回滚过的那次 `ensure_open` 留下的形状：记号指着一行没有的树。照它解析出来
    的分支不属于任何一批活，写进去的东西谁也交付不了。"""
    pid, room = _room_with_a_batch(client)
    from app.domain.workspace import service as ws

    ws.bind_tree(uuid.UUID(room), uuid.uuid4())
    assert _marker_branch(room) != _open_branch(client, room)

    _read_the_place(client, room)

    assert _marker_branch(room) == _open_branch(client, room)
