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

    # Editing again updates the SAME canonical doc (no duplicate doc root).
    client.put(
        f"/api/topics/{tid}/doc",
        json={"content": "## 目标\n改成做问答系统", "author": "user-1"},
    )
    updated = client.get(f"/api/topics/{tid}/doc").json()["data"]
    assert updated["id"] == doc["id"]
    assert "问答系统" in updated["content"]
    # GET /docs is now the structured node tree (B1): heading + paragraph.
    nodes = client.get(f"/api/topics/{tid}/docs").json()["data"]["data"]
    assert [n["node_type"] for n in nodes] == ["heading", "paragraph"]


def test_doc_edit_emits_conversation_event(client):
    tid = _topic(client)
    client.put(f"/api/topics/{tid}/doc", json={"content": "x", "author": "user-1"})
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    # An append-only event block records the edit (spec H1 / eval B2).
    assert any(b["kind"] == "event" and "编辑了文档" in b["content"] for b in blocks)


def test_doc_canonicalizes_friendly_mentions(client, bearer):
    """A + backstop: friendly "@handle / @话题名" in doc content is rewritten to
    structured tokens on PUT, same as chat replies (裸名 stays untouched)."""
    p = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "user-1"}
    ).json()["data"]
    client.post(
        f"/api/projects/{p['id']}/members",
        json={"user_handle": "user-1"},
        headers=bearer("user-1"),  # the project owner
    )
    t = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "主话题"}
    ).json()["data"]
    other = client.post(
        "/api/topics", json={"project_id": p["id"], "title": "分页调研"}
    ).json()["data"]

    client.put(
        f"/api/topics/{t['id']}/doc",
        json={
            "content": "待办：@user-1 跟进，结论同步到 @分页调研。裸名 user-1 不动",
            "author": "cheese",
        },
    )
    doc = client.get(f"/api/topics/{t['id']}/doc").json()["data"]
    assert "<@user-1>" in doc["content"]
    assert f"<#{other['id']}>" in doc["content"]
    assert "裸名 user-1 不动" in doc["content"]
