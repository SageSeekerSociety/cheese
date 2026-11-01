"""图片输入 (chat image attachments): upload → attachment block → 芝士 sees it.

POST /attachments 把字节收进项目的资料库，并在这个房间的文件区 uploads/ 下留一份。
消息引用的是后者：时间线上多一个 attachment block，提示词把 芝士 指向那个文件（沙箱
里的 Read 能看图）。资料库本身见 test_library.py。
"""

import base64
import threading
import time
import uuid
from urllib.parse import quote

import pytest

from app.api import deps as session_turn_deps
from app.api.deps import get_chat_service
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.library import service as library
from app.main import app
from tests.ask_fixtures import wait_turn_idle
from tests.conftest import StubChannel
from tests.integration.conftest import (
    in_thread,
    post_message,
    post_project,
    room_agent_seat,
    room_socket,
    session_auth_headers,
)
from tests.support.pictures import BROKEN_PNG

# A valid 1x1 transparent PNG (67 bytes) — small but real image bytes.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _create_project_and_topic(client) -> tuple[str, str]:
    client.headers.update(session_auth_headers("user-1"))
    pr = post_project(client, json={"name": "Demo"})
    project_id = pr.json()["data"]["id"]
    tr = client.post(
        "/topics",
        json={"project_id": project_id, "title": "图片话题"},
    )
    return project_id, tr.json()["data"]["id"]


def _create_project_and_thread(client) -> tuple[str, str]:
    """A project and a 支线 in its channel: where 芝士 answers when called."""
    project_id, topic_id = _create_project_and_topic(client)
    return project_id, in_thread(client, topic_id, "user-1")


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
    # 发进来的那一份留在这段对话里（#3828），不进资料库。
    assert att["path"].startswith("uploads/")
    assert att["path"].endswith("/screenshot.png")

    raw = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw.content == PNG_1PX
    # 随机串把这个地址钉死在这一次上传上，字节不会再变，浏览器可以留着。另外两种
    # 地址的规矩见下面那条测试。
    assert raw.headers["cache-control"] == "private, max-age=3600"


def test_a_room_file_that_can_change_is_never_left_to_the_browsers_cache(client):
    """房间里同一份文件重做过之后，那个地址下的字节变了，浏览器不许自己答「没变」。

    芝士把一版重做完再摆出来（`POST /topics/{id}/shown`），路径还是那一个。留下
    一小时的话，新的一版摆出来之后整整一小时里，点开画的还是旧的——2026-10-15
    报的「同名文件显示不了新改动」就是这个。贴进来的那一份相反：`uploads/<随机串>/
    <名字>` 里的随机串把它钉死在一次粘贴上，字节写完不会再变，留一小时是有意的。
    """
    _, topic_id = _create_project_and_topic(client)

    for png in (PNG_1PX, PNG_1PX + b"\x00"):
        shown = client.post(
            f"/topics/{topic_id}/shown",
            json={"path": "预览图.png", "content_b64": base64.b64encode(png).decode()},
        )
        assert shown.status_code == 200, shown.text

    raw = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": "预览图.png"}
    )
    assert raw.status_code == 200, raw.text
    assert raw.content == PNG_1PX + b"\x00"
    assert raw.headers["cache-control"] == "private, no-cache"

    pasted = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("shot.png", PNG_1PX, "image/png")},
        data={"origin": "clipboard"},
    )
    assert pasted.status_code == 200, pasted.text
    path = pasted.json()["data"]["path"]
    assert path.startswith("uploads/")
    kept = client.get(f"/topics/{topic_id}/attachments/raw", params={"path": path})
    assert kept.status_code == 200, kept.text
    assert kept.headers["cache-control"] == "private, max-age=3600"


