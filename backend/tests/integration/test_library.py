"""资料库: 给进项目的文件按原名留着，任何房间都引用得到。

上传落在项目一级，不在某个话题的目录里——「上周那份预算表」这句话是在一个从没见过
那份文件的房间里说的，所以它的身份是名字，不是随机串。同名不覆盖：撞了取下一个
`(n)`，两次上传就是两份。

消息里带的是这份资料自己的地址 `library/<名字>`，不是一份拷贝：一个项目里同一份
资料只有一份字节，哪个房间引用它都读的是那一份。
"""

from app.core.sandbox_auth import mint_scoped_token
from tests.integration.conftest import session_auth_headers


def _project(client) -> str:
    client.headers.update(session_auth_headers("user-1"))
    return client.post("/projects", json={"name": "Demo"}).json()["data"]["id"]


def _topic(client, project_id: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project_id, "title": title, "created_by": "user-1"},
    )
    return r.json()["data"]["id"]


def _upload(client, topic_id: str, name: str, content: bytes) -> dict:
    r = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": (name, content, "application/octet-stream")},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _library(client, project_id: str) -> list[dict]:
    r = client.get(f"/projects/{project_id}/library")
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def test_upload_keeps_the_name_and_a_collision_takes_the_next_number(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")

    first = _upload(client, topic_id, "预算表.xlsx", b"first")
    second = _upload(client, topic_id, "预算表.xlsx", b"second")

    assert first["path"] == "library/预算表.xlsx"
    assert second["path"] == "library/预算表(2).xlsx"

    names = [f["path"] for f in _library(client, project_id)]
    assert sorted(names) == ["预算表(2).xlsx", "预算表.xlsx"]

    for att, content in ((first, b"first"), (second, b"second")):
        raw = client.get(
            f"/topics/{topic_id}/attachments/raw",
            params={"path": att["path"], "download": "true"},
        )
        assert raw.content == content


def test_another_room_attaches_a_file_it_never_saw(client):
    """症状一：跨话题拿不到之前上传的文件。"""
    project_id = _project(client)
    uploaded_in = _topic(client, project_id, "房间一")
    later = _topic(client, project_id, "房间二")

    _upload(client, uploaded_in, "合同.docx", b"PK\x03\x04contract")

    r = client.post(f"/topics/{later}/attachments", data={"library_path": "合同.docx"})
    assert r.status_code == 200, r.text
    att = r.json()["data"]
    assert att["path"] == "library/合同.docx"
    assert att["bytes"] == len(b"PK\x03\x04contract")

    raw = client.get(
        f"/topics/{later}/attachments/raw",
        params={"path": att["path"], "download": "true"},
    )
    assert raw.status_code == 200
    assert raw.content == b"PK\x03\x04contract"

    # 引用一份已有的文件不会在资料库里多出一项。
    assert [f["path"] for f in _library(client, project_id)] == ["合同.docx"]


def test_the_room_reads_the_library_copy_itself_not_a_copy(client):
    """同一份资料被两个房间引用，两边读到的是同一份字节。"""
    project_id = _project(client)
    one = _topic(client, project_id, "房间一")
    two = _topic(client, project_id, "房间二")
    att = _upload(client, one, "预算表.xlsx", b"one-and-only")

    for room in (one, two):
        ref = client.post(
            f"/topics/{room}/attachments", data={"library_path": "预算表.xlsx"}
        ).json()["data"]
        assert ref["path"] == att["path"]
        raw = client.get(
            f"/topics/{room}/attachments/raw",
            params={"path": ref["path"], "download": "true"},
        )
        assert raw.status_code == 200
        assert raw.content == b"one-and-only"

    # 引用多少次，资料库里都只有那一项。
    assert [f["path"] for f in _library(client, project_id)] == ["预算表.xlsx"]


def test_the_room_cannot_shadow_a_library_file(client):
    """`library/…` 是资料库那一份的地址，房间里写不了同名的东西——否则读的人拿到
    的是房间那份，还以为看的是原件。"""
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")

    declared = client.post(
        f"/topics/{topic_id}/artifact",
        json={"path": "library/预算表.xlsx", "content": "<p>假的</p>", "as": "html"},
    )
    assert declared.status_code == 422, declared.text


def test_a_library_file_does_not_belong_to_a_task_branch(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    _upload(client, topic_id, "预算表.xlsx", b"first")

    r = client.get(
        f"/topics/{topic_id}/attachments/raw",
        params={
            "path": "library/预算表.xlsx",
            "download": "true",
            "task": "00000000-0000-0000-0000-000000000001",
        },
    )
    assert r.status_code in (404, 422)


def test_a_pasted_screenshot_stays_in_the_room(client):
    """资料库按名字寻址，而剪贴板里那张图没有名字——`image.png` 是浏览器编的。"""
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")

    r = client.post(
        f"/topics/{topic_id}/attachments",
        files={"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"origin": "clipboard"},
    )
    assert r.status_code == 200, r.text
    att = r.json()["data"]
    assert att["path"].startswith("uploads/")
    assert _library(client, project_id) == []

    # 这条消息照样带得走它。
    raw = client.get(
        f"/topics/{topic_id}/attachments/raw", params={"path": att["path"]}
    )
    assert raw.status_code == 200
    assert raw.content == b"\x89PNG\r\n\x1a\n"


def test_a_file_the_library_does_not_have(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")

    missing = client.post(
        f"/topics/{topic_id}/attachments", data={"library_path": "不存在.xlsx"}
    )
    assert missing.status_code == 404

    escape = client.post(
        f"/topics/{topic_id}/attachments", data={"library_path": "../secret.txt"}
    )
    assert escape.status_code == 422

    neither = client.post(f"/topics/{topic_id}/attachments")
    assert neither.status_code == 422


def test_a_document_in_the_library_opens_in_the_preview(client):
    """`library/…` 是一个地址，预览那一格得认它——芝士 读过一份资料之后会在消息里
    引用它，点那枚 chip 不能落到「这个来源里没有这份文件」。"""
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    _upload(client, topic_id, "说明.md", "# 说明\n第一行\n".encode())
    _upload(client, topic_id, "合同.docx", b"PK\x03\x04\xff\xfe\x00\x01docx")

    text = client.get(
        f"/topics/{topic_id}/preview/file", params={"path": "library/说明.md"}
    )
    assert text.status_code == 200, text.text
    assert "第一行" in text.json()["data"]["content"]

    # 二进制那一份不给正文，但给版本——文档视图靠它取页面。
    binary = client.get(
        f"/topics/{topic_id}/preview/file", params={"path": "library/合同.docx"}
    )
    assert binary.status_code == 200, binary.text
    assert binary.json()["data"]["binary"] is True
    assert binary.json()["data"]["version"]

    missing = client.get(
        f"/topics/{topic_id}/preview/file", params={"path": "library/没有.md"}
    )
    assert missing.status_code == 422


def test_the_agent_takes_a_copy_of_a_file_nobody_attached(client):
    """`cheese library get` 走的这条路：芝士 自己的凭据也读得到资料库。"""
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    _upload(client, topic_id, "预算表.xlsx", b"budget")

    client.headers.pop("Authorization", None)
    client.cookies.clear()
    agent = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id, topic_id=topic_id, ttl_s=3600
        )
    }
    # 凭据是为一轮、一个地方铸的，所以它得说自己在哪儿干活；不说就够不到项目。
    assert (
        client.get(f"/projects/{project_id}/library", headers=agent).status_code == 403
    )

    listed = client.get(
        f"/projects/{project_id}/library", params={"topic": topic_id}, headers=agent
    )
    assert listed.status_code == 200, listed.text
    assert [f["path"] for f in listed.json()["data"]["data"]] == ["预算表.xlsx"]

    raw = client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "预算表.xlsx", "topic": topic_id},
        headers=agent,
    )
    assert raw.status_code == 200, raw.text
    assert raw.content == b"budget"
    assert raw.headers["x-content-type-options"] == "nosniff"

    escape = client.get(
        f"/projects/{project_id}/library/raw",
        params={"path": "../secret.txt", "topic": topic_id},
        headers=agent,
    )
    assert escape.status_code == 422


