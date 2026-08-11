"""Activity ingestion (eval E1) — with the stub agent (no live model)."""


def test_ingest_activity_creates_event_topic(client):
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    pid = p["id"]

    r = client.post(
        f"/api/projects/{pid}/activities",
        json={
            "text": "今天做了用户访谈，15 人，发现大家选课最看重老师评分。",
            "author": "user-1",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert "topic_id" in data
    assert data["summary"]  # 芝士 (stub) produced a summary

    # An event topic was created under the project, carrying the raw input.
    topics = client.get(f"/api/topics?project_id={pid}").json()["data"]["data"]
    event_topics = [t for t in topics if t["title"].startswith("[活动]")]
    assert len(event_topics) == 1

    blocks = client.get(f"/api/topics/{data['topic_id']}/blocks").json()["data"]["data"]
    kinds = [b["kind"] for b in blocks]
    assert "event" in kinds  # the raw activity input
    assert any(b["author_type"] == "ai" for b in blocks)  # 芝士's digest


def test_ingest_activity_404_for_missing_project(client):
    r = client.post(
        "/api/projects/00000000-0000-0000-0000-000000000000/activities",
        json={"text": "x"},
    )
    assert r.status_code == 404