def test_video_can_be_referenced_and_seeked_without_opening_active_files(client):
    _, topic_id = _create_project_and_topic(client)
    content = b"0123456789"
    uploaded = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("screen.MP4", content, "video/mp4")},
    )
    assert uploaded.status_code == 200, uploaded.text
    att = uploaded.json()["data"]
    assert att["mime"] == "video/mp4"

    inline = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert inline.status_code == 200
    assert inline.headers["content-type"].startswith("video/mp4")
    assert inline.headers["accept-ranges"] == "bytes"
    assert inline.content == content

    partial = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": att["path"]},
        headers={"Range": "bytes=3-6"},
    )
    assert partial.status_code == 206
    assert partial.headers["content-range"] == "bytes 3-6/10"
    assert partial.content == b"3456"

    saved = client.post(f"/topics/{topic_id}/shown/save", json={"path": att["path"]})
    assert saved.status_code == 200, saved.text
    library_ref = client.post(
        f"/topics/{topic_id}/attachments",
        data={"library_path": saved.json()["data"]["name"]},
    )
    assert library_ref.status_code == 200, library_ref.text
    assert library_ref.json()["data"]["mime"] == "video/mp4"

    for filename, mime in (("clip.html", "text/html"), ("clip.svg", "image/svg+xml")):
        uploaded_bad = client.post(
            f"/topics/{topic_id}/attachments",
            files={"file": (filename, b"not a media document", mime)},
        ).json()["data"]
        blocked = client.get(
            f"/topics/{topic_id}/attachments/raw",
            params={"path": uploaded_bad["path"]},
        )
        assert blocked.status_code == 422


# 压小的两道上限：「超过」与那个数多大无关，而两百兆的请求在测试里发不动。整 MB，
# 报出去的那个数才认得出来。
SMALL_FILE_CEILING = 1024 * 1024
SMALL_VIDEO_CEILING = 2 * 1024 * 1024


def test_a_video_gets_the_video_ceiling_and_every_other_file_keeps_its_own(
    client, monkeypatch
):
    """手机录屏动辄几十上百 MB：视频按视频的上限收，别的文件照旧，拒的时候说的是
    这一类自己的上限。"""
    monkeypatch.setattr(library, "MAX_FILE_BYTES", SMALL_FILE_CEILING)
    monkeypatch.setattr(library, "MAX_VIDEO_BYTES", SMALL_VIDEO_CEILING)
    project_id, topic_id = _create_project_and_topic(client)
    over_general = b"x" * (SMALL_FILE_CEILING + 1)

    refused = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("report.pdf", over_general, "application/pdf")},
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["i18n"] == {
        "key": "fileTooLarge",
        "params": {"size": "1 MB"},
    }

    # An iPhone records HEVC in a QuickTime container: `.MOV`, video/quicktime.
    for filename, mime in (
        ("screen.mp4", "video/mp4"),
        ("IMG_0001.MOV", "video/quicktime"),
    ):
        taken = client.post(
            f"/topics/{topic_id}/attachments",
            files={"file": (filename, over_general, mime)},
        )
        assert taken.status_code == 200, taken.text
        att = taken.json()["data"]
        assert att["mime"] == mime
        assert att["bytes"] == len(over_general)
        played = client.get(
            f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
        )
        assert played.status_code == 200
        assert played.headers["content-type"] == mime
        assert played.content == over_general

    too_long = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("long.mp4", b"x" * (SMALL_VIDEO_CEILING + 1), "video/mp4")},
    )
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["error"]["i18n"] == {
        "key": "fileTooLarge",
        "params": {"size": "2 MB"},
    }


