"""房间文件的草稿历史，和在线编辑器把人的修改存回来的那条路。

规则都是用户说得出来的：
- 每保存一次都留一版，之前的哪一版都能取回来；恢复本身也是新的一版，不抹掉历史。
- 芝士按旧内容改完再写回，而这期间有人存过 —— 不能直接盖掉，要报冲突。
- 编辑器的保存回调要有编辑器的签名；没签名的请求一个字节也写不进房间。
- 编辑器保存时文件已被别人改过：两份都留，谁的都不丢。
"""

import base64

import jwt
import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.documents import editor
from app.domain.textfile import content_version
from tests.integration.conftest import post_project

SECRET = "test-office-editor-secret"


@pytest.fixture(autouse=True)
def _editor_on(monkeypatch):
    monkeypatch.setattr(settings, "office_editor_jwt_secret", SECRET)


def _room(client) -> tuple[str, str]:
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post(
        "/topics", json={"project_id": project, "title": "改一份报告"}
    ).json()["data"]["id"]
    return project, room


def _agent(project: str, room: str) -> dict:
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project, topic_id=room, ttl_s=3600
        )
    }


def _show(client, project, room, data: bytes, **extra):
    return client.post(
        f"/topics/{room}/shown",
        headers=_agent(project, room),
        json={
            "path": "output/报告.docx",
            "content_b64": base64.b64encode(data).decode(),
            **extra,
        },
    )


def _current(client, room) -> bytes:
    r = client.get(f"/topics/{room}/files/raw", params={"path": "output/报告.docx"})
    assert r.status_code == 200, r.text
    return r.content


