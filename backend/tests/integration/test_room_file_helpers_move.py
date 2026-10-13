"""房间文件那几个 helper 各回各家之后，六条路由一字不差（Part of #2143 pilot 2）。

这不是行为负控 —— 负控要证明「实现改坏了会红」。这里证明的是「同一份种子，
迁移前后得到同一份响应」：字段对字段，含发出去的那一帧、写出去的那一份，以及
广播先于提交的那次落库。先在旧代码上跑绿，把 helper 搬进各自的领域再跑，还是
同一份断言。

最后几条守卫说的是行为本身，不是某一次迁移：绑错房间、卡没开分支、路径越界、
资料库路径不许当任务分支读、帧要在提交之前出去。
"""

import base64
import hashlib
import io
import uuid
import zipfile

import pytest

from app.core.config import settings
from app.domain.documents import catalogue
from app.domain.library import service as library
from app.domain.room_task.models import Task
from app.domain.textfile import content_version
from tests.delivery import delivery_task_id
from tests.integration.conftest import (
    post_project,
    room_socket,
    session_auth_headers,
)
from tests.integration.test_file_panel_safety import (  # noqa: F401
    _mktopic,
    _put,
    task_machine,
)

#: The exact card an artifact block serialises to. A move that changed the wire
#: would add or drop a key here, which is the first thing this pins.
CARD_KEYS = {
    "author",
    "author_type",
    "content",
    "created_at",
    "id",
    "kind",
    "meta",
    "mime_type",
    "reactions",
    "refs",
    "reply_to",
    "conversation_id",
    "seq",
    "turn_id",
}


@pytest.fixture(autouse=True)
def content_domain(monkeypatch):
    monkeypatch.setattr(settings, "sites_domain", "content.example.com")
    monkeypatch.setattr(settings, "sites_scheme", "https")


@pytest.fixture(autouse=True)
def _editor_on(monkeypatch):
    monkeypatch.setattr(settings, "office_editor_jwt_secret", "test-editor-secret")


