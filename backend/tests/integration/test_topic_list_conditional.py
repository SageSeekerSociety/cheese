"""GET /api/topics 的条件请求。

侧栏每 30 秒轮询一次整份话题清单，清单可以到几百 KB。这一条钉的是：清单没变时服务端
只回一行 header，不再把整份 body 传一遍。
"""

import time

from app.domain.agent.runtime import get_broker
from tests.integration.conftest import (
    new_project,
    post_project,
    session_auth_headers,
)


def _make_project(client) -> str:
    r = post_project(client, json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str, title: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_topics_list_serves_etag_and_304_on_hit(client):
    pid = _make_project(client)
    _make_topic(client, pid, "A")

    first = client.get("/topics", params={"project_id": pid})
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag, "清单必须带 ETag，否则客户端没有东西可以拿来问"
    # 私有视图：别人（或中间代理）不该缓存下来再发给另一个登录的人。
    assert first.headers.get("Cache-Control") == "private, no-cache"
    # 没命中那条路照走信封 —— 前端 request() 靠 {code,message,data} 解包。
    assert first.json()["code"] == 200

    # 带着同一个 ETag 回来问：清单没变，回 304、空 body。
    again = client.get(
        "/topics", params={"project_id": pid}, headers={"If-None-Match": etag}
    )
    assert again.status_code == 304
    assert again.content == b""

    # 清单真的变了（多了一个房间）：同一个 ETag 不再命中，照常回整份 body。
    _make_topic(client, pid, "B")
    changed = client.get(
        "/topics", params={"project_id": pid}, headers={"If-None-Match": etag}
    )
    assert changed.status_code == 200
    assert changed.headers.get("ETag") != etag
    assert len(changed.json()["data"]["data"]) >= 3  # 根房间 + A + B


def test_topics_list_304_also_carries_the_headers(client):
    pid = _make_project(client)
    etag = client.get("/topics", params={"project_id": pid}).headers.get("ETag")
    assert etag

    again = client.get(
        "/topics", params={"project_id": pid}, headers={"If-None-Match": etag}
    )
    assert again.status_code == 304
    # RFC 要求 304 也带回 ETag / Cache-Control：客户端要靠它们更新自己的缓存。
    assert again.headers.get("ETag") == etag
    assert again.headers.get("Cache-Control") == "private, no-cache"


def _room_activity(payload: dict, room: str) -> list[dict]:
    rows = payload["data"]["data"]
    return next(t for t in rows if t["id"] == room)["activity"]


def _typing_expires_in(broker, room: str) -> float:
    entry = next(e for e in broker.activity.snapshot(room) if e["kind"] == "typing")
    return entry["expires_in"]


def test_a_typing_member_does_not_break_the_etag(client):
    """有人打字时清单的 ETag 照样命中，因为打字条目根本不进这份列表。

    打字条目的 `expires_in` 是「还差几秒过期」，每次请求现算、值每次都不一样。侧栏不
    画打字的人（`useTopicRail`），这份列表三十秒才读一次、打字却五秒就过去了。留着它，
    只要有人在打字，几百 KB 的清单每三十秒都命中不了 304、原样重传。
    """
    project = new_project(client, owner="alice")
    pid, room = project["id"], project["root_topic_id"]
    headers = session_auth_headers("alice")
    broker = get_broker()

    # 一个队友在这个房间里干活 —— 它必须出现在清单里（侧栏那行要带绿点）。
    client.portal.call(
        broker.publish,
        room,
        {"type": "turn_started", "turn_id": "t1", "agent": "cheese-a1"},
    )
    # 同时有人在打字。房间 socket 上那份 activity 仍是完整的，含打字
    # （`test_member_activity` 钉着它）；这份列表不带它。
    broker.activity.typing(room, "alice", True)

    first = client.get("/topics", params={"project_id": pid}, headers=headers)
    assert first.status_code == 200
    assert [a["kind"] for a in _room_activity(first.json(), room)] == ["working"]
    etag = first.headers["ETag"]

    # 打字的 `expires_in` 确实在变 —— 变的那个值才是把清单的 ETag 打穿的原因。
    before = _typing_expires_in(broker, room)
    time.sleep(0.2)
    assert _typing_expires_in(broker, room) < before

    # 同一个 ETag 回来问：清单没变（打字条目根本不在里面），回 304。
    again = client.get(
        "/topics",
        params={"project_id": pid},
        headers={**headers, "If-None-Match": etag},
    )
    assert again.status_code == 304
    assert again.content == b""
