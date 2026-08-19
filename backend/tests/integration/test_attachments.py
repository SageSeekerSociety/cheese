"""图片输入 (chat image attachments): upload → attachment block → 芝士 sees it.

The image is a REAL worktree file (所有产出都是 git): POST /attachments writes
the bytes under uploads/, the WS message references it, the timeline gains an
attachment block, and the agent prompt points 芝士 at the file (its sandbox
Read tool is image-capable).
"""

from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.main import app
from tests.conftest import StubAgent, StubHooksProvider
from tests.integration.conftest import chat_ws_url

# A valid 1x1 transparent PNG (67 bytes) — small but real image bytes.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _create_project_and_topic(client) -> tuple[str, str]:
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
    assert att["path"].startswith("uploads/img-")
    assert att["path"].endswith(".png")

    raw = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.content == PNG_1PX


def test_upload_rejects_non_image(client):
    _, topic_id = _create_project_and_topic(client)
    r = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("evil.html", b"<script>1</script>", "text/html")},
    )
    assert r.status_code == 422


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


class _NoEmbedScreen(StubHooksProvider):
    """The same screen, declaring it cannot carry image bytes."""

    name = "no-embed"
    embeds_images = False


def _run_on_non_embedding_backend(client, tmp_path) -> _NoEmbedScreen:
    """Point this client's next turn at a provider that cannot embed images,
    and hand back the screen the prompt will actually reach."""
    screen = _NoEmbedScreen()
    service = ChatService(
        session_factory=client.test_factory,
        agent=StubAgent(),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([screen], screen.name),
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
