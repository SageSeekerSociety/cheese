"""房间「活着」和「有我要读的东西」是两个问题，答案相反。

两个查询挨在一起，join 的是同一对表，写法几乎一样，结论必须相反：

- **最后活动时间**要算上支线。一个房间的活正在跑，这个房间就是活的，它该往列表
  上排——而「活都派出去了、房间主线安静着」恰恰是这种房间的常态。
- **未读角标**不能算支线。每条支线说一句话就把房间标成未读，红点会立刻变成噪音，
  而设计明说支线不要未读那一整套。

写成一个文件是因为它们最可能的坏法是「顺手统一」：下一个人看到两处相似的查询、
一处带 `task_id IS NULL` 一处不带，很容易以为是漏了。
"""

from tests.integration.conftest import session_auth_headers


def _project(client) -> dict:
    return client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]


def _room(client, project_id: str) -> str:
    return client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]


def _dispatch(client, room_id: str, title: str = "一件活") -> str:
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _say(client, place_id: str, who: str, text: str) -> None:
    r = client.post(
        f"/topics/{place_id}/comments",
        json={"content": text, "author": who},
        headers=session_auth_headers(who),
    )
    assert r.status_code == 200, r.text


def _rooms(client, project_id: str, viewer: str) -> dict[str, dict]:
    rows = client.get(
        f"/topics?project_id={project_id}", headers=session_auth_headers(viewer)
    ).json()["data"]["data"]
    return {t["id"]: t for t in rows}


def test_work_in_a_room_keeps_the_room_alive(client):
    """支线里说话 → 房间的最后活动时间跟着往前走。"""
    p = _project(client)
    room_id = _room(client, p["id"])
    before = _rooms(client, p["id"], "alice")[room_id]["last_activity_at"]

    task_id = _dispatch(client, room_id)
    _say(client, task_id, "bob", "我在这条支线里干活")

    after = _rooms(client, p["id"], "alice")[room_id]["last_activity_at"]
    assert after > before, "房间里有活在跑，它却看起来一动没动"


def test_work_in_a_room_does_not_light_the_unread_badge(client):
    """支线里说话 → 房间的未读角标不动。

    反过来做的话，一个把活都派出去的房间会永远顶着红点，而红点里没有一句是给
    房间里的人看的。
    """
    p = _project(client)
    room_id = _room(client, p["id"])
    task_id = _dispatch(client, room_id)

    _say(client, task_id, "bob", "我在这条支线里干活")
    assert _rooms(client, p["id"], "alice")[room_id]["unread"] == 0

    # 房间主线上有人说话才算未读——这一半必须还成立，否则上面那条就是把角标
    # 整个关掉了。
    _say(client, room_id, "bob", "房间里说一句")
    assert _rooms(client, p["id"], "alice")[room_id]["unread"] == 1
