"""图片输入 (chat image attachments): upload → attachment block → 芝士 sees it.

The image is a REAL worktree file (所有产出都是 git): POST /attachments writes
the bytes under uploads/, the WS message references it, the timeline gains an
attachment block, and the agent prompt points 芝士 at the file (its sandbox
Read tool is image-capable).
"""

from urllib.parse import quote

import pytest

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.main import app
from tests.conftest import StubChannel
from tests.integration.conftest import chat_ws_url, session_auth_headers

# A valid 1x1 transparent PNG (67 bytes) — small but real image bytes.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _create_project_and_topic(client) -> tuple[str, str]:
    client.headers.update(session_auth_headers("user-1"))
    pr = client.post("/projects", json={"name": "Demo"})
    project_id = pr.json()["data"]["id"]
    tr = client.post(
        "/topics",
        json={"project_id": project_id, "title": "图片话题", "created_by": "user-1"},
    )
    return project_id, tr.json()["data"]["id"]


def _upload(client, topic_id: str) -> dict:
    r = client.post(
        f"/topics/{topic_id}/attachments",
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
    assert att["path"].startswith("uploads/")
    assert att["path"].endswith(".png")

    raw = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.content == PNG_1PX


@pytest.mark.parametrize(
    ("filename", "content", "mime"),
    [
        ("需求 文档.pdf", b"%PDF-1.7 test", "application/pdf"),
        (
            "report.docx",
            b"PK\x03\x04docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("data.csv", b"a,b\n1,2", "text/csv"),
        ("evil.html", b"<script>1</script>", "text/html"),
        ("drawing.svg", b"<svg onload='alert(1)'/>", "image/svg+xml"),
    ],
)
def test_upload_and_download_documents(client, filename, content, mime):
    project_id, topic_id = _create_project_and_topic(client)
    r = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": (filename, content, mime)},
    )
    assert r.status_code == 200, r.text
    att = r.json()["data"]
    assert att["path"].endswith("/" + filename)
    raw = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": att["path"], "download": "true"},
    )
    assert raw.status_code == 200
    assert raw.content == content
    assert raw.headers["content-type"] == "application/octet-stream"
    assert quote(filename, safe="") in raw.headers["content-disposition"]
    assert raw.headers["content-disposition"].startswith("attachment;")
    assert raw.headers["x-content-type-options"] == "nosniff"
    inline = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert inline.status_code == 422
    workspace = client.get(
        f"/projects/{project_id}/file/raw",
        params={"path": att["path"], "topic": topic_id, "download": "true"},
    )
    assert workspace.status_code == 200
    assert workspace.content == content
    assert workspace.headers["content-disposition"].startswith("attachment;")


def test_document_reaches_agent_as_file(client, stub_hooks):
    _, topic_id = _create_project_and_topic(client)
    att = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("paper.pdf", b"%PDF-1.7 test", "application/pdf")},
    ).json()["data"]
    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "",
                "summon": True,
                "attachments": [att],
            }
        )
        frames = _drain_until_done(ws)
    block = next(f["block"] for f in frames if f["type"] == "user_block")
    assert block["kind"] == "attachment"
    assert block["mime_type"] == "application/pdf"
    assert att["path"] in (stub_hooks.last_prompt or "")
    assert "发来一个文件" in stub_hooks.last_prompt
    assert "发来一张图片" not in stub_hooks.last_prompt


def test_duplicate_filename_and_upload_limits(client):
    _, topic_id = _create_project_and_topic(client)
    paths = []
    for content in (b"first", b"second"):
        att = client.post(
            f"/topics/{topic_id}/attachments",
            files={"file": ("same.txt", content, "text/plain")},
        ).json()["data"]
        paths.append(att["path"])
    assert paths[0] != paths[1]
    for path, content in zip(paths, (b"first", b"second"), strict=True):
        raw = client.get(
            f"/topics/{topic_id}/attachments/raw",
            params={"path": path, "download": "true"},
        )
        assert raw.content == content
    for content in (b"", b"x" * (10 * 1024 * 1024 + 1)):
        response = client.post(
            f"/topics/{topic_id}/attachments",
            files={"file": ("bad.pdf", content, "application/pdf")},
        )
        assert response.status_code == 422


