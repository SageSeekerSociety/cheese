"""Memory search endpoint (`cheese recall`) on the flat DB backend."""


def _project(client) -> str:
    r = client.post("/api/projects", json={"name": "Mem"})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def test_search_project_memory(client):
    pid = _project(client)
    for fact in ("技术栈=FastAPI", "数据库用 PostgreSQL", "前端 Vue 3"):
        r = client.post(f"/api/projects/{pid}/memory", json={"content": fact})
        assert r.status_code == 200

    r = client.post(f"/api/projects/{pid}/memory/search", json={"query": "PostgreSQL"})
    assert r.status_code == 200
    hits = r.json()["data"]["hits"]
    assert len(hits) == 1
    assert "PostgreSQL" in hits[0]["abstract"]
    assert hits[0]["uri"].startswith("db://memory/")


def test_search_personal_memory(client):
    pid = _project(client)
    r = client.post(
        f"/api/projects/{pid}/memory",
        json={"content": "偏好简洁汇报", "scope": "user", "owner": "andyl"},
    )
    assert r.status_code == 200

    r = client.post(
        f"/api/projects/{pid}/memory/search",
        json={"query": "简洁", "scope": "user", "owner": "andyl"},
    )
    assert r.status_code == 200
    assert len(r.json()["data"]["hits"]) == 1

    # Project scope must not see the personal fact.
    r = client.post(f"/api/projects/{pid}/memory/search", json={"query": "简洁"})
    assert r.json()["data"]["hits"] == []


def test_search_requires_query(client):
    pid = _project(client)
    r = client.post(f"/api/projects/{pid}/memory/search", json={"query": "  "})
    assert r.status_code == 422
