"""Living doc: docs-out display + docs-in edit (evals B1/B2)."""

from tests.integration.conftest import post_project, session_auth_headers
from tests.support.living_doc import document_of


def _doc(client, room) -> str:
    """The room's document, as its routes address it."""
    return f"/documents/{document_of(client, room)}"


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "话题"}).json()[
        "data"
    ]
    return t["id"]


def test_doc_absent_then_created_and_updated(client):
    tid = _topic(client)

    assert client.get(_doc(client, tid)).json()["data"] is None

    r = client.put(
        _doc(client, tid),
        json={
            "content": "## 目标\n做推荐系统",
            "expected_version": 0,
        },
        headers=session_auth_headers("owner"),
    )
    assert r.status_code == 200
    doc = r.json()["data"]
    assert doc["kind"] == "doc"
    assert "做推荐系统" in doc["content"]

    # docs-out: the doc is now readable.
    got = client.get(_doc(client, tid)).json()["data"]
    assert got["id"] == doc["id"]

    # Editing again updates the SAME canonical doc (no duplicate doc root).
    client.put(
        _doc(client, tid),
        json={
            "content": "## 目标\n改成做问答系统",
            "expected_version": 1,
        },
        headers=session_auth_headers("owner"),
    )
    updated = client.get(_doc(client, tid)).json()["data"]
    assert updated["id"] == doc["id"]
    assert "问答系统" in updated["content"]
    # GET /docs is now the structured node tree (B1): heading + paragraph.
    nodes = client.get(f"{_doc(client, tid)}/nodes").json()["data"]["data"]
    assert [n["node_type"] for n in nodes] == ["heading", "paragraph"]


def test_doc_edit_emits_conversation_event(client):
    tid = _topic(client)
    client.put(
        _doc(client, tid),
        json={"content": "x", "expected_version": 0},
        headers=session_auth_headers("owner"),
    )
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    # An append-only event block records the edit (spec H1 / eval B2).
    assert any(b["kind"] == "event" and "编辑了文档" in b["content"] for b in blocks)


def test_empty_editor_paragraph_is_saved_without_a_contribution_notice(client):
    tid = _topic(client)
    for version, content in enumerate(
        ["调查安排", "调查安排\n\n&nbsp;", "调查安排\n\n实地计数"]
    ):
        response = client.put(
            _doc(client, tid),
            json={"content": content, "expected_version": version},
            headers=session_auth_headers("owner"),
        )
        assert response.status_code == 200
        assert response.json()["data"]["content"] == content
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    edits = [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]
    # Writes in a row are one line; the empty paragraph never shows in it.
    assert len(edits) == 1
    assert "+实地计数" in edits[-1]["meta"]["detail"]
    assert "&nbsp;" not in edits[-1]["meta"]["detail"]


def test_literal_entity_in_code_remains_in_edit_evidence(client):
    tid = _topic(client)
    client.put(
        _doc(client, tid),
        json={
            "content": "```html\n&nbsp;\n```",
            "expected_version": 0,
        },
        headers=session_auth_headers("owner"),
    )
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    edits = [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]
    assert "+&nbsp;" in edits[-1]["meta"]["detail"]


def test_document_save_pushes_persisted_notice_and_refresh_to_teammates(
    client, monkeypatch
):
    from app.api.routes import topics

    frames = []

    async def publish(channel, frame):
        frames.append((channel, frame))

    monkeypatch.setattr(topics.get_broker(), "publish", publish)
    tid = _topic(client)
    frames.clear()
    response = client.put(
        _doc(client, tid),
        json={"content": "调查安排", "expected_version": 0},
        headers=session_auth_headers("owner"),
    )
    assert response.status_code == 200
    assert [frame["type"] for _, frame in frames] == ["event_block", "state"]
    assert all(channel == tid for channel, _ in frames)
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert frames[0][1]["block"]["id"] in {block["id"] for block in blocks}
    frames.clear()
    client.put(
        _doc(client, tid),
        json={
            "content": "调查安排\n\n&nbsp;",
            "expected_version": 1,
        },
        headers=session_auth_headers("owner"),
    )
    assert [frame["type"] for _, frame in frames] == ["state"]