def test_the_player_streams_a_video_with_the_cookie_its_first_read_left(client):
    """`<video>` 自己发的按段请求带不了 Authorization：带着头读过一次之后，这个房间
    的这条路上留下一个 cookie，播放器凭它接着按段读；它只在这条路上作数。"""
    _, topic_id = _create_project_and_topic(client)
    content = b"0123456789"
    att = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("screen.mov", content, "video/quicktime")},
    ).json()["data"]
    raw = f"/topics/{topic_id}/attachments/raw"

    first = client.get(
        raw, params={"path": att["path"]}, headers={"Range": "bytes=0-0"}
    )
    assert first.status_code == 206
    assert first.content == b"0"
    cookie = first.headers["set-cookie"]
    assert f"Path=/api/topics/{topic_id}/attachments/raw" in cookie
    assert "HttpOnly" in cookie
    name, value = cookie.split(";", 1)[0].split("=", 1)

    client.headers.clear()
    client.cookies.clear()
    without = client.get(raw, params={"path": att["path"]})
    assert without.status_code in (401, 403)

    seek = client.get(
        raw,
        params={"path": att["path"]},
        headers={"Range": "bytes=3-6", "Cookie": f"{name}={value}"},
    )
    assert seek.status_code == 206
    assert seek.headers["content-range"] == "bytes 3-6/10"
    assert seek.content == b"3456"

    # The same cookie opens nothing else.
    elsewhere = client.get(f"/topics/{topic_id}", headers={"Cookie": f"{name}={value}"})
    assert elsewhere.status_code in (401, 403)


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
    # Room uploads are not files in the project's committed repository.
    assert workspace.status_code == 404


