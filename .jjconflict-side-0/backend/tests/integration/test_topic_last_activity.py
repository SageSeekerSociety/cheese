"""话题最后活动时间 — GET /api/topics{,/{id}} last_activity_at, sort, active_since.

The bug this pins down: `updated_at` is the topics ROW's mtime, so a topic that
was talked in all day still reported the moment its title or session id last
changed, and "最近活跃的话题" came out empty or in the wrong order.
"""

from datetime import UTC, datetime, timedelta


def _make_project(client) -> str:
    r = client.post("/projects", json={"name": "P"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _make_topic(client, project_id: str, title: str) -> str:
    r = client.post("/topics", json={"project_id": project_id, "title": title})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _add_block(client, topic_id: str, text: str) -> str:
    """Land a real block in the topic and return its created_at."""
    r = client.post(f"/topics/{topic_id}/decision", json={"decision": text})
    assert r.status_code == 200
    return r.json()["data"]["created_at"]


def _list(client, project_id: str, **params) -> list[dict]:
    r = client.get("/topics", params={"project_id": project_id, **params})
    assert r.status_code == 200
    # The project's auto-created root topic is not part of what these cases seed.
    return [t for t in r.json()["data"]["data"] if t["kind"] != "root"]


def _titles(client, project_id: str, **params) -> list[str]:
    return [t["title"] for t in _list(client, project_id, **params)]


def test_last_activity_follows_a_new_block(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid, "A")

    fresh = client.get(f"/topics/{tid}").json()["data"]
    # No blocks yet: the topic's own creation is the last thing that happened.
    assert fresh["last_activity_at"] == fresh["created_at"]

    block_at = _add_block(client, tid, "ship it")

    after = client.get(f"/topics/{tid}").json()["data"]
    assert after["last_activity_at"] == block_at
    assert after["last_activity_at"] > fresh["last_activity_at"]
    # The row's own mtime is a different question and did not move — which is
    # exactly why it cannot answer "was there activity here".
    assert after["updated_at"] == fresh["updated_at"]

    # And it keeps following: a second block wins over the first.
    second_at = _add_block(client, tid, "and again")
    latest = client.get(f"/topics/{tid}").json()["data"]
    assert latest["last_activity_at"] == second_at


def test_metadata_edits_do_not_count_as_activity(client):
    pid = _make_project(client)
    tid = _make_topic(client, pid, "A")
    block_at = _add_block(client, tid, "ship it")

    r = client.post(f"/topics/{tid}/title", json={"title": "renamed"})
    assert r.status_code == 200

    after = client.get(f"/topics/{tid}").json()["data"]
    assert after["title"] == "renamed"
    # Renaming bumps the row's mtime past the block, and last_activity_at must
    # not follow it — the topic has been silent since that block.
    assert after["updated_at"] > block_at
    assert after["last_activity_at"] == block_at


def test_list_reports_last_activity_per_topic(client):
    pid = _make_project(client)
    a = _make_topic(client, pid, "A")
    _make_topic(client, pid, "B")
    a_block = _add_block(client, a, "only A gets a block")

    rows = {t["title"]: t for t in _list(client, pid)}
    assert rows["A"]["last_activity_at"] == a_block
    assert rows["B"]["last_activity_at"] == rows["B"]["created_at"]


def test_sort_by_last_activity(client):
    pid = _make_project(client)
    a = _make_topic(client, pid, "A")
    b = _make_topic(client, pid, "B")
    c = _make_topic(client, pid, "C")

    # Activity order deliberately differs from creation order.
    _add_block(client, a, "first")
    _add_block(client, c, "second")
    _add_block(client, b, "third")

    assert _titles(client, pid, sort="last_activity_at", order="desc") == [
        "B",
        "C",
        "A",
    ]
    assert _titles(client, pid, sort="last_activity_at", order="asc") == ["A", "C", "B"]

    # The old sort still answers its own (different) question: nothing here has
    # touched the rows since creation, so it stays in creation order.
    assert _titles(client, pid, sort="updated_at", order="asc") == ["A", "B", "C"]


def test_filter_active_since(client):
    pid = _make_project(client)
    old = _make_topic(client, pid, "Old")
    recent = _make_topic(client, pid, "Recent")

    _add_block(client, old, "long ago")
    cutoff = datetime.now(UTC)
    _add_block(client, recent, "just now")

    active = _list(client, pid, active_since=cutoff.isoformat())
    assert [t["title"] for t in active] == ["Recent"]
    # `total` describes what came back, not the unfiltered project.
    r = client.get(
        "/topics", params={"project_id": pid, "active_since": cutoff.isoformat()}
    )
    assert r.json()["data"]["total"] == len(r.json()["data"]["data"])

    # A cutoff in the future keeps nothing; one in the past keeps everything.
    future = (cutoff + timedelta(hours=1)).isoformat()
    assert _list(client, pid, active_since=future) == []
    past = (cutoff - timedelta(days=1)).isoformat()
    assert sorted(_titles(client, pid, active_since=past)) == ["Old", "Recent"]


def test_active_since_without_offset_is_read_as_utc(client):
    """Clients send bare `2026-08-12T09:00:00` constantly; it must not 500 nor
    silently drift by the server's local offset."""
    pid = _make_project(client)
    tid = _make_topic(client, pid, "A")
    _add_block(client, tid, "now")

    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    assert _titles(client, pid, active_since=naive_past.isoformat()) == ["A"]

    naive_future = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    assert _titles(client, pid, active_since=naive_future.isoformat()) == []
