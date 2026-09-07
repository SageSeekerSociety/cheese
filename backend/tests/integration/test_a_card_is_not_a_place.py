"""一件活不是一个地点：卡的 id 不是地址。

一条活曾经是一个 `topics` 行，然后是一个 `tasks` 行**仍然被当地址用**——`/blocks`、
`/doc`、`/usage` 全都认它，界面上点开它就跳到一个新页面。它不该是：做这条活的是房间
会话里的一个分身，它没有名册、没有归档、没有自己的一轮，也没有自己的 token。

所以这里钉两件事：拿卡的 id 当话题地址一律 404；卡照常读得到、说得上话，走的是它
所在的房间 —— 看卡的人本来就站在那儿。
"""

import uuid
from datetime import UTC, datetime

from app.domain.project.models import Project
from app.domain.room_task.models import Task, WorkTree
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _room(client, project_id: str, title: str = "运维") -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": "alice"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _card(client, room_id: str, title: str = "接口分页", brief: str = "") -> dict:
    r = client.post(f"/topics/{room_id}/split", json={"title": title, "brief": brief})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_a_cards_id_is_not_a_topic_address(client):
    """读和写都不行，而且是同一个理由：那个 id 名下没有地点。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id)

    assert client.get(f"/topics/{card['id']}").status_code == 404
    assert client.get(f"/topics/{card['id']}/blocks").status_code == 404
    assert client.get(f"/topics/{card['id']}/doc").status_code == 404
    assert (
        client.post(f"/topics/{card['id']}/title", json={"title": "换个名"}).status_code
        == 404
    )


def test_a_card_is_read_through_the_room_it_belongs_to(client):
    """卡本身、它的看板那一格、它的对话，一条请求全给。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id, title="接口分页", brief="加 cursor 参数")

    r = client.get(f"/topics/{room_id}/tasks/{card['id']}")
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    assert got["id"] == card["id"]
    assert got["title"] == "接口分页"
    assert got["brief"] == "加 cursor 参数"
    # 状态词是后端算的那一句，和它在看板上显示的是同一句。
    assert got["presentation"]["display_status"]
    assert got["blocks"] == []


def test_another_rooms_card_is_not_readable_through_this_room(client):
    """房间是地址的一半，不是装饰：拿 A 房间的地址读 B 房间的卡答 404。"""
    project_id = _project(client)
    mine = _room(client, project_id, "我的房间")
    theirs = _room(client, project_id, "别人的房间")
    card = _card(client, theirs)

    assert client.get(f"/topics/{mine}/tasks/{card['id']}").status_code == 404


def test_saying_something_on_a_card_lands_on_the_card(client):
    """人在卡下面说的话落在这条活的时间线上，不在房间主线上。

    房间会被叫来转达（做这条活的分身住在房间的会话里，人够不着它），但那句话本身
    留在它被说的地方——看这张卡的人下周打开还看得见。
    """
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id)

    r = client.post(
        f"/topics/{room_id}/tasks/{card['id']}/messages",
        json={"content": "这条先别动 routes"},
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 200, r.text

    said = client.get(f"/topics/{room_id}/tasks/{card['id']}").json()["data"]["blocks"]
    assert [b["content"] for b in said] == ["这条先别动 routes"]
    # 房间主线上没有这句话。
    room_line = client.get(f"/topics/{room_id}/blocks").json()["data"]
    assert all("这条先别动 routes" not in b["content"] for b in room_line)


def test_a_claim_is_only_recorded_when_it_names_a_card(client):
    """声明记在卡上。房间自己声明只查不记——它没有卡可以记。"""
    project_id = _project(client)
    room_id = _room(client, project_id)
    card = _card(client, room_id)

    room_said = client.post(
        f"/topics/{room_id}/claim", json={"paths": ["backend/app/x.py"]}
    )
    assert room_said.status_code == 200, room_said.text
    assert room_said.json()["data"]["claimed"] == []

    on_card = client.post(
        f"/topics/{room_id}/tasks/{card['id']}/claim",
        json={"paths": ["backend/app/x.py"]},
    )
    assert on_card.status_code == 200, on_card.text
    assert on_card.json()["data"]["claimed"] == ["backend/app/x.py"]

    # 记下来了才拦得住第二条活。
    other = _card(client, room_id, title="另一件")
    clash = client.post(
        f"/topics/{room_id}/tasks/{other['id']}/claim",
        json={"paths": ["backend/app/x.py"]},
    )
    assert clash.json()["data"]["refusals"], "同一个文件被两条活声明，要拒绝"


def test_a_card_that_kept_its_old_topic_id_still_renders(client):
    """存量：一条活的 id 曾经是一个 `topics` 行的 id（migration c4a7e91b2d05 保号
    搬家，那些 `topics` 行同一次被删掉）。

    这一批不做转换迁移——它们就是历史卡。要保证的只有一件事：**不炸**。它照常出现
    在房间的清单里、照常读得出来，而拿它的 id 当话题地址照样 404（那个 topics 行
    早就不在了，所以这不是新规矩，只是新规矩和旧数据说的是同一句话）。
    """
    ids: dict[str, uuid.UUID] = {}
    inherited_id = uuid.uuid4()

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="老房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()
            tree = WorkTree(project_id=project.id, room_id=room.id)
            s.add(tree)
            await s.flush()
            s.add(
                Task(
                    id=inherited_id,
                    project_id=project.id,
                    room_id=room.id,
                    tree_id=tree.id,
                    title="从前是个话题",
                    created_at=datetime(2026, 7, 1, tzinfo=UTC),
                )
            )
            await s.commit()
            ids["room"] = room.id

    client.portal.call(_seed)
    room_id = ids["room"]

    listed = client.get(f"/topics/{room_id}/tasks").json()["data"]
    assert [t["id"] for t in listed] == [str(inherited_id)]
    assert listed[0]["presentation"]["display_status"]

    one = client.get(f"/topics/{room_id}/tasks/{inherited_id}")
    assert one.status_code == 200, one.text
    assert one.json()["data"]["title"] == "从前是个话题"

    assert client.get(f"/topics/{inherited_id}").status_code == 404