def test_download_rejects_traversal_and_anonymous_access(client):
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)
    response = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": "../secret.txt", "download": "true"},
    )
    assert response.status_code == 422
    client.headers.clear()
    client.cookies.clear()
    response = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": att["path"], "download": "true"},
    )
    assert response.status_code in (401, 403, 404)
    client.headers.update(session_auth_headers("outsider"))
    response = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": att["path"], "download": "true"},
    )
    assert response.status_code == 403


def test_raw_rejects_non_image_and_traversal(client):
    _, topic_id = _create_project_and_topic(client)
    _upload(client, topic_id)  # ensure the worktree exists
    for bad in ("uploads/../secret.txt", "docs/readme.md"):
        r = client.get(f"/topics/{topic_id}/attachments/raw", params={"path": bad})
        assert r.status_code == 422, bad


def test_message_with_attachment_creates_block_and_prompts_agent(client, stub_hooks):
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "看看这张截图",
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
    prompt = stub_hooks.last_prompt or ""
    assert "[user-1]: 看看这张截图" in prompt
    assert att["path"] in prompt
    assert "已附在本条消息里" in prompt

    # Persisted in the timeline: message + attachment + AI reply.
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert [b["kind"] for b in blocks] == ["message", "attachment", "message"]


def test_image_only_message_allowed(client, stub_hooks):
    """A send with no text but an image still posts and reaches 芝士."""
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "",
                "summon": True,
                "attachments": [att],
            }
        )
        frames = _drain_until_done(ws)

    user_frames = [f["block"] for f in frames if f["type"] == "user_block"]
    assert [b["kind"] for b in user_frames] == ["attachment"]
    # Twice, and both are load-bearing: the line that tells 芝士 what was
    # posted, and the @-mention the screen resolves into the image itself.
    assert (stub_hooks.last_prompt or "").count(att["path"]) == 2

    # 芝士's reply threads under the attachment block (the turn's anchor).
    assistant = next(f for f in frames if f["type"] == "assistant_block")["block"]
    assert assistant["reply_to"] == user_frames[0]["id"]


# --- 图片输入 on a backend that does NOT embed images -----------------------
# A backend that silently drops the image while the prompt insists it is
# attached is the worst shape available — 芝士 doesn't error, it writes a
# confident answer about a picture it never saw. So the capability is declared
# per backend, and the prompt's wording follows the declaration.


class _NoEmbedScreen(StubChannel):
    """The same screen, declaring it cannot carry image bytes."""

    name = "no-embed"
    embeds_images = False


def _run_on_non_embedding_backend(client, tmp_path) -> _NoEmbedScreen:
    """Point this client's next turn at a provider that cannot embed images,
    and hand back the screen the prompt will actually reach."""
    screen = _NoEmbedScreen()
    service = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([screen.runtime], screen.name),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return screen


def test_prompt_does_not_claim_attachment_when_backend_drops_images(client, tmp_path):
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)
    screen = _run_on_non_embedding_backend(client, tmp_path)

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json(
            {
                "type": "message",
                "content": "看看这张截图",
                "summon": True,
                "attachments": [att],
            }
        )
        _drain_until_done(ws)

    prompt = screen.last_prompt or ""
    # The path stays — the file is real and 芝士 can open it with Read.
    assert att["path"] in prompt
    # ...but the turn must not claim the bytes rode along with the message,
    assert "已附在本条消息里" not in prompt
    # ...and must say the opposite plainly, so a turn that cannot open the file
    # reports that instead of inventing what the picture showed.
    assert "没有附在本条消息里" in prompt


def test_embedding_backend_still_says_the_image_is_attached(client, stub_hooks):
    """Per-provider capability, not a global downgrade: a screen resolves an
    @-mentioned path into a native image block, so it must keep telling 芝士
    the image is inline."""
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)

    with client.websocket_connect(chat_ws_url(topic_id, "user-1")) as ws:
        ws.send_json(
            {"type": "message", "content": "看图", "summon": True, "attachments": [att]}
        )
        _drain_until_done(ws)

    prompt = stub_hooks.last_prompt or ""
    assert "已附在本条消息里" in prompt
    assert "没有附在本条消息里" not in prompt
