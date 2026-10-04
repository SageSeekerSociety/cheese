"""B1 Phase 1: editing the living doc keeps a structured node tree in sync."""

from tests.integration.conftest import post_project, session_auth_headers
from tests.support.living_doc import document_of


def _doc(client, room) -> str:
    """The room's document, as its routes address it."""
    return f"/documents/{document_of(client, room)}"


def _project_and_topic(client) -> str:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    tid = client.post("/topics", json={"project_id": pid, "title": "T"}).json()["data"][
        "id"
    ]
    return tid


DOC_V1 = "# 目标\n\n搭建原型。\n\n## 约束\n\n- 数据脱敏\n- Recall@10"


def test_document_edits_keep_each_sections_author_and_change_record(client):
    from app.core.sandbox_auth import mint_scoped_token

    tid = _project_and_topic(client)
    pid = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    response = client.put(
        _doc(client, tid),
        json={"content": DOC_V1, "expected_version": 0},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text
    revised = DOC_V1.replace("搭建原型。", "先做三个路口的实地观察。")
    response = client.put(
        _doc(client, tid),
        headers={"X-Cheese-Token": mint_scoped_token(project_id=pid, topic_id=tid)},
        json={"content": revised, "expected_version": 1},
    )
    assert response.status_code == 200, response.text
    nodes = {node["content"]: node for node in _nodes(client, tid)}
    assert nodes["# 目标"]["author"] == "alice"
    changed_author = nodes["先做三个路口的实地观察。"]["author"]
    assert changed_author.startswith("cheese")
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    edits = [
        block for block in blocks if (block.get("meta") or {}).get("action") == "doc"
    ]
    # The two writes came in a row, so the room reads one line for both.
    change = next(block for block in edits if block["meta"]["doc_version"] == 2)
    assert change["author"] == changed_author
    assert "<@alice>" in change["content"] and "芝士" in change["content"]
    assert "+先做三个路口的实地观察。" in change["meta"]["detail"]
    assert "搭建原型" not in change["meta"]["detail"]


def _nodes(client, tid: str) -> list[dict]:
    return client.get(f"{_doc(client, tid)}/nodes").json()["data"]["data"]


def test_doc_edit_builds_node_tree(client):
    tid = _project_and_topic(client)
    r = client.put(
        _doc(client, tid),
        json={"content": DOC_V1, "expected_version": 0},
    )
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
    root = client.get(_doc(client, tid)).json()["data"]
    assert all(
        n["struct_parent"] == root["id"] and n["kind"] == "doc_node" for n in nodes
    )
    # the canonical doc still returns the full markdown (frontend contract)
    assert root["content"] == DOC_V1


def test_resetting_same_doc_keeps_node_ids_stable(client):
    tid = _project_and_topic(client)
    client.put(
        _doc(client, tid),
        json={"content": DOC_V1, "expected_version": 0},
    )
    ids1 = [n["id"] for n in _nodes(client, tid)]
    # Re-set identical markdown — should be a no-op for the tree.
    client.put(
        _doc(client, tid),
        json={"content": DOC_V1, "expected_version": 1},
    )
    ids2 = [n["id"] for n in _nodes(client, tid)]
    assert ids1 == ids2


def test_editing_one_block_preserves_other_node_ids(client):
    tid = _project_and_topic(client)
    client.put(
        _doc(client, tid),
        json={"content": DOC_V1, "expected_version": 0},
    )
    before = {n["content"]: n["id"] for n in _nodes(client, tid)}

    # Change only the paragraph; headings and list are untouched.
    v2 = DOC_V1.replace("搭建原型。", "搭建一个推荐原型。")
    client.put(
        _doc(client, tid),
        json={"content": v2, "expected_version": 1},
    )
    after = {n["content"]: n["id"] for n in _nodes(client, tid)}

    for unchanged in ("# 目标", "## 约束", "- 数据脱敏\n- Recall@10"):
        assert after[unchanged] == before[unchanged]
    # the edited paragraph is a new node
    assert "搭建一个推荐原型。" in after


def test_doc_nodes_excluded_from_conversation_timeline(client):
    tid = _project_and_topic(client)
    client.put(
        _doc(client, tid),
        json={"content": DOC_V1, "expected_version": 0},
    )
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert not any(b["kind"] == "doc_node" for b in blocks)
