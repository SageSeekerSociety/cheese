"""图片输入 (chat image attachments): upload → attachment block → 芝士 sees it.

The image is a REAL worktree file (所有产出都是 git): POST /attachments writes
the bytes under uploads/, the WS message references it, the timeline gains an
attachment block, and the agent prompt points 芝士 at the file (its sandbox
Read tool is image-capable).
"""

# A valid 1x1 transparent PNG (67 bytes) — small but real image bytes.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _create_project_and_topic(client) -> tuple[str, str]:
    pr = client.post("/api/projects", json={"name": "Demo"})
    project_id = pr.json()["data"]["id"]
    tr = client.post(
        "/api/topics", json={"project_id": project_id, "title": "图片话题"}
    )
    return project_id, tr.json()["data"]["id"]


def _upload(client, topic_id: str) -> dict:
    r = client.post(
        f"/api/topics/{topic_id}/attachments",
        files={"file": ("screenshot.png", PNG_1PX, "image/png")},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _drain_until_done(ws) -> list[dict]:
    frames: list[dict] = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("done", "error"):
            break
    return frames


def test_upload_then_raw_roundtrip(client):
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)
    assert att["mime"] == "image/png"
    assert att["path"].startswith("uploads/img-")
    assert att["path"].endswith(".png")

    raw = client.get(
        f"/api/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.content == PNG_1PX


def test_upload_rejects_non_image(client):
    _, topic_id = _create_project_and_topic(client)
    r = client.post(
        f"/api/topics/{topic_id}/attachments",
        files={"file": ("evil.html", b"<script>1</script>", "text/html")},
    )
    assert r.status_code == 422


def test_raw_rejects_non_image_and_traversal(client):
    _, topic_id = _create_project_and_topic(client)
    _upload(client, topic_id)  # ensure the worktree exists
    for bad in ("uploads/../secret.txt", "docs/readme.md"):
        r = client.get(
            f"/api/topics/{topic_id}/attachments/raw", params={"path": bad}
        )
        assert r.status_code == 422, bad


def test_message_with_attachment_creates_block_and_prompts_agent(
    client, stub_agent
):
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)

    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "看看这张截图",
                "author": "user-1",
                "summon": True,
                "attachments": [att],
            }
        )
        frames = _drain_until_done(ws)

    # Both the text block and the attachment block stream as user_block frames.
    user_frames = [f["block"] for f in frames if f["type"] == "user_block"]
    assert [b["kind"] for b in user_frames] == ["message", "attachment"]
    att_block = user_frames[1]
    assert att_block["content"] == att["path"]
    assert att_block["mime_type"] == "image/png"
    assert att_block["author_type"] == "human"

    # The prompt tells 芝士 the image is attached INLINE (images= carries the
    # content to the model) and where the file lives in its workspace.
    prompt = stub_agent.last_prompt or ""
    assert "[user-1]: 看看这张截图" in prompt
    assert att["path"] in prompt
    assert "已附在本条消息里" in prompt

    # Persisted in the timeline: message + attachment + AI reply.
    blocks = client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]
    assert [b["kind"] for b in blocks] == ["message", "attachment", "message"]


def test_image_only_message_allowed(client, stub_agent):
    """A send with no text but an image still posts and reaches 芝士."""
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)

    with client.websocket_connect(f"/api/topics/{topic_id}/chat") as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "",
                "author": "user-1",
                "summon": True,
                "attachments": [att],
            }
        )
        frames = _drain_until_done(ws)

    user_frames = [f["block"] for f in frames if f["type"] == "user_block"]
    assert [b["kind"] for b in user_frames] == ["attachment"]
    assert (stub_agent.last_prompt or "").count(att["path"]) == 1

    # 芝士's reply threads under the attachment block (the turn's anchor).
    assistant = next(f for f in frames if f["type"] == "assistant_block")["block"]
    assert assistant["reply_to"] == user_frames[0]["id"]