def _room(client, owner: str = "alice") -> tuple[str, str]:
    p = post_project(client, json={"name": "P"}, owner=owner).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return p["id"], t["id"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _docx() -> bytes:
    decl = "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    openxml = "http://schemas.openxmlformats.org"
    when = 'w:author="芝士" w:date="2026-09-18T02:00:00Z"'
    document = (
        f"{decl}<w:document {w}><w:body>\n"
        f'<w:p><w:r><w:t xml:space="preserve">合同期限为 </w:t></w:r>'
        f'<w:del w:id="1" {when}><w:r><w:delText>30</w:delText></w:r></w:del>'
        f'<w:ins w:id="2" {when}><w:r><w:t>60</w:t></w:r></w:ins>'
        f'<w:r><w:t xml:space="preserve"> 天。</w:t></w:r></w:p>\n'
        "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:

        def put(name: str, data: str) -> None:
            # A fixed timestamp on purpose: bare ``writestr`` stamps the current
            # ZIP time, and callers compare whole bytes across two ``_docx()``
            # calls -- two calls that straddle a two-second boundary would then
            # differ for no product reason at all. The assertions still compare
            # real bytes; only the clock is gone.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            package.writestr(info, data)

        put(
            "[Content_Types].xml",
            f'{decl}<Types xmlns="{openxml}/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.'
            'openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml"'
            ' ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        put(
            "_rels/.rels",
            f'{decl}<Relationships xmlns="{openxml}/package/2006/relationships">'
            '<Relationship Id="rId1"'
            f' Type="{openxml}/officeDocument/2006/relationships/officeDocument"'
            ' Target="word/document.xml"/>'
            "</Relationships>",
        )
        put("word/document.xml", document)
    return buffer.getvalue()


# --- the six callers' routes, field for field ---------------------------------


def test_shown_registers_lists_and_broadcasts_the_same_card(client):
    _pid, tid = _room(client)
    with room_socket(client, tid, "alice") as ws:
        r = client.post(
            f"/topics/{tid}/shown",
            headers=session_auth_headers("alice"),
            json={"path": "报告.html", "content": "<h1>hi</h1>"},
        )
        frame = ws.receive_json()

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 200 and body["message"] == "ok"
    card = body["data"]
    assert set(card) == CARD_KEYS
    assert card["kind"] == "artifact"
    assert card["author_type"] == "participant"
    assert card["author"].startswith("cheese-")
    assert card["content"] == "报告.html"
    assert card["mime_type"] == "text/html"
    assert card["refs"] == ["报告.html"]
    assert card["conversation_id"] == tid
    assert card["meta"] is None and card["turn_id"] is None
    assert card["reactions"] == []
    assert card["created_at"]
    # 摆出来就是房间里说了一句话: the frame carries the very same card.
    assert frame == {"type": "assistant_block", "block": card}

    # …and the write is committed by the time the request answers.
    listed = client.get(f"/topics/{tid}/shown").json()["data"]
    assert listed["total"] == 1
    assert listed["data"][0]["path"] == "报告.html"
    assert listed["data"][0]["mime"] == "text/html"
    assert listed["data"][0]["kind"] == "file"


def test_shown_save_keeps_the_same_name(client):
    _pid, tid = _room(client)
    client.post(
        f"/topics/{tid}/shown",
        headers=session_auth_headers("alice"),
        json={"path": "报告.html", "content": "<h1>hi</h1>"},
    )
    r = client.post(f"/topics/{tid}/shown/save", json={"path": "报告.html"})
    assert r.status_code == 200, r.text
    assert r.json() == {"code": 200, "message": "ok", "data": {"name": "报告.html"}}


def test_preview_reads_the_same_bytes(client):
    pid, tid = _room(client)
    pid_u, tid_u = uuid.UUID(pid), uuid.UUID(tid)
    library.write_room_file(pid_u, tid_u, "site/报告.html", b"<b>x</b>")
    client.post(
        f"/topics/{tid}/shown",
        headers=session_auth_headers("alice"),
        json={"path": "site/报告.html"},
    )

    r = client.get(f"/topics/{tid}/preview")
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["kind"] == "file"
    assert data["path"] == "site/报告.html"
    assert data["mime"] == "text/html"
    assert data["artifact_id"]

    r = client.get(
        f"/topics/{tid}/preview/file",
        headers=session_auth_headers("alice"),
        params={"path": "site/报告.html"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {
        "binary": False,
        "bytes": 8,
        "content": "<b>x</b>",
        "path": "site/报告.html",
        "too_large": False,
        "version": content_version(b"<b>x</b>"),
    }


def test_attachments_upload_library_and_raw_read_the_same(client):
    pid, tid = _room(client)
    pid_u, tid_u = uuid.UUID(pid), uuid.UUID(tid)

    uploaded = client.post(
        f"/topics/{tid}/attachments",
        files={"file": ("shot.png", b"\x89PNG\r\n\x1a\n0000", "image/png")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["data"] == {
        "bytes": 12,
        "mime": "image/png",
        "path": "library/shot.png",
    }

    given = client.post(
        f"/projects/{pid}/library",
        files={"file": ("原件.png", b"\x89PNG-library", "image/png")},
    )
    assert given.status_code == 200, given.text
    named = client.post(f"/topics/{tid}/attachments", data={"library_path": "原件.png"})
    assert named.status_code == 200, named.text
    assert named.json()["data"] == {
        "bytes": 12,
        "mime": "image/png",
        "path": "library/原件.png",
    }

    library.write_room_file(pid_u, tid_u, "shot.png", b"\x89PNG-room")
    raw = client.get(
        f"/topics/{tid}/attachments/raw",
        params={"path": "shot.png", "download": "true"},
    )
    assert raw.status_code == 200, raw.text
    assert raw.content == b"\x89PNG-room"
    assert raw.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''shot.png"
    )
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["cache-control"] == "private, max-age=3600"


def test_attachments_pdf_converts_the_same(client, monkeypatch):
    pid, tid = _room(client)
    pid_u, tid_u = uuid.UUID(pid), uuid.UUID(tid)
    library.write_room_file(pid_u, tid_u, "评审简报.docx", _docx())

    import app.api.routes.topics_attachments as attachments

    seen: dict = {}

    async def fake_render(data, path, endpoint, timeout=90.0):
        seen["data"], seen["path"] = data, path
        return b"%PDF-1.7 converted"

    monkeypatch.setattr(attachments, "render_to_pdf", fake_render)

    r = client.get(f"/topics/{tid}/attachments/pdf", params={"path": "评审简报.docx"})
    assert r.status_code == 200, r.text
    assert r.content == b"%PDF-1.7 converted"
    assert seen["path"] == "评审简报.docx"
    assert seen["data"] == _docx()


def test_documents_recalc_and_convert_read_the_same(client, monkeypatch):
    _pid, tid = _room(client)

    import app.api.routes.topics_documents as documents

    async def fake_recalc(raw, path, endpoint, timeout=90.0):
        assert raw == b"PK-xlsx"
        return b"PK-recalc", []

    async def fake_convert(raw, path, target, endpoint, timeout=90.0):
        assert (raw, path, target) == (b"PK-xlsx", "旧表.doc", "xlsx")
        return b"PK-converted"

    monkeypatch.setattr(documents, "recalculate", fake_recalc)
    monkeypatch.setattr(documents, "convert", fake_convert)
    body = {
        "path": "预算.xlsx",
        "content_b64": base64.b64encode(b"PK-xlsx").decode(),
    }

    recalc = client.post(f"/topics/{tid}/documents/recalc", json=body)
    assert recalc.status_code == 200, recalc.text
    assert recalc.json()["data"] == {
        "content_b64": "UEstcmVjYWxj",
        "errors": [],
        "path": "预算.xlsx",
    }

    convert = client.post(
        f"/topics/{tid}/documents/convert",
        json={**body, "path": "旧表.doc", "to": "xlsx"},
    )
    assert convert.status_code == 200, convert.text
    assert convert.json()["data"] == {
        "content_b64": "UEstY29udmVydGVk",
        "path": "旧表.xlsx",
    }


def test_documents_revisions_read_and_decide_the_same(client, monkeypatch):
    pid, tid = _room(client)
    pid_u, tid_u = uuid.UUID(pid), uuid.UUID(tid)
    library.write_room_file(pid_u, tid_u, "合同.docx", _docx())

    import app.api.routes.topics_documents as documents

    async def fake_convert(raw, path, target, endpoint, timeout=90.0):
        return raw

    monkeypatch.setattr(documents, "convert", fake_convert)

    listing = client.get(
        f"/topics/{tid}/documents/revisions", params={"path": "合同.docx"}
    )
    assert listing.status_code == 200, listing.text
    body = listing.json()["data"]
    assert body["path"] == "合同.docx"
    assert body["version"] == content_version(_docx())
    assert body["revisions"] == [
        {
            "added": "60",
            "author": "芝士",
            "date": "2026-09-18T02:00:00Z",
            "kind": "replace",
            "number": 1,
            "paragraph": 0,
            "removed": "30",
        }
    ]

    decided = client.post(
        f"/topics/{tid}/documents/revisions",
        json={
            "path": "合同.docx",
            "accept": [1],
            "version": body["version"],
        },
    )
    assert decided.status_code == 200, decided.text
    out = decided.json()["data"]
    assert out["path"] == "合同.docx"
    assert out["revisions"] == []
    assert out["version"] != body["version"]
    # The decision was written back into the room's file.
    back = client.get(f"/topics/{tid}/files/raw", params={"path": "合同.docx"})
    assert back.status_code == 200, back.text
    assert b"60" in back.content


def test_room_files_new_raw_revisions_restore_copy_and_editor(client):
    _pid, tid = _room(client)
    template = catalogue.TEMPLATES[0]
    target = f"文档/{template.name}.{template.suffix}"

    made = client.post(
        f"/topics/{tid}/files/new", json={"template": template.id, "path": target}
    )
    assert made.status_code == 200, made.text
    version = made.json()["data"]["version"]
    assert made.json()["data"] == {"path": target, "version": version}

    raw = client.get(f"/topics/{tid}/files/raw", params={"path": target})
    assert raw.status_code == 200, raw.text
    assert raw.content == catalogue.template_bytes(template)
    assert raw.headers["x-cheese-version"] == version
    assert raw.headers["content-type"] == "application/octet-stream"

    history = client.get(f"/topics/{tid}/files/revisions", params={"path": target})
    assert history.status_code == 200, history.text
    rows = history.json()["data"]["data"]
    assert len(rows) == 1
    assert rows[0]["source"] == "template"
    assert rows[0]["note"] == f"从「{template.name}」模板新建"
    assert rows[0]["size"] == len(raw.content)
    assert rows[0]["version"] == version

    restored = client.post(f"/topics/{tid}/files/revisions/{rows[0]['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["version"] == version

    copied = client.post(
        f"/topics/{tid}/files/copy",
        json={"source": target, "path": "文档/副本" + f".{template.suffix}"},
    )
    assert copied.status_code == 200, copied.text
    assert copied.json()["data"] == {
        "path": "文档/副本" + f".{template.suffix}",
        "version": version,
    }

    editor = client.get(f"/topics/{tid}/files/editor", params={"path": target})
    assert editor.status_code == 200, editor.text
    config = editor.json()["data"]
    assert config["enabled"] is True
    assert config["editable"] is True
    assert config["version"] == version
    assert config["config"]["document"]["fileType"] == template.suffix
    assert config["config"]["document"]["title"] == target.rsplit("/", 1)[-1]


def test_a_task_bound_and_a_committed_source_read_the_same(client, task_machine):  # noqa: F811
    from tests.integration.conftest import post_project as _post_project
    from tests.machine_work import machine_commits
    from tests.support import git_store

    pid = uuid.UUID(
        _post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    )
    tid = str(_mktopic(client, pid))
    task = delivery_task_id(client, tid)

    _put(client, pid, uuid.UUID(tid), "output/图.png", b"\x89PNG-live")
    live = client.get(
        f"/topics/{tid}/attachments/raw",
        params={"path": "output/图.png", "task": str(task)},
    )
    assert live.status_code == 200, live.text
    assert live.content == b"\x89PNG-live"
    assert live.headers["cache-control"] == "no-store"

    machine_commits(pid, task, {"docs/readme.md": "# committed\n"})
    git_store.merge_task(pid, task, message="merge")
    committed = client.get(
        f"/topics/{tid}/attachments/raw",
        params={"path": "docs/readme.md", "source": "committed", "download": "true"},
    )
    assert committed.status_code == 200, committed.text
    assert committed.content == b"# committed\n"
    assert committed.headers["cache-control"] == "no-store"


# --- guards: the behaviours the move had to leave exactly as they were ---------


def test_binding_refuses_a_card_from_another_room(client, task_machine):  # noqa: F811
    from tests.integration.conftest import post_project as _post_project

    pid = uuid.UUID(
        _post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    )
    home = str(_mktopic(client, pid))
    elsewhere = str(_mktopic(client, pid))
    task = delivery_task_id(client, home)

    r = client.get(
        f"/topics/{elsewhere}/attachments/raw",
        params={"path": "x.png", "task": str(task)},
    )
    assert r.status_code == 404, r.text
    assert "Task not found" in r.json()["message"]


def test_binding_refuses_a_card_that_has_no_branch(client, task_machine):  # noqa: F811
    from tests.integration.conftest import post_project as _post_project

    pid = uuid.UUID(
        _post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    )
    tid = str(_mktopic(client, pid))
    task = delivery_task_id(client, tid)

    async def _clear() -> None:
        async with client.test_factory() as session:
            row = await session.get(Task, task)
            row.branch_name = None
            await session.commit()

    client.portal.call(_clear)

    r = client.get(
        f"/topics/{tid}/attachments/raw", params={"path": "x.png", "task": str(task)}
    )
    assert r.status_code == 404, r.text
    assert "Task not found" in r.json()["message"]


@pytest.mark.parametrize("bad", ["../etc/passwd", "/etc/passwd", ".git/config"])
def test_path_rules_still_reject_traversal_absolute_and_git(client, bad):
    _pid, tid = _room(client)
    r = client.post(
        f"/topics/{tid}/shown",
        headers=session_auth_headers("alice"),
        json={"path": bad},
    )
    assert r.status_code == 422, (bad, r.text)
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None


def test_a_library_path_is_not_read_from_a_task_branch(client, task_machine):  # noqa: F811
    from tests.integration.conftest import post_project as _post_project

    pid = uuid.UUID(
        _post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    )
    tid = str(_mktopic(client, pid))
    task = delivery_task_id(client, tid)

    r = client.get(
        f"/topics/{tid}/attachments/raw",
        params={"path": "library/原件.png", "task": str(task)},
    )
    assert r.status_code == 422, r.text
    assert "资料库里的文件不属于某个任务分支" in r.text


def test_the_frame_goes_out_before_the_caller_commits(client, monkeypatch):
    """The card is broadcast first; a broker that refuses takes the write with
    it. If the handler committed before publishing, the block would survive the
    failed publish — and nobody in the room would ever have seen it."""
    pid, tid = _room(client)

    import app.api.routes.topics as topics
    import app.api.routes.topics_shown as shown
    from app.core.errors import ValidationError

    class _RefusingBroker:
        async def publish(self, channel, payload):
            # A real response, not a crash: TestClient re-raises server errors,
            # and a 422 is what the app's own handler makes of this refusal.
            raise ValidationError("broker down")

    monkeypatch.setattr(topics, "get_broker", lambda: _RefusingBroker())
    monkeypatch.setattr(shown, "get_broker", lambda: _RefusingBroker(), raising=False)

    r = client.post(
        f"/topics/{tid}/shown",
        headers=session_auth_headers("alice"),
        json={"path": "报告.html", "content": "<h1>hi</h1>"},
    )
    assert r.status_code != 200, r.text
    assert client.get(f"/topics/{tid}/shown").json()["data"]["total"] == 0
    assert client.get(f"/topics/{tid}/preview").json()["data"] is None
