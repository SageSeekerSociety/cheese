"""GET /api/topics?sort=&order= — 话题列表排序 (最后更新时间 / 标题)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.topic.models import Topic


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(
    client, project_id: str, title: str, parent_id: str | None = None
) -> str:
    body = {"project_id": project_id, "title": title}
    if parent_id:
        body["parent_id"] = parent_id
    r = client.post("/topics", json=body)
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _set_updated_at(client, topic_id: str, when: datetime) -> None:
    async def _update():
        async with client.test_factory() as s:
            topic = await s.get(Topic, uuid.UUID(topic_id))
            topic.updated_at = when
            await s.commit()

    asyncio.run(_update())


def _titles(client, project_id: str, **params) -> list[str]:
    # project_id must ride in `params` too: httpx REPLACES a URL's existing
    # query string with `params=` instead of merging, so putting it in the URL
    # silently dropped it and every request 400ed on the missing field.
    r = client.get("/topics", params={"project_id": project_id, **params})
    assert r.status_code == 200
    # Exclude the auto-created root topic — this test only cares about the
    # work topics it seeded, in the order it seeded checkable titles for.
    return [t["title"] for t in r.json()["data"]["data"] if t["kind"] != "root"]


def test_sort_by_title(client):
    pid = _make_project(client)
    _make_topic(client, pid, "Banana")
    _make_topic(client, pid, "Apple")
    _make_topic(client, pid, "Cherry")

    assert _titles(client, pid, sort="title", order="asc") == [
        "Apple",
        "Banana",
        "Cherry",
    ]
    assert _titles(client, pid, sort="title", order="desc") == [
        "Cherry",
        "Banana",
        "Apple",
    ]


def test_sort_by_updated_at(client):
    pid = _make_project(client)
    a = _make_topic(client, pid, "A")
    b = _make_topic(client, pid, "B")
    c = _make_topic(client, pid, "C")

    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Explicitly out of creation order: C touched most recently, A least.
    _set_updated_at(client, a, base)
    _set_updated_at(client, b, base + timedelta(hours=2))
    _set_updated_at(client, c, base + timedelta(hours=1))

    assert _titles(client, pid, sort="updated_at", order="desc") == ["B", "C", "A"]
    assert _titles(client, pid, sort="updated_at", order="asc") == ["A", "C", "B"]


def test_sort_defaults_to_created_order_when_unspecified(client):
    # No sort/order params: preserves the pre-existing behavior (creation order)
    # so callers that don't opt into sorting see no change.
    pid = _make_project(client)
    _make_topic(client, pid, "First")
    _make_topic(client, pid, "Second")
    _make_topic(client, pid, "Third")

    assert _titles(client, pid) == ["First", "Second", "Third"]


def test_sort_does_not_break_parent_child_structure(client):
    # Sorting must not disturb parent_id linkage — the tree is rebuilt
    # client-side from parent_id, regardless of list order.
    pid = _make_project(client)
    parent = _make_topic(client, pid, "Zeta parent")
    child = _make_topic(client, pid, "Alpha child", parent_id=parent)

    r = client.get(
        "/topics", params={"project_id": pid, "sort": "title", "order": "asc"}
    )
    assert r.status_code == 200
    by_id = {t["id"]: t for t in r.json()["data"]["data"]}
    # "Alpha child" sorts before "Zeta parent" by title, but parent_id linkage
    # (what the frontend rebuilds the tree from) must be untouched by that.
    assert by_id[child]["parent_id"] == parent
    root = next(t for t in by_id.values() if t["kind"] == "root")
    assert by_id[parent]["parent_id"] == root["id"]