def test_a_file_the_project_no_longer_wants(client):
    """扔掉一份资料：它从清单里消失，引用过它的消息也如实说它不在了。"""
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    att = _upload(client, topic_id, "说明.md", "# 说明\n第一行\n".encode())
    _upload(client, topic_id, "预算表.xlsx", b"budget")

    gone = client.delete(f"/projects/{project_id}/library", params={"path": "说明.md"})
    assert gone.status_code == 200, gone.text
    assert [f["path"] for f in _library(client, project_id)] == ["预算表.xlsx"]

    # 那条旧消息里的引用还在，点开它得到的是「东西不在了」，不是别的什么文件。
    opened = client.get(
        f"/topics/{topic_id}/preview/file", params={"path": att["path"]}
    )
    assert opened.status_code == 422
    assert "资料库" in opened.json()["message"]

    assert (
        client.delete(
            f"/projects/{project_id}/library", params={"path": "说明.md"}
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/projects/{project_id}/library", params={"path": "../secret.txt"}
        ).status_code
        == 422
    )


def test_the_agent_cannot_throw_away_what_it_was_given(client):
    """读资料库的是人和 芝士，扔掉它的只有人。"""
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    _upload(client, topic_id, "预算表.xlsx", b"budget")

    client.headers.pop("Authorization", None)
    client.cookies.clear()
    agent = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id, topic_id=topic_id, ttl_s=3600
        )
    }
    refused = client.delete(
        f"/projects/{project_id}/library",
        params={"path": "预算表.xlsx", "topic": topic_id},
        headers=agent,
    )
    assert refused.status_code == 403

    listed = client.get(
        f"/projects/{project_id}/library", params={"topic": topic_id}, headers=agent
    )
    assert [f["path"] for f in listed.json()["data"]["data"]] == ["预算表.xlsx"]


def test_the_library_is_the_project_members_only(client):
    project_id = _project(client)
    topic_id = _topic(client, project_id, "房间一")
    _upload(client, topic_id, "预算表.xlsx", b"first")

    client.headers.update(session_auth_headers("outsider"))
    assert client.get(f"/projects/{project_id}/library").status_code == 403
    assert (
        client.delete(
            f"/projects/{project_id}/library", params={"path": "预算表.xlsx"}
        ).status_code
        == 403
    )
