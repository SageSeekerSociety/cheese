"""Living doc: docs-out display + docs-in edit (evals B1/B2)."""


def _topic(client) -> str:
    p = client.post("/api/projects", json={"name": "P"}).json()["data"]
    t = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "话题"}
    ).json()["data"]
    return t["id"]


def test_doc_absent_then_created_and_updated(client):
    tid = _topic(client)

    assert client.get(f"/api/topics/{tid}/doc").json()["data"] is None

    r = client.put(
        f"/api/topics/{tid}/doc",
        json={"content": "## 目标\n做推荐系统", "author": "user-1"},
    )
    assert r.status_code == 200
    doc = r.json()["data"]
    assert doc["kind"] == "doc"
    assert "做推荐系统" in doc["content"]

    # docs-out: the doc is now readable.
    got = client.get(f"/api/topics/{tid}/doc").json()["data"]
    assert got["id"] == doc["id"]

    # Editing again updates the SAME doc (no duplicate doc blocks).
    client.put(
        f"/api/topics/{tid}/doc",
        json={"content": "## 目标\n改成做问答系统", "author": "user-1"},
    )
    docs = client.get(f"/api/topics/{tid}/docs").json()["data"]
    assert docs["total"] == 1
    assert "问答系统" in docs["data"][0]["content"]


def test_doc_edit_emits_conversation_event(client):
    tid = _topic(client)
    client.put(f"/api/topics/{tid}/doc", json={"content": "x", "author": "user-1"})
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    # An append-only event block records the edit (spec H1 / eval B2).
    assert any(b["kind"] == "event" and "编辑了文档" in b["content"] for b in blocks)