def _history(client, room, path="output/报告.docx") -> list[dict]:
    r = client.get(f"/topics/{room}/files/revisions", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_every_save_is_kept_and_an_earlier_one_comes_back(client):
    project, room = _room(client)
    assert _show(client, project, room, b"draft one").status_code == 200
    assert (
        _show(client, project, room, b"draft two", note="改了第二段").status_code == 200
    )
    assert _show(client, project, room, b"draft three").status_code == 200

    history = _history(client, room)
    assert [h["seq"] for h in history] == [3, 2, 1]
    assert history[1]["note"] == "改了第二段"
    assert history[0]["author_kind"] == "agent"

    first = history[-1]
    raw = client.get(f"/topics/{room}/files/revisions/{first['id']}/raw")
    assert raw.content == b"draft one"

    restored = client.post(f"/topics/{room}/files/revisions/{first['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert _current(client, room) == b"draft one"
    # 恢复是往后加一版，第三稿仍然取得回来。
    after = _history(client, room)
    assert [h["seq"] for h in after] == [4, 3, 2, 1]
    assert (
        client.get(f"/topics/{room}/files/revisions/{after[1]['id']}/raw").content
        == b"draft three"
    )


def test_a_write_based_on_an_old_version_does_not_overwrite_a_newer_save(client):
    project, room = _room(client)
    _show(client, project, room, b"v1")
    read_at = content_version(b"v1")
    # 期间有人存了一版。
    _show(client, project, room, b"someone else's save")

    late = _show(client, project, room, b"my edit of v1", base_version=read_at)

    assert late.status_code == 409
    assert _current(client, room) == b"someone else's save"
    # 按最新那一版读过再写，就写得进去。
    ok = _show(
        client,
        project,
        room,
        b"my edit of latest",
        base_version=content_version(b"someone else's save"),
    )
    assert ok.status_code == 200
    assert _current(client, room) == b"my edit of latest"


def _open(client, room) -> dict:
    r = client.get(f"/topics/{room}/files/editor", params={"path": "output/报告.docx"})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _link_of(config: dict) -> str:
    return config["editorConfig"]["callbackUrl"].rsplit("/", 1)[-1]


def _fake_editor_result(monkeypatch, data: bytes):
    class _Response:
        content = data

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            assert url.startswith(settings.office_editor_internal_url)
            return _Response()

    monkeypatch.setattr("app.api.routes.room_files.httpx.AsyncClient", _Client)


def _callback(client, link: str, payload: dict, *, sign: bool = True):
    body = dict(payload)
    if sign:
        body["token"] = jwt.encode(payload, SECRET, algorithm="HS256")
    return client.post(f"/office-editor/callback/{link}", json=body)


SAVED = {
    "key": "k",
    "status": 6,
    "url": "https://example.test/office-editor/cache/files/data/k/output.docx?md5=x",
    "users": ["alice"],
}


def test_the_editor_opens_the_current_file_and_its_save_lands_as_a_revision(
    client, monkeypatch
):
    project, room = _room(client)
    _show(client, project, room, b"generated by cheese")
    opened = _open(client, room)
    assert opened["enabled"] and opened["editable"]
    config = opened["config"]
    # The editor fetches the document at the URL in the config.
    fetched = client.get(
        config["document"]["url"].replace(settings.office_editor_backend_url, "")
    )
    assert fetched.content == b"generated by cheese"

    _fake_editor_result(monkeypatch, b"edited by a person")
    done = _callback(client, _link_of(config), SAVED)
    assert done.json() == {"error": 0}
    assert _current(client, room) == b"edited by a person"
    top = _history(client, room)[0]
    assert (top["source"], top["author_kind"]) == ("editor", "human")

    # 同一次编辑里再按一次保存，不算和自己冲突。
    _fake_editor_result(monkeypatch, b"edited again")
    assert _callback(client, _link_of(config), SAVED).json() == {"error": 0}
    assert _current(client, room) == b"edited again"


def test_an_unsigned_save_callback_writes_nothing(client, monkeypatch):
    project, room = _room(client)
    _show(client, project, room, b"original")
    config = _open(client, room)["config"]
    _fake_editor_result(monkeypatch, b"injected")

    refused = _callback(client, _link_of(config), SAVED, sign=False)
    forged = client.post(
        f"/office-editor/callback/{_link_of(config)}",
        json={**SAVED, "token": jwt.encode(SAVED, "wrong", algorithm="HS256")},
    )

    assert refused.status_code == 403
    assert forged.status_code == 403
    assert _current(client, room) == b"original"


def test_an_editor_save_after_someone_else_changed_the_file_keeps_both(
    client, monkeypatch
):
    project, room = _room(client)
    _show(client, project, room, b"v1")
    config = _open(client, room)["config"]
    # 编辑器开着的时候，芝士改了这份文件。
    _show(client, project, room, b"cheese changed it", note="补了结论")

    _fake_editor_result(monkeypatch, b"person's edit of v1")
    assert _callback(client, _link_of(config), SAVED).json() == {"error": 0}

    assert _current(client, room) == b"cheese changed it"
    aside = "output/报告（alice 的修改）.docx"
    assert _history(client, room, aside)[0]["size"] == len(b"person's edit of v1")


def test_a_link_token_is_bound_to_its_file(client):
    project, room = _room(client)
    _show(client, project, room, b"x")
    link = _link_of(_open(client, room)["config"])
    target = editor.read_link(link)
    assert target.path == "output/报告.docx"
    assert client.get("/office-editor/files/not-a-token").status_code == 403


def test_a_library_original_is_copied_into_the_room_before_editing(client):
    project, room = _room(client)
    up = client.post(
        f"/topics/{room}/attachments",
        files={
            "file": ("模板.docx", b"the user's template", "application/octet-stream")
        },
    )
    assert up.status_code == 200, up.text
    library_path = up.json()["data"]["path"]

    closed = client.get(
        f"/topics/{room}/files/editor", params={"path": library_path}
    ).json()["data"]
    assert closed["enabled"] is False and closed["copyable"] is True

    copied = client.post(
        f"/topics/{room}/files/copy",
        json={"source": library_path, "path": "文档/新方案.docx"},
    )
    assert copied.status_code == 200, copied.text
    assert (
        client.get(
            f"/topics/{room}/files/raw", params={"path": "文档/新方案.docx"}
        ).content
        == b"the user's template"
    )
    # 原件不动。
    assert (
        client.get(f"/topics/{room}/files/raw", params={"path": library_path}).content
        == b"the user's template"
    )


def test_closing_the_editor_after_saving_does_not_leave_a_duplicate(
    client, monkeypatch
):
    """「保存」之后芝士又改了，人再关掉编辑器：关闭时编辑器会把同样的内容再交一次。
    那不是一次冲突的保存，不该多出一份「某某的修改」。"""
    project, room = _room(client)
    _show(client, project, room, b"v1")
    config = _open(client, room)["config"]
    _fake_editor_result(monkeypatch, b"person's saved edit")
    assert _callback(client, _link_of(config), SAVED).json() == {"error": 0}
    _show(client, project, room, b"cheese changed it afterwards")

    closed = {**SAVED, "status": 2}
    assert _callback(client, _link_of(config), closed).json() == {"error": 0}

    assert _current(client, room) == b"cheese changed it afterwards"
    shown = client.get(f"/topics/{room}/shown").json()["data"]["data"]
    assert [row["path"] for row in shown] == ["output/报告.docx"]
