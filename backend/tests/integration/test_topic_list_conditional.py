"""GET /api/topics 的条件请求。

侧栏每 30 秒轮询一次整份话题清单，清单可以到几百 KB。这一条钉的是：清单没变时服务端
只回一行 header，不再把整份 body 传一遍。
"""

from tests.integration.conftest import post_project


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