def test_doc_canonicalizes_friendly_mentions(client, bearer):
    """A + backstop: friendly "@handle / @话题名" in doc content is rewritten to
    structured tokens on PUT, same as chat replies (裸名 stays untouched)."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "主话题"}).json()[
        "data"
    ]
    other = client.post(
        "/topics", json={"project_id": p["id"], "title": "分页调研"}
    ).json()["data"]

    client.put(
        _doc(client, t["id"]),
        json={
            "content": "待办：@user-1 跟进，结论同步到 @分页调研。裸名 user-1 不动",
            "expected_version": 0,
        },
    )
    doc = client.get(_doc(client, t["id"])).json()["data"]
    assert "<@user-1>" in doc["content"]
    assert f"<#{other['id']}>" in doc["content"]
    assert "裸名 user-1 不动" in doc["content"]


# --- 一份文档只有整块写法，所以旧版本写回去必须被拒 -----------------------------


def test_a_write_based_on_an_old_version_is_refused(client):
    """芝士 reads the doc, works for a while, and sets back a document it built
    from what it read. A person edited it meanwhile. There is no partial write
    of this doc, so letting the second write win erases the first completely."""
    tid = _topic(client)
    client.put(
        _doc(client, tid),
        json={"content": "# 目标\n做推荐", "expected_version": 0},
    )
    stale = client.get(_doc(client, tid)).json()["data"]["doc_version"]
    client.put(
        _doc(client, tid),
        json={
            "content": "# 目标\n做推荐\n\n先跑通召回",
            "expected_version": stale,
        },
        headers=session_auth_headers("owner"),
    )

    r = client.put(
        _doc(client, tid),
        json={
            "content": "# 目标\n做问答",
            "expected_version": stale,
        },
    )
    assert r.status_code == 409, r.text
    # The current version rides along, so a client can rebase without a re-read.
    assert r.json()["error"]["data"]["doc_version"] == stale + 1
    # Nothing of the refused write survived anywhere.
    doc = client.get(_doc(client, tid)).json()["data"]
    assert doc["content"] == "# 目标\n做推荐\n\n先跑通召回"
    assert doc["doc_version"] == stale + 1


def test_a_refused_write_announces_nothing(client):
    """The '编辑了文档' event and the doc's node tree are effects of a write that
    happened. A rejected save that still emitted them would put a change in the
    room, and an anchor in the tree, that is in no version of the document."""
    tid = _topic(client)
    client.put(
        _doc(client, tid),
        json={"content": "# 甲", "expected_version": 0},
        headers=session_auth_headers("owner"),
    )
    before = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]

    r = client.put(
        _doc(client, tid),
        json={"content": "# 乙", "expected_version": 0},
    )
    assert r.status_code == 409

    after = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert [b["id"] for b in after] == [b["id"] for b in before]
    nodes = client.get(f"{_doc(client, tid)}/nodes").json()["data"]["data"]
    assert [n["content"] for n in nodes] == ["# 甲"]


def test_creating_the_first_doc_expects_no_doc(client):
    """0 is a version like any other: it says "there is nothing here yet". A
    writer that got that answer minutes ago must not create the doc over one
    somebody else created since."""
    tid = _topic(client)
    first = client.put(
        _doc(client, tid),
        json={"content": "# 甲", "expected_version": 0},
        headers=session_auth_headers("owner"),
    )
    assert first.status_code == 200
    assert first.json()["data"]["doc_version"] == 1

    second = client.put(
        _doc(client, tid),
        json={"content": "# 乙", "expected_version": 0},
    )
    assert second.status_code == 409
    assert client.get(_doc(client, tid)).json()["data"]["content"] == "# 甲"


def test_a_doc_that_reads_like_a_log_is_written_anyway_and_flagged(client):
    """写入时检查（#1889 第 3 条）：写入照样成功，警告跟着这一次响应回来。

    拦下来是错的——让人先猜格式再写字，比一条警告贵得多；静默接受也是错的——
    下一次读它的人读到的还是流水账。所以两样都要：文档是新的，警告也在。
    """
    tid = _topic(client)
    logged = (
        "## 进展日志\n\n"
        "2026-09-01 起了个架子\n"
        "2026-09-02 接上了接口\n"
        "2026-09-03 修好了两个 bug\n"
    )

    r = client.put(
        _doc(client, tid),
        json={"content": logged, "expected_version": 0},
        headers=session_auth_headers("owner"),
    )

    assert r.status_code == 200
    assert r.json()["data"]["content"] == logged
    assert any("进展日志" in w for w in r.json()["warnings"])
    assert client.get(_doc(client, tid)).json()["data"]["content"] == logged


def test_a_doc_written_as_state_comes_back_with_no_warnings(client):
    tid = _topic(client)
    state = "## 目标\n\n支持翻页。\n\n## 现状\n\n用 cursor，不用 offset。\n"

    r = client.put(
        _doc(client, tid),
        json={"content": state, "expected_version": 0},
        headers=session_auth_headers("owner"),
    )

    assert r.status_code == 200
    # 一个干净的响应里没有这个字段：空表和「没有警告」在这里是同一件事。
    assert "warnings" not in r.json()
