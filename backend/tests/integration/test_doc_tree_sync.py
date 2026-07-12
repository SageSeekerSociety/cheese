"""B1 Phase 1: editing the living doc keeps a structured node tree in sync."""


def _project_and_topic(client) -> str:
    pid = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    tid = client.post("/api/topics", json={"project_id": pid, "title": "T"}).json()[
        "data"
    ]["id"]
    return tid


DOC_V1 = "# 目标\n\n搭建原型。\n\n## 约束\n\n- 数据脱敏\n- Recall@10"


def _nodes(client, tid: str) -> list[dict]:
    return client.get(f"/api/topics/{tid}/docs").json()["data"]["data"]


def test_doc_edit_builds_node_tree(client):
    tid = _project_and_topic(client)
    r = client.put(f"/api/topics/{tid}/doc", json={"content": DOC_V1, "author": "u"})
    assert r.status_code == 200

    nodes = _nodes(client, tid)
    assert [n["node_type"] for n in nodes] == [
        "heading",
        "paragraph",
        "heading",
        "list",
    ]
    # ordered by struct_order
    assert [n["struct_order"] for n in nodes] == [0.0, 1.0, 2.0, 3.0]
    # every node is parented to the canonical doc root, and is a doc_node
    root = client.get(f"/api/topics/{tid}/doc").json()["data"]
    assert all(
        n["struct_parent"] == root["id"] and n["kind"] == "doc_node" for n in nodes
    )
    # the canonical doc still returns the full markdown (frontend contract)
    assert root["content"] == DOC_V1


def test_resetting_same_doc_keeps_node_ids_stable(client):
    tid = _project_and_topic(client)
    client.put(f"/api/topics/{tid}/doc", json={"content": DOC_V1, "author": "u"})
    ids1 = [n["id"] for n in _nodes(client, tid)]
    # Re-set identical markdown — should be a no-op for the tree.
    client.put(f"/api/topics/{tid}/doc", json={"content": DOC_V1, "author": "u"})
    ids2 = [n["id"] for n in _nodes(client, tid)]
    assert ids1 == ids2


def test_editing_one_block_preserves_other_node_ids(client):
    tid = _project_and_topic(client)
    client.put(f"/api/topics/{tid}/doc", json={"content": DOC_V1, "author": "u"})
    before = {n["content"]: n["id"] for n in _nodes(client, tid)}

    # Change only the paragraph; headings and list are untouched.
    v2 = DOC_V1.replace("搭建原型。", "搭建一个推荐原型。")
    client.put(f"/api/topics/{tid}/doc", json={"content": v2, "author": "u"})
    after = {n["content"]: n["id"] for n in _nodes(client, tid)}

    for unchanged in ("# 目标", "## 约束", "- 数据脱敏\n- Recall@10"):
        assert after[unchanged] == before[unchanged]
    # the edited paragraph is a new node
    assert "搭建一个推荐原型。" in after


def test_doc_nodes_excluded_from_conversation_timeline(client):
    tid = _project_and_topic(client)
    client.put(f"/api/topics/{tid}/doc", json={"content": DOC_V1, "author": "u"})
    blocks = client.get(f"/api/topics/{tid}/blocks").json()["data"]["data"]
    assert not any(b["kind"] == "doc_node" for b in blocks)
