"""Living doc: docs-out display + docs-in edit (evals B1/B2)."""


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "话题"}).json()[
        "data"
    ]
    return t["id"]


def test_doc_absent_then_created_and_updated(client):
    tid = _topic(client)

    assert client.get(f"/topics/{tid}/doc").json()["data"] is None

    r = client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "## 目标\n做推荐系统",
            "author": "user-1",
            "expected_version": 0,
        },
    )
    assert r.status_code == 200
    doc = r.json()["data"]
    assert doc["kind"] == "doc"
    assert "做推荐系统" in doc["content"]

    # docs-out: the doc is now readable.
    got = client.get(f"/topics/{tid}/doc").json()["data"]
    assert got["id"] == doc["id"]

    # Editing again updates the SAME canonical doc (no duplicate doc root).
    client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "## 目标\n改成做问答系统",
            "author": "user-1",
            "expected_version": 1,
        },
    )
    updated = client.get(f"/topics/{tid}/doc").json()["data"]
    assert updated["id"] == doc["id"]
    assert "问答系统" in updated["content"]
    # GET /docs is now the structured node tree (B1): heading + paragraph.
    nodes = client.get(f"/topics/{tid}/docs").json()["data"]["data"]
    assert [n["node_type"] for n in nodes] == ["heading", "paragraph"]


def test_doc_edit_emits_conversation_event(client):
    tid = _topic(client)
    client.put(
        f"/topics/{tid}/doc",
        json={"content": "x", "author": "user-1", "expected_version": 0},
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
            f"/topics/{tid}/doc",
            json={"content": content, "author": "user-1", "expected_version": version},
        )
        assert response.status_code == 200
        assert response.json()["data"]["content"] == content
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    edits = [b for b in blocks if (b.get("meta") or {}).get("action") == "doc"]
    assert len(edits) == 2
    assert "+实地计数" in edits[-1]["meta"]["detail"]
    assert "&nbsp;" not in edits[-1]["meta"]["detail"]


def test_literal_entity_in_code_remains_in_edit_evidence(client):
    tid = _topic(client)
    client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "```html\n&nbsp;\n```",
            "author": "user-1",
            "expected_version": 0,
        },
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
        f"/topics/{tid}/doc",
        json={"content": "调查安排", "author": "user-1", "expected_version": 0},
    )
    assert response.status_code == 200
    assert [frame["type"] for _, frame in frames] == ["event_block", "state"]
    assert all(channel == tid for channel, _ in frames)
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert frames[0][1]["block"]["id"] in {block["id"] for block in blocks}
    frames.clear()
    client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "调查安排\n\n&nbsp;",
            "author": "user-1",
            "expected_version": 1,
        },
    )
    assert [frame["type"] for _, frame in frames] == ["state"]


def test_doc_canonicalizes_friendly_mentions(client, bearer):
    """A + backstop: friendly "@handle / @话题名" in doc content is rewritten to
    structured tokens on PUT, same as chat replies (裸名 stays untouched)."""
    p = client.post("/projects", json={"name": "P", "owner_handle": "user-1"}).json()[
        "data"
    ]
    client.post(
        f"/projects/{p['id']}/members",
        json={"user_handle": "user-1"},
        headers=bearer("user-1"),  # the project owner
    )
    t = client.post("/topics", json={"project_id": p["id"], "title": "主话题"}).json()[
        "data"
    ]
    other = client.post(
        "/topics", json={"project_id": p["id"], "title": "分页调研"}
    ).json()["data"]

    client.put(
        f"/topics/{t['id']}/doc",
        json={
            "content": "待办：@user-1 跟进，结论同步到 @分页调研。裸名 user-1 不动",
            "author": "cheese",
            "expected_version": 0,
        },
    )
    doc = client.get(f"/topics/{t['id']}/doc").json()["data"]
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
        f"/topics/{tid}/doc",
        json={"content": "# 目标\n做推荐", "author": "cheese", "expected_version": 0},
    )
    stale = client.get(f"/topics/{tid}/doc").json()["data"]["doc_version"]
    client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "# 目标\n做推荐\n\n先跑通召回",
            "author": "user-1",
            "expected_version": stale,
        },
    )

    r = client.put(
        f"/topics/{tid}/doc",
        json={
            "content": "# 目标\n做问答",
            "author": "cheese",
            "expected_version": stale,
        },
    )
    assert r.status_code == 409, r.text
    # The current version rides along, so a client can rebase without a re-read.
    assert r.json()["error"]["data"]["doc_version"] == stale + 1
    # Nothing of the refused write survived anywhere.
    doc = client.get(f"/topics/{tid}/doc").json()["data"]
    assert doc["content"] == "# 目标\n做推荐\n\n先跑通召回"
    assert doc["doc_version"] == stale + 1


def test_a_refused_write_announces_nothing(client):
    """The '编辑了文档' event and the doc's node tree are effects of a write that
    happened. A rejected save that still emitted them would put a change in the
    room, and an anchor in the tree, that is in no version of the document."""
    tid = _topic(client)
    client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 甲", "author": "user-1", "expected_version": 0},
    )
    before = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]

    r = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 乙", "author": "cheese", "expected_version": 0},
    )
    assert r.status_code == 409

    after = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    assert [b["id"] for b in after] == [b["id"] for b in before]
    nodes = client.get(f"/topics/{tid}/docs").json()["data"]["data"]
    assert [n["content"] for n in nodes] == ["# 甲"]


def test_creating_the_first_doc_expects_no_doc(client):
    """0 is a version like any other: it says "there is nothing here yet". A
    writer that got that answer minutes ago must not create the doc over one
    somebody else created since."""
    tid = _topic(client)
    first = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 甲", "author": "user-1", "expected_version": 0},
    )
    assert first.status_code == 200
    assert first.json()["data"]["doc_version"] == 1

    second = client.put(
        f"/topics/{tid}/doc",
        json={"content": "# 乙", "author": "cheese", "expected_version": 0},
    )
    assert second.status_code == 409
    assert client.get(f"/topics/{tid}/doc").json()["data"]["content"] == "# 甲"