@pytest.mark.parametrize(
    ("filename", "content", "mime"),
    [
        ("paper.pdf", b"%PDF-1.7 test", "application/pdf"),
        ("source.zip", b"PK\x03\x04source", "application/zip"),
        ("notes.txt", b"read this on demand", "text/plain"),
        ("unknown.bin", b"unknown", "application/octet-stream"),
        ("drawing.svg", b"<svg/>", "image/svg+xml"),
        (
            "report.docx",
            b"PK\x03\x04docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
def test_document_reaches_agent_as_file(client, stub_hooks, filename, content, mime):
    _, topic_id = _create_project_and_thread(client)
    att = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": (filename, content, mime)},
    ).json()["data"]
    with room_socket(client, topic_id, "user-1") as ws:
        # 正文只有一个 @：点名写在正文里（I13），一份没点名的文件只是落在房间
        # 里。输入框上对着一个附件按 ⌘Enter，发出去的就是这一条。
        post_message(
            client, topic_id, "user-1", {"content": "@芝士", "attachments": [att]}
        )
        frames = _drain_until_done(ws)
    block = next(
        f["block"]
        for f in frames
        if f["type"] == "user_block" and f["block"]["kind"] == "attachment"
    )
    assert block["kind"] == "attachment"
    assert block["mime_type"] == att["mime"]
    assert att["path"] in (stub_hooks.last_prompt or "")
    assert "发来一个文件" in stub_hooks.last_prompt
    assert "发来一张图片" not in stub_hooks.last_prompt
    session = stub_hooks._session_for(uuid.UUID(topic_id))
    handed = [m for m in session.written if m.get("type") == "user"][-1]["message"]
    assert isinstance(handed["content"], str) or not any(
        item.get("type") == "image" for item in handed["content"]
    )
    raw = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={"path": att["path"], "download": "true"},
    )
    assert raw.content == content


# The Office types, whose MIME strings are the long ones: .docx is 71
# characters, .pptx 73, .xlsx 65. Uploading one always worked — what did not
# was SENDING it, because the attachment block's mime_type column was 64 wide
# and the insert rolled the whole message back. The person saw a bare
# 「消息未能保存,请重新发送」 and every retry failed identically.
OFFICE_MIMES = [
    (
        "说明书.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("表.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    (
        "讲稿.pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
]


@pytest.mark.parametrize(("filename", "mime"), OFFICE_MIMES)
def test_office_document_lands_in_the_room(client, stub_hooks, filename, mime):
    """发一个 .docx 进房间，它要真的出现在时间线上。"""
    _, topic_id = _create_project_and_thread(client)
    att = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": (filename, b"PK\x03\x04", mime)},
    ).json()["data"]
    with room_socket(client, topic_id, "user-1") as ws:
        post_message(
            client, topic_id, "user-1", {"content": "这是说明书", "attachments": [att]}
        )
        frames = _drain_until_done(ws)

    assert not [f for f in frames if f["type"] == "error"], frames
    # 正文和附件是两个 user_block —— 要的是附件那个。
    block = next(
        f["block"]
        for f in frames
        if f["type"] == "user_block" and f["block"]["kind"] == "attachment"
    )
    assert block["mime_type"] == mime

    # 时间线上真的有它 —— 上一句只证明后端回了一帧，这一句才证明它落了库。
    timeline = client.get(f"/topics/{topic_id}/blocks", params={"limit": 50})
    assert timeline.status_code == 200
    kinds = [
        b["content"]
        for b in timeline.json()["data"]["data"]
        if b["kind"] == "attachment"
    ]
    assert att["path"] in kinds


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
    """An image sent with a call to 芝士 in the main line reaches 芝士 in the
    message's 支线: the files stay with the message, and the 支线's first
    turn reads both."""
    _, topic_id = _create_project_and_topic(client)
    att = _upload(client, topic_id)

    with room_socket(client, topic_id, "user-1") as ws:
        asked = post_message(
            client,
            topic_id,
            "user-1",
            {"content": "@芝士 看看这张截图", "attachments": [att]},
        )
        user_frames = [ws.receive_json()["block"] for _ in range(2)]

    # Both the text block and the attachment block land in the main line.
    assert [b["kind"] for b in user_frames] == ["message", "attachment"]
    att_block = user_frames[1]
    assert att_block["content"] == att["path"]
    assert att_block["mime_type"] == "image/png"
    assert not att_block["author"].startswith("cheese")

    thread = client.post(f"/blocks/{asked['id']}/thread").json()["data"]["id"]
    deadline = time.monotonic() + 30
    while att["path"] not in (stub_hooks.last_prompt or ""):
        assert time.monotonic() < deadline, stub_hooks.last_prompt
        time.sleep(0.2)

    # The prompt tells 芝士 the image is attached INLINE (images= carries the
    # content to the model) and where the file lives in its workspace.
    prompt = stub_hooks.last_prompt or ""
    assert f"[user-1]: <@{room_agent_seat(client, topic_id)}> 看看这张截图" in prompt
    assert "已附在本条消息里" in prompt

    # The session hearing the prompt is not its answer having landed: what it
    # says comes back through the reader afterwards. Wait for the 支线's turn
    # to close before reading what it left there.
    wait_turn_idle(client, thread)
    main = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert [b["kind"] for b in main] == ["message", "attachment"]
    # The turn's record reaches the 支线 by its own path: the session's records
    # are read back and written after the prompt was handed over, so seeing the
    # prompt does not mean the record is there yet. Wait for it.
    while not (
        answer := client.get(f"/topics/{thread}/session-records").json()["data"][
            "items"
        ]
    ):
        assert time.monotonic() < deadline, "the 支线's turn left no record"
        time.sleep(0.2)
    assert any(item["record"].get("type") == "assistant" for item in answer)
    assert client.get(f"/topics/{thread}/blocks").json()["data"]["data"] == []


def test_image_only_message_allowed(client, stub_hooks):
    """一张图可以是这条消息的全部内容：正文只剩一个 @，照样到得了芝士。

    以前这条叫「没有正文」：召唤是帧上那一位说了算的，所以一张光秃秃的图自己就
    能把芝士叫起来。点名写进正文之后（I13），没点名的图只是落在房间里 —— 输入框
    上对着一张图按 ⌘Enter，发出去的正是下面这一条。
    """
    _, topic_id = _create_project_and_thread(client)
    att = _upload(client, topic_id)

    with room_socket(client, topic_id, "user-1") as ws:
        post_message(
            client, topic_id, "user-1", {"content": "@芝士", "attachments": [att]}
        )
        frames = _drain_until_done(ws)

    user_frames = [f["block"] for f in frames if f["type"] == "user_block"]
    assert [b["kind"] for b in user_frames] == ["message", "attachment"]
    # The line that tells 芝士 what was posted names the file, and the image
    # itself rides in the same message the session is handed.
    assert att["path"] in (stub_hooks.last_prompt or "")
    session = stub_hooks._session_for(uuid.UUID(topic_id))
    handed = [m for m in session.written if m.get("type") == "user"][-1]
    assert any(block.get("type") == "image" for block in handed["message"]["content"])

    # The execution record retains the attachment as its input anchor.
    output = client.get(f"/topics/{topic_id}/session-records").json()["data"]["items"]
    assert any(item["record"].get("type") == "assistant" for item in output)
    blocks = client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]
    assert {
        item["turn_id"] for item in output if item["record"].get("type") == "assistant"
    } == {blocks[0]["meta"]["consumed_turn"]}


@pytest.mark.parametrize("midturn", [False, True])
def test_mixed_files_only_embed_the_image(client, tmp_path, midturn):
    """The actual session input carries one image, not ZIP/PDF/unknown bytes,
    and not a PNG whose bytes do not decode — the browser calling it image/png
    at upload, or the message calling it that, changes nothing."""
    _, topic_id = _create_project_and_thread(client)
    image = _upload(client, topic_id)
    files = [
        client.post(
            f"/topics/{topic_id}/attachments",
            files={"file": (name, data, mime)},
        ).json()["data"]
        for name, data, mime in (
            ("source.zip", b"PK\x03\x04source", "application/zip"),
            ("paper.pdf", b"%PDF-1.7 test", "application/pdf"),
            ("broken.png", BROKEN_PNG, "image/png"),
            ("claimed.png", BROKEN_PNG, "image/png"),
            ("unknown.bin", b"unknown", "application/octet-stream"),
        )
    ]
    broken, claimed = files[2], files[3]
    # The upload keeps it, as a plain file.
    assert broken["mime"] == claimed["mime"] == "application/octet-stream"
    # A message may still call it a picture: a client's word is a claim too.
    claimed["mime"] = "image/png"
    # Legacy records can have no MIME; never guess image/png from their path.
    files[-1]["mime"] = ""

    class HeldScreen(StubChannel):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.injected = threading.Event()
            self.inputs = []

        def emit_turn(self, tid, prompt, reply, *, agent=None):
            self.inputs.append(prompt)
            self.starts(tid, agent=agent)
            self.acknowledges(tid, prompt, agent=agent)
            if not midturn:
                self.stops(tid, reply, agent=agent)
            elif len(self.inputs) == 1:
                self.started.set()
            else:
                self.injected.set()

    screen = HeldScreen()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([screen.runtime]),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    with room_socket(client, topic_id, "user-1") as ws:
        if midturn:
            post_message(client, topic_id, "user-1", {"content": "@芝士 等待"})
            assert screen.started.wait(5)
        post_message(
            client,
            topic_id,
            "user-1",
            {"content": "@芝士 看附件", "attachments": [image, *files]},
        )
        if midturn:
            try:
                assert screen.injected.wait(5)
            finally:
                screen.stops(uuid.UUID(topic_id), "完成")
        _drain_until_done(ws)

    session = screen._session_for(uuid.UUID(topic_id))
    handed = [m for m in session.written if m.get("type") == "user"][-1]["message"]
    image_blocks = [b for b in handed["content"] if b.get("type") == "image"]
    assert [
        (b["source"]["media_type"], base64.b64decode(b["source"]["data"]))
        for b in image_blocks
    ] == [("image/png", PNG_1PX)]
    prompt = screen.inputs[-1]
    assert "已附在本条消息里" in prompt
    for file in files:
        assert f"发来一个文件：{file['path']}" in prompt
    for file in (broken, claimed):
        line = next(x for x in prompt.splitlines() if file["path"] in x)
        assert "不要用 Read 打开它" in line


# --- 图片输入 on a backend that does NOT embed images -----------------------
# A backend that silently drops the image while the prompt insists it is
# attached is the worst shape available — 芝士 doesn't error, it writes a
# confident answer about a picture it never saw. So the capability is declared
# per backend, and the prompt's wording follows the declaration.


class _NoEmbedScreen(StubChannel):
    """The same screen, whose runtime declares it cannot carry image bytes."""

    name = "no-embed"

    def __init__(self, **policy: float) -> None:
        super().__init__(**policy)
        # The capability is the provider's, and the provider is the runtime.
        self.runtime.embeds_images = False


def _run_on_non_embedding_backend(client, tmp_path) -> _NoEmbedScreen:
    """Point this client's next turn at a provider that cannot embed images,
    and hand back the screen the prompt will actually reach."""
    screen = _NoEmbedScreen()
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
        compute=ComputePool([screen.runtime]),
    )
    app.dependency_overrides[get_chat_service] = lambda: service
    return screen


def test_prompt_does_not_claim_attachment_when_backend_drops_images(client, tmp_path):
    _, topic_id = _create_project_and_thread(client)
    att = _upload(client, topic_id)
    screen = _run_on_non_embedding_backend(client, tmp_path)

    with room_socket(client, topic_id, "user-1") as ws:
        post_message(
            client,
            topic_id,
            "user-1",
            {"content": "@芝士 看看这张截图", "attachments": [att]},
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
    _, topic_id = _create_project_and_thread(client)
    att = _upload(client, topic_id)

    with room_socket(client, topic_id, "user-1") as ws:
        post_message(
            client, topic_id, "user-1", {"content": "@芝士 看图", "attachments": [att]}
        )
        _drain_until_done(ws)

    prompt = stub_hooks.last_prompt or ""
    assert "已附在本条消息里" in prompt
    assert "没有附在本条消息里" not in prompt


def test_a_deleted_library_file_does_not_wedge_the_room(client, stub_hooks):
    """A message whose attachment has since left the library still gets its
    turn: the turn finishes, 芝士 is told the file is gone instead of being
    handed an image, and the next message is not held behind it."""
    project_id, topic_id = _create_project_and_thread(client)
    sent = _upload(client, topic_id)
    saved = client.post(f"/topics/{topic_id}/shown/save", json={"path": sent["path"]})
    assert saved.status_code == 200, saved.text
    att = {**sent, "path": "library/screenshot.png"}
    gone = client.delete(
        f"/projects/{project_id}/library", params={"path": "screenshot.png"}
    )
    assert gone.status_code == 200, gone.text

    with room_socket(client, topic_id, "user-1") as ws:
        post_message(
            client,
            topic_id,
            "user-1",
            {"content": "@芝士 看看这张截图", "attachments": [att]},
        )
        frames = _drain_until_done(ws)

    assert frames[-1]["type"] == "done", frames[-1]
    prompt = stub_hooks.last_prompt or ""
    assert att["path"] in prompt
    assert "已经不在了" in prompt
    assert "已附在本条消息里" not in prompt
    session = stub_hooks._session_for(uuid.UUID(topic_id))
    handed = [m for m in session.written if m.get("type") == "user"][-1]["message"]
    assert isinstance(handed["content"], str) or not any(
        block.get("type") == "image" for block in handed["content"]
    )

    with room_socket(client, topic_id, "user-1") as ws:
        post_message(client, topic_id, "user-1", {"content": "@芝士 那就先不看图了"})
        frames = _drain_until_done(ws)

    assert frames[-1]["type"] == "done", frames[-1]
    prompt = stub_hooks.last_prompt or ""
    assert "那就先不看图了" in prompt
    assert att["path"] not in prompt
